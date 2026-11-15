"""Owner-scoped plans, bounded imports and immutable dataset activation."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from flask import Blueprint, jsonify, request, send_file, url_for
from werkzeug.utils import secure_filename

from .state import check_session_ownership, session_manager, workspace_manager

bp = Blueprint("datasets", __name__)
_LOCK = threading.RLock()
_FORMATS = {".csv", ".parquet", ".xlsx"}


def _scope(sid):
    allowed, user_id = check_session_ownership(sid)
    if not allowed:
        raise PermissionError("This session belongs to another user")
    session = session_manager.get_or_create(sid)
    if user_id and not getattr(session, "owner_user_id", ""):
        session.owner_user_id = user_id
    workspace_id = str(getattr(session, "workspace_id", "") or "")
    body = request.get_json(silent=True) if request.is_json else None
    supplied = body.get("workspace_id") if isinstance(body, dict) and "workspace_id" in body else request.args.get("workspace_id")
    if supplied is not None and supplied != workspace_id:
        raise PermissionError("Workspace does not match the current session")
    return session, {"owner_id": user_id or "local-default", "session_id": sid, "workspace_id": workspace_id}


def catalog_for_session(sid):
    from data.dataset_catalog import DatasetCatalog
    _session, scope = _scope(sid)
    return DatasetCatalog(**scope)


def _public(value):
    if isinstance(value, dict):
        return {str(key): _public(item) for key, item in value.items()
                if str(key) not in {"path", "file_path", "db_path", "input_path", "root", "upload_root", "remote_root", "staging_root", "snapshot_root", "absolute_path"}
                and not str(key).endswith("_directory")}
    if isinstance(value, (tuple, list)):
        return [_public(item) for item in value]
    return value


def _error(exc):
    code = str(getattr(exc, "code", "") or "invalid_request")
    status = 403 if isinstance(exc, PermissionError) else 404 if isinstance(exc, (KeyError, FileNotFoundError)) else 409 if "conflict" in str(exc).lower() else 400
    if code in {"budget_exceeded", "resource_busy"}:
        status = 413 if code == "budget_exceeded" else 409
    return jsonify({"ok": False, "error": str(exc)[:1000], "code": code}), status


@contextmanager
def _database(catalog):
    directory = Path(catalog.root) / "api"
    directory.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(directory / "operations.sqlite3", timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS plans (
                id TEXT PRIMARY KEY, scope TEXT NOT NULL, payload TEXT NOT NULL, expires REAL NOT NULL,
                input_name TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS launches (
                id TEXT PRIMARY KEY, scope TEXT NOT NULL, idempotency_key TEXT NOT NULL,
                fingerprint TEXT NOT NULL, plan_id TEXT NOT NULL, job_id TEXT NOT NULL DEFAULT '',
                state TEXT NOT NULL, result TEXT, error TEXT,
                UNIQUE(scope, idempotency_key));
        """)
        if "input_name" not in {row[1] for row in connection.execute("PRAGMA table_info(plans)")}:
            connection.execute("ALTER TABLE plans ADD COLUMN input_name TEXT NOT NULL DEFAULT ''")
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def _scope_key(scope):
    return json.dumps(scope, sort_keys=True, separators=(",", ":"))


def _load_plan(connection, plan_id, scope):
    row = connection.execute("SELECT * FROM plans WHERE id=? AND scope=?", (str(plan_id), _scope_key(scope))).fetchone()
    if row is None:
        raise KeyError("Dataset plan not found")
    if row["expires"] < time.time():
        raise ValueError("Dataset plan expired; prepare it again")
    return json.loads(row["payload"])


