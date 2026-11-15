# -*- coding: utf-8 -*-
"""Blueprint: business knowledge base — parse, preview, confirm, CRUD, toggle."""
import logging
import uuid
from contextlib import contextmanager
from pathlib import Path

from flask import Blueprint, request, jsonify, send_from_directory

from .state import session_manager, config_manager, require_session_ownership
from infrastructure.paths import content_data_path

log = logging.getLogger(__name__)
bp = Blueprint("knowledge", __name__)

# Source mode: <project>/uploads/knowledge; packaged mode: <data-root>/uploads/knowledge.
# This file lives at <root>/api/knowledge.py → parent = api/ → parent = root
_KB_DIR = content_data_path("uploads", "knowledge")
_YAML_EXTS = {".yaml", ".yml"}
_ALLOWED_EXTS = {".xlsx", ".xls", ".docx", *_YAML_EXTS}
_ONTOLOGY_MAX_BYTES = 256 * 1024


def _ensure_dir() -> None:
    _KB_DIR.mkdir(parents=True, exist_ok=True)


def _scope_context() -> tuple[str, str]:
    """Resolve the trusted user plus the session's stable Workspace identity."""
    import os as _os
    body = request.get_json(silent=True) if request.is_json else {}
    sid = str(
        request.args.get("session_id")
        or request.form.get("session_id")
        or (body or {}).get("session_id")
        or ""
    )
    if bool(_os.environ.get("RAILWAY_PROJECT_ID")) or _os.environ.get("VERCEL") == "1":
        # Never let a browser-supplied header/query override the authenticated
        # tenant in managed cloud mode.
        from .auth import current_user
        auth_user = current_user()
        user_id = auth_user["id"] if auth_user else ""
    else:
        user_id = str(
            request.headers.get("X-BAA-User-ID")
            or request.args.get("user_id")
            or (body or {}).get("user_id")
            or ""
        ).strip()[:200]
    if not user_id:
        user_id = "local-default"
    from data.workspace import workspace_manager
    workspace_id = str(workspace_manager.workspace_id_for_session(sid) or "")
    return workspace_id, user_id


def _kb_dir() -> Path:
    from Function.Knowledge.knowledge_base import knowledge_scope_dir
    _wid, user_id = _scope_context()
    return knowledge_scope_dir(workspace_id="", user_id=user_id)


@contextmanager
def _kb():
    """Yield a short-lived KnowledgeBase and always close its connection.

    Each API request opens its own SQLite connection; closing it here (rather
    than relying on GC) avoids WAL read-snapshot retention and file-handle
    growth under multi-tenant traffic.
    """
    from Function.Knowledge.knowledge_base import KnowledgeBase
    _wid, user_id = _scope_context()
    kb = KnowledgeBase(workspace_id="", user_id=user_id)
    try:
        yield kb
    finally:
        kb.close()


def _category_id(default: int = 1):
    body = request.get_json(silent=True) if request.is_json else {}
    raw = request.args.get("category_id") or (body or {}).get("category_id") or default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _get_client(sid: str, provider: str = ""):
    """Return (client, model_name) for the given session.

    Priority:
      1. explicit provider passed from the request (frontend model-sel value)
      2. provider stored on the session (set via POST /api/session/<sid>/model)
      3. global default provider from config
    """
    if not provider:
        sess = session_manager.get_or_create(sid)
        provider = getattr(sess, "model_provider", None) or ""
    if not provider:
        provider = config_manager.get_default_provider() or ""
    if not provider:
        raise ValueError("未配置任何 LLM 模型，请先在「模型设置」中添加模型。")
    from LLM.llm_config_manager import get_llm_client
    client = get_llm_client(provider)
    cfg = config_manager.get_config(provider)
    log.info("[knowledge] using provider=%s model=%s for LLM extraction", provider, cfg.model)
    return client, cfg.model




# ── Business categories ───────────────────────────────────────────────────────

@bp.get("/api/knowledge/categories")
def list_categories():
    with _kb() as kb:
        return jsonify(kb.list_categories())


@bp.post("/api/knowledge/categories")
def add_category():
    body = request.get_json(silent=True) or {}
    try:
        with _kb() as kb:
            return jsonify(kb.add_category(body.get("name", ""))), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@bp.put("/api/knowledge/categories/<int:cid>")
