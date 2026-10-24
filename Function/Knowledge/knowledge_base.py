# -*- coding: utf-8 -*-
"""
KnowledgeBase — SQLite-backed store for business knowledge.

DB location is scope-dependent:
  - mounted Workspace: <workspace>/.zhixi/knowledge/knowledge.db
  - desktop default user: <content_data_root>/uploads/knowledge/knowledge.db
  - named user: <content_data_root>/uploads/knowledge/users/<user-hash>/knowledge.db

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
from infrastructure.paths import content_data_path
import jieba
from Function.Knowledge.neural_embedder import (
    embed as _neural_embed,
    embed_batch as _neural_embed_batch,
    embed_query as _neural_embed_query,
    get_embedding_signature as _neural_embedding_signature,
    cosine as _neural_cosine,
    is_neural as _is_neural_embedding,
)

# ── Path resolution ───────────────────────────────────────────────────────────
# Walk up from this file: Function/Knowledge/ → Function/ → project root
_KB_DIR  = content_data_path("uploads", "knowledge")
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


MIN_STRUCTURED_SCORE = _env_float("BAA_KB_MIN_STRUCTURED_SCORE", 0.015)
MIN_CHUNK_SCORE = _env_float("BAA_KB_MIN_CHUNK_SCORE", 0.02)
MIN_STRUCTURED_LEXICAL_SCORE = _env_float("BAA_KB_MIN_STRUCTURED_LEXICAL_SCORE", 0.12)
MIN_STRUCTURED_VECTOR_SCORE = _env_float("BAA_KB_MIN_STRUCTURED_VECTOR_SCORE", 0.55)
DEFAULT_CATEGORY_NAME = "默认业务"


def _ensure_dir() -> None:
    _KB_DIR.mkdir(parents=True, exist_ok=True)


def _init_db(conn: sqlite3.Connection) -> None:
    had_categories_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='knowledge_categories'"
    ).fetchone() is not None
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS knowledge_categories (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL UNIQUE,
            enabled    INTEGER DEFAULT 1,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS metrics (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT NOT NULL UNIQUE,
            alias        TEXT DEFAULT '',
            definition   TEXT DEFAULT '',
            sql_template TEXT DEFAULT '',
            notes        TEXT DEFAULT '',
            category_id  INTEGER DEFAULT 1,
            enabled      INTEGER DEFAULT 1,
            updated_at   REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS business_rules (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_id     TEXT NOT NULL UNIQUE,
            description TEXT DEFAULT '',
            condition   TEXT DEFAULT '',
            severity    TEXT DEFAULT 'warning',
            category_id INTEGER DEFAULT 1,
            enabled     INTEGER DEFAULT 1,
            updated_at  REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS context_notes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            topic      TEXT NOT NULL,
            content    TEXT DEFAULT '',
            tags       TEXT DEFAULT '',
            category_id INTEGER DEFAULT 1,
            enabled    INTEGER DEFAULT 1,
            updated_at REAL NOT NULL
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS metrics_fts
            USING fts5(name, alias, definition, notes,
                       content=metrics, content_rowid=id);

        CREATE VIRTUAL TABLE IF NOT EXISTS context_notes_fts
            USING fts5(topic, content, tags,
                       content=context_notes, content_rowid=id);

        CREATE TABLE IF NOT EXISTS structured_embeddings (
            entity_type         TEXT NOT NULL,
            entity_id           INTEGER NOT NULL,
            content_hash        TEXT NOT NULL,
            embedding_signature TEXT NOT NULL,
            embedding           TEXT NOT NULL,
            updated_at          REAL NOT NULL,
            PRIMARY KEY(entity_type, entity_id)
        );
        CREATE TABLE IF NOT EXISTS rag_chunks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT DEFAULT 'file',
            source_name TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            content     TEXT NOT NULL,
            embedding   TEXT NOT NULL,
            category_id INTEGER DEFAULT 1,
            enabled     INTEGER DEFAULT 1,
            updated_at  REAL NOT NULL,
            UNIQUE(source_type, source_name, chunk_index)
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS rag_chunks_fts
            USING fts5(source_name, content,
                       content=rag_chunks, content_rowid=id);
    """)
    now = time.time()
    if not had_categories_table:
        conn.execute(
            "INSERT INTO knowledge_categories (id, name, enabled, updated_at) VALUES (1, ?, 1, ?)",
            (DEFAULT_CATEGORY_NAME, now),
        )
    # Add enabled/category columns to existing tables if upgrading from old schema
    for table in ("metrics", "business_rules", "context_notes"):
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN enabled INTEGER DEFAULT 1")
        except sqlite3.OperationalError as e:
            log.debug("[knowledge_base] 列已存在，跳过 ALTER TABLE: %s", e)
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN category_id INTEGER DEFAULT 1")
        except sqlite3.OperationalError as e:
            log.debug("[knowledge_base] 分类列已存在，跳过 ALTER TABLE: %s", e)
        conn.execute(f"UPDATE {table} SET category_id=1 WHERE category_id IS NULL")
    for ddl in (
        "ALTER TABLE rag_chunks ADD COLUMN source_type TEXT DEFAULT 'file'",
        "ALTER TABLE rag_chunks ADD COLUMN enabled INTEGER DEFAULT 1",
        "ALTER TABLE rag_chunks ADD COLUMN category_id INTEGER DEFAULT 1",
        "ALTER TABLE rag_chunks ADD COLUMN embedding_signature TEXT DEFAULT ''",
        "ALTER TABLE rag_chunks ADD COLUMN content_hash TEXT DEFAULT ''",
    ):
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError as e:
            log.debug("[knowledge_base] 列已存在，跳过 DDL: %s", e)
    conn.execute("UPDATE rag_chunks SET category_id=1 WHERE category_id IS NULL")
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
    """Embed text using neural BGE model (with hash fallback)."""
    return _neural_embed(text)


