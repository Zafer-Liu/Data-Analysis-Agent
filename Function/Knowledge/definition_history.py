"""Scoped ordinary definitions and immutable history in the existing KB database."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import uuid

TABLES = {"metric": ("metrics", "name"), "rule": ("business_rules", "rule_id")}


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))


def content_hash(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _ddl_parts(body: str) -> list[str]:
    """Split column/constraint declarations, preserving quoted commas and defaults."""
    parts, start, depth, quote = [], 0, 0, ""
    index = 0
    while index < len(body):
        char = body[index]
        if quote:
            closing = "]" if quote == "[" else quote
            if char == closing:
                if index + 1 < len(body) and body[index + 1] == closing:
                    index += 1
                else:
                    quote = ""
        elif char in "'\"`[":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(body[start:index])
            start = index + 1
        index += 1
    parts.append(body[start:])
    return parts


def _scope_identity(conn: sqlite3.Connection, table: str, key: str) -> None:
    """Replace the global UNIQUE key in one savepoint; retain IDs and derived indexes."""
    global_indexes, scoped = [], False
    for index in conn.execute(f"PRAGMA index_list({_quote(table)})"):
        if index[2] and not index[4]:  # A partial index is a separate business constraint.
            columns = [row[2] for row in conn.execute(f"PRAGMA index_info({_quote(index[1])})")]
            if columns == [key]:
                global_indexes.append(index[1])
            elif columns in (["category_id", key], [key, "category_id"]):
                scoped = True
    if not global_indexes:
        if not scoped:
            conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {_quote(table + '_namespace_key')} "
                         f"ON {_quote(table)}(category_id,{_quote(key)})")
        return
    # DROP TABLE can cascade into extension tables when FK enforcement is on.
    # Unknown extension schemas must fail before touching either parent or child.
    if conn.execute("PRAGMA foreign_keys").fetchone()[0]:
        for row in list(conn.execute("SELECT name FROM sqlite_master WHERE type='table'")):
            if any(fk[2] == table for fk in conn.execute(f"PRAGMA foreign_key_list({_quote(row[0])})")):
                raise ValueError(f"Cannot safely migrate {table} while foreign-key extension tables are attached")
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
    opening, closing = sql.index("("), sql.rfind(")")
    identifier = rf'(?:{re.escape(key)}|"{re.escape(key)}"|`{re.escape(key)}`|\[{re.escape(key)}\])'
    declarations = []
    for part in _ddl_parts(sql[opening + 1:closing]):
        if re.fullmatch(rf'\s*(?:CONSTRAINT\s+\S+\s+)?UNIQUE\s*\(\s*{identifier}\s*\)'
                        r'(?:\s+ON\s+CONFLICT\s+\w+)?\s*', part, re.I):
            continue
        if re.match(rf'^\s*{identifier}\s+', part, re.I):
            # Quoted defaults/identifiers may themselves contain the word UNIQUE.
            part = re.sub(r"'([^']|'')*'|\"([^\"]|\"\")*\"|`([^`]|``)*`|\[[^]]*\]|"
                          r'\bUNIQUE\b(?:\s+ON\s+CONFLICT\s+\w+)?',
                          lambda match: '' if re.match(r'^UNIQUE\b', match[0], re.I) else match[0], part, flags=re.I)
        declarations.append(part)
    objects = [(row[0], row[1]) for row in conn.execute(
        "SELECT name,sql FROM sqlite_master WHERE tbl_name=? AND type IN ('index','trigger') AND sql IS NOT NULL",
        (table,),
    )]
    has_sequence = conn.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone()
    sequence = conn.execute("SELECT seq FROM sqlite_sequence WHERE name=?", (table,)).fetchone() if has_sequence else None
    temporary = table + "_scoped_migration"
    conn.execute(f"CREATE TABLE {_quote(temporary)} (" + ",".join(declarations) + ")" + sql[closing + 1:])
    for index in conn.execute(f"PRAGMA index_list({_quote(temporary)})"):
        if index[2] and [row[2] for row in conn.execute(f"PRAGMA index_info({_quote(index[1])})")] == [key]:
            raise ValueError(f"Unsupported legacy UNIQUE declaration in {table}; original data is unchanged")
    columns = ",".join(_quote(row[1]) for row in conn.execute(f"PRAGMA table_info({_quote(table)})"))
    conn.execute(f"INSERT INTO {_quote(temporary)} ({columns}) SELECT {columns} FROM {_quote(table)}")
    conn.execute(f"DROP TABLE {_quote(table)}")
    conn.execute(f"ALTER TABLE {_quote(temporary)} RENAME TO {_quote(table)}")
    for name, statement in objects:
        if name not in global_indexes:
            conn.execute(statement)
    if not scoped:
        conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {_quote(table + '_namespace_key')} ON {_quote(table)}(category_id,{_quote(key)})")
    if sequence:
        conn.execute("UPDATE sqlite_sequence SET seq=MAX(seq,?) WHERE name=?", (sequence[0], table))


def init_definition_history(conn: sqlite3.Connection) -> None:
    conn.execute("SAVEPOINT scoped_definitions")
    try:
        for table, key in TABLES.values():
            _scope_identity(conn, table, key)
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            for field in ("source_filename", "revision_id", "content_hash"):
                if field not in columns:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {field} TEXT NOT NULL DEFAULT ''")
        conn.execute("""CREATE TABLE IF NOT EXISTS definition_revisions (
            revision_id TEXT PRIMARY KEY, kind TEXT NOT NULL, definition_id INTEGER NOT NULL,
            content_hash TEXT NOT NULL, snapshot TEXT NOT NULL, created_at REAL NOT NULL)""")
        conn.execute("CREATE INDEX IF NOT EXISTS definition_history_idx ON definition_revisions(kind,definition_id)")
        for kind, (table, _) in TABLES.items():
            for row in list(conn.execute(f"SELECT id FROM {table} WHERE revision_id=''")):
                record_definition(conn, kind, row[0])
        conn.execute("RELEASE scoped_definitions")
    except Exception:
        conn.execute("ROLLBACK TO scoped_definitions")
        conn.execute("RELEASE scoped_definitions")
        raise


def record_definition(conn: sqlite3.Connection, kind: str, definition_id: int) -> dict | None:
    table, _ = TABLES[kind]
    row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (definition_id,)).fetchone()
    if row is None:
        return None
    snapshot = dict(row)
    previous = snapshot.pop("revision_id", "")
    snapshot.pop("content_hash", None)
    snapshot.pop("updated_at", None)
    category = conn.execute("SELECT name,enabled FROM knowledge_categories WHERE id=?", (snapshot["category_id"],)).fetchone()
    snapshot["business_namespace"] = category[0] if category else ""
    snapshot["namespace_enabled"] = bool(category[1]) if category else False
    snapshot["source_status"] = "linked" if snapshot.get("source_filename") else "unknown"
    snapshot["metric_contract_id"] = f"{kind}:{definition_id}"
    digest = content_hash(snapshot)
    old = conn.execute("SELECT content_hash FROM definition_revisions WHERE revision_id=?", (previous,)).fetchone()
    revision = previous if old and old[0] == digest else "dr_" + uuid.uuid4().hex
    if revision != previous:
        conn.execute("INSERT INTO definition_revisions VALUES(?,?,?,?,?,?)",
                     (revision, kind, definition_id, digest, canonical_json(snapshot), time.time()))
    conn.execute(f"UPDATE {table} SET revision_id=?,content_hash=? WHERE id=?", (revision, digest, definition_id))
    return {**snapshot, "revision_id": revision, "content_hash": digest}


def get_definition_revision(conn: sqlite3.Connection, kind: str, definition_id: int, revision_id: str) -> dict | None:
    if kind not in TABLES:
        raise ValueError("unknown definition kind")
    row = conn.execute("SELECT * FROM definition_revisions WHERE kind=? AND definition_id=? AND revision_id=?",
                       (kind, definition_id, revision_id)).fetchone()
    if row is None:
        return None
    value = json.loads(row["snapshot"])
    if content_hash(value) != row["content_hash"]:
        raise ValueError("definition revision failed hash verification")
    return {**value, "revision_id": row["revision_id"], "content_hash": row["content_hash"]}


def definition_revisions(conn: sqlite3.Connection, kind: str, definition_id: int, *, offset=0, limit=100) -> list[dict]:
    if kind not in TABLES:
        raise ValueError("unknown definition kind")
    return [dict(row) for row in conn.execute(
        "SELECT revision_id,content_hash,created_at FROM definition_revisions "
        "WHERE kind=? AND definition_id=? ORDER BY created_at,revision_id LIMIT ? OFFSET ?",
        (kind, definition_id, max(1, min(100, int(limit))), max(0, int(offset))),
    )]


def prune_definition_history(conn: sqlite3.Connection, before: float) -> int:
    """Apply the owner's retention cutoff while keeping every live revision."""
    live = " UNION ".join(f"SELECT revision_id FROM {table}" for table, _ in TABLES.values())
    return conn.execute(f"DELETE FROM definition_revisions WHERE created_at<? AND revision_id NOT IN ({live})",
                        (float(before),)).rowcount
