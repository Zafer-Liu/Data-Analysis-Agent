# -*- coding: utf-8 -*-
"""Blueprint: business knowledge base — parse, preview, confirm, CRUD, toggle."""
import logging
import uuid
from pathlib import Path

from flask import Blueprint, request, jsonify, send_from_directory

from .state import session_manager, config_manager
from infrastructure.paths import data_path

log = logging.getLogger(__name__)
bp = Blueprint("knowledge", __name__)

# Source mode: <project>/uploads/knowledge; packaged mode: <data-root>/uploads/knowledge.
# This file lives at <root>/api/knowledge.py → parent = api/ → parent = root
_KB_DIR = data_path("uploads", "knowledge")
_ALLOWED_EXTS = {".xlsx", ".xls", ".docx"}


def _ensure_dir() -> None:
    _KB_DIR.mkdir(parents=True, exist_ok=True)


def _scope_context() -> tuple[str, str]:
    """Resolve the trusted user plus the session's stable Workspace identity."""
    body = request.get_json(silent=True) if request.is_json else {}
    sid = str(
        request.args.get("session_id")
        or request.form.get("session_id")
        or (body or {}).get("session_id")
        or ""
    )
    user_id = str(
        request.headers.get("X-BAA-User-ID")
        or request.args.get("user_id")
        or (body or {}).get("user_id")
        or "local-default"
    ).strip()[:200]
    from data.workspace import workspace_manager
    workspace_id = str(workspace_manager.workspace_id_for_session(sid) or "")
    return workspace_id, user_id


def _kb_dir() -> Path:
    from Function.Knowledge.knowledge_base import knowledge_scope_dir
    workspace_id, user_id = _scope_context()
    return knowledge_scope_dir(workspace_id=workspace_id, user_id=user_id)


def _kb():
    from Function.Knowledge.knowledge_base import KnowledgeBase
    workspace_id, user_id = _scope_context()
    return KnowledgeBase(workspace_id=workspace_id, user_id=user_id)


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
        client, model = _get_client(sid, provider=provider)
        from Function.Knowledge.file_parser import parse_file as _parse
        result = _parse(str(save_path), client, model)
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
        _kb().delete_document_index(filename)
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
        kb = _kb()
        counts = kb.bulk_insert(records) if records else {"metrics": 0, "rules": 0, "notes": 0}
        rag = {"chunks": 0}
        if filename:
            source_path = _kb_dir() / filename
            if source_path.exists() and source_path.suffix.lower() in _ALLOWED_EXTS:
                from Function.Knowledge.file_parser import extract_text
                text = extract_text(str(source_path))
                if text.strip():
                    rag = kb.index_document(filename, text, source_type="file")
        return jsonify({"ok": True, "inserted": counts, "rag": rag})
    except Exception as e:
        log.exception("Knowledge confirm failed")
        return jsonify({"error": str(e)}), 500


# ── Toggle enabled ────────────────────────────────────────────────────────────

@bp.post("/api/knowledge/metrics/<int:mid>/toggle")
def toggle_metric(mid: int):
    rec = _kb().get_metric_by_id(mid)
    if not rec:
        return jsonify({"error": "Not found"}), 404
    updated = _kb().update_metric(mid, enabled=0 if rec["enabled"] else 1)
    return jsonify(updated)


@bp.post("/api/knowledge/rules/<int:rid>/toggle")
def toggle_rule(rid: int):
    rec = _kb().get_rule_by_id(rid)
    if not rec:
        return jsonify({"error": "Not found"}), 404
    updated = _kb().update_rule(rid, enabled=0 if rec["enabled"] else 1)
    return jsonify(updated)


@bp.post("/api/knowledge/notes/<int:nid>/toggle")
def toggle_note(nid: int):
    rec = _kb().get_note_by_id(nid)
    if not rec:
        return jsonify({"error": "Not found"}), 404
    updated = _kb().update_note(nid, enabled=0 if rec["enabled"] else 1)
    return jsonify(updated)


# ── Metrics CRUD ──────────────────────────────────────────────────────────────

@bp.get("/api/knowledge/metrics")
def list_metrics():
    return jsonify(_kb().list_metrics())


@bp.post("/api/knowledge/metrics")
def add_metric():
    body = request.get_json(silent=True) or {}
    try:
        record = _kb().add_metric(
            name=body.get("name", ""),
            alias=body.get("alias", ""),
            definition=body.get("definition", ""),
            sql_template=body.get("sql_template", ""),
            notes=body.get("notes", ""),
        )
        return jsonify(record), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@bp.put("/api/knowledge/metrics/<int:mid>")
def update_metric(mid: int):
    body = request.get_json(silent=True) or {}
    record = _kb().update_metric(mid, **body)
    if record is None:
        return jsonify({"error": "Not found"}), 404
    return jsonify(record)


@bp.delete("/api/knowledge/metrics/<int:mid>")
def delete_metric(mid: int):
    _kb().delete_metric(mid)
    return jsonify({"ok": True})


# ── Business rules CRUD ───────────────────────────────────────────────────────

@bp.get("/api/knowledge/rules")
def list_rules():
    return jsonify(_kb().list_rules())


@bp.post("/api/knowledge/rules")
def add_rule():
    body = request.get_json(silent=True) or {}
    try:
        record = _kb().add_rule(
            rule_id=body.get("rule_id", ""),
            description=body.get("description", ""),
            condition=body.get("condition", ""),
            severity=body.get("severity", "warning"),
        )
        return jsonify(record), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@bp.put("/api/knowledge/rules/<int:rid>")
def update_rule(rid: int):
    body = request.get_json(silent=True) or {}
    record = _kb().update_rule(rid, **body)
    if record is None:
        return jsonify({"error": "Not found"}), 404
    return jsonify(record)


@bp.delete("/api/knowledge/rules/<int:rid>")
def delete_rule(rid: int):
    _kb().delete_rule(rid)
    return jsonify({"ok": True})


# ── Context notes CRUD ────────────────────────────────────────────────────────

@bp.get("/api/knowledge/notes")
def list_notes():
    return jsonify(_kb().list_notes())


@bp.post("/api/knowledge/notes")
def add_note():
    body = request.get_json(silent=True) or {}
    try:
        record = _kb().add_note(
            topic=body.get("topic", ""),
            content=body.get("content", ""),
            tags=body.get("tags", ""),
        )
        return jsonify(record), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@bp.put("/api/knowledge/notes/<int:nid>")
def update_note(nid: int):
    body = request.get_json(silent=True) or {}
    record = _kb().update_note(nid, **body)
    if record is None:
        return jsonify({"error": "Not found"}), 404
    return jsonify(record)


@bp.delete("/api/knowledge/notes/<int:nid>")
def delete_note(nid: int):
    _kb().delete_note(nid)
    return jsonify({"ok": True})


# ── Search ────────────────────────────────────────────────────────────────────

@bp.get("/api/knowledge/search")
def search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"metrics": [], "rules": [], "notes": []})
    return jsonify(_kb().search(q))


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
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _TEMP_PROMPT_REFINE_SYSTEM},
                {"role": "user",   "content": raw_text},
            ],
            max_tokens=1024,
            temperature=0,
        )
        refined = strip_temp_prompt_thinking(
            resp.choices[0].message.content or ""
        )
        if not refined:
            return raw_text, "模型未返回可用正文，已按原文保存。"
        return refined, ""
    except Exception as e:
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
def get_temp_prompt(sid: str):
    sess = session_manager.get_or_create(sid)
    return jsonify(_temp_prompt_state(sess))


@bp.post("/api/session/<sid>/temp-prompt")
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
