"""Durable, scope-bound reservations and bounded replayable cluster events."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path

from data.cluster_connections import ClusterError, config_fingerprint, scope_text
from infrastructure.paths import content_data_path

TERMINAL = frozenset({"succeeded", "failed", "cancelled"})
# not_submitted is proven absence of execution, not a server cancel acknowledgement.
REMOTE_TERMINAL = TERMINAL | {"not_submitted"}


def public_query(record):
    result = {k: v for k, v in record.items() if k not in {
        "driver_state", "fingerprint", "config", "fetch_token", "snapshot_claimed", "snapshot_executor",
    }}
    result["status"] = result["state"]
    return result


class ClusterQueryStore:
    def __init__(self, root=None, *, owner_id="local-default", session_id="", workspace_id=""):
        self.scope = (scope_text(owner_id, "owner_id", empty=False),
                      scope_text(session_id, "session_id"), scope_text(workspace_id, "workspace_id"))
        self.root = Path(root) if root is not None else content_data_path("cluster")
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "queries.sqlite3"
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS queries (
                  id TEXT PRIMARY KEY, owner TEXT NOT NULL, session TEXT NOT NULL,
                  workspace TEXT NOT NULL, idem TEXT NOT NULL, fingerprint TEXT NOT NULL,
                  record TEXT NOT NULL, UNIQUE(owner,session,workspace,idem));
                CREATE TABLE IF NOT EXISTS events (
                  query_id TEXT NOT NULL, seq INTEGER NOT NULL, event TEXT NOT NULL,
                  PRIMARY KEY(query_id,seq));
            """)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def reserve(self, sql, config, budget, *, idempotency_key="", connection_id=""):
        if (not isinstance(idempotency_key, str) or len(idempotency_key) > 200
                or any(ord(c) < 32 for c in idempotency_key)):
            raise ClusterError("invalid_idempotency_key", "Invalid idempotency key")
        idem = idempotency_key or uuid.uuid4().hex
        fingerprint = hashlib.sha256(json.dumps({"sql": sql, "config": config, "budget": budget, "connection_id": connection_id}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT record,fingerprint FROM queries WHERE owner=? AND session=? AND workspace=? AND idem=?", (*self.scope, idem)).fetchone()
            if old:
                if old["fingerprint"] != fingerprint:
                    raise ClusterError("idempotency_conflict", "The idempotency key refers to another query, connection or budget")
                return json.loads(old["record"]), False
            now = time.time()
            record = {"query_id": uuid.uuid4().hex, "native_query_id": "", "connection_id": connection_id,
                      "owner_id": self.scope[0], "session_id": self.scope[1], "workspace_id": self.scope[2],
                      "kind": config["kind"], "state": "submitting", "remote_state": "unknown",
                      "complete": False, "fetch_started": False, "result_rows": 0, "result_bytes": 0,
                      "cancel_requested": False, "cancel_confirmed": False, "idempotency_key": idem, "sql": sql,
                      "budget": budget, "config": config, "fingerprint": fingerprint,
                      "driver_state": {}, "created_at": now, "updated_at": now,
                      "statistics": {"server_stats_source": "unavailable", "scan_bytes": None,
                                     "cpu_seconds": None, "queued_seconds": None, "elapsed_seconds": None},
                      "error_code": "", "error": "", "snapshot": None, "snapshot_claimed": False}
            db.execute("INSERT INTO queries VALUES (?,?,?,?,?,?,?)", (record["query_id"], *self.scope, idem, fingerprint, json.dumps(record)))
            self._event(db, record["query_id"], {"state": "submitting", "created_at": now})
            return record, True

    def get(self, query_id):
        with self._db() as db:
            row = db.execute("SELECT record FROM queries WHERE id=? AND owner=? AND session=? AND workspace=?", (query_id, *self.scope)).fetchone()
        if row is None:
            raise ClusterError("cluster_not_found", "Cluster query not found in this scope")
        return json.loads(row["record"])

    def list(self, *, limit=100, idempotency_key=""):
        limit = self._limit(limit)
        with self._db() as db:
            rows = db.execute("SELECT record FROM queries WHERE owner=? AND session=? AND workspace=? "
                              "AND (?='' OR idem=?) ORDER BY rowid DESC LIMIT ?", (*self.scope, idempotency_key, idempotency_key, limit))
            return [public_query(json.loads(r["record"])) for r in rows]

    def has_active_connection(self, config, connection_id):
        """Stream durable facts when deciding whether a cached transport may close."""
        fingerprint = config_fingerprint(config)
        with self._db() as db:
            for row in db.execute("SELECT record FROM queries WHERE owner=? AND session=? AND workspace=?", self.scope):
                record = json.loads(row["record"])
                if record["connection_id"] != connection_id or config_fingerprint(record["config"]) != fingerprint:
                    continue
                if record.get("snapshot_claimed"):
                    return True
                if record["state"] == "failed" and record.get("submission_phase") == "preflight":
                    continue
                if record["remote_state"] not in REMOTE_TERMINAL:
                    return True
                if record["remote_state"] == "succeeded" and not record["complete"] and record["state"] not in TERMINAL:
                    return True
        return False

    @staticmethod
    def _limit(limit):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ClusterError("invalid_event_cursor", "Limit must be between 1 and 200")
        return limit

    @staticmethod
    def _event(db, query_id, event):
        seq = db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE query_id=?", (query_id,)).fetchone()[0]
        db.execute("INSERT INTO events VALUES (?,?,?)", (query_id, seq, json.dumps({"seq": seq, **event})))
        db.execute("DELETE FROM events WHERE query_id=? AND seq<=?", (query_id, seq - 1000))

    def update(self, query_id, **changes):
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT record FROM queries WHERE id=? AND owner=? AND session=? AND workspace=?", (query_id, *self.scope)).fetchone()
            if row is None:
                raise ClusterError("cluster_not_found", "Cluster query not found in this scope")
            record = json.loads(row["record"])
            event_fields = ("state", "remote_state", "error_code", "native_query_id", "complete", "cancel_requested", "cancel_confirmed")
            previous = tuple(record.get(k) for k in event_fields)
            if changes.pop("claim_submit", False):
                if record.get("cancel_requested"):
                    changes.update(state="cancelled", remote_state="not_submitted", submission_phase="not_submitted",
                                   error_code="query_cancelled", error="Query was cancelled before transport submission")
                else:
                    changes["submission_phase"] = "sending"
            if changes.pop("claim_fetch", False):
                if record["fetch_started"]:
                    raise ClusterError("result_already_consumed", "The result stream cannot be replayed or resubmitted")
                if record.get("cancel_requested"):
                    raise ClusterError("query_cancelled", "Result consumption was cancelled")
                changes["fetch_started"] = True
            if changes.pop("claim_snapshot", False):
                from infrastructure.resource_budget import process_alive, process_identity
                if record.get("snapshot_claimed") and (not record.get("snapshot_executor") or process_alive(record["snapshot_executor"])):
                    raise ClusterError("resource_busy", "A snapshot is already being materialized")
                changes["snapshot_claimed"] = True
                changes["snapshot_executor"] = process_identity()
            if record.get("cancel_requested") and changes.get("complete") is True and not changes.get("snapshot"):
                raise ClusterError("query_cancelled", "Result consumption was cancelled before completion")
            if record["remote_state"] in REMOTE_TERMINAL and changes.get("remote_state", record["remote_state"]) != record["remote_state"]:
                raise ClusterError("cluster_protocol_error", "A confirmed server terminal state cannot change")
            if record["state"] == "cancel_pending" and changes.get("state") in {"running", "fetching"}:
                changes.pop("state")
            record.update(changes)
            record["updated_at"] = time.time()
            db.execute("UPDATE queries SET record=? WHERE id=?", (json.dumps(record), query_id))
            if previous != tuple(record.get(k) for k in event_fields):
                self._event(db, query_id, {k: record.get(k) for k in event_fields} | {"created_at": record["updated_at"]})
            return record

    def events(self, query_id, *, after_seq=0, limit=100):
        self.get(query_id)
        if isinstance(after_seq, bool) or not isinstance(after_seq, int) or after_seq < 0:
            raise ClusterError("invalid_event_cursor", "Invalid event cursor")
        limit = self._limit(limit)
        with self._db() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT event FROM events WHERE query_id=? AND seq>? ORDER BY seq LIMIT ?", (query_id, after_seq, limit))]

    def event_window(self, query_id):
        self.get(query_id)
        with self._db() as db:
            row = db.execute("SELECT MIN(seq),MAX(seq) FROM events WHERE query_id=?", (query_id,)).fetchone()
            return {"first_seq": row[0] or 0, "last_seq": row[1] or 0}