def _parse_object(value, label):
    if value in (None, ""):
        return {}
    if isinstance(value, str):
        if len(value) > 65_536:
            raise ValueError(f"{label} is too large")
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _authorized_input(sid, session, catalog, payload, budget):
    uploaded = request.files.get("file")
    if uploaded is not None:
        original_name = str(uploaded.filename or "")
        suffix = Path(original_name).suffix.lower()
        name = (secure_filename(Path(original_name).stem) or "dataset") + suffix
        if suffix not in _FORMATS:
            raise ValueError("Supported dataset formats: CSV, Parquet, XLSX")
        incoming = Path(catalog.root) / "incoming"
        incoming.mkdir(parents=True, exist_ok=True)
        target = incoming / (uuid.uuid4().hex + suffix)
        size = 0
        try:
            with target.open("xb") as stream:
                while True:
                    chunk = uploaded.stream.read(min(1024 * 1024, budget.max_input_bytes + 1))
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > budget.max_input_bytes:
                        raise ValueError("budget_exceeded: upload is larger than max_input_bytes")
                    stream.write(chunk)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return target, " ".join(Path(original_name).name.split())[:240]
    workspace_file = payload.get("workspace_file")
    if workspace_file:
        runtime = workspace_manager.get(sid)
        if runtime is None:
            raise PermissionError("Mount an authorized workspace before selecting its files")
        path = runtime.resolve_tool_path(str(workspace_file))
        if not path.is_file() or path.suffix.lower() not in _FORMATS:
            raise ValueError("Select a CSV, Parquet or XLSX file in the mounted workspace")
        return path, path.name
    source_id = str(payload.get("source_id") or "")
    if source_id:
        entry = next((item for item in getattr(session, "_sources", []) if str(item.get("id")) == source_id), None)
        path_value = getattr((entry or {}).get("source"), "file_path", None)
        if path_value:
            path = Path(path_value).resolve(strict=True)
            if path.suffix.lower() in _FORMATS:
                return path, path.name
        raise ValueError("This data source has no reusable file; materialize a bounded dataset first")
    raise ValueError("Upload a file or select an authorized workspace_file/source_id")


def plan_authorized_file(sid, input_path, *, options=None, budget=None, input_name=None):
    """Internal bridge for files already authorized by upload/workspace APIs."""
    from data.import_pipeline import create_import_plan
    from infrastructure.resource_budget import BudgetSpec
    _session, scope = _scope(sid)
    catalog = catalog_for_session(sid)
    path = Path(input_path).resolve(strict=True)
    if not path.is_file() or path.suffix.lower() not in _FORMATS:
        raise ValueError("Select a CSV, Parquet or XLSX input")
    selected_budget = budget if isinstance(budget, BudgetSpec) else BudgetSpec.from_dict(budget)
    plan = create_import_plan(path, **scope, options=options, budget=selected_budget)
    plan_id = uuid.uuid4().hex
    with _database(catalog) as connection:
        connection.execute("INSERT INTO plans(id,scope,payload,expires,input_name) VALUES (?, ?, ?, ?, ?)",
                           (plan_id, _scope_key(scope), json.dumps(plan, ensure_ascii=False, default=str), time.time() + 3600,
                            " ".join(str(input_name or path.name).split())[:240]))
    return {**_public(plan), "plan_id": plan_id, "input_name": input_name or path.name}


@bp.post("/api/session/<sid>/datasets/plans")
def create_plan(sid):
    try:
        from data.import_pipeline import create_import_plan
        from infrastructure.resource_budget import BudgetSpec
        session, scope = _scope(sid)
        catalog = catalog_for_session(sid)
        payload = request.get_json(silent=True) or request.form.to_dict()
        options = _parse_object(payload.get("options"), "options")
        budget = BudgetSpec.from_dict(_parse_object(payload.get("budget"), "budget"))
        path, name = _authorized_input(sid, session, catalog, payload, budget)
        plan = create_import_plan(path, **scope, options=options, budget=budget)
        plan_id = uuid.uuid4().hex
        with _database(catalog) as connection:
            connection.execute("INSERT INTO plans(id,scope,payload,expires,input_name) VALUES (?, ?, ?, ?, ?)",
                               (plan_id, _scope_key(scope), json.dumps(plan, ensure_ascii=False, default=str), time.time() + 3600, name))
        public = {**_public(plan), "plan_id": plan_id, "input_name": name, "expires_in_seconds": 3600}
        return jsonify({"ok": True, "plan": public, "plan_id": plan_id})
    except (ValueError, TypeError, PermissionError, OSError, KeyError, RuntimeError) as exc:
        return _error(exc)


