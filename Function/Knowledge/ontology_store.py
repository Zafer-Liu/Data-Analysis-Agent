"""Versioned ontology storage and relation-aware retrieval in the user's KB."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import time
import uuid

from .ontology_parser import validate_ontology
from .ontology_semantics import (
    PROJECTION_VERSION, build_nodes, choose_seeds, expand_dependencies, name_matches,
)

log = logging.getLogger(__name__)


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def init_ontology_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ontologies (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            version TEXT NOT NULL,
            category_id INTEGER NOT NULL,
            filename TEXT NOT NULL UNIQUE,
            source_name TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            binding TEXT NOT NULL,
            document TEXT NOT NULL,
            summary TEXT NOT NULL,
            warnings TEXT NOT NULL,
            projection_version INTEGER NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ontology_nodes (
            ontology_id TEXT NOT NULL,
            path TEXT NOT NULL,
            kind TEXT NOT NULL,
            label TEXT NOT NULL,
            content TEXT NOT NULL,
            payload TEXT NOT NULL,
            embedding TEXT NOT NULL DEFAULT '[]',
            signature TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (ontology_id, path)
        );
        CREATE INDEX IF NOT EXISTS ontology_category_idx ON ontologies(category_id);
    """)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(ontologies)")}
    if "projection_version" not in columns:
        conn.execute("ALTER TABLE ontologies ADD COLUMN projection_version INTEGER NOT NULL DEFAULT 0")
    for field in ("revision_id", "content_hash", "binding_hash"):
        if field not in columns:
            conn.execute(f"ALTER TABLE ontologies ADD COLUMN {field} TEXT NOT NULL DEFAULT ''")
    conn.execute("""CREATE TABLE IF NOT EXISTS ontology_revisions (
        ontology_id TEXT NOT NULL, revision_id TEXT PRIMARY KEY, content_hash TEXT NOT NULL,
        binding_hash TEXT NOT NULL, snapshot_hash TEXT NOT NULL, snapshot TEXT NOT NULL,
        created_at REAL NOT NULL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS ontology_history_idx ON ontology_revisions(ontology_id,created_at)")
    store = OntologyStore(conn)
    for row in list(conn.execute("SELECT id FROM ontologies WHERE revision_id=''")):
        store._record_revision(row[0])


def source_identity(source) -> str:
    """Use physical identity, never a transient srcN prefix or a display name."""
    handle = getattr(source, "handle", None)
    manifest = getattr(handle, "manifest", None)
    if isinstance(manifest, dict) and manifest.get("dataset_id") and manifest.get("owner_id"):
        # A version changes data, not the stable identity used by field bindings.
        identity = [type(source).__name__, manifest["owner_id"], manifest.get("workspace_id", ""), manifest["dataset_id"]]
        return hashlib.sha256(_json(identity).encode("utf-8")).hexdigest()
    config = getattr(source, "config", None)
    connection_id = str(getattr(source, "connection_id", "") or "")
    if connection_id and isinstance(config, dict) and config.get("kind") in {"trino", "hive", "spark_sql"}:
        scope = getattr(getattr(source, "store", None), "scope", ())
        owner = scope[0] if isinstance(scope, tuple) and scope else ""
        identity = [type(source).__name__, owner, connection_id,
                    *[config.get(key, "") for key in ("host", "port", "catalog", "schema", "username")]]
        return hashlib.sha256(_json(identity).encode("utf-8")).hexdigest()
    engine = getattr(source, "_engine", None)
    url = getattr(engine, "url", None)
    if url is not None:
        database = str(url.database or "")
        if str(url.drivername).startswith("sqlite") and database in ("", ":memory:"):
            return ""
        identity = [type(source).__name__, url.drivername, url.host, url.port,
                    database, url.username]
    else:
        path = getattr(source, "_db_path", None)
        if not path or str(path) == ":memory:":
            return ""  # Ambiguous sources require an explicit rebind on reconnect.
        from pathlib import Path
        identity = [type(source).__name__, str(Path(path).resolve())]
    return hashlib.sha256(_json(identity).encode("utf-8")).hexdigest()


def source_descriptors(entries, session_id: str, *, catalog: bool = False) -> list[dict]:
    result = []
    for index, entry in enumerate(entries, 1):
        source = entry["source"]
        try:
            if catalog:
                preview = source.get_preview()
                tables = {}
                for row in preview:
                    name = str(row["name"])
                    if name.startswith("[分析表] ") and name[6:] in getattr(source, "_cache_tables", set()):
                        name = name[6:]
                    tables[name] = list(row.get("columns") or [])
            else:
                tables = {str(name): [] for name in source.list_tables()}
        except Exception:
            log.warning("[ontology] source metadata unavailable", exc_info=True)
            tables = {}
        try:
            identity = source_identity(source)
        except (AttributeError, TypeError, ValueError):
            identity = ""
            log.debug("[ontology] source has no stable identity", exc_info=True)
        result.append({
            "source_id": str(entry["id"]), "session_id": str(session_id),
            "source_key": identity,
            "source_name": str(getattr(source, "name", "数据源")),
            "index": index, "tables": tables,
        })
    return result


def physical_tables(document: dict) -> dict[str, set[str]]:
    tables: dict[str, set[str]] = {}
    for entity in document.get("entities", {}).values():
        source = entity.get("source") or {}
        name = source.get("table") or source.get("view")
        if name:
            columns = tables.setdefault(name, set())
            columns.update(entity.get("properties", {}))
            if source.get("primary_key") and re.fullmatch(r"\w+", source["primary_key"]):
                columns.add(source["primary_key"])
    for relation in document.get("relations", []):
        join = relation.get("join") or {}
        for side in ("from", "to"):
            if join.get(f"{side}_table"):
                tables.setdefault(join[f"{side}_table"], set()).add(join[f"{side}_column"])
        if join.get("via_table"):
            tables.setdefault(join["via_table"], set()).update(
                [join["via_from_column"], join["via_to_column"]]
            )
    for logical, surface in document.get("query_surface", {}).items():
        name = (surface.get("source_view") or surface.get("source_table")
                or surface.get("table") or surface.get("view"))
        tables.setdefault(logical, set()).update(surface.get("columns", []))
        if name:
            tables.setdefault(name, set()).update(surface.get("columns", []))
    return tables


def table_requirements(document: dict) -> list[dict]:
    surfaces = document.get("query_surface", {})
    return [{"name": name, "kind": "query_surface" if name in surfaces else "table",
             "columns": sorted(columns), "declared_source": (
                 surfaces.get(name, {}).get("source_view") or surfaces.get(name, {}).get("source_table")
                 or surfaces.get(name, {}).get("table") or surfaces.get(name, {}).get("view") or "")}
            for name, columns in sorted(physical_tables(document).items())]


def effective_mapping(document: dict, binding: dict, actual: dict) -> dict[str, str]:
    declared = binding.get("mapping", {})
    result = {name: target for name, target in declared.items() if target and target in actual}
    for name, surface in document.get("query_surface", {}).items():
        if name in declared:
            continue  # An explicitly unmapped object must stay unmapped.
        physical = (surface.get("source_view") or surface.get("source_table")
                    or surface.get("table") or surface.get("view"))
        target = name if name in actual else result.get(physical, physical)
        if target and target in actual:
            result[name] = target
    return result


def validate_binding(document: dict, descriptor: dict | None, mapping=None,
                     column_mapping=None) -> tuple[dict, list[dict]]:
    if mapping is not None and (not isinstance(mapping, dict) or
                                any(not isinstance(k, str) or not isinstance(v, str)
                                    for k, v in mapping.items())):
        raise ValueError("表映射必须是名称到名称的对象")
    if column_mapping is not None and (not isinstance(column_mapping, dict) or any(
        not isinstance(table, str) or not isinstance(fields, dict)
        or any(not isinstance(key, str) or not isinstance(value, str) for key, value in fields.items())
        for table, fields in column_mapping.items()
    )):
        raise ValueError("字段映射必须是按本体表名分组的名称映射")
    expected = physical_tables(document)
    unknown = (set(mapping or {}) | set(column_mapping or {})) - set(expected)
    if unknown:
        raise ValueError(f"未知的本体表映射：{', '.join(sorted(unknown))}")
    for table, fields in (column_mapping or {}).items():
        if set(fields) - expected[table]:
            raise ValueError(f"{table} 包含未声明的字段映射")
    if descriptor is None:
        return {}, [{"path": "binding", "message": "尚未绑定数据源，保存后不会参与对话检索。"}]
    resolved, fields_resolved, warnings = {}, {}, []
    actual = descriptor["tables"]
    surfaces = document.get("query_surface", {})
    for name, columns in expected.items():
        surface = surfaces.get(name, {})
        physical = (surface.get("source_view") or surface.get("source_table")
                    or surface.get("table") or surface.get("view"))
        fallback = name if name in actual else (mapping or {}).get(physical, physical or name)
        target = (mapping or {}).get(name, fallback)
        resolved[name] = target if target in actual else ""
        if not resolved[name]:
            warnings.append({"path": f"binding.tables.{name}", "message": f"表/查询对象 {name} 待映射。"})
            continue
        missing, fields_resolved[name] = [], {}
        supplied = (column_mapping or {}).get(name, {})
        for column in sorted(columns):
            destination = supplied.get(column, column)
            if destination and destination in actual[target]:
                fields_resolved[name][column] = destination
            else:
                if destination and column in supplied and actual[target]:
                    raise ValueError(f"{name}.{column} 映射的字段 {destination} 不存在于 {target}")
                fields_resolved[name][column] = ""
                missing.append(column)
        if not actual[target]:
            warnings.append({"path": f"binding.tables.{name}", "message": f"{target} 的字段信息不可用，请核对实际结构。"})
        elif missing:
            warnings.append({"path": f"binding.tables.{name}", "message":
                             f"{target} 未映射字段：{', '.join(missing)}。"})
        if name in surfaces:
            warnings.append({"path": f"binding.query_surface.{name}", "message":
                             f"{name} 已映射到 {target}；映射不代表已实施权限过滤。"})
    binding = {key: descriptor[key] for key in ("source_id", "session_id", "source_key", "source_name")}
    binding.update(mapping=resolved, column_mapping=fields_resolved)
    return binding, warnings


def _nodes(document: dict) -> list[dict]:
    return build_nodes(document)


class OntologyStore:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def list(self, category_id=None, *, include_document=False, enabled_only=False) -> list[dict]:
        sql = """SELECT o.*, c.name AS category_name FROM ontologies o
                 JOIN knowledge_categories c ON c.id=o.category_id WHERE 1=1"""
        args = []
        if category_id is not None:
            sql += " AND o.category_id=?"
            args.append(int(category_id))
        if enabled_only:
            sql += " AND o.enabled=1 AND c.enabled=1"
        rows = self.conn.execute(sql + " ORDER BY o.updated_at DESC", args)
        return [self._decode(dict(row), include_document) for row in rows]

    @staticmethod
    def _decode(row: dict, include_document=True) -> dict:
        for field in ("binding", "document", "summary", "warnings"):
            row[field] = json.loads(row[field])
        if not include_document:
            row.pop("document", None)
        row["security_status"] = "advisory_only"
        return row

    def get(self, oid: str, *, revision_id: str = "") -> dict | None:
        if revision_id:
            row = self.conn.execute("SELECT * FROM ontology_revisions WHERE ontology_id=? AND revision_id=?",
                                    (oid, revision_id)).fetchone()
            if row is None:
                return None
            from .definition_history import content_hash
            snapshot = json.loads(row["snapshot"])
            if (content_hash(snapshot) != row["snapshot_hash"]
                    or content_hash(snapshot["document"]) != row["content_hash"]
                    or content_hash(snapshot["binding"]) != row["binding_hash"]):
                raise ValueError("ontology revision failed hash verification")
            return {**snapshot, "revision_id": row["revision_id"], "content_hash": row["content_hash"],
                    "binding_hash": row["binding_hash"], "security_status": "advisory_only"}
        row = self.conn.execute("SELECT * FROM ontologies WHERE id=?", (oid,)).fetchone()
        return self._decode(dict(row)) if row else None

    def _record_revision(self, oid: str) -> None:
        from .definition_history import content_hash, canonical_json
        record = self.get(oid)
        if record is None:
            return
        previous = record.pop("revision_id", "")
        for field in ("content_hash", "binding_hash", "updated_at", "projection_version", "security_status", "summary", "warnings"):
            record.pop(field, None)
        category = self.conn.execute("SELECT name,enabled FROM knowledge_categories WHERE id=?",
                                     (record["category_id"],)).fetchone()
        record["business_namespace"] = category[0] if category else ""
        record["namespace_enabled"] = bool(category[1]) if category else False
        digest, binding_digest = content_hash(record["document"]), content_hash(record["binding"])
        snapshot_digest = content_hash(record)
        old = self.conn.execute("SELECT snapshot_hash FROM ontology_revisions WHERE revision_id=? AND ontology_id=?",
                                (previous, oid)).fetchone()
        revision = previous if old and old[0] == snapshot_digest else "or_" + uuid.uuid4().hex
        if revision != previous:
            self.conn.execute("INSERT INTO ontology_revisions VALUES(?,?,?,?,?,?,?)",
                              (oid, revision, digest, binding_digest, snapshot_digest, canonical_json(record), time.time()))
        self.conn.execute("UPDATE ontologies SET revision_id=?,content_hash=?,binding_hash=? WHERE id=?",
                          (revision, digest, binding_digest, oid))

    def revisions(self, oid: str, *, offset=0, limit=100) -> list[dict]:
        return [dict(row) for row in self.conn.execute(
            "SELECT revision_id,content_hash,binding_hash,created_at FROM ontology_revisions "
            "WHERE ontology_id=? ORDER BY created_at,revision_id LIMIT ? OFFSET ?",
            (oid, max(1, min(100, int(limit))), max(0, int(offset))))]

    def prune_history(self, before: float, *, commit=True) -> int:
        deleted = self.conn.execute(
            "DELETE FROM ontology_revisions WHERE created_at<? AND revision_id NOT IN (SELECT revision_id FROM ontologies)",
            (float(before),)).rowcount
        if commit:
            self.conn.commit()
        return deleted

    def save(self, parsed: dict, *, filename: str, source_name: str, category_id: int,
             binding: dict, ontology_id: str = "", enabled: bool = True,
             extra_warnings=(), commit=True, embed=True, _stable_id: str = "") -> dict:
        if not ontology_id:
            existing = self.conn.execute("SELECT id FROM ontologies WHERE filename=?", (filename,)).fetchone()
            ontology_id = existing["id"] if existing else ""
        old = self.get(ontology_id) if ontology_id else None
        if ontology_id and old is None:
            raise ValueError("要替换的本体不存在")
        if old is None and self.conn.execute("SELECT COUNT(*) FROM ontologies").fetchone()[0] >= 100:
            raise ValueError("最多保存 100 个本体，请替换已有版本或删除不用的本体")
        collision = self.conn.execute("SELECT id FROM ontologies WHERE filename=?", (filename,)).fetchone()
        if collision and (old is None or collision["id"] != old["id"]):
            raise ValueError("该源文件已关联其他本体，请重新上传")
        oid = old["id"] if old else (_stable_id or uuid.uuid4().hex)
        document = parsed["ontology"]
        nodes = _nodes(document)
        vectors, signature = [[] for _ in nodes], ""
        if nodes and embed:
            try:
                from .knowledge_base import _embed_batch, _embedding_signature
                vectors = _embed_batch([node["content"] for node in nodes])
                if len(vectors) != len(nodes):
                    raise ValueError("Incomplete ontology embedding batch")
                signature = _embedding_signature()
            except Exception:
                vectors, signature = [[] for _ in nodes], ""
                log.warning("[ontology] embeddings unavailable; lexical search retained", exc_info=True)
        try:
            self.conn.execute("""INSERT INTO ontologies
                (id,name,version,category_id,filename,source_name,enabled,binding,document,summary,warnings,updated_at,projection_version)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,version=excluded.version,category_id=excluded.category_id,
                filename=excluded.filename,source_name=excluded.source_name,enabled=excluded.enabled,
                binding=excluded.binding,document=excluded.document,summary=excluded.summary,
                warnings=excluded.warnings,updated_at=excluded.updated_at,projection_version=excluded.projection_version""",
                (oid, document["domain"]["name"], str(document.get("version", "1.0.0")),
                 int(category_id), filename, source_name, int(enabled), _json(binding), _json(document),
                 _json(parsed["summary"]), _json([*parsed["warnings"], *extra_warnings]), time.time(), PROJECTION_VERSION))
            self.conn.execute("DELETE FROM ontology_nodes WHERE ontology_id=?", (oid,))
            self.conn.executemany("""INSERT INTO ontology_nodes
                (ontology_id,path,kind,label,content,payload,embedding,signature) VALUES (?,?,?,?,?,?,?,?)""",
                [(oid, node["path"], node["kind"], node["label"], node["content"],
                  _json({"value": node["payload"], "names": node["names"]}),
                  _json(vectors[index]), signature) for index, node in enumerate(nodes)])
            self._record_revision(oid)
            if commit:
                self.conn.commit()
        except Exception:
            if commit:
                self.conn.rollback()
            raise
        return self.get(oid)

    def update(self, oid: str, *, binding=None, enabled=None, warnings=None, filename=None) -> dict:
        record = self.get(oid)
        if not record:
            raise LookupError("本体不存在")
        self.conn.execute("UPDATE ontologies SET binding=?, enabled=?, warnings=?, filename=?, updated_at=? WHERE id=?",
                          (_json(record["binding"] if binding is None else binding),
                           record["enabled"] if enabled is None else int(enabled),
                           _json(record["warnings"] if warnings is None else warnings),
                           record["filename"] if filename is None else filename, time.time(), oid))
        self._record_revision(oid)
        self.conn.commit()
        return self.get(oid)

    def delete(self, oid: str, *, commit=True) -> bool:
        self.conn.execute("DELETE FROM ontology_nodes WHERE ontology_id=?", (oid,))
        deleted = self.conn.execute("DELETE FROM ontologies WHERE id=?", (oid,)).rowcount > 0
        if commit:
            self.conn.commit()
        return deleted

    def export(self) -> list[dict]:
        return self.list(include_document=True)

    def prepare_snapshot(self, snapshot: dict) -> list[tuple[dict, dict]]:
        rows = snapshot.get("ontologies") or []
        if not isinstance(rows, list) or len(rows) > 100:
            raise ValueError("本体快照格式无效或数量超过 100")
        prepared, seen = [], set()
        for item in rows:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise ValueError("本体快照缺少稳定 ID")
            oid = item["id"]
            if not re.fullmatch(r"[a-f0-9]{32}", oid) or oid in seen:
                raise ValueError("本体快照 ID 无效或重复")
            seen.add(oid)
            binding = item.get("binding") or {}
            if not isinstance(binding, dict) or not isinstance(binding.get("mapping", {}), dict):
                raise ValueError("本体绑定快照格式无效")
            if any(not isinstance(k, str) or not isinstance(v, str)
                   for k, v in binding.get("mapping", {}).items()):
                raise ValueError("本体表映射快照格式无效")
            parsed = validate_ontology(item.get("document"))
            validate_binding(parsed["ontology"], None, binding.get("mapping"), binding.get("column_mapping"))
            if any(not isinstance(binding.get(key, ""), str) or len(binding.get(key, "")) > 512
                   for key in ("source_id", "session_id", "source_key", "source_name")):
                raise ValueError("本体数据源标识快照格式无效")
            if not isinstance(item.get("enabled", True), (bool, int)) or item.get("enabled", True) not in (0, 1):
                raise ValueError("本体启用状态快照格式无效")
            prepared.append((item, parsed))
        return prepared

    def restore(self, prepared, category_ids: dict[str, int]) -> int:
        self.conn.execute("DELETE FROM ontology_nodes")
        self.conn.execute("DELETE FROM ontologies")
        fallback = next(iter(category_ids.values()))
        for item, parsed in prepared:
            # Save under the stable ID without depending on transient filenames.
            record = self.save(parsed, filename=f"{item['id']}.yaml",
                               source_name=str(item.get("source_name") or "ontology.yaml"),
                               category_id=category_ids.get(item.get("category_name", ""), fallback),
                               binding=item.get("binding") or {}, enabled=bool(item.get("enabled", True)),
                               commit=False, embed=False, _stable_id=item["id"])
        return len(prepared)

    def rebuild_embeddings(self) -> int:
        from .knowledge_base import _embedding_signature, _embed_batch
        signature = _embedding_signature()
        rows = [dict(row) for row in self.conn.execute(
            "SELECT ontology_id,path,content FROM ontology_nodes WHERE signature<>? OR embedding='[]'",
            (signature,),
        )]
        if not rows:
            return 0
        vectors = _embed_batch([row["content"] for row in rows])
        if len(vectors) != len(rows):
            raise ValueError("Incomplete ontology embedding batch")
        self.conn.executemany(
            "UPDATE ontology_nodes SET embedding=?,signature=? WHERE ontology_id=? AND path=?",
            [(_json(vector), signature, row["ontology_id"], row["path"])
             for row, vector in zip(rows, vectors)],
        )
        self.conn.commit()
        return len(rows)

    @staticmethod
    def _bound_source(binding: dict, sources: list[dict]) -> dict | None:
        exact = next((source for source in sources
                      if binding.get("session_id") == source["session_id"]
                      and binding.get("source_id") == source["source_id"]), None)
        if exact and (not binding.get("source_key") or binding["source_key"] == exact.get("source_key")):
            return exact
        matches = [source for source in sources if binding.get("source_key")
                   and binding["source_key"] == source.get("source_key")]
        return matches[0] if len(matches) == 1 else None

    def _ensure_projection(self, record: dict) -> None:
        if record.get("projection_version", 0) >= PROJECTION_VERSION:
            return
        nodes = _nodes(record["document"])
        try:
            self.conn.execute("DELETE FROM ontology_nodes WHERE ontology_id=?", (record["id"],))
            self.conn.executemany("""INSERT INTO ontology_nodes
                (ontology_id,path,kind,label,content,payload) VALUES (?,?,?,?,?,?)""",
                [(record["id"], node["path"], node["kind"], node["label"], node["content"],
                  _json({"value": node["payload"], "names": node["names"]})) for node in nodes])
            parsed = validate_ontology(record["document"])
            warnings = [*parsed["warnings"], *(item for item in record["warnings"]
                        if item["path"].startswith(("binding", "$.binding")))]
            self.conn.execute("UPDATE ontologies SET projection_version=?,summary=?,warnings=? WHERE id=?",
                              (PROJECTION_VERSION, _json(parsed["summary"]), _json(warnings), record["id"]))
            self.conn.commit()
            record.update(summary=parsed["summary"], warnings=warnings, projection_version=PROJECTION_VERSION)
        except Exception:
            self.conn.rollback()
            raise

    def definition(self, oid: str, path: str, *, revision_id: str = "",
                   offset: int = 0, limit: int = 4000) -> dict | None:
        record = self.get(oid, revision_id=revision_id)
        if not record:
            return None
        row = next((node for node in _nodes(record["document"]) if node["path"] == path), None)
        if row is None:
            return None
        from .definition_history import canonical_json, content_hash
        text = canonical_json(row["payload"])
        offset, limit = max(0, min(len(text), int(offset))), max(1, min(4000, int(limit)))
        content = text[offset:offset + limit]
        end = offset + len(content)
        return {"id": oid, "version": record["version"], "source_name": record["source_name"],
                "revision_id": record["revision_id"], "content_hash": record["content_hash"],
                "binding_hash": record["binding_hash"], "definition_hash": content_hash(row["payload"]),
                "path": path, "kind": row["kind"],
                "definition": row["payload"] if offset == 0 and end == len(text) else None,
                "content": content, "offset": offset, "total_chars": len(text),
                "next_offset": end if end < len(text) else None,
                "returned_chars": len(content), "limit": limit,
                "reference": {"ontology_id": oid, "revision_id": record["revision_id"], "path": path},
                "complete": offset == 0 and end == len(text), "security_status": "advisory_only"}

    def search(self, question: str, sources: list[dict], *, max_chars=16000) -> list[dict]:
        """Retrieve typed seeds and their complete semantic dependencies."""
        if not isinstance(question, str) or not question.strip() or not sources:
            return []
        from .knowledge_base import _text_match_score, _embed_query, _embedding_signature, _cosine
        query_vector, signature = [], ""
        results, remaining = [], max(512, min(int(max_chars), 64000))
        mentioned_ids = set(re.findall(r"\b[a-f0-9]{32}\b", question))
        for record in self.list(include_document=True, enabled_only=True):
            if mentioned_ids and record["id"] not in mentioned_ids:
                continue
            source = self._bound_source(record["binding"], sources)
            if not source:
                continue
            self._ensure_projection(record)
            rows = []
            for raw in self.conn.execute("SELECT * FROM ontology_nodes WHERE ontology_id=?", (record["id"],)):
                row = dict(raw)
                data = json.loads(row["payload"])
                row.update(payload=data["value"], names=data["names"])
                rows.append(row)
            ranked = []
            for row in rows:
                matches = name_matches(question, row["names"])
                exact = max(map(len, matches), default=0)
                lookup = row["path"].casefold() in question.casefold()
                lexical = _text_match_score(question, row["content"])
                semantic = 0.0
                if not exact and row["signature"]:
                    if not signature:
                        try:
                            signature = _embedding_signature()
                            query_vector = _embed_query(question)
                        except Exception:
                            signature, query_vector = "unavailable", []
                            log.warning("[ontology] semantic retrieval unavailable; using lexical matches", exc_info=True)
                    if query_vector and row["signature"] == signature:
                        semantic = _cosine(query_vector, json.loads(row["embedding"]))
                if lookup or exact or lexical >= 0.12 or semantic >= 0.6:
                    ranked.append((int(lookup) * 1000 + exact * 2 + lexical + semantic, row))
            ranked.sort(key=lambda item: (-item[0], item[1]["path"]))
            seeds = choose_seeds(question, ranked)
            if not seeds:
                continue
            chosen, plan = expand_dependencies(record["document"], rows, seeds, question)
            available = effective_mapping(record["document"], record["binding"], source["tables"])
            fields = {name: {key: value for key, value in columns.items() if key != value}
                      for name, columns in record["binding"].get("column_mapping", {}).items() if name in available}
            for surface in plan["query_surface_candidates"]:
                surface["mapped_table"] = available.get(surface["name"], "")
                surface["authorization_verified"] = False
            # Bound metadata as well as entries; JSON escaping counts towards the budget.
            result = {"id": record["id"], "name": record["name"], "version": record["version"],
                      "revision_id": record["revision_id"], "content_hash": record["content_hash"],
                      "binding_hash": record["binding_hash"],
                      "source_name": record["source_name"], "source_id": source["source_id"],
                      "table_prefix": f"src{source['index']}__" if len(sources) > 1 else "",
                      "mapping": available, "column_mapping": {name: value for name, value in fields.items() if value},
                      "unavailable_tables": sorted(set(physical_tables(record["document"])) - set(available)),
                      "security_status": "advisory_only", "semantic_context": plan,
                      "warnings": record["warnings"][:16], "warning_count": len(record["warnings"]),
                      "entries": [], "omitted_paths": [], "omitted_count": 0}
            required = {row["path"] for row in seeds}
            required.update(row["path"] for row in chosen if row["kind"] not in {"dimensions"}
                            or row["path"].split(".", 1)[1] in plan["requested_dimensions"])
            plan["required_paths"] = sorted(required)
            entries, omitted = [], []
            for row in chosen:
                entry = {"path": row["path"], "kind": row["kind"], "content": _json(row["payload"])}
                result["entries"] = [*entries, entry]
                if len(_json(result)) + 1200 > remaining:
                    omitted.append(row["path"])
                    result["entries"] = entries
                    continue
                entries.append(entry)
            result["entries"] = entries
            result["omitted_paths"] = omitted[:24]
            result["omitted_count"] = len(omitted)
            result["requires_followup"] = bool(required.intersection(omitted))
            cost = len(_json(result))
            if not entries or cost > remaining:
                # Return a discoverable, bounded lookup hint instead of silently dropping a match.
                result = {"id": record["id"], "name": record["name"], "version": record["version"],
                          "revision_id": record["revision_id"], "content_hash": record["content_hash"],
                          "binding_hash": record["binding_hash"],
                          "source_name": record["source_name"], "security_status": "advisory_only",
                          "entries": [], "omitted_paths": [row["path"] for row in seeds[:4]],
                          "omitted_count": len(chosen), "requires_followup": True}
                cost = len(_json(result))
                if cost > remaining:
                    continue
            results.append(result)
            remaining -= cost
            if remaining < 1200:
                break
        return results