def update_category(cid: int):
    body = request.get_json(silent=True) or {}
    patch = {k: body[k] for k in ("name", "enabled") if k in body}
    with _kb() as kb:
        record = kb.update_category(cid, **patch)
    if record is None:
        return jsonify({"error": "Not found"}), 404
    return jsonify(record)


@bp.post("/api/knowledge/categories/<int:cid>/toggle")
def toggle_category(cid: int):
    with _kb() as kb:
        rec = kb.get_category_by_id(cid)
        if not rec:
            return jsonify({"error": "Not found"}), 404
        return jsonify(kb.update_category(cid, enabled=0 if rec["enabled"] else 1))


@bp.delete("/api/knowledge/categories/<int:cid>")
def delete_category(cid: int):
    try:
        with _kb() as kb:
            source_files = [row["filename"] for row in kb.ontologies.list(cid)]
            deleted = kb.delete_category(cid)
        for filename in source_files:
            (_kb_dir() / Path(filename).name).unlink(missing_ok=True)
        return jsonify({"ok": True, "deleted": deleted})
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


# ── Ontology lifecycle and source binding ─────────────────────────────────────

def _ontology_body() -> dict:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ValueError("请求正文必须是 JSON 对象")
    return body


def _ontology_sources(*, active_only: bool = False) -> list[dict]:
    from .state import check_session_ownership
    from Function.Knowledge.ontology_store import source_descriptors
    body = _ontology_body() if request.is_json else {}
    sid = str(request.args.get("session_id") or request.form.get("session_id")
              or body.get("session_id") or "")
    if not sid:
        return []
    allowed, _ = check_session_ownership(sid)
    if not allowed:
        raise PermissionError("无权访问此会话的数据源")
    sess = session_manager.get(sid)
    entries = (sess._active_entries() if active_only else list(getattr(sess, "_sources", []))) if sess else []
    return source_descriptors(entries, sid, catalog=not active_only)


def _ontology_binding(document: dict, body: dict):
    from Function.Knowledge.ontology_store import validate_binding
    source_id = str(body.get("source_id") or "")
    sources = _ontology_sources()
    source = next((item for item in sources if item["source_id"] == source_id), None)
    if source_id and source is None:
        raise ValueError("数据源已移除，请刷新后重新绑定")
    return validate_binding(document, source, body.get("mapping"), body.get("column_mapping"))


def _ontology_error(exc):
    return jsonify({"error": str(exc), "errors": getattr(exc, "errors", [])}), (
        403 if isinstance(exc, PermissionError) else 400
    )


@bp.get("/api/knowledge/ontology-sources")
def ontology_sources():
    try:
        return jsonify({"sources": _ontology_sources()})
    except (ValueError, PermissionError) as exc:
        return _ontology_error(exc)


@bp.get("/api/knowledge/ontologies")
def list_ontologies():
    with _kb() as kb:
        if request.args.get("export") == "1":
            return jsonify(kb.ontologies.export())
        return jsonify(kb.ontologies.list(_category_id()))


@bp.get("/api/knowledge/ontologies/<oid>")
def get_ontology(oid: str):
    with _kb() as kb:
        record = kb.ontologies.get(oid)
    if record is None:
        return jsonify({"error": "本体不存在"}), 404
    return jsonify(record)


@bp.get("/api/knowledge/ontologies/<oid>/definition")
def get_ontology_definition(oid: str):
    path = request.args.get("path", "")
    if not path or len(path) > 512:
        return jsonify({"error": "请提供有效的定义路径"}), 400
    try:
        offset, limit = _read_bounds()
        with _kb() as kb:
            definition = kb.ontologies.definition(oid, path, revision_id=request.args.get("revision_id", ""),
                                                  offset=offset, limit=limit)
        if definition is None:
            return jsonify({"error": "未找到该定义或修订已过保留期"}), 404
        return jsonify(definition)
    except (ValueError, TypeError) as exc:
        return _ontology_error(exc)


def _read_bounds(max_limit=4000):
    try:
        return max(0, int(request.args.get("offset", 0))), max(1, min(max_limit, int(request.args.get("limit", max_limit))))
    except (ValueError, TypeError) as exc:
        raise ValueError("offset 和 limit 必须是整数") from exc


@bp.get("/api/knowledge/ontologies/<oid>/revisions")
def ontology_revisions(oid: str):
    try:
        offset, limit = _read_bounds(100)
        with _kb() as kb:
            revisions = kb.ontologies.revisions(oid, offset=offset, limit=limit)
        return jsonify({"id": oid, "revisions": revisions, "offset": offset,
                        "next_offset": offset + len(revisions) if len(revisions) == limit else None})
    except (ValueError, TypeError) as exc:
        return _ontology_error(exc)