def submit_import_plan(sid, plan_id, *, idempotency_key):
    """Also used by the legacy upload facade; no caller-supplied paths here."""
    from data.import_pipeline import run_import
    from data.sources.dataset import DatasetDataSource

    session, scope = _scope(sid)
    catalog = catalog_for_session(sid)
    key = str(idempotency_key or "")
    if not key or len(key) > 160:
        raise ValueError("A bounded idempotency_key is required")
    scope_key = _scope_key(scope)
    with _LOCK, _database(catalog) as connection:
        connection.execute("BEGIN IMMEDIATE")
        plan = _load_plan(connection, plan_id, scope)
        plan_name = connection.execute("SELECT input_name FROM plans WHERE id=?", (str(plan_id),)).fetchone()[0] or "Dataset import"
        fingerprint = hashlib.sha256(json.dumps(plan, sort_keys=True, default=str).encode()).hexdigest()
        previous = connection.execute("SELECT * FROM launches WHERE scope=? AND idempotency_key=?", (scope_key, key)).fetchone()
        if previous is not None:
            if previous["fingerprint"] != fingerprint:
                raise ValueError("idempotency_conflict: this key belongs to a different import plan")
            return _launch_response(dict(previous), session)
        operation_id = uuid.uuid4().hex
        connection.execute("INSERT INTO launches(id,scope,idempotency_key,fingerprint,plan_id,state) VALUES (?,?,?,?,?,?)",
                           (operation_id, scope_key, key, fingerprint, str(plan_id), "queued"))

        def worker(context):
            try:
                context.check_canceled()
                manifest = run_import(plan, catalog=catalog, progress=context.set_progress, cancel=context.is_canceled)
                handle = catalog.get(manifest["dataset_id"], snapshot_id=manifest["snapshot_id"], verify=True)
                canceled_after_publish = context.is_canceled()
                source_id = None
                activate_in_scope = not canceled_after_publish and str(getattr(session, "workspace_id", "") or "") == scope["workspace_id"]
                if activate_in_scope:
                    source = DatasetDataSource(handle, name=plan_name)
                    source._dataset_workspace_id = scope["workspace_id"]
                    source_id = session.add_source(source)
                result = {"manifest": _public(manifest), "dataset_id": manifest["dataset_id"],
                          "snapshot_id": manifest["snapshot_id"], "source_id": source_id,
                          "complete": True, "activated": activate_in_scope, "result_committed": True,
                          "cancel_requested": canceled_after_publish}
                with _database(catalog) as database:
                    database.execute("UPDATE launches SET state='succeeded',result=? WHERE id=? AND scope=?",
                                     (json.dumps(result, default=str), operation_id, scope_key))
                return result
            except BaseException as exc:
                with _database(catalog) as database:
                    database.execute("UPDATE launches SET state='failed',error=? WHERE id=? AND scope=?",
                                     (str(exc)[:1000], operation_id, scope_key))
                raise

        job_id = session.job_runner.create(worker, job_type="dataset_import", label=plan_name)
        connection.execute("UPDATE launches SET job_id=? WHERE id=?", (job_id, operation_id))
        return {"ok": True, "operation_id": operation_id, "job_id": job_id, "status": "queued"}


def _launch_response(launch, session):
    job = session.job_runner.get_status(launch["job_id"]) if launch.get("job_id") else None
    return {"ok": True, "operation_id": launch["id"], "job_id": launch.get("job_id", ""),
            "status": (job or {}).get("status") or launch["state"], "job": job,
            "result": json.loads(launch["result"]) if launch.get("result") else None,
            "error": launch.get("error")}


@bp.post("/api/session/<sid>/datasets/imports")
def submit_import(sid):
    try:
        payload = request.get_json(silent=True) or {}
        result = submit_import_plan(sid, payload.get("plan_id"), idempotency_key=payload.get("idempotency_key"))
        return jsonify(result), 202 if result["status"] not in {"succeeded", "failed", "canceled"} else 200
    except (ValueError, TypeError, PermissionError, OSError, KeyError, RuntimeError) as exc:
        return _error(exc)


@bp.get("/api/session/<sid>/datasets/imports/<operation_id>")
def import_status(sid, operation_id):
    try:
        session, scope = _scope(sid)
        with _database(catalog_for_session(sid)) as connection:
            row = connection.execute("SELECT * FROM launches WHERE id=? AND scope=?", (operation_id, _scope_key(scope))).fetchone()
        if row is None:
            raise KeyError("Import operation not found")
        return jsonify(_public(_launch_response(dict(row), session)))
    except (ValueError, TypeError, PermissionError, OSError, KeyError, RuntimeError) as exc:
        return _error(exc)


@bp.get("/api/session/<sid>/datasets")
def list_datasets(sid):
    try:
        return jsonify({"ok": True, "datasets": _public(catalog_for_session(sid).list())})
    except (ValueError, TypeError, PermissionError, OSError, KeyError, RuntimeError) as exc:
        return _error(exc)


