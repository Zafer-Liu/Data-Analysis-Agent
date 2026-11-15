"""Durable scoped plans, approvals and remote execution checkpoints."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from infrastructure.paths import content_data_path


class RemoteExecutionError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def canonical_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


class RemoteExecutionStore:
    """SQLite is the authority; a dropped SSH acknowledgement is never a new run."""

    def __init__(self, root=None):
        self.root = Path(root) if root is not None else content_data_path("outputs", "remote_training")
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.root / "executions.sqlite3"), timeout=30,
                                   check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS records (
                kind TEXT NOT NULL, id TEXT NOT NULL, owner_id TEXT NOT NULL,
                session_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
                body TEXT NOT NULL, PRIMARY KEY(kind,id)
            );
            CREATE INDEX IF NOT EXISTS records_scope ON records(kind,owner_id,session_id,workspace_id);
            CREATE TABLE IF NOT EXISTS submissions (
                owner_id TEXT NOT NULL, session_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL, plan_hash TEXT NOT NULL, run_id TEXT NOT NULL,
                PRIMARY KEY(owner_id,session_id,workspace_id,idempotency_key)
            );
            CREATE TABLE IF NOT EXISTS run_claims (
                run_id TEXT PRIMARY KEY, token TEXT NOT NULL, expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS job_event_outbox (
                run_id TEXT NOT NULL, native_id TEXT NOT NULL, native_seq INTEGER NOT NULL,
                body TEXT NOT NULL, PRIMARY KEY(run_id,native_id,native_seq)
            );
        """)
        self._db.commit()

    @contextmanager
    def transaction(self):
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self._db.commit()
            except BaseException:
                self._db.rollback()
                raise

    @staticmethod
    def scope(owner_id, session_id, workspace_id=""):
        if any(not isinstance(value, str) or len(value) > 512 or any(ord(c) < 32 for c in value)
               for value in (owner_id, session_id, workspace_id)) or not owner_id or not session_id:
            raise RemoteExecutionError("invalid_scope", "Bounded owner_id/session_id/workspace_id strings are required")
        return owner_id, session_id, workspace_id

    def _write(self, kind, value):
        self._db.execute("INSERT INTO records VALUES (?,?,?,?,?,?) "
                         "ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body",
                         (kind, value["id"], value["owner_id"], value["session_id"],
                          value["workspace_id"], json.dumps(value, ensure_ascii=False, allow_nan=False)))

    def put(self, kind, value):
        with self.transaction():
            self._write(kind, value)
        return value

    def get(self, kind, identifier, *, owner_id, session_id, workspace_id=""):
        scope = self.scope(owner_id, session_id, workspace_id)
        with self._lock:
            row = self._db.execute("SELECT body FROM records WHERE kind=? AND id=? AND "
                                   "owner_id=? AND session_id=? AND workspace_id=?",
                                   (kind, identifier, *scope)).fetchone()
        if row is None:
            raise RemoteExecutionError("not_found", "Scoped remote training record not found")
        return json.loads(row["body"])

    def update(self, kind, identifier, *, scope, **changes):
        with self.transaction():
            value = self.get(kind, identifier, **scope)
            value.update(changes)
            value["updated_at"] = time.time()
            self._write(kind, value)
        return value

    def list(self, kind, *, owner_id, session_id, workspace_id=""):
        scope = self.scope(owner_id, session_id, workspace_id)
        with self._lock:
            rows = self._db.execute("SELECT body FROM records WHERE kind=? AND owner_id=? "
                                    "AND session_id=? AND workspace_id=?", (kind, *scope)).fetchall()
        return [json.loads(row["body"]) for row in rows]

    def create_submission(self, plan, grant, idempotency_key):
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200:
            raise RemoteExecutionError("invalid_idempotency_key", "A bounded idempotency_key is required")
        scope = self.scope(plan["owner_id"], plan["session_id"], plan["workspace_id"])
        with self.transaction():
            row = self._db.execute("SELECT run_id,plan_hash FROM submissions WHERE owner_id=? "
                                   "AND session_id=? AND workspace_id=? AND idempotency_key=?",
                                   (*scope, idempotency_key)).fetchone()
            if row:
                if row["plan_hash"] != plan["plan_hash"]:
                    raise RemoteExecutionError("idempotency_conflict", "Key already binds a different plan")
                return self.get("run", row["run_id"], owner_id=scope[0], session_id=scope[1], workspace_id=scope[2]), False
            stored_grant = self.get("grant", grant["id"], owner_id=scope[0], session_id=scope[1], workspace_id=scope[2])
            if stored_grant.get("run_id"):
                raise RemoteExecutionError("grant_consumed", "Approval has already been consumed")
            if stored_grant["expires_at"] <= time.time():
                raise RemoteExecutionError("grant_expired", "Approval expired")
            identifier = uuid.uuid4().hex
            value = dict(id=identifier, run_id=identifier, owner_id=scope[0], session_id=scope[1],
                         workspace_id=scope[2], plan_id=plan["id"], plan_hash=plan["plan_hash"],
                         grant_id=grant["id"], grant_hash=grant["grant_hash"],
                         idempotency_key=idempotency_key,
                         remote_idempotency_key=canonical_hash([*scope, idempotency_key]),
                         connection_id=plan["connection_id"], task_id="", runner_instance_id="",
                         status="preparing", phase="reserve_pending", cancel_intent=False,
                         native_seq=0, transfers={}, exit_acknowledged=False, leases_released=False,
                         job_id="", created_at=time.time(), deadline_at=grant["deadline_at"])
            self._write("run", value)
            stored_grant["run_id"] = identifier
            self._write("grant", stored_grant)
            self._db.execute("INSERT INTO submissions VALUES (?,?,?,?,?,?)",
                             (*scope, idempotency_key, plan["plan_hash"], identifier))
        return value, True

    def submission(self, key, plan, scope):
        with self._lock:
            row = self._db.execute("SELECT run_id,plan_hash FROM submissions WHERE owner_id=? AND session_id=? AND workspace_id=? AND idempotency_key=?",
                                   (*self.scope(**scope), key)).fetchone()
            if row is None:
                return None
            if row["plan_hash"] != plan["plan_hash"]:
                raise RemoteExecutionError("idempotency_conflict", "Key already binds another training plan")
            return self.get("run", row["run_id"], **scope)

    def request_cancel(self, run_id, scope):
        """Serialize accepted Stop intent with the immutable publication point."""
        with self.transaction():
            run = self.get("run", run_id, **scope)
            if run["status"] not in {"succeeded", "failed", "canceled"}:
                run.update(cancel_intent=True, status="canceling", phase="cancel_pending", updated_at=time.time())
                self._write("run", run)
        return run

    def checkpoint_events(self, run_id, scope, native_id, events, *, native_seq, **changes):
        """Keep native replay bytes durable before advancing the remote cursor."""
        with self.transaction():
            run = self.get("run", run_id, **scope)
            if run.get("job_id"):
                for event in events:
                    self._db.execute("INSERT OR IGNORE INTO job_event_outbox VALUES (?,?,?,?)",
                                     (run_id, native_id, event["seq"], json.dumps(event, ensure_ascii=False, allow_nan=False)))
            run.update(changes, native_seq=max(run["native_seq"], native_seq), updated_at=time.time())
            self._write("run", run)
        return run

    def pending_job_events(self, run_id, scope):
        with self._lock:
            self.get("run", run_id, **scope)
            rows = self._db.execute("SELECT native_id,native_seq,body FROM job_event_outbox WHERE run_id=? ORDER BY native_seq",
                                    (run_id,)).fetchall()
        return [(row["native_id"], row["native_seq"], json.loads(row["body"])) for row in rows]

    def acknowledge_job_event(self, run_id, scope, native_id, native_seq):
        with self.transaction():
            self.get("run", run_id, **scope)
            self._db.execute("DELETE FROM job_event_outbox WHERE run_id=? AND native_id=? AND native_seq=?",
                             (run_id, native_id, native_seq))

    def acknowledge_job_delivery(self, run_id, scope, job_id):
        with self.transaction():
            run = self.get("run", run_id, **scope)
            if (run.get("job_id") != job_id or run["status"] not in {"succeeded", "failed", "canceled"}
                    or not run["exit_acknowledged"]):
                return False
            # Cleared terminal history is also acknowledged, without recreating
            # the Job or keeping an undeliverable outbox forever.
            self._db.execute("DELETE FROM job_event_outbox WHERE run_id=?", (run_id,))
            run["job_delivery_acknowledged"] = True
            self._write("run", run)
        return True

    def publish_delivery(self, run_id, scope, result, manifest, path, *, register=None):
        from remote_runner.safe_io import atomic_json
        with self.transaction():
            run = self.get("run", run_id, **scope)
            if run["status"] in {"succeeded", "failed", "canceled"}:
                return run
            if run["cancel_intent"]:
                run.update(status="canceled", phase="delivery_canceled", leases_released=True)
            else:
                atomic_json(path, manifest)
                if register:
                    result["lifecycle_registered"] = bool(register())
                run.update(status="succeeded", phase="complete", result=result, artifacts=manifest,
                           leases_released=True, error=None)
            self._write("run", run)
        return run

    def checkpoint(self, run_id, value, scope):
        with self.transaction():
            run = self.get("run", run_id, **scope)
            key = value["direction"] + ":" + value["remote_path"]
            run["transfers"][key] = value
            run["transfer_progress"] = {key: value[key] for key in ("direction", "offset", "size_bytes") if key in value}
            self._write("run", run)

    def renew_claim(self, run_id, token, *, seconds=120):
        with self.transaction():
            result = self._db.execute("UPDATE run_claims SET expires_at=? WHERE run_id=? AND token=? AND expires_at>?",
                                      (time.time() + seconds, run_id, token, time.time()))
            return result.rowcount == 1

    def claim(self, run_id, *, seconds=120):
        token = uuid.uuid4().hex
        with self.transaction():
            now = time.time()
            row = self._db.execute("SELECT expires_at FROM run_claims WHERE run_id=?", (run_id,)).fetchone()
            if row and row["expires_at"] > now:
                return None
            self._db.execute("INSERT OR REPLACE INTO run_claims VALUES (?,?,?)", (run_id, token, now + seconds))
        return token

    def release_claim(self, run_id, token):
        with self.transaction():
            self._db.execute("DELETE FROM run_claims WHERE run_id=? AND token=?", (run_id, token))

    def pending_scopes(self, *, owner_id="local-default"):
        """Trusted startup discovery; never expose this across an HTTP boundary."""
        result = set()
        with self._lock:
            rows = self._db.execute("SELECT owner_id,session_id,workspace_id,body FROM records WHERE kind='run' AND owner_id=?", (owner_id,)).fetchall()
        for owner, session, workspace, body in rows:
            run = json.loads(body)
            if (run.get("status") not in {"succeeded", "failed", "canceled"}
                    or (run.get("job_id") and not run.get("job_delivery_acknowledged"))):
                result.add((owner, session, workspace))
        return [{"owner_id": owner, "session_id": session, "workspace_id": workspace}
                for owner, session, workspace in sorted(result)]

    def close(self):
        with self._lock:
            self._db.close()