@bp.get("/api/knowledge/<kind>/<int:did>/revisions")
@bp.get("/api/knowledge/<kind>/<int:did>/revisions/<revision_id>")
def ordinary_definition_revisions(kind: str, did: int, revision_id: str = ""):
    from Function.Knowledge.definition_history import canonical_json
    kinds = {"metrics": "metric", "rules": "rule"}
    if kind not in kinds:
        return jsonify({"error": "Unknown definition kind"}), 404
    try:
        offset, limit = _read_bounds(4000 if revision_id else 100)
        with _kb() as kb:
            if not revision_id:
                rows = kb.definition_revisions(kinds[kind], did, offset=offset, limit=limit)
                return jsonify({"revisions": rows, "offset": offset,
                                "next_offset": offset + len(rows) if len(rows) == limit else None})
            definition = kb.definition_revision(kinds[kind], did, revision_id)
        if definition is None:
            return jsonify({"error": "未找到该修订或已过保留期"}), 404
        text = canonical_json(definition)
        offset = min(offset, len(text))
        content = text[offset:offset + limit]
        end = offset + len(content)
        return jsonify({"definition_id": did, "kind": kinds[kind], "revision_id": revision_id,
                        "content_hash": definition["content_hash"], "content": content,
                        "definition": definition if offset == 0 and end == len(text) else None,
                        "offset": offset, "returned_chars": len(content), "total_chars": len(text),
                        "next_offset": end if end < len(text) else None})
    except (ValueError, TypeError) as exc:
        return _ontology_error(exc)


@bp.post("/api/knowledge/ontologies/<oid>/preview")
def preview_ontology_retrieval(oid: str):
    try:
        body = _ontology_body()
        question = body.get("question", "")
        if not isinstance(question, str) or not question.strip() or len(question) > 4000:
            raise ValueError("请输入 1–4000 字符的检索问题")
        sources = _ontology_sources(active_only=True)
        with _kb() as kb:
            record = kb.ontologies.get(oid)
            if record is None:
                return jsonify({"error": "本体不存在"}), 404
            bound = kb.ontologies._bound_source(record["binding"], sources) is not None
            matches = kb.ontologies.search(f"{question} ontology:{oid}", sources)
        return jsonify({"matches": matches, "bound_to_active_source": bound,
                        "enabled": bool(record["enabled"]), "security_status": "advisory_only"})
    except (ValueError, PermissionError) as exc:
        return _ontology_error(exc)


@bp.get("/api/knowledge/ontologies/<oid>/source")
def download_ontology(oid: str):
    with _kb() as kb:
        record = kb.ontologies.get(oid)
    if record is None:
        return jsonify({"error": "本体不存在"}), 404
    import yaml
    from flask import Response
    from werkzeug.http import dump_options_header
    response = Response(yaml.safe_dump(record["document"], allow_unicode=True, sort_keys=False),
                        content_type="application/x-yaml; charset=utf-8")
    name = Path(record["source_name"]).name.replace("\\", "_").replace("\r", "").replace("\n", "")
    response.headers["Content-Disposition"] = dump_options_header(
        "attachment", {"filename": name if name.isascii() else "ontology.yaml"},
    )
    return response


@bp.put("/api/knowledge/ontologies/<oid>")
def update_ontology(oid: str):
    try:
        body = _ontology_body()
        with _kb() as kb:
            old = kb.ontologies.get(oid)
            if old is None:
                return jsonify({"error": "本体不存在"}), 404
            binding = warnings = None
            if any(key in body for key in ("source_id", "mapping", "column_mapping")):
                from Function.Knowledge.ontology_parser import validate_ontology
                parsed = validate_ontology(old["document"])
                binding, extra = _ontology_binding(old["document"], {**old["binding"], **body})
                warnings = [*parsed["warnings"], *extra]
            enabled = body.get("enabled")
            if enabled is not None and not isinstance(enabled, bool):
                raise ValueError("enabled 必须是布尔值")
            record = kb.ontologies.update(oid, binding=binding, enabled=enabled, warnings=warnings)
        return jsonify(record)
    except (ValueError, PermissionError) as exc:
        return _ontology_error(exc)