@bp.get("/api/session/<sid>/datasets/<dataset_id>/versions")
def versions(sid, dataset_id):
    try:
        return jsonify({"ok": True, "versions": _public(catalog_for_session(sid).versions(dataset_id))})
    except (ValueError, TypeError, PermissionError, OSError, KeyError, RuntimeError) as exc:
        return _error(exc)


@bp.get("/api/session/<sid>/datasets/<dataset_id>/preview")
def preview(sid, dataset_id):
    source = None
    try:
        from data.sources.dataset import DatasetDataSource
        limit = int(request.args.get("limit", 100))
        if not 1 <= limit <= 100:
            raise ValueError("Preview limit must be between 1 and 100")
        handle = catalog_for_session(sid).get(dataset_id, snapshot_id=request.args.get("snapshot_id", ""), verify=True)
        source = DatasetDataSource(handle)
        tables = source.list_tables()
        table = request.args.get("table") or (tables[0] if tables else "")
        if table not in tables:
            raise ValueError("Select a table from this dataset")
        return jsonify({"ok": True, "preview": _public(source.get_preview_table(table, max_rows=limit)),
                        "manifest": _public(handle.manifest)})
    except (ValueError, TypeError, PermissionError, OSError, KeyError, RuntimeError) as exc:
        return _error(exc)
    finally:
        if source is not None:
            source.close()


@bp.get("/api/session/<sid>/datasets/<dataset_id>/files")
def dataset_files(sid, dataset_id):
    try:
        handle = catalog_for_session(sid).get(dataset_id, snapshot_id=request.args.get("snapshot_id", ""), verify=True)
        files = [{"index": index, "name": Path(item["relative_path"]).name,
                  "size_bytes": item["size_bytes"], "sha256": item["sha256"],
                  "url": url_for("datasets.dataset_file", sid=sid, dataset_id=dataset_id,
                                 file_index=index, snapshot_id=handle.snapshot_id)}
                 for index, item in enumerate(handle.manifest["files"])]
        return jsonify({"ok": True, "dataset_id": dataset_id, "snapshot_id": handle.snapshot_id, "files": files})
    except (ValueError, TypeError, PermissionError, OSError, KeyError, RuntimeError) as exc:
        return _error(exc)


@bp.get("/api/session/<sid>/datasets/<dataset_id>/files/<int:file_index>")
def dataset_file(sid, dataset_id, file_index):
    try:
        handle = catalog_for_session(sid).get(dataset_id, snapshot_id=request.args.get("snapshot_id", ""), verify=True)
        files = handle.manifest["files"]
        if not 0 <= file_index < len(files):
            raise KeyError("Dataset file not found")
        root = Path(handle.root).resolve(strict=True)
        relative = Path(files[file_index]["relative_path"])
        candidate = root / relative
        if relative.is_absolute() or ".." in relative.parts or candidate.is_symlink() or not candidate.resolve(strict=True).is_relative_to(root):
            raise PermissionError("Dataset file is outside the immutable snapshot")
        response = send_file(candidate, as_attachment=True, download_name=candidate.name,
                             conditional=True, etag=str(files[file_index]["sha256"]))
        response.headers["Cache-Control"] = "private, no-store"
        return response
    except (ValueError, TypeError, PermissionError, OSError, KeyError, RuntimeError) as exc:
        return _error(exc)


@bp.post("/api/session/<sid>/datasets/<dataset_id>/activate")
def activate(sid, dataset_id):
    try:
        from data.sources.dataset import DatasetDataSource
        session, _scope_value = _scope(sid)
        payload = request.get_json(silent=True) or {}
        handle = catalog_for_session(sid).get(dataset_id, snapshot_id=str(payload.get("snapshot_id") or ""), verify=True)
        for entry in session._sources:
            existing = getattr(entry.get("source"), "handle", None)
            if existing is not None and existing.snapshot_id == handle.snapshot_id:
                return jsonify({"ok": True, "source_id": entry["id"], "reused": True})
        source = DatasetDataSource(handle)
        source._dataset_workspace_id = _scope_value["workspace_id"]
        return jsonify({"ok": True, "source_id": session.add_source(source), "reused": False})
    except (ValueError, TypeError, PermissionError, OSError, KeyError, RuntimeError) as exc:
        return _error(exc)
