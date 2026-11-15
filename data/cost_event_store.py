"""Durable numeric-only usage receipts, call attempts and scope visibility."""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from infrastructure.paths import data_path


_NUMERIC_ATTRIBUTION_FIELDS = frozenset({
    "payload_tokens_est", "system_tokens_est", "tool_schema_tokens_est",
    "history_tokens_est", "memory_tokens_est", "tool_result_tokens_est",
    "project_instruction_tokens_est", "current_user_tokens_est",
})
_VALID_SCOPES = frozenset({"session", "workflow_node", "team_task"})
USAGE_SOURCES = frozenset({
    "provider_reported", "operator_verified", "estimated", "offline_adapter",
    "unavailable", "legacy_unknown",
})
MEASUREMENT_MODES = frozenset({"actual_model", "offline_contract", "legacy_unknown"})
EVENT_STATUSES = frozenset({"succeeded", "failed", "canceled", "timeout", "unknown"})
_ACTUAL_SOURCES = frozenset({"provider_reported", "operator_verified"})
_TOKEN_FIELDS = ("input_tokens", "output_tokens", "cached_input_tokens", "cache_write_tokens")
_SCOPE_FIELDS = ("scope_type", "scope_id", "parent_scope_id")


def _optional_number(value: Any, *, integer: bool = False) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or number < 0 or (integer and not number.is_integer()):
        return None
    return int(number) if integer else number


def _non_negative_int(value: Any) -> int:
    return int(_optional_number(value, integer=True) or 0)


def _positive_attempt(value: Any) -> int:
    number = _optional_number(value, integer=True)
    if number is None or number < 1:
        raise ValueError("attempt must be a positive integer")
    return int(number)


def _clean_attribution(value: Mapping[str, Any] | None) -> dict[str, int]:
    source = value if isinstance(value, Mapping) else {}
    return {field: number for field in _NUMERIC_ATTRIBUTION_FIELDS
            if (number := _optional_number(source.get(field), integer=True)) is not None}


def model_cost_event_id(
    model_call_id: str, *, attempt: int = 1, scope_type: str = "session",
    workspace_id: str = "", session_id: str = "", scope_id: str = "",
) -> str:
    """Opaque receipt id; include owner ids when a call id is only locally unique.

    The ledger dedupes the same model_call_id/attempt across scope views within
    one workspace/session. A real provider retry must use a new call id or attempt.
    """
    if not str(model_call_id or "").strip():
        raise ValueError("model_call_id is required")
    if scope_type not in _VALID_SCOPES:
        raise ValueError("unsupported cost-event scope")
    # Scope is a view, not a second billable identity. Keep its parameters for
    # compatibility while returning the same id across all views of one call.
    key = json.dumps([workspace_id, session_id, str(model_call_id), _positive_attempt(attempt)], separators=(",", ":"))
    return "model-call:" + hashlib.sha256(key.encode("utf-8")).hexdigest()


