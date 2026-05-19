# -*- coding: utf-8 -*-
"""
KnowledgeBase — SQLite-backed store for business knowledge.

DB location: <project_root>/uploads/knowledge/knowledge.db
  — relative to the project root so the path is portable across machines.

Three tables:
  metrics        — canonical metric definitions (DAU, LTV, …)
  business_rules — sanity-check assertions
  context_notes  — free-form background knowledge

Every table has an `enabled` column (1 = active, 0 = disabled).
Only enabled records are injected into the Agent's System Prompt
and returned by query_knowledge.
"""
import sqlite3
import time
from pathlib import Path

# ── Path resolution ───────────────────────────────────────────────────────────
# Walk up from this file: Function/Knowledge/ → Function/ → project root
_PROJECT_ROOT = Path(__file__).parent.parent.parent
_KB_DIR  = _PROJECT_ROOT / "uploads" / "knowledge"
_DB_PATH = _KB_DIR / "knowledge.db"


def _ensure_dir() -> None:
    _KB_DIR.mkdir(parents=True, exist_ok=True)


def _init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS metrics (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT NOT NULL UNIQUE,
            alias        TEXT DEFAULT '',
            definition   TEXT DEFAULT '',
            sql_template TEXT DEFAULT '',
            notes        TEXT DEFAULT '',
            enabled      INTEGER DEFAULT 1,
            updated_at   REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS business_rules (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_id     TEXT NOT NULL UNIQUE,
            description TEXT DEFAULT '',
            condition   TEXT DEFAULT '',
            severity    TEXT DEFAULT 'warning',
            enabled     INTEGER DEFAULT 1,
            updated_at  REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS context_notes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            topic      TEXT NOT NULL,
            content    TEXT DEFAULT '',
            tags       TEXT DEFAULT '',
            enabled    INTEGER DEFAULT 1,
            updated_at REAL NOT NULL
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS metrics_fts
            USING fts5(name, alias, definition, notes,
                       content=metrics, content_rowid=id);

        CREATE VIRTUAL TABLE IF NOT EXISTS context_notes_fts
            USING fts5(topic, content, tags,
                       content=context_notes, content_rowid=id);
    """)
    # Add enabled column to existing tables if upgrading from old schema
    for table in ("metrics", "business_rules", "context_notes"):
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN enabled INTEGER DEFAULT 1")
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()


class KnowledgeBase:
    """Thread-safe single-instance knowledge store."""

    def __init__(self, db_path: Path | None = None):
        _ensure_dir()
        self._path = db_path or _DB_PATH
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        _init_db(self._conn)

    # ── internal helpers ──────────────────────────────────────────────────────

    def _now(self) -> float:
        return time.time()

    def _rows(self, cur) -> list[dict]:
        return [dict(r) for r in cur.fetchall()]

    def _rebuild_fts(self, table: str) -> None:
        if table == "metrics":
            self._conn.execute(
                "INSERT INTO metrics_fts(metrics_fts) VALUES('rebuild')")
        elif table == "context_notes":
            self._conn.execute(
                "INSERT INTO context_notes_fts(context_notes_fts) VALUES('rebuild')")
        self._conn.commit()

    # ── enabled summary (for System Prompt injection) ─────────────────────────

    def get_enabled_summary(self) -> str:
        """Return a compact text block of all enabled records for injection
        into the LLM System Prompt.  Only name+definition for metrics
        (sql_template and notes are fetched on demand via query_knowledge).
        """
        metrics = self._rows(self._conn.execute(
            "SELECT name, alias, definition FROM metrics WHERE enabled=1 ORDER BY name"
        ))
        rules = self._rows(self._conn.execute(
            "SELECT rule_id, description, severity FROM business_rules WHERE enabled=1"
        ))
        notes = self._rows(self._conn.execute(
            "SELECT topic, content FROM context_notes WHERE enabled=1 ORDER BY topic"
        ))

        if not metrics and not rules and not notes:
            return ""

        parts: list[str] = ["## Business Knowledge Base (active entries)\n"]

        if metrics:
            parts.append("### Metric Definitions")
            for m in metrics:
                alias = f" ({m['alias']})" if m.get("alias") else ""
                defn  = m.get("definition") or "—"
                parts.append(f"- **{m['name']}**{alias}: {defn}")

        if rules:
            parts.append("\n### Business Rules")
            for r in rules:
                sev = r.get("severity", "warning").upper()
                parts.append(f"- [{sev}] {r['rule_id']}: {r.get('description','')}")

        if notes:
            parts.append("\n### Context Notes")
            for n in notes:
                parts.append(f"- **{n['topic']}**: {n.get('content','')[:200]}")

        return "\n".join(parts)

    # ── metrics CRUD ──────────────────────────────────────────────────────────

    def add_metric(self, name: str, alias: str = "", definition: str = "",
                   sql_template: str = "", notes: str = "",
                   enabled: int = 1) -> dict:
        cur = self._conn.execute(
            """INSERT INTO metrics
                 (name, alias, definition, sql_template, notes, enabled, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET
                 alias=excluded.alias, definition=excluded.definition,
                 sql_template=excluded.sql_template, notes=excluded.notes,
                 enabled=excluded.enabled, updated_at=excluded.updated_at""",
            (name.strip(), alias, definition, sql_template, notes,
             enabled, self._now()),
        )
        self._conn.commit()
        self._rebuild_fts("metrics")
        return self.get_metric_by_id(cur.lastrowid or self._metric_id(name))

    def _metric_id(self, name: str) -> int:
        row = self._conn.execute(
            "SELECT id FROM metrics WHERE name=?", (name,)).fetchone()
        return row["id"] if row else -1

    def get_metric_by_id(self, mid: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM metrics WHERE id=?", (mid,)).fetchone()
        return dict(row) if row else None

    def update_metric(self, mid: int, **fields) -> dict | None:
        allowed = {"name", "alias", "definition", "sql_template", "notes", "enabled"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return self.get_metric_by_id(mid)
        updates["updated_at"] = self._now()
        set_clause = ", ".join(f"{k}=?" for k in updates)
        self._conn.execute(
            f"UPDATE metrics SET {set_clause} WHERE id=?",
            (*updates.values(), mid),
        )
        self._conn.commit()
        self._rebuild_fts("metrics")
        return self.get_metric_by_id(mid)

    def delete_metric(self, mid: int) -> bool:
        self._conn.execute("DELETE FROM metrics WHERE id=?", (mid,))
        self._conn.commit()
        self._rebuild_fts("metrics")
        return True

    def list_metrics(self) -> list[dict]:
        return self._rows(
            self._conn.execute("SELECT * FROM metrics ORDER BY name"))

    # ── business_rules CRUD ───────────────────────────────────────────────────

    def add_rule(self, rule_id: str, description: str = "",
                 condition: str = "", severity: str = "warning",
                 enabled: int = 1) -> dict:
        cur = self._conn.execute(
            """INSERT INTO business_rules
                 (rule_id, description, condition, severity, enabled, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(rule_id) DO UPDATE SET
                 description=excluded.description, condition=excluded.condition,
                 severity=excluded.severity, enabled=excluded.enabled,
                 updated_at=excluded.updated_at""",
            (rule_id.strip(), description, condition, severity,
             enabled, self._now()),
        )
        self._conn.commit()
        rid = cur.lastrowid or self._rule_id(rule_id)
        return self.get_rule_by_id(rid)

    def _rule_id(self, rule_id: str) -> int:
        row = self._conn.execute(
            "SELECT id FROM business_rules WHERE rule_id=?", (rule_id,)
        ).fetchone()
        return row["id"] if row else -1

    def get_rule_by_id(self, rid: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM business_rules WHERE id=?", (rid,)
        ).fetchone()
        return dict(row) if row else None

    def update_rule(self, rid: int, **fields) -> dict | None:
        allowed = {"rule_id", "description", "condition", "severity", "enabled"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return self.get_rule_by_id(rid)
        updates["updated_at"] = self._now()
        set_clause = ", ".join(f"{k}=?" for k in updates)
        self._conn.execute(
            f"UPDATE business_rules SET {set_clause} WHERE id=?",
            (*updates.values(), rid),
        )
        self._conn.commit()
        return self.get_rule_by_id(rid)

    def delete_rule(self, rid: int) -> bool:
        self._conn.execute("DELETE FROM business_rules WHERE id=?", (rid,))
        self._conn.commit()
        return True

    def list_rules(self) -> list[dict]:
        return self._rows(self._conn.execute(
            "SELECT * FROM business_rules ORDER BY severity DESC, rule_id"))

    # ── context_notes CRUD ────────────────────────────────────────────────────

    def add_note(self, topic: str, content: str = "", tags: str = "",
                 enabled: int = 1) -> dict:
        cur = self._conn.execute(
            """INSERT INTO context_notes (topic, content, tags, enabled, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (topic.strip(), content, tags, enabled, self._now()),
        )
        self._conn.commit()
        self._rebuild_fts("context_notes")
        return self.get_note_by_id(cur.lastrowid)

    def get_note_by_id(self, nid: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM context_notes WHERE id=?", (nid,)
        ).fetchone()
        return dict(row) if row else None

    def update_note(self, nid: int, **fields) -> dict | None:
        allowed = {"topic", "content", "tags", "enabled"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return self.get_note_by_id(nid)
        updates["updated_at"] = self._now()
        set_clause = ", ".join(f"{k}=?" for k in updates)
        self._conn.execute(
            f"UPDATE context_notes SET {set_clause} WHERE id=?",
            (*updates.values(), nid),
        )
        self._conn.commit()
        self._rebuild_fts("context_notes")
        return self.get_note_by_id(nid)

    def delete_note(self, nid: int) -> bool:
        self._conn.execute("DELETE FROM context_notes WHERE id=?", (nid,))
        self._conn.commit()
        self._rebuild_fts("context_notes")
        return True

    def list_notes(self) -> list[dict]:
        return self._rows(self._conn.execute(
            "SELECT * FROM context_notes ORDER BY topic"))

    # ── search (only enabled records) ─────────────────────────────────────────

    def search(self, question: str, limit: int = 5) -> dict[str, list[dict]]:
        """Full-text search across enabled metrics and context_notes."""
        q = question.strip()

        try:
            metric_rows = self._rows(self._conn.execute(
                """SELECT m.* FROM metrics m
                   JOIN metrics_fts ON metrics_fts.rowid = m.id
                   WHERE metrics_fts MATCH ? AND m.enabled=1
                   ORDER BY rank LIMIT ?""",
                (q, limit),
            ))
        except sqlite3.OperationalError:
            like = f"%{q}%"
            metric_rows = self._rows(self._conn.execute(
                """SELECT * FROM metrics
                   WHERE (name LIKE ? OR alias LIKE ? OR definition LIKE ?)
                   AND enabled=1 LIMIT ?""",
                (like, like, like, limit),
            ))

        try:
            note_rows = self._rows(self._conn.execute(
                """SELECT n.* FROM context_notes n
                   JOIN context_notes_fts ON context_notes_fts.rowid = n.id
                   WHERE context_notes_fts MATCH ? AND n.enabled=1
                   ORDER BY rank LIMIT ?""",
                (q, limit),
            ))
        except sqlite3.OperationalError:
            like = f"%{q}%"
            note_rows = self._rows(self._conn.execute(
                """SELECT * FROM context_notes
                   WHERE (topic LIKE ? OR content LIKE ? OR tags LIKE ?)
                   AND enabled=1 LIMIT ?""",
                (like, like, like, limit),
            ))

        rule_rows = self._rows(self._conn.execute(
            "SELECT * FROM business_rules WHERE enabled=1 ORDER BY severity DESC"
        ))

        return {"metrics": metric_rows, "rules": rule_rows, "notes": note_rows}

    # ── bulk insert ───────────────────────────────────────────────────────────

    def bulk_insert(self, records: list[dict]) -> dict[str, int]:
        counts: dict[str, int] = {"metrics": 0, "rules": 0, "notes": 0}
        for rec in records:
            table = rec.get("table", "")
            if table == "metrics":
                self.add_metric(
                    name=rec.get("name", ""),
                    alias=rec.get("alias", ""),
                    definition=rec.get("definition", ""),
                    sql_template=rec.get("sql_template", ""),
                    notes=rec.get("notes", ""),
                )
                counts["metrics"] += 1
            elif table == "business_rules":
                self.add_rule(
                    rule_id=rec.get("rule_id", ""),
                    description=rec.get("description", ""),
                    condition=rec.get("condition", ""),
                    severity=rec.get("severity", "warning"),
                )
                counts["rules"] += 1
            elif table == "context_notes":
                self.add_note(
                    topic=rec.get("topic", ""),
                    content=rec.get("content", ""),
                    tags=rec.get("tags", ""),
                )
                counts["notes"] += 1
        return counts
