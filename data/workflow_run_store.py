"""Transactional Workflow Run and NodeRun persistence for WF2."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Mapping

from agent.workflows.models import (
    NODE_RUN_TERMINAL_STATUSES,
    RUN_TERMINAL_STATUSES,
    NodeRunStatus,
    RunStatus,
    can_transition_node_run,
    can_transition_run,
)

from data.workflow_sqlite import configure_workflow_connection, workflow_db_init_lock


_RUN_SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_runs (
    id TEXT PRIMARY KEY,
    workflow_version_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    status TEXT NOT NULL,
    input_json TEXT NOT NULL DEFAULT '{}',
    started_by TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finished_at TEXT,
    cancel_requested_at TEXT,
    failure_code TEXT NOT NULL DEFAULT '',
    failure_message TEXT NOT NULL DEFAULT '',
    input_manifest_id TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_workspace
    ON workflow_runs(workspace_id, started_at);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_session
    ON workflow_runs(session_id, started_at);

CREATE TABLE IF NOT EXISTS workflow_node_runs (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    iteration INTEGER NOT NULL DEFAULT 1,
    attempt INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL,
    agent_profile_id TEXT NOT NULL,
    job_id TEXT NOT NULL DEFAULT '',
    input_json TEXT NOT NULL DEFAULT '{}',
    output_json TEXT,
    input_manifest_id TEXT NOT NULL DEFAULT '',
    output_manifest_id TEXT NOT NULL DEFAULT '',
    operation_key TEXT UNIQUE,
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    model_name TEXT NOT NULL DEFAULT "",
    provider_name TEXT NOT NULL DEFAULT "",
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cached_input_tokens INTEGER NOT NULL DEFAULT 0,
    tool_calls INTEGER NOT NULL DEFAULT 0,
    UNIQUE(run_id, node_id, iteration, attempt),
    FOREIGN KEY(run_id) REFERENCES workflow_runs(id)
);
CREATE INDEX IF NOT EXISTS idx_workflow_node_runs_run
    ON workflow_node_runs(run_id, created_at);
CREATE INDEX IF NOT EXISTS idx_workflow_node_runs_job
    ON workflow_node_runs(job_id);

CREATE TABLE IF NOT EXISTS workflow_artifact_manifests (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    node_run_id TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    items_json TEXT NOT NULL DEFAULT '[]',
    summary TEXT NOT NULL DEFAULT '',
    supersedes_manifest_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(run_id, node_run_id, kind, supersedes_manifest_id),
    FOREIGN KEY(run_id) REFERENCES workflow_runs(id)
);
CREATE INDEX IF NOT EXISTS idx_workflow_artifact_manifests_run
    ON workflow_artifact_manifests(run_id, created_at);

CREATE TABLE IF NOT EXISTS workflow_artifact_blobs (
    artifact_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    content_json TEXT NOT NULL,
    size INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_workflow_artifact_blobs_workspace
    ON workflow_artifact_blobs(workspace_id, created_at);

CREATE TABLE IF NOT EXISTS workflow_artifact_consumptions (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    consumer_node_run_id TEXT NOT NULL,
    producer_node_run_id TEXT NOT NULL DEFAULT '',
    manifest_id TEXT NOT NULL DEFAULT '',
    artifact_id TEXT NOT NULL,
    purpose TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(consumer_node_run_id, producer_node_run_id, artifact_id, purpose),
    FOREIGN KEY(run_id) REFERENCES workflow_runs(id)
);
CREATE INDEX IF NOT EXISTS idx_workflow_artifact_consumptions_run
    ON workflow_artifact_consumptions(run_id, consumer_node_run_id);

CREATE TABLE IF NOT EXISTS workflow_approvals (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    node_run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    status TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    artifact_manifest_id TEXT NOT NULL DEFAULT '',
    revised_artifact_manifest_id TEXT NOT NULL DEFAULT '',
    requested_at TEXT NOT NULL,
    decided_at TEXT,
    decision TEXT NOT NULL DEFAULT '',
    decided_by TEXT NOT NULL DEFAULT '',
    comment TEXT NOT NULL DEFAULT '',
    comments_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(node_run_id, reason),
    FOREIGN KEY(run_id) REFERENCES workflow_runs(id)
);
CREATE INDEX IF NOT EXISTS idx_workflow_approvals_run
    ON workflow_approvals(run_id, status, requested_at);

CREATE TABLE IF NOT EXISTS workflow_run_templates (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL UNIQUE,
    workflow_version_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    source_manifest_id TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES workflow_runs(id)
);
CREATE INDEX IF NOT EXISTS idx_workflow_run_templates_workspace
    ON workflow_run_templates(workspace_id, created_at);

CREATE TABLE IF NOT EXISTS workflow_knowledge_candidates (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    workflow_version_id TEXT NOT NULL,
    source_manifest_id TEXT NOT NULL DEFAULT '',
    candidate_type TEXT NOT NULL,
    title TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    decision_comment TEXT NOT NULL DEFAULT '',
    decided_by TEXT NOT NULL DEFAULT '',
    decided_at TEXT,
    published_ref_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(run_id, candidate_type, title),
    FOREIGN KEY(run_id) REFERENCES workflow_runs(id)
);
CREATE INDEX IF NOT EXISTS idx_workflow_knowledge_candidates_workspace
    ON workflow_knowledge_candidates(workspace_id, status, created_at);
CREATE TABLE IF NOT EXISTS workflow_event_sequences (
    run_id TEXT PRIMARY KEY,
    last_sequence INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS workflow_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, sequence)
);
CREATE INDEX IF NOT EXISTS idx_workflow_events_run
    ON workflow_events(run_id, sequence);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load(value: str | None, fallback: Any) -> Any:
    if value is None:
        return fallback
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return fallback


def _stable_payload(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _bounded_json_value(value: Any, *, limit: int = 20) -> Any:
    if isinstance(value, list):
        return value[:limit]
    if isinstance(value, tuple):
        return list(value[:limit])
    return value


def _source_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    metadata: dict[str, Any] = {}
    data_snapshot_id = value.get("data_snapshot_id") or value.get("source_snapshot_id")
    if data_snapshot_id:
        metadata["data_snapshot_id"] = str(data_snapshot_id)
    sql = value.get("sql") or value.get("query_sql")
    if sql:
        sql_text = str(sql)
        metadata["sql_hash"] = hashlib.sha256(sql_text.encode("utf-8")).hexdigest()
        metadata["sql"] = sql_text[:20_000]
    source_tool = value.get("source_tool") or value.get("tool")
    if source_tool:
        metadata["source_tool"] = str(source_tool)
    source_job_id = value.get("source_job_id") or value.get("job_id")
    if source_job_id:
        metadata["source_job_id"] = str(source_job_id)
    sources = value.get("sources")
    if isinstance(sources, list) and sources:
        metadata["sources"] = _bounded_json_value(sources)
    artifacts = value.get("artifacts")
    if isinstance(artifacts, list) and artifacts:
        metadata["tool_artifacts"] = _bounded_json_value(artifacts)
    artifact_id = value.get("artifact_id")
    if artifact_id:
        metadata["source_artifact_id"] = str(artifact_id)
    artifact_uri = value.get("uri") or value.get("artifact_uri")
    if artifact_uri:
        metadata["source_artifact_uri"] = str(artifact_uri)
    return metadata


def _artifact_item(
    *,
    run_id: str,
    node_run_id: str,
    logical_name: str,
    value: Any,
    source_job_id: str = "",
    source_tool: str = "workflow",
    inline_limit: int = 1000,
    artifact_type: str = "workflow_material",
    schema_version: str = "v1",
) -> dict[str, Any]:
    text = _stable_payload(value)
    encoded = text.encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    source_metadata = _source_metadata(value)
    evidence = []
    quality: dict[str, Any] = {"status": "not_validated", "checks": []}
    if isinstance(value, Mapping):
        raw_evidence = value.get("evidence") or value.get("sources") or []
        if isinstance(raw_evidence, (list, tuple)):
            evidence.extend(str(item) for item in raw_evidence if str(item).strip())
        raw_quality = value.get("quality")
        if isinstance(raw_quality, Mapping):
            quality = dict(raw_quality)
    for key in ("source_artifact_id", "source_artifact_uri", "data_snapshot_id", "sql_hash"):
        if source_metadata.get(key):
            evidence.append(f"{key}:{source_metadata[key]}")
    artifact_id = f"wf_{digest[:16]}_{uuid.uuid4().hex[:8]}"
    item: dict[str, Any] = {
        "artifact_id": artifact_id,
        "logical_name": str(logical_name),
        "type": artifact_type,
        "schema_version": schema_version,
        "name": str(logical_name),
        "uri": f"artifact://workflow/{run_id}/{node_run_id or 'input'}/{artifact_id}",
        "sha256": digest,
        "media_type": "application/json",
        "size": len(encoded),
        "source_job_id": str(source_metadata.pop("source_job_id", source_job_id or "")),
        "source_tool": str(source_metadata.pop("source_tool", source_tool or "workflow")),
        "created_at": _now(),
        "evidence": list(dict.fromkeys(evidence))[:30],
        "quality": quality,
    }
    item.update(source_metadata)
    if len(text) <= inline_limit:
        item["data"] = value
    else:
        item["data_preview"] = text[:inline_limit]
        # Keep the complete immutable value in the companion blob table.  The
        # manifest remains compact for Run Detail, while downstream nodes can
        # consume the original Artifact rather than a lossy preview.
        item["content_available"] = True
        item["_artifact_blob_value"] = value
    return item


class WorkflowRunStoreError(RuntimeError):
    pass


class WorkflowRunStore:
    """Run state fixed to one Workspace database and identity."""

    def __init__(self, db_path: Path, workspace_id: str):
        self.path = Path(db_path)
        self.workspace_id = str(workspace_id or "")
        if not self.workspace_id:
            raise ValueError("workspace_id is required")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.path), check_same_thread=False, timeout=30.0,
        )
        self._conn.row_factory = sqlite3.Row
        with workflow_db_init_lock(self.path), self._lock:
            configure_workflow_connection(self._conn, self.path)
            self._conn.executescript(_RUN_SCHEMA)
            self._migrate_schema_locked()
            self._conn.commit()

    def _migrate_schema_locked(self) -> None:
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(workflow_runs)").fetchall()
        }
        if "input_manifest_id" not in columns:
            self._conn.execute(
                "ALTER TABLE workflow_runs ADD COLUMN "
                "input_manifest_id TEXT NOT NULL DEFAULT ''"
            )
        node_columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(workflow_node_runs)").fetchall()
        }
        if "input_manifest_id" not in node_columns:
            self._conn.execute(
                "ALTER TABLE workflow_node_runs ADD COLUMN "
                "input_manifest_id TEXT NOT NULL DEFAULT ''"
            )
        if "output_manifest_id" not in node_columns:
            self._conn.execute(
                "ALTER TABLE workflow_node_runs ADD COLUMN "
                "output_manifest_id TEXT NOT NULL DEFAULT ''"
            )
        for name, ddl in {
            "model_name": "TEXT NOT NULL DEFAULT ''",
            "provider_name": "TEXT NOT NULL DEFAULT ''",
            "input_tokens": "INTEGER NOT NULL DEFAULT 0",
            "output_tokens": "INTEGER NOT NULL DEFAULT 0",
            "cached_input_tokens": "INTEGER NOT NULL DEFAULT 0",
            "tool_calls": "INTEGER NOT NULL DEFAULT 0",
        }.items():
            if name not in node_columns:
                self._conn.execute(
                    f"ALTER TABLE workflow_node_runs ADD COLUMN {name} {ddl}"
                )
        approval_columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(workflow_approvals)").fetchall()
        }
        if "revised_artifact_manifest_id" not in approval_columns:
            self._conn.execute(
                "ALTER TABLE workflow_approvals ADD COLUMN "
                "revised_artifact_manifest_id TEXT NOT NULL DEFAULT ''"
            )
        if "comments_json" not in approval_columns:
            self._conn.execute(
                "ALTER TABLE workflow_approvals ADD COLUMN "
                "comments_json TEXT NOT NULL DEFAULT '{}'"
            )

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield
            except Exception:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()

    @staticmethod
    def _run(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["input"] = _load(item.pop("input_json"), {})
        return item

    @staticmethod
    def _node(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["input"] = _load(item.pop("input_json"), {})
        item["output"] = _load(item.pop("output_json"), None)
        return item

    @staticmethod
    def _manifest(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["items"] = _load(item.pop("items_json"), [])
        return item

    @staticmethod
    def _approval(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["comments"] = _load(item.pop("comments_json", "{}"), {})
        return item

    @staticmethod
    def _knowledge_candidate(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["payload"] = _load(item.pop("payload_json"), {})
        item["published_ref"] = _load(item.pop("published_ref_json"), {})
        return item

    def _create_manifest_locked(
        self,
        *,
        run_id: str,
        node_run_id: str = "",
        kind: str,
        items: list[dict[str, Any]],
        summary: str = "",
        supersedes_manifest_id: str = "",
    ) -> str:
        manifest_id = _id("mft")
        stored_items: list[dict[str, Any]] = []
        for raw_item in items:
            item = dict(raw_item)
            if "_artifact_blob_value" in item:
                value = item.pop("_artifact_blob_value")
                self._conn.execute(
                    "INSERT INTO workflow_artifact_blobs "
                    "(artifact_id, workspace_id, content_json, size, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        str(item["artifact_id"]),
                        self.workspace_id,
                        _dump(value),
                        int(item.get("size") or 0),
                        _now(),
                    ),
                )
            stored_items.append(item)
        self._conn.execute(
            "INSERT INTO workflow_artifact_manifests "
            "(id, workspace_id, run_id, node_run_id, kind, items_json, summary, "
            "supersedes_manifest_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                manifest_id,
                self.workspace_id,
                run_id,
                node_run_id,
                kind,
                _dump(stored_items),
                str(summary or "")[:1000],
                supersedes_manifest_id,
                _now(),
            ),
        )
        self._event_locked(
            run_id,
            "workflow_artifact_manifest_created",
            {
                "run_id": run_id,
                "manifest_id": manifest_id,
                "node_run_id": node_run_id,
                "kind": kind,
                "artifact_count": len(stored_items),
            },
        )
        return manifest_id

    def get_artifact_content(self, artifact_id: str) -> Any | None:
        """Resolve a complete Artifact value without exposing it in run lists."""
        artifact_id = str(artifact_id or "").strip()
        if not artifact_id:
            return None
        with self._lock:
            blob = self._conn.execute(
                "SELECT content_json FROM workflow_artifact_blobs "
                "WHERE artifact_id = ? AND workspace_id = ?",
                (artifact_id, self.workspace_id),
            ).fetchone()
            if blob is not None:
                return _load(blob["content_json"], None)
            manifests = self._conn.execute(
                "SELECT items_json FROM workflow_artifact_manifests WHERE workspace_id = ?",
                (self.workspace_id,),
            ).fetchall()
        for row in manifests:
            for item in _load(row["items_json"], []):
                if str(item.get("artifact_id") or "") == artifact_id and "data" in item:
                    return item["data"]
        return None

    def _event_locked(
        self,
        run_id: str,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        row = self._conn.execute(
            "SELECT last_sequence FROM workflow_event_sequences WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        sequence = int(row["last_sequence"]) + 1 if row else 1
        if row:
            self._conn.execute(
                "UPDATE workflow_event_sequences SET last_sequence = ? WHERE run_id = ?",
                (sequence, run_id),
            )
        else:
            self._conn.execute(
                "INSERT INTO workflow_event_sequences(run_id, last_sequence) "
                "VALUES (?, ?)",
                (run_id, sequence),
            )
        self._conn.execute(
            "INSERT INTO workflow_events "
            "(run_id, sequence, event_type, payload_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (run_id, sequence, event_type, _dump(payload), _now()),
        )

    def create_run(
        self,
        *,
        workflow_version_id: str,
        session_id: str,
        graph: Mapping[str, Any],
        inputs: Mapping[str, Any],
        started_by: str = "",
    ) -> dict[str, Any]:
        run_id = _id("run")
        now = _now()
        with self._transaction():
            self._conn.execute(
                "INSERT INTO workflow_runs "
                "(id, workflow_version_id, workspace_id, session_id, status, "
                "input_json, started_by, started_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    workflow_version_id,
                    self.workspace_id,
                    session_id,
                    RunStatus.CREATED.value,
                    _dump(inputs),
                    started_by,
                    now,
                    now,
                ),
            )
            for node in graph.get("nodes", []):
                node_run_id = _id("nr")
                self._conn.execute(
                    "INSERT INTO workflow_node_runs "
                    "(id, run_id, node_id, status, agent_profile_id, input_json, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        node_run_id,
                        run_id,
                        str(node["node_id"]),
                        NodeRunStatus.PENDING.value,
                        str(node.get("agent_profile_id") or ""),
                        _dump({}),
                        now,
                        now,
                    ),
                )
            input_items = [
                _artifact_item(
                    run_id=run_id,
                    node_run_id="",
                    logical_name=str(key),
                    value=value,
                    source_tool="workflow_input",
                )
                for key, value in sorted(inputs.items())
            ]
            input_manifest_id = self._create_manifest_locked(
                run_id=run_id,
                kind="run_input",
                items=input_items,
                summary="Workflow run input manifest",
            )
            self._conn.execute(
                "UPDATE workflow_runs SET input_manifest_id = ? WHERE id = ?",
                (input_manifest_id, run_id),
            )
            self._event_locked(
                run_id,
                "workflow_run_created",
                {"run_id": run_id, "workflow_version_id": workflow_version_id},
            )
        return self.get_run(run_id)

    def seed_checkpoint_nodes(
        self,
        run_id: str,
        *,
        source_run_id: str,
        source_nodes: Mapping[str, Mapping[str, Any]],
    ) -> bool:
        """Reuse successful node outputs when branching from a checkpoint.

        Manifests stay immutable in their source Run; the new Run records an
        explicit audit event for every reused node and consumes those manifests
        normally when dispatching its downstream nodes.
        """
        if not source_nodes:
            return False
        if any(
            str(node.get("status") or "") != NodeRunStatus.SUCCEEDED.value
            for node in source_nodes.values()
        ):
            return False
        now = _now()
        with self._transaction():
            target = self._conn.execute(
                "SELECT id FROM workflow_runs WHERE id = ? AND workspace_id = ?",
                (run_id, self.workspace_id),
            ).fetchone()
            source = self._conn.execute(
                "SELECT id FROM workflow_runs WHERE id = ? AND workspace_id = ?",
                (source_run_id, self.workspace_id),
            ).fetchone()
            if target is None or source is None:
                return False
            for node_id, node in source_nodes.items():
                cursor = self._conn.execute(
                    "UPDATE workflow_node_runs SET status = ?, input_json = ?, output_json = ?, "
                    "input_manifest_id = ?, output_manifest_id = ?, updated_at = ?, "
                    "finished_at = ? WHERE run_id = ? AND node_id = ? AND iteration = 1 AND attempt = 1 "
                    "AND status = ?",
                    (
                        NodeRunStatus.SUCCEEDED.value,
                        _dump(node.get("input") or {}),
                        _dump(node.get("output")) if node.get("output") is not None else None,
                        str(node.get("input_manifest_id") or ""),
                        str(node.get("output_manifest_id") or ""),
                        now,
                        now,
                        run_id,
                        str(node_id),
                        NodeRunStatus.PENDING.value,
                    ),
                )
                if cursor.rowcount != 1:
                    raise WorkflowRunStoreError(
                        f"unable to seed checkpoint node: {node_id}"
                    )
                self._event_locked(
                    run_id,
                    "workflow_checkpoint_node_reused",
                    {
                        "run_id": run_id,
                        "source_run_id": source_run_id,
                        "node_id": str(node_id),
                        "source_node_run_id": str(node.get("id") or ""),
                        "source_manifest_id": str(node.get("output_manifest_id") or ""),
                    },
                )
            self._event_locked(
                run_id,
                "workflow_run_checkpoint_forked",
                {
                    "run_id": run_id,
                    "source_run_id": source_run_id,
                    "reused_node_ids": sorted(str(node_id) for node_id in source_nodes),
                },
            )
        return True

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM workflow_runs WHERE id = ? AND workspace_id = ?",
                (run_id, self.workspace_id),
            ).fetchone()
        return self._run(row) if row else None

    def list_runs(self, session_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
        query = "SELECT * FROM workflow_runs WHERE workspace_id = ?"
        params: list[Any] = [self.workspace_id]
        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)
        query += " ORDER BY started_at DESC LIMIT ?"
        params.append(max(1, min(500, int(limit))))
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [self._run(row) for row in rows]

    def list_node_runs(self, run_id: str) -> list[dict[str, Any]]:
        if self.get_run(run_id) is None:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM workflow_node_runs WHERE run_id = ? "
                "ORDER BY created_at, node_id",
                (run_id,),
            ).fetchall()
        return [self._node(row) for row in rows]

    def get_node_run(self, node_run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT n.* FROM workflow_node_runs n "
                "JOIN workflow_runs r ON r.id = n.run_id "
                "WHERE n.id = ? AND r.workspace_id = ?",
                (node_run_id, self.workspace_id),
            ).fetchone()
        return self._node(row) if row else None

    def create_retry_iteration(
        self,
        node_run_id: str,
        *,
        max_iteration: int | None = None,
        input_overrides: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Create the next human-requested iteration for a terminal node run."""
        new_id = _id("nr")
        now = _now()
        with self._transaction():
            row = self._conn.execute(
                "SELECT n.* FROM workflow_node_runs n "
                "JOIN workflow_runs r ON r.id = n.run_id "
                "WHERE n.id = ? AND r.workspace_id = ?",
                (node_run_id, self.workspace_id),
            ).fetchone()
            if row is None or NodeRunStatus(row["status"]) not in NODE_RUN_TERMINAL_STATUSES:
                return None
            latest = self._conn.execute(
                "SELECT MAX(iteration) AS iteration FROM workflow_node_runs "
                "WHERE run_id = ? AND node_id = ?",
                (row["run_id"], row["node_id"]),
            ).fetchone()
            next_iteration = int(latest["iteration"] or row["iteration"]) + 1
            if max_iteration is not None and next_iteration > int(max_iteration):
                return None
            try:
                self._conn.execute(
                    "INSERT INTO workflow_node_runs "
                    "(id, run_id, node_id, iteration, attempt, status, "
                    "agent_profile_id, input_json, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        new_id,
                        row["run_id"],
                        row["node_id"],
                        next_iteration,
                        1,
                        NodeRunStatus.PENDING.value,
                        row["agent_profile_id"],
                        _dump(dict(input_overrides or {})),
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError:
                return None
            self._event_locked(
                row["run_id"],
                "workflow_node_iteration_created",
                {
                    "run_id": row["run_id"],
                    "node_run_id": new_id,
                    "node_id": row["node_id"],
                    "iteration": next_iteration,
                    "previous_node_run_id": node_run_id,
                },
            )
        return self.get_node_run(new_id)

    def create_retry_attempt(self, node_run_id: str) -> dict[str, Any] | None:
        """Create the next attempt for a terminal node run, idempotently per attempt."""
        new_id = _id("nr")
        now = _now()
        with self._transaction():
            row = self._conn.execute(
                "SELECT n.* FROM workflow_node_runs n "
                "JOIN workflow_runs r ON r.id = n.run_id "
                "WHERE n.id = ? AND r.workspace_id = ?",
                (node_run_id, self.workspace_id),
            ).fetchone()
            if row is None or NodeRunStatus(row["status"]) not in NODE_RUN_TERMINAL_STATUSES:
                return None
            latest = self._conn.execute(
                "SELECT MAX(attempt) AS attempt FROM workflow_node_runs "
                "WHERE run_id = ? AND node_id = ? AND iteration = ?",
                (row["run_id"], row["node_id"], row["iteration"]),
            ).fetchone()
            next_attempt = int(latest["attempt"] or row["attempt"]) + 1
            try:
                self._conn.execute(
                    "INSERT INTO workflow_node_runs "
                    "(id, run_id, node_id, iteration, attempt, status, "
                    "agent_profile_id, input_json, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        new_id,
                        row["run_id"],
                        row["node_id"],
                        row["iteration"],
                        next_attempt,
                        NodeRunStatus.PENDING.value,
                        row["agent_profile_id"],
                        _dump({}),
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError:
                return None
            self._event_locked(
                row["run_id"],
                "workflow_node_retry_created",
                {
                    "run_id": row["run_id"],
                    "node_run_id": new_id,
                    "node_id": row["node_id"],
                    "attempt": next_attempt,
                    "previous_node_run_id": node_run_id,
                },
            )
        return self.get_node_run(new_id)

    def transition_run(
        self,
        run_id: str,
        target: RunStatus,
        *,
        failure_code: str = "",
        failure_message: str = "",
    ) -> bool:
        with self._transaction():
            row = self._conn.execute(
                "SELECT * FROM workflow_runs WHERE id = ? AND workspace_id = ?",
                (run_id, self.workspace_id),
            ).fetchone()
            if row is None:
                return False
            current = RunStatus(row["status"])
            if current is target:
                return False
            if not can_transition_run(current, target):
                raise WorkflowRunStoreError(
                    f"invalid run transition: {current.value} -> {target.value}"
                )
            now = _now()
            finished = now if target in RUN_TERMINAL_STATUSES else None
            cancel_requested = now if target is RunStatus.CANCELING else row[
                "cancel_requested_at"
            ]
            self._conn.execute(
                "UPDATE workflow_runs SET status = ?, updated_at = ?, finished_at = ?, "
                "cancel_requested_at = ?, failure_code = ?, failure_message = ? "
                "WHERE id = ?",
                (
                    target.value,
                    now,
                    finished,
                    cancel_requested,
                    failure_code,
                    failure_message,
                    run_id,
                ),
            )
            self._event_locked(
                run_id,
                "workflow_run_status",
                {"run_id": run_id, "status": target.value},
            )
        return True

    def record_node_usage(
        self,
        node_run_id: str,
        usage: Mapping[str, Any],
    ) -> bool:
        """Persist actual model usage without mixing it into node artifacts."""
        with self._transaction():
            row = self._conn.execute(
                "SELECT n.run_id FROM workflow_node_runs n "
                "JOIN workflow_runs r ON r.id = n.run_id "
                "WHERE n.id = ? AND r.workspace_id = ?",
                (node_run_id, self.workspace_id),
            ).fetchone()
            if row is None:
                return False
            input_tokens = max(0, int(usage.get("input_tokens") or 0))
            output_tokens = max(0, int(usage.get("output_tokens") or 0))
            self._conn.execute(
                "UPDATE workflow_node_runs SET model_name = ?, provider_name = ?, "
                "input_tokens = ?, output_tokens = ?, cached_input_tokens = ?, "
                "tool_calls = ?, updated_at = ? WHERE id = ?",
                (
                    str(usage.get("model") or ""),
                    str(usage.get("provider") or ""),
                    input_tokens,
                    output_tokens,
                    max(0, int(usage.get("cached_input_tokens") or 0)),
                    max(0, int(usage.get("tool_calls") or 0)),
                    _now(),
                    node_run_id,
                ),
            )
            self._event_locked(
                row["run_id"],
                "workflow_node_usage",
                {
                    "run_id": row["run_id"],
                    "node_run_id": node_run_id,
                    "model": str(usage.get("model") or ""),
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                },
            )
        return True

    def record_event(
        self,
        run_id: str,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> bool:
        """Append a durable, workspace-scoped audit event for a Workflow Run."""
        with self._transaction():
            row = self._conn.execute(
                "SELECT id FROM workflow_runs WHERE id = ? AND workspace_id = ?",
                (run_id, self.workspace_id),
            ).fetchone()
            if row is None:
                return False
            self._event_locked(run_id, str(event_type), dict(payload))
        return True
    def transition_node(
        self,
        node_run_id: str,
        target: NodeRunStatus,
        *,
        output: Any = None,
        error: str = "",
        artifact_types: Mapping[str, str] | None = None,
    ) -> bool:
        with self._transaction():
            row = self._conn.execute(
                "SELECT n.* FROM workflow_node_runs n "
                "JOIN workflow_runs r ON r.id = n.run_id "
                "WHERE n.id = ? AND r.workspace_id = ?",
                (node_run_id, self.workspace_id),
            ).fetchone()
            if row is None:
                return False
            current = NodeRunStatus(row["status"])
            if current is target:
                return False
            if not can_transition_node_run(current, target):
                raise WorkflowRunStoreError(
                    f"invalid node transition: {current.value} -> {target.value}"
                )
            now = _now()
            started = now if target is NodeRunStatus.RUNNING else row["started_at"]
            finished = now if target in NODE_RUN_TERMINAL_STATUSES else None
            output_json = _dump(output) if output is not None else row["output_json"]
            output_manifest_id = row["output_manifest_id"]
            if (
                target is NodeRunStatus.OUTPUT_READY
                and output is not None
                and not output_manifest_id
            ):
                values = output if isinstance(output, Mapping) else {"result": output}
                items = [
                    _artifact_item(
                        run_id=row["run_id"],
                        node_run_id=node_run_id,
                        logical_name=str(key),
                        value=value,
                        source_job_id=row["job_id"],
                        source_tool="workflow_node",
                        artifact_type=(artifact_types or {}).get(str(key), "workflow_material"),
                    )
                    for key, value in sorted(values.items())
                ]
                output_manifest_id = self._create_manifest_locked(
                    run_id=row["run_id"],
                    node_run_id=node_run_id,
                    kind="node_output",
                    items=items,
                    summary=f"Workflow node output: {row['node_id']}",
                )
            self._conn.execute(
                "UPDATE workflow_node_runs SET status = ?, output_json = ?, "
                "output_manifest_id = ?, error = ?, updated_at = ?, started_at = ?, "
                "finished_at = ? WHERE id = ?",
                (
                    target.value,
                    output_json,
                    output_manifest_id,
                    error,
                    now,
                    started,
                    finished,
                    node_run_id,
                ),
            )
            self._event_locked(
                row["run_id"],
                "workflow_node_status",
                {
                    "run_id": row["run_id"],
                    "node_run_id": node_run_id,
                    "node_id": row["node_id"],
                    "status": target.value,
                },
            )
        return True

    def create_revised_node_manifest(
        self,
        node_run_id: str,
        *,
        values: Mapping[str, Any],
        summary: str = "",
    ) -> str | None:
        """Save human-revised node outputs and make them the downstream manifest."""
        if not isinstance(values, Mapping) or not values:
            raise WorkflowRunStoreError("revised outputs must be a non-empty object")
        with self._transaction():
            row = self._conn.execute(
                "SELECT n.* FROM workflow_node_runs n "
                "JOIN workflow_runs r ON r.id = n.run_id "
                "WHERE n.id = ? AND r.workspace_id = ?",
                (node_run_id, self.workspace_id),
            ).fetchone()
            if row is None:
                return None
            if not row["output_manifest_id"]:
                raise WorkflowRunStoreError("node has no base output manifest to revise")
            items = [
                _artifact_item(
                    run_id=row["run_id"],
                    node_run_id=node_run_id,
                    logical_name=str(key),
                    value=value,
                    source_job_id=row["job_id"],
                    source_tool="human_revision",
                )
                for key, value in sorted(values.items())
            ]
            manifest_id = self._create_manifest_locked(
                run_id=row["run_id"],
                node_run_id=node_run_id,
                kind="node_output_revision",
                items=items,
                summary=summary or f"Human revision for workflow node: {row['node_id']}",
                supersedes_manifest_id=row["output_manifest_id"],
            )
            self._conn.execute(
                "UPDATE workflow_node_runs SET output_json = ?, output_manifest_id = ?, "
                "updated_at = ? WHERE id = ?",
                (_dump(dict(values)), manifest_id, _now(), node_run_id),
            )
            self._event_locked(
                row["run_id"],
                "workflow_node_output_revised",
                {
                    "run_id": row["run_id"],
                    "node_run_id": node_run_id,
                    "node_id": row["node_id"],
                    "manifest_id": manifest_id,
                    "supersedes_manifest_id": row["output_manifest_id"],
                },
            )
        return manifest_id

    def claim_node(self, node_run_id: str, operation_key: str) -> bool:
        with self._transaction():
            row = self._conn.execute(
                "SELECT n.* FROM workflow_node_runs n "
                "JOIN workflow_runs r ON r.id = n.run_id "
                "WHERE n.id = ? AND r.workspace_id = ?",
                (node_run_id, self.workspace_id),
            ).fetchone()
            if row is None or row["status"] != NodeRunStatus.READY.value:
                return False
            try:
                self._conn.execute(
                    "UPDATE workflow_node_runs SET status = ?, operation_key = ?, "
                    "updated_at = ? WHERE id = ? AND status = ?",
                    (
                        NodeRunStatus.QUEUED.value,
                        operation_key,
                        _now(),
                        node_run_id,
                        NodeRunStatus.READY.value,
                    ),
                )
            except sqlite3.IntegrityError:
                return False
            self._event_locked(
                row["run_id"],
                "workflow_node_claimed",
                {
                    "run_id": row["run_id"],
                    "node_run_id": node_run_id,
                    "operation_key": operation_key,
                },
            )
        return True

    def bind_job(self, node_run_id: str, job_id: str) -> bool:
        with self._transaction():
            cursor = self._conn.execute(
                "UPDATE workflow_node_runs SET job_id = ?, updated_at = ? "
                "WHERE id = ? AND status = ? AND job_id = ''",
                (job_id, _now(), node_run_id, NodeRunStatus.QUEUED.value),
            )
        return cursor.rowcount == 1

    def set_node_input(self, node_run_id: str, value: Mapping[str, Any]) -> None:
        with self._transaction():
            row = self._conn.execute(
                "SELECT n.* FROM workflow_node_runs n "
                "JOIN workflow_runs r ON r.id = n.run_id "
                "WHERE n.id = ? AND r.workspace_id = ?",
                (node_run_id, self.workspace_id),
            ).fetchone()
            if row is None:
                return
            input_manifest_id = row["input_manifest_id"]
            if not input_manifest_id:
                items = [
                    _artifact_item(
                        run_id=row["run_id"],
                        node_run_id=node_run_id,
                        logical_name=str(key),
                        value=value_item,
                        source_tool="workflow_node_input",
                    )
                    for key, value_item in sorted(value.items())
                ]
                input_manifest_id = self._create_manifest_locked(
                    run_id=row["run_id"],
                    node_run_id=node_run_id,
                    kind="node_input",
                    items=items,
                    summary=f"Workflow node input: {row['node_id']}",
                )
            self._conn.execute(
                "UPDATE workflow_node_runs SET input_json = ?, input_manifest_id = ?, "
                "updated_at = ? WHERE id = ?",
                (_dump(value), input_manifest_id, _now(), node_run_id),
            )

    def get_manifest(self, manifest_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM workflow_artifact_manifests "
                "WHERE id = ? AND workspace_id = ?",
                (manifest_id, self.workspace_id),
            ).fetchone()
        return self._manifest(row) if row else None

    def list_manifests(self, run_id: str) -> list[dict[str, Any]]:
        if self.get_run(run_id) is None:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM workflow_artifact_manifests WHERE run_id = ? "
                "ORDER BY created_at, id",
                (run_id,),
            ).fetchall()
        return [self._manifest(row) for row in rows]

    def create_approval(
        self,
        *,
        run_id: str,
        node_run_id: str,
        node_id: str,
        mode: str,
        reason: str,
        artifact_manifest_id: str = "",
    ) -> dict[str, Any] | None:
        approval_id = _id("ap")
        now = _now()
        with self._transaction():
            if self.get_run(run_id) is None:
                return None
            try:
                self._conn.execute(
                    "INSERT INTO workflow_approvals "
                    "(id, workspace_id, run_id, node_run_id, node_id, status, "
                    "mode, reason, artifact_manifest_id, requested_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        approval_id,
                        self.workspace_id,
                        run_id,
                        node_run_id,
                        node_id,
                        "pending",
                        mode,
                        reason,
                        artifact_manifest_id,
                        now,
                    ),
                )
            except sqlite3.IntegrityError:
                row = self._conn.execute(
                    "SELECT * FROM workflow_approvals "
                    "WHERE node_run_id = ? AND reason = ? AND workspace_id = ?",
                    (node_run_id, reason, self.workspace_id),
                ).fetchone()
                return self._approval(row) if row else None
            self._event_locked(
                run_id,
                "workflow_approval_requested",
                {
                    "run_id": run_id,
                    "approval_id": approval_id,
                    "node_run_id": node_run_id,
                    "node_id": node_id,
                    "mode": mode,
                    "reason": reason,
                    "artifact_manifest_id": artifact_manifest_id,
                },
            )
        return self.get_approval(approval_id)

    def get_approval(self, approval_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM workflow_approvals "
                "WHERE id = ? AND workspace_id = ?",
                (approval_id, self.workspace_id),
            ).fetchone()
        return self._approval(row) if row else None

    def list_approvals(
        self,
        run_id: str = "",
        *,
        status: str = "",
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM workflow_approvals WHERE workspace_id = ?"
        params: list[Any] = [self.workspace_id]
        if run_id:
            query += " AND run_id = ?"
            params.append(run_id)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY requested_at DESC, id"
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [self._approval(row) for row in rows]

    def decide_approval(
        self,
        approval_id: str,
        *,
        decision: str,
        decided_by: str = "",
        comment: str = "",
        comments: Mapping[str, Any] | None = None,
        revised_artifact_manifest_id: str = "",
    ) -> dict[str, Any] | None:
        normalized = str(decision or "").strip().lower()
        allowed = {
            "approve", "approved", "continue",
            "approve_with_changes", "approved_with_changes",
            "reject", "rejected", "fail", "stop",
            "reject_and_stop",
            "rework", "retry", "redo", "reject_and_retry",
        }
        if normalized not in allowed:
            raise WorkflowRunStoreError("unsupported approval decision")
        canonical = {
            "approved": "approve",
            "continue": "approve",
            "approved_with_changes": "approve_with_changes",
            "reject": "reject_and_stop",
            "rejected": "reject_and_stop",
            "fail": "reject_and_stop",
            "stop": "reject_and_stop",
            "rework": "reject_and_retry",
            "retry": "reject_and_retry",
            "redo": "reject_and_retry",
        }.get(normalized, normalized)
        with self._transaction():
            row = self._conn.execute(
                "SELECT * FROM workflow_approvals "
                "WHERE id = ? AND workspace_id = ?",
                (approval_id, self.workspace_id),
            ).fetchone()
            if row is None:
                return None
            if row["status"] != "pending":
                raise WorkflowRunStoreError("workflow approval already decided")
            now = _now()
            self._conn.execute(
                "UPDATE workflow_approvals SET status = ?, decided_at = ?, decision = ?, "
                "decided_by = ?, comment = ?, comments_json = ?, "
                "revised_artifact_manifest_id = ? WHERE id = ?",
                (
                    "decided", now, canonical, decided_by, comment,
                    _dump(dict(comments or {})), revised_artifact_manifest_id,
                    approval_id,
                ),
            )
            self._event_locked(
                row["run_id"],
                "workflow_approval_decided",
                {
                    "run_id": row["run_id"],
                    "approval_id": approval_id,
                    "node_run_id": row["node_run_id"],
                    "decision": canonical,
                    "revised_artifact_manifest_id": revised_artifact_manifest_id,
                    "decided_by": decided_by,
                },
            )
        return self.get_approval(approval_id)

    def record_artifact_consumption(
        self,
        *,
        run_id: str,
        consumer_node_run_id: str,
        producer_node_run_id: str,
        manifest_id: str,
        artifact_id: str,
        purpose: str = "",
    ) -> bool:
        consumption_id = _id("ac")
        with self._transaction():
            if self.get_run(run_id) is None:
                return False
            try:
                self._conn.execute(
                    "INSERT INTO workflow_artifact_consumptions "
                    "(id, workspace_id, run_id, consumer_node_run_id, "
                    "producer_node_run_id, manifest_id, artifact_id, purpose, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        consumption_id,
                        self.workspace_id,
                        run_id,
                        consumer_node_run_id,
                        producer_node_run_id,
                        manifest_id,
                        artifact_id,
                        purpose,
                        _now(),
                    ),
                )
            except sqlite3.IntegrityError:
                return False
            self._event_locked(
                run_id,
                "workflow_artifact_consumed",
                {
                    "run_id": run_id,
                    "consumer_node_run_id": consumer_node_run_id,
                    "producer_node_run_id": producer_node_run_id,
                    "manifest_id": manifest_id,
                    "artifact_id": artifact_id,
                    "purpose": purpose,
                },
            )
        return True

    def list_consumptions(self, run_id: str) -> list[dict[str, Any]]:
        if self.get_run(run_id) is None:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM workflow_artifact_consumptions WHERE run_id = ? "
                "ORDER BY created_at, id",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_events(self, run_id: str, after_sequence: int = 0) -> list[dict[str, Any]]:
        if self.get_run(run_id) is None:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM workflow_events WHERE run_id = ? AND sequence > ? "
                "ORDER BY sequence",
                (run_id, max(0, int(after_sequence))),
            ).fetchall()
        return [
            {
                **_load(row["payload_json"], {}),
                "type": row["event_type"],
                "sequence": row["sequence"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def create_run_template(
        self,
        run_id: str,
        *,
        name: str,
        description: str = "",
        source_manifest_id: str = "",
        created_by: str = "",
    ) -> dict[str, Any]:
        """Mark one succeeded Run as a reusable template bound to its version."""
        with self._transaction():
            run = self._conn.execute(
                "SELECT * FROM workflow_runs WHERE id = ? AND workspace_id = ?",
                (run_id, self.workspace_id),
            ).fetchone()
            if run is None:
                raise WorkflowRunStoreError(f"workflow run not found: {run_id}")
            if str(run["status"]) != RunStatus.SUCCEEDED.value:
                raise WorkflowRunStoreError("only a succeeded workflow run can become a template")
            existing = self._conn.execute(
                "SELECT * FROM workflow_run_templates WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if existing is None:
                template_id = _id("wft")
                self._conn.execute(
                    "INSERT INTO workflow_run_templates "
                    "(id, workspace_id, run_id, workflow_version_id, name, "
                    "description, source_manifest_id, created_by, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        template_id,
                        self.workspace_id,
                        run_id,
                        run["workflow_version_id"],
                        str(name or "").strip() or f"Template {run_id}",
                        str(description or "").strip(),
                        str(source_manifest_id or ""),
                        str(created_by or ""),
                        _now(),
                    ),
                )
                self._event_locked(
                    run_id,
                    "workflow_template_created",
                    {"run_id": run_id, "template_id": template_id},
                )
                existing = self._conn.execute(
                    "SELECT * FROM workflow_run_templates WHERE id = ?",
                    (template_id,),
                ).fetchone()
        return dict(existing)

    def list_run_templates(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM workflow_run_templates "
                "WHERE workspace_id = ? ORDER BY created_at DESC",
                (self.workspace_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_knowledge_candidates(
        self,
        run_id: str,
        candidates: list[Mapping[str, Any]],
        *,
        source_manifest_id: str = "",
    ) -> list[dict[str, Any]]:
        """Create idempotent pending candidates from one succeeded Run."""
        with self._transaction():
            run = self._conn.execute(
                "SELECT * FROM workflow_runs WHERE id = ? AND workspace_id = ?",
                (run_id, self.workspace_id),
            ).fetchone()
            if run is None:
                raise WorkflowRunStoreError(f"workflow run not found: {run_id}")
            if str(run["status"]) != RunStatus.SUCCEEDED.value:
                raise WorkflowRunStoreError("knowledge candidates require a succeeded workflow run")
            for candidate in candidates:
                candidate_type = str(candidate.get("candidate_type") or "").strip()
                title = str(candidate.get("title") or "").strip()
                payload = candidate.get("payload")
                if not candidate_type or not title or not isinstance(payload, Mapping):
                    continue
                self._conn.execute(
                    "INSERT OR IGNORE INTO workflow_knowledge_candidates "
                    "(id, workspace_id, run_id, workflow_version_id, "
                    "source_manifest_id, candidate_type, title, payload_json, "
                    "status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        _id("wkc"),
                        self.workspace_id,
                        run_id,
                        run["workflow_version_id"],
                        str(source_manifest_id or ""),
                        candidate_type,
                        title[:240],
                        _dump(dict(payload)),
                        "pending",
                        _now(),
                    ),
                )
            rows = self._conn.execute(
                "SELECT * FROM workflow_knowledge_candidates "
                "WHERE run_id = ? ORDER BY created_at, id",
                (run_id,),
            ).fetchall()
            self._event_locked(
                run_id,
                "workflow_knowledge_candidates_created",
                {"run_id": run_id, "count": len(rows)},
            )
        return [self._knowledge_candidate(row) for row in rows]

    def list_knowledge_candidates(
        self,
        *,
        run_id: str = "",
        status: str = "",
    ) -> list[dict[str, Any]]:
        query = (
            "SELECT * FROM workflow_knowledge_candidates WHERE workspace_id = ?"
        )
        params: list[Any] = [self.workspace_id]
        if run_id:
            query += " AND run_id = ?"
            params.append(run_id)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC, id"
        with self._lock:
            rows = self._conn.execute(query, tuple(params)).fetchall()
        return [self._knowledge_candidate(row) for row in rows]

    def get_knowledge_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM workflow_knowledge_candidates "
                "WHERE id = ? AND workspace_id = ?",
                (candidate_id, self.workspace_id),
            ).fetchone()
        return self._knowledge_candidate(row) if row else None

    def decide_knowledge_candidate(
        self,
        candidate_id: str,
        *,
        decision: str,
        decided_by: str = "",
        comment: str = "",
        published_ref: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        target = "accepted" if decision == "accept" else "rejected"
        with self._transaction():
            row = self._conn.execute(
                "SELECT * FROM workflow_knowledge_candidates "
                "WHERE id = ? AND workspace_id = ?",
                (candidate_id, self.workspace_id),
            ).fetchone()
            if row is None:
                return None
            if str(row["status"]) in {"accepted", "rejected"}:
                return self._knowledge_candidate(row)
            self._conn.execute(
                "UPDATE workflow_knowledge_candidates SET status = ?, "
                "decision_comment = ?, decided_by = ?, decided_at = ?, "
                "published_ref_json = ? WHERE id = ?",
                (
                    target,
                    str(comment or ""),
                    str(decided_by or ""),
                    _now(),
                    _dump(dict(published_ref or {})),
                    candidate_id,
                ),
            )
            self._event_locked(
                row["run_id"],
                "workflow_knowledge_candidate_decided",
                {
                    "run_id": row["run_id"],
                    "candidate_id": candidate_id,
                    "decision": target,
                },
            )
            updated = self._conn.execute(
                "SELECT * FROM workflow_knowledge_candidates WHERE id = ?",
                (candidate_id,),
            ).fetchone()
        return self._knowledge_candidate(updated)
    def delete_run_cascade(self, run_id: str) -> dict[str, Any] | None:
        """Physically delete one terminal Run and all records it owns."""
        with self._transaction():
            run = self._conn.execute(
                "SELECT * FROM workflow_runs WHERE id = ? AND workspace_id = ?",
                (run_id, self.workspace_id),
            ).fetchone()
            if run is None:
                return None
            if str(run["status"]) not in {"canceled", "succeeded", "failed"}:
                raise WorkflowRunStoreError(
                    f"workflow run is still active: {run_id}"
                )
            job_rows = self._conn.execute(
                "SELECT job_id FROM workflow_node_runs "
                "WHERE run_id = ? AND job_id <> ''",
                (run_id,),
            ).fetchall()
            counts = {}
            for table, key, column in (
                ("workflow_knowledge_candidates", "knowledge_candidates", "run_id"),
                ("workflow_run_templates", "templates", "run_id"),
                ("workflow_artifact_consumptions", "consumptions", "run_id"),
                ("workflow_approvals", "approvals", "run_id"),
                ("workflow_artifact_manifests", "manifests", "run_id"),
                ("workflow_events", "events", "run_id"),
                ("workflow_event_sequences", None, "run_id"),
                ("workflow_node_runs", "node_runs", "run_id"),
                ("workflow_runs", "runs", "id"),
            ):
                cursor = self._conn.execute(
                    f"DELETE FROM {table} WHERE {column} = ?",
                    (run_id,),
                )
                if key:
                    counts[key] = max(0, int(cursor.rowcount))
        return {
            "run_id": run_id,
            "session_id": str(run["session_id"]),
            "job_ids": list(dict.fromkeys(str(row["job_id"]) for row in job_rows)),
            "deleted": counts,
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()