def _measurement_kind(row: Mapping[str, Any]) -> tuple[str, str]:
    source = row.get("usage_source", "legacy_unknown")
    mode = str(row.get("measurement_mode", "legacy_unknown"))
    # An unreported attempt in an actual-model run is missing measurement,
    # not an estimate/offline unit. Keep its nulls while exposing the known
    # subtotal of reported attempts in that same measurement mode.
    compatible = source in _ACTUAL_SOURCES or (mode == "actual_model" and source == "unavailable")
    return mode, "actual" if compatible else str(source)


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(rows)
    homogeneous = len({_measurement_kind(row) for row in rows}) <= 1
    result: dict[str, Any] = {"event_count": count, "mixed_measurements": not homogeneous}
    for field in _TOKEN_FIELDS:
        known = [row[field] for row in rows if row.get(field) is not None]
        result[field] = sum(known) if count and len(known) == count and homogeneous else None
        result[f"known_{field}"] = sum(known) if known and homogeneous else None
    result["tool_call_count"] = sum(row.get("tool_call_count", 0) for row in rows)
    priced = [row["estimated_cost"] for row in rows if row.get("estimated_cost") is not None]
    result["priced_event_count"] = len(priced)
    price_keys = {(row.get("currency"), row.get("price_version")) for row in rows}
    result["cost_configured"] = bool(
        count and homogeneous and len(priced) == count and len(price_keys) == 1
        and all(next(iter(price_keys)))
    )
    result["estimated_cost"] = sum(priced) if result["cost_configured"] else None
    result["currency"] = next(iter(price_keys))[0] if len(price_keys) == 1 else ""
    result["price_version"] = next(iter(price_keys))[1] if len(price_keys) == 1 else ""
    result["usage_available_event_count"] = sum(
        row.get("input_tokens") is not None and row.get("output_tokens") is not None for row in rows
    )
    result["unknown_usage_event_count"] = count - result["usage_available_event_count"]
    result["actual_usage_event_count"] = sum(
        row.get("measurement_mode") == "actual_model" and row.get("usage_source") in _ACTUAL_SOURCES
        and row.get("input_tokens") is not None and row.get("output_tokens") is not None for row in rows
    )
    inputs, cached = result["input_tokens"], result["cached_input_tokens"]
    result["cache_hit_ratio"] = round(cached / inputs, 4) if inputs and cached is not None else None
    return result


