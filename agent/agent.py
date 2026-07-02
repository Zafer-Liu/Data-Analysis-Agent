#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Business Analyst Agent — main entry point.

The heavy lifting is split across:
  prompts.py             — base system prompt and path setup
  tools/schemas.py       — AGENT_TOOLS (JSON schemas sent to the LLM)
  tools/business/data.py — DataToolsMixin  (schema / query / analysis / chart / clean)
  tools/business/export.py — ExportToolsMixin (Excel / Word / PPT)
"""
import json
import logging
import time
import ast
import copy
from typing import Iterator, List, Dict, Any, Optional, Tuple

from .prompts      import (
    PromptContext,
    build_temp_prompt_section,
    get_system_prompt,
    message_needs_chart_rules,
    message_needs_hooks_rules,
    message_needs_knowledge,
    message_needs_workspace_rules,
    schema_has_unnamed_columns,
)
from .activation   import ActivationContext, INTERNAL_ACTIONS
from .commands     import (
    CommandDef, CommandDispatcher, CommandLoader, CommandRegistry,
)
from .skills       import SkillDef, SkillExecutor, SkillLoader
from .tools.schemas import AGENT_TOOLS, get_tools_with_mcp
from .tools.business import DataToolsMixin, ExportToolsMixin
from .tools.exposure import filter_tools_for_turn
from .tools.results import make_tool_result, read_tool_result_artifact
from .tools.parallel import should_parallelize_batch
from .tools.workspace import (
    WorkspaceBashService,
    WorkspaceTaskStore,
    WorkspaceTeamStore,
    WorkspaceToolService,
    structured_output,
)
from .tools.web import browse_webpage
from .tools.hooks_config import configure_hooks_from_agent
from .mcp_manager  import get_mcp_manager
from data.workspace import workspace_manager
from .compaction   import (
    adaptive_safety_margin,
    apply_tool_result_budget, compaction_circuit_open, compaction_threshold,
    estimate_payload_tokens_with_anchor, record_compaction_result,
    record_payload_usage,
    should_compact_history, compact_history,
    should_trim_history, trim_oversized_tool_results,
)
from .retry        import (
    call_with_retry as _call_with_retry,
    is_context_length_error as _is_context_length_error,
    is_retryable as _is_retryable,
)
from .validate     import (
    normalize_ask_user_args as _normalize_ask_user_args,
    validate_tool_args as _validate_tool_args,
)
from .reasoning    import ThinkTagStreamParser, split_reasoning_tags
from .hooks.models import HookContext
from .token_metrics import build_prompt_breakdown, finalize_prompt_breakdown
from .mcp_discovery import (
    build_mcp_catalog,
    mcp_catalog_version,
    search_mcp_catalog,
)
from LLM.llm_config_manager import (
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_MAX_OUTPUT_TOKENS,
)
from LLM.prompt_cache import (
    PromptCachePolicy,
    apply_prompt_cache_policy,
    stable_prompt_cache_key,
)
from .workflow_stage import (
    completed_tool_names,
    filter_tools_for_stage,
    infer_workflow_stage,
)
from .stage_compaction import compact_completed_stage_results

log = logging.getLogger(__name__)


_PROPOSE_CMDS = (
    "ppt", "ppt_revise", "export", "excel_revise",
    "report", "report_revise", "dashboard", "dashboard_revise",
)


def _as_bool_arg(value: Any) -> bool:
    """Coerce tool-call booleans without treating the string 'False' as true."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return False


def _normalize_chart_call_args(
    args: Dict[str, Any],
) -> Dict[str, Any]:
    """Make chart execution and UI metadata use the same non-empty values."""
    normalized = dict(args or {})
    chart_type = str(normalized.get("chart_type") or "Bar_Chart").strip()
    if not chart_type:
        chart_type = "Bar_Chart"
    title = str(normalized.get("title") or "").strip()
    if not title:
        title = f"数据可视化 · {chart_type.replace('_', ' ')}"
    normalized["chart_type"] = chart_type
    normalized["title"] = title[:120]
    return normalized


_REQUIRED_SUCCESSFUL_SKILL_TOOLS = {
    "regression": frozenset({"run_analysis"}),
}


def _missing_required_skill_tools(
    skill_name: str,
    successful_tools: set[str] | frozenset[str],
) -> tuple[str, ...]:
    """Return required Skill tools that have not completed successfully."""
    required = _REQUIRED_SUCCESSFUL_SKILL_TOOLS.get(
        str(skill_name or ""), frozenset(),
    )
    return tuple(sorted(required - set(successful_tools or ())))


def _missing_skill_response_contract(
    skill_name: str,
    content: str,
) -> tuple[str, ...]:
    """Validate deterministic final-answer obligations for selected Skills."""
    if str(skill_name or "") != "regression":
        return ()
    text = str(content or "").lower()
    requirements = {
        "相关方向（明确写出负相关/负线性）": (
            "负相关", "负线性", "negative correlation",
            "negative relationship",
        ),
        "显著性（说明显著或不显著，并给出 p 值口径）": (
            "显著", "p值", "p 值", "p-value", "p value",
        ),
        "非因果边界（明确相关不等于因果）": (
            "因果", "causal", "causality",
        ),
    }
    return tuple(
        label for label, tokens in requirements.items()
        if not any(token in text for token in tokens)
    )


def _skill_requires_text_only(
    skill_name: str,
    successful_tools: set[str] | frozenset[str],
) -> bool:
    """Stop ReAct tool use once a terminal analysis Skill has its result."""
    return (
        str(skill_name or "") == "regression"
        and not _missing_required_skill_tools(skill_name, successful_tools)
    )


def _remember_turn_tool_result_artifacts(
    allowed: list[dict],
    artifacts: list[dict] | tuple[dict, ...],
    *,
    session_id: str,
    limit: int = 20,
) -> None:
    """Authorize newly-created tool-result Artifacts for this turn only."""
    by_id = {
        str(item.get("artifact_id") or ""): dict(item)
        for item in allowed
        if isinstance(item, dict) and item.get("artifact_id")
    }
    for item in artifacts or ():
        if not isinstance(item, dict):
            continue
        artifact_id = str(item.get("artifact_id") or "")
        if (
            item.get("type") != "tool_result"
            or not artifact_id.startswith("tr_")
            or str(item.get("session_id") or "") != str(session_id or "")
        ):
            continue
        by_id[artifact_id] = dict(item)
    allowed[:] = list(by_id.values())[-max(1, int(limit)):]


def _scope_tool_result_reader(
    tools: list[dict],
    allowed_artifacts: list[dict],
) -> list[dict]:
    """Hide the Artifact reader until IDs exist, then enum-scope its input."""
    artifact_ids = list(dict.fromkeys(
        str(item.get("artifact_id") or "")
        for item in allowed_artifacts
        if isinstance(item, dict)
        and str(item.get("artifact_id") or "").startswith("tr_")
    ))
    scoped: list[dict] = []
    for schema in tools:
        name = str(((schema.get("function") or {}).get("name") or ""))
        if name != "read_tool_result":
            scoped.append(schema)
            continue
        if not artifact_ids:
            continue
        clone = copy.deepcopy(schema)
        properties = (
            clone.setdefault("function", {})
            .setdefault("parameters", {})
            .setdefault("properties", {})
        )
        properties.setdefault("artifact_id", {})["enum"] = artifact_ids[-20:]
        scoped.append(clone)
    return scoped


def _decode_tool_call_args(
    tool_name: str,
    raw_arguments: str,
) -> tuple[Dict[str, Any], str]:
    """Decode tool JSON without silently converting malformed calls to `{}`."""
    raw = raw_arguments or "{}"
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        return {}, (
            f"[ARG ERROR] '{tool_name}' arguments are invalid JSON: "
            f"{exc.msg if isinstance(exc, json.JSONDecodeError) else exc}"
        )
    if not isinstance(value, dict):
        return {}, f"[ARG ERROR] '{tool_name}' arguments must be a JSON object."
    return value, ""


def _sanitize_rejected_tool_call_history(
    assistant_entry: Dict[str, Any],
    tool_call_id: str,
) -> bool:
    """Keep rejected tool-call history provider-valid for the correction turn.

    The malformed call is never executed. Its paired tool result contains the
    explicit ARG ERROR; only the protocol field is normalized so compatible
    providers do not reject the next request before the model can self-correct.
    """
    for call in assistant_entry.get("tool_calls") or []:
        if str(call.get("id") or "") != str(tool_call_id or ""):
            continue
        function = call.get("function")
        if not isinstance(function, dict):
            return False
        function["arguments"] = "{}"
        return True
    return False


_SENSITIVE_TOOL_ARG_KEYS = frozenset({
    "api_key",
    "authorization",
    "client_secret",
    "password",
    "refresh_token",
    "access_token",
    "secret",
})


def _tool_detail_value(value: Any, *, key: str = "") -> Any:
    normalized_key = str(key or "").strip().lower().replace("-", "_")
    if (
        normalized_key in _SENSITIVE_TOOL_ARG_KEYS
        or normalized_key.endswith("_api_key")
        or normalized_key.endswith("_password")
        or normalized_key.endswith("_secret")
    ):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(item_key): _tool_detail_value(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_tool_detail_value(item) for item in value]
    if isinstance(value, str) and len(value) > 12_000:
        return value[:12_000] + "\n…[内容过长，已截断]"
    return value


