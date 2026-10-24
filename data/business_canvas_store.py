"""SQLite persistence for business canvas drawer projects.

Scope:
- built-in templates (draw.io diagram layouts, see data/diagram_templates.py)
- owner-scoped canvas projects
- immutable (capped) revision history for every diagram update
"""
from __future__ import annotations

import os
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from infrastructure.paths import content_data_path
from data.diagram_templates import DIAGRAM_TEMPLATES


_SCHEMA_VERSION = 4

# name/description are zh (default); name_en/description_en are picked by the
# frontend according to the current UI language.
_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "id": "blank_canvas",
        "name": "空白画布",
        "name_en": "Blank canvas",
        "description": "从零开始的空白 draw.io 画布，自由绘制任意图形。",
        "description_en": "An empty draw.io canvas — draw anything from scratch.",
        "priority": "P0",
        "rendering_mode": "diagram",
    },
    {
        "id": "business_model_canvas",
        "name": "商业模式画布",
        "name_en": "Business Model Canvas",
        "description": "用 9 个模块描绘商业模式，适合从用户、价值、渠道、收入和成本整体拆解产品。",
        "description_en": "Describe a business model in 9 blocks: customers, value, channels, revenue and cost.",
       "priority": "P0",
        "rendering_mode": "diagram",
   },
   {
       "id": "bcg_matrix",
        "name": "BCG 矩阵",
        "name_en": "BCG Matrix",
        "description": "2×2 quadrant: market growth vs relative market share, classify business units.",
        "description_en": "2×2 quadrant: market growth vs relative market share, classify business units.",
        "priority": "P0",
        "rendering_mode": "diagram",
    },
    {
        "id": "swot_analysis",
        "name": "SWOT 分析",
        "name_en": "SWOT Analysis",
        "description": "4 quadrant: internal strengths/weaknesses + external opportunities/threats.",
        "description_en": "4 quadrant: internal strengths/weaknesses + external opportunities/threats.",
        "priority": "P0",
        "rendering_mode": "diagram",
    },
    {
        "id": "value_proposition",
        "name": "价值主张速写",
        "name_en": "Value Proposition Sketch",
        "description": "Ad-Lib 句式拆解价值主张的六个组成部分，用一句话说清楚产品为谁、解决什么、有何不同。",
        "description_en": "Ad-Lib style: break a value proposition into 6 parts — one sentence for who, what and why different.",
        "priority": "P0",
        "rendering_mode": "diagram",
    },
)