class CostEventStore:
    """SQLite ledger with one charge per call attempt and multiple scope views.

    Legacy NOT NULL token columns remain intact; availability flags expose their
    values as nullable. Historic zero defaults never become reported zero usage.
    Scope associations do not create another billed event. Old rows without call
    identity retain their independent identity; their relationship is unknown.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else data_path("outputs", "cost_events.sqlite3")
        self._lock = threading.RLock()
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _initialize(self) -> None:
        with self._lock, self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cost_events (
                    event_id TEXT PRIMARY KEY, occurred_at TEXT NOT NULL,
                    workspace_id TEXT NOT NULL DEFAULT '', session_id TEXT NOT NULL DEFAULT '',
                    scope_type TEXT NOT NULL, scope_id TEXT NOT NULL DEFAULT '',
                    parent_scope_id TEXT NOT NULL DEFAULT '', provider TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '', input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0, cached_input_tokens INTEGER NOT NULL DEFAULT 0,
                    cache_write_tokens INTEGER NOT NULL DEFAULT 0, tool_call_count INTEGER NOT NULL DEFAULT 0,
                    latency_ms INTEGER, estimated_cost REAL, currency TEXT NOT NULL DEFAULT '',
                    price_version TEXT NOT NULL DEFAULT '', attribution_json TEXT NOT NULL DEFAULT '{}'
                )
            """)
            existing = {row[1] for row in conn.execute("PRAGMA table_info(cost_events)")}
            additions = {
                "status": "TEXT NOT NULL DEFAULT 'unknown'",
                "usage_source": "TEXT NOT NULL DEFAULT 'legacy_unknown'",
                "measurement_mode": "TEXT NOT NULL DEFAULT 'legacy_unknown'",
                "model_call_id": "TEXT NOT NULL DEFAULT ''",
                "attempt": "INTEGER NOT NULL DEFAULT 1",
                "retry_of_event_id": "TEXT NOT NULL DEFAULT ''",
                **{field + "_available": "INTEGER NOT NULL DEFAULT 0" for field in _TOKEN_FIELDS},
            }
            for column, declaration in additions.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE cost_events ADD COLUMN {column} {declaration}")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cost_events_scope "
                         "ON cost_events(workspace_id, session_id, scope_type, scope_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cost_events_occurred ON cost_events(occurred_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cost_events_call "
                         "ON cost_events(workspace_id, session_id, model_call_id, attempt)")
            conn.execute("""CREATE TABLE IF NOT EXISTS cost_event_scopes (
                event_id TEXT NOT NULL, scope_type TEXT NOT NULL, scope_id TEXT NOT NULL DEFAULT '',
                parent_scope_id TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(event_id, scope_type, scope_id, parent_scope_id)
            )""")
            conn.execute("INSERT OR IGNORE INTO cost_event_scopes SELECT event_id,scope_type,scope_id,parent_scope_id FROM cost_events")
            self._migrate_call_views(conn)

    @staticmethod
    def _migrate_call_views(conn: sqlite3.Connection) -> None:
        """Coalesce identified receipts from the earlier per-scope ledger layout.

        Unknown legacy identities stay independent. Conflicting identified usage
        aborts the transaction rather than silently choosing a billed amount.
        """
        duplicates = conn.execute(
            "SELECT workspace_id,session_id,model_call_id,attempt FROM cost_events WHERE model_call_id<>'' "
            "GROUP BY workspace_id,session_id,model_call_id,attempt HAVING COUNT(*)>1"
        ).fetchall()
        for identity in duplicates:
            rows = conn.execute(
                "SELECT * FROM cost_events WHERE workspace_id=? AND session_id=? AND model_call_id=? AND attempt=? "
                "ORDER BY occurred_at,event_id", tuple(identity),
            ).fetchall()
            canonical = dict(rows[0])
            for duplicate in rows[1:]:
                for field in ("provider", "model", "measurement_mode", "currency", "price_version"):
                    known = {canonical[field], duplicate[field]} - {"", "legacy_unknown"}
                    if len(known) > 1:
                        raise ValueError("conflicting legacy call receipts require reconciliation")
                    if known:
                        canonical[field] = next(iter(known))
                sources = {canonical["usage_source"], duplicate["usage_source"]} - {"unavailable", "legacy_unknown"}
                if len(sources) > 1 and not sources.issubset(_ACTUAL_SOURCES):
                    raise ValueError("conflicting legacy usage provenance requires reconciliation")
                if sources:
                    canonical["usage_source"] = "provider_reported" if "provider_reported" in sources else next(iter(sources))
                for field in _TOKEN_FIELDS:
                    if duplicate[field + "_available"]:
                        if canonical[field + "_available"] and canonical[field] != duplicate[field]:
                            raise ValueError("conflicting legacy usage counters require reconciliation")
                        canonical[field], canonical[field + "_available"] = duplicate[field], 1
                if duplicate["estimated_cost"] is not None:
                    if canonical["estimated_cost"] is not None and canonical["estimated_cost"] != duplicate["estimated_cost"]:
                        raise ValueError("conflicting legacy costs require reconciliation")
                    canonical["estimated_cost"] = duplicate["estimated_cost"]
                canonical["tool_call_count"] = max(canonical["tool_call_count"], duplicate["tool_call_count"])
                if duplicate["status"] != "unknown":
                    canonical["status"] = duplicate["status"]
                if canonical["latency_ms"] is None:
                    canonical["latency_ms"] = duplicate["latency_ms"]
                if canonical["attribution_json"] == "{}":
                    canonical["attribution_json"] = duplicate["attribution_json"]
                if not canonical["retry_of_event_id"]:
                    canonical["retry_of_event_id"] = duplicate["retry_of_event_id"]
                conn.execute(
                    "INSERT OR IGNORE INTO cost_event_scopes SELECT ?,scope_type,scope_id,parent_scope_id FROM cost_event_scopes WHERE event_id=?",
                    (canonical["event_id"], duplicate["event_id"]),
                )
                conn.execute("UPDATE cost_events SET retry_of_event_id=? WHERE retry_of_event_id=?",
                             (canonical["event_id"], duplicate["event_id"]))
                conn.execute("DELETE FROM cost_event_scopes WHERE event_id=?", (duplicate["event_id"],))
                conn.execute("DELETE FROM cost_events WHERE event_id=?", (duplicate["event_id"],))
            columns = [field for field in canonical if field != "event_id"]
            conn.execute("UPDATE cost_events SET " + ",".join(field + "=?" for field in columns) + " WHERE event_id=?",
                         [*(canonical[field] for field in columns), canonical["event_id"]])

    def record(
        self, *, event_id: str, occurred_at: str, scope_type: str,
        scope_id: str = "", parent_scope_id: str = "", workspace_id: str = "",
        session_id: str = "", provider: str = "", model: str = "",
        input_tokens: int | None = None, output_tokens: int | None = None,
        cached_input_tokens: int | None = None, cache_write_tokens: int | None = None,
        tool_call_count: int = 0, latency_ms: int | None = None,
        estimated_cost: float | None = None, currency: str = "", price_version: str = "",
        attribution: Mapping[str, Any] | None = None, status: str = "unknown",
        usage_source: str = "legacy_unknown", measurement_mode: str = "legacy_unknown",
        model_call_id: str = "", attempt: int = 1, retry_of_event_id: str = "",
    ) -> bool:
        """Upsert a receipt; unknown receipts cannot erase measured usage.

        Actual retries get distinct call ids/attempts, including failed calls with
        usage. Cost is retained only for actual_model and verified/reported usage.
        """
        if not str(event_id or "").strip() or len(str(event_id)) > 160:
            raise ValueError("event_id must contain 1..160 characters")
        if scope_type not in _VALID_SCOPES:
            raise ValueError(f"unsupported cost-event scope: {scope_type}")
        try:
            occurred = datetime.fromisoformat(str(occurred_at).replace("Z", "+00:00"))
            if occurred.tzinfo is None:
                raise ValueError("occurred_at requires a timezone")
            timestamp = occurred.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        except (ValueError, TypeError) as exc:
            raise ValueError("occurred_at must be an ISO timestamp with timezone") from exc
        if status not in EVENT_STATUSES or usage_source not in USAGE_SOURCES or measurement_mode not in MEASUREMENT_MODES:
            raise ValueError("unsupported cost-event status, usage source, or measurement mode")
        attempt = _positive_attempt(attempt)
        for name, value, bound in (("model_call_id", model_call_id, 160), ("workspace_id", workspace_id, 160),
                                   ("session_id", session_id, 160), ("scope_id", scope_id, 160),
                                   ("parent_scope_id", parent_scope_id, 160)):
            if len(str(value or "")) > bound:
                raise ValueError(f"{name} is too long")
        numbers = {field: _optional_number(value, integer=True) for field, value in (
            ("input_tokens", input_tokens), ("output_tokens", output_tokens),
            ("cached_input_tokens", cached_input_tokens), ("cache_write_tokens", cache_write_tokens),
        )}
        if usage_source == "unavailable":
            numbers = {field: None for field in _TOKEN_FIELDS}
        has_usage = numbers["input_tokens"] is not None and numbers["output_tokens"] is not None
        safe_cost = (_optional_number(estimated_cost) if has_usage and measurement_mode == "actual_model"
                     and usage_source in _ACTUAL_SOURCES else None)
        record = {
            "event_id": str(event_id), "occurred_at": timestamp,
            "workspace_id": str(workspace_id or ""), "session_id": str(session_id or ""),
            "scope_type": scope_type, "scope_id": str(scope_id or ""),
            "parent_scope_id": str(parent_scope_id or ""), "provider": str(provider or "")[:120],
            "model": str(model or "")[:160], **{field: value or 0 for field, value in numbers.items()},
            **{field + "_available": int(value is not None) for field, value in numbers.items()},
            "tool_call_count": _non_negative_int(tool_call_count),
            "latency_ms": _optional_number(latency_ms, integer=True), "estimated_cost": safe_cost,
            "currency": str(currency or "")[:12], "price_version": str(price_version or "")[:80],
            "attribution_json": json.dumps(_clean_attribution(attribution), separators=(",", ":")),
            "status": status, "usage_source": usage_source, "measurement_mode": measurement_mode,
            "model_call_id": str(model_call_id or ""), "attempt": attempt,
            "retry_of_event_id": str(retry_of_event_id or "")[:160],
        }
        visibility = tuple(record[field] for field in _SCOPE_FIELDS)
        with self._lock, self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            receipt = conn.execute("SELECT * FROM cost_events WHERE event_id=?", (record["event_id"],)).fetchone()
            identity = ("workspace_id", "session_id", "model_call_id", "attempt")
            if receipt and any(receipt[field] != record[field] for field in identity):
                raise ValueError("event_id cannot overwrite a distinct owner, call or retry attempt")
            prior = None
            if record["model_call_id"]:
                prior = conn.execute(
                    "SELECT * FROM cost_events WHERE workspace_id=? AND session_id=? AND model_call_id=? AND attempt=? "
                    "ORDER BY occurred_at,event_id LIMIT 1", tuple(record[field] for field in identity),
                ).fetchone()
            existing = prior or receipt
            if existing:
                if not record["model_call_id"] and any(existing[field] != record[field] for field in _SCOPE_FIELDS):
                    raise ValueError("scope cannot change without model_call_id")
                for field in ("measurement_mode", "provider", "model"):
                    unknown = {"", "legacy_unknown"}
                    if record[field] not in unknown and existing[field] not in unknown and record[field] != existing[field]:
                        raise ValueError(f"call receipt has conflicting {field}")
                    if record[field] in unknown:
                        record[field] = existing[field]
                old_source, new_source = existing["usage_source"], record["usage_source"]
                if old_source not in {"unavailable", "legacy_unknown"} and new_source not in {"unavailable", "legacy_unknown"}:
                    if (old_source in _ACTUAL_SOURCES) != (new_source in _ACTUAL_SOURCES) or (
                        old_source not in _ACTUAL_SOURCES and old_source != new_source
                    ):
                        raise ValueError("call receipt has conflicting usage provenance")
                if new_source in {"unavailable", "legacy_unknown"}:
                    record["usage_source"] = old_source
                usage_changed = any(record[field + "_available"] and (
                    not existing[field + "_available"] or record[field] != existing[field]
                ) for field in _TOKEN_FIELDS)
                for field in _TOKEN_FIELDS:
                    if not record[field + "_available"] and existing[field + "_available"]:
                        record[field], record[field + "_available"] = existing[field], 1
                if record["estimated_cost"] is None and existing["estimated_cost"] is not None and not usage_changed:
                    for field in ("estimated_cost", "currency", "price_version"):
                        record[field] = existing[field]
                for field in ("event_id", "occurred_at", *_SCOPE_FIELDS):
                    record[field] = existing[field]
                record["tool_call_count"] = max(record["tool_call_count"], existing["tool_call_count"])
                for field in ("latency_ms", "retry_of_event_id"):
                    if record[field] in {None, ""}:
                        record[field] = existing[field]
                if record["attribution_json"] == "{}":
                    record["attribution_json"] = existing["attribution_json"]
                if record["status"] == "unknown":
                    record["status"] = existing["status"]
            columns = list(record)
            updates = ",".join(f"{field}=excluded.{field}" for field in columns if field != "event_id")
            conn.execute(
                f"INSERT INTO cost_events ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)}) "
                f"ON CONFLICT(event_id) DO UPDATE SET {updates}", list(record.values()),
            )
            conn.execute("INSERT OR IGNORE INTO cost_event_scopes VALUES (?,?,?,?)", (record["event_id"], *visibility))
        return True

    @staticmethod
    def _where(**filters: str) -> tuple[str, list[str]]:
        allowed = {"workspace_id", "session_id", *_SCOPE_FIELDS, "measurement_mode", "status", "usage_source"}
        if set(filters) - allowed:
            raise ValueError("unsupported cost-event filter")
        parts, params, scopes, scope_params = [], [], [], []
        for field, value in filters.items():
            if not value:
                continue
            if field in _SCOPE_FIELDS:
                scopes.append(f"s.{field}=?")
                scope_params.append(str(value))
            else:
                parts.append(f"e.{field}=?")
                params.append(str(value))
        if scopes:
            parts.append("EXISTS (SELECT 1 FROM cost_event_scopes s WHERE s.event_id=e.event_id AND " + " AND ".join(scopes) + ")")
            params.extend(scope_params)
        return (" WHERE " + " AND ".join(parts) if parts else "", params)

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        for field in _TOKEN_FIELDS:
            if not item.pop(field + "_available", 0):
                item[field] = None
        if (item["input_tokens"] is None or item["output_tokens"] is None
                or item["measurement_mode"] != "actual_model" or item["usage_source"] not in _ACTUAL_SOURCES):
            item["estimated_cost"] = None
        try:
            item["attribution"] = json.loads(item.pop("attribution_json") or "{}")
        except json.JSONDecodeError:
            item["attribution"] = {}
        return item

    def summary(self, *, workspace_id: str = "", session_id: str = "", scope_type: str = "",
                scope_id: str = "", parent_scope_id: str = "", measurement_mode: str = "",
                status: str = "", usage_source: str = "") -> dict[str, Any]:
        """Unique events; mixed modes have null totals and explicit mode groups."""
        where, params = self._where(**locals_without_self(locals()))
        with self._lock, self._connection() as conn:
            rows = [self._decode(row) for row in conn.execute("SELECT e.* FROM cost_events e" + where, params)]
        result = _aggregate(rows)
        groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        modes: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            key = (row["measurement_mode"], row["status"], row["usage_source"])
            groups.setdefault(key, []).append(row)
            modes.setdefault(row["measurement_mode"], []).append(row)
        result["groups"] = [
            {"measurement_mode": key[0], "status": key[1], "usage_source": key[2], **_aggregate(group)}
            for key, group in sorted(groups.items())
        ]
        result["measurement_totals"] = {mode: _aggregate(group) for mode, group in sorted(modes.items())}
        return result

    def cache_trends(self, *, workspace_id: str = "", session_id: str = "", scope_type: str = "",
                     scope_id: str = "", parent_scope_id: str = "", days: int = 90) -> list[dict[str, Any]]:
        bounded_days = max(1, min(int(days or 90), 90))
        cutoff = (datetime.now(timezone.utc) - timedelta(days=bounded_days - 1)).strftime("%Y-%m-%dT00:00:00Z")
        filters = {key: value for key, value in locals_without_self(locals()).items() if key not in {"days", "bounded_days", "cutoff"}}
        where, params = self._where(**filters)
        where += (" AND " if where else " WHERE ") + "e.occurred_at>=?"
        with self._lock, self._connection() as conn:
            rows = [self._decode(row) for row in conn.execute("SELECT e.* FROM cost_events e" + where, [*params, cutoff])]
        groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
        for row in rows:
            key = (row["occurred_at"][:10], row["provider"], row["model"], row["measurement_mode"], row["status"], row["usage_source"])
            groups.setdefault(key, []).append(row)
        names = ("day", "provider", "model", "measurement_mode", "status", "usage_source")
        return [{**dict(zip(names, key)), **_aggregate(group)} for key, group in sorted(groups.items())]

    def list_events(self, *, workspace_id: str = "", session_id: str = "", scope_type: str = "",
                    scope_id: str = "", parent_scope_id: str = "", limit: int = 100,
                    measurement_mode: str = "", status: str = "", usage_source: str = "") -> list[dict[str, Any]]:
        filters = {key: value for key, value in locals_without_self(locals()).items() if key != "limit"}
        where, params = self._where(**filters)
        with self._lock, self._connection() as conn:
            rows = conn.execute("SELECT e.* FROM cost_events e" + where + " ORDER BY e.occurred_at DESC,e.event_id DESC LIMIT ?",
                                [*params, max(1, min(int(limit or 100), 500))]).fetchall()
            items = []
            for row in rows:
                item = self._decode(row)
                item["scopes"] = [dict(scope) for scope in conn.execute(
                    "SELECT scope_type,scope_id,parent_scope_id FROM cost_event_scopes WHERE event_id=? ORDER BY scope_type,scope_id,parent_scope_id",
                    (item["event_id"],),
                )]
                items.append(item)
        return items


def locals_without_self(values: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if key != "self"}


_store: CostEventStore | None = None
_store_lock = threading.Lock()


def get_cost_event_store() -> CostEventStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = CostEventStore()
        return _store