@bp.delete("/api/knowledge/ontologies/<oid>")
def delete_ontology(oid: str):
    with _kb() as kb:
        record = kb.ontologies.get(oid)
        if record is None:
            return jsonify({"error": "本体不存在"}), 404
        kb.ontologies.delete(oid)
    (_kb_dir() / Path(record["filename"]).name).unlink(missing_ok=True)
    return jsonify({"ok": True})


def _parse_ontology_upload(file, save_path: Path, filename: str):
    from Function.Knowledge.ontology_parser import parse_ontology
    from Function.Knowledge.ontology_store import physical_tables, table_requirements
    try:
        raw = file.stream.read(_ONTOLOGY_MAX_BYTES + 1)
        if len(raw) > _ONTOLOGY_MAX_BYTES:
            raise ValueError("本体文件不能超过 256 KB")
        parsed = parse_ontology(raw.decode("utf-8-sig"))
        sources = _ontology_sources()
        save_path.write_bytes(raw)
        return jsonify({**parsed, "filename": filename, "source_name": Path(file.filename).name,
                        "sources": sources, "expected_tables": sorted(physical_tables(parsed["ontology"])),
                        "table_requirements": table_requirements(parsed["ontology"])})
    except (ValueError, PermissionError, UnicodeError) as exc:
        save_path.unlink(missing_ok=True)
        return _ontology_error(exc)


def _confirm_ontology(body: dict, source_path: Path):
    from Function.Knowledge.ontology_parser import parse_ontology
    try:
        if not source_path.is_file() or source_path.stat().st_size > _ONTOLOGY_MAX_BYTES:
            raise ValueError("本体源文件不存在或超过 256 KB，请重新上传")
        parsed = parse_ontology(source_path.read_text(encoding="utf-8-sig"))
        binding, warnings = _ontology_binding(parsed["ontology"], body)
        oid = str(body.get("ontology_id") or "")
        with _kb() as kb:
            old = (kb.ontologies.get(oid) if oid else next(
                (row for row in kb.ontologies.list() if row["filename"] == source_path.name), None,
            ))
            oid = old["id"] if old else oid
            record = kb.ontologies.save(
                parsed, filename=source_path.name,
                source_name=str(body.get("source_name") or source_path.name)[:240],
                category_id=kb._normalize_category_id(_category_id()), binding=binding,
                ontology_id=oid, enabled=bool(old["enabled"]) if old else True,
                extra_warnings=warnings,
            )
        if old and old["filename"] != source_path.name:
            (_kb_dir() / Path(old["filename"]).name).unlink(missing_ok=True)
        return jsonify({"ok": True, "ontology": record,
                        "replaced_filename": old["filename"] if old else ""})
    except (ValueError, PermissionError, UnicodeError) as exc:
        return _ontology_error(exc)


# ── File parse → preview ──────────────────────────────────────────────────────

@bp.post("/api/knowledge/parse")
def parse_file():
    """Upload docx/xlsx, keep the file in uploads/knowledge/, return preview."""
    scope_dir = _kb_dir()
    scope_dir.mkdir(parents=True, exist_ok=True)

    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    f = request.files["file"]
    sid = request.form.get("session_id", "")
    provider = request.form.get("provider", "")
    original_name = f.filename or "upload"
    ext = Path(original_name).suffix.lower()

    if ext not in _ALLOWED_EXTS:
        return jsonify({"error": f"不支持的文件格式 {ext}，请上传 .xlsx / .xls / .docx / .yaml / .yml"}), 400

    # Keep original filename; prepend uuid prefix to avoid collisions
    safe_stem = "".join(c if c.isalnum() or c in "-_." else "_"
                        for c in Path(original_name).stem)[:60]
    filename = f"{uuid.uuid4().hex[:8]}_{safe_stem}{ext}"
    save_path = scope_dir / filename
    if ext in _YAML_EXTS:
        return _parse_ontology_upload(f, save_path, filename)
    f.save(str(save_path))

    try:
        selected_provider = (
            provider
            or getattr(session_manager.get_or_create(sid), "model_provider", "")
            or config_manager.get_default_provider()
            or ""
        )
        client, model = _get_client(sid, provider=provider)
        _workspace_id, user_id = _scope_context()
        from agent.model_call_audit import audit_model_call
        from Function.Knowledge.file_parser import parse_file as _parse
        result = _parse(
            str(save_path), client, model,
            audit_factory=lambda: audit_model_call(
                user_id=user_id, session_id=sid, source="web",
                purpose="knowledge_parse", billing_scope="platform",
                provider=selected_provider, model=model,
            ),
            include_usage=config_manager.is_hosted_provider(selected_provider),
        )
        result["filename"] = filename          # let frontend reference the file
        return jsonify(result)
    except Exception as e:
        log.exception("Knowledge parse failed")
        msg = str(e)
        if "JSONDecodeError" in type(e).__name__ or (msg.startswith(('"', "'")) and len(msg) < 80):
            msg = "LLM 返回格式异常，无法解析。请检查模型是否正常，或尝试重新上传。"
        return jsonify({"error": msg}), 500