def _format_tool_detail(
    tool_name: str,
    args: Dict[str, Any],
    summary: str,
) -> str:
    """Build an expanded tool view that is richer than its collapsed label."""
    safe_args = _tool_detail_value(dict(args or {}))
    if not safe_args:
        return str(summary or tool_name)
    try:
        serialized = json.dumps(
            safe_args,
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    except (TypeError, ValueError):
        serialized = str(safe_args)
    detail = (
        f"{summary}\n\n"
        f"工具：{tool_name}\n"
        f"完整参数：\n{serialized}"
    )
    if len(detail) > 30_000:
        detail = detail[:30_000] + "\n…[详情过长，已截断]"
    return detail


class BusinessAgent(DataToolsMixin, ExportToolsMixin):
    MAX_ITERATIONS = 120
    MAX_RUN_SECONDS = 600
    DELEGATED_MAX_TOOL_ROUNDS = 20
    DELEGATED_TIMEOUT_SECONDS = 300

    # Approximate chars-per-token ratio for fast estimation (conservative)
    _CHARS_PER_TOKEN = 3.5
    # Reserve headroom for the response + tools list (default for large windows)
    _CONTEXT_RESERVE = DEFAULT_MAX_OUTPUT_TOKENS

    def _context_reserve(self) -> int:
        """Headroom reserved for the response + tools list.

        The default reserves the market-standard maximum output budget. For a
        model configured with a smaller output cap, reserve only that cap.
        Invalid small-window combinations are bounded to 45% of the window.
        """
        window = self._get_context_window()
        requested = max(
            1_000,
            int(getattr(self, "_max_output_tokens", self._CONTEXT_RESERVE)),
        )
        return min(requested, max(1_000, int(window * 0.45)))

    def __init__(
        self,
        client,
        model: str,
        data_source=None,
        combined_schema: Optional[str] = None,
        all_sources: Optional[List] = None,
        merged_source=None,
        enable_thinking: bool = False,
        thinking_budget: int = 8000,
        chart_store: Optional[dict] = None,
        session_chart_ids: Optional[List[str]] = None,
        color_scheme: str = "mckinsey",
        session_id: str = "",
        workspace_id: Optional[str] = None,
        user_id: str = "",
        job_runner=None,
        context_window: Optional[int] = None,
        max_output_tokens: Optional[int] = None,
        hook_engine=None,
        hook_context: Optional[HookContext] = None,
        compaction_state: Optional[Dict[str, Any]] = None,
        provider: str = "",
        usage_recorder=None,
        mcp_discovery_recorder=None,
        supports_prompt_cache: bool = False,
        prompt_cache_mode: str = "none",
        prompt_cache_retention: str = "in_memory",
        cache_breakpoint_strategy: str = "stable_prefix",
    ):
        self.client = client
        self.model = model
        self.data_source = data_source
        # All active DataSource objects — used by _route_query for multi-source routing
        self._all_sources: List = all_sources if all_sources else ([data_source] if data_source else [])
        # MergedDataSource — single DuckDB connection covering all active sources.
        # When present, cross-source SQL (containing src{N}__ prefixes) is routed here.
        self._merged_source = merged_source
        # Pre-computed merged schema (multi-source); takes priority over single-source schema
        self._combined_schema: Optional[str] = combined_schema
        self.enable_thinking = enable_thinking
        self.thinking_budget = thinking_budget
        # User-configured context window (from the model config). When set, it
        # overrides the model-name heuristic so the compaction trigger and the
        # frontend context bar use the exact same number.
        self._configured_context_window: Optional[int] = (
            context_window if context_window and context_window > 0 else None
        )
        self._schema_cache: Optional[str] = None
        self._chart_store: dict = chart_store if chart_store is not None else {}
        self._session_chart_ids: List[str] = session_chart_ids if session_chart_ids is not None else []
        self.ppt_color_scheme: str = color_scheme
        self._session_id: str = session_id
        # C5: freeze workspace identity at Agent creation.  ``None`` keeps
        # compatibility with direct/test construction by snapshotting the
        # current session binding exactly once; an explicit empty string means
        # this turn was created without a mounted user workspace.
        self._workspace_id: str = (
            str(workspace_manager.workspace_id_for_session(session_id) or "")
            if workspace_id is None else str(workspace_id or "")
        )
        self._user_id: str = str(user_id or "").strip()[:200]
        self._knowledge_allowed_this_turn: bool = False
        self._job_runner = job_runner
        self._active_job_id: str = ""
        # Cap for a single LLM response. Defaults to the common 384K output;
        # caller should pass cfg.max_output_tokens so it matches the model's limit.
        self._max_output_tokens: int = (
            max_output_tokens
            if max_output_tokens and max_output_tokens > 0
            else DEFAULT_MAX_OUTPUT_TOKENS
        )
        self._mcp_manager = get_mcp_manager()
        self._hook_engine = hook_engine
        self._hook_context = hook_context or HookContext(event_name="")
        self._compaction_state = compaction_state if compaction_state is not None else {
            "consecutive_failures": 0,
            "last_failure_type": "",
            "circuit_open": False,
        }
        self._provider = str(provider or "")
        self._usage_recorder = usage_recorder
        self._mcp_discovery_recorder = mcp_discovery_recorder
        self._prompt_cache_policy = PromptCachePolicy(
            enabled=bool(supports_prompt_cache),
            mode=str(prompt_cache_mode or "none"),
            retention=str(prompt_cache_retention or "in_memory"),
            breakpoint_strategy=str(
                cache_breakpoint_strategy or "stable_prefix"
            ),
        )

    def _apply_prompt_cache(
        self,
        call_kwargs: Dict[str, Any],
        *,
        workflow_stage: str,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        exposed_tools = list(tools or [])
        cache_key = stable_prompt_cache_key(
            provider=getattr(self, "_provider", ""),
            model=self.model,
            workflow_stage=workflow_stage,
            tools=exposed_tools,
        )
        return apply_prompt_cache_policy(
            call_kwargs,
            policy=getattr(
                self,
                "_prompt_cache_policy",
                PromptCachePolicy(),
            ),
            cache_key=cache_key,
            user_id=getattr(self, "_user_id", ""),
            workspace_id=getattr(self, "_workspace_id", ""),
        )

    def _hook_tool_context(
        self,
        event_name: str,
        tool_name: str,
        args: Dict[str, Any],
        *,
        ok: Optional[bool] = None,
        error: str = "",
        elapsed_seconds: Optional[float] = None,
    ) -> HookContext:
        return self._hook_context.child(
            event_name=event_name,
            tool_name=tool_name,
            tool_args=dict(args or {}),
            tool_ok=ok,
            tool_error=error or "",
            elapsed_seconds=elapsed_seconds,
        )

    def _drain_hook_prompt_messages(self) -> list[str]:
        if not self._hook_engine:
            return []
        return self._hook_engine.drain_prompt_messages()

    def _hook_prompt_system_message(self, prompts: list[str]) -> Dict[str, str] | None:
        cleaned = [str(item).strip() for item in prompts if str(item or "").strip()]
        if not cleaned:
            return None
        return {
            "role": "system",
            "content": "[Hook Prompt]\n" + "\n\n".join(cleaned)[:8000],
        }

    def _run_post_tool_hooks(self, tool_name: str, args: Dict[str, Any], envelope) -> tuple[list[dict], list[str]]:
        if not self._hook_engine:
            return [], []
        ctx = self._hook_tool_context(
            "post_tool_use",
            tool_name,
            args,
            ok=bool(envelope.ok),
            error=str(envelope.error or ""),
            elapsed_seconds=envelope.debug.get("elapsed_seconds"),
        )
        notifications = [item.to_event() for item in self._hook_engine.run_hooks("post_tool_use", ctx)]
        prompts = self._drain_hook_prompt_messages()
        return notifications, prompts

    def _run_hook_event(self, event_name: str, **updates: Any) -> tuple[list[dict], list[str]]:
        if not self._hook_engine:
            return [], []
        ctx = self._hook_context.child(event_name=event_name, **updates)
        notifications = [item.to_event() for item in self._hook_engine.run_hooks(event_name, ctx)]
        prompts = self._drain_hook_prompt_messages()
        return notifications, prompts

    def _run_delegated_llm(
        self,
        *,
        member: dict,
        prompt: str,
        inbox_context: str = "",
        timeout_seconds: int = 300,
        max_tokens: int = 1600,
    ) -> dict:
        def _visible_text(text: str) -> str:
            visible, _reasoning = split_reasoning_tags(str(text or ""))
            return visible.strip()

        def _with_tool_footer(text: str) -> str:
            content = _visible_text(text)
            if used_tools:
                content = f"{content}\n\n---\n工具使用：{', '.join(used_tools)}".strip()
            return content

        output_tokens = max(400, min(4000, int(max_tokens or 1600), self._max_output_tokens))
        delegated_tools = self._delegated_tool_schemas()
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a bounded delegated analyst working as one member of a team. "
                    "You may use the provided read-only tools to inspect schemas, query data, "
                    "read relevant workspace files (including bounded Excel worksheet previews), "
                    "and search business knowledge. When workspace spreadsheets are registered "
                    "as data-source tables, prefer get_schema/query_data for quantitative work; "
                    "use workspace_read_file for direct sheet inspection or fallback. Do not "
                    "modify data, create teams, or ask the user questions. "
                    f"Role: {member.get('role', 'analyst')}. "
                    f"Instructions: {member.get('instructions', '')}\n"
                    "Return concise Markdown. Focus on the requested subtask only. "
                    "Do not restate all input data; provide concrete findings, risks, "
                    "and recommendations that the Leader can synthesize."
                ),
            },
            {"role": "user", "content": prompt + inbox_context},
        ]
        used_tools: list[str] = []
        tool_events: list[dict] = []
        last_content = ""
        for _delegated_iteration in range(self.DELEGATED_MAX_TOOL_ROUNDS):
            kwargs = {
                "model": self.model,
                "messages": messages,
                "stream": False,
                "temperature": 0.1,
                "max_tokens": output_tokens,
            }
            if delegated_tools:
                kwargs["tools"] = delegated_tools
                kwargs["tool_choice"] = "auto"
            kwargs, cache_metadata = self._apply_prompt_cache(
                kwargs,
                workflow_stage="team_delegate",
                tools=delegated_tools,
            )
            delegated_breakdown = build_prompt_breakdown(
                messages,
                delegated_tools,
                current_user_message=prompt + inbox_context,
                model=self.model,
                provider=getattr(self, "_provider", ""),
                iteration=_delegated_iteration + 1,
                activation_kind="team_delegate",
                chars_per_token=self._CHARS_PER_TOKEN,
            )
            delegated_breakdown.update({
                "prompt_cache_enabled": cache_metadata["enabled"],
                "prompt_cache_mode": cache_metadata["mode"],
                "prompt_cache_key": cache_metadata["cache_key"],
                "prompt_cache_retention": cache_metadata["retention"],
                "prompt_cache_scope_isolated": cache_metadata["scope_isolated"],
            })
            try:
                response = self.client.chat.completions.create(
                    **kwargs,
                    timeout=max(10, min(self.DELEGATED_TIMEOUT_SECONDS, int(
                        timeout_seconds or self.DELEGATED_TIMEOUT_SECONDS
                    ))),
                )
            except TypeError:
                kwargs.pop("timeout", None)
                response = self.client.chat.completions.create(**kwargs)
            delegated_usage = getattr(response, "usage", None)
            usage_recorder = getattr(self, "_usage_recorder", None)
            if delegated_usage is not None and usage_recorder is not None:
                finalized = finalize_prompt_breakdown(
                    delegated_breakdown, delegated_usage,
                )
                usage_recorder(
                    finalized["actual_prompt_tokens"],
                    finalized["actual_completion_tokens"],
                    breakdown=finalized,
                    cached_input_tokens=finalized["cached_input_tokens"],
                    cache_write_tokens=finalized["cache_write_tokens"],
                    update_last_prompt=False,
                )
            msg = response.choices[0].message
            last_content = getattr(msg, "content", None) or ""
            tool_calls = list(getattr(msg, "tool_calls", None) or [])
            if not tool_calls:
                return {"content": _with_tool_footer(last_content), "tool_events": tool_events}
            assistant_msg = {"role": "assistant", "content": last_content, "tool_calls": []}
            for index, call in enumerate(tool_calls[:4]):
                fn = getattr(call, "function", None)
                tool_name = str(getattr(fn, "name", "") or "")
                raw_args = getattr(fn, "arguments", "{}") or "{}"
                call_id = str(getattr(call, "id", "") or f"delegate_call_{index}")
                assistant_msg["tool_calls"].append({
                    "id": call_id,
                    "type": "function",
                    "function": {"name": tool_name, "arguments": raw_args},
                })
            messages.append(assistant_msg)
            for call in tool_calls[:4]:
                fn = getattr(call, "function", None)
                tool_name = str(getattr(fn, "name", "") or "")
                raw_args = getattr(fn, "arguments", "{}") or "{}"
                call_id = str(getattr(call, "id", "") or "delegate_call")
                try:
                    tool_args = json.loads(raw_args)
                except json.JSONDecodeError:
                    try:
                        tool_args = ast.literal_eval(raw_args)
                    except (ValueError, SyntaxError):
                        tool_args = {}
                if not isinstance(tool_args, dict):
                    tool_args = {}
                started = time.perf_counter()
                created_at = time.strftime("%Y-%m-%dT%H:%M:%S")
                try:
                    tool_text = self._execute_delegated_tool(tool_name, tool_args)
                    tool_status = "ok"
                except Exception as exc:
                    tool_text = f"Error: {exc}"
                    tool_status = "error"
                elapsed = max(0.0, time.perf_counter() - started)
                used_tools.append(tool_name)
                tool_events.append({
                    "tool": tool_name,
                    "args": tool_args,
                    "result": str(tool_text)[:2000],
                    "status": tool_status,
                    "elapsed_seconds": round(elapsed, 3),
                    "created_at": created_at,
                })
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": str(tool_text)[:8000],
                })
        final_content = ""
        if used_tools:
            final_messages = messages + [{
                "role": "user",
                "content": (
                    "工具轮数已经用完。不要再调用任何工具，不要输出 <think>。"
                    "请只基于上面的工具结果，直接输出该成员给 Leader 的最终 Markdown 结论："
                    "包括关键发现、数据依据、风险/限制、可执行建议。"
                    "如果数据不足，请明确说明不足，而不是继续请求查询。"
                ),
            }]
            final_kwargs = {
                "model": self.model,
                "messages": final_messages,
                "stream": False,
                "temperature": 0.1,
                "max_tokens": output_tokens,
            }
            final_kwargs, _final_cache_metadata = self._apply_prompt_cache(
                final_kwargs,
                workflow_stage="team_delegate_final",
                tools=[],
            )
            try:
                response = self.client.chat.completions.create(
                    **final_kwargs,
                    timeout=max(10, min(self.DELEGATED_TIMEOUT_SECONDS, int(
                        timeout_seconds or self.DELEGATED_TIMEOUT_SECONDS
                    ))),
                )
            except TypeError:
                final_kwargs.pop("timeout", None)
                response = self.client.chat.completions.create(**final_kwargs)
            except Exception as exc:
                log.warning("[team] delegated final synthesis failed: %s", exc)
            else:
                final_content = _visible_text(getattr(response.choices[0].message, "content", "") or "")
        if not final_content:
            final_content = (
                "成员工具调用已达到上限，未能生成完整最终总结。"
                "请 Leader 根据下方工具调用流程中的结果进行汇总。"
            )
        return {"content": _with_tool_footer(final_content), "tool_events": tool_events}

    def _delegated_tool_schemas(self) -> list[dict]:
        allowed = {
            "workspace_status",
            "workspace_glob",
            "workspace_grep",
            "workspace_read_file",
            "get_schema",
            "get_table_detail",
            "query_data",
            "query_knowledge",
            "select_chart",
            "profile_data",
        }
        schemas = [
            schema for schema in AGENT_TOOLS
            if ((schema.get("function") or {}).get("name") or "") in allowed
        ]
        if not self._knowledge_allowed_this_turn:
            schemas = [
                schema for schema in schemas
                if ((schema.get("function") or {}).get("name") or "")
                != "query_knowledge"
            ]
        return schemas

    def _execute_delegated_tool(self, name: str, args: dict) -> str:
        try:
            if name == "workspace_status":
                return self._tool_workspace_status()
            if name.startswith("workspace_"):
                ws_tools = WorkspaceToolService(self._session_id, workspace_id=self._workspace_id)
                if name == "workspace_glob":
                    return json.dumps(ws_tools.glob(
                        args.get("pattern", "**/*"),
                        args.get("path", ""),
                        args.get("max_results", 20),
                        args.get("cursor", 0),
                    ), ensure_ascii=False)
                if name == "workspace_grep":
                    return json.dumps(ws_tools.grep(
                        args.get("pattern", ""),
                        args.get("path", "."),
                        args.get("include", "*"),
                        args.get("max_results", 20),
                    ), ensure_ascii=False)
                if name == "workspace_read_file":
                    return json.dumps(ws_tools.read_file(
                        args.get("file_path", ""),
                        args.get("offset", 0),
                        args.get("limit", 120),
                        args.get("sheet_name", ""),
                    ), ensure_ascii=False)
            if name == "get_schema":
                return self._tool_get_schema()
            if name == "get_table_detail":
                return self._tool_get_table_detail(args.get("table_name", ""))
            if name == "query_data":
                return self._tool_query_data(args.get("sql", ""))
            if name == "query_knowledge":
                if not self._knowledge_allowed_this_turn:
                    return "Knowledge lookup is not allowed for this request."
                return self._tool_query_knowledge(args.get("question", ""))
            if name == "select_chart":
                return self._tool_select_chart(
                    args.get("user_intent", ""),
                    args.get("available_columns", []),
                )
            if name == "profile_data":
                return json.dumps(self._tool_profile_data(
                    args.get("table_name", ""),
                    args.get("columns"),
                ), ensure_ascii=False)
            return f"Unsupported delegated tool: {name}"
        except Exception as exc:
            return f"Delegated tool error [{name}]: {exc}"

    def _workspace_runtime(self):
        """Resolve only the runtime frozen for this Agent turn (C5)."""
        if not self._workspace_id:
            return None
        return workspace_manager.get_by_workspace(self._workspace_id)

    def _workspace_path_authorization(self):
        """Return the SQL sandbox capability for this Agent's fixed Workspace."""
        return workspace_manager.path_authorization(self._workspace_id)

    def _run_as_job(self, fn, job_type: str, label: str = ""):
        """Submit ``fn(ctx)`` and bridge its persisted events into Agent output.

        B2-B4 call this helper after their payload/module-level worker functions
        are introduced. Keeping the bridge here preserves the current
        tool-call -> result -> continued reasoning flow without letting workers
        touch the LLM client or SSE generator.
        """
        if self._job_runner is None:
            raise RuntimeError("JobRunner is not available for this session")
        jid = self._job_runner.create(fn, job_type=job_type, label=label)
        self._active_job_id = jid
        try:
            for event in self._job_runner.iter_events(jid):
                yield event
            job = self._job_runner.get_status(jid)
            if job is None:
                raise RuntimeError(f"job disappeared: {jid}")
            return job
        finally:
            current = self._job_runner.get_status(jid)
            if current is not None:
                from data.jobs_store import _TERMINAL
                if current["status"] not in _TERMINAL:
                    self._job_runner.cancel(jid)
            self._active_job_id = ""

    # ── Context helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max(1, int(len(text) / BusinessAgent._CHARS_PER_TOKEN))

    def _estimate_messages_tokens(self, messages: List[Dict]) -> int:
        total = 0
        for m in messages:
            content = m.get("content") or ""
            if isinstance(content, list):
                content = " ".join(str(c) for c in content)
            total += self._estimate_tokens(str(content))
            # tool_calls entries add overhead
            if m.get("tool_calls"):
                total += self._estimate_tokens(json.dumps(m["tool_calls"]))
        return total

    def _hard_prune(
        self, system_msgs: List[Dict], history: List[Dict], extra_msgs: List[Dict], context_window: int
    ) -> List[Dict]:
        """Hard truncation safety net: drop oldest messages until tokens fit.

        Two protections beyond a naive pop(0):
          1. A compaction summary message (tagged ``_compaction_summary``) is
             never dropped — it IS the compressed earlier context.
          2. After pruning, the surviving head is never an orphan ``role: tool``
             message; OpenAI requires every tool message to follow the
             assistant message that holds its tool_calls.
        """
        budget = context_window - self._context_reserve()
        fixed_tokens = self._estimate_messages_tokens(system_msgs + extra_msgs)
        available = budget - fixed_tokens

        pruned = list(history)
        before = len(pruned)

        # Pin a leading compaction-summary message so it survives pruning.
        pinned: List[Dict] = []
        if pruned and pruned[0].get("_compaction_summary"):
            pinned = [pruned.pop(0)]

        def _fits() -> bool:
            return self._estimate_messages_tokens(pinned + pruned) <= available

        while pruned and not _fits():
            pruned.pop(0)
            # Don't leave the head as an orphan tool message.
            while pruned and pruned[0].get("role") == "tool":
                pruned.pop(0)

        result = pinned + pruned
        if len(result) < before:
            log.info(
                "[context] hard-pruned %d→%d turns (budget=%d tokens)",
                before, len(result), available,
            )
        return result

    def _get_context_window(self) -> int:
        """Context window for the current model.

        Priority:
          1. User-configured value (cfg.context_window, set in 「模型设置」).
             This is the recommended path — it matches the frontend context bar
             exactly and requires no inference from the model name.
          2. Market-standard 1M fallback.

        Recommendation: always fill in the context window field when adding a
        custom model, so compaction triggers at the correct threshold.
        """
        if self._configured_context_window:
            return self._configured_context_window
        return DEFAULT_CONTEXT_WINDOW

    def _adaptive_thinking_budget(self, remaining_tokens: int) -> int:
        """Scale thinking budget so it never exceeds ~40% of what's left."""
        cap = int(remaining_tokens * 0.4)
        return max(1000, min(self.thinking_budget, cap))

    def set_data_source(self, source):
        self.data_source = source
        self._schema_cache = None

    def _tool_workspace_status(self) -> str:
        """Return a bounded summary of system roots and optional user workdir."""
        try:
            runtime = self._workspace_runtime()
            result = {
                "system_workspace": workspace_manager.system_status(),
                "user_workspace": {"mounted": runtime is not None},
                "usage": (
                    "When a user workspace is mounted, omit path (or use workspace://user) to search it first. "
                    "Use explicit path uploads, outputs, or mcp only when the user refers to those roots; "
                    "then use workspace_grep or workspace_read_file only for relevant files."
                ),
            }
            if runtime is not None:
                files = runtime.list_data_files(max_files=5)
                result["user_workspace"] = {
                    "mounted": True,
                    "uri": "workspace://user",
                    "workdir": str(runtime.workdir),
                    "artifacts_dir": str(runtime.artifacts_dir),
                    "recent_data_files": files,
                    "recent_files_truncated": len(files) >= 5,
                    "data_source_state": "mounted files are already registered as tables",
                }
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            log.warning("[agent] workspace_status failed: %s", e)
            return f"Unable to check workspace status: {e}"

    def _get_skill_def(self, name: str) -> SkillDef | None:
        runtime = self._workspace_runtime()
        loader = SkillLoader(
            workspace_dir=(runtime.workdir / ".baa" / "skills") if runtime else None,
        )
        return loader.load_all().get(name)

    # ── Agent loop ────────────────────────────────────────────────────────────

    def run(
        self,
        user_message: str,
        history: List[Dict],
        command: str = "",
        activation: ActivationContext | None = None,
        active_skill: SkillDef | None = None,
        active_command: CommandDef | None = None,
        last_reasoning: str = "",
        last_prompt_tokens: int = 0,
        ppt_title: str = "",
        ppt_slides: Optional[List] = None,
        excel_tables: Optional[List] = None,
        excel_filename: str = "",
        report_title: str = "",
        report_sections: Optional[List] = None,
        dashboard_name: str = "",
        dashboard_widgets: Optional[List] = None,
        temp_prompt: str = "",
        data_context: Optional[Dict] = None,
        recovery_context: str = "",
        team_context: str = "",
        teams_enabled: bool = False,
        discovered_tools: frozenset[str] | set[str] | None = None,
        discovered_mcp_tools: list[str] | tuple[str, ...] | None = None,
        mcp_catalog_version_seen: str = "",
        tool_result_artifacts: list[dict] | None = None,
    ) -> Iterator[Dict]:
        """
        Yields event dicts consumed by the Flask SSE stream:
          {"type": "tool_start",    "tool": str, "display": str}
          {"type": "text_delta",    "content": str}
          {"type": "chart_html",    "html": str}
          {"type": "text",          "content": str}
          {"type": "ppt_outline",   "title": str, "slides": list, "markdown": str}
          {"type": "excel_outline", "tables": list, "filename": str, "markdown": str}
          {"type": "report_outline","title": str, "sections": list, "markdown": str}
          {"type": "dashboard_outline","name": str, "widgets": list, "markdown": str}
          {"type": "usage",         ...}
          {"type": "reasoning",     "content": str}
          {"type": "done"}
          {"type": "error",         "message": str}
        """
        if activation is None:
            legacy = (command or "").strip()
            activation = ActivationContext(
                internal_action=legacy if legacy in INTERNAL_ACTIONS else "",
                command_name=legacy if legacy and legacy not in INTERNAL_ACTIONS else "",
            )
        if active_skill is not None and active_skill.name != activation.skill_name:
            raise ValueError("active Skill does not match activation context")
        if active_command is not None and active_command.name != activation.command_name:
            raise ValueError("active Command does not match activation context")

        command_name = activation.command_name
        action_name = activation.internal_action
        # Existing guarded business-flow branches use one policy token. It is
        # derived from typed activation rather than accepting a mixed namespace.
        command = command_name or action_name
        _msg_preview = user_message[:120].replace("\n", " ")
        log.info(
            "[run] activation=%s:%r  msg=%r  model=%s",
            activation.kind, activation.name or "(none)", _msg_preview, self.model,
        )

        # ── Confirm fast-paths: bypass LLM entirely ───────────────────────────
        if command == "ppt_confirm":
            slides = ppt_slides or []
            yield {"type": "tool_start", "tool": "generate_ppt",
                   "display": f"生成 PPT：{ppt_title}（{len(slides)} 张）..."}
            try:
                result = self._tool_generate_ppt(ppt_title, slides, "")
            except Exception as exc:
                yield {"type": "error", "message": f"PPT 生成失败: {exc}"}
                yield {"type": "done"}
                return
            yield {"type": "text", "content": result}
            yield {"type": "done"}
            return

        if command == "excel_confirm":
            tables = excel_tables or ["*"]
            yield {"type": "tool_start", "tool": "export_excel",
                   "display": f"导出 Excel → {', '.join(tables)[:50]}..."}
            try:
                result = self._tool_export_excel(tables=tables, filename=excel_filename)
            except Exception as exc:
                yield {"type": "error", "message": f"Excel 导出失败: {exc}"}
                yield {"type": "done"}
                return
            yield {"type": "text", "content": result}
            yield {"type": "done"}
            return

        if command == "report_confirm":
            sections = report_sections or []
            yield {"type": "tool_start", "tool": "export_report",
                   "display": f"生成报告：{report_title}（{len(sections)} 个章节）..."}
            try:
                result = yield from self._tool_export_report_with_jobs(
                    title=report_title, sections=sections,
                )
            except Exception as exc:
                yield {"type": "error", "message": f"报告生成失败: {exc}"}
                yield {"type": "done"}
                return
            yield {"type": "text", "content": result}
            yield {"type": "done"}
            return

        if command == "dashboard_confirm":
            widgets = dashboard_widgets or []
            yield {"type": "tool_start", "tool": "generate_dashboard",
                   "display": f"生成看板：{dashboard_name}（{len(widgets)} 个组件）..."}
            try:
                result = yield from self._tool_generate_dashboard_with_jobs(
                    name=dashboard_name, widgets=widgets
                )
            except Exception as exc:
                yield {"type": "error", "message": f"看板生成失败: {exc}"}
                yield {"type": "done"}
                return
            yield {"type": "text", "content": result}
            yield {"type": "done"}
            return


        # ── Data-source connectivity check ────────────────────────────────────
        # If the agent was built with data sources but no usable schema, it means
        # the connection is broken (e.g. server restart wiped in-memory state).
        # Fail fast with a clear message instead of letting the LLM silently
        # exhaust its turns and return an empty reply.
        _has_sources = bool(self._all_sources or self.data_source)
        _has_schema  = bool(
            getattr(self, "_combined_schema", None)
            or getattr(self, "_schema_cache", None)
        )
        if _has_sources and not _has_schema:
            # One last attempt: try to get the schema right now.
            try:
                _live_schema = self._tool_get_schema()
            except Exception:
                _live_schema = ""
            if not _live_schema or _live_schema == "No data source connected.":
                src_names = "、".join(
                    getattr(s, "name", "未知数据源") for s in self._all_sources
                ) if self._all_sources else "已连接数据源"
                yield {
                    "type": "error",
                    "message": (
                        f"数据源「{src_names}」的连接已断开（可能由服务重启引起），"
                        "请在侧边栏重新连接数据源后再试。"
                    ),
                }
                yield {"type": "done"}
                return

        _activation_prompt = ""
        skill_activation = None
        trusted_skill_name = ""
        if command_name:
            command_def = active_command
            if command_def is None:
                command_def = CommandLoader().load().get(command_name)
            if command_def is None:
                raise ValueError(f"unknown slash command: {command_name}")
            command_dispatch = CommandDispatcher(
                CommandRegistry((command_def,)),
            ).prepare_agent_turn(command_def.name, user_message)
            command_prompt = command_dispatch.prompt
            if command_prompt:
                _activation_prompt = (
                    f"[ACTIVE COMMAND: /{command_def.name}]\n{command_prompt}"
                )
        elif activation.skill_name:
            skill = active_skill or self._get_skill_def(activation.skill_name)
            if skill is None:
                raise ValueError(f"unknown analysis skill: {activation.skill_name}")
            skill_activation = SkillExecutor().activate(skill, user_message)
            # Only project-bundled Skills may unlock their audited proposal
            # tool. A workspace/user Skill with the same name cannot inherit it.
            if skill.source == "builtin":
                trusted_skill_name = skill.name
                if skill.name in {"export", "report", "ppt", "dashboard"}:
                    command = skill.name
            _activation_prompt = (
                f"[ACTIVE ANALYSIS SKILL: {skill.name}]\n"
                f"{skill_activation.prompt}"
            )
        _requested_tools = (
            set(skill_activation.requested_tools) if skill_activation else set()
        )
        _workspace_available = self._workspace_runtime() is not None
        _knowledge_relevant = message_needs_knowledge(user_message)
        self._knowledge_allowed_this_turn = _knowledge_relevant
        _kb_checked_this_turn = False
        _preflight_knowledge_msg: List[Dict] = []
        if _knowledge_relevant:
            _kb_checked_this_turn = True
            kb_question = user_message[:200]
            yield {
                "type": "tool_start",
                "tool": "query_knowledge",
                "display": f"查询知识库: {kb_question[:40]}",
                "detail": f"查询知识库: {kb_question}",
            }
            kb_result, kb_refs = self._tool_query_knowledge_with_refs(kb_question)
            yield {"type": "tool_end", "tool": "query_knowledge"}
            if kb_refs:
                yield {
                    "type": "knowledge_refs",
                    "refs": kb_refs,
                    "query": kb_question,
                }
            if (
                kb_result
                and kb_result != "No relevant knowledge found."
                and not kb_result.startswith("Knowledge base unavailable:")
            ):
                _preflight_knowledge_msg = [{
                    "role": "system",
                    "content": (
                        "[RETRIEVED BUSINESS KNOWLEDGE — TOP MATCHES]\n"
                        f"{kb_result[:3000]}\n"
                        "[END RETRIEVED BUSINESS KNOWLEDGE]\n"
                        "Use only relevant entries above; do not infer omitted "
                        "knowledge or contradict canonical metric definitions."
                    ),
                }]
        _output_tool_names = {
            "propose_ppt_outline", "generate_ppt",
            "propose_report_outline", "export_report",
            "propose_excel_export", "export_excel",
            "propose_dashboard_outline", "generate_dashboard",
        }
        _prompt_context = PromptContext(
            has_data_source=_has_sources,
            source_count=len(self._all_sources),
            has_workspace=_workspace_available,
            needs_workspace=(
                bool(_requested_tools.intersection({
                    "workspace_status", "workspace_glob", "workspace_grep",
                    "workspace_read_file",
                }))
                or message_needs_workspace_rules(
                    user_message, has_workspace=_workspace_available,
                )
            ),
            teams_enabled=teams_enabled,
            activation_kind=activation.kind,
            activation_name=activation.name,
            needs_chart=(
                bool(_requested_tools.intersection({"select_chart", "generate_chart"}))
                or message_needs_chart_rules(user_message)
            ),
            needs_output=(
                bool(_requested_tools.intersection(_output_tool_names))
                or command in _PROPOSE_CMDS
            ),
            needs_hooks=(
                bool(_requested_tools.intersection({"browse_webpage", "configure_hooks"}))
                or message_needs_hooks_rules(user_message)
            ),
            # This adds capability guidance only; no knowledge content is read.
            has_knowledge=True,
            has_unnamed_columns=schema_has_unnamed_columns(
                self._combined_schema or self._schema_cache or ""
            ),
        )
        system = get_system_prompt(_prompt_context)
        if _activation_prompt:
            system += f"\n\n{_activation_prompt}"
        # Per-session temporary instruction (user-set, this conversation only).
        if temp_prompt:
            system += build_temp_prompt_section(temp_prompt)
        if recovery_context:
            system += (
                "\n\n[RECOVERED ACTIVE CONTEXT]\n"
                + recovery_context[:6000]
                + "\n[END RECOVERED ACTIVE CONTEXT]"
            )
        if team_context:
            system += (
                "\n\n[CURRENT TEAMS CONTEXT]\n"
                + team_context[:5000]
                + "\n[END CURRENT TEAMS CONTEXT]"
            )
        if data_context:
            selected_tables = data_context.get("tables") or []
            table_lines = "\n".join(
                f"- data source '{item.get('source_name', '')}', table "
                f"'{item.get('table', '')}', SQL identifier "
                f"\"{item.get('query_table', item.get('table', ''))}\""
                for item in selected_tables
            )
            system += (
                "\n\n[CURRENT DATA PREVIEW CONTEXT]\n"
                "The user explicitly selected these tables in Data Preview:\n"
                f"{table_lines}\n"
                "Prefer these tables for ambiguous analysis requests and join them when the request "
                "requires combined fields. Use the exact SQL identifiers listed above. "
                "If the user's request clearly names another table or requires other tables, follow "
                "the request instead. Never claim the preview sample is the full dataset.\n"
                "[END CURRENT DATA PREVIEW CONTEXT]"
            )

        prior_reasoning_msg: List[Dict] = []
        if last_reasoning:
            # Truncate reasoning to a reasonable cap but keep it meaningful
            summary = last_reasoning[:2000]
            prior_reasoning_msg = [{
                "role": "system",
                "content": (
                    f"[Prior turn reasoning summary]\n{summary}\n"
                    "[End of prior reasoning — use this context to inform your analysis "
                    "but do not repeat or reference it explicitly to the user.]"
                ),
            }]

        _system_msg = {"role": "system", "content": system}
        _user_msg = {"role": "user", "content": user_message}
        _ctx_window = self._get_context_window()
        _turn_safety_margin = adaptive_safety_margin(self._compaction_state)

        # ── Rule-based trim before fixed-reserve compaction ──────────────────
        # Before considering semantic compaction, do a cheap pass that truncates
        # oversized tool result messages (large query outputs etc.).  This alone
        # is often enough to bring the context back under the compaction threshold.
        if should_trim_history(
            history=history,
            last_prompt_tokens=last_prompt_tokens,
            context_window=_ctx_window,
            chars_per_token=self._CHARS_PER_TOKEN,
            output_reserve=self._context_reserve(),
            safety_margin=_turn_safety_margin,
        ):
            history, _n_trimmed = trim_oversized_tool_results(history)
            if _n_trimmed:
                log.info("[trim] rule-based trim: shortened %d tool result(s)", _n_trimmed)

        # ── Semantic compaction (with frontend animation) ─────────────────────
        # Trigger口径与前端上下文条一致：使用上一轮真实 usage，并为模型
        # 输出和 P95 单轮增长保留固定空间。
        _needs_compact = should_compact_history(
            history=history,
            last_prompt_tokens=last_prompt_tokens,
            context_window=_ctx_window,
            chars_per_token=self._CHARS_PER_TOKEN,
            output_reserve=self._context_reserve(),
            safety_margin=_turn_safety_margin,
        ) and not compaction_circuit_open(self._compaction_state)
        if _needs_compact:
            # Report the larger of the two trigger signals for an honest %.
            from .compaction import _estimate_history_tokens
            _est = _estimate_history_tokens(history, self._CHARS_PER_TOKEN)
            _used = max(last_prompt_tokens or 0, _est)
            _pct = int(_used / _ctx_window * 100) if _ctx_window else 0
            hook_events, _hook_prompts = self._run_hook_event(
                "pre_compact",
                message=user_message,
                extra={"used_tokens": _used, "context_window": _ctx_window, "percent": _pct},
            )
            for hook_event in hook_events:
                yield hook_event
            yield {
                "type": "tool_start",
                "tool": "compaction",
                "display": "压缩对话历史…",
                "detail": f"上下文使用已达 {_pct}%，正在语义压缩以节省上下文空间",
            }
            _working_history, _compacted = compact_history(
                history=history,
                client=self.client,
                model=self.model,
            )
            record_compaction_result(
                self._compaction_state,
                success=_compacted,
                error_type="" if _compacted else "auto_compaction_failed",
            )
            yield {"type": "tool_end", "tool": "compaction"}
            yield {"type": "agent_activity", "message": "正在思考下一步…"}
            hook_events, _hook_prompts = self._run_hook_event(
                "post_compact",
                message=user_message,
                extra={"compacted": _compacted, "used_tokens": _used, "context_window": _ctx_window},
            )
            for hook_event in hook_events:
                yield hook_event
            log.info(
                "[compaction] trigger≈%d/%d tokens (%d%%) compacted=%s",
                _used, _ctx_window, _pct, _compacted,
            )
            # Push an immediate estimate so the frontend context bar reflects
            # the shrink right away — the precise value arrives later via the
            # real 'usage' event once this turn's LLM call returns.
            if _compacted:
                # Persist the compacted prior history. Without this event the
                # next user turn would summarize the same raw history again.
                yield {
                    "type": "history_compacted",
                    "history": _working_history,
                    "reason": "auto_threshold",
                }
                _est_after = _estimate_history_tokens(
                    _working_history, self._CHARS_PER_TOKEN
                ) + self._estimate_messages_tokens(
                    [_system_msg] + prior_reasoning_msg
                    + _preflight_knowledge_msg + [_user_msg]
                )
                yield {
                    "type": "context_estimate",
                    "prompt_tokens": _est_after,
                    "context_window": _ctx_window,
                    "estimated": True,
                }
        else:
            _working_history = history

        # ── Hard truncation safety net ────────────────────────────────────────
        _pruned_history = self._hard_prune(
            system_msgs=[_system_msg],
            history=_working_history,
            extra_msgs=prior_reasoning_msg + _preflight_knowledge_msg + [_user_msg],
            context_window=_ctx_window,
        )

        messages: List[Dict] = [
            _system_msg,
            *_pruned_history,
            *prior_reasoning_msg,
            *_preflight_knowledge_msg,
            _user_msg,
        ]
        # Track where this turn's new messages start (after system + history + user)
        _turn_start_idx = len(messages)
        _archived_turn_messages: List[Dict[str, Any]] = []
        _emergency_compaction_used = False
        _skip_auto_compact_once = False
        _allowed_tool_result_artifacts = [
            dict(item) for item in (tool_result_artifacts or ())
            if isinstance(item, dict) and item.get("artifact_id")
        ][-20:]

        try:
            _all_mcp_schemas = self._mcp_manager.get_all_openai_schemas()
        except Exception as exc:
            log.warning("[mcp-discovery] catalog unavailable: %s", exc)
            _all_mcp_schemas = []
        _mcp_catalog = build_mcp_catalog(_all_mcp_schemas)
        _mcp_catalog_version = mcp_catalog_version(_mcp_catalog)
        _connected_mcp_names = {item["name"] for item in _mcp_catalog}
        _turn_discovered_mcp = (
            [
                name for name in (discovered_mcp_tools or ())
                if name in _connected_mcp_names
            ]
            if str(mcp_catalog_version_seen or "") == _mcp_catalog_version
            else []
        )
        _pre_discovered_mcp = search_mcp_catalog(
            _mcp_catalog, user_message, limit=5,
        )
        for item in _pre_discovered_mcp:
            name = item["name"]
            if name in _turn_discovered_mcp:
                _turn_discovered_mcp.remove(name)
            _turn_discovered_mcp.append(name)
        _turn_discovered_mcp = _turn_discovered_mcp[-10:]
        _mcp_recorder = getattr(self, "_mcp_discovery_recorder", None)
        if _mcp_recorder is not None:
            _mcp_recorder(
                [item["name"] for item in _pre_discovered_mcp],
                _mcp_catalog_version,
            )

        pending_charts: List[Dict[str, str]] = []
        _successful_tool_names: set[str] = set()
        _last_missing_response_contract: tuple[str, ...] = ()
        _force_text_only = False
        all_reasoning: List[str] = []
        _consecutive_errors = 0
        _run_start = time.monotonic()
        _MAX_RUN_SECONDS = self.MAX_RUN_SECONDS
        _MAX_CONSECUTIVE_ERRORS = 3

        _PROPOSE_FLOW_CMDS = ("ppt", "ppt_revise", "export", "excel_revise",
                              "report", "report_revise", "dashboard", "dashboard_revise")

        _force_propose = False
        for _iteration in range(self.MAX_ITERATIONS):
            # ── Hard exit guards ──────────────────────────────────────────────
            if time.monotonic() - _run_start > _MAX_RUN_SECONDS:
                log.warning("[run] time limit reached (%.0fs)", _MAX_RUN_SECONDS)
                yield {"type": "text", "content": "分析超时，已终止。请尝试缩小问题范围后重试。"}
                yield {"type": "done"}
                return
            if _consecutive_errors >= _MAX_CONSECUTIVE_ERRORS:
                log.warning("[run] %d consecutive tool errors, aborting", _consecutive_errors)
                yield {"type": "text", "content": "连续工具调用失败，已终止。请检查数据源连接或简化查询。"}
                yield {"type": "done"}
                return

            if _force_propose:
                if command in ("ppt", "ppt_revise"):
                    nudge = (
                        "All data has been gathered in the tool results above. "
                        "Call propose_ppt_outline with a COMPLETE slides array (8–15 slides). "
                        "CRITICAL: use ONLY real numbers, labels, and values extracted from "
                        "the tool results in this conversation — do NOT fabricate or invent data. "
                        "Include: cover, toc, section_divider slides, at least 2 chart slides "
                        "(grouped_bar / donut / stacked_bar / timeline using the actual queried values), "
                        "and a closing slide. "
                        "Colors must be one of: NAVY, ACCENT_BLUE, ACCENT_GREEN, ACCENT_ORANGE, ACCENT_RED. "
                        "Output ONLY the tool call — no surrounding text."
                    )
                elif command in ("export", "excel_revise"):
                    nudge = (
                        "Call propose_excel_export now with the tables list and an optional filename. "
                        "Output ONLY the tool call — no surrounding text."
                    )
                elif command in ("dashboard", "dashboard_revise"):
                    nudge = (
                        "All data schema information has been gathered. "
                        "Call propose_dashboard_outline with a complete widgets array (2-6 widgets). "
                        "CRITICAL: use ONLY real table/column names from the schema — do NOT fabricate. "
                        "Each widget must have: title, chart_type, sql (valid SQL against the real tables), "
                        "field_mapping, and grid ({x,y,w,h}). "
                        "Output ONLY the tool call — no surrounding text."
                    )
                else:
                    nudge = (
                        "Compose the report outline from the conversation above and call "
                        "propose_report_outline with 4-6 concise sections. "
                        "Each section body must stay under 120 Chinese characters. "
                        "Submit an outline, not the full report text. "
                        "Output ONLY the tool call — no surrounding text."
                    )
                messages.append({"role": "user", "content": nudge})
                _force_propose = False
                _max_tokens = self._max_output_tokens
            else:
                _max_tokens = self._max_output_tokens

            _available_tools = filter_tools_for_turn(
                get_tools_with_mcp(
                    self._mcp_manager,
                    selected_mcp_tools=_turn_discovered_mcp[-5:],
                ),
                activation=activation,
                skill_allowed_tools=(
                    skill_activation.requested_tools if skill_activation else None
                ),
                trusted_skill=trusted_skill_name,
                user_message=user_message,
                discovered_tools=discovered_tools,
                has_data_source=_has_sources,
                has_workspace=_workspace_available,
                teams_enabled=teams_enabled,
                include_mcp=True,
                allowed_mcp_tools=frozenset(_turn_discovered_mcp[-5:]),
            )
            if _kb_checked_this_turn or not _knowledge_relevant:
                _available_tools = [
                    schema for schema in _available_tools
                    if ((schema.get("function") or {}).get("name") or "")
                    != "query_knowledge"
                ]
            _stage_context = infer_workflow_stage(
                activation=activation,
                user_message=user_message,
                has_data_source=_has_sources,
                needs_chart=_prompt_context.needs_chart,
                needs_output=_prompt_context.needs_output,
                completed_tools=completed_tool_names(
                    messages[_turn_start_idx:]
                ),
            )
            _available_tools = filter_tools_for_stage(
                _available_tools, _stage_context,
            )
            _available_tools = _scope_tool_result_reader(
                _available_tools, _allowed_tool_result_artifacts,
            )
            if _skill_requires_text_only(
                trusted_skill_name, _successful_tool_names,
            ):
                _force_text_only = True
            if _force_text_only:
                _available_tools = []
            messages, _stage_archive_stats = compact_completed_stage_results(
                messages,
                stage=_stage_context.stage,
                turn_start_idx=_turn_start_idx,
            )
            log.debug(
                "[workflow-stage] stage=%s tools=%d archived=%d saved_chars=%d",
                _stage_context.stage,
                len(_available_tools),
                _stage_archive_stats["archived"],
                _stage_archive_stats["saved_chars"],
            )

            # Phase 0B: prepare the actual payload before every ReAct model
            # call, not only once at the beginning of the user turn.
            messages, _budget_stats = apply_tool_result_budget(messages)
            _payload_tokens, _payload_signature, _used_usage_anchor = (
                estimate_payload_tokens_with_anchor(
                    messages,
                    _available_tools,
                    self._compaction_state,
                    chars_per_token=self._CHARS_PER_TOKEN,
                )
            )
            _turn_safety_margin = adaptive_safety_margin(self._compaction_state)
            _auto_threshold = compaction_threshold(
                _ctx_window,
                output_reserve=self._context_reserve(),
                safety_margin=_turn_safety_margin,
            )
            _skip_auto_for_this_call = _skip_auto_compact_once
            _skip_auto_compact_once = False
            if (
                _payload_tokens >= _auto_threshold
                and len(messages[1:]) >= 4
                and not _skip_auto_for_this_call
                and not compaction_circuit_open(self._compaction_state)
            ):
                yield {
                    "type": "tool_start",
                    "tool": "compaction",
                    "display": "压缩本轮上下文…",
                    "detail": (
                        f"本轮 ReAct Payload 约 {_payload_tokens:,} Token，"
                        "已接近模型上下文上限"
                    ),
                }
                _candidate_history, _did_compact = compact_history(
                    history=messages[1:],
                    client=self.client,
                    model=self.model,
                )
                record_compaction_result(
                    self._compaction_state,
                    success=_did_compact,
                    error_type="" if _did_compact else "react_compaction_failed",
                )
                yield {"type": "tool_end", "tool": "compaction"}
                if _did_compact:
                    _archived_turn_messages.extend(
                        message for message in messages[_turn_start_idx:]
                        if message.get("role") in {"assistant", "tool"}
                    )
                    messages = [messages[0], *_candidate_history]
                    _turn_start_idx = len(messages)
                    (
                        _payload_tokens,
                        _payload_signature,
                        _used_usage_anchor,
                    ) = estimate_payload_tokens_with_anchor(
                        messages, _available_tools, self._compaction_state,
                        chars_per_token=self._CHARS_PER_TOKEN,
                    )
                    yield {
                        "type": "context_estimate",
                        "prompt_tokens": _payload_tokens,
                        "context_window": _ctx_window,
                        "estimated": True,
                    }
                elif compaction_circuit_open(self._compaction_state):
                    yield {
                        "type": "agent_activity",
                        "message": "自动压缩连续失败，已暂停摘要并启用规则裁剪。",
                    }

            call_kwargs: Dict[str, Any] = dict(
                model=self.model,
                messages=messages,
                temperature=0.1,
                max_tokens=_max_tokens,
            )
            if _available_tools:
                call_kwargs["tools"] = _available_tools
                call_kwargs["tool_choice"] = "auto"
                if (
                    trusted_skill_name == "regression"
                    and "query_data" in _successful_tool_names
                    and "run_analysis" not in _successful_tool_names
                    and any(
                        ((schema.get("function") or {}).get("name") or "")
                        == "run_analysis"
                        for schema in _available_tools
                    )
                ):
                    call_kwargs["tool_choice"] = {
                        "type": "function",
                        "function": {"name": "run_analysis"},
                    }
            _prompt_breakdown = build_prompt_breakdown(
                messages,
                _available_tools,
                current_user_message=user_message,
                model=self.model,
                provider=self._provider,
                iteration=_iteration + 1,
                activation_kind=activation.kind,
                activation_name=activation.name,
                workflow_stage=_stage_context.stage,
                chars_per_token=self._CHARS_PER_TOKEN,
            )
            _prompt_breakdown["stage_archived_results"] = int(
                _stage_archive_stats["archived"]
            )
            _prompt_breakdown["stage_saved_chars"] = int(
                _stage_archive_stats["saved_chars"]
            )
            log.debug(
                "[tokens] iteration=%d payload≈%d system≈%d tools≈%d history≈%d",
                _iteration + 1,
                _prompt_breakdown["payload_tokens_est"],
                _prompt_breakdown["system_tokens_est"],
                _prompt_breakdown["tool_schema_tokens_est"],
                _prompt_breakdown["history_tokens_est"],
            )
            log.debug("[tools] exposed=%d command=%r has_data=%s",
                      len(_available_tools), command or "(none)", _has_sources)

            if self.enable_thinking and self.model.startswith("claude"):
                _ctx = self._get_context_window()
                _used = self._estimate_messages_tokens(messages)
                _remaining = max(4000, _ctx - _used)
                _budget = self._adaptive_thinking_budget(_remaining)
                call_kwargs["temperature"] = 1
                extra_body = dict(call_kwargs.get("extra_body") or {})
                extra_body["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": _budget,
                }
                call_kwargs["extra_body"] = extra_body
                log.debug("[thinking] budget=%d (remaining≈%d tokens)", _budget, _remaining)

            call_kwargs, _cache_metadata = self._apply_prompt_cache(
                call_kwargs,
                workflow_stage=_stage_context.stage,
                tools=_available_tools,
            )
            _prompt_breakdown.update({
                "prompt_cache_enabled": _cache_metadata["enabled"],
                "prompt_cache_mode": _cache_metadata["mode"],
                "prompt_cache_key": _cache_metadata["cache_key"],
                "prompt_cache_retention": _cache_metadata["retention"],
                "prompt_cache_scope_isolated": _cache_metadata["scope_isolated"],
            })

            # ── Streaming path ────────────────────────────────────────────────
            call_kwargs["stream"] = True
            call_kwargs["stream_options"] = {"include_usage": True}
            _t0 = time.monotonic()
            try:
                stream = _call_with_retry(self.client.chat.completions.create, **call_kwargs)
            except Exception as exc:
                log.error("[llm] API call failed after retries: %s", exc)
                if _is_context_length_error(exc) and not _emergency_compaction_used:
                    _emergency_compaction_used = True
                    yield {
                        "type": "tool_start",
                        "tool": "compaction",
                        "display": "上下文超限，正在紧急压缩…",
                        "detail": "模型拒绝了过长请求；压缩后将只重试一次。",
                    }
                    _candidate_history, _did_compact = compact_history(
                        history=messages[1:],
                        client=self.client,
                        model=self.model,
                    )
                    record_compaction_result(
                        self._compaction_state,
                        success=_did_compact,
                        error_type="" if _did_compact else "emergency_compaction_failed",
                    )
                    yield {"type": "tool_end", "tool": "compaction"}
                    if _did_compact:
                        _archived_turn_messages.extend(
                            message for message in messages[_turn_start_idx:]
                            if message.get("role") in {"assistant", "tool"}
                        )
                        messages = [messages[0], *_candidate_history]
                        _turn_start_idx = len(messages)
                        _skip_auto_compact_once = True
                        yield {
                            "type": "agent_activity",
                            "message": "紧急压缩完成，正在重试原请求…",
                        }
                        continue
                retryable, _ = _is_retryable(exc)
                if retryable:
                    yield {"type": "error", "message": f"LLM 服务暂时不可用，请稍后重试: {exc}"}
                else:
                    yield {"type": "error", "message": f"LLM 调用失败: {exc}"}
                yield {"type": "done"}
                return

            tc_acc: Dict[int, Dict[str, str]] = {}
            content_parts: List[str] = []
            reasoning_parts: List[str] = []
            think_parser = ThinkTagStreamParser()
            usage_data = None
            finish_reason = None

            for chunk in stream:
                if getattr(chunk, "usage", None):
                    usage_data = chunk.usage
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
                delta = choice.delta

                if delta.content:
                    visible_delta, tagged_reasoning = think_parser.feed(delta.content)
                    if visible_delta:
                        content_parts.append(visible_delta)
                        if command not in _PROPOSE_CMDS:
                            yield {"type": "text_delta", "content": visible_delta}
                    if tagged_reasoning:
                        reasoning_parts.append(tagged_reasoning)

                rc = getattr(delta, "reasoning_content", None)
                if rc:
                    reasoning_parts.append(rc)

                if delta.tool_calls:
                    for tcd in delta.tool_calls:
                        idx = tcd.index
                        if idx not in tc_acc:
                            tc_acc[idx] = {"id": "", "name": "", "args": ""}
                        if tcd.id:
                            tc_acc[idx]["id"] = tcd.id
                        if tcd.function:
                            if tcd.function.name:
                                tc_acc[idx]["name"] += tcd.function.name
                            if tcd.function.arguments:
                                tc_acc[idx]["args"] += tcd.function.arguments

            visible_tail, reasoning_tail = think_parser.finish()
            if visible_tail:
                content_parts.append(visible_tail)
                if command not in _PROPOSE_CMDS:
                    yield {"type": "text_delta", "content": visible_tail}
            if reasoning_tail:
                reasoning_parts.append(reasoning_tail)

            full_content = "".join(content_parts).strip()
            has_tool_calls = bool(tc_acc) and finish_reason == "tool_calls"
            reasoning_content = "".join(reasoning_parts) or None

            if usage_data:
                _elapsed = time.monotonic() - _t0
                _prompt_breakdown = finalize_prompt_breakdown(
                    _prompt_breakdown, usage_data,
                )
                record_payload_usage(
                    self._compaction_state,
                    _payload_signature,
                    prompt_tokens=usage_data.prompt_tokens,
                    completion_tokens=usage_data.completion_tokens,
                )
                self._compaction_state["last_usage"] = {
                    "prompt_tokens": int(usage_data.prompt_tokens or 0),
                    "completion_tokens": int(usage_data.completion_tokens or 0),
                    "estimated_payload_tokens": int(_payload_tokens),
                    "used_incremental_anchor": bool(_used_usage_anchor),
                    "safety_margin": int(_turn_safety_margin),
                    "recorded_at": time.time(),
                }
                log.info(
                    "[llm] stream done  finish=%s  in=%.0f out=%.0f  %.2fs",
                    finish_reason,
                    usage_data.prompt_tokens,
                    usage_data.completion_tokens,
                    _elapsed,
                )
                yield {
                    "type": "usage",
                    "prompt_tokens": usage_data.prompt_tokens,
                    "completion_tokens": usage_data.completion_tokens,
                    "total_tokens": usage_data.total_tokens,
                    "cached_input_tokens": _prompt_breakdown["cached_input_tokens"],
                    "cache_write_tokens": _prompt_breakdown["cache_write_tokens"],
                    "prompt_breakdown": _prompt_breakdown,
                    # context_window lets the frontend draw the context bar and
                    # keeps the % shown there consistent with the compaction
                    # trigger (both use _get_context_window()).
                    "context_window": _ctx_window,
                }

            class _F:
                def __init__(self, name, arguments):
                    self.name = name
                    self.arguments = arguments

            class _TC:
                def __init__(self, id_, name, arguments):
                    self.id = id_
                    self.function = _F(name, arguments)

            tc_objects = [
                _TC(v["id"], v["name"], v["args"])
                for _, v in sorted(tc_acc.items())
            ]

            # ── Dispatch tool calls ───────────────────────────────────────────
            if has_tool_calls:
                asst_entry: Dict[str, Any] = {
                    "role": "assistant",
                    "content": full_content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in tc_objects
                    ],
                }
                if reasoning_content:
                    asst_entry["reasoning_content"] = reasoning_content
                    all_reasoning.append(reasoning_content)
                messages.append(asst_entry)

                _outline_proposed = False

                # Guard: if any tool call is a blocked output tool, cancel the whole
                # tool-call batch and instruct the model to reply with plain text.
                _OUTPUT_TOOL_GUARDS = {
                    "propose_ppt_outline":       {"ppt", "ppt_revise"},
                    "generate_ppt":              {"ppt_confirm"},
                    "propose_report_outline":    {"report", "report_revise"},
                    "export_report":             {"report_confirm"},
                    "propose_excel_export":      {"export", "excel_revise"},
                    "export_excel":              {"excel_confirm"},
                    "propose_dashboard_outline": {"dashboard", "dashboard_revise"},
                    "generate_dashboard":        {"dashboard_confirm"},
                }
                blocked = [
                    tc for tc in tc_objects
                    if tc.function.name in _OUTPUT_TOOL_GUARDS
                    and command not in _OUTPUT_TOOL_GUARDS[tc.function.name]
                ]
                if blocked:
                    blocked_names = ", ".join(tc.function.name for tc in blocked)
                    log.warning("[tool] blocked output tool(s): %s (command=%r)", blocked_names, command)
                    # asst_entry (with reasoning_content if present) was already appended at line 540.
                    # Only append fake tool results so the model can continue.
                    for tc in tc_objects:
                        if tc.function.name in _OUTPUT_TOOL_GUARDS:
                            content = (
                                f"[SYSTEM BLOCK] '{tc.function.name}' requires a slash command. "
                                "Do NOT call output tools in regular chat. "
                                "Reply to the user in plain text, and suggest the relevant slash command if appropriate."
                            )
                        else:
                            content = "ok"
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": content,
                        })
                    continue  # next iteration — model will now reply in plain text

                _parsed_tools = []
                _hook_prompt_backlog: list[str] = []
                for tc in tc_objects:
                    name = tc.function.name
                    args, _decode_error = _decode_tool_call_args(
                        name, tc.function.arguments,
                    )
                    if _decode_error:
                        log.warning(
                            "[tool] %s: invalid JSON args=%r",
                            name, tc.function.arguments,
                        )
                        if name == "propose_report_outline":
                            _decode_error += (
                                "\nRetry with 4-6 concise section objects. "
                                "Keep each section content under 120 Chinese "
                                "characters and output only the tool call."
                            )
                        _sanitize_rejected_tool_call_history(
                            asst_entry, tc.id,
                        )
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": _decode_error,
                        })
                        yield {"type": "tool_end", "tool": name}
                        yield {
                            "type": "agent_activity",
                            "message": "工具参数格式错误，正在修正…",
                        }
                        _consecutive_errors += 1
                        continue
                    if name == "generate_chart":
                        args = _normalize_chart_call_args(args)
                    elif name == "ask_user":
                        args = _normalize_ask_user_args(args)

                    # Pre-dispatch validation: catch obviously bad args early
                    # A4：把 workspace 的 allowed_roots 传入，让 SQL 路径白名单生效
                    _ws_runtime = self._workspace_runtime()
                    _workspace_auth = self._workspace_path_authorization()
                    # A fixed but unavailable Workspace must fail closed instead
                    # of silently falling back to global uploads/Information.
                    _allowed_roots = [] if self._workspace_id and _workspace_auth is None else None
                    _val_err = _validate_tool_args(
                        name,
                        args,
                        allowed_roots=_allowed_roots,
                        workspace_authorization=_workspace_auth,
                    )
                    if _val_err:
                        log.warning("[tool] %s: arg validation failed: %s", name, _val_err)
                        # Inject as a synthetic tool result so the model can self-correct
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": f"[ARG ERROR] {_val_err}",
                        })
                        yield {"type": "tool_end", "tool": name}
                        yield {"type": "agent_activity", "message": "正在思考下一步…"}
                        _consecutive_errors += 1
                        continue
                    display_map = {
                        "query_knowledge":       f"查询知识库: {args.get('question', '')}",
                        "get_schema":            "读取数据结构",
                        "get_table_detail":      f"查看表结构: {args.get('table_name', '?')}",
                        "create_analysis_table": f"提取字段 → {args.get('table_name', 'analysis_data')}",
                        "select_chart":          f"查询图表注册表: {args.get('user_intent', '?')[:40]}",
                        "query_data":            f"执行查询: {args.get('sql', '')}",
                        "run_analysis":          f"运行分析: {args.get('analysis_name', '?')} · 目标列: {args.get('target_column', '?')}",
                        "generate_chart":        (
                            f"生成图表：{args.get('title')} "
                            f"（{args.get('chart_type')}）"
                        ),
                        "profile_data":          f"分析数据概况: {args.get('table_name', '自动检测')}",
                        "clean_data":            f"数据清洗 [{args.get('operation', '?')}]: {args.get('table_name', '自动检测')}",
                        "export_excel":          f"导出 Excel → {', '.join(args.get('tables', []))}",
                        "export_report":         f"生成 Word 报告: {args.get('title', '?')}",
                        "propose_excel_export":  f"预览 Excel 导出：{', '.join(args.get('tables', ['*']))}",
                        "propose_report_outline": f"生成报告大纲：{args.get('title', '?')}（{len(args.get('sections', []))} 章节）",
                        "propose_ppt_outline":   f"生成 PPT 大纲：{args.get('title', '?')} ({len(args.get('slides', []))} 张)",
                        "generate_ppt":          f"生成 PPT: {args.get('title', '?')} ({len(args.get('slides', []))} 张)",
                        "set_ppt_color_scheme":  f"切换配色方案 → {args.get('scheme', '?')}",
                        "propose_dashboard_outline": f"生成看板大纲：{args.get('name', '?')} ({len(args.get('widgets', []))} 个)",
                        "generate_dashboard":    f"生成看板：{args.get('name', '?')} ({len(args.get('widgets', []))} 个组件)",
                        "ask_user":              f"向用户提问：{args.get('question', '?')[:40]}",
                        "workspace_glob":        f"查找工作目录文件: {args.get('pattern', '**/*')}",
                        "workspace_grep":        f"搜索工作目录内容: {args.get('pattern', '')[:40]}",
                        "workspace_read_file":   f"读取工作目录文件: {args.get('file_path', '?')}",
                        "workspace_write_file":  f"写入工作目录文件: {args.get('file_path', '?')}",
                        "workspace_edit_file":   f"编辑工作目录文件: {args.get('file_path', '?')}",
                        "workspace_delete_file": f"删除工作目录文件: {args.get('file_path', '?')}",
                        "workspace_move_file":   f"移动工作目录文件: {args.get('source_path', '?')} → {args.get('destination_path', '?')}",
                        "workspace_bash":        f"执行受限工作目录命令: {args.get('command', '')[:80]}",
                        "workspace_command":     f"执行受控操作: {args.get('operation', '?')}",
                        "browse_webpage":        f"浏览网页: {args.get('url', '')[:70]}",
                        "configure_hooks":       "配置 Hooks 自动化",
                        "read_tool_result":      f"读取工具结果: {args.get('artifact_id', '?')}",
                        "structured_output":     "校验结构化输出",
                        "load_analysis_skill":  f"加载分析技能: {args.get('name', '?')}",
                        "task_create":          f"创建工作区任务: {args.get('title', '?')}",
                        "task_get":             f"查看工作区任务: {args.get('task_id', '?')}",
                        "task_list":            "列出工作区任务",
                        "task_update":          f"更新工作区任务: {args.get('task_id', '?')}",
                        "team_create":          f"创建分析团队: {args.get('name', '?')}",
                        "team_delete":          f"删除分析团队: {args.get('name', '?')}",
                        "team_list":            "列出分析团队",
                        "team_status":          f"查看分析团队状态: {args.get('name', '?')}",
                        "send_message":         f"发送团队消息: {args.get('recipient', '?')}",
                        "agent_delegate":       f"委派分析任务: {args.get('description', '')[:40]}",
                        "plan_complete":        "提交结构化计划",
                    }
                    full_display = display_map.get(name, name)
                    expanded_detail = _format_tool_detail(
                        name, args, full_display,
                    )
                    if self._hook_engine:
                        hook_events, hook_prompts = self._run_hook_event(
                            "tool_call",
                            tool_name=name,
                            tool_args=dict(args or {}),
                        )
                        for hook_event in hook_events:
                            yield hook_event
                        _hook_prompt_backlog.extend(hook_prompts)
                        rejected = self._hook_engine.run_pre_tool_hooks(
                            self._hook_tool_context("pre_tool_use", name, args)
                        )
                        for notification in self._hook_engine.drain_notifications():
                            yield notification.to_event()
                        _hook_prompt_backlog.extend(self._drain_hook_prompt_messages())
                        if rejected:
                            reason = str(rejected.reason or "tool call rejected by hook")
                            log.warning("[hooks] rejected tool=%s hook=%s reason=%s", name, rejected.hook_id, reason)
                            _tool_t0 = time.monotonic()
                            yield {
                                "type": "tool_start",
                                "tool": name,
                                "display": f"Hook 拦截: {name}",
                                "detail": reason,
                            }
                            envelope = make_tool_result(
                                name,
                                f"ERROR: Tool call rejected by hook {rejected.hook_id}: {reason}",
                                ok=False,
                                error=f"hook_rejected:{rejected.hook_id}",
                                debug={
                                    "elapsed_seconds": round(time.monotonic() - _tool_t0, 3),
                                    "args_preview": {k: str(v)[:80] for k, v in args.items() if k != "slides"},
                                    "hook_id": rejected.hook_id,
                                },
                                session_id=self._session_id,
                                runtime=self._workspace_runtime(),
                            )
                            yield {
                                "type": "tool_audit",
                                "tool": name,
                                "ok": False,
                                "error": envelope.error,
                                "summary": envelope.summary,
                                "content": str(envelope.data),
                                "sources": envelope.sources,
                                "artifacts": envelope.artifacts,
                                "elapsed_seconds": envelope.debug.get("elapsed_seconds"),
                                "args_preview": envelope.debug.get("args_preview", {}),
                            }
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": envelope.to_model_text(),
                            })
                            yield {"type": "tool_end", "tool": name}
                            yield {"type": "agent_activity", "message": "正在思考下一步…"}
                            _consecutive_errors += 1
                            continue
                    yield {
                        "type": "tool_start",
                        "tool": name,
                        "display": full_display[:60] + ("…" if len(full_display) > 60 else ""),
                        "detail": expanded_detail,
                    }
                    _parsed_tools.append((tc, name, args))

                if should_parallelize_batch(_parsed_tools):
                    from concurrent.futures import ThreadPoolExecutor, as_completed

                    def _run_parallel_tool(item):
                        tc, name, args = item
                        t0 = time.monotonic()
                        sources: list[dict] = []
                        artifacts: list[dict] = []
                        if name == "query_knowledge":
                            raw, refs = self._tool_query_knowledge_with_refs(
                                question=args.get("question", "")
                            )
                            sources = refs
                            events = [{
                                "type": "knowledge_refs",
                                "refs": refs,
                                "query": args.get("question", ""),
                            }]
                        elif name == "get_table_detail":
                            raw = self._tool_get_table_detail(
                                table_name=args.get("table_name", "")
                            )
                            events = []
                        elif name == "select_chart":
                            raw = self._tool_select_chart(
                                user_intent=args.get("user_intent", ""),
                                available_columns=args.get("available_columns", []),
                            )
                            events = []
                        elif name.startswith("mcp__"):
                            raw = self._mcp_manager.call_tool(name, args)
                            recorder = getattr(self, "_mcp_discovery_recorder", None)
                            if recorder is not None:
                                recorder([name], _mcp_catalog_version, used=True)
                            events = []
                        else:
                            raw = f"Unknown parallel tool: {name}"
                            events = []
                        envelope = make_tool_result(
                            name,
                            raw,
                            sources=sources,
                            artifacts=artifacts,
                            debug={
                                "elapsed_seconds": round(time.monotonic() - t0, 3),
                                "args_preview": {
                                    k: str(v)[:80] for k, v in args.items()
                                    if k != "slides"
                                },
                                "parallel": True,
                            },
                            session_id=self._session_id,
                            runtime=_ws_runtime,
                        )
                        return tc, name, envelope, events

                    with ThreadPoolExecutor(max_workers=min(4, len(_parsed_tools))) as ex:
                        futures = [ex.submit(_run_parallel_tool, item) for item in _parsed_tools]
                        parallel_results = [f.result() for f in as_completed(futures)]

                    result_by_id = {tc.id: (tc, name, env, events)
                                    for tc, name, env, events in parallel_results}
                    for tc, name, _args in _parsed_tools:
                        _tc, _name, envelope, events = result_by_id[tc.id]
                        _remember_turn_tool_result_artifacts(
                            _allowed_tool_result_artifacts,
                            envelope.artifacts,
                            session_id=self._session_id,
                        )
                        for event in events:
                            yield event
                        yield {
                            "type": "tool_audit",
                            "tool": name,
                            "ok": envelope.ok,
                            "error": envelope.error,
                            "summary": envelope.summary,
                            "content": str(envelope.data),
                            "sources": envelope.sources,
                            "artifacts": envelope.artifacts,
                            "elapsed_seconds": envelope.debug.get("elapsed_seconds"),
                            "args_preview": envelope.debug.get("args_preview", {}),
                            "parallel": True,
                        }
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": envelope.to_model_text(),
                        })
                        hook_events, hook_prompts = self._run_post_tool_hooks(name, _args, envelope)
                        for hook_event in hook_events:
                            yield hook_event
                        _hook_prompt_backlog.extend(hook_prompts)
                        yield {"type": "tool_end", "tool": name}
                        yield {"type": "agent_activity", "message": "正在思考下一步…"}
                    hook_msg = self._hook_prompt_system_message(_hook_prompt_backlog)
                    if hook_msg:
                        messages.append(hook_msg)
                    continue

                for tc, name, args in _parsed_tools:
                    _args_preview = {k: str(v)[:80] for k, v in args.items() if k != "slides"}
                    log.info("[tool] %s  args=%s", name, _args_preview)
                    _tool_t0 = time.monotonic()
                    tool_sources: list[dict] = []
                    tool_artifacts: list[dict] = []

                    # Mark KB as checked if the model explicitly called it
                    if name == "query_knowledge":
                        _kb_checked_this_turn = True

                    try:
                        if name == "select_chart":
                            tool_result = self._tool_select_chart(
                                user_intent=args.get("user_intent", ""),
                                available_columns=args.get("available_columns", []),
                            )
                        elif name == "query_knowledge":
                            tool_result, kb_refs = self._tool_query_knowledge_with_refs(
                                question=args.get("question", "")
                            )
                            tool_sources = kb_refs
                            yield {
                                "type": "knowledge_refs",
                                "refs": kb_refs,
                                "query": args.get("question", ""),
                            }
                        elif name == "get_schema":
                            tool_result = self._tool_get_schema()
                        elif name == "workspace_status":
                            tool_result = self._tool_workspace_status()
                        elif name == "get_table_detail":
                            tool_result = self._tool_get_table_detail(
                                table_name=args.get("table_name", "")
                            )
                        elif name == "create_analysis_table":
                            tool_result, tool_sources = yield from self._tool_create_analysis_table_with_jobs(
                                sql=args.get("sql", ""),
                                table_name=args.get("table_name", "analysis_data"),
                            )
                            yield {"type": "data_refs", "refs": tool_sources}
                        elif name == "delete_analysis_tables":
                            tool_result = self._tool_delete_analysis_tables(
                                table_names=args.get("table_names", []),
                                confirm=_as_bool_arg(args.get("confirm", False)),
                            )
                        elif name == "query_data":
                            tool_result, tool_sources = yield from self._tool_query_data_with_jobs(
                                args.get("sql", "")
                            )
                            yield {"type": "data_refs", "refs": tool_sources}
                        elif name == "run_analysis":
                            tool_result = yield from self._tool_run_analysis_with_jobs(
                                analysis_name=args.get("analysis_name", ""),
                                sql=args.get("sql", ""),
                                target_column=args.get("target_column", ""),
                                groupby_column=args.get("groupby_column", ""),
                                n_deciles=int(args.get("n_deciles", 10)),
                            )
                            tool_sources = self._data_refs_for_sql(
                                args.get("sql", ""), self.data_source, None
                            )
                            yield {"type": "data_refs", "refs": tool_sources}
                        elif name == "generate_chart":
                            tool_sources = self._data_refs_for_sql(
                                args.get("sql", ""), self.data_source, None
                            )
                            chart = yield from self._tool_generate_chart_with_jobs(
                                chart_type=args.get("chart_type", "Bar_Chart"),
                                sql=args.get("sql", ""),
                                field_mapping=args.get("field_mapping", {}),
                                title=args.get("title", ""),
                            )
                            if "html" in chart:
                                pending_charts.append({
                                    "html": chart["html"],
                                    "title": args["title"],
                                    "chart_type": args["chart_type"],
                                })
                                yield {
                                    "type": "chart_placeholder",
                                    "index": len(pending_charts) - 1,
                                }
                                tool_result = (
                                    f"Chart generated: {args['title']} "
                                    f"({args['chart_type']}). "
                                    "It is displayed to the user."
                                )
                                tool_artifacts = [{
                                    "type": "chart",
                                    "name": args["title"],
                                    "chart_type": args["chart_type"],
                                }]
                                yield {"type": "data_refs", "refs": tool_sources}
                            else:
                                tool_result = f"Chart failed: {chart.get('error', 'unknown')}"
                        elif name == "profile_data":
                            result = yield from self._tool_profile_data_with_jobs(
                                table_name=args.get("table_name", ""),
                                columns=args.get("columns", []),
                            )
                            for html in result.get("charts", []):
                                pending_charts.append({
                                    "html": html,
                                    "title": f"数据概况分布图 {len(pending_charts) + 1}",
                                    "chart_type": "profile",
                                })
                                yield {
                                    "type": "chart_placeholder",
                                    "index": len(pending_charts) - 1,
                                }
                            tool_result = result.get("text", "数据概况生成失败。")
                        elif name == "clean_data":
                            tool_result = yield from self._tool_clean_data_with_jobs(
                                operation=args.get("operation", ""),
                                table_name=args.get("table_name", ""),
                                columns=args.get("columns"),
                                fill_method=args.get("fill_method", "mean"),
                                lower_pct=float(args.get("lower_pct", 1)),
                                upper_pct=float(args.get("upper_pct", 99)),
                                trim_column=args.get("trim_column", ""),
                                min_val=args.get("min_val"),
                                max_val=args.get("max_val"),
                                output_table=args.get("output_table", "cleaned_data"),
                            )
                        elif name == "export_excel":
                            tool_result = self._tool_export_excel(
                                tables=args.get("tables", []),
                                filename=args.get("filename", ""),
                            )
                        elif name == "export_report":
                            tool_result = yield from self._tool_export_report_with_jobs(
                                title=args.get("title", "分析报告"),
                                sections=args.get("sections", []),
                            )
                        elif name == "propose_excel_export":
                            result = self._tool_propose_excel_export(
                                tables=args.get("tables", ["*"]),
                                filename=args.get("filename", ""),
                                summary=args.get("summary", ""),
                            )
                            yield {
                                "type": "excel_outline",
                                "tables": result["tables"],
                                "filename": result["filename"],
                                "markdown": result["markdown"],
                            }
                            tool_result = "导出计划已展示给用户，等待其通过按钮确认或修改。请不要输出任何文字。"
                            _outline_proposed = True
                        elif name == "propose_report_outline":
                            result = self._tool_propose_report_outline(
                                title=args.get("title", "分析报告"),
                                sections=args.get("sections", []),
                            )
                            yield {
                                "type": "report_outline",
                                "title": result["title"],
                                "sections": result["sections"],
                                "markdown": result["markdown"],
                            }
                            tool_result = "报告大纲已展示给用户，等待其通过按钮确认或修改。请不要输出任何文字。"
                            _outline_proposed = True
                        elif name == "set_ppt_color_scheme":
                            tool_result = self._tool_set_ppt_color_scheme(
                                scheme=args.get("scheme", "mckinsey"),
                            )
                            yield {
                                "type": "ppt_scheme",
                                "scheme": self.ppt_color_scheme,
                            }
                        elif name == "propose_ppt_outline":
                            _ppt_slides = args.get("slides", [])
                            if not _ppt_slides:
                                tool_result = (
                                    "ERROR: slides array is empty. You MUST provide a "
                                    "slides array with 8-15 slide objects. Re-read the "
                                    "query results above and call propose_ppt_outline "
                                    "again with a complete slides list."
                                )
                            else:
                                result = self._tool_propose_ppt_outline(
                                    title=args.get("title", "演示文稿"),
                                    slides=_ppt_slides,
                                )
                                yield {
                                    "type": "ppt_outline",
                                    "title": result["title"],
                                    "slides": result["slides"],
                                    "markdown": result["markdown"],
                                }
                                tool_result = "大纲已展示给用户，等待其通过按钮确认或修改。"
                                _outline_proposed = True
                        elif name == "generate_ppt":
                            tool_result = self._tool_generate_ppt(
                                title=args.get("title", "Presentation"),
                                slides=args.get("slides", []),
                                filename=args.get("filename", ""),
                            )
                        elif name == "propose_dashboard_outline":
                            _dash_widgets = args.get("widgets", [])
                            if not _dash_widgets:
                                tool_result = (
                                    "ERROR: widgets array is empty. You MUST provide a "
                                    "widgets array with at least 1 widget object. Re-read "
                                    "the data schema and call propose_dashboard_outline "
                                    "again with a complete widgets list."
                                )
                            elif self.data_source is None:
                                result = self._tool_propose_dashboard_outline(
                                    name=args.get("name", "数据看板"),
                                    widgets=_dash_widgets,
                                )
                                yield {
                                    "type": "dashboard_outline",
                                    "name": result["name"],
                                    "widgets": result["widgets"],
                                    "markdown": result["markdown"],
                                }
                                tool_result = "看板大纲已展示给用户，等待其通过按钮确认或修改。请不要输出任何文字。"
                                _outline_proposed = True
                            else:
                                # Validate every widget SQL before showing outline
                                _sql_errors = []
                                for _w in _dash_widgets:
                                    _wsql = _w.get("sql", "").strip()
                                    if not _wsql:
                                        _sql_errors.append(
                                            f"Widget '{_w.get('title', '?')}' has empty SQL."
                                        )
                                        continue
                                    _guard_error = _validate_tool_args(
                                        "query_data",
                                        {"sql": _wsql},
                                        allowed_roots=(
                                            [] if self._workspace_id
                                            and self._workspace_path_authorization() is None
                                            else None
                                        ),
                                        workspace_authorization=self._workspace_path_authorization(),
                                    )
                                    if _guard_error:
                                        _sql_errors.append(
                                            f"Widget '{_w.get('title', '?')}': {_guard_error}"
                                        )
                                        continue
                                    # Wrap in a subquery with LIMIT 1 to keep validation cheap
                                    _test_sql = (
                                        f"SELECT * FROM ({_wsql}) AS __val__ LIMIT 1"
                                    )
                                    try:
                                        _df, _err = self.data_source.execute_query(_test_sql)
                                    except Exception as _exc:
                                        _err = str(_exc)
                                    if _err:
                                        _sql_errors.append(
                                            f"Widget '{_w.get('title', '?')}': {_err}"
                                        )
                                if _sql_errors:
                                    _real_schema = self._tool_get_schema() if hasattr(self, "_tool_get_schema") else ""
                                    tool_result = (
                                        "ERROR: The following widget SQL queries are invalid — "
                                        "they reference tables or columns that do NOT exist in "
                                        "the actual data source. You MUST fix them and call "
                                        "propose_dashboard_outline again.\n\n"
                                        "FAILED QUERIES:\n"
                                        + "\n".join(f"  - {e}" for e in _sql_errors)
                                        + (f"\n\nREAL SCHEMA:\n{_real_schema}" if _real_schema else "")
                                    )
                                else:
                                    result = self._tool_propose_dashboard_outline(
                                        name=args.get("name", "数据看板"),
                                        widgets=_dash_widgets,
                                    )
                                    yield {
                                        "type": "dashboard_outline",
                                        "name": result["name"],
                                        "widgets": result["widgets"],
                                        "markdown": result["markdown"],
                                    }
                                    tool_result = "看板大纲已展示给用户，等待其通过按钮确认或修改。请不要输出任何文字。"
                                    _outline_proposed = True
                        elif name == "generate_dashboard":
                            tool_result = yield from self._tool_generate_dashboard_with_jobs(
                                name=args.get("name", "数据看板"),
                                widgets=args.get("widgets", []),
                                color_scheme=args.get("color_scheme", ""),
                            )
                        elif name == "ask_user":
                            yield {
                                "type": "ask_user",
                                "question": args.get("question", ""),
                                "options": args.get("options", []),
                                "multi_select": _as_bool_arg(args.get("multi_select", False)),
                            }
                            tool_result = "问题已展示给用户，等待用户回答后继续。请不要输出任何文字。"
                            _outline_proposed = True
                        elif name == "browse_webpage":
                            tool_result = browse_webpage(
                                args.get("url", ""),
                                max_chars=int(args.get("max_chars", 12000) or 12000),
                            )
                        elif name == "configure_hooks":
                            tool_result = configure_hooks_from_agent(
                                args.get("settings"),
                                merge=_as_bool_arg(args.get("merge", True)),
                                reason=args.get("reason", ""),
                                confirm_command_hooks=_as_bool_arg(
                                    args.get("confirm_command_hooks", False)
                                ),
                            )
                        elif name == "read_tool_result":
                            tool_result = read_tool_result_artifact(
                                args.get("artifact_id", ""),
                                allowed_artifacts=_allowed_tool_result_artifacts,
                                session_id=self._session_id,
                                workspace_id=self._workspace_id,
                                runtime=_ws_runtime,
                                workspace_root=(
                                    workspace_manager.root_for_workspace(
                                        self._workspace_id
                                    )
                                    if self._workspace_id else None
                                ),
                                offset=args.get("offset", 0),
                                limit=args.get("limit", 4000),
                                query=args.get("query", ""),
                            )
                        elif name == "search_mcp_tools":
                            matches = search_mcp_catalog(
                                _mcp_catalog,
                                args.get("query", ""),
                                server=args.get("server", ""),
                                limit=args.get("limit", 5),
                            )
                            for item in matches:
                                discovered_name = item["name"]
                                if discovered_name in _turn_discovered_mcp:
                                    _turn_discovered_mcp.remove(discovered_name)
                                _turn_discovered_mcp.append(discovered_name)
                            _turn_discovered_mcp[:] = _turn_discovered_mcp[-10:]
                            recorder = getattr(self, "_mcp_discovery_recorder", None)
                            if recorder is not None:
                                recorder(
                                    [item["name"] for item in matches],
                                    _mcp_catalog_version,
                                )
                            tool_result = {
                                "query": args.get("query", ""),
                                "catalog_version": _mcp_catalog_version,
                                "matches": matches,
                                "hint": (
                                    "Matched tools will be available on the next model step."
                                    if matches else
                                    "No connected MCP tool matched this query."
                                ),
                            }
                        elif name.startswith("workspace_"):
                            ws_tools = WorkspaceToolService(
                                self._session_id, workspace_id=self._workspace_id,
                            )
                            if name == "workspace_glob":
                                tool_result = ws_tools.glob(
                                    args.get("pattern", "**/*"), args.get("path", ""),
                                    args.get("max_results", 20), args.get("cursor", 0),
                                )
                            elif name == "workspace_grep":
                                tool_result = ws_tools.grep(
                                    args.get("pattern", ""), args.get("path", "."),
                                    args.get("include", "*"), args.get("max_results", 20),
                                )
                            elif name == "workspace_read_file":
                                tool_result = ws_tools.read_file(
                                    args.get("file_path", ""),
                                    args.get("offset", 0),
                                    args.get("limit", 200),
                                    args.get("sheet_name", ""),
                                )
                            elif name == "workspace_write_file":
                                tool_result = ws_tools.write_file(args.get("file_path", ""), args.get("content", ""))
                            elif name == "workspace_edit_file":
                                tool_result = ws_tools.edit_file(
                                    args.get("file_path", ""), args.get("old_string", ""), args.get("new_string", "")
                                )
                            elif name == "workspace_delete_file":
                                tool_result = ws_tools.delete_file(
                                    args.get("file_path", ""), confirm=_as_bool_arg(args.get("confirm", False))
                                )
                            elif name == "workspace_move_file":
                                tool_result = ws_tools.move_file(
                                    args.get("source_path", ""), args.get("destination_path", ""),
                                    confirm_overwrite=_as_bool_arg(args.get("confirm_overwrite", False)),
                                )
                            elif name == "workspace_bash":
                                tool_result = WorkspaceBashService(
                                    self._session_id, workspace_id=self._workspace_id,
                                ).execute(
                                    args.get("command", ""), args.get("timeout", 30),
                                    confirm=_as_bool_arg(args.get("confirm", False)),
                                )
                            elif name == "workspace_command":
                                tool_result = ws_tools.command(
                                    args.get("operation", ""), args.get("path", "."),
                                    timeout=args.get("timeout", 30),
                                )
                            else:
                                tool_result = "Unknown workspace tool"
                        elif name == "structured_output":
                            tool_result = structured_output(args.get("output"), args.get("required_fields"))
                        elif name == "load_analysis_skill":
                            skill = self._get_skill_def(args.get("name", ""))
                            tool_result = (
                                {"name": skill.name, "description": skill.description, "prompt": skill.prompt}
                                if skill else "ERROR: unknown analysis skill"
                            )
                        elif name.startswith("task_"):
                            task_store = WorkspaceTaskStore(
                                self._session_id, workspace_id=self._workspace_id,
                            )
                            if name == "task_create":
                                tool_result = task_store.create(
                                    args.get("title", ""), args.get("description", ""), args.get("assignee", ""),
                                    args.get("blocks"), args.get("blocked_by"),
                                )
                            elif name == "task_get":
                                tool_result = task_store.get(args.get("task_id", ""))
                            elif name == "task_list":
                                tool_result = task_store.list(args.get("status", ""), args.get("assignee", ""))
                            elif name == "task_update":
                                tool_result = task_store.update(
                                    args.get("task_id", ""), status=args.get("status"),
                                    assignee=args.get("assignee"), description=args.get("description"),
                                    add_blocks=args.get("add_blocks"), add_blocked_by=args.get("add_blocked_by"),
                                )
                        elif name in {
                            "team_create", "team_delete", "team_list", "team_status",
                            "send_message", "agent_delegate", "team_delegate",
                        }:
                            team_store = WorkspaceTeamStore(
                                self._session_id, workspace_id=self._workspace_id,
                            )
                            if name == "team_create":
                                tool_result = team_store.create(
                                    args.get("name", ""), args.get("description", ""), args.get("members", [])
                                )
                                yield {
                                    "type": "team_event",
                                    "event": "team_created",
                                    "team": tool_result.get("name", args.get("name", "")),
                                    "scope": getattr(team_store, "scope", ""),
                                    "team_status": tool_result,
                                }
                            elif name == "team_delete":
                                tool_result = team_store.delete(args.get("name", ""))
                                yield {
                                    "type": "team_event",
                                    "event": "team_deleted",
                                    "team": tool_result.get("deleted", args.get("name", "")),
                                    "scope": getattr(team_store, "scope", ""),
                                }
                            elif name == "team_list":
                                tool_result = team_store.list()
                                yield {
                                    "type": "team_event",
                                    "event": "teams_listed",
                                    "scope": getattr(team_store, "scope", ""),
                                    "count": len(tool_result),
                                }
                            elif name == "team_status":
                                tool_result = team_store.status(args.get("name", ""))
                                yield {
                                    "type": "team_event",
                                    "event": "team_status",
                                    "team": tool_result.get("name", args.get("name", "")),
                                    "scope": getattr(team_store, "scope", ""),
                                    "team_status": tool_result,
                                }
                            elif name == "send_message":
                                tool_result = team_store.send_message(
                                    args.get("team_name", ""), args.get("recipient", ""), args.get("message", "")
                                )
                                yield {
                                    "type": "team_event",
                                    "event": "message_sent",
                                    "team": args.get("team_name", ""),
                                    "recipient": args.get("recipient", ""),
                                    "scope": getattr(team_store, "scope", ""),
                                    "sent": tool_result.get("sent", 0),
                                    "messages": tool_result.get("messages", []),
                                }
                            elif name == "team_delegate":
                                from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed

                                team_name = str(args.get("team_name", "")).strip()
                                assignments = args.get("assignments", [])
                                if isinstance(assignments, str):
                                    try:
                                        assignments = json.loads(assignments)
                                    except json.JSONDecodeError:
                                        try:
                                            assignments = ast.literal_eval(assignments)
                                        except (ValueError, SyntaxError):
                                            assignments = []
                                if not isinstance(assignments, list):
                                    assignments = []
                                assignments = [
                                    item for item in assignments[:8]
                                    if isinstance(item, dict)
                                    and str(item.get("member_name", "")).strip()
                                    and str(item.get("prompt", "")).strip()
                                ]
                                timeout_seconds = max(
                                    10,
                                    min(
                                        self.DELEGATED_TIMEOUT_SECONDS,
                                        int(
                                            args.get(
                                                "timeout_seconds",
                                                self.DELEGATED_TIMEOUT_SECONDS,
                                            )
                                            or self.DELEGATED_TIMEOUT_SECONDS
                                        ),
                                    ),
                                )
                                result_max_tokens = max(
                                    400,
                                    min(2500, int(args.get("result_max_tokens", 1200) or 1200)),
                                )
                                max_workers = max(
                                    1,
                                    min(
                                        int(args.get("max_concurrency", len(assignments) or 1) or 1),
                                        len(assignments) or 1,
                                        6,
                                    ),
                                )
                                if not team_name:
                                    raise ValueError("team_delegate requires team_name")
                                if not assignments:
                                    raise ValueError("team_delegate requires non-empty assignments")

                                hook_events, _hook_prompts = self._run_hook_event(
                                    "subagent_start",
                                    tool_name=name,
                                    tool_args=dict(args or {}),
                                    message=f"{len(assignments)} parallel teammate tasks",
                                )
                                for hook_event in hook_events:
                                    yield hook_event

                                prepared = []
                                results = []
                                for assignment in assignments:
                                    member_name = str(assignment.get("member_name", "")).strip()
                                    try:
                                        assignment_prompt = str(assignment.get("prompt", ""))[:12_000]
                                        assignment_note = (
                                            f"任务：{str(assignment.get('description', '')).strip()}\n\n"
                                            if str(assignment.get("description", "")).strip()
                                            else "任务：团队并行分析\n\n"
                                        ) + assignment_prompt
                                        assignment_message = team_store.send_message(
                                            team_name,
                                            member_name,
                                            assignment_note,
                                            sender="leader",
                                            read=True,
                                            queue=False,
                                            message_type="assignment",
                                        )
                                        turn_info = team_store.begin_member_turn(team_name, member_name)
                                        member = turn_info["member"]
                                        inbox = turn_info.get("inbox", [])
                                        inbox_context = ""
                                        if inbox:
                                            inbox_context = "\n\n[Unread team mailbox]\n" + "\n".join(
                                                f"- From {msg.get('sender', '')}: {msg.get('message', '')}"
                                                for msg in inbox
                                            )
                                        prepared.append({
                                            "member_name": member_name,
                                            "member": member,
                                            "prompt": assignment_prompt,
                                            "description": str(assignment.get("description", ""))[:500],
                                            "inbox_context": inbox_context,
                                            "consumed_messages": len(inbox),
                                            "assignment_message": assignment_message,
                                        })
                                        yield {
                                            "type": "team_event",
                                            "event": "member_started",
                                            "team": team_name,
                                            "member": member_name,
                                            "description": assignment.get("description", ""),
                                            "message": assignment_message,
                                            "parallel": True,
                                        }
                                    except Exception as exc:
                                        error_text = str(exc)
                                        results.append({
                                            "member": member_name,
                                            "status": "failed",
                                            "error": error_text,
                                            "result": "",
                                        })
                                        yield {
                                            "type": "team_event",
                                            "event": "member_failed",
                                            "team": team_name,
                                            "member": member_name,
                                            "message": {"message": error_text, "message_type": "error"},
                                            "parallel": True,
                                        }

                                def _run_prepared_delegate(item: dict) -> tuple[dict, dict]:
                                    delegated = self._run_delegated_llm(
                                        member=item["member"],
                                        prompt=item["prompt"],
                                        inbox_context=item["inbox_context"],
                                        timeout_seconds=timeout_seconds,
                                        max_tokens=result_max_tokens,
                                    )
                                    return item, delegated

                                pending = set()
                                future_to_item = {}
                                executor = ThreadPoolExecutor(max_workers=max_workers)
                                try:
                                    for item in prepared:
                                        future = executor.submit(_run_prepared_delegate, item)
                                        pending.add(future)
                                        future_to_item[future] = item
                                    try:
                                        completed_iter = as_completed(
                                            pending,
                                            timeout=timeout_seconds + 5,
                                        )
                                        for future in completed_iter:
                                            pending.discard(future)
                                            item = future_to_item[future]
                                            member_name = item["member_name"]
                                            try:
                                                _item, delegated = future.result()
                                                content = str(delegated.get("content", ""))
                                                tool_events = delegated.get("tool_events", [])
                                            except Exception as exc:
                                                content = str(exc)
                                                tool_events = []
                                                completion = team_store.complete_member_turn(
                                                    team_name, member_name, content, ok=False, tool_events=tool_events
                                                )
                                                results.append({
                                                    "member": member_name,
                                                    "status": "failed",
                                                    "error": content,
                                                    "result": "",
                                                    "consumed_messages": item["consumed_messages"],
                                                })
                                                yield {
                                                    "type": "team_event",
                                                    "event": "member_failed",
                                                    "team": team_name,
                                                    "member": member_name,
                                                    "message": completion.get("message", {}),
                                                    "parallel": True,
                                                }
                                                continue
                                            completion = team_store.complete_member_turn(
                                                team_name, member_name, content, ok=True, tool_events=tool_events
                                            )
                                            results.append({
                                                "member": member_name,
                                                "status": "idle",
                                                "error": "",
                                                "result": content,
                                                "tool_count": len(tool_events),
                                                "consumed_messages": item["consumed_messages"],
                                            })
                                            yield {
                                                "type": "team_event",
                                                "event": "member_idle",
                                                "team": team_name,
                                                "member": member_name,
                                                "message": completion.get("message", {}),
                                                "parallel": True,
                                            }
                                    except TimeoutError:
                                        pass
                                    for future in list(pending):
                                        future.cancel()
                                        item = future_to_item[future]
                                        member_name = item["member_name"]
                                        content = f"团队成员执行超过 {timeout_seconds} 秒未完成，已停止等待。"
                                        completion = team_store.complete_member_turn(
                                            team_name, member_name, content, ok=False
                                        )
                                        results.append({
                                            "member": member_name,
                                            "status": "failed",
                                            "error": content,
                                            "result": "",
                                            "consumed_messages": item["consumed_messages"],
                                        })
                                        yield {
                                            "type": "team_event",
                                            "event": "member_failed",
                                            "team": team_name,
                                            "member": member_name,
                                            "message": completion.get("message", {}),
                                            "parallel": True,
                                        }
                                finally:
                                    executor.shutdown(wait=False, cancel_futures=True)

                                failed = [item for item in results if item.get("status") == "failed"]
                                tool_result = {
                                    "team": team_name,
                                    "status": "partial_failed" if failed else "completed",
                                    "parallel": True,
                                    "assignment_count": len(assignments),
                                    "completed_count": len(results) - len(failed),
                                    "failed_count": len(failed),
                                    "results": results,
                                    "delivered_to": "leader",
                                }
                                hook_events, _hook_prompts = self._run_hook_event(
                                    "subagent_stop",
                                    tool_name=name,
                                    tool_args=dict(args or {}),
                                    message=str(tool_result)[:1000],
                                )
                                for hook_event in hook_events:
                                    yield hook_event
                            else:
                                team_name = args.get("team_name", "")
                                member_name = args.get("member_name", "")
                                turn_info = None
                                if team_name and member_name:
                                    delegated_prompt = str(args.get("prompt", ""))[:20_000]
                                    assignment_note = (
                                        f"任务：{str(args.get('description', '')).strip()}\n\n"
                                        if str(args.get("description", "")).strip()
                                        else "任务：单成员分析\n\n"
                                    ) + delegated_prompt
                                    assignment_message = team_store.send_message(
                                        team_name,
                                        member_name,
                                        assignment_note,
                                        sender="leader",
                                        read=True,
                                        queue=False,
                                        message_type="assignment",
                                    )
                                    turn_info = team_store.begin_member_turn(team_name, member_name)
                                    member = turn_info["member"]
                                else:
                                    member = {"role": "delegated business analyst", "instructions": ""}
                                    assignment_message = {}
                                    delegated_prompt = str(args.get("prompt", ""))[:20_000]
                                inbox = turn_info.get("inbox", []) if turn_info else []
                                inbox_context = ""
                                if inbox:
                                    inbox_context = "\n\n[Unread team mailbox]\n" + "\n".join(
                                        f"- From {msg.get('sender', '')}: {msg.get('message', '')}"
                                        for msg in inbox
                                    )
                                hook_events, _hook_prompts = self._run_hook_event(
                                    "subagent_start",
                                    tool_name=name,
                                    tool_args=dict(args or {}),
                                    message=delegated_prompt[:1000],
                                )
                                for hook_event in hook_events:
                                    yield hook_event
                                if team_name and member_name:
                                    yield {
                                        "type": "team_event",
                                        "event": "member_started",
                                        "team": team_name,
                                        "member": member_name,
                                        "description": args.get("description", ""),
                                        "message": assignment_message,
                                    }
                                try:
                                    delegated = self._run_delegated_llm(
                                        member=member,
                                        prompt=delegated_prompt,
                                        inbox_context=inbox_context,
                                        timeout_seconds=self.DELEGATED_TIMEOUT_SECONDS,
                                        max_tokens=2000,
                                    )
                                    tool_result = str(delegated.get("content", ""))
                                    tool_events = delegated.get("tool_events", [])
                                except Exception as exc:
                                    tool_events = []
                                    if team_name and member_name:
                                        completion = team_store.complete_member_turn(
                                            team_name, member_name, str(exc), ok=False, tool_events=tool_events
                                        )
                                        yield {
                                            "type": "team_event",
                                            "event": "member_failed",
                                            "team": team_name,
                                            "member": member_name,
                                            "message": completion.get("message", {}),
                                        }
                                    raise
                                if team_name and member_name:
                                    completion = team_store.complete_member_turn(
                                        team_name, member_name, tool_result, ok=True, tool_events=tool_events
                                    )
                                    yield {
                                        "type": "team_event",
                                        "event": "member_idle",
                                        "team": team_name,
                                        "member": member_name,
                                        "message": completion.get("message", {}),
                                    }
                                    tool_result = {
                                        "team": team_name,
                                        "member": member_name,
                                        "status": completion.get("status", "idle"),
                                        "consumed_messages": len(inbox),
                                        "result": tool_result,
                                        "tool_count": len(tool_events),
                                        "delivered_to": "leader",
                                    }
                                hook_events, _hook_prompts = self._run_hook_event(
                                    "subagent_stop",
                                    tool_name=name,
                                    tool_args=dict(args or {}),
                                    message=str(tool_result)[:1000],
                                )
                                for hook_event in hook_events:
                                    yield hook_event
                        elif name == "plan_complete":
                            tool_result = {
                                "summary": args.get("summary", ""),
                                "steps": args.get("steps", []),
                            }
                        elif name.startswith("mcp__"):
                            tool_result = self._mcp_manager.call_tool(name, args)
                            recorder = getattr(self, "_mcp_discovery_recorder", None)
                            if recorder is not None:
                                recorder([name], _mcp_catalog_version, used=True)
                        else:
                            tool_result = f"Unknown tool: {name}"

                    except Exception as exc:
                        tool_result = f"工具执行错误 [{name}]: {exc}"
                        log.error("[tool] %s FAILED (%.2fs): %s", name, time.monotonic() - _tool_t0, exc)
                        _consecutive_errors += 1
                    else:
                        _consecutive_errors = 0
                        _result_preview = str(tool_result)[:120].replace("\n", " ")
                        log.info("[tool] %s OK  %.2fs  result=%r", name, time.monotonic() - _tool_t0, _result_preview)

                    envelope = make_tool_result(
                        name,
                        tool_result,
                        sources=tool_sources,
                        artifacts=tool_artifacts,
                        debug={
                            "elapsed_seconds": round(time.monotonic() - _tool_t0, 3),
                            "args_preview": _args_preview,
                        },
                        session_id=self._session_id,
                        runtime=self._workspace_runtime(),
                    )
                    _remember_turn_tool_result_artifacts(
                        _allowed_tool_result_artifacts,
                        envelope.artifacts,
                        session_id=self._session_id,
                    )
                    yield {
                        "type": "tool_audit",
                        "tool": name,
                        "ok": envelope.ok,
                        "error": envelope.error,
                        "summary": envelope.summary,
                        "content": str(envelope.data),
                        "sources": envelope.sources,
                        "artifacts": envelope.artifacts,
                        "elapsed_seconds": envelope.debug.get("elapsed_seconds"),
                        "args_preview": envelope.debug.get("args_preview", {}),
                        "recovery": {
                            "sql": str(args.get("sql", ""))[:4000]
                            if name in {"query_data", "create_analysis_table"} else "",
                        },
                    }
                    if envelope.ok:
                        _successful_tool_names.add(name)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": envelope.to_model_text(),
                    })
                    hook_events, hook_prompts = self._run_post_tool_hooks(name, args, envelope)
                    for hook_event in hook_events:
                        yield hook_event
                    _hook_prompt_backlog.extend(hook_prompts)
                    yield {"type": "tool_end", "tool": name}
                    if not _outline_proposed:
                        yield {"type": "agent_activity", "message": "正在思考下一步…"}

                hook_msg = self._hook_prompt_system_message(_hook_prompt_backlog)
                if hook_msg:
                    messages.append(hook_msg)

                if _outline_proposed:
                    for chart_item in pending_charts:
                        yield {"type": "chart_html", **chart_item}
                    pending_charts.clear()
                    yield {"type": "done"}
                    return

            # ── Final text response ───────────────────────────────────────────
            else:
                if reasoning_content:
                    all_reasoning.append(reasoning_content)

                _missing_skill_tools = _missing_required_skill_tools(
                    trusted_skill_name, _successful_tool_names,
                )
                if _missing_skill_tools:
                    messages.extend([
                        {"role": "assistant", "content": full_content},
                        {
                            "role": "user",
                            "content": (
                                "[QUALITY GATE] Do not finish yet. The activated "
                                f"Skill requires successful tool(s): "
                                f"{', '.join(_missing_skill_tools)}. Call the "
                                "required tool now, fix any tool error, and only "
                                "then provide the final answer."
                            ),
                        },
                    ])
                    yield {
                        "type": "agent_activity",
                        "message": "正在完成 Skill 必需分析步骤…",
                    }
                    continue

                _missing_response_contract = _missing_skill_response_contract(
                    trusted_skill_name, full_content,
                )
                if _missing_response_contract:
                    _last_missing_response_contract = _missing_response_contract
                    _force_text_only = True
                    messages.extend([
                        {"role": "assistant", "content": full_content},
                        {
                            "role": "user",
                            "content": (
                                "[QUALITY GATE] The analysis tool succeeded, "
                                "but the final answer is incomplete. Add all of "
                                "the following using ONLY existing tool results:\n- "
                                + "\n- ".join(_missing_response_contract)
                                + "\nDo not call tools again. State the direction, "
                                "significance, and that correlation does not imply "
                                "causation."
                            ),
                        },
                    ])
                    yield {
                        "type": "agent_activity",
                        "message": "正在补全统计结论与非因果说明…",
                    }
                    continue

                if command in _PROPOSE_FLOW_CMDS:
                    messages.append({"role": "assistant", "content": full_content})
                    _force_propose = True
                    continue

                if all_reasoning:
                    yield {"type": "reasoning", "content": "\n\n---\n\n".join(all_reasoning)}

                for chart_item in pending_charts:
                    yield {"type": "chart_html", **chart_item}

                yield {"type": "text", "content": full_content}
                log.info("[run] finished normally  model=%s", self.model)

                # Emit tool messages so chat.py can store them in history.
                # Only include messages that belong to THIS turn (after _turn_start_idx).
                # Strip system-injected content that must not re-enter the prompt.
                _ALLOWED_ROLES = {"assistant", "tool"}
                _turn_msgs = [
                    *_archived_turn_messages,
                    *(m for m in messages[_turn_start_idx:]
                      if m.get("role") in _ALLOWED_ROLES),
                ]
                if _turn_msgs:
                    yield {"type": "tool_history", "messages": _turn_msgs}

                yield {"type": "done"}
                return

        _missing_skill_tools = _missing_required_skill_tools(
            trusted_skill_name, _successful_tool_names,
        )
        if _missing_skill_tools:
            log.warning(
                "[run] required Skill tools never succeeded: %s",
                ", ".join(_missing_skill_tools),
            )
            yield {
                "type": "error",
                "message": (
                    "分析未完成：必需工具未成功执行（"
                    + "、".join(_missing_skill_tools)
                    + "）。"
                ),
            }
            yield {"type": "done"}
            return
        if _last_missing_response_contract:
            log.warning(
                "[run] Skill final-answer contract never satisfied: %s",
                "; ".join(_last_missing_response_contract),
            )
            yield {
                "type": "error",
                "message": (
                    "分析已执行，但最终结论仍缺少："
                    + "、".join(_last_missing_response_contract)
                    + "。"
                ),
            }
            yield {"type": "done"}
            return
        log.warning("[run] max iterations reached  model=%s", self.model)
        yield {
            "type": "text",
            "content": "分析完成（已达到最大工具调用次数）。Analysis complete (max iterations reached).",
        }
        yield {"type": "done"}