def _embed_batch(texts: list[str]) -> list[list[float]]:
    """Batch embed for efficiency (neural model supports batched inference)."""
    return _neural_embed_batch(texts)


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity (vectors are pre-normalized, so dot product suffices)."""
    return _neural_cosine(a, b)

def _embed_query(text: str) -> list[float]:
    return _neural_embed_query(text)


def _embedding_signature() -> str:
    return _neural_embedding_signature()


def _jieba_tokenize(text: str) -> str:
    """Jieba-segment text into space-separated tokens for FTS5 matching.

    FTS5 treats whitespace as token boundary. Without jieba, Chinese text is
    stored as one giant token and MATCH queries fail. With jieba, '商业画布分析'
    becomes '商业 画布 分析', each searchable independently.
    """
    words = jieba.cut_for_search(text or "")
    return " ".join(w.strip() for w in words if w.strip())


def _rrf_fuse(
    ranked_lists: list[list[dict]],
    id_key: str,
    k: int = 60,
    limit: int = 5,
) -> list[dict]:
    """Reciprocal Rank Fusion: combine multiple ranked lists by position.

    RRF score = sum(1 / (k + rank_i)) across all lists where the item appears.
    This normalises different score scales (cosine vs BM25 vs lexical) into a
    single rank-based score, which is more robust than weighted sum.
    """
    scores: dict[object, float] = {}
    items: dict[object, dict] = {}
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked):
            iid = item.get(id_key)
            if iid is None:
                continue
            scores[iid] = scores.get(iid, 0.0) + 1.0 / (k + rank)
            items[iid] = item
    fused = sorted(scores.items(), key=lambda x: -x[1])
    result = []
    for iid, score in fused[:limit]:
        item = dict(items[iid])
        item["rrf_score"] = round(score, 6)
        result.append(item)
    return result


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

    def __enter__(self) -> "KnowledgeBase":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ── internal helpers ──────────────────────────────────────────────────────

    def _now(self) -> float:
        return time.time()

    def _rows(self, cur) -> list[dict]:
        return [dict(r) for r in cur.fetchall()]

    @staticmethod
    def _structured_text(entity_type: str, record: dict) -> str:
        if entity_type == "metric":
            fields = ("name", "alias", "definition", "notes")
        elif entity_type == "rule":
            fields = ("rule_id", "description", "condition", "severity")
        else:
            fields = ("topic", "content", "tags")
        return " ".join(str(record.get(key) or "") for key in fields).strip()

    def _load_structured_embeddings(
        self, entity_type: str, records: list[dict], signature: str
    ) -> dict[int, list[float]]:
        if not records:
            return {}
        ids = [int(record["id"]) for record in records]
        placeholders = ",".join("?" for _ in ids)
        rows = self._rows(self._conn.execute(
            f"""SELECT entity_id, content_hash, embedding
                FROM structured_embeddings
                WHERE entity_type=? AND embedding_signature=?
                  AND entity_id IN ({placeholders})""",
            (entity_type, signature, *ids),
        ))
        hashes = {
            int(record["id"]): hashlib.sha256(
                self._structured_text(entity_type, record).encode("utf-8")
            ).hexdigest()
            for record in records
        }
        result: dict[int, list[float]] = {}
        for row in rows:
            entity_id = int(row["entity_id"])
            if row["content_hash"] != hashes.get(entity_id):
                continue
            try:
                result[entity_id] = json.loads(row["embedding"])
            except (TypeError, json.JSONDecodeError):
                continue
        return result

    def rebuild_structured_embeddings(self) -> int:
        specs = (
            ("metric", "metrics"),
            ("rule", "business_rules"),
            ("note", "context_notes"),
        )
        signature = _embedding_signature()
        stale: list[tuple[str, int, str, str]] = []
        for entity_type, table in specs:
            records = self._rows(self._conn.execute(f"SELECT * FROM {table}"))
            cached = self._load_structured_embeddings(entity_type, records, signature)
            for record in records:
                entity_id = int(record["id"])
                if entity_id in cached:
                    continue
                text = self._structured_text(entity_type, record)
                content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                stale.append((entity_type, entity_id, text, content_hash))
        if not stale:
            return 0
        vectors = _embed_batch([item[2] for item in stale])
        now = self._now()
        for (entity_type, entity_id, _text, content_hash), vector in zip(stale, vectors):
            self._conn.execute(
                """INSERT INTO structured_embeddings
                     (entity_type, entity_id, content_hash, embedding_signature, embedding, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(entity_type, entity_id) DO UPDATE SET
                     content_hash=excluded.content_hash,
                     embedding_signature=excluded.embedding_signature,
                     embedding=excluded.embedding,
                     updated_at=excluded.updated_at""",
                (entity_type, entity_id, content_hash, signature,
                 json.dumps(vector, separators=(",", ":")), now),
            )
        self._conn.commit()
        return len(stale)

    def _cache_structured_record(self, entity_type: str, record: dict | None) -> bool:
        if not record:
            return False
        text = self._structured_text(entity_type, record)
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        signature = _embedding_signature()
        cached = self._conn.execute(
            """SELECT 1 FROM structured_embeddings
               WHERE entity_type=? AND entity_id=?
                 AND content_hash=? AND embedding_signature=?""",
            (entity_type, int(record["id"]), content_hash, signature),
        ).fetchone()
        if cached:
            return False
        vector = _embed_batch([text])[0]
        self._conn.execute(
            """INSERT INTO structured_embeddings
                 (entity_type, entity_id, content_hash, embedding_signature, embedding, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(entity_type, entity_id) DO UPDATE SET
                 content_hash=excluded.content_hash,
                 embedding_signature=excluded.embedding_signature,
                 embedding=excluded.embedding,
                 updated_at=excluded.updated_at""",
            (entity_type, int(record["id"]), content_hash, signature,
             json.dumps(vector, separators=(",", ":")), self._now()),
        )
        self._conn.commit()
        return True
    def _try_cache_structured_record(self, entity_type: str, record: dict | None) -> None:
        try:
            self._cache_structured_record(entity_type, record)
        except Exception as exc:
            log.warning("[knowledge_base] %s %s vector cache failed: %s",
                        entity_type, (record or {}).get("id", "?"), exc)
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

    # ── categories ────────────────────────────────────────────────────────────

    def _normalize_category_id(self, category_id: int | str | None = None) -> int:
        try:
            cid = int(category_id or 1)
        except (TypeError, ValueError):
            cid = 1
        row = self._conn.execute(
            "SELECT id FROM knowledge_categories WHERE id=?", (cid,)
        ).fetchone()
        if row:
            return int(row["id"])
        fallback = self._conn.execute(
            "SELECT id FROM knowledge_categories ORDER BY id LIMIT 1"
        ).fetchone()
        if not fallback:
            raise ValueError("请先创建业务分类")
        return int(fallback["id"])

    def list_categories(self) -> list[dict]:
        return self._rows(self._conn.execute(
            "SELECT * FROM knowledge_categories ORDER BY id=1 DESC, name"
        ))

    def add_category(self, name: str, enabled: int = 1) -> dict:
        name = (name or "").strip()
        if not name:
            raise ValueError("分类名称不能为空")
        cur = self._conn.execute(
            """INSERT INTO knowledge_categories (name, enabled, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET
                 enabled=excluded.enabled, updated_at=excluded.updated_at""",
            (name, enabled, self._now()),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT id FROM knowledge_categories WHERE name=?", (name,)
        ).fetchone()
        cid = int(row["id"]) if row else int(cur.lastrowid)
        return self.get_category_by_id(cid)

    def get_category_by_id(self, cid: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM knowledge_categories WHERE id=?", (cid,)
        ).fetchone()
        return dict(row) if row else None

    def update_category(self, cid: int, **fields) -> dict | None:
        allowed = {"name", "enabled"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if "name" in updates:
            updates["name"] = str(updates["name"] or "").strip()
            if not updates["name"]:
                raise ValueError("分类名称不能为空")
        if not updates:
            return self.get_category_by_id(cid)
        updates["updated_at"] = self._now()
        set_clause = ", ".join(f"{k}=?" for k in updates)
        self._conn.execute(
            f"UPDATE knowledge_categories SET {set_clause} WHERE id=?",
            (*updates.values(), cid),
        )
        self._conn.commit()
        return self.get_category_by_id(cid)

    def delete_category(self, cid: int) -> dict[str, int]:
        """Delete a non-default category and all of its indexed knowledge."""
        cid = int(cid)
        if not self.get_category_by_id(cid):
            raise LookupError("业务分类不存在")

        tables = {
            "metrics": "metrics",
            "rules": "business_rules",
            "notes": "context_notes",
            "chunks": "rag_chunks",
        }
        counts = {
            key: int(self._conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE category_id=?", (cid,)
            ).fetchone()[0])
            for key, table in tables.items()
        }
        try:
            for entity_type, table in (("metric", "metrics"),
                                       ("rule", "business_rules"),
                                       ("note", "context_notes")):
                self._conn.execute(
                    f"""DELETE FROM structured_embeddings
                        WHERE entity_type=? AND entity_id IN
                              (SELECT id FROM {table} WHERE category_id=?)""",
                    (entity_type, cid),
                )
            for table in tables.values():
                self._conn.execute(f"DELETE FROM {table} WHERE category_id=?", (cid,))
            self._conn.execute("DELETE FROM knowledge_categories WHERE id=?", (cid,))
            for fts_table in ("metrics_fts", "context_notes_fts", "rag_chunks_fts"):
                self._conn.execute(
                    f"INSERT INTO {fts_table}({fts_table}) VALUES('rebuild')"
                )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return counts

    def replace_structured_snapshot(self, snapshot: dict) -> dict[str, int]:
        """Replace structured knowledge from a browser-owned cloud snapshot.

        Managed Web deployments deliberately keep user content outside the
        Railway Volume.  The browser therefore owns the durable copy and uses
        this method only to reconstruct the disposable server-side search
        store.  Source-document chunks are restored separately from file
        blobs, so they are intentionally cleared here.
        """
        if not isinstance(snapshot, dict):
            raise ValueError("知识库快照格式无效")

        raw_categories = snapshot.get("categories") or []
        category_rows = []
        seen_names: set[str] = set()
        for item in raw_categories:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()[:120]
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            category_rows.append((name, 1 if item.get("enabled", True) else 0))
        if not category_rows:
            category_rows.append((DEFAULT_CATEGORY_NAME, 1))
        if len(category_rows) > 100:
            raise ValueError("业务分类数量超过上限")

        records_by_kind = {
            "metrics": snapshot.get("metrics") or [],
            "rules": snapshot.get("rules") or [],
            "notes": snapshot.get("notes") or [],
        }
        if any(not isinstance(rows, list) or len(rows) > 2000
               for rows in records_by_kind.values()):
            raise ValueError("知识库条目数量超过上限或格式无效")

        try:
            self._conn.execute("BEGIN")
            for table in ("structured_embeddings", "rag_chunks", "metrics",
                          "business_rules", "context_notes", "knowledge_categories"):
                self._conn.execute(f"DELETE FROM {table}")
            now = self._now()
            category_ids: dict[str, int] = {}
            for name, enabled in category_rows:
                cur = self._conn.execute(
                    "INSERT INTO knowledge_categories (name, enabled, updated_at) VALUES (?, ?, ?)",
                    (name, enabled, now),
                )
                category_ids[name] = int(cur.lastrowid)

            fallback_category = next(iter(category_ids.values()))
            inserted = {"metrics": 0, "rules": 0, "notes": 0}
            for item in records_by_kind["metrics"]:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()[:200]
                if not name:
                    continue
                cid = category_ids.get(str(item.get("category_name") or ""), fallback_category)
                self._conn.execute(
                    """INSERT INTO metrics
                       (name, alias, definition, sql_template, notes, category_id, enabled, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (name, str(item.get("alias") or ""), str(item.get("definition") or ""),
                     str(item.get("sql_template") or ""), str(item.get("notes") or ""), cid,
                     1 if item.get("enabled", True) else 0, now),
                )
                inserted["metrics"] += 1
            for item in records_by_kind["rules"]:
                if not isinstance(item, dict):
                    continue
                rule_id = str(item.get("rule_id") or "").strip()[:200]
                if not rule_id:
                    continue
                cid = category_ids.get(str(item.get("category_name") or ""), fallback_category)
                self._conn.execute(
                    """INSERT INTO business_rules
                       (rule_id, description, condition, severity, category_id, enabled, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (rule_id, str(item.get("description") or ""), str(item.get("condition") or ""),
                     str(item.get("severity") or "warning"), cid,
                     1 if item.get("enabled", True) else 0, now),
                )
                inserted["rules"] += 1
            for item in records_by_kind["notes"]:
                if not isinstance(item, dict):
                    continue
                topic = str(item.get("topic") or "").strip()[:200]
                if not topic:
                    continue
                cid = category_ids.get(str(item.get("category_name") or ""), fallback_category)
                self._conn.execute(
                    """INSERT INTO context_notes
                       (topic, content, tags, category_id, enabled, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (topic, str(item.get("content") or ""), str(item.get("tags") or ""), cid,
                     1 if item.get("enabled", True) else 0, now),
                )
                inserted["notes"] += 1
            self._conn.execute("INSERT INTO metrics_fts(metrics_fts) VALUES('rebuild')")
            self._conn.execute("INSERT INTO context_notes_fts(context_notes_fts) VALUES('rebuild')")
            self._conn.execute("INSERT INTO rag_chunks_fts(rag_chunks_fts) VALUES('rebuild')")
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

        if sum(inserted.values()):
            self.rebuild_structured_embeddings()
        return {**inserted, "categories": len(category_rows)}

    # ── enabled summary (admin/debug display only; never prompt injection) ─────

    def get_enabled_summary(self) -> str:
        """Return an administrative summary of enabled records.

        This method must not be used to build an LLM prompt. Runtime access uses
        ``search()`` so only relevant Top-K entries cross the model boundary.
        """
        metrics = self._rows(self._conn.execute(
            """SELECT m.name, m.alias, m.definition, m.sql_template FROM metrics m
               JOIN knowledge_categories c ON c.id=m.category_id
               WHERE m.enabled=1 AND c.enabled=1 ORDER BY m.name"""
        ))
        rules = self._rows(self._conn.execute(
            """SELECT r.rule_id, r.description, r.severity FROM business_rules r
               JOIN knowledge_categories c ON c.id=r.category_id
               WHERE r.enabled=1 AND c.enabled=1"""
        ))
        notes = self._rows(self._conn.execute(
            """SELECT n.topic, n.content FROM context_notes n
               JOIN knowledge_categories c ON c.id=n.category_id
               WHERE n.enabled=1 AND c.enabled=1 ORDER BY n.topic"""
        ))
        rag_sources = self._rows(self._conn.execute(
            """SELECT source_name, COUNT(*) AS chunks
               FROM rag_chunks rc
               JOIN knowledge_categories c ON c.id=rc.category_id
               WHERE rc.enabled=1 AND c.enabled=1
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
                   enabled: int = 1, category_id: int | str | None = None,
                   cache_embedding: bool = True,
                   rebuild_fts: bool = True) -> dict:
        cur = self._conn.execute(
            """INSERT INTO metrics
                 (name, alias, definition, sql_template, notes, category_id, enabled, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET
                 alias=excluded.alias, definition=excluded.definition,
                 sql_template=excluded.sql_template, notes=excluded.notes,
                 category_id=excluded.category_id, enabled=excluded.enabled,
                 updated_at=excluded.updated_at""",
            (name.strip(), alias, definition, sql_template, notes,
             self._normalize_category_id(category_id), enabled, self._now()),
        )
        self._conn.commit()
        if rebuild_fts:
            self._rebuild_fts("metrics")
        record = self.get_metric_by_id(cur.lastrowid or self._metric_id(name))
        if cache_embedding:
            self._try_cache_structured_record("metric", record)
        return record

    def _metric_id(self, name: str) -> int:
        row = self._conn.execute(
            "SELECT id FROM metrics WHERE name=?", (name,)).fetchone()
        return row["id"] if row else -1

    def get_metric_by_id(self, mid: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM metrics WHERE id=?", (mid,)).fetchone()
        return dict(row) if row else None

    def update_metric(self, mid: int, **fields) -> dict | None:
        allowed = {"name", "alias", "definition", "sql_template", "notes", "enabled", "category_id"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if "category_id" in updates:
            updates["category_id"] = self._normalize_category_id(updates["category_id"])
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
        record = self.get_metric_by_id(mid)
        self._try_cache_structured_record("metric", record)
        return record

    def delete_metric(self, mid: int) -> bool:
        self._conn.execute("DELETE FROM metrics WHERE id=?", (mid,))
        self._conn.execute("DELETE FROM structured_embeddings WHERE entity_type=? AND entity_id=?",
                           ("metric", mid))
        self._conn.commit()
        self._rebuild_fts("metrics")
        return True

    def list_metrics(self, category_id: int | str | None = None) -> list[dict]:
        cid = self._normalize_category_id(category_id)
        return self._rows(
            self._conn.execute("SELECT * FROM metrics WHERE category_id=? ORDER BY name", (cid,)))

    # ── business_rules CRUD ───────────────────────────────────────────────────

    def add_rule(self, rule_id: str, description: str = "",
                 condition: str = "", severity: str = "warning",
                 enabled: int = 1, category_id: int | str | None = None,
                 cache_embedding: bool = True,
                 rebuild_fts: bool = True) -> dict:
        cur = self._conn.execute(
            """INSERT INTO business_rules
                 (rule_id, description, condition, severity, category_id, enabled, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(rule_id) DO UPDATE SET
                 description=excluded.description, condition=excluded.condition,
                 severity=excluded.severity, category_id=excluded.category_id,
                 enabled=excluded.enabled, updated_at=excluded.updated_at""",
            (rule_id.strip(), description, condition, severity,
             self._normalize_category_id(category_id), enabled, self._now()),
        )
        self._conn.commit()
        rid = cur.lastrowid or self._rule_id(rule_id)
        record = self.get_rule_by_id(rid)
        if cache_embedding:
            self._try_cache_structured_record("rule", record)
        return record

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
        allowed = {"rule_id", "description", "condition", "severity", "enabled", "category_id"}
        updates = {key: value for key, value in fields.items() if key in allowed}
        if "category_id" in updates:
            updates["category_id"] = self._normalize_category_id(updates["category_id"])
        if not updates:
            return self.get_rule_by_id(rid)
        updates["updated_at"] = self._now()
        set_clause = ", ".join(f"{key}=?" for key in updates)
        self._conn.execute(
            f"UPDATE business_rules SET {set_clause} WHERE id=?",
            (*updates.values(), rid),
        )
        self._conn.commit()
        record = self.get_rule_by_id(rid)
        self._try_cache_structured_record("rule", record)
        return record
    def delete_rule(self, rid: int) -> bool:
        self._conn.execute("DELETE FROM business_rules WHERE id=?", (rid,))
        self._conn.execute("DELETE FROM structured_embeddings WHERE entity_type=? AND entity_id=?",
                           ("rule", rid))
        self._conn.commit()
        return True

    def list_rules(self, category_id: int | str | None = None) -> list[dict]:
        cid = self._normalize_category_id(category_id)
        return self._rows(self._conn.execute(
            "SELECT * FROM business_rules WHERE category_id=? ORDER BY severity DESC, rule_id", (cid,)))

    # ── context_notes CRUD ────────────────────────────────────────────────────

    def add_note(self, topic: str, content: str = "", tags: str = "",
                 enabled: int = 1, category_id: int | str | None = None,
                 cache_embedding: bool = True,
                 rebuild_fts: bool = True) -> dict:
        cur = self._conn.execute(
            """INSERT INTO context_notes (topic, content, tags, category_id, enabled, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (topic.strip(), content, tags, self._normalize_category_id(category_id), enabled, self._now()),
        )
        self._conn.commit()
        if rebuild_fts:
            self._rebuild_fts("context_notes")
        record = self.get_note_by_id(cur.lastrowid)
        if cache_embedding:
            self._try_cache_structured_record("note", record)
        return record

    def get_note_by_id(self, nid: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM context_notes WHERE id=?", (nid,)
        ).fetchone()
        return dict(row) if row else None

    def update_note(self, nid: int, **fields) -> dict | None:
        allowed = {"topic", "content", "tags", "enabled", "category_id"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if "category_id" in updates:
            updates["category_id"] = self._normalize_category_id(updates["category_id"])
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
        record = self.get_note_by_id(nid)
        self._try_cache_structured_record("note", record)
        return record

    def delete_note(self, nid: int) -> bool:
        self._conn.execute("DELETE FROM context_notes WHERE id=?", (nid,))
        self._conn.execute("DELETE FROM structured_embeddings WHERE entity_type=? AND entity_id=?",
                           ("note", nid))
        self._conn.commit()
        self._rebuild_fts("context_notes")
        return True

    def list_notes(self, category_id: int | str | None = None) -> list[dict]:
        cid = self._normalize_category_id(category_id)
        return self._rows(self._conn.execute(
            "SELECT * FROM context_notes WHERE category_id=? ORDER BY topic", (cid,)))

    # ── RAG document chunks ───────────────────────────────────────────────────

    def index_document(self, source_name: str, text: str,
                       source_type: str = "file", enabled: int = 1,
                       category_id: int | str | None = None) -> dict[str, int]:
        """Chunk and incrementally vector-index a source document."""
        source_name = Path(source_name).name.strip()
        chunks = _chunk_text(text)
        signature = _embedding_signature()
        existing = self._rows(self._conn.execute(
            """SELECT chunk_index, content_hash, embedding, embedding_signature
               FROM rag_chunks WHERE source_type=? AND source_name=?""",
            (source_type, source_name),
        ))
        cached = {
            (int(row["chunk_index"]), row.get("content_hash"), row.get("embedding_signature")):
                row.get("embedding")
            for row in existing
        }
        hashes = [hashlib.sha256(chunk.encode("utf-8")).hexdigest() for chunk in chunks]
        serialized_vectors: list[str | None] = [
            cached.get((idx, content_hash, signature))
            for idx, content_hash in enumerate(hashes)
        ]
        stale_indexes = [idx for idx, value in enumerate(serialized_vectors) if not value]
        if stale_indexes:
            vectors = _embed_batch([chunks[idx] for idx in stale_indexes])
            for idx, vector in zip(stale_indexes, vectors):
                serialized_vectors[idx] = json.dumps(vector, separators=(",", ":"))

        cid = self._normalize_category_id(category_id)
        self._conn.execute(
            "DELETE FROM rag_chunks WHERE source_type=? AND source_name=?",
            (source_type, source_name),
        )
        now = self._now()
        for idx, chunk in enumerate(chunks):
            self._conn.execute(
                """INSERT INTO rag_chunks
                     (source_type, source_name, chunk_index, content, embedding,
                      embedding_signature, content_hash, category_id, enabled, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (source_type, source_name, idx, chunk, serialized_vectors[idx], signature,
                 hashes[idx], cid, enabled, now),
            )
        self._conn.commit()
        self._rebuild_fts("rag_chunks")
        return {"chunks": len(chunks), "embedded": len(stale_indexes)}
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
                      category_id, enabled, updated_at
               FROM rag_chunks
               ORDER BY updated_at DESC LIMIT ?""",
            (limit,),
        ))

    def _vector_rank_records(
        self,
        query: str,
        records: list[dict],
        entity_type: str,
        q_vec: list[float],
        limit: int,
        min_score: float = 0.0,
    ) -> list[dict]:
        if not records:
            return []
        texts = [self._structured_text(entity_type, record) for record in records]
        embeddings = self._load_structured_embeddings(
            entity_type, records, _embedding_signature()
        )

        vec_ranked = []
        for record in records:
            embedding = embeddings.get(int(record["id"]))
            if embedding:
                vec_ranked.append((_cosine(q_vec, embedding), record))
        vec_ranked.sort(key=lambda item: -item[0])
        vec_list = [
            dict(record, vector_score=round(score, 4))
            for score, record in vec_ranked[:limit * 3]
            if score >= MIN_STRUCTURED_VECTOR_SCORE
        ]

        lex_ranked = []
        for record, text in zip(records, texts):
            score = _text_match_score(query, text)
            lex_ranked.append((score, record))
        lex_ranked.sort(key=lambda item: -item[0])
        lex_list = [
            dict(record, lexical_score=round(score, 4))
            for score, record in lex_ranked[:limit * 3]
            if score >= MIN_STRUCTURED_LEXICAL_SCORE
        ]

        fused = _rrf_fuse([vec_list, lex_list], id_key="id", limit=limit)
        return [row for row in fused if row.get("rrf_score", 0) >= min_score]
    def _search_chunks(
        self,
        question: str,
        limit: int = 5,
        min_score: float = MIN_CHUNK_SCORE,
        q_vec: list[float] | None = None,
    ) -> list[dict]:
        q = question.strip()
        q_jieba = _jieba_tokenize(q)
        fts_rows: list[dict] = []
        try:
            fts_rows = self._rows(self._conn.execute(
                """SELECT c.id, c.source_type, c.source_name, c.chunk_index,
                          c.content, c.embedding, c.embedding_signature, 1.0 AS keyword_score
                   FROM rag_chunks c
                   JOIN rag_chunks_fts ON rag_chunks_fts.rowid = c.id
                   JOIN knowledge_categories kc ON kc.id=c.category_id
                   WHERE rag_chunks_fts MATCH ? AND c.enabled=1 AND kc.enabled=1
                   ORDER BY rank LIMIT ?""",
                (q_jieba, limit * 4),
            ))
        except sqlite3.OperationalError as e:
            log.debug("[knowledge_base] FTS 搜索失败，回退到 LIKE 查询: %s", e)
            like = f"%{q}%"
            fts_rows = self._rows(self._conn.execute(
                """SELECT id, source_type, source_name, chunk_index, content,
                          embedding, embedding_signature, 0.6 AS keyword_score
                   FROM rag_chunks
                   WHERE enabled=1 AND category_id IN (SELECT id FROM knowledge_categories WHERE enabled=1)
                     AND (source_name LIKE ? OR content LIKE ?)
                   LIMIT ?""",
                (like, like, limit * 4),
            ))

        all_rows = self._rows(self._conn.execute(
            """SELECT id, source_type, source_name, chunk_index, content, embedding,
                      embedding_signature, 0.0 AS keyword_score
               FROM rag_chunks
               WHERE enabled=1 AND category_id IN (SELECT id FROM knowledge_categories WHERE enabled=1)"""
        ))
        by_id = {r["id"]: r for r in all_rows}
        for r in fts_rows:
            by_id[r["id"]] = r

        q_vec = q_vec or _embed_query(q)
        # Channel 1: vector similarity
        vec_ranked: list[tuple[float, dict]] = []
        # Channel 2: lexical score
        lex_ranked: list[tuple[float, dict]] = []
        # Channel 3: FTS keyword score (BM25 via SQLite FTS5 rank)
        kw_ranked: list[tuple[float, dict]] = []

        fts_ids = {r["id"] for r in fts_rows}

        for row in by_id.values():
            try:
                emb = (
                    json.loads(row.get("embedding") or "[]")
                    if row.get("embedding_signature") == _embedding_signature()
                    else []
                )
            except json.JSONDecodeError as e:
                log.debug("[knowledge_base] embedding JSON 解析失败: %s", e)
                emb = []
            vector_score = _cosine(q_vec, emb)
            lexical_score = _text_match_score(
                q,
                f"{row.get('source_name', '')}\n{row.get('content', '')}",
            )
            keyword_score = 1.0 if row["id"] in fts_ids else 0.0
            clean = {
                k: v for k, v in row.items()
                if k not in {"embedding", "embedding_signature", "keyword_score"}
            }
            clean["vector_score"] = round(vector_score, 4)
            clean["lexical_score"] = round(lexical_score, 4)
            clean["keyword_score"] = keyword_score
            vec_ranked.append((vector_score, clean))
            lex_ranked.append((lexical_score, clean))
            kw_ranked.append((keyword_score, clean))

        vec_ranked.sort(key=lambda item: item[0], reverse=True)
        lex_ranked.sort(key=lambda item: item[0], reverse=True)
        kw_ranked.sort(key=lambda item: item[0], reverse=True)

        vec_list = [r for _, r in vec_ranked[:limit * 3]]
        lex_list = [r for _, r in lex_ranked[:limit * 3]]
        kw_list = [r for _, r in kw_ranked[:limit * 3]]

        fused = _rrf_fuse([vec_list, lex_list, kw_list], id_key="id", limit=limit)

        # Filter by minimum RRF score
        result = [r for r in fused if r.get("rrf_score", 0) >= min_score]
        return result

    # ── search (only enabled records) ─────────────────────────────────────────

    def search(self, question: str, limit: int = 5) -> dict[str, list[dict]]:
        """Hybrid RAG search with a global Top-K cap across all result types."""
        q = question.strip()
        limit = max(1, min(int(limit or 5), 10))

        q_vec = _embed_query(q)

        # Vector fallback for structured records.  This complements SQLite FTS,
        # especially for Chinese wording variations where tokenization is weak.
        all_metrics = self._rows(self._conn.execute(
            "SELECT * FROM metrics WHERE enabled=1 AND category_id IN (SELECT id FROM knowledge_categories WHERE enabled=1)"
        ))
        all_rules = self._rows(self._conn.execute(
            "SELECT * FROM business_rules WHERE enabled=1 AND category_id IN (SELECT id FROM knowledge_categories WHERE enabled=1)"
        ))
        all_notes = self._rows(self._conn.execute(
            "SELECT * FROM context_notes WHERE enabled=1 AND category_id IN (SELECT id FROM knowledge_categories WHERE enabled=1)"
        ))

        metric_rows = self._vector_rank_records(
            q,
            all_metrics,
            "metric",
            q_vec,
            limit,
            min_score=MIN_STRUCTURED_SCORE,
        )

        note_rows = self._vector_rank_records(
            q,
            all_notes,
            "note",
            q_vec,
            limit,
            min_score=MIN_STRUCTURED_SCORE,
        )

        rule_rows = self._vector_rank_records(
            q,
            all_rules,
            "rule",
            q_vec,
            limit,
            min_score=MIN_STRUCTURED_SCORE,
        )

        chunk_rows = self._search_chunks(q, limit=limit, min_score=MIN_CHUNK_SCORE, q_vec=q_vec)

        ranked: list[tuple[float, str, dict]] = []
        for kind, rows in (
            ("metrics", metric_rows),
            ("rules", rule_rows),
            ("notes", note_rows),
            ("documents", chunk_rows),
        ):
            for row in rows:
                # RRF is rank-based and comparable across result types, so it
                # (not raw cosine) must drive the global Top-K merge.
                score = float(row.get("rrf_score", row.get("vector_score", 0.0)) or 0.0)
                ranked.append((score, kind, row))
        ranked.sort(key=lambda item: item[0], reverse=True)

        result: dict[str, list[dict]] = {
            "metrics": [], "rules": [], "notes": [], "documents": [],
        }
        for _, kind, row in ranked[:limit]:
            result[kind].append(row)
        return result

    # ── bulk insert ───────────────────────────────────────────────────────────

    def bulk_insert(self, records: list[dict], category_id: int | str | None = None) -> dict[str, int]:
        counts: dict[str, int] = {"metrics": 0, "rules": 0, "notes": 0}
        # Defer FTS rebuild: add_metric/add_note would otherwise rebuild the
        # full-text index once per record (O(N) rebuilds -> O(N^2) work).
        for rec in records:
            table = rec.get("table", "")
            if table == "metrics":
                self.add_metric(
                    name=rec.get("name", ""),
                    alias=rec.get("alias", ""),
                    definition=rec.get("definition", ""),
                    sql_template=rec.get("sql_template", ""),
                    notes=rec.get("notes", ""),
                    category_id=rec.get("category_id") or category_id,
                    cache_embedding=False,
                    rebuild_fts=False,
                )
                counts["metrics"] += 1
            elif table == "business_rules":
                self.add_rule(
                    rule_id=rec.get("rule_id", ""),
                    description=rec.get("description", ""),
                    condition=rec.get("condition", ""),
                    severity=rec.get("severity", "warning"),
                    category_id=rec.get("category_id") or category_id,
                    cache_embedding=False,
                )
                counts["rules"] += 1
            elif table == "context_notes":
                self.add_note(
                    topic=rec.get("topic", ""),
                    content=rec.get("content", ""),
                    tags=rec.get("tags", ""),
                    category_id=rec.get("category_id") or category_id,
                    cache_embedding=False,
                    rebuild_fts=False,
                )
                counts["notes"] += 1
        if sum(counts.values()):
            if counts["metrics"]:
                self._rebuild_fts("metrics")
            if counts["notes"]:
                self._rebuild_fts("context_notes")
            self.rebuild_structured_embeddings()
        return counts

    def rebuild_all_embeddings(self) -> dict:
        """Incrementally rebuild document and structured vectors."""
        signature = _embedding_signature()
        rows = self._rows(self._conn.execute(
            """SELECT id, content, embedding, embedding_signature, content_hash
               FROM rag_chunks WHERE content IS NOT NULL"""
        ))
        stale = []
        for row in rows:
            content = str(row.get("content") or "")
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            valid_vector = False
            try:
                valid_vector = bool(json.loads(row.get("embedding") or "[]"))
            except (TypeError, json.JSONDecodeError):
                pass
            if (row.get("embedding_signature") != signature
                    or row.get("content_hash") != content_hash
                    or not valid_vector):
                stale.append((row, content_hash))
        if stale:
            vectors = _embed_batch([item[0]["content"] for item in stale])
            now = self._now()
            for (row, content_hash), vector in zip(stale, vectors):
                self._conn.execute(
                    """UPDATE rag_chunks
                       SET embedding=?, embedding_signature=?, content_hash=?, updated_at=?
                       WHERE id=?""",
                    (json.dumps(vector, separators=(",", ":")), signature,
                     content_hash, now, row["id"]),
                )
            self._conn.commit()
        structured = self.rebuild_structured_embeddings()
        return {
            "rebuilt": len(stale) + structured,
            "document_chunks": len(stale),
            "structured_records": structured,
        }