# ── List uploaded source files ────────────────────────────────────────────────

@bp.get("/api/knowledge/files")
def list_files():
    """Return metadata of all uploaded source files in uploads/knowledge/."""
    scope_dir = _kb_dir()
    scope_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for p in sorted(scope_dir.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True):
        if p.suffix.lower() in _ALLOWED_EXTS:
            files.append({
                "filename": p.name,
                "size":     p.stat().st_size,
                "mtime":    p.stat().st_mtime,
            })
    return jsonify(files)


@bp.get("/api/knowledge/files/<filename>/impact")
def source_delete_impact(filename: str):
    with _kb() as kb:
        return jsonify(kb.source_delete_impact(filename))


@bp.delete("/api/knowledge/files/<filename>")
def delete_file(filename: str):
    """Delete a source file from uploads/knowledge/."""
    # Security: strip any path separators
    filename = Path(filename).name
    target = _kb_dir() / filename
    if not target.exists():
        return jsonify({"error": "File not found"}), 404
    with _kb() as kb:
        impact = kb.source_delete_impact(filename)
        for record in kb.ontologies.list():
            if record["filename"] == filename:
                kb.ontologies.delete(record["id"])
    target.unlink()
    try:
        with _kb() as kb:
            kb.delete_document_index(filename)
    except Exception:
        log.exception("Failed to delete RAG index for %s", filename)
    return jsonify({"ok": True, "impact": impact})


# ── Confirm → bulk insert ─────────────────────────────────────────────────────

@bp.post("/api/knowledge/confirm")
def confirm_records():
    try:
        body = _ontology_body()
    except ValueError as exc:
        return _ontology_error(exc)
    records = body.get("records", [])
    filename = Path(body.get("filename") or "").name
    if Path(filename).suffix.lower() in _YAML_EXTS:
        return _confirm_ontology(body, _kb_dir() / filename)
    if not records and not filename:
        return jsonify({"error": "No records or source file provided"}), 400
    try:
        category_id = _category_id()
        with _kb() as kb:
            linked_filename = filename if filename and (_kb_dir() / filename).is_file() else None
            counts = kb.bulk_insert(records, category_id=category_id, source_filename=linked_filename) if records else {"metrics": 0, "rules": 0, "notes": 0}
            rag = {"chunks": 0}
            if filename:
                source_path = _kb_dir() / filename
                if source_path.exists() and source_path.suffix.lower() in _ALLOWED_EXTS:
                    from Function.Knowledge.file_parser import extract_text
                    text = extract_text(str(source_path))
                    if text.strip():
                        rag = kb.index_document(filename, text, source_type="file", category_id=category_id)
        return jsonify({"ok": True, "inserted": counts, "rag": rag})
    except Exception as e:
        log.exception("Knowledge confirm failed")
        return jsonify({"error": str(e)}), 500


# ── Browser-owned cloud recovery ─────────────────────────────────────────────

@bp.post("/api/knowledge/restore")
def restore_structured_snapshot():
    """Recreate the disposable cloud index from the browser's local snapshot."""
    body = request.get_json(silent=True) or {}
    try:
        # Source files are restored by the browser immediately after this
        # endpoint.  Remove only supported temporary source files so the
        # server's file list cannot retain stale copies across restarts/tabs.
        scope_dir = _kb_dir()
        scope_dir.mkdir(parents=True, exist_ok=True)
        with _kb() as kb:
            restored = kb.replace_structured_snapshot(body.get("snapshot") or {})
        for path in scope_dir.iterdir():
            if path.is_file() and path.suffix.lower() in _ALLOWED_EXTS:
                path.unlink()
        return jsonify({"ok": True, "restored": restored})
    except Exception as e:
        log.exception("Knowledge structured restore failed")
        return jsonify({"error": str(e)}), 400


