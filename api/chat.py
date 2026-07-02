"""Blueprint: conversation (SSE streaming) and chart serving."""
import json
import logging
import re
import time
import uuid

from flask import Blueprint, request, Response, jsonify

from .state import session_manager, config_manager, chart_store
from agent.activation import ActivationContext, INTERNAL_ACTIONS
from agent.agent import BusinessAgent
from agent.commands import CommandLoader, CommandType
from agent.prompts import get_system_prompt
from agent.reasoning import split_reasoning_tags
from agent.retry import call_with_retry as _call_with_retry
from agent.skills import SkillLoader

log = logging.getLogger(__name__)
bp = Blueprint("chat", __name__)


_PROMPT_SUGGESTION_DIRECTIVE = """You are a prompt suggestion engine.
Predict the single next message this user is most likely to type after reading the assistant's latest answer.
Return only the message text that should be prefilled in the chat input.
Do not explain, do not quote, do not use markdown, and do not mention that this is a suggestion.
Keep it short, concrete, and directly actionable."""


def _visible_history_for_prompt_suggestion(history: list, max_messages: int = 8, max_chars: int = 9000) -> list[dict]:
    visible: list[dict] = []
    total = 0
    for msg in reversed(history or []):
        role = msg.get("role")
        content = str(msg.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content or msg.get("tool_calls"):
            continue
        content = re.sub(r"\s+", " ", content)[:2200]
        total += len(content)
        visible.append({"role": role, "content": content})
        if len(visible) >= max_messages or total >= max_chars:
            break
    return list(reversed(visible))


def _sanitize_prompt_suggestion(text: str, max_len: int = 220) -> str:
    suggestion = str(text or "").strip()
    if not suggestion:
        return ""
    if re.search(r"(?is)<think(?:ing)?\b", suggestion) and not re.search(r"(?is)</think(?:ing)?\s*>", suggestion):
        return ""
    suggestion = re.sub(r"(?is)<think\b[^>]*>.*?</think\s*>", "", suggestion)
    suggestion = re.sub(r"(?is)<thinking\b[^>]*>.*?</thinking\s*>", "", suggestion)
    suggestion = re.sub(r"(?is)</?think(?:ing)?\b[^>]*>", "", suggestion)
    suggestion = re.sub(r"^```(?:\w+)?\s*", "", suggestion)
    suggestion = re.sub(r"\s*```$", "", suggestion)
    suggestion = suggestion.strip().strip("\"'“”‘’`")
    suggestion = re.sub(
        r"^(?:建议|用户下一步|下一条消息|next message|suggestion|user)\s*[:：]\s*",
        "",
        suggestion,
        flags=re.IGNORECASE,
    ).strip()
    suggestion = re.sub(r"^```(?:\w+)?\s*", "", suggestion)
    suggestion = re.sub(r"\s*```$", "", suggestion)
    suggestion = re.sub(r"^\s*[-*•]\s*", "", suggestion).strip()
    suggestion = re.sub(r"[ \t]+", " ", suggestion)
    suggestion = re.sub(r"\n{3,}", "\n\n", suggestion)
    if len(suggestion) > max_len:
        suggestion = suggestion[:max_len].rstrip("，,。.!！?？;；:：、 ")
    return suggestion


def _build_prompt_suggestion_messages(history: list, lang: str = "zh") -> list[dict]:
    visible = _visible_history_for_prompt_suggestion(history)
    if len(visible) < 2:
        return []
    language_hint = (
        "Prefer Chinese unless the recent conversation is clearly in another language."
        if lang != "en" else
        "Prefer English unless the recent conversation is clearly in another language."
    )
    return [
        {"role": "system", "content": get_system_prompt()},
        *visible,
        {
            "role": "system",
            "content": f"{_PROMPT_SUGGESTION_DIRECTIVE}\n{language_hint}",
        },
    ]


def _fallback_prompt_suggestion(lang: str = "zh") -> str:
    return ""


def _build_team_context(sid: str, workspace_id: str = "", *, max_teams: int = 6, max_messages: int = 3) -> str:
    try:
        from agent.tools.workspace.teams import WorkspaceTeamStore

        store = WorkspaceTeamStore(sid, workspace_id=workspace_id)
        teams = store.list()
    except Exception as exc:
        log.debug("[teams] context unavailable sid=%s workspace=%s error=%s", sid, workspace_id, exc)
        return ""
    if not teams:
        return ""

    lines = [
        "Existing workspace analyst teams are available. Use team_status when details may be stale, "
        "send_message to queue work for members, and team_delegate for parallel bounded member turns.",
        "Delegated members have limited read-only tools for schema, data queries, knowledge search, "
        "and workspace file reading. They cannot mutate data or create nested teams.",
        "For a fresh user request, do not synthesize old member results as if they were new work. "
        "If prior team messages are from an earlier turn or may be stale, create/recreate the team "
        "or call team_delegate again for the required members before producing the final answer.",
    ]
    for team in teams[:max_teams]:
        name = str(team.get("name") or "")
        if not name:
            continue
        try:
            status = store.status(name)
        except Exception:
            status = team
        description = str(status.get("description") or "").strip()
        lines.append(
            f"- Team {name}: {len(status.get('members') or [])} members; "
            f"lead_unread={status.get('lead_unread_messages', 0)}; "
            f"description={description[:160] or 'none'}"
        )
        members = []
        for member in (status.get("members") or [])[:10]:
            last_message = str(member.get("last_message") or "").replace("\n", " ")[:90]
            members.append(
                f"{member.get('name', '')}"
                f"(role={member.get('role', '') or 'analyst'}, "
                f"status={member.get('status', 'idle')}, "
                f"unread={member.get('unread_messages', 0)}"
                f"{', last=' + last_message if last_message else ''})"
            )
        if members:
            lines.append("  Members: " + "; ".join(members))
        recent = status.get("recent_messages") or []
        for message in recent[-max_messages:]:
            body = str(message.get("message") or "").replace("\n", " ")[:140]
            if body:
                lines.append(
                    f"  Message {message.get('sender', '?')} -> {message.get('recipient', '?')}: {body}"
                )
    return "\n".join(lines)[:5000]


class ActivationRequestError(ValueError):
    def __init__(self, message: str, code: str = "invalid_activation") -> None:
        super().__init__(message)
        self.code = code


def _resolve_activation(sess, payload: dict):
    """Resolve untrusted request names into server-owned typed definitions."""
    skill_name = str(payload.get("skill") or "").strip()
    command_name = str(payload.get("command") or "").strip().lstrip("/").lower()
    internal_action = str(payload.get("internal_action") or "").strip().lower()

    # Compatibility window for S3: current confirmation cards still send
    # internal actions through `command`. S4 will emit `internal_action`.
    if command_name in INTERNAL_ACTIONS and not internal_action:
        internal_action, command_name = command_name, ""
    try:
        activation = ActivationContext(
            skill_name=skill_name,
            command_name=command_name,
            internal_action=internal_action,
        )
    except ValueError as exc:
        raise ActivationRequestError(
            "skill、command 和 internal_action 不能同时使用。",
            "activation_conflict",
        ) from exc

    if internal_action and internal_action not in INTERNAL_ACTIONS:
        raise ActivationRequestError("未知的内部操作。", "unknown_internal_action")

    from data.workspace import workspace_manager
    runtime = workspace_manager.get(sess.session_id)
    workspace_root = runtime.workdir if runtime else None
    skill_def = None
    command_def = None
    if skill_name:
        loader = SkillLoader(
            workspace_dir=(workspace_root / ".baa" / "skills") if workspace_root else None,
        )
        skill_def = loader.load_all().get(skill_name)
        if skill_def is None:
            raise ActivationRequestError(
                f"未知技能：{skill_name}", "unknown_skill",
            )
    elif command_name:
        loader = CommandLoader(
            workspace_dir=(workspace_root / ".baa" / "commands") if workspace_root else None,
        )
        command_def = loader.load().get(command_name)
        if command_def is None:
            raise ActivationRequestError(
                f"未知斜杠命令：/{command_name}", "unknown_command",
            )
        if command_def.type is not CommandType.PROMPT:
            raise ActivationRequestError(
                f"/{command_def.name} 是 {command_def.type.value} 命令，不能提交给 Agent。",
                "command_not_agent_routable",
            )
        activation = ActivationContext(command_name=command_def.name)
    return activation, skill_def, command_def


def _resolve_data_context(sess, raw) -> dict | None:
    """Validate preview-selected remote SQL tables against active sources."""
    if not isinstance(raw, dict):
        return None
    requested = raw.get("tables")
    if not isinstance(requested, list):
        requested = [raw] if raw.get("table") else []
    requested = requested[:20]
    if not requested or not hasattr(sess, "_active_entries"):
        return None

    active = sess._active_entries()
    from data.sources.sql import SQLDataSource
    active_by_id = {entry.get("id"): (idx, entry.get("source"))
                    for idx, entry in enumerate(active, start=1)
                    if isinstance(entry.get("source"), SQLDataSource)}
    source_tables = {}
    all_names = []
    for source_id, (_, src) in active_by_id.items():
        try:
            source_tables[source_id] = src.list_catalog_tables()
            if not source_tables[source_id]:
                source_tables[source_id] = src.list_tables()
        except Exception:
            # Compatibility for lightweight/legacy SQL source implementations.
            try:
                source_tables[source_id] = src.list_tables()
            except Exception:
                source_tables[source_id] = []
        try:
            all_names.extend(source_tables[source_id])
        except Exception:
            pass
    collision = len(all_names) != len(set(all_names))

    valid_source_ids = {
        str(item.get("source_id") or "")
        for item in requested if isinstance(item, dict)
        and str(item.get("table") or "").strip()
           in source_tables.get(str(item.get("source_id") or ""), [])
    }
    cross_source = len(valid_source_ids) > 1

    resolved = []
    seen = set()
    for item in requested:
        if not isinstance(item, dict):
            continue
        source_id = str(item.get("source_id") or "")
        table = str(item.get("table") or "").strip()
        if (source_id, table) in seen or source_id not in active_by_id:
            continue
        idx, src = active_by_id[source_id]
        if table not in source_tables.get(source_id, []):
            continue
        seen.add((source_id, table))
        resolved.append({
            "source_id": source_id,
            "source_name": getattr(src, "name", "未命名"),
            "table": table,
            "query_table": f"src{idx}__{table}" if (collision or cross_source) else table,
        })
    return {"tables": resolved} if resolved else None


def _apply_sql_analysis_context(sess, data_context: dict | None) -> list[dict]:
    """Persist validated SQL scope and return active SQL sources still unscoped."""
    from data.sources.sql import SQLDataSource
    selected_by_source: dict[str, list[str]] = {}
    for item in (data_context or {}).get("tables", []):
        selected_by_source.setdefault(item["source_id"], []).append(item["table"])

    missing = []
    changed = False
    for entry in sess._active_entries() if hasattr(sess, "_active_entries") else []:
        src = entry.get("source")
        if not isinstance(src, SQLDataSource):
            continue
        if entry["id"] in selected_by_source:
            src.set_analysis_tables(selected_by_source[entry["id"]])
            changed = True
        if not src.get_analysis_tables():
            missing.append({"source_id": entry["id"], "source_name": getattr(src, "name", "SQL 数据库")})
    if changed:
        sess._combined_schema_cache = None
        if hasattr(sess, "_invalidate_merged_source"):
            sess._invalidate_merged_source()
    return missing


def _build_agent(
    sess, *, workspace_id: str | None = None, source_snapshot=None,
    hook_engine=None, hook_context=None, user_id: str = "",
) -> BusinessAgent:
    provider = sess.model_provider or config_manager.get_default_provider()
    if not provider:
        raise ValueError("未配置任何 LLM 模型，请先在「模型设置」中添加模型。")
    from LLM.llm_config_manager import get_llm_client
    client = get_llm_client(provider)
    cfg = config_manager.get_config(provider)
    # Use cached schema when available; recompute only after data source changes
    # (cache is invalidated by add_source / remove_source / toggle_source / data_source setter).
    if source_snapshot is not None:
        combined_schema = source_snapshot.combined_schema
    elif hasattr(sess, "get_combined_schema"):
        if not getattr(sess, "_combined_schema_cache", None):
            sess._combined_schema_cache = sess.get_combined_schema()
        combined_schema = sess._combined_schema_cache
    else:
        combined_schema = None
    active_sources = (
        list(source_snapshot.sources) if source_snapshot is not None else
        ([e["source"] for e in sess._active_entries()]
         if hasattr(sess, "_active_entries") else [])
    )
    if not active_sources and sess.data_source:
        active_sources = [sess.data_source]

    # 若有数据源但 schema 仍为空，尝试实时获取一次。
    # 若获取后仍为空（SQL 数据源连接断开、文件丢失等），报错提示用户重新连接，
    # 而不是让 LLM 无声地空转后输出空回复。
    if active_sources and not combined_schema:
        if source_snapshot is None:
            try:
                combined_schema = sess.get_combined_schema()
                sess._combined_schema_cache = combined_schema
            except Exception as exc:
                log.warning("[chat] schema fetch failed  sid=%s  error=%s", sess.session_id, exc)
                combined_schema = None
        if not combined_schema:
            src_names = "、".join(getattr(s, "name", "未知数据源") for s in active_sources)
            raise ValueError(
                f"数据源「{src_names}」的连接已断开（可能由服务重启引起），"
                "请在侧边栏重新连接数据源后再试。"
            )

    # Build (or reuse cached) MergedDataSource when ≥2 sources are active.
    # This enables cross-source JOIN / UNION in the agent.
    merged_source = (
        source_snapshot.merged_source if source_snapshot is not None else
        (sess.get_merged_source() if hasattr(sess, "get_merged_source") else None)
    )

    src_names = [getattr(s, "name", "?") for s in active_sources]
    log.debug("[chat] build_agent  provider=%s  model=%s  active_sources=%s  merged=%s",
              provider, cfg.model, src_names, merged_source is not None)

    provider_defaults = config_manager.DEFAULT_CONFIGS.get(provider, {})
    supports_prompt_cache = getattr(cfg, "supports_prompt_cache", None)
    if supports_prompt_cache is None:
        supports_prompt_cache = provider_defaults.get(
            "supports_prompt_cache", False
        )
    prompt_cache_mode = getattr(cfg, "prompt_cache_mode", None)
    if not prompt_cache_mode:
        prompt_cache_mode = provider_defaults.get("prompt_cache_mode", "none")

    return BusinessAgent(
        client=client, model=cfg.model,
        data_source=(source_snapshot.primary if source_snapshot is not None else sess.data_source),
        combined_schema=combined_schema,
        all_sources=active_sources,
        merged_source=merged_source,
        enable_thinking=cfg.enable_thinking,
        thinking_budget=cfg.thinking_budget,
        chart_store=chart_store,
        session_chart_ids=list(getattr(sess, "chart_ids", [])),
        color_scheme=getattr(sess, "ppt_color_scheme", "mckinsey"),
        session_id=sess.session_id,
        workspace_id=workspace_id,
        user_id=user_id,
        job_runner=sess.job_runner,
        context_window=getattr(cfg, "context_window", None),
        max_output_tokens=getattr(cfg, "max_output_tokens", None),
        hook_engine=hook_engine,
        hook_context=hook_context,
        compaction_state=getattr(sess, "compaction_state", None),
        provider=provider,
        usage_recorder=sess.record_usage,
        mcp_discovery_recorder=sess.record_discovered_mcp_tools,
        supports_prompt_cache=supports_prompt_cache,
        prompt_cache_mode=prompt_cache_mode,
        prompt_cache_retention=(
            getattr(cfg, "prompt_cache_retention", None)
            or provider_defaults.get("prompt_cache_retention", "in_memory")
        ),
        cache_breakpoint_strategy=(
            getattr(cfg, "cache_breakpoint_strategy", None)
            or provider_defaults.get(
                "cache_breakpoint_strategy", "stable_prefix"
            )
        ),
    )


# ── Session lifecycle ──────────────────────────────────────────────────────

@bp.post("/api/session/new")
def new_session():
    sess = session_manager.create()
    try:
        from agent.hooks.models import HookContext
        from data.hooks_store import load_engine

        load_engine().run_hooks(
            "session_start",
            HookContext(event_name="session_start", session_id=sess.session_id),
        )
    except Exception as exc:
        log.debug("[hooks] session_start skipped sid=%s error=%s", sess.session_id, exc)
    log.info("[session] created  sid=%s", sess.session_id)
    return jsonify({"session_id": sess.session_id})


@bp.get("/api/session/<sid>/ping")
def ping_session(sid: str):
    sess = session_manager.get(sid)
    if not sess:
        log.debug("[session] ping  sid=%s  alive=False", sid)
        return jsonify({"alive": False}), 404
    from api.saved_sessions import _visible_msg_count
    cnt = _visible_msg_count(sess.history)
    log.debug("[session] ping  sid=%s  alive=True  msg_count=%d", sid, cnt)
    return jsonify({"alive": True, "msg_count": cnt})


@bp.get("/api/session/<sid>/load-current")
def load_current_session(sid: str):
    sess = session_manager.get(sid)
    if not sess:
        log.warning("[session] load-current  sid=%s  not found", sid)
        return jsonify({"error": "session not found"}), 404
    from api.saved_sessions import _visible_msg_count
    cnt = _visible_msg_count(sess.history)
    log.info("[session] load-current  sid=%s  msg_count=%d", sid, cnt)
    return jsonify({
        "history":      sess.history,
        "total_input":  sess.total_input_tokens,
        "total_output": sess.total_output_tokens,
        "total_cached_input": getattr(sess, "total_cached_input_tokens", 0),
        "total_cache_write": getattr(sess, "total_cache_write_tokens", 0),
        "usage_breakdowns": list(getattr(sess, "usage_breakdowns", []))[-100:],
        "msg_count":    cnt,
    })


@bp.get("/api/session/<sid>/token-metrics")
def get_token_metrics(sid: str):
    """Return bounded per-call Token diagnostics without prompt contents."""
    sess = session_manager.get(sid)
    if not sess:
        return jsonify({"error": "session not found"}), 404
    breakdowns = list(getattr(sess, "usage_breakdowns", []) or [])[-100:]
    actual_prompt = sum(
        int(item.get("actual_prompt_tokens") or 0)
        for item in breakdowns if isinstance(item, dict)
    )
    estimated_prompt = sum(
        int(item.get("payload_tokens_est") or 0)
        for item in breakdowns if isinstance(item, dict)
    )
    estimation_error_pct = (
        round(abs(estimated_prompt - actual_prompt) / actual_prompt * 100, 2)
        if actual_prompt else None
    )
    total_input = int(getattr(sess, "total_input_tokens", 0) or 0)
    total_cached = int(getattr(sess, "total_cached_input_tokens", 0) or 0)
    return jsonify({
        "ok": True,
        "calls_retained": len(breakdowns),
        "retention_limit": 100,
        "total_input_tokens": total_input,
        "total_output_tokens": int(getattr(sess, "total_output_tokens", 0) or 0),
        "total_cached_input_tokens": total_cached,
        "total_cache_write_tokens": int(
            getattr(sess, "total_cache_write_tokens", 0) or 0
        ),
        "cache_hit_ratio": (
            round(total_cached / total_input, 4) if total_input else 0.0
        ),
        "estimated_prompt_tokens_retained": estimated_prompt,
        "actual_prompt_tokens_retained": actual_prompt,
        "estimation_error_pct": estimation_error_pct,
        "breakdowns": breakdowns,
    })


@bp.post("/api/session/<sid>/clear")
def clear_history(sid: str):
    sess = session_manager.get_or_create(sid)
    old_count = len(sess.history)
    sess.clear_history()
    log.info("[session] clear  sid=%s  cleared=%d entries", sid, old_count)
    return jsonify({"ok": True})


# ── Chart serving ──────────────────────────────────────────────────────────

@bp.get("/api/chart/<chart_id>")
def serve_chart(chart_id: str):
    html = chart_store.get(chart_id)
    if not html:
        log.warning("[chart] not found  chart_id=%s", chart_id)
        return "Chart not found", 404
    return Response(html, mimetype="text/html")


# ── Stop ───────────────────────────────────────────────────────────────────

@bp.post("/api/session/<sid>/stop")
def stop_session(sid: str):
    sess = session_manager.get(sid)
    if sess:
        sess.cancel_requested = True
        try:
            from agent.hooks.models import HookContext
            from data.hooks_store import load_engine

            load_engine().run_hooks(
                "stop",
                HookContext(event_name="stop", session_id=sid, message="stop requested"),
            )
        except Exception as exc:
            log.debug("[hooks] stop skipped sid=%s error=%s", sid, exc)
        log.info("[session] stop requested  sid=%s", sid)
    return jsonify({"ok": True})


# ── Prompt suggestion ───────────────────────────────────────────────────────

@bp.post("/api/session/<sid>/prompt-suggestion")
def prompt_suggestion(sid: str):
    log.debug("[prompt-suggestion] called sid=%s", sid)
    sess = session_manager.get(sid)
    if not sess:
        log.warning("[prompt-suggestion] session not found sid=%s", sid)
        return jsonify({"ok": False, "suggestion": ""}), 404

    lang = str((request.json or {}).get("lang") or "zh").lower()
    messages = _build_prompt_suggestion_messages(sess.history, lang=lang)
    if not messages:
        log.info("[prompt-suggestion] history too short sid=%s history_len=%d", sid, len(sess.history or []))
        return jsonify({"ok": False, "suggestion": ""})

    provider = sess.model_provider or config_manager.get_default_provider()
    cfg = config_manager.get_config(provider) if provider else None
    if not provider or cfg is None:
        log.warning("[prompt-suggestion] no provider/config sid=%s provider=%s cfg=%s", sid, provider, cfg)
        return jsonify({"ok": False, "suggestion": ""})

    log.debug("[prompt-suggestion] calling LLM sid=%s provider=%s model=%s msg_count=%d",
              sid, provider, cfg.model, len(messages))
    try:
        from LLM.llm_config_manager import get_llm_client
        client = get_llm_client(provider)
        response = _call_with_retry(
            client.chat.completions.create,
            model=cfg.model,
            messages=messages,
            temperature=0.25,
            max_tokens=80,
        )
        raw = response.choices[0].message.content if response.choices else ""
        log.debug("[prompt-suggestion] raw response sid=%s raw=%r", sid, raw[:300] if raw else "")
        suggestion = _sanitize_prompt_suggestion(raw)
        log.debug("[prompt-suggestion] sanitized sid=%s suggestion=%r", sid, suggestion[:200] if suggestion else "")
    except Exception as exc:
        log.warning("[prompt-suggestion] LLM call failed sid=%s error=%s", sid, exc)
        return jsonify({"ok": False, "suggestion": ""})

    log.info("[prompt-suggestion] result sid=%s ok=%s suggestion=%r", sid, bool(suggestion), suggestion[:100] if suggestion else "")
    return jsonify({"ok": bool(suggestion), "suggestion": suggestion})


# ── Chat SSE ───────────────────────────────────────────────────────────────

@bp.post("/api/session/<sid>/chat")
def chat_stream(sid: str):
    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        return jsonify({
            "error": "请求正文必须是 JSON 对象",
            "code": "invalid_request_body",
        }), 400
    raw_message = d.get("message")
    if raw_message is not None and not isinstance(raw_message, str):
        return jsonify({
            "error": "消息必须是字符串",
            "code": "invalid_message_type",
        }), 400
    message = (raw_message or "").strip()
    if not message:
        return jsonify({"error": "消息不能为空"}), 400

    sess = session_manager.get_or_create(sid)
    user_id = str(
        request.headers.get("X-BAA-User-ID")
        or d.get("user_id")
        or "local-default"
    ).strip()[:200]
    try:
        activation, active_skill, active_command = _resolve_activation(sess, d)
    except ActivationRequestError as exc:
        return jsonify({"error": str(exc), "code": exc.code}), 400
    if activation.kind == "none":
        added_tools = sess.record_discovered_tools(message)
        if added_tools:
            log.info("[tools] discovered sid=%s tools=%s", sid, added_tools)
    sess.cancel_requested = False
    _command_usage_before = (
        sess.total_input_tokens,
        sess.total_output_tokens,
        sess.total_cached_input_tokens,
    )
    data_context = _resolve_data_context(sess, d.get("data_context"))
    missing_sql_scope = _apply_sql_analysis_context(sess, data_context)
    if missing_sql_scope:
        return jsonify({
            "error": "请先在数据预览中为 SQL 数据库选择一张或多张分析表。",
            "code": "sql_table_selection_required",
            "sources": missing_sql_scope,
        }), 400
    from filehistory import FileHistoryError, for_session as file_history_for_session
    file_history = file_history_for_session(sid)
    file_history_snapshot_id = ""
    if file_history is not None:
        try:
            snapshot = file_history.begin_snapshot(message, sess.capture_rewind_state())
            file_history_snapshot_id = str(snapshot.get("id") or "")
        except FileHistoryError as exc:
            return jsonify({"error": str(exc), "code": "file_history_unavailable"}), 500
    conversation_job_id = sess.job_runner.begin_tracked(
        "conversation_analysis", label=message[:96],
    )
    conversation_job = sess.job_runner.get_status(conversation_job_id) or {}
    fixed_workspace_id = str(conversation_job.get("workspace_id") or "")
    from data.workspace import workspace_manager
    fixed_workspace_runtime = (
        workspace_manager.get_by_workspace(fixed_workspace_id)
        if fixed_workspace_id else None
    )
    try:
        source_snapshot = sess.acquire_data_source_snapshot()
    except Exception as exc:
        sess.job_runner.fail_tracked(
            conversation_job_id, f"Data source snapshot failed: {exc}",
        )
        if file_history is not None and file_history_snapshot_id:
            try:
                file_history.finalize_snapshot(file_history_snapshot_id, "failed")
            except FileHistoryError:
                log.exception("[filehistory] snapshot finalize failed sid=%s", sid)
        return jsonify({"error": f"无法固定当前数据源：{exc}"}), 500
    sess.record_activation(activation, message, conversation_job_id)
    sess.job_runner.append_tracked_event(conversation_job_id, {
        "type": "conversation_activation",
        "job_id": conversation_job_id,
        "activation": activation.to_record(),
    })

    from api.saved_sessions import _visible_msg_count
    _turn_start = time.monotonic()
    log.info("[chat] turn start  sid=%s  activation=%s:%r  history=%d msgs  msg=%.80r",
             sid, activation.kind, activation.name or "(none)",
             _visible_msg_count(sess.history), message)

    def _sse(obj) -> str:
        from agent.events import serialize_event
        return f"data: {json.dumps(serialize_event(obj), ensure_ascii=False)}\n\n"

    def generate():
        runner = sess.job_runner
        command_metric_recorded = False

        def _record_prompt_command_metric(outcome: str, error_code: str = "") -> None:
            nonlocal command_metric_recorded
            if command_metric_recorded or active_command is None:
                return
            command_metric_recorded = True
            sess.record_command_metric(
                command=active_command.name,
                command_type=active_command.type.value,
                outcome=outcome,
                duration_ms=int((time.monotonic() - _turn_start) * 1000),
                error_code=error_code,
                input_tokens=max(0, sess.total_input_tokens - _command_usage_before[0]),
                output_tokens=max(0, sess.total_output_tokens - _command_usage_before[1]),
                cached_input_tokens=max(
                    0,
                    sess.total_cached_input_tokens - _command_usage_before[2],
                ),
            )
        try:
            from agent.hooks.models import HookContext
            from data.hooks_store import load_engine

            hook_engine = load_engine()
            hook_context = HookContext(
                event_name="turn_start",
                session_id=sid,
                turn_id=conversation_job_id,
                workspace_id=fixed_workspace_id,
                workspace_name=(
                    fixed_workspace_runtime.to_dict().get("name", "")
                    if fixed_workspace_runtime is not None else ""
                ),
                workspace_path=(
                    fixed_workspace_runtime.to_dict().get("path", "")
                    if fixed_workspace_runtime is not None else ""
                ),
                message=message,
                model_provider=sess.model_provider or config_manager.get_default_provider() or "",
            )
        except Exception as exc:
            log.warning("[hooks] disabled for turn sid=%s error=%s", sid, exc)
            hook_engine = None
            hook_context = None

        try:
            agent = _build_agent(
                sess, workspace_id=fixed_workspace_id,
                source_snapshot=source_snapshot,
                hook_engine=hook_engine,
                hook_context=hook_context,
                user_id=user_id,
            )
        except ValueError as exc:
            log.error("[chat] build_agent failed  sid=%s  error=%s", sid, exc)
            runner.fail_tracked(conversation_job_id, str(exc))
            source_snapshot.release()
            _record_prompt_command_metric("error", "agent_build_failed")
            yield _sse({"type": "error", "message": str(exc)})
            yield _sse({"type": "done"})
            return

        collected: list[str] = []
        collected_reasoning: list[str] = []
        turn_chart_ids: list[str] = []
        completed_normally = False
        tool_calls_in_turn: list[str] = []
        step_count = 0
        pending_steps: dict[str, list[dict]] = {}
        artifact_signatures: set[str] = set()
        stream_error = ""

        def _append_parent_artifact(artifact: dict) -> None:
            if not isinstance(artifact, dict) or not artifact:
                return
            signature = json.dumps(artifact, ensure_ascii=False, sort_keys=True, default=str)
            if signature in artifact_signatures:
                return
            artifact_signatures.add(signature)
            runner.append_tracked_event(conversation_job_id, {
                "type": "artifact_created", "job_id": conversation_job_id,
                "artifact": artifact,
            })

        def _collect_downloads(content: str) -> None:
            for name, url in re.findall(r"\[([^\]]+)\]\((/api/output/[^)]+)\)", content or ""):
                _append_parent_artifact({
                    "type": "file", "name": name.replace("📥", "").strip(), "url": url,
                })

        def _append_tool_result_summary(event: dict) -> None:
            tool = str(event.get("tool") or "").strip()
            if not tool:
                return
            if event.get("artifacts"):
                return
            result_tools = {
                "query_data", "create_analysis_table", "delete_analysis_tables",
                "run_analysis", "profile_data", "clean_data",
                "export_excel", "export_report", "generate_ppt", "generate_dashboard",
                "workspace_read_file", "workspace_write_file", "workspace_edit_file",
                "workspace_delete_file", "workspace_move_file", "workspace_bash",
                "workspace_command", "task_create", "task_update", "team_create",
                "team_delete", "team_list", "team_status", "send_message", "agent_delegate",
            }
            if tool not in result_tools:
                return
            content = str(event.get("content") or "").strip()
            summary = str(event.get("summary") or "").strip()
            error = str(event.get("error") or "").strip()
            label = {
                "query_data": "query_data 查询结果",
                "create_analysis_table": "创建分析表结果",
                "delete_analysis_tables": "删除分析表结果",
                "run_analysis": "分析计算结果",
                "profile_data": "数据概况结果",
                "clean_data": "数据清洗结果",
                "export_excel": "Excel 导出结果",
                "export_report": "报告生成结果",
                "generate_ppt": "PPT 生成结果",
                "generate_dashboard": "看板生成结果",
            }.get(tool, f"{tool} 结果")
            if error:
                label = f"{label}（失败）"
            _append_parent_artifact({
                "type": "tool_result_summary",
                "tool": tool,
                "name": label,
                "summary": summary or content[:500],
                "ok": bool(event.get("ok", True)),
            })

        def _start_step(event: dict) -> None:
            nonlocal step_count
            step_count += 1
            tool = str(event.get("tool") or "unknown")
            step = {
                "step_id": f"step-{step_count}", "tool": tool,
                "display": event.get("display") or tool,
                "started_monotonic": time.monotonic(),
            }
            pending_steps.setdefault(tool, []).append(step)
            runner.append_tracked_event(conversation_job_id, {
                "type": "conversation_step_started", "job_id": conversation_job_id,
                "step_id": step["step_id"], "tool": tool,
                "display": step["display"], "step_number": step_count,
            })
            runner.update_tracked(
                conversation_job_id, min(95, step_count * 4),
                f"已执行 {step_count} 个步骤",
            )

        def _finish_step(tool: str, ok: bool = True, error: str = "", elapsed=None) -> None:
            queue = pending_steps.get(tool) or []
            if not queue:
                return
            step = queue.pop(0)
            duration = elapsed
            if duration is None:
                duration = time.monotonic() - step["started_monotonic"]
            runner.append_tracked_event(conversation_job_id, {
                "type": "conversation_step_finished", "job_id": conversation_job_id,
                "step_id": step["step_id"], "tool": tool,
                "display": step["display"], "step_number": int(step["step_id"].split("-")[-1]),
                "status": "succeeded" if ok else "failed",
                "elapsed_seconds": round(float(duration or 0), 3), "error": error or "",
            })

        # Every data-backed conversation exposes the same readable schema
        # snapshot without forcing the model to call get_schema each turn.
        schema_text = str(source_snapshot.combined_schema or "")
        if schema_text:
            from agent.tools.results import persist_large_tool_result
            _preview, schema_artifact, _budget = persist_large_tool_result(
                sid, "get_schema", schema_text,
                runtime=fixed_workspace_runtime, threshold=1, deduplicate=True,
            )
            if schema_artifact:
                schema_artifact["name"] = "get_schema 数据结构"
                sess.record_tool_audit({"recovery": {}, "artifacts": [schema_artifact]})
                _append_parent_artifact(schema_artifact)

        ppt_title       = d.get("ppt_title", "")
        ppt_slides      = d.get("ppt_slides") or []
        excel_tables    = d.get("excel_tables") or []
        excel_filename  = d.get("excel_filename", "")
        report_title    = d.get("report_title", "")
        report_sections = d.get("report_sections") or []
        dashboard_name    = d.get("dashboard_name", "")
        dashboard_widgets = d.get("dashboard_widgets") or []

        # Per-session temporary instruction — only injected when enabled.
        active_temp_prompt = (
            getattr(sess, "temp_prompt", "")
            if getattr(sess, "temp_prompt_enabled", False) else ""
        )
        if hook_engine and hook_context:
            for notification in hook_engine.run_hooks("user_prompt_submit", hook_context):
                yield _sse(notification.to_event())
            for notification in hook_engine.run_hooks("turn_start", hook_context):
                yield _sse(notification.to_event())
            hook_prompts = hook_engine.drain_prompt_messages()
            if hook_prompts:
                hook_prompt_text = "[Hook Prompt]\n" + "\n\n".join(hook_prompts)
                active_temp_prompt = (
                    f"{active_temp_prompt}\n\n{hook_prompt_text}"
                    if active_temp_prompt else hook_prompt_text
                )
        workspace_status = (
            {"mounted": True, **fixed_workspace_runtime.to_dict()}
            if fixed_workspace_runtime is not None else {"mounted": False}
        )
        recovery_context = sess.build_recovery_context(workspace_status)
        teams_enabled = bool(d.get("teams_enabled"))
        team_context = _build_team_context(sid, fixed_workspace_id) if teams_enabled else ""

        conversation_scope = runner.conversation_scope(conversation_job_id)
        conversation_scope.__enter__()
        try:
            for event in agent.run(
                message, list(sess.history), activation=activation,
                active_skill=active_skill, active_command=active_command,
                last_reasoning=getattr(sess, "last_reasoning", ""),
                last_prompt_tokens=getattr(sess, "last_prompt_tokens", 0),
                ppt_title=ppt_title, ppt_slides=ppt_slides,
                excel_tables=excel_tables, excel_filename=excel_filename,
                report_title=report_title, report_sections=report_sections,
                dashboard_name=dashboard_name, dashboard_widgets=dashboard_widgets,
                temp_prompt=active_temp_prompt,
                data_context=data_context,
                recovery_context=recovery_context,
                team_context=team_context,
                teams_enabled=teams_enabled,
                discovered_tools=frozenset(getattr(sess, "discovered_tools", []) or []),
                discovered_mcp_tools=list(
                    getattr(sess, "discovered_mcp_tools", []) or []
                ),
                mcp_catalog_version_seen=str(
                    getattr(sess, "mcp_catalog_version", "") or ""
                ),
                tool_result_artifacts=list(
                    getattr(sess, "recent_artifacts", []) or []
                ),
            ):
                if sess.cancel_requested:
                    log.info("[chat] cancelled by user  sid=%s", sid)
                    runner.cancel_tracked(conversation_job_id)
                    sess.cancel_requested = False
                    yield _sse({"type": "stopped"})
                    return

                etype = event.get("type")
                if etype == "tool_start":
                    _start_step(event)
                elif etype == "tool_audit":
                    sess.record_tool_audit(event)
                    _finish_step(
                        str(event.get("tool") or "unknown"), bool(event.get("ok", True)),
                        str(event.get("error") or ""), event.get("elapsed_seconds"),
                    )
                    for artifact in event.get("artifacts") or []:
                        _append_parent_artifact(artifact)
                    _append_tool_result_summary(event)
                    _collect_downloads(str(event.get("content") or ""))
                    # Recovery metadata is server-only; do not expose full SQL
                    # or future internal context fields through browser SSE.
                    event = {key: value for key, value in event.items() if key != "recovery"}
                elif etype == "tool_end":
                    _finish_step(str(event.get("tool") or "unknown"))
                elif etype == "artifact_created" and event.get("artifact"):
                    _append_parent_artifact(event["artifact"])
                elif etype == "error":
                    stream_error = str(event.get("message") or "Conversation failed")
                elif etype == "hook_event":
                    runner.append_tracked_event(conversation_job_id, {
                        "type": "hook_event",
                        "job_id": conversation_job_id,
                        "hook_id": event.get("hook_id", ""),
                        "event": event.get("event", ""),
                        "ok": bool(event.get("ok", True)),
                        "output": str(event.get("output") or "")[:500],
                    })

                # Provider/fallback safety net. BusinessAgent normally separates
                # <think> during streaming, but never let an embedded block leak
                # into the final answer if a compatibility path returns it whole.
                if etype == "text":
                    visible_text, embedded_reasoning = split_reasoning_tags(
                        event.get("content", "")
                    )
                    if embedded_reasoning:
                        collected_reasoning.append(embedded_reasoning)
                        yield _sse({"type": "reasoning", "content": embedded_reasoning})
                    event = {**event, "content": visible_text}

                if etype == "chart_html":
                    cid = uuid.uuid4().hex
                    chart_store[cid] = event["html"]
                    if not hasattr(sess, "chart_ids"):
                        sess.chart_ids = []
                    sess.chart_ids.append(cid)
                    turn_chart_ids.append(cid)
                    chart_title = str(
                        event.get("title")
                        or event.get("chart_type")
                        or f"图表 {len(turn_chart_ids)}"
                    ).strip()
                    chart_type = str(event.get("chart_type") or "").strip()
                    log.info("[chat] chart generated  sid=%s  chart_id=%s", sid, cid)
                    yield _sse({
                        "type": "chart_ref",
                        "chart_id": cid,
                        "title": chart_title,
                        "chart_type": chart_type,
                    })
                    _append_parent_artifact({
                        "type": "chart", "name": chart_title,
                        "chart_type": chart_type,
                        "url": f"/api/chart/{cid}", "chart_id": cid,
                    })
                elif etype == "chart_placeholder":
                    pass
                elif etype == "ppt_scheme":
                    sess.ppt_color_scheme = event.get("scheme", "mckinsey")
                elif etype == "usage":
                    sess.record_usage(
                        event.get("prompt_tokens", 0),
                        event.get("completion_tokens", 0),
                        breakdown=event.get("prompt_breakdown"),
                        cached_input_tokens=event.get("cached_input_tokens", 0),
                        cache_write_tokens=event.get("cache_write_tokens", 0),
                    )
                    cfg = config_manager.get_config(sess.model_provider)
                    enriched = {
                        **event,
                        "max_output_tokens": cfg.max_output_tokens if cfg else None,
                        "session_total_input":  sess.total_input_tokens,
                        "session_total_output": sess.total_output_tokens,
                    }
                    if not enriched.get("context_window"):
                        enriched["context_window"] = cfg.context_window if cfg else None
                    yield _sse(enriched)
                elif etype == "history_compacted":
                    compacted_history = event.get("history")
                    if isinstance(compacted_history, list):
                        sess.history = compacted_history
                    # Internal state mutation; the browser only needs the
                    # surrounding compaction activity events.
                else:
                    yield _sse(event)

                if etype == "text":
                    collected.append(event.get("content", ""))
                elif etype == "reasoning":
                    collected_reasoning.append(event.get("content", ""))
                elif etype == "tool_history":
                    msgs = event.get("messages", [])
                    sess.add_tool_messages(msgs)
                    names = [m.get("tool_calls", [{}])[0].get("function", {}).get("name", "")
                             for m in msgs if m.get("role") == "assistant" and m.get("tool_calls")]
                    tool_calls_in_turn.extend(n for n in names if n)
                elif etype == "tool_start":
                    pass  # already logged by agent.py

            for tool, queue in list(pending_steps.items()):
                while queue:
                    _finish_step(tool, ok=not bool(stream_error), error=stream_error)
            completed_normally = True
            sess.add_user(message)
            sess.add_assistant(
                "".join(collected),
                reasoning="".join(collected_reasoning),
                chart_ids=turn_chart_ids,
            )
            final_answer = "".join(collected)
            if hook_engine and hook_context:
                end_context = hook_context.child(
                    event_name="turn_end",
                    final_answer=final_answer,
                    elapsed_seconds=time.monotonic() - _turn_start,
                )
                for notification in hook_engine.run_hooks("turn_end", end_context):
                    yield _sse(notification.to_event())
            _collect_downloads(final_answer)
            if stream_error:
                runner.fail_tracked(conversation_job_id, stream_error)
            else:
                runner.succeed_tracked(conversation_job_id, {
                    "answer": final_answer,
                    "step_count": step_count,
                    "chart_ids": turn_chart_ids,
                    "activation": activation.to_record(),
                })

            elapsed = time.monotonic() - _turn_start
            reply_preview = "".join(collected)[:120].replace("\n", " ")
            log.info(
                "[chat] turn done  sid=%s  elapsed=%.2fs  tools=%s  charts=%d  "
                "total_in=%d  total_out=%d  reply=%.120r",
                sid, elapsed, tool_calls_in_turn or "none",
                len(turn_chart_ids), sess.total_input_tokens, sess.total_output_tokens,
                reply_preview,
            )

        except Exception as exc:
            log.exception("[chat] unhandled agent error  sid=%s", sid)
            runner.fail_tracked(conversation_job_id, f"{type(exc).__name__}: {exc}")
            if hook_engine and hook_context:
                error_context = hook_context.child(event_name="error", error=str(exc))
                for notification in hook_engine.run_hooks("error", error_context):
                    yield _sse(notification.to_event())
            yield _sse({"type": "error", "message": f"内部错误：{exc}"})

        finally:
            conversation_scope.__exit__(None, None, None)
            current = runner.get_status(conversation_job_id)
            if current and current.get("status") not in {"succeeded", "failed", "canceled"}:
                if sess.cancel_requested:
                    runner.cancel_tracked(conversation_job_id)
                    sess.cancel_requested = False
                elif not completed_normally:
                    runner.fail_tracked(conversation_job_id, "Conversation stream ended before completion.")
            if file_history is not None and file_history_snapshot_id:
                current = runner.get_status(conversation_job_id) or {}
                try:
                    file_history.finalize_snapshot(
                        file_history_snapshot_id,
                        str(current.get("status") or "interrupted"),
                    )
                except FileHistoryError:
                    log.exception("[filehistory] snapshot finalize failed sid=%s", sid)
            source_snapshot.release()
            current = runner.get_status(conversation_job_id) or {}
            status = str(current.get("status") or "")
            _record_prompt_command_metric(
                "success" if status == "succeeded" else "error",
                "" if status == "succeeded" else (status or "stream_incomplete"),
            )
            yield _sse({"type": "done"})

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )
