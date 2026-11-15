"""Scoped cluster facade. The application factory owns Blueprint registration."""
from __future__ import annotations

from contextlib import contextmanager
import json
import threading
from urllib.parse import urlsplit

from flask import Blueprint, current_app, jsonify, request

from data.cluster_connections import (CONFIG_KEYS, ClusterConnectionStore, ClusterError,
                                      config_fingerprint, credential_label, public_connection, scope_text)
from data.cluster_query_store import ClusterQueryStore
from infrastructure.paths import is_cloud_managed_runtime

bp = Blueprint("cluster", __name__, url_prefix="/api/datasource/cluster")
_SOURCE_LOCK = threading.RLock()


@bp.after_request
def _no_cache(response):
    response.headers["Cache-Control"] = "no-store"
    return response


def _owner():
    if not is_cloud_managed_runtime():
        return "local-default"
    from .auth import current_user
    user = current_user()
    if not user or not user.get("id"):
        raise ClusterError("cluster_permission_denied", "Authentication is required")
    return scope_text(user["id"], "owner_id", empty=False)


def _scope(payload, *, optional=False):
    owner = _owner()
    sid = payload.get("session_id", "")
    if optional and not sid:
        if payload.get("workspace_id"):
            raise ClusterError("cluster_permission_denied", "A session is required for a workspace")
        return {"owner_id": owner, "session_id": "", "workspace_id": ""}
    sid = scope_text(sid, "session_id", empty=False)
    from .state import check_session_ownership, session_manager
    allowed, user_id = check_session_ownership(sid)
    if not allowed or (user_id and user_id != owner):
        raise ClusterError("cluster_permission_denied", "This session belongs to another owner")
    session = session_manager.get_or_create(sid)
    if user_id and not getattr(session, "owner_user_id", ""):
        session.owner_user_id = user_id
    workspace = scope_text(str(getattr(session, "workspace_id", "") or ""), "workspace_id")
    if "workspace_id" in payload and payload["workspace_id"] != workspace:
        raise ClusterError("cluster_permission_denied", "Workspace does not match the authorized session")
    return {"owner_id": owner, "session_id": sid, "workspace_id": workspace}


def _payload(allowed=None):
    raw = request.stream.read(128 * 1024 + 1)
    if len(raw) > 128 * 1024:
        raise ClusterError("invalid_request", "Cluster request exceeds 128 KiB")
    try:
        value = json.loads(raw) if raw else {}
    except (ValueError, UnicodeError, RecursionError):
        raise ClusterError("invalid_request", "A JSON object is required") from None
    if not isinstance(value, dict) or (allowed is not None and set(value) - set(allowed)):
        raise ClusterError("invalid_request", "Unknown request fields or invalid JSON object")
    return value


def _connections(owner):
    return ClusterConnectionStore(current_app.config.get("CLUSTER_STORE_ROOT"), owner_id=owner)


def _queries(scope):
    return ClusterQueryStore(current_app.config.get("CLUSTER_STORE_ROOT"), **scope)


def _error(exc):
    if not isinstance(exc, ClusterError):
        # Preserve shared pipeline codes without exposing local paths or raw
        # driver exception strings through the HTTP boundary.
        messages = {"budget_exceeded": "Snapshot import exceeded its resource budget",
                    "idempotency_conflict": "The import key already belongs to another immutable plan",
                    "operation_busy": "The shared catalog operation is already active",
                    "dataset_changed": "Verified staging bytes changed before publication",
                    "snapshot_changed": "Verified staging bytes changed before publication",
                    "canceled": "Snapshot import was cancelled before publication"}
        code = getattr(exc, "code", "")
        exc = (ClusterError("resource_busy" if code == "operation_busy" else code, messages[code]) if code in messages else
               ClusterError("cluster_request_failed", "Cluster request could not be completed"))
    status = (403 if exc.code in {"cluster_permission_denied", "scope_mismatch"} else
              404 if exc.code == "cluster_not_found" else
              409 if exc.code in {"idempotency_conflict", "resource_busy", "result_already_consumed", "credential_boundary_changed"} else
              413 if exc.code in {"budget_exceeded", "cluster_frame_too_large"} else
              503 if exc.code in {"dependency_unavailable", "credential_unavailable", "remote_state_unknown", "cluster_transport_error", "cluster_request_failed"} else 400)
    return jsonify({"ok": False, **exc.to_dict()}), status


def _endpoint(function):
    from functools import wraps
    @wraps(function)
    def invoke(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception as exc:
            return _error(exc)
    return invoke


def _assert_network_target(config):
    if not is_cloud_managed_runtime():
        return
    def origin(value):
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
            raise ValueError("Invalid approved cluster origin")
        return parsed.hostname.lower(), parsed.port or 443
    configured = current_app.config.get("CLUSTER_ALLOWED_ORIGINS", ())
    if not isinstance(configured, (list, tuple, set)):
        configured = ()
    try:
        allowed = {origin(value) for value in configured if isinstance(value, str)}
        host = str(config.get("host") or "").lower()
        port = int(config.get("port") or (443 if config.get("kind") == "trino" else 10000))
        targets = {(host, port)}
        for endpoint in config.get("approved_endpoints") or []:
            targets.add(origin(endpoint))
    except (TypeError, ValueError):
        raise ClusterError("cluster_permission_denied", "Hosted cluster targets require administrator-approved HTTPS origins") from None
    if config.get("tls", True) is not True or not allowed or not targets.issubset(allowed):
        raise ClusterError("cluster_permission_denied", "Hosted cluster targets require administrator-approved HTTPS origins")


@contextmanager
def _source(config, store, connection_id, *, submission=False):
    if submission:
        _assert_network_target(config)
    from data.sources.trino import TrinoDataSource
    from data.sources.hive import HiveDataSource
    from data.sources.spark_sql import SparkSQLDataSource
    classes = {"trino": TrinoDataSource, "hive": HiveDataSource, "spark_sql": SparkSQLDataSource}
    key = (*store.scope, connection_id, config_fingerprint(config))
    temporary = False
    with _SOURCE_LOCK:
        registry = current_app.extensions.setdefault("cluster_sources", {})
        source = registry.get(key)
        if source is None:
            if len(registry) >= 32:
                for old_key, candidate in list(registry.items()):
                    if not candidate.store.has_active_connection(candidate.config, candidate.connection_id):
                        candidate.close()
                        del registry[old_key]
                        break
            if len(registry) >= 32 and submission:
                raise ClusterError("resource_busy", "The process cluster-connection limit has been reached")
            source = classes[config["kind"]](config, query_store=store, connection_id=connection_id,
                       budget=current_app.config.get("CLUSTER_QUERY_BUDGET"),
                       credential_resolver=current_app.config.get("CLUSTER_CREDENTIAL_RESOLVER"),
                       transport_factory=current_app.config.get("CLUSTER_TRANSPORT_FACTORIES", {}).get(config["kind"]))
            temporary = len(registry) >= 32
            if not temporary:
                registry[key] = source
    try:
        yield source
    finally:
        if temporary:
            source.close()


def create_session_source(connection_id, scope):
    """Create a leased Session source, separate from the API query cache."""
    from data.sources.trino import TrinoDataSource
    from data.sources.hive import HiveDataSource
    from data.sources.spark_sql import SparkSQLDataSource
    classes = {"trino": TrinoDataSource, "hive": HiveDataSource, "spark_sql": SparkSQLDataSource}
    connections = _connections(scope["owner_id"])
    config = connections.get(connection_id)["config"]
    _assert_network_target(config)
    fingerprint = config_fingerprint(config)
    source = classes[config["kind"]](
        config, query_store=_queries(scope), connection_id=connection_id,
        budget=current_app.config.get("CLUSTER_QUERY_BUDGET"),
        credential_resolver=current_app.config.get("CLUSTER_CREDENTIAL_RESOLVER"),
        transport_factory=current_app.config.get("CLUSTER_TRANSPORT_FACTORIES", {}).get(config["kind"]),
    )
    def configuration_unchanged():
        try:
            return config_fingerprint(connections.get(connection_id)["config"]) == fingerprint
        except ClusterError:
            return False
    source._configuration_guard = configuration_unchanged
    return source


@bp.post("/connections/<connection_id>/activate")
@_endpoint
def activate_connection(connection_id):
    payload = _payload({"session_id", "workspace_id"})
    scope = _scope(payload)
    from .state import session_manager
    session = session_manager.get_or_create(scope["session_id"])
    config = _connections(scope["owner_id"]).get(connection_id)["config"]
    fingerprint = config_fingerprint(config)
    for entry in session._sources:
        candidate = entry.get("source")
        if (getattr(candidate, "connection_id", None) == connection_id
                and not getattr(candidate, "_closed", False)
                and isinstance(getattr(candidate, "config", None), dict)
                and config_fingerprint(candidate.config) == fingerprint):
            return jsonify({"ok": True, "source_id": entry["id"], "reused": True, "sources": session.list_sources()})
    source = create_session_source(connection_id, scope)
    try:
        source.test_connection()
        source_id = session.add_source(source)
    except BaseException:
        source.close()
        raise
    return jsonify({"ok": True, "source_id": source_id, "reused": False, "sources": session.list_sources()})


def _save_connection(payload, *, connection_id=""):
    scope = _scope(payload, optional=True)
    store = _connections(scope["owner_id"])
    old = store.get(connection_id)["config"] if connection_id else {}
    cfg = {**old, **{k: v for k, v in payload.items() if k in CONFIG_KEYS}}
    _assert_network_target(cfg)
    if "credential_ref" in payload:
        raise ClusterError("cluster_permission_denied", "Credential references are created by the server vault")
    secret = payload.get("secret")
    new_ref = ""
    if cfg.get("auth", "none") == "none":
        cfg["credential_ref"] = ""
        if secret is not None:
            raise ClusterError("invalid_cluster_config", "Unauthenticated connections cannot have a secret")
    elif secret is not None:
        if not isinstance(secret, str) or not secret or len(secret) > 16384 or any(c in secret for c in "\r\n\0"):
            raise ClusterError("credential_unavailable", "Supply a nonempty bounded secret")
        from infrastructure.credential_store import store_secret
        try:
            new_ref = store_secret(secret, label=credential_label(scope["owner_id"]))
        except Exception:
            raise ClusterError("credential_unavailable", "The OS credential vault could not store the secret") from None
        cfg["credential_ref"] = new_ref
    try:
        if is_cloud_managed_runtime() and cfg.get("tls", True) is not True:
            raise ClusterError("unsupported_auth", "Hosted cluster connections require verified TLS")
        record = store.save(cfg, connection_id=connection_id)
        return public_connection(record)
    except BaseException:
        if new_ref:
            from infrastructure.credential_store import delete_secret
            try:
                delete_secret(new_ref)
            except Exception:
                pass
        raise


@bp.get("/connections")
@_endpoint
def list_connections():
    scope = _scope(request.args, optional=True)
    return jsonify({"ok": True, "connections": _connections(scope["owner_id"]).list()})


@bp.post("/connections")
@_endpoint
def create_connection():
    payload = _payload(CONFIG_KEYS | {"session_id", "workspace_id", "secret"})
    return jsonify({"ok": True, "connection": _save_connection(payload)}), 201


@bp.patch("/connections/<connection_id>")
@_endpoint
def update_connection(connection_id):
    payload = _payload(CONFIG_KEYS | {"session_id", "workspace_id", "secret"})
    return jsonify({"ok": True, "connection": _save_connection(payload, connection_id=connection_id)})


@bp.delete("/connections/<connection_id>")
@_endpoint
def delete_connection(connection_id):
    payload = _payload({"session_id", "workspace_id"}) if request.content_length else request.args
    scope = _scope(payload, optional=True)
    _connections(scope["owner_id"]).delete(connection_id)
    # Existing queries retain their frozen endpoint and vault reference for
    # status/cancellation. Do not revoke their credential during deletion.
    return jsonify({"ok": True, "connection_id": connection_id, "deleted": True})


@bp.post("/connections/<connection_id>/test")
@_endpoint
def test_connection(connection_id):
    payload = _payload({"session_id", "workspace_id"})
    scope = _scope(payload)
    config = _connections(scope["owner_id"]).get(connection_id)["config"]
    with _source(config, _queries(scope), connection_id, submission=True) as source:
        return jsonify({"ok": True, **source.test_connection()})


@bp.post("/queries")
@_endpoint
def submit_query():
    payload = _payload({"session_id", "workspace_id", "connection_id", "sql", "idempotency_key", "budget"})
    scope = _scope(payload)
    key = payload.get("idempotency_key")
    if not isinstance(key, str) or not key.strip() or len(key) > 200:
        raise ClusterError("invalid_idempotency_key", "A nonempty idempotency_key of at most 200 characters is required")
    connection_id = payload.get("connection_id")
    if not isinstance(connection_id, str):
        raise ClusterError("invalid_request", "A connection_id is required")
    store = _queries(scope)
    config = _connections(scope["owner_id"]).get(connection_id)["config"]
    with _source(config, store, connection_id, submission=True) as source:
        query = source.submit_query(payload.get("sql"), idempotency_key=key, budget=payload.get("budget"))
    return jsonify({"ok": True, "query": query}), 202


@bp.get("/queries")
@_endpoint
def list_queries():
    scope = _scope(request.args)
    return jsonify({"ok": True, "queries": _queries(scope).list(idempotency_key=request.args.get("idempotency_key", ""),
                                                              limit=_integer_arg("limit", 100))})


def _integer_arg(name, default):
    try:
        return int(request.args.get(name, default))
    except (TypeError, ValueError):
        raise ClusterError("invalid_event_cursor", "Invalid event cursor or limit") from None


@bp.get("/queries/<query_id>")
@_endpoint
def query_status(query_id):
    scope = _scope(request.args)
    store = _queries(scope)
    record = store.get(query_id)
    with _source(record["config"], store, record["connection_id"]) as source:
        return jsonify({"ok": True, "query": source.query_status(query_id)})


@bp.get("/queries/<query_id>/events")
@_endpoint
def query_events(query_id):
    store = _queries(_scope(request.args))
    after = _integer_arg("after_seq", 0)
    events = store.events(query_id, after_seq=after, limit=_integer_arg("limit", 100))
    window = store.event_window(query_id)
    return jsonify({"ok": True, "query_id": query_id, "events": events, **window,
                    "next_seq": events[-1]["seq"] if events else after,
                    "truncated": bool(window["first_seq"] > after + 1), "semantics": "append"})


@bp.post("/queries/<query_id>/cancel")
@_endpoint
def cancel_query(query_id):
    scope = _scope(_payload({"session_id", "workspace_id"}))
    store = _queries(scope)
    record = store.get(query_id)
    with _source(record["config"], store, record["connection_id"]) as source:
        query = source.cancel_query(query_id)
    return jsonify({"ok": True, "query": query}), 202 if query["state"] == "cancel_pending" else 200


@bp.post("/queries/<query_id>/snapshot")
@_endpoint
def snapshot_query(query_id):
    payload = _payload({"session_id", "workspace_id", "idempotency_key"})
    scope = _scope(payload)
    store = _queries(scope)
    record = store.get(query_id)
    factory = current_app.config.get("CLUSTER_CATALOG_FACTORY")
    if factory is None:
        try:
            from data.dataset_catalog import DatasetCatalog
        except ImportError:
            raise ClusterError("dependency_unavailable", "The shared dataset catalog is not available") from None
        factory = DatasetCatalog
    catalog = factory(**scope)
    with _source(record["config"], store, record["connection_id"]) as source:
        manifest = source.snapshot_result(query_id, catalog=catalog, idempotency_key=payload.get("idempotency_key", ""))
        query = source.query_status(query_id)
    return jsonify({"ok": True, "manifest": manifest, "query": query})