@bp.post("/api/knowledge/restore-file")
def restore_source_file():
    """Restore one browser-owned source file without calling an LLM again."""
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    f = request.files["file"]
    original_name = f.filename or "knowledge-source"
    ext = Path(original_name).suffix.lower()
    if ext not in _ALLOWED_EXTS:
        return jsonify({"error": f"不支持的文件格式 {ext}"}), 400
    safe_stem = "".join(c if c.isalnum() or c in "-_." else "_"
                        for c in Path(original_name).stem)[:60]
    filename = f"{uuid.uuid4().hex[:8]}_{safe_stem}{ext}"
    target = _kb_dir() / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        if ext in _YAML_EXTS:
            from Function.Knowledge.ontology_parser import parse_ontology
            raw = f.stream.read(_ONTOLOGY_MAX_BYTES + 1)
            if len(raw) > _ONTOLOGY_MAX_BYTES:
                raise ValueError("本体文件不能超过 256 KB")
            parsed = parse_ontology(raw.decode("utf-8-sig"))
            with _kb() as kb:
                record = kb.ontologies.get(str(request.form.get("ontology_id") or ""))
                if not record or record["document"] != parsed["ontology"]:
                    raise ValueError("请先恢复匹配的本体结构化快照")
                target.write_bytes(raw)
                kb.ontologies.update(record["id"], filename=filename)
            return jsonify({"ok": True, "filename": filename, "rag": {"chunks": 0}})
        f.save(str(target))
        category_name = str(request.form.get("category_name") or "").strip()
        with _kb() as kb:
            categories = kb.list_categories()
            fallback_category = int(categories[0]["id"]) if categories else 1
            category_id = next(
                (int(row["id"]) for row in categories if row["name"] == category_name),
                fallback_category,
            )
            from Function.Knowledge.file_parser import extract_text
            text = extract_text(str(target))
            rag = kb.index_document(
                filename, text, source_type="file", category_id=category_id,
            ) if text.strip() else {"chunks": 0, "embedded": 0}
        return jsonify({"ok": True, "filename": filename, "rag": rag})
    except Exception as e:
        target.unlink(missing_ok=True)
        log.exception("Knowledge source restore failed")
        return jsonify({"error": str(e)}), 400


# ── Toggle enabled ────────────────────────────────────────────────────────────

@bp.post("/api/knowledge/metrics/<int:mid>/toggle")
def toggle_metric(mid: int):
    with _kb() as kb:
        rec = kb.get_metric_by_id(mid)
        if not rec:
            return jsonify({"error": "Not found"}), 404
        return jsonify(kb.update_metric(mid, enabled=0 if rec["enabled"] else 1))


@bp.post("/api/knowledge/rules/<int:rid>/toggle")
def toggle_rule(rid: int):
    with _kb() as kb:
        rec = kb.get_rule_by_id(rid)
        if not rec:
            return jsonify({"error": "Not found"}), 404
        return jsonify(kb.update_rule(rid, enabled=0 if rec["enabled"] else 1))


@bp.post("/api/knowledge/notes/<int:nid>/toggle")
def toggle_note(nid: int):
    with _kb() as kb:
        rec = kb.get_note_by_id(nid)
        if not rec:
            return jsonify({"error": "Not found"}), 404
        return jsonify(kb.update_note(nid, enabled=0 if rec["enabled"] else 1))


# ── Metrics CRUD ──────────────────────────────────────────────────────────────

@bp.get("/api/knowledge/metrics")
def list_metrics():
    with _kb() as kb:
        return jsonify(kb.list_metrics(_category_id()))


@bp.post("/api/knowledge/metrics")
def add_metric():
    body = request.get_json(silent=True) or {}
    try:
        with _kb() as kb:
            record = kb.add_metric(
                name=body.get("name", ""),
                alias=body.get("alias", ""),
                definition=body.get("definition", ""),
                sql_template=body.get("sql_template", ""),
                notes=body.get("notes", ""),
                category_id=body.get("category_id"),
            )
        return jsonify(record), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@bp.put("/api/knowledge/metrics/<int:mid>")
def update_metric(mid: int):
    body = request.get_json(silent=True) or {}
    with _kb() as kb:
        record = kb.update_metric(mid, **body)
    if record is None:
        return jsonify({"error": "Not found"}), 404
    return jsonify(record)


@bp.delete("/api/knowledge/metrics/<int:mid>")
def delete_metric(mid: int):
    with _kb() as kb:
        kb.delete_metric(mid)
    return jsonify({"ok": True})


# ── Business rules CRUD ───────────────────────────────────────────────────────

