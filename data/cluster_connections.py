"""Owner-scoped cluster settings; credentials remain in the OS vault."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import ipaddress
import json
import re
import sqlite3
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from infrastructure.paths import content_data_path


class ClusterError(RuntimeError):
    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code, self.details = code, details

    def to_dict(self):
        return {"code": self.code, "error": str(self), **self.details}


IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-]{0,127}$")
FUNCTIONS = frozenset("""ABS AVG COUNT SUM MIN MAX COALESCE NULLIF CAST TRY_CAST CASE IF
    ROUND FLOOR CEIL CEILING SQRT POW POWER MOD LOWER UPPER LENGTH CHAR_LENGTH
    TRIM LTRIM RTRIM SUBSTRING CONCAT CONCAT_WS REPLACE STRPOS POSITION
    DATE DATE_ADD DATE_SUB DATE_DIFF DATEDIFF DATE_TRUNC EXTRACT YEAR MONTH DAY
    HOUR MINUTE SECOND CURRENT_DATE CURRENT_TIMESTAMP TO_DATE TO_TIMESTAMP
    UNIX_TIMESTAMP FROM_UNIXTIME ROW_NUMBER RANK DENSE_RANK LAG LEAD FIRST_VALUE
    LAST_VALUE STDDEV STDDEV_SAMP STDDEV_POP VARIANCE VAR_SAMP VAR_POP
    APPROX_DISTINCT APPROX_COUNT_DISTINCT PERCENTILE_APPROX PERCENTILE_CONT
    TIMESTAMP_TRUNC TIMESTAMP_DIFF TIME_TO_STR STR_TO_TIME TIME_STR_TO_TIME
    TS_OR_DS_TO_DATE TS_OR_DS_ADD TS_OR_DS_DIFF ISNULL IFNULL REGEXP_LIKE
    ARRAY_SIZE SIZE ELEMENT_AT MAP_KEYS MAP_VALUES GET_JSON_OBJECT
    JSON_EXTRACT JSON_EXTRACT_SCALAR JSON_VALUE JSON_QUERY ARRAY_AGG
    ARRAY_CONTAINS ARRAY_JOIN LOG LN LOG10 EXP SIGN GREATEST LEAST""".split())
CONFIG_KEYS = frozenset({
    "name", "kind", "host", "port", "tls", "auth", "username", "credential_ref",
    "catalog", "schema", "allowed_tables", "allowed_functions", "approved_endpoints",
    "max_response_bytes", "batch_rows", "timeout_seconds", "resource_group", "hs2_driver",
})


def scope_text(value, field, *, empty=True):
    if (not isinstance(value, str) or len(value) > 512 or (not empty and not value.strip())
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ClusterError("cluster_permission_denied", f"Invalid {field}")
    return value


def credential_label(owner_id):
    return "cluster-" + hashlib.sha256(scope_text(owner_id, "owner_id", empty=False).encode()).hexdigest()[:24]


def _text(value, field, *, empty=True, limit=128):
    if (not isinstance(value, str) or len(value) > limit or (not empty and not value)
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ClusterError("invalid_cluster_config", f"Invalid {field}")
    return value


def _integer(value, field, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ClusterError("invalid_cluster_config", f"Invalid {field}")
    return value


def _host(value):
    host = _text(value, "host", empty=False, limit=253).lower()
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        if not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", p) for p in host.split(".")):
            raise ClusterError("invalid_cluster_config", "Use a hostname or IP address without URL, path or user information")
        return host


def normalize_config(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) - CONFIG_KEYS:
        raise ClusterError("invalid_cluster_config", "Unknown connection fields; DSNs, secrets and SQL SET are not accepted")
    cfg = dict(value)
    kind = cfg.setdefault("kind", "trino")
    if kind not in {"trino", "hive", "spark_sql"}:
        raise ClusterError("invalid_cluster_config", "Unsupported cluster kind")
    cfg["name"] = _text(cfg.get("name", kind), "name", empty=False, limit=80)
    cfg["host"] = host = _host(cfg.get("host", ""))
    cfg["port"] = _integer(cfg.get("port", 443 if kind == "trino" else 10000), "port", 1, 65535)
    cfg.setdefault("tls", True)
    if not isinstance(cfg["tls"], bool):
        raise ClusterError("invalid_cluster_config", "tls must be a boolean")
    auth = cfg.setdefault("auth", "none")
    if auth not in ({"none", "basic", "jwt"} if kind == "trino" else {"none", "ldap"}):
        raise ClusterError("unsupported_auth", "This adapter does not implement the requested authentication method")
    if auth != "none" and not cfg["tls"]:
        raise ClusterError("unsupported_auth", "Authentication requires certificate- and hostname-verified TLS")
    cfg["username"] = _text(cfg.get("username", "baa_readonly"), "username", empty=False)
    cfg["credential_ref"] = _text(cfg.get("credential_ref", ""), "credential_ref", limit=256)
    if auth != "none" and not cfg["credential_ref"]:
        raise ClusterError("credential_unavailable", "An OS credential reference is required")
    if auth == "none" and cfg["credential_ref"]:
        raise ClusterError("invalid_cluster_config", "Unauthenticated connections cannot have a credential reference")
    for key in ("catalog", "schema"):
        cfg[key] = _text(cfg.get(key, "" if key == "catalog" else "default"), key, empty=key == "catalog")
        if cfg[key] and not IDENTIFIER.fullmatch(cfg[key]):
            raise ClusterError("invalid_cluster_config", f"Invalid {key}")
    if kind == "trino" and not cfg["catalog"]:
        raise ClusterError("invalid_cluster_config", "Trino requires a default catalog")
    if kind != "trino" and cfg["catalog"]:
        raise ClusterError("invalid_cluster_config", "HS2 catalog selection is not supported; use schema.table")
    tables = cfg.get("allowed_tables", [])
    if not isinstance(tables, list) or not tables or len(tables) > 200:
        raise ClusterError("invalid_cluster_config", "Configure 1 to 200 authorized tables")
    canonical = []
    for name in tables:
        if not isinstance(name, str) or any(not IDENTIFIER.fullmatch(p) for p in name.split(".")):
            raise ClusterError("invalid_cluster_config", "Invalid authorized table")
        parts = name.split(".")
        if len(parts) == 1:
            parts = [cfg["schema"], *parts]
        if kind == "trino" and len(parts) == 2:
            parts = [cfg["catalog"], *parts]
        if len(parts) != (3 if kind == "trino" else 2):
            raise ClusterError("invalid_cluster_config", "Invalid table qualification")
        canonical.append(".".join(parts).lower())
    cfg["allowed_tables"] = sorted(set(canonical))
    functions = cfg.get("allowed_functions", sorted(FUNCTIONS))
    if (not isinstance(functions, list) or len(functions) > len(FUNCTIONS)
            or any(not isinstance(f, str) or f.upper() not in FUNCTIONS for f in functions)):
        raise ClusterError("invalid_cluster_config", "Only approved built-in functions may be enabled")
    cfg["allowed_functions"] = sorted({f.upper() for f in functions})
    cfg["max_response_bytes"] = _integer(cfg.get("max_response_bytes", 1024 * 1024), "max_response_bytes", 1024, 8 * 1024 * 1024)
    cfg["batch_rows"] = _integer(cfg.get("batch_rows", 256), "batch_rows", 1, 2048)
    cfg["timeout_seconds"] = _integer(cfg.get("timeout_seconds", 30), "timeout_seconds", 1, 120)
    cfg["resource_group"] = _text(cfg.get("resource_group", ""), "resource_group", limit=128)
    if cfg["resource_group"] and not re.fullmatch(r"[A-Za-z0-9_.-]+", cfg["resource_group"]):
        raise ClusterError("invalid_cluster_config", "Invalid resource group")
    if kind != "trino" and cfg["resource_group"]:
        raise ClusterError("unsupported_capability", "HS2 resource pools must be configured on the server for this read-only account")
    driver = cfg.get("hs2_driver", "pyhive")
    if driver != "pyhive" or (kind == "trino" and "hs2_driver" in cfg):
        raise ClusterError("dependency_unavailable", "Only the pinned PyHive HS2 driver is supported")
    if kind != "trino":
        cfg["hs2_driver"] = driver
    endpoints = cfg.get("approved_endpoints", [])
    if not isinstance(endpoints, list) or len(endpoints) > 10 or (kind != "trino" and endpoints):
        raise ClusterError("invalid_cluster_config", "Invalid approved endpoints")
    scheme = "https" if cfg["tls"] else "http"
    origin = f"{scheme}://{'[' + host + ']' if ':' in host else host}:{cfg['port']}"
    origins = {origin}
    for endpoint in endpoints:
        if not isinstance(endpoint, str):
            raise ClusterError("invalid_cluster_config", "Invalid approved endpoint")
        try:
            parsed = urlsplit(endpoint)
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            eh = _host(parsed.hostname or "")
        except (ValueError, ClusterError) as exc:
            raise ClusterError("invalid_cluster_config", "Invalid approved endpoint") from exc
        if (parsed.scheme != scheme or parsed.username or parsed.password or parsed.path not in {"", "/"}
                or parsed.query or parsed.fragment or any(ord(c) < 33 for c in endpoint)):
            raise ClusterError("invalid_cluster_config", "Endpoints must be explicit origins with the same TLS policy")
        origins.add(f"{scheme}://{'[' + eh + ']' if ':' in eh else eh}:{port}")
    cfg["approved_endpoints"] = sorted(origins) if kind == "trino" else []
    return cfg


def public_connection(record):
    result = {**record, "config": dict(record["config"])}
    result["config"]["credential_configured"] = bool(result["config"].pop("credential_ref", ""))
    return result


class ClusterConnectionStore:
    def __init__(self, root=None, *, owner_id: str):
        self.owner_id = scope_text(owner_id, "owner_id", empty=False)
        self.root = Path(root) if root is not None else content_data_path("cluster")
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "connections.sqlite3"
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS connections (id TEXT PRIMARY KEY, owner TEXT NOT NULL, config TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL)")

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _record(row):
        return {"connection_id": row["id"], "config": json.loads(row["config"]), "created_at": row["created"], "updated_at": row["updated"]}

    def get(self, connection_id):
        with self._db() as db:
            row = db.execute("SELECT * FROM connections WHERE id=? AND owner=?", (connection_id, self.owner_id)).fetchone()
        if row is None:
            raise ClusterError("cluster_not_found", "Cluster connection not found")
        return self._record(row)

    def list(self):
        with self._db() as db:
            return [public_connection(self._record(r)) for r in db.execute("SELECT * FROM connections WHERE owner=? ORDER BY created", (self.owner_id,))]

    def save(self, config, *, connection_id=""):
        cfg = normalize_config(config)
        if cfg["credential_ref"] and not re.fullmatch(re.escape(credential_label(self.owner_id)) + r":[0-9a-f]{32}", cfg["credential_ref"]):
            raise ClusterError("cluster_permission_denied", "Credential reference does not belong to this owner")
        if connection_id:
            old = self.get(connection_id)["config"]
            boundary = ("kind", "host", "port", "tls", "auth", "username", "approved_endpoints")
            if any(old[k] != cfg[k] for k in boundary) and cfg["credential_ref"] and cfg["credential_ref"] == old["credential_ref"]:
                raise ClusterError("credential_boundary_changed", "Store a new credential when changing the endpoint or authentication boundary")
        now = time.time()
        connection_id = connection_id or uuid.uuid4().hex
        with self._db() as db:
            db.execute("INSERT INTO connections VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET config=excluded.config, updated=excluded.updated WHERE connections.owner=excluded.owner", (connection_id, self.owner_id, json.dumps(cfg), now, now))
        return self.get(connection_id)

    def delete(self, connection_id):
        self.get(connection_id)
        with self._db() as db:
            db.execute("DELETE FROM connections WHERE id=? AND owner=?", (connection_id, self.owner_id))


def config_fingerprint(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
