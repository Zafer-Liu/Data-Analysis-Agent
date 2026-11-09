"""Durable, privacy-safe LLM cost-event storage.

Session state intentionally retains only a small live window of usage
breakdowns.  This store keeps the numeric audit trail needed to compare
interactive chats, Workflow nodes, and dynamic team tasks without persisting
prompts, tool arguments, model reasoning, or source data.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from infrastructure.paths import data_path


_NUMERIC_ATTRIBUTION_FIELDS = frozenset({
    "payload_tokens_est",
    "system_tokens_est",
    "tool_schema_tokens_est",
    "history_tokens_est",
    "memory_tokens_est",
    "tool_result_tokens_est",
    "project_instruction_tokens_est",
    "current_user_tokens_est",
})
_VALID_SCOPES = frozenset({"session", "workflow_node", "team_task"})


def _non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _clean_attribution(value: Mapping[str, Any] | None) -> dict[str, int]:
    source = value if isinstance(value, Mapping) else {}
    return {
        field: _non_negative_int(source.get(field))
        for field in _NUMERIC_ATTRIBUTION_FIELDS
        if source.get(field) is not None
    }


class CostEventStore:
    """A small SQLite store with idempotent upserts keyed by ``event_id``."""

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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cost_events (
                    event_id TEXT PRIMARY KEY,
                    occurred_at TEXT NOT NULL,
                    workspace_id TEXT NOT NULL DEFAULT '',
                    session_id TEXT NOT NULL DEFAULT '',
                    scope_type TEXT NOT NULL,
                    scope_id TEXT NOT NULL DEFAULT '',
                    parent_scope_id TEXT NOT NULL DEFAULT '',
                    provider TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    cached_input_tokens INTEGER NOT NULL DEFAULT 0,
                    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
                    tool_call_count INTEGER NOT NULL DEFAULT 0,
                    latency_ms INTEGER,
                    estimated_cost REAL,
                    currency TEXT NOT NULL DEFAULT '',
                    price_version TEXT NOT NULL DEFAULT '',
                    attribution_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cost_events_scope "
                "ON cost_events(workspace_id, session_id, scope_type, scope_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cost_events_occurred "
                "ON cost_events(occurred_at)"
            )

    def record(
        self,
        *,
        event_id: str,
        occurred_at: str,
        scope_type: str,
        scope_id: str = "",
        parent_scope_id: str = "",
        workspace_id: str = "",
        session_id: str = "",
        provider: str = "",
        model: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        cached_input_tokens: int = 0,
        cache_write_tokens: int = 0,
        tool_call_count: int = 0,
        latency_ms: int | None = None,
        estimated_cost: float | None = None,
        currency: str = "",
        price_version: str = "",
        attribution: Mapping[str, Any] | None = None,
    ) -> bool:
        """Insert or replace a numeric-only event.

        Reusing the same event id updates a retried hand-off instead of
        double-counting it.  Callers must use a new event id for a distinct
        model call.
        """
        if not str(event_id or "").strip():
            raise ValueError("event_id is required")
        if scope_type not in _VALID_SCOPES:
            raise ValueError(f"unsupported cost-event scope: {scope_type}")
        if not str(occurred_at or "").strip():
            raise ValueError("occurred_at is required")
        safe_cost = None
        if estimated_cost is not None:
            try:
                safe_cost = max(0.0, float(estimated_cost))
            except (TypeError, ValueError):
                safe_cost = None
        safe_latency = None if latency_ms is None else _non_negative_int(latency_ms)
        values = (
            str(event_id)[:160], str(occurred_at)[:40], str(workspace_id or "")[:160],
            str(session_id or "")[:160], scope_type, str(scope_id or "")[:160],
            str(parent_scope_id or "")[:160], str(provider or "")[:120], str(model or "")[:160],
            _non_negative_int(input_tokens), _non_negative_int(output_tokens),
            _non_negative_int(cached_input_tokens), _non_negative_int(cache_write_tokens),
            _non_negative_int(tool_call_count), safe_latency, safe_cost,
            str(currency or "")[:12], str(price_version or "")[:80],
            json.dumps(_clean_attribution(attribution), separators=(",", ":")),
        )
        with self._lock, self._connection() as conn:
            conn.execute(
                """
                INSERT INTO cost_events (
                    event_id, occurred_at, workspace_id, session_id, scope_type,
                    scope_id, parent_scope_id, provider, model, input_tokens,
                    output_tokens, cached_input_tokens, cache_write_tokens,
                    tool_call_count, latency_ms, estimated_cost, currency,
                    price_version, attribution_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    occurred_at=excluded.occurred_at,
                    workspace_id=excluded.workspace_id,
                    session_id=excluded.session_id,
                    scope_type=excluded.scope_type,
                    scope_id=excluded.scope_id,
                    parent_scope_id=excluded.parent_scope_id,
                    provider=excluded.provider,
                    model=excluded.model,
                    input_tokens=excluded.input_tokens,
                    output_tokens=excluded.output_tokens,
                    cached_input_tokens=excluded.cached_input_tokens,
                    cache_write_tokens=excluded.cache_write_tokens,
                    tool_call_count=excluded.tool_call_count,
                    latency_ms=excluded.latency_ms,
                    estimated_cost=excluded.estimated_cost,
                    currency=excluded.currency,
                    price_version=excluded.price_version,
                    attribution_json=excluded.attribution_json
                """,
                values,
            )
        return True

    def summary(
        self,
        *,
        workspace_id: str = "",
        session_id: str = "",
        scope_type: str = "",
        scope_id: str = "",
        parent_scope_id: str = "",
    ) -> dict[str, Any]:
        """Return scoped aggregates without exposing any prompt-adjacent text."""
        clauses: list[str] = []
        params: list[str] = []
        for column, value in (
            ("workspace_id", workspace_id),
            ("session_id", session_id),
            ("scope_type", scope_type),
            ("scope_id", scope_id),
            ("parent_scope_id", parent_scope_id),
        ):
            if value:
                clauses.append(f"{column} = ?")
                params.append(str(value))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._lock, self._connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS event_count, "
                "COALESCE(SUM(input_tokens), 0) AS input_tokens, "
                "COALESCE(SUM(output_tokens), 0) AS output_tokens, "
                "COALESCE(SUM(cached_input_tokens), 0) AS cached_input_tokens, "
                "COALESCE(SUM(cache_write_tokens), 0) AS cache_write_tokens, "
                "COALESCE(SUM(tool_call_count), 0) AS tool_call_count, "
                "COALESCE(SUM(CASE WHEN estimated_cost IS NOT NULL THEN 1 ELSE 0 END), 0) "
                "AS priced_event_count, "
                "SUM(estimated_cost) AS estimated_cost "
                "FROM cost_events" + where,
                params,
            ).fetchone()
        result = dict(row or {})
        event_count = _non_negative_int(result.get("event_count"))
        priced_event_count = _non_negative_int(result.get("priced_event_count"))
        result["cost_configured"] = bool(event_count and event_count == priced_event_count)
        if priced_event_count != event_count:
            result["estimated_cost"] = None
        input_tokens = _non_negative_int(result.get("input_tokens"))
        cached_input_tokens = _non_negative_int(result.get("cached_input_tokens"))
        result["cache_hit_ratio"] = (
            round(cached_input_tokens / input_tokens, 4) if input_tokens else None
        )
        return result

    def cache_trends(
        self,
        *,
        workspace_id: str = "",
        session_id: str = "",
        scope_type: str = "",
        scope_id: str = "",
        parent_scope_id: str = "",
        days: int = 90,
    ) -> list[dict[str, Any]]:
        """Return numeric cache-hit trend buckets, never prompt or tool content."""
        bounded_days = max(1, min(int(days or 90), 90))
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=bounded_days - 1)
        ).strftime("%Y-%m-%dT00:00:00Z")
        clauses = ["occurred_at >= ?"]
        params: list[str] = [cutoff]
        for column, value in (
            ("workspace_id", workspace_id),
            ("session_id", session_id),
            ("scope_type", scope_type),
            ("scope_id", scope_id),
            ("parent_scope_id", parent_scope_id),
        ):
            if value:
                clauses.append(f"{column} = ?")
                params.append(str(value))
        with self._lock, self._connection() as conn:
            rows = conn.execute(
                "SELECT substr(occurred_at, 1, 10) AS day, provider, model, "
                "COUNT(*) AS event_count, "
                "COALESCE(SUM(input_tokens), 0) AS input_tokens, "
                "COALESCE(SUM(cached_input_tokens), 0) AS cached_input_tokens, "
                "COALESCE(SUM(cache_write_tokens), 0) AS cache_write_tokens "
                "FROM cost_events WHERE " + " AND ".join(clauses)
                + " GROUP BY day, provider, model ORDER BY day ASC, provider ASC, model ASC",
                params,
            ).fetchall()
        trends: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            input_tokens = _non_negative_int(item.get("input_tokens"))
            cached_input_tokens = _non_negative_int(item.get("cached_input_tokens"))
            item["cache_hit_ratio"] = (
                round(cached_input_tokens / input_tokens, 4) if input_tokens else None
            )
            trends.append(item)
        return trends

    def list_events(
        self,
        *,
        workspace_id: str = "",
        session_id: str = "",
        scope_type: str = "",
        scope_id: str = "",
        parent_scope_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return a bounded, numeric-only cost-event audit trail."""
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("workspace_id", workspace_id),
            ("session_id", session_id),
            ("scope_type", scope_type),
            ("scope_id", scope_id),
            ("parent_scope_id", parent_scope_id),
        ):
            if value:
                clauses.append(f"{column} = ?")
                params.append(str(value))
        params.append(max(1, min(int(limit or 100), 500)))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._lock, self._connection() as conn:
            rows = conn.execute(
                "SELECT event_id, occurred_at, scope_type, scope_id, parent_scope_id, "
                "provider, model, input_tokens, output_tokens, cached_input_tokens, "
                "cache_write_tokens, tool_call_count, latency_ms, estimated_cost, "
                "currency, price_version, attribution_json "
                "FROM cost_events" + where + " ORDER BY occurred_at DESC, event_id DESC LIMIT ?",
                params,
            ).fetchall()
        events = []
        for row in rows:
            item = dict(row)
            try:
                item["attribution"] = json.loads(item.pop("attribution_json") or "{}")
            except json.JSONDecodeError:
                item["attribution"] = {}
            events.append(item)
        return events


_store: CostEventStore | None = None
_store_lock = threading.Lock()


def get_cost_event_store() -> CostEventStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = CostEventStore()
        return _store