@bp.get("/api/knowledge/rules")
def list_rules():
    with _kb() as kb:
        return jsonify(kb.list_rules(_category_id()))


@bp.post("/api/knowledge/rules")
def add_rule():
    body = request.get_json(silent=True) or {}
    try:
        with _kb() as kb:
            record = kb.add_rule(
                rule_id=body.get("rule_id", ""),
                description=body.get("description", ""),
                condition=body.get("condition", ""),
                severity=body.get("severity", "warning"),
                category_id=body.get("category_id"),
            )
        return jsonify(record), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@bp.put("/api/knowledge/rules/<int:rid>")
def update_rule(rid: int):
    body = request.get_json(silent=True) or {}
    with _kb() as kb:
        record = kb.update_rule(rid, **body)
    if record is None:
        return jsonify({"error": "Not found"}), 404
    return jsonify(record)


@bp.delete("/api/knowledge/rules/<int:rid>")
def delete_rule(rid: int):
    with _kb() as kb:
        kb.delete_rule(rid)
    return jsonify({"ok": True})


# ── Context notes CRUD ────────────────────────────────────────────────────────

@bp.get("/api/knowledge/notes")
def list_notes():
    with _kb() as kb:
        return jsonify(kb.list_notes(_category_id()))


@bp.post("/api/knowledge/notes")
def add_note():
    body = request.get_json(silent=True) or {}
    try:
        with _kb() as kb:
            record = kb.add_note(
                topic=body.get("topic", ""),
                content=body.get("content", ""),
                tags=body.get("tags", ""),
                category_id=body.get("category_id"),
            )
        return jsonify(record), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@bp.put("/api/knowledge/notes/<int:nid>")
def update_note(nid: int):
    body = request.get_json(silent=True) or {}
    with _kb() as kb:
        record = kb.update_note(nid, **body)
    if record is None:
        return jsonify({"error": "Not found"}), 404
    return jsonify(record)


@bp.delete("/api/knowledge/notes/<int:nid>")
def delete_note(nid: int):
    with _kb() as kb:
        kb.delete_note(nid)
    return jsonify({"ok": True})


# ── Search ────────────────────────────────────────────────────────────────────

@bp.get("/api/knowledge/search")
def search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"metrics": [], "rules": [], "notes": [], "ontologies": []})
    try:
        sources = _ontology_sources(active_only=True)
        with _kb() as kb:
            results = kb.search(q)
            results["ontologies"] = kb.ontologies.search(q, sources)
        return jsonify(results)
    except PermissionError as exc:
        return _ontology_error(exc)


# ── Temporary per-session prompt ──────────────────────────────────────────────
# A free-form instruction the user sets for a single conversation. When enabled,
# api/chat.py injects it into the system prompt on every turn of that session.
# Lives only in the in-memory ChatSession — not persisted to disk.

from agent.prompts import (  # noqa: E402
    TEMP_PROMPT_MAX_CHARS,
    strip_temp_prompt_thinking,
)

# System prompt for the optional LLM "tidy up" pass. Turns a user's colloquial,
# possibly long description into a clean, imperative instruction block.
_TEMP_PROMPT_REFINE_SYSTEM = (
    "你是一个提示词整理助手。用户会给你一段为某次数据分析对话临时设定的指令，"
    "内容可能口语化、冗长或结构松散。请把它整理成一段清晰、精炼、可直接作为"
    "系统补充指令使用的中文说明。\n"
    "要求：\n"
    "- 保留用户的全部实质意图，不要新增、不要编造需求。\n"
    "- 用祈使句，必要时用简短的项目符号列表组织。\n"
    "- 不要写解释、不要加标题、不要用代码块，只输出整理后的指令正文。\n"
    "- 绝对不要输出思考过程，不要输出 <think>、</think> 或类似推理标签。\n"
    "- 如果原文已经足够清晰，可原样或仅做轻微润色后返回。"
)


