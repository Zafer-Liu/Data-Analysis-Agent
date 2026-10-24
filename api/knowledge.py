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
_ALLOWED_EXTS = {".xlsx", ".xls", ".docx"}


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
            deleted = kb.delete_category(cid)
        return jsonify({"ok": True, "deleted": deleted})
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


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
        return jsonify({"error": f"不支持的文件格式 {ext}，请上传 .xlsx / .xls / .docx"}), 400

    # Keep original filename; prepend uuid prefix to avoid collisions
    safe_stem = "".join(c if c.isalnum() or c in "-_." else "_"
                        for c in Path(original_name).stem)[:60]
    filename = f"{uuid.uuid4().hex[:8]}_{safe_stem}{ext}"
    save_path = scope_dir / filename
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


@bp.delete("/api/knowledge/files/<filename>")
def delete_file(filename: str):
    """Delete a source file from uploads/knowledge/."""
    # Security: strip any path separators
    filename = Path(filename).name
    target = _kb_dir() / filename
    if not target.exists():
        return jsonify({"error": "File not found"}), 404
    target.unlink()
    try:
        with _kb() as kb:
            kb.delete_document_index(filename)
    except Exception:
        log.exception("Failed to delete RAG index for %s", filename)
    return jsonify({"ok": True})


# ── Confirm → bulk insert ─────────────────────────────────────────────────────

@bp.post("/api/knowledge/confirm")
def confirm_records():
    body = request.get_json(silent=True) or {}
    records = body.get("records", [])
    filename = Path(body.get("filename") or "").name
    if not records and not filename:
        return jsonify({"error": "No records or source file provided"}), 400
    try:
        category_id = _category_id()
        with _kb() as kb:
            counts = kb.bulk_insert(records, category_id=category_id) if records else {"metrics": 0, "rules": 0, "notes": 0}
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
        for path in scope_dir.iterdir():
            if path.is_file() and path.suffix.lower() in _ALLOWED_EXTS:
                path.unlink()
        with _kb() as kb:
            restored = kb.replace_structured_snapshot(body.get("snapshot") or {})
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
        return jsonify({"metrics": [], "rules": [], "notes": []})
    with _kb() as kb:
        return jsonify(kb.search(q))


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
