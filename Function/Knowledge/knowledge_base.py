# -*- coding: utf-8 -*-
"""
KnowledgeBase — SQLite-backed store for business knowledge.

DB location is scope-dependent:
  - mounted Workspace: <workspace>/.zhixi/knowledge/knowledge.db
  - desktop default user: <data_root>/uploads/knowledge/knowledge.db
  - named user: <data_root>/uploads/knowledge/users/<user-hash>/knowledge.db

Three tables:
  metrics        — canonical metric definitions (DAU, LTV, …)
  business_rules — sanity-check assertions
  context_notes  — free-form background knowledge

Every table has an `enabled` column (1 = active, 0 = disabled).
Only enabled records are returned by query_knowledge. Knowledge content is
never injected wholesale into the Agent's System Prompt.
"""
import logging
log = logging.getLogger(__name__)
import sqlite3
import time
import hashlib
import json
import math
import os
import re
from pathlib import Path
from infrastructure.paths import data_path

# ── Path resolution ───────────────────────────────────────────────────────────
# Walk up from this file: Function/Knowledge/ → Function/ → project root
_KB_DIR  = data_path("uploads", "knowledge")
_DB_PATH = _KB_DIR / "knowledge.db"
_DEFAULT_USER_ID = "local-default"


def normalize_user_id(user_id: str | None) -> str:
    """Return a bounded logical user key supplied by the trusted app layer."""
    value = str(user_id or "").strip()
    return value[:200] or _DEFAULT_USER_ID


def knowledge_scope_dir(
    *,
    workspace_id: str = "",
    user_id: str = "",
    workspace_root: Path | None = None,
) -> Path:
    """Resolve an isolated storage directory without mixing scope contents."""
    if workspace_id:
        root = Path(workspace_root).resolve() if workspace_root else None
        if root is None:
            from data.workspace import workspace_manager
            resolved = workspace_manager.root_for_workspace(str(workspace_id))
            root = Path(resolved).resolve() if resolved else None
        if root is None:
            raise ValueError("Knowledge Workspace is not available")
        return root / ".zhixi" / "knowledge"

    owner = normalize_user_id(user_id)
    # Preserve the existing desktop user's database and uploaded files.
    if owner == _DEFAULT_USER_ID:
        return _KB_DIR
    owner_hash = hashlib.sha256(owner.encode("utf-8")).hexdigest()[:24]
    return _KB_DIR / "users" / owner_hash


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError) as e:
        log.debug("[knowledge_base] 环境变量转换失败: %s", e)
        return default