def _refine_temp_prompt(sid: str, provider: str, raw_text: str) -> tuple[str, str]:
    """Run the LLM tidy-up pass. Returns (refined_text, warning).

    On any failure we degrade gracefully to the raw text so the feature never
    breaks just because the model misbehaves.
    """
    try:
        client, model = _get_client(sid, provider=provider)
    except Exception as e:
        return raw_text, f"未能调用模型整理（已按原文保存）：{e}"
    try:
        sess = session_manager.get_or_create(sid)
        selected_provider = (
            provider or getattr(sess, "model_provider", "")
            or config_manager.get_default_provider() or ""
        )
        from agent.model_call_audit import audit_model_call
        from agent.retry import call_with_retry
        audit = audit_model_call(
            user_id=str(getattr(sess, "owner_user_id", "") or ""),
            session_id=sid, source="web", purpose="temp_prompt_refine",
            billing_scope="platform", provider=selected_provider, model=model,
        )
        resp = call_with_retry(
            client.chat.completions.create,
            on_attempt=audit.attempt,
            model=model,
            messages=[
                {"role": "system", "content": _TEMP_PROMPT_REFINE_SYSTEM},
                {"role": "user",   "content": raw_text},
            ],
            max_tokens=1024,
            temperature=0,
        )
        audit.succeed(resp)
        refined = strip_temp_prompt_thinking(
            resp.choices[0].message.content or ""
        )
        if not refined:
            return raw_text, "模型未返回可用正文，已按原文保存。"
        return refined, ""
    except Exception as e:
        if "audit" in locals():
            audit.fail(e)
        log.warning("[temp-prompt] refine failed: %s", e)
        return raw_text, f"整理失败，已按原文保存：{e}"


def _temp_prompt_state(sess) -> dict:
    # Repair prompts saved before reasoning-tag filtering was added.
    stored = getattr(sess, "temp_prompt", "")
    cleaned = strip_temp_prompt_thinking(stored)
    if cleaned != stored:
        sess.temp_prompt = cleaned
        if not cleaned:
            sess.temp_prompt_enabled = False
    return {
        "temp_prompt": cleaned,
        "enabled":     bool(getattr(sess, "temp_prompt_enabled", False)),
        "max_chars":   TEMP_PROMPT_MAX_CHARS,
    }


@bp.get("/api/session/<sid>/temp-prompt")
@require_session_ownership
def get_temp_prompt(sid: str):
    sess = session_manager.get_or_create(sid)
    return jsonify(_temp_prompt_state(sess))


@bp.post("/api/session/<sid>/temp-prompt")
@require_session_ownership
def set_temp_prompt(sid: str):
    """Save the temp prompt. body: {text, raw: bool, provider?: str}

    raw=True  → store the user's text verbatim (after basic trimming).
    raw=False → run an LLM tidy-up pass first, falling back to raw on failure.
    Saving non-empty text auto-enables the prompt; clearing it disables it.
    """
    body = request.get_json(silent=True) or {}
    raw_text = strip_temp_prompt_thinking(body.get("text") or "")
    use_raw  = bool(body.get("raw", True))
    provider = (body.get("provider") or "").strip()

    if len(raw_text) > TEMP_PROMPT_MAX_CHARS:
        return jsonify({
            "error": f"内容过长（超过 {TEMP_PROMPT_MAX_CHARS} 字），请精简后再保存。"
        }), 400

    sess = session_manager.get_or_create(sid)
    warning = ""

    if not raw_text:
        # Empty input clears and disables the prompt.
        sess.temp_prompt = ""
        sess.temp_prompt_enabled = False
        return jsonify({**_temp_prompt_state(sess), "warning": ""})

    if use_raw:
        final_text = raw_text
    else:
        final_text, warning = _refine_temp_prompt(sid, provider, raw_text)

    final_text = strip_temp_prompt_thinking(final_text)
    if not final_text:
        return jsonify({"error": "清理思考内容后没有可保存的指令正文。"}), 400

    sess.temp_prompt = final_text
    sess.temp_prompt_enabled = True
    log.info("[temp-prompt] set  sid=%s  raw=%s  len=%d", sid, use_raw, len(final_text))
    return jsonify({**_temp_prompt_state(sess), "warning": warning})


@bp.post("/api/session/<sid>/temp-prompt/toggle")
@require_session_ownership
def toggle_temp_prompt(sid: str):
    """Flip the enabled switch. Cannot enable when there's no text to inject."""
    sess = session_manager.get_or_create(sid)
    if not getattr(sess, "temp_prompt", "").strip():
        sess.temp_prompt_enabled = False
        return jsonify({**_temp_prompt_state(sess),
                        "warning": "临时指令为空，无法启用。"})
    sess.temp_prompt_enabled = not bool(getattr(sess, "temp_prompt_enabled", False))
    log.info("[temp-prompt] toggle  sid=%s  enabled=%s", sid, sess.temp_prompt_enabled)
    return jsonify(_temp_prompt_state(sess))