_TEMPLATE_MAP = {tpl["id"]: tpl for tpl in _TEMPLATES}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS business_canvas_schema_meta (
    schema_version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS canvas_projects (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL DEFAULT '',
    owner_uid TEXT NOT NULL DEFAULT '',
    template_id TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    rendering_mode TEXT NOT NULL DEFAULT 'card',
    diagram_xml TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_canvas_projects_session_updated
    ON canvas_projects(session_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS canvas_diagram_revisions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    before_xml TEXT NOT NULL DEFAULT '',
    after_xml TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES canvas_projects(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_canvas_diagram_revisions_project_created
    ON canvas_diagram_revisions(project_id, created_at DESC);
"""

# Retention cap for per-project diagram revision history.
_DIAGRAM_REVISION_LIMIT = 100


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def business_canvas_db_path() -> Path:
    override = os.environ.get("BAA_BUSINESS_CANVAS_DB")
    if override:
        return Path(override)
    # Canvas content is user-generated.  In managed cloud deployments it must
    # stay on the ephemeral content root; desktop/local deployments still map
    # content_data_path() to their normal persistent application-data root.
    return content_data_path("business_canvas", "business_canvas.sqlite")


def list_canvas_templates() -> list[dict[str, Any]]:
    templates: list[dict[str, Any]] = []
    for tpl in _TEMPLATES:
        item = {
            "id": tpl["id"],
            "name": tpl["name"],
            "name_en": tpl.get("name_en") or tpl["name"],
            "description": tpl["description"],
            "description_en": tpl.get("description_en") or tpl["description"],
            "priority": tpl["priority"],
            "rendering_mode": tpl.get("rendering_mode", "diagram"),
        }
        diagram_xml = DIAGRAM_TEMPLATES.get(tpl["id"], {}).get("xml", "")
        if diagram_xml:
            item["diagram_xml"] = diagram_xml
        templates.append(item)
    return templates


class BusinessCanvasError(RuntimeError):
    pass


class BusinessCanvasStore:
    def __init__(self, db_path: Path | None = None):
        self._path = Path(db_path or business_canvas_db_path())
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.executescript(_SCHEMA)
            rows = self._conn.execute("SELECT schema_version FROM business_canvas_schema_meta").fetchall()
            if not rows:
                self._conn.execute(
                    "INSERT INTO business_canvas_schema_meta(schema_version) VALUES (?)",
                    (_SCHEMA_VERSION,),
                )
                self._conn.commit()
            else:
                current_version = int(rows[0]["schema_version"])
                if current_version < _SCHEMA_VERSION:
                    self._migrate(current_version, _SCHEMA_VERSION)

    def _migrate(self, from_version: int, to_version: int) -> None:
        """Apply incremental schema migrations."""
        with self._lock:
            self._begin()
            try:
                if from_version < 2:
                    self._conn.execute(
                        "ALTER TABLE canvas_projects ADD COLUMN rendering_mode TEXT NOT NULL DEFAULT 'card'"
                    )
                    self._conn.execute(
                        "ALTER TABLE canvas_projects ADD COLUMN diagram_xml TEXT NOT NULL DEFAULT ''"
                    )
                if from_version < 3:
                    # Fresh v3 databases already have owner_uid in CREATE TABLE;
                    # only ALTER when upgrading an older database.
                    cols = {
                        row["name"]
                        for row in self._conn.execute("PRAGMA table_info(canvas_projects)")
                    }
                    if "owner_uid" not in cols:
                        self._conn.execute(
                            "ALTER TABLE canvas_projects ADD COLUMN owner_uid TEXT NOT NULL DEFAULT ''"
                        )
                    self._conn.executescript(
                        "CREATE TABLE IF NOT EXISTS canvas_diagram_revisions (\n"
                        "    id TEXT PRIMARY KEY,\n"
                        "    project_id TEXT NOT NULL,\n"
                        "    actor_type TEXT NOT NULL,\n"
                        "    before_xml TEXT NOT NULL DEFAULT '',\n"
                        "    after_xml TEXT NOT NULL DEFAULT '',\n"
                        "    created_at TEXT NOT NULL,\n"
                        "    FOREIGN KEY(project_id) REFERENCES canvas_projects(id) ON DELETE CASCADE\n"
                        ");\n"
                        "CREATE INDEX IF NOT EXISTS idx_canvas_diagram_revisions_project_created\n"
                        "    ON canvas_diagram_revisions(project_id, created_at DESC);"
                    )
                if from_version < 4:
                    # Block/card editing layer removed in v4 — projects are
                    # diagram-only now. Drop the dead tables (data is unrecoverable
                    # from any live code path).
                    self._conn.execute("DROP TABLE IF EXISTS canvas_revisions")
                    self._conn.execute("DROP TABLE IF EXISTS canvas_blocks")
                self._conn.execute(
                    "UPDATE business_canvas_schema_meta SET schema_version = ?", (to_version,)
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def _begin(self) -> None:
        """Start an immediate transaction, committing any leaked one first."""
        if self._conn.in_transaction:
            self._conn.commit()
        self._conn.execute("BEGIN IMMEDIATE")

    def _project_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)

    def list_templates(self) -> list[dict[str, Any]]:
        return list_canvas_templates()

    def list_projects(
        self,
        session_id: str = "",
        *,
        owner_uid: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        # session_id is accepted for backwards-compat but no longer used as a filter.
        # Canvas projects are global (not session-scoped) so they survive server
        # restarts, but they are owner-scoped in multi-user (cloud) deployments.
        del session_id
        with self._lock:
            if owner_uid is None:
                rows = self._conn.execute(
                    "SELECT * FROM canvas_projects WHERE status = 'active' "
                    "ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM canvas_projects WHERE status = 'active' "
                    "AND owner_uid = ? "
                    "ORDER BY updated_at DESC LIMIT ?",
                    (owner_uid, limit),
                ).fetchall()
        return [self._project_from_row(row) for row in rows]

    def create_project(
        self,
        *,
        session_id: str,
        template_id: str,
        title: str,
        workspace_id: str = "",
        owner_uid: str = "",
    ) -> dict[str, Any]:
        session_id = str(session_id or "").strip()
        template_id = str(template_id or "").strip()
        title = str(title or "").strip()
        if not session_id:
            raise ValueError("session_id is required")
        template = _TEMPLATE_MAP.get(template_id)
        if not template:
            raise BusinessCanvasError(f"unknown canvas template: {template_id}")
        if not title:
            title = template["name"]
        rendering_mode = template.get("rendering_mode", "diagram")
        diagram_xml = DIAGRAM_TEMPLATES.get(template_id, {}).get("xml", "")
        now = _now_iso()
        project_id = _new_id("canvas")
        with self._lock:
            self._begin()
            try:
                self._conn.execute(
                    "INSERT INTO canvas_projects"
                    "(id, session_id, workspace_id, owner_uid, template_id, title, status, rendering_mode, diagram_xml, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)",
                    (project_id, session_id, workspace_id or "", owner_uid or "", template_id, title, rendering_mode, diagram_xml, now, now),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        project = self.get_project(project_id)
        if project is None:
            raise BusinessCanvasError("created canvas project not found")
        return project

    def get_project(
        self,
        project_id: str,
        *,
        session_id: str | None = None,
        owner_uid: str | None = None,
    ) -> dict[str, Any] | None:
        # session_id is kept for backwards-compat but no longer used as a filter.
        # owner_uid=None means "no ownership check" (internal/trusted callers);
        # a string value restricts access to that owner (plus shared '' rows).
        del session_id
        project_id = str(project_id or "").strip()
        if not project_id:
            return None
        with self._lock:
            if owner_uid is None:
                project_row = self._conn.execute(
                    "SELECT * FROM canvas_projects WHERE id = ? AND status = 'active'",
                    (project_id,),
                ).fetchone()
            else:
                project_row = self._conn.execute(
                    "SELECT * FROM canvas_projects WHERE id = ? AND status = 'active' "
                    "AND owner_uid = ?",
                    (project_id, owner_uid),
                ).fetchone()
            if not project_row:
                return None
        return self._project_from_row(project_row)

    def get_project_diagram_xml(
        self, project_id: str, *, owner_uid: str | None = None
    ) -> str:
        project_id = str(project_id or "").strip()
        if not project_id:
            return ""
        with self._lock:
            if owner_uid is None:
                row = self._conn.execute(
                    "SELECT diagram_xml FROM canvas_projects WHERE id = ? AND status = 'active'",
                    (project_id,),
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT diagram_xml FROM canvas_projects "
                    "WHERE id = ? AND status = 'active' AND owner_uid = ?",
                    (project_id, owner_uid),
                ).fetchone()
        return row["diagram_xml"] if row else ""

    def update_project_diagram_xml(
        self,
        *,
        project_id: str,
        diagram_xml: str,
        actor_type: str = "user",
        owner_uid: str | None = None,
    ) -> dict[str, Any]:
        project_id = str(project_id or "").strip()
        if not project_id:
            raise BusinessCanvasError("project_id is required")
        actor_type = str(actor_type or "").strip()
        if actor_type not in {"user", "agent"}:
            raise BusinessCanvasError("actor_type must be user or agent")
        project = self.get_project(project_id, owner_uid=owner_uid)
        if not project:
            raise BusinessCanvasError("canvas project not found")
        now = _now_iso()
        old_xml = project.get("diagram_xml", "")
        with self._lock:
            self._begin()
            try:
                self._conn.execute(
                    "UPDATE canvas_projects SET diagram_xml = ?, updated_at = ? WHERE id = ?",
                    (diagram_xml, now, project_id),
                )
                # Immutable revision history for diagram updates (capped retention).
                self._conn.execute(
                    "INSERT INTO canvas_diagram_revisions"
                    "(id, project_id, actor_type, before_xml, after_xml, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (_new_id("drev"), project_id, actor_type, old_xml, diagram_xml, now),
                )
                self._conn.execute(
                    "DELETE FROM canvas_diagram_revisions WHERE project_id = ? AND id NOT IN ("
                    "    SELECT id FROM canvas_diagram_revisions WHERE project_id = ? "
                    "    ORDER BY created_at DESC, rowid DESC LIMIT ?"
                    ")",
                    (project_id, project_id, _DIAGRAM_REVISION_LIMIT),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return self.get_project(project_id) or {}

    def list_diagram_revisions(
        self, project_id: str, *, owner_uid: str | None = None
    ) -> list[dict[str, Any]]:
        project = self.get_project(project_id, owner_uid=owner_uid)
        if not project:
            raise BusinessCanvasError("canvas project not found")
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM canvas_diagram_revisions WHERE project_id = ? "
                "ORDER BY created_at DESC, rowid DESC",
                (project_id,),
            ).fetchall()
        revisions: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["before_xml"] = item.pop("before_xml", "")
            item["after_xml"] = item.pop("after_xml", "")
            revisions.append(item)
        return revisions

    def update_project_rendering_mode(
        self,
        *,
        project_id: str,
        rendering_mode: str,
        owner_uid: str | None = None,
    ) -> dict[str, Any]:
        rendering_mode = str(rendering_mode or "card").strip()
        if rendering_mode not in {"card", "diagram", "both"}:
            raise BusinessCanvasError("rendering_mode must be card, diagram, or both")
        if not self.get_project(project_id, owner_uid=owner_uid):
            raise BusinessCanvasError("canvas project not found")
        now = _now_iso()
        with self._lock:
            self._begin()
            try:
                self._conn.execute(
                    "UPDATE canvas_projects SET rendering_mode = ?, updated_at = ? WHERE id = ?",
                    (rendering_mode, now, project_id),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return self.get_project(project_id) or {}

    def update_project_title(
        self,
        *,
        project_id: str,
        title: str,
        owner_uid: str | None = None,
    ) -> dict[str, Any]:
        title = str(title or "").strip()
        if not title:
            raise BusinessCanvasError("title is required")
        if not self.get_project(project_id, owner_uid=owner_uid):
            raise BusinessCanvasError("canvas project not found")
        now = _now_iso()
        with self._lock:
            self._begin()
            try:
                self._conn.execute(
                    "UPDATE canvas_projects SET title = ?, updated_at = ? WHERE id = ?",
                    (title, now, project_id),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return self.get_project(project_id) or {}

    def delete_project(self, project_id: str, *, owner_uid: str | None = None) -> None:
        """Soft-delete a canvas project (status='deleted'); blocks and revisions
        are preserved for potential recovery. `owner_uid` restricts deletion to
        the exact owning user; local mode uses the explicit empty-string owner.
        """
        project_id = str(project_id or "").strip()
        if not project_id:
            raise BusinessCanvasError("project_id is required")
        with self._lock:
            if owner_uid is None:
                cur = self._conn.execute(
                    "UPDATE canvas_projects SET status = 'deleted', updated_at = ? "
                    "WHERE id = ? AND status = 'active'",
                    (_now_iso(), project_id),
                )
            else:
                cur = self._conn.execute(
                    "UPDATE canvas_projects SET status = 'deleted', updated_at = ? "
                    "WHERE id = ? AND status = 'active' AND owner_uid = ?",
                    (_now_iso(), project_id, owner_uid),
                )
            if cur.rowcount == 0:
                raise BusinessCanvasError("canvas project not found")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()


_STORE: BusinessCanvasStore | None = None
_STORE_LOCK = threading.RLock()


def get_business_canvas_store() -> BusinessCanvasStore:
    global _STORE
    with _STORE_LOCK:
        path = business_canvas_db_path()
        if _STORE is None or _STORE._path != path:
            if _STORE is not None:
                _STORE.close()
            _STORE = BusinessCanvasStore(path)
        return _STORE


def reset_business_canvas_store() -> None:
    """Close the cached store. Intended for tests that use temporary DB files."""
    global _STORE
    with _STORE_LOCK:
        if _STORE is not None:
            _STORE.close()
            _STORE = None