MIN_STRUCTURED_SCORE = _env_float("BAA_KB_MIN_STRUCTURED_SCORE", 0.40)
MIN_CHUNK_SCORE = _env_float("BAA_KB_MIN_CHUNK_SCORE", 0.45)


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

        CREATE TABLE IF NOT EXISTS rag_chunks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT DEFAULT 'file',
            source_name TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            content     TEXT NOT NULL,
            embedding   TEXT NOT NULL,
            enabled     INTEGER DEFAULT 1,
            updated_at  REAL NOT NULL,
            UNIQUE(source_type, source_name, chunk_index)
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS rag_chunks_fts
            USING fts5(source_name, content,
                       content=rag_chunks, content_rowid=id);
    """)
    # Add enabled column to existing tables if upgrading from old schema
    for table in ("metrics", "business_rules", "context_notes"):
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN enabled INTEGER DEFAULT 1")
        except sqlite3.OperationalError as e:
            log.debug("[knowledge_base] 列已存在，跳过 ALTER TABLE: %s", e)
    for ddl in (
        "ALTER TABLE rag_chunks ADD COLUMN source_type TEXT DEFAULT 'file'",
        "ALTER TABLE rag_chunks ADD COLUMN enabled INTEGER DEFAULT 1",
    ):
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError as e:
            log.debug("[knowledge_base] 列已存在，跳过 DDL: %s", e)
    conn.commit()


# ── Local vectorizer ──────────────────────────────────────────────────────────

_EMBED_DIM = 384


def _cjk_runs(text: str) -> list[str]:
    return re.findall(r"[\u4e00-\u9fff]+", text or "")


def _cjk_ngrams(text: str, sizes: tuple[int, ...] = (2, 3, 4)) -> list[str]:
    grams: list[str] = []
    for run in _cjk_runs(text):
        chars = list(run)
        for n in sizes:
            grams.extend(
                "".join(chars[i:i + n])
                for i in range(max(0, len(chars) - n + 1))
            )
    return grams


def _tokens(text: str) -> list[str]:
    """Tokenize mixed Chinese/English text for local semantic-ish retrieval.

    This intentionally avoids heavyweight dependencies.  It combines Latin words,
    CJK unigrams, and short CJK n-grams so Chinese business terms still share
    signal even when the user's wording is not an exact FTS match.
    """
    text = (text or "").lower()
    words = re.findall(r"[a-z0-9_]+", text)
    cjk_chars = re.findall(r"[\u4e00-\u9fff]", text)
    cjk_runs = _cjk_runs(text)
    return words + cjk_chars + cjk_runs + _cjk_ngrams(text)


def _text_match_score(query: str, text: str) -> float:
    """Lightweight lexical score tuned for Chinese business phrases."""
    q = (query or "").lower().strip()
    t = (text or "").lower()
    if not q or not t:
        return 0.0

    score = 0.0
    if q in t:
        score += 1.2

    q_words = set(re.findall(r"[a-z0-9_]+", q))
    t_words = set(re.findall(r"[a-z0-9_]+", t))
    if q_words:
        score += 0.45 * len(q_words & t_words) / max(1, len(q_words))

    q_grams = set(_cjk_ngrams(q, sizes=(2, 3)))
    t_grams = set(_cjk_ngrams(t, sizes=(2, 3)))
    if q_grams:
        score += 0.9 * len(q_grams & t_grams) / max(1, len(q_grams))

    # Short Chinese terms such as 成本、奖励、溢价 often matter a lot in BI
    # questions; reward exact term overlap without requiring full phrase match.
    q_terms = {run for run in _cjk_runs(q) if len(run) >= 2}
    for term in q_terms:
        if term in t:
            score += min(0.3, len(term) / 20)

    return round(score, 4)


def _embed(text: str) -> list[float]:
    vec = [0.0] * _EMBED_DIM
    for tok in _tokens(text):
        digest = hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest()
        raw = int.from_bytes(digest, "big")
        idx = raw % _EMBED_DIM
        sign = -1.0 if raw & 1 else 1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec))
    if norm:
        vec = [round(v / norm, 6) for v in vec]
    return vec


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    return sum(x * y for x, y in zip(a, b))


def _chunk_text(text: str, max_chars: int = 1200, overlap: int = 160) -> list[str]:
    """Split text into retrieval chunks with light overlap."""
    text = re.sub(r"\r\n?", "\n", text or "")
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 2 <= max_chars:
            current = f"{current}\n\n{para}".strip() if current else para
            continue
        if current:
            chunks.append(current.strip())
        if len(para) <= max_chars:
            tail = current[-overlap:] if current and overlap else ""
            current = f"{tail}\n\n{para}".strip() if tail else para
        else:
            start = 0
            while start < len(para):
                end = start + max_chars
                piece = para[start:end].strip()
                if piece:
                    chunks.append(piece)
                if end >= len(para):
                    break
                next_start = end - overlap
                start = next_start if next_start > start else end
            current = ""
    if current:
        chunks.append(current.strip())
    return chunks


class KnowledgeBase:
    """Thread-safe single-instance knowledge store."""

    def __init__(
        self,
        db_path: Path | None = None,
        *,
        workspace_id: str = "",
        user_id: str = "",
        workspace_root: Path | None = None,
    ):
        scope_dir = (
            Path(db_path).parent
            if db_path is not None
            else knowledge_scope_dir(
                workspace_id=workspace_id,
                user_id=user_id,
                workspace_root=workspace_root,
            )
        )
        scope_dir.mkdir(parents=True, exist_ok=True)
        self._path = Path(db_path) if db_path is not None else scope_dir / "knowledge.db"
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        _init_db(self._conn)

    def close(self) -> None:
        """Close the SQLite connection explicitly.

        The app normally keeps short-lived instances around only briefly, but
        tests on Windows need this so temporary DB files can be removed.
        """
        try:
            self._conn.close()
        except Exception as e:
            log.warning("[knowledge_base] 关闭数据库连接异常: %s", e)

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
        elif table == "rag_chunks":
            self._conn.execute(
                "INSERT INTO rag_chunks_fts(rag_chunks_fts) VALUES('rebuild')")
        self._conn.commit()

    # ── enabled summary (admin/debug display only; never prompt injection) ─────

    def get_enabled_summary(self) -> str:
        """Return an administrative summary of enabled records.

        This method must not be used to build an LLM prompt. Runtime access uses
        ``search()`` so only relevant Top-K entries cross the model boundary.
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
        rag_sources = self._rows(self._conn.execute(
            """SELECT source_name, COUNT(*) AS chunks
               FROM rag_chunks
               WHERE enabled=1
               GROUP BY source_name
               ORDER BY source_name"""
        ))

        if not metrics and not rules and not notes and not rag_sources:
            return ""

        parts: list[str] = ["## Business Knowledge Base (active entries)\n"]

        if metrics:
            parts.append("### Metric Definitions")
            parts.append("(Call query_knowledge with the metric name or alias to get the full SQL template)")
            for m in metrics:
                alias = m.get("alias") or ""
                defn  = m.get("definition") or "—"
                has_sql = "✓ has SQL template" if m.get("sql_template") else ""
                alias_part = f" | alias: {alias}" if alias else ""
                sql_part   = f" | {has_sql}" if has_sql else ""
                parts.append(f"- **{m['name']}**{alias_part}: {defn}{sql_part}")

        if rules:
            parts.append("\n### Business Rules")
            for r in rules:
                sev = r.get("severity", "warning").upper()
                parts.append(f"- [{sev}] {r['rule_id']}: {r.get('description','')}")

        if notes:
            parts.append("\n### Context Notes")
            for n in notes:
                parts.append(f"- **{n['topic']}**: {n.get('content','')[:200]}")

        if rag_sources:
            parts.append("\n### Indexed Source Documents")
            parts.append("(Call query_knowledge to retrieve relevant chunks from these sources)")
            for src in rag_sources:
                parts.append(f"- {src['source_name']} ({src['chunks']} chunks)")

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

    # ── RAG document chunks ───────────────────────────────────────────────────

    def index_document(self, source_name: str, text: str,
                       source_type: str = "file", enabled: int = 1) -> dict[str, int]:
        """Chunk and vector-index a source document for RAG retrieval."""
        source_name = Path(source_name).name.strip()
        chunks = _chunk_text(text)
        self._conn.execute(
            "DELETE FROM rag_chunks WHERE source_type=? AND source_name=?",
            (source_type, source_name),
        )
        for idx, chunk in enumerate(chunks):
            self._conn.execute(
                """INSERT INTO rag_chunks
                     (source_type, source_name, chunk_index, content, embedding,
                      enabled, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    source_type,
                    source_name,
                    idx,
                    chunk,
                    json.dumps(_embed(chunk), separators=(",", ":")),
                    enabled,
                    self._now(),
                ),
            )
        self._conn.commit()
        self._rebuild_fts("rag_chunks")
        return {"chunks": len(chunks)}

    def delete_document_index(self, source_name: str, source_type: str = "file") -> int:
        source_name = Path(source_name).name
        cur = self._conn.execute(
            "DELETE FROM rag_chunks WHERE source_type=? AND source_name=?",
            (source_type, source_name),
        )
        self._conn.commit()
        self._rebuild_fts("rag_chunks")
        return cur.rowcount

    def list_chunks(self, limit: int = 200) -> list[dict]:
        return self._rows(self._conn.execute(
            """SELECT id, source_type, source_name, chunk_index, content,
                      enabled, updated_at
               FROM rag_chunks
               ORDER BY updated_at DESC LIMIT ?""",
            (limit,),
        ))

    def _vector_rank_records(
        self,
        query: str,
        records: list[dict],
        text_getter,
        limit: int,
        min_score: float = 0.0,
    ) -> list[dict]:
        q_vec = _embed(query)
        ranked: list[tuple[float, dict]] = []
        for rec in records:
            text = text_getter(rec)
            score = _cosine(q_vec, _embed(text)) + _text_match_score(query, text)
            if score >= min_score:
                ranked.append((score, rec))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [dict(r, vector_score=round(s, 4)) for s, r in ranked[:limit]]

    def _search_chunks(
        self,
        question: str,
        limit: int = 5,
        min_score: float = MIN_CHUNK_SCORE,
    ) -> list[dict]:
        q = question.strip()
        fts_rows: list[dict] = []
        try:
            fts_rows = self._rows(self._conn.execute(
                """SELECT c.id, c.source_type, c.source_name, c.chunk_index,
                          c.content, c.embedding, 1.0 AS keyword_score
                   FROM rag_chunks c
                   JOIN rag_chunks_fts ON rag_chunks_fts.rowid = c.id
                   WHERE rag_chunks_fts MATCH ? AND c.enabled=1
                   ORDER BY rank LIMIT ?""",
                (q, limit * 4),
            ))
        except sqlite3.OperationalError as e:
            log.debug("[knowledge_base] FTS 搜索失败，回退到 LIKE 查询: %s", e)
            like = f"%{q}%"
            fts_rows = self._rows(self._conn.execute(
                """SELECT id, source_type, source_name, chunk_index, content,
                          embedding, 0.6 AS keyword_score
                   FROM rag_chunks
                   WHERE enabled=1 AND (source_name LIKE ? OR content LIKE ?)
                   LIMIT ?""",
                (like, like, limit * 4),
            ))

        all_rows = self._rows(self._conn.execute(
            """SELECT id, source_type, source_name, chunk_index, content, embedding,
                      0.0 AS keyword_score
               FROM rag_chunks
               WHERE enabled=1"""
        ))
        by_id = {r["id"]: r for r in all_rows}
        for r in fts_rows:
            by_id[r["id"]] = r

        q_vec = _embed(q)
        ranked: list[tuple[float, dict]] = []
        for row in by_id.values():
            try:
                emb = json.loads(row.get("embedding") or "[]")
            except json.JSONDecodeError as e:
                log.debug("[knowledge_base] embedding JSON 解析失败: %s", e)
                emb = []
            vector_score = _cosine(q_vec, emb)
            lexical_score = _text_match_score(
                q,
                f"{row.get('source_name', '')}\n{row.get('content', '')}",
            )
            keyword_score = float(row.get("keyword_score") or 0.0)
            score = vector_score + lexical_score + keyword_score
            if score < min_score:
                continue
            clean = {
                k: v for k, v in row.items()
                if k not in {"embedding", "keyword_score"}
            }
            clean["vector_score"] = round(vector_score, 4)
            clean["lexical_score"] = round(lexical_score, 4)
            clean["score"] = round(score, 4)
            ranked.append((score, clean))

        ranked.sort(key=lambda item: item[0], reverse=True)
        return [r for _, r in ranked[:limit]]

    # ── search (only enabled records) ─────────────────────────────────────────

    def search(self, question: str, limit: int = 5) -> dict[str, list[dict]]:
        """Hybrid RAG search with a global Top-K cap across all result types."""
        q = question.strip()
        limit = max(1, min(int(limit or 5), 10))

        # Vector fallback for structured records.  This complements SQLite FTS,
        # especially for Chinese wording variations where tokenization is weak.
        all_metrics = self._rows(self._conn.execute(
            "SELECT * FROM metrics WHERE enabled=1"
        ))
        all_rules = self._rows(self._conn.execute(
            "SELECT * FROM business_rules WHERE enabled=1"
        ))
        all_notes = self._rows(self._conn.execute(
            "SELECT * FROM context_notes WHERE enabled=1"
        ))

        metric_rows = self._vector_rank_records(
            q,
            all_metrics,
            lambda m: " ".join([
                m.get("name", ""), m.get("alias", ""),
                m.get("definition", ""), m.get("notes", ""),
            ]),
            limit,
            min_score=MIN_STRUCTURED_SCORE,
        )

        note_rows = self._vector_rank_records(
            q,
            all_notes,
            lambda n: " ".join([
                n.get("topic", ""), n.get("content", ""), n.get("tags", ""),
            ]),
            limit,
            min_score=MIN_STRUCTURED_SCORE,
        )

        rule_rows = self._vector_rank_records(
            q,
            all_rules,
            lambda r: " ".join([
                r.get("rule_id", ""), r.get("description", ""),
                r.get("condition", ""), r.get("severity", ""),
            ]),
            limit,
            min_score=MIN_STRUCTURED_SCORE,
        )

        chunk_rows = self._search_chunks(q, limit=limit, min_score=MIN_CHUNK_SCORE)

        ranked: list[tuple[float, str, dict]] = []
        for kind, rows in (
            ("metrics", metric_rows),
            ("rules", rule_rows),
            ("notes", note_rows),
            ("documents", chunk_rows),
        ):
            for row in rows:
                score = float(row.get("score", row.get("vector_score", 0.0)) or 0.0)
                ranked.append((score, kind, row))
        ranked.sort(key=lambda item: item[0], reverse=True)

        result: dict[str, list[dict]] = {
            "metrics": [], "rules": [], "notes": [], "documents": [],
        }
        for _, kind, row in ranked[:limit]:
            result[kind].append(row)
        return result

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
