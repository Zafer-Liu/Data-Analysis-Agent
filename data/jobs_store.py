#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SQLite persistence for jobs and their replayable event stream."""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from infrastructure.paths import content_data_path
from typing import Any, Dict, List, Mapping, Optional

log = logging.getLogger(__name__)

STATUS_CREATED = "created"
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_CANCELING = "canceling"
STATUS_CANCELED = "canceled"

_TERMINAL = {STATUS_SUCCEEDED, STATUS_FAILED, STATUS_CANCELED}

EVENT_RETENTION_DAYS = 30
MAX_EVENTS_PER_SESSION = 5000

# Compatibility aliases for callers outside this module during the B migration.
STATUS_STARTED = STATUS_RUNNING
STATUS_PROGRESS = STATUS_RUNNING
STATUS_DONE = STATUS_SUCCEEDED
STATUS_ERROR = STATUS_FAILED

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    workspace_id TEXT DEFAULT '',
    type TEXT NOT NULL,
    label TEXT DEFAULT '',
    parent_id TEXT DEFAULT '',
    status TEXT NOT NULL,
    progress INTEGER DEFAULT 0,
    message TEXT DEFAULT '',
    result TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_session ON jobs(session_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS job_event_sequences (
    session_id TEXT PRIMARY KEY,
    last_sequence INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS job_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(session_id, sequence)
);
CREATE INDEX IF NOT EXISTS idx_job_events_replay
    ON job_events(session_id, sequence);
CREATE INDEX IF NOT EXISTS idx_job_events_job
    ON job_events(job_id, sequence);
"""

_ALLOWED_TRANSITIONS = {
    STATUS_CREATED: {STATUS_QUEUED, STATUS_CANCELING, STATUS_CANCELED},
    STATUS_QUEUED: {STATUS_RUNNING, STATUS_CANCELING, STATUS_CANCELED},
    STATUS_RUNNING: {
        STATUS_RUNNING, STATUS_SUCCEEDED, STATUS_FAILED,
        STATUS_CANCELING, STATUS_CANCELED,
    },
    STATUS_CANCELING: {STATUS_CANCELED, STATUS_FAILED},
    STATUS_SUCCEEDED: set(),
    STATUS_FAILED: set(),
    STATUS_CANCELED: set(),
}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    data = dict(row)
    if data.get("result"):
        try:
            data["result"] = json.loads(data["result"])
        except (json.JSONDecodeError, TypeError):
            pass
    return data


def _event_row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    payload = json.loads(row["payload_json"])
    return {
        **payload,
        "type": row["event_type"],
        "job_id": row["job_id"],
        "session_id": row["session_id"],
        "sequence": row["sequence"],
        "created_at": row["created_at"],
    }


class JobsStore:
    """Thread-safe jobs CRUD plus a durable, per-session ordered event log."""

    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            db_path = content_data_path("outputs", "jobs", "jobs.db")
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._path = db_path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(db_path), check_same_thread=False, timeout=30.0,
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.executescript(_SCHEMA)
            self._ensure_columns_locked()
            self._migrate_legacy_statuses_locked()
            self._recover_interrupted_jobs_locked()
            self._conn.commit()
        self.cleanup_events()
        log.info("[jobs] store opened at %s", db_path)

    def _ensure_columns_locked(self) -> None:
        columns = {
            row["name"] for row in self._conn.execute("PRAGMA table_info(jobs)")
        }
        for name, ddl in {
            "label": "TEXT DEFAULT ''",
            "parent_id": "TEXT DEFAULT ''",
            "message": "TEXT DEFAULT ''",
            "updated_at": "TEXT",
            "workspace_id": "TEXT DEFAULT ''",
        }.items():
            if name not in columns:
                self._conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {ddl}")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_workspace ON jobs(workspace_id)"
        )

    def _migrate_legacy_statuses_locked(self) -> None:
        mapping = {
            "started": STATUS_RUNNING,
            "progress": STATUS_RUNNING,
            "done": STATUS_SUCCEEDED,
            "error": STATUS_FAILED,
        }
        for old, new in mapping.items():
            self._conn.execute(
                "UPDATE jobs SET status = ?, updated_at = COALESCE(updated_at, ?) "
                "WHERE status = ?",
                (new, _now_iso(), old),
            )

    def _recover_interrupted_jobs_locked(self) -> None:
        """Close jobs whose worker disappeared during an application restart."""
        placeholders = ",".join("?" for _ in _TERMINAL)
        rows = self._conn.execute(
            f"SELECT * FROM jobs WHERE status NOT IN ({placeholders})",
            tuple(_TERMINAL),
        ).fetchall()
        for row in rows:
            now = _now_iso()
            if row["status"] == STATUS_CANCELING:
                status = STATUS_CANCELED
                error = None
                event = {
                    "type": "job_canceled", "job_id": row["id"],
                    "status": STATUS_CANCELED,
                }
            else:
                status = STATUS_FAILED
                error = "Application restarted before the job completed."
                event = {
                    "type": "job_error", "job_id": row["id"],
                    "status": STATUS_FAILED, "error": error,
                }
            self._conn.execute(
                "UPDATE jobs SET status = ?, error = ?, updated_at = ?, finished_at = ? "
                "WHERE id = ?",
                (status, error, now, now, row["id"]),
            )
            self._append_event_locked(row["session_id"], row["id"], event, now)
        if rows:
            log.warning("[jobs] recovered %d interrupted jobs after restart", len(rows))

    # ── Creation and transitions ──────────────────────────────────────────

    def create(
        self,
        session_id: str,
        job_type: str,
        label: str = "",
        parent_id: str = "",
        workspace_id: str = "",
    ) -> Dict[str, Any]:
        jid = str(uuid.uuid4())[:12]
        now = _now_iso()
        payload = {
            "type": "job_created",
            "job_id": jid,
            "job_type": job_type,
            "label": label,
            "status": STATUS_CREATED,
            "workspace_id": workspace_id,
        }
        with self._transaction():
            self._conn.execute(
                "INSERT INTO jobs "
                "(id, session_id, workspace_id, type, label, parent_id, status, progress, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
                (
                    jid, session_id, workspace_id, job_type, label, parent_id,
                    STATUS_CREATED, now, now,
                ),
            )
            self._append_event_locked(session_id, jid, payload, now)
        return self.get(jid)  # type: ignore[return-value]

    def mark_queued(self, jid: str) -> bool:
        return self._transition(jid, STATUS_QUEUED)

    def mark_started(self, jid: str) -> bool:
        return self._transition(
            jid,
            STATUS_RUNNING,
            event={"type": "job_started", "job_id": jid, "status": STATUS_RUNNING},
            started_at=_now_iso(),
        )

    def set_progress(self, jid: str, progress: int, message: str = "") -> bool:
        progress = max(0, min(100, int(progress)))
        with self._transaction():
            row = self._get_row_locked(jid)
            if row is None or row["status"] != STATUS_RUNNING:
                return False
            if row["progress"] == progress and (row["message"] or "") == message:
                return False
            now = _now_iso()
            self._conn.execute(
                "UPDATE jobs SET progress = ?, message = ?, updated_at = ? WHERE id = ?",
                (progress, message, now, jid),
            )
            self._append_event_locked(
                row["session_id"], jid,
                {
                    "type": "job_progress",
                    "job_id": jid,
                    "job_type": row["type"],
                    "status": STATUS_RUNNING,
                    "progress": progress,
                    "message": message,
                },
                now,
            )
        return True

    def mark_succeeded(self, jid: str, result: Any) -> bool:
        result_json = json.dumps(result, ensure_ascii=False, default=str)
        return self._transition(
            jid,
            STATUS_SUCCEEDED,
            event={
                "type": "job_done", "job_id": jid,
                "status": STATUS_SUCCEEDED, "result": result,
            },
            progress=100,
            result=result_json,
            finished_at=_now_iso(),
        )

    def mark_failed(self, jid: str, error: str) -> bool:
        return self._transition(
            jid,
            STATUS_FAILED,
            event={
                "type": "job_error", "job_id": jid,
                "status": STATUS_FAILED, "error": error,
            },
            error=error,
            finished_at=_now_iso(),
        )

    def mark_canceling(self, jid: str) -> bool:
        return self._transition(jid, STATUS_CANCELING)

    def mark_canceled(self, jid: str) -> bool:
        return self._transition(
            jid,
            STATUS_CANCELED,
            event={
                "type": "job_canceled", "job_id": jid,
                "status": STATUS_CANCELED,
            },
            finished_at=_now_iso(),
        )

    # Compatibility method names while B2-B4 migrate callers.
    mark_done = mark_succeeded
    mark_error = mark_failed

    def _transition(
        self,
        jid: str,
        new_status: str,
        *,
        event: Optional[Mapping[str, Any]] = None,
        **extra: Any,
    ) -> bool:
        with self._transaction():
            row = self._get_row_locked(jid)
            if row is None:
                log.warning("[jobs] transition on missing job %s", jid)
                return False
            current = row["status"]
            if new_status not in _ALLOWED_TRANSITIONS.get(current, set()):
                log.warning("[jobs] reject transition %s: %s -> %s", jid, current, new_status)
                return False
            now = _now_iso()
            sets = ["status = ?", "updated_at = ?"]
            values: List[Any] = [new_status, now]
            for key, value in extra.items():
                sets.append(f"{key} = ?")
                values.append(value)
            values.append(jid)
            self._conn.execute(
                f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", values,
            )
            if event is not None:
                self._append_event_locked(row["session_id"], jid, event, now)
        return True

    # ── Durable events ────────────────────────────────────────────────────

    def append_event(self, jid: str, event: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        """Persist a non-state event, currently used for ``artifact_created``."""
        with self._transaction():
            row = self._get_row_locked(jid)
            if row is None:
                return None
            return self._append_event_locked(row["session_id"], jid, event, _now_iso())

    def _append_event_locked(
        self,
        session_id: str,
        jid: str,
        event: Mapping[str, Any],
        created_at: str,
    ) -> Dict[str, Any]:
        event_type = str(event.get("type") or "").strip()
        if not event_type:
            raise ValueError("job event type cannot be empty")
        self._conn.execute(
            "INSERT INTO job_event_sequences(session_id, last_sequence) VALUES (?, 0) "
            "ON CONFLICT(session_id) DO NOTHING",
            (session_id,),
        )
        self._conn.execute(
            "UPDATE job_event_sequences SET last_sequence = last_sequence + 1 "
            "WHERE session_id = ?",
            (session_id,),
        )
        sequence = self._conn.execute(
            "SELECT last_sequence FROM job_event_sequences WHERE session_id = ?",
            (session_id,),
        ).fetchone()["last_sequence"]
        payload = dict(event)
        payload.pop("sequence", None)
        payload.pop("session_id", None)
        payload.pop("created_at", None)
        self._conn.execute(
            "INSERT INTO job_events "
            "(session_id, job_id, sequence, event_type, payload_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                session_id, jid, sequence, event_type,
                json.dumps(payload, ensure_ascii=False, default=str), created_at,
            ),
        )
        return {
            **payload,
            "type": event_type,
            "job_id": jid,
            "session_id": session_id,
            "sequence": sequence,
            "created_at": created_at,
        }

    def list_events(
        self,
        session_id: str,
        after_sequence: int = 0,
        limit: int = 200,
        job_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        sql = (
            "SELECT * FROM job_events WHERE session_id = ? AND sequence > ?"
        )
        params: List[Any] = [session_id, max(0, int(after_sequence))]
        if job_id:
            sql += " AND job_id = ?"
            params.append(job_id)
        sql += " ORDER BY sequence ASC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_event_row_to_dict(row) for row in rows]

    def last_sequence(self, session_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT last_sequence FROM job_event_sequences WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return int(row["last_sequence"]) if row else 0

    def oldest_sequence(self, session_id: str) -> int:
        """Return the oldest retained sequence, or latest+1 when no events remain."""
        with self._lock:
            row = self._conn.execute(
                "SELECT MIN(sequence) AS sequence FROM job_events WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row and row["sequence"] is not None:
            return int(row["sequence"])
        return self.last_sequence(session_id) + 1

    def cleanup_events(
        self,
        retention_days: int = EVENT_RETENTION_DAYS,
        max_events_per_session: int = MAX_EVENTS_PER_SESSION,
    ) -> int:
        """Bound event storage by age and per-session count without reusing sequences."""
        retention_days = max(1, int(retention_days))
        max_events_per_session = max(100, int(max_events_per_session))
        cutoff = (datetime.now() - timedelta(days=retention_days)).isoformat(timespec="milliseconds")
        with self._transaction():
            before = self._conn.total_changes
            self._conn.execute("DELETE FROM job_events WHERE created_at < ?", (cutoff,))
            sessions = self._conn.execute(
                "SELECT session_id FROM job_events GROUP BY session_id HAVING COUNT(*) > ?",
                (max_events_per_session,),
            ).fetchall()
            for row in sessions:
                self._conn.execute(
                    "DELETE FROM job_events WHERE session_id = ? AND id NOT IN ("
                    "SELECT id FROM job_events WHERE session_id = ? "
                    "ORDER BY sequence DESC LIMIT ?)",
                    (row["session_id"], row["session_id"], max_events_per_session),
                )
            deleted = self._conn.total_changes - before
        if deleted:
            log.info("[jobs] event cleanup removed=%d", deleted)
        return deleted

    # ── Queries ───────────────────────────────────────────────────────────

    def _get_row_locked(self, jid: str) -> Optional[sqlite3.Row]:
        return self._conn.execute("SELECT * FROM jobs WHERE id = ?", (jid,)).fetchone()

    def get(self, jid: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._get_row_locked(jid)
        return _row_to_dict(row) if row else None

    def get_for_session(self, session_id: str, jid: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE session_id = ? AND id = ?",
                (session_id, jid),
            ).fetchone()
        return _row_to_dict(row) if row else None

    def list_by_session(
        self, session_id: str, limit: int = 50, top_level_only: bool = False,
    ) -> List[Dict[str, Any]]:
        parent_filter = "AND COALESCE(parent_id, '') = '' " if top_level_only else ""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE session_id = ? "
                + parent_filter +
                "ORDER BY created_at DESC LIMIT ?",
                (session_id, max(1, min(int(limit), 500))),
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def list_children(self, session_id: str, parent_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE session_id = ? AND parent_id = ? "
                "ORDER BY created_at ASC",
                (session_id, parent_id),
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def list_detail_events(
        self, session_id: str, job_ids: List[str],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Return retained conversation step events grouped by parent job."""
        clean_ids = [str(value) for value in job_ids if value]
        if not clean_ids:
            return {}
        placeholders = ",".join("?" for _ in clean_ids)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM job_events WHERE session_id = ? "
                f"AND job_id IN ({placeholders}) "
                "AND event_type IN ('conversation_step_started', 'conversation_step_finished') "
                "ORDER BY sequence ASC",
                (session_id, *clean_ids),
            ).fetchall()
        return_value: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            return_value.setdefault(row["job_id"], []).append(_event_row_to_dict(row))
        return return_value

    def list_active(
        self, session_id: str, top_level_only: bool = False,
    ) -> List[Dict[str, Any]]:
        placeholders = ",".join("?" for _ in _TERMINAL)
        parent_filter = "AND COALESCE(parent_id, '') = '' " if top_level_only else ""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM jobs WHERE session_id = ? "
                f"AND status NOT IN ({placeholders}) "
                + parent_filter + "ORDER BY created_at ASC",
                (session_id, *_TERMINAL),
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def list_artifacts(
        self, session_id: str, job_ids: List[str],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Return retained artifact events grouped by job for history hydration."""
        clean_ids = [str(value) for value in job_ids if value]
        if not clean_ids:
            return {}
        placeholders = ",".join("?" for _ in clean_ids)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT job_id, payload_json FROM job_events WHERE session_id = ? "
                f"AND event_type = 'artifact_created' AND job_id IN ({placeholders}) "
                "ORDER BY sequence ASC",
                (session_id, *clean_ids),
            ).fetchall()
        result: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            payload = json.loads(row["payload_json"])
            artifact = payload.get("artifact")
            if isinstance(artifact, dict):
                result.setdefault(row["job_id"], []).append(artifact)
        return result

    def purge_terminal_ids(self, session_id: str, job_ids: List[str]) -> int:
        """Delete selected terminal jobs and events without touching other history."""
        selected = list(dict.fromkeys(str(job_id) for job_id in job_ids if job_id))
        if not selected:
            return 0
        with self._transaction():
            pending = list(selected)
            all_ids = set(selected)
            while pending:
                placeholders = ",".join("?" for _ in pending)
                children = self._conn.execute(
                    f"SELECT id FROM jobs WHERE session_id = ? "
                    f"AND parent_id IN ({placeholders})",
                    (session_id, *pending),
                ).fetchall()
                pending = [
                    str(row["id"]) for row in children
                    if str(row["id"]) not in all_ids
                ]
                all_ids.update(pending)
            placeholders = ",".join("?" for _ in all_ids)
            rows = self._conn.execute(
                f"SELECT id, status FROM jobs WHERE session_id = ? "
                f"AND id IN ({placeholders})",
                (session_id, *all_ids),
            ).fetchall()
            active = [
                str(row["id"]) for row in rows
                if str(row["status"]) not in _TERMINAL
            ]
            if active:
                raise RuntimeError("jobs are still active: " + ", ".join(active))
            existing_ids = [str(row["id"]) for row in rows]
            if not existing_ids:
                return 0
            placeholders = ",".join("?" for _ in existing_ids)
            self._conn.execute(
                f"DELETE FROM job_events WHERE session_id = ? "
                f"AND job_id IN ({placeholders})",
                (session_id, *existing_ids),
            )
            self._conn.execute(
                f"DELETE FROM jobs WHERE session_id = ? AND id IN ({placeholders})",
                (session_id, *existing_ids),
            )
        return len(existing_ids)

    def clear_terminal(self, session_id: str) -> int:
        """Delete completed/failed/canceled jobs and their retained events.

        Active jobs are intentionally preserved. Sequence counters are not
        rewound, so browser replay cursors remain monotonic after cleanup.
        """
        placeholders = ",".join("?" for _ in _TERMINAL)
        with self._transaction():
            rows = self._conn.execute(
                f"SELECT id FROM jobs WHERE session_id = ? "
                f"AND status IN ({placeholders})",
                (session_id, *_TERMINAL),
            ).fetchall()
            job_ids = [row["id"] for row in rows]
            if not job_ids:
                return 0
            id_placeholders = ",".join("?" for _ in job_ids)
            self._conn.execute(
                f"DELETE FROM job_events WHERE session_id = ? "
                f"AND job_id IN ({id_placeholders})",
                (session_id, *job_ids),
            )
            self._conn.execute(
                f"DELETE FROM jobs WHERE session_id = ? "
                f"AND id IN ({id_placeholders})",
                (session_id, *job_ids),
            )
        return len(job_ids)

    # ── Transaction/lifecycle ─────────────────────────────────────────────

    class _Transaction:
        def __init__(self, store: "JobsStore"):
            self.store = store

        def __enter__(self):
            self.store._lock.acquire()
            self.store._conn.execute("BEGIN IMMEDIATE")
            return self

        def __exit__(self, exc_type, exc, tb):
            try:
                if exc_type is None:
                    self.store._conn.commit()
                else:
                    self.store._conn.rollback()
            finally:
                self.store._lock.release()
            return False

    def _transaction(self) -> "JobsStore._Transaction":
        return self._Transaction(self)

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    @property
    def path(self) -> Path:
        return self._path
