"""Long-term memory rendering, extraction, and scoped agent access."""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from agent.reasoning import split_reasoning_tags
from data import memory_store
from infrastructure.paths import data_path

log = logging.getLogger(__name__)
_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="baa-memory")
_FAILURES: dict[str, int] = {}
_FAILURE_LIMIT = 3
# Consolidation runs at most once per interval; the lock file's mtime records
# the last attempt (start-of-run), its content the owning PID.
_CONSOLIDATE_INTERVAL_SECONDS = 24 * 3600
_CONSOLIDATE_MAX_OPS = 20
# Messages produced by background extraction/consolidation, waiting for the
# frontend to fetch them as toasts. Keyed by session id, bounded on both axes.
_NOTICES: dict[str, list[str]] = {}
_NOTICES_LOCK = threading.Lock()
_NOTICES_MAX_SESSIONS = 100
_NOTICES_MAX_PER_SESSION = 10
_EXTRACT_SYSTEM = """You extract durable memory for a local data-analysis assistant. Return JSON only.
Your first non-whitespace character must be `{`; do not emit reasoning, `<think>` tags, Markdown fences, or prose.
Remember only explicit user preferences (user), corrections (feedback), confirmed workspace facts (project), or durable resource pointers (reference). Do not store tasks, casual chat, raw query values, credentials, connection strings, secrets, or private data. Existing memory summaries are included for deduplication.
Explicit output preferences MUST be recorded: chart title/label language, number formatting, metric definitions, report style, and any rule the user says applies from now on. Example: "图表标题用中文" -> create user memory; "毛利口径=(收入-成本)/收入" -> create user memory.
Scope is mandatory: user/feedback are global user memory; project/reference are current-workspace memory. Explicit wording wins: "本项目"、"当前工作区"、project names, table/field names, dataset rules, and file paths mean project/reference. "以后"、"所有项目"、"默认" and personal output preferences mean user/feedback. When unsure, choose project rather than polluting global memory.
Output {\"ops\":[]} when nothing is durable. Each operation is create or update with type, name, title, body, optional why and how_to_apply. The \"op\" field holds the verb (\"create\" or \"update\"); the \"type\" field holds the memory category (\"user\", \"feedback\", \"project\", or \"reference\"). Never put the verb in \"type\" and never omit \"type\". Names must use lowercase kebab-case, for example unit-net-rate-formula. At most 5 operations."""
_EXTRACT_REPAIR_SYSTEM = """Return a valid JSON object only, with exactly one top-level key: "ops".
Do not include reasoning, `<think>` tags, Markdown fences, or prose. Re-evaluate the supplied conversation turn and return {"ops":[]} if nothing is durable. Each item in ops must use the memory extraction schema: op, type, name, title, body, optional why and how_to_apply."""
_CONSOLIDATE_SYSTEM = """You curate the long-term memory of a local data-analysis assistant. Return JSON only.
Given today's date and every memory record, merge duplicates or overlapping records into the most complete one, rewrite relative dates (e.g. \"last week\") as absolute dates, and archive records that are stale, superseded, or merged away. Never invent new facts and never store secrets. Output {\"ops\":[]} when nothing needs curation. Each operation is either {\"op\":\"update\",\"name\":...,\"title\":...,\"body\":...} (partial fields allowed) or {\"op\":\"archive\",\"name\":...}. At most 20 operations."""


def render_memory_section(*, user_id: str = "", workspace_id: str = "") -> str:
    return memory_store.render_memory_section(user_id=user_id, workspace_id=workspace_id)


def read_memory(name: str, *, user_id: str = "", workspace_id: str = "") -> str:
    record = memory_store.get_record(name, user_id=user_id, workspace_id=workspace_id)
    if not record:
        return "Memory record is unavailable in the current scope."
    lines = [f"[{record['type']}] {record['title']}", record["body"]]
    if record.get("why"):
        lines.append(f"Why: {record['why']}")
    if record.get("how_to_apply"):
        lines.append(f"How to apply: {record['how_to_apply']}")
    return "\n".join(lines)


_THINK_BLOCK_RE = re.compile(
    r"(?:<(?:think|analysis|reasoning)\b[^>]*>.*?</(?:think|analysis|reasoning)>|```(?:think|analysis|reasoning)\b.*?```)",
    re.DOTALL | re.IGNORECASE,
)


def _strip_think_blocks(text: str) -> str:
    """Remove reasoning blocks a thinking model may inline in ``content``.

    MiniMax-M3 (and similar) emit the reasoning chain directly inside the
    message content wrapped in ``<think>...</think>`` instead of a separate
    reasoning field.  Extraction/consolidation need only the JSON part.
    """
    return _THINK_BLOCK_RE.sub("", str(text or "")).strip()


def _content_text(content: Any) -> str:
    """Normalise OpenAI-compatible text and multipart content formats."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        for key in ("text", "content", "value"):
            value = content.get(key)
            if isinstance(value, (str, list, dict)):
                return _content_text(value)
        return ""
    if isinstance(content, list):
        return "\n".join(_content_text(item) for item in content if _content_text(item))
    text = getattr(content, "text", None)
    return str(text) if text is not None else ""


def _final_response_content(message: Any) -> str:
    """Extract final answer while ignoring provider-specific reasoning fields.

    OpenAI-compatible providers differ: some use ``reasoning_content``, some
    emit ``<think>`` inline, and multimodal providers use content-part arrays.
    Memory extraction must only consume the final answer, never hidden
    reasoning, regardless of its transport format.
    """
    content = message.get("content") if isinstance(message, dict) else getattr(message, "content", "")
    text = _content_text(content).strip()
    # ``split_reasoning_tags`` handles streaming-style <think> tags. Other
    # providers use analysis/reasoning tags, sometimes without a closing tag.
    # An unclosed reasoning block has no trustworthy final-answer segment.
    text = re.sub(r"<(?:analysis|reasoning)\b[^>]*>.*?(?:</(?:analysis|reasoning)>|$)", "", text,
                  flags=re.DOTALL | re.IGNORECASE)
    visible, _embedded_reasoning = split_reasoning_tags(text)
    return _strip_think_blocks(visible).strip()


def _parse_json_payload(raw: str) -> dict:
    """Parse an operation object from a model reply, tolerating extra text.

    Some reasoning-capable providers append a second JSON fragment, prose, or
    an inlined ``<think>`` chain to the requested object.  Do not join from the
    first ``{`` to the last ``}``: that creates invalid JSON (``Extra data``).
    Instead decode each complete object and prefer the final one that satisfies
    the extraction contract.
    """
    text = str(raw or "").strip()
    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError as full_error:
        candidates: list[dict] = []
        for candidate_text in (text, _strip_think_blocks(text)):
            decoder = json.JSONDecoder()
            offset = 0
            while True:
                start = candidate_text.find("{", offset)
                if start < 0:
                    break
                try:
                    payload, end = decoder.raw_decode(candidate_text, start)
                except json.JSONDecodeError:
                    offset = start + 1
                    continue
                if isinstance(payload, dict) and "ops" in payload:
                    candidates.append(payload)
                offset = end
        if candidates:
            return candidates[-1]
        raise full_error


def _response_diagnostics(raw: str) -> dict[str, Any]:
    """Safe response-shape metadata for logs; never log conversation content."""
    text = str(raw or "").strip()
    return {
        "chars": len(text),
        "starts_json": text.startswith("{"),
        "has_ops_key": '"ops"' in text,
        "has_think": "<think" in text.lower(),
        "has_fence": "```" in text,
    }


def _truncate_for_context(text: str, max_chars: int) -> str:
    """Keep both ends of an oversized turn without exceeding its allowance."""
    text = str(text or "")
    if len(text) <= max_chars:
        return text
    if max_chars < 80:
        return text[:max_chars]
    head = max_chars // 2
    tail = max_chars - head - 28
    return f"{text[:head]}\n…[中间内容因上下文上限省略]…\n{text[-tail:]}"


def _extraction_turn_for_context(
    user_message: str, assistant_message: str, context_tokens: int,
) -> tuple[str, str]:
    """Bound extraction input to its 80%-of-model context allowance."""
    max_chars = max(2_000, int(context_tokens * 3.5))
    # Give each side half the allowance.  Keeping the conclusion/end of each
    # side makes this robust for long streamed assistant responses.
    each = max_chars // 2
    return (
        _truncate_for_context(user_message, each),
        _truncate_for_context(assistant_message, each),
    )


def schedule_extraction(*, provider: str, session_id: str, user_id: str, workspace_id: str, user_message: str, assistant_message: str, runner: Any = None) -> None:
    """Submit extraction without coupling it to the SSE lifecycle.

    When a JobRunner is supplied the extraction reports its lifecycle as a
    tracked top-level job so it shows up in the task history panel.
    """
    if not user_message.strip() or not assistant_message.strip():
        return
    if _FAILURES.get(session_id, 0) >= _FAILURE_LIMIT:
        return
    _EXECUTOR.submit(
        _extract,
        provider=provider,
        session_id=session_id,
        user_id=user_id,
        workspace_id=workspace_id,
        user_message=user_message,
        assistant_message=assistant_message,
        runner=runner,
    )


def _job_begin(runner: Any, label: str) -> str:
    """Open a tracked job for history visibility; never break extraction."""
    if runner is None:
        return ""
    try:
        return runner.begin_tracked("memory_extraction", label=label)
    except Exception as exc:
        log.warning("[memory] job tracking unavailable: %s", exc)
        return ""


def _job_finish(runner: Any, job_id: str, *, result: Any = None, error: str = "", message: str = "") -> None:
    if runner is None or not job_id:
        return
    try:
        if error:
            runner.fail_tracked(job_id, error)
        else:
            if message:
                # Surface the outcome on the history card: "succeeded" alone
                # reads as "memory written" even when the model found nothing.
                runner.update_tracked(job_id, 100, message)
            runner.succeed_tracked(job_id, result)
    except Exception as exc:
        log.warning("[memory] job tracking finalize failed: %s", exc)


def _extract(*, provider: str, session_id: str, user_id: str, workspace_id: str, user_message: str, assistant_message: str, runner: Any = None) -> None:
    # Created here (memory worker thread) so the runner's thread-local
    # conversation scope does not apply: the job stays top-level and thus
    # visible in the task history list.
    job_id = _job_begin(runner, f"记忆提取：{user_message[:60]}")
    raw = ""
    try:
        from LLM.llm_config_manager import (
            auxiliary_token_limits,
            get_config_manager,
            get_llm_client,
        )

        manager = get_config_manager()
        selected = provider or manager.get_default_provider()
        if not selected:
            _job_finish(runner, job_id, result={"skipped": "未配置模型提供方"}, message="未配置模型提供方，本轮跳过")
            return
        config = manager.get_config(selected)
        if not config:
            _job_finish(runner, job_id, result={"skipped": "模型配置不可用"}, message="模型配置不可用，本轮跳过")
            return
        context_budget, output_budget = auxiliary_token_limits(config)
        user_message, assistant_message = _extraction_turn_for_context(
            user_message, assistant_message, context_budget,
        )
        summaries = [
            {key: record[key] for key in ("name", "type", "title")}
            for record in memory_store.list_records(user_id=user_id, workspace_id=workspace_id)
        ]
        client = get_llm_client(selected)
        # Use 80% of the selected model's declared output capability. Thinking
        # models can consume a substantial prefix on reasoning before emitting
        # final JSON, so a legacy 1--2K cap was counterproductive.
        extraction_input = {
            "existing_memory": summaries,
            "turn": {"user": user_message, "assistant": assistant_message},
        }
        response = client.chat.completions.create(
            model=config.model,
            temperature=0,
            max_tokens=output_budget,
            messages=[
                {"role": "system", "content": _EXTRACT_SYSTEM},
                {"role": "user", "content": json.dumps(extraction_input, ensure_ascii=False)},
            ],
        )
        raw = _final_response_content(response.choices[0].message)
        if not raw:
            memory_store.record_extraction_activity(
                user_id=user_id, session_id=session_id,
                status="skipped", message="模型未返回内容，本轮跳过",
            )
            _job_finish(runner, job_id, result={"skipped": True}, message="模型未返回内容，本轮跳过")
            return
        try:
            operations = _parse_json_payload(raw).get("ops") or []
        except json.JSONDecodeError:
            log.warning(
                "[memory] invalid JSON on first extraction attempt sid=%s shape=%s; retrying",
                session_id, _response_diagnostics(raw),
            )
            response = client.chat.completions.create(
                model=config.model,
                temperature=0,
                max_tokens=output_budget,
                messages=[
                    {"role": "system", "content": _EXTRACT_REPAIR_SYSTEM},
                    {"role": "user", "content": json.dumps(extraction_input, ensure_ascii=False)},
                ],
            )
            raw = _final_response_content(response.choices[0].message)
            if not raw:
                raise json.JSONDecodeError("empty repair response", "", 0)
            operations = _parse_json_payload(raw).get("ops") or []
        if not isinstance(operations, list) or len(operations) > 5:
            raise ValueError("invalid memory operation list")
        saved_records: list[dict[str, str]] = []
        applied = _apply_operations(
            operations, session_id=session_id,
            user_id=user_id, workspace_id=workspace_id,
            scope_hint=_explicit_scope_hint(user_message),
            saved_records=saved_records,
        )
        _FAILURES.pop(session_id, None)
        summary = (
            f"已写入 {applied} 条记忆" if applied
            else "提取到的操作均未通过校验" if operations
            else "本轮无可长期记忆的内容"
        )
        memory_store.record_extraction_activity(
            user_id=user_id, session_id=session_id,
            status="saved" if applied else "rejected" if operations else "skipped",
            message=summary, records=saved_records,
        )
        _job_finish(runner, job_id, result={"ops": len(operations), "applied": applied}, message=summary)
    except json.JSONDecodeError as exc:
        _FAILURES[session_id] = _FAILURES.get(session_id, 0) + 1
        diagnostics = _response_diagnostics(raw)
        log.warning(
            "[memory] extraction failed after JSON repair sid=%s error=%s shape=%s",
            session_id, exc, diagnostics,
        )
        memory_store.record_extraction_activity(
            user_id=user_id, session_id=session_id, status="failed",
            message="模型两次未返回可用 JSON，未写入记忆",
        )
        _job_finish(runner, job_id, error="记忆提取失败：模型两次未返回可用 JSON")
    except Exception as exc:
        _FAILURES[session_id] = _FAILURES.get(session_id, 0) + 1
        log.warning(
            "[memory] extraction failed sid=%s: %s shape=%s",
            session_id, exc, _response_diagnostics(raw),
        )
        memory_store.record_extraction_activity(
            user_id=user_id, session_id=session_id, status="failed",
            message=f"提取失败：{exc}",
        )
        _job_finish(runner, job_id, error=f"记忆提取失败：{exc}")


def pop_notices(session_id: str) -> list[str]:
    """Return and clear pending memory toast messages for one session."""
    with _NOTICES_LOCK:
        return _NOTICES.pop(str(session_id or ""), [])


def _push_notice(session_id: str, title: str) -> None:
    if not session_id or not title:
        return
    with _NOTICES_LOCK:
        while len(_NOTICES) >= _NOTICES_MAX_SESSIONS and session_id not in _NOTICES:
            _NOTICES.pop(next(iter(_NOTICES)))
        queue = _NOTICES.setdefault(session_id, [])
        queue.append(title)
        del queue[:-_NOTICES_MAX_PER_SESSION]


def _explicit_scope_hint(user_message: str) -> str:
    """Return an explicit user-requested memory scope, if present."""
    text = str(user_message or "").lower()
    workspace_markers = ("本项目", "当前项目", "当前工作区", "此项目", "该项目", "这个项目")
    global_markers = ("所有项目", "跨项目", "以后都", "以后所有", "全局", "默认情况下")
    if any(marker in text for marker in workspace_markers):
        return "workspace"
    if any(marker in text for marker in global_markers):
        return "user"
    return ""


def _has_project_specific_content(operation: dict[str, Any]) -> bool:
    """Recognize content that must not silently become a global preference."""
    text = " ".join(str(operation.get(key) or "") for key in ("title", "body", "why", "how_to_apply"))
    # The bare "表" would also match 图表/报表/表格/列表 and the bare "项目"
    # would match "所有项目", silently downgrading harmless preferences to
    # workspace records.  Require project-specific qualifiers instead.
    return bool(re.search(
        r"(?:工作区|数据集|数据源|(?<![图报格列])表(?:名)?|字段|列名|schema|数据库|"
        r"(?:本|当前|此|该)项目|项目(?:名|数据|资料|文件|文档|目录|仓库)|"
        r"\\.zhixi|[A-Za-z0-9_.-]+\\.(?:csv|xlsx|xls|parquet|duckdb|json))|`[^`]+`",
        text,
        re.IGNORECASE,
    ))


def _normalize_automatic_operation(operation: dict[str, Any], *, scope_hint: str = "") -> dict[str, Any]:
    """Apply deterministic scope and identifier safeguards to LLM output."""
    normalized = dict(operation)
    raw_name = str(normalized.get("name") or "").strip()
    kebab_name = re.sub(r"-+", "-", raw_name.lower().replace("_", "-")).strip("-")
    if kebab_name and re.fullmatch(r"[a-z0-9-]+", kebab_name):
        normalized["name"] = kebab_name

    # Field-name confusion tolerance: thinking models sometimes write the
    # operation verb into "type" (e.g. {"type": "create", ...}) and omit both
    # "op" and the record type.  Promote it to "op" and default the record
    # type to "user"; project-content sniffing below still downgrades it.
    raw_op = str(normalized.get("op") or "").strip().lower()
    type_field = str(normalized.get("type") or "").strip().lower()
    if not raw_op and type_field in {"create", "update", "delete"}:
        normalized["op"] = type_field
        normalized.pop("type", None)
    memory_type = str(normalized.get("type") or "").strip().lower()
    if not memory_type:
        normalized["type"] = "user"
        memory_type = "user"

    if scope_hint == "workspace" and memory_type in {"user", "feedback"}:
        normalized["type"] = "project"
    elif scope_hint == "user":
        # User explicitly asked for global scope ("所有项目" / "全局"); the
        # project-content sniff must not re-downgrade that preference.
        normalized["type"] = "user"
    elif memory_type in {"user", "feedback"} and _has_project_specific_content(normalized):
        normalized["type"] = "project"
    return normalized


def _apply_operations(operations: list[Any], *, session_id: str, user_id: str, workspace_id: str, scope_hint: str = "", saved_records: list[dict[str, str]] | None = None) -> int:
    """Apply validated ops one by one; a rejected op never fails the batch.

    Returns the number of records actually written.
    """
    applied = 0
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        operation = _normalize_automatic_operation(operation, scope_hint=scope_hint)
        op = str(operation.get("op") or "").lower()
        name = str(operation.get("name") or "")
        try:
            if op == "create" and memory_store.get_record(
                name, user_id=user_id, workspace_id=workspace_id,
            ):
                # Duplicate name: converge onto the existing record instead of
                # raising, which would trip the extraction circuit breaker.
                op = "update"
            saved: dict[str, Any] | None = None
            if op == "create":
                saved = memory_store.create_record(
                    operation, user_id=user_id, workspace_id=workspace_id,
                    actor="extraction", automatic=True, source_session=session_id,
                )
            elif op == "update":
                saved = memory_store.update_record(
                    name, operation, user_id=user_id, workspace_id=workspace_id,
                    actor="extraction", automatic=True,
                )
            if saved:
                applied += 1
                if saved_records is not None:
                    saved_records.append({
                        "name": saved["name"], "title": saved["title"], "scope": saved["scope"],
                    })
                label = "工作区级" if saved["scope"] == "workspace" else "用户级"
                _push_notice(session_id, f"已记住（{label}）：{saved['title']}")
        except ValueError as exc:
            log.warning("[memory] extraction op rejected sid=%s name=%s: %s", session_id, name, exc)
    return applied


# ── consolidation (P2 governance) ──────────────────────────────────────────

def _consolidation_lock_path():
    return data_path("memory", ".consolidate-lock")


def _acquire_consolidation_lock() -> float | None:
    """Claim the once-per-interval consolidation slot.

    Returns the previous lock mtime (0.0 if none) when acquired, or None when
    another run happened/started within the interval. The mtime is stamped at
    acquisition, so a crashed run naturally retries after the interval.
    """
    lock = _consolidation_lock_path()
    previous = 0.0
    try:
        previous = lock.stat().st_mtime
    except OSError:
        pass
    if previous and time.time() - previous < _CONSOLIDATE_INTERVAL_SECONDS:
        return None
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(str(os.getpid()), encoding="utf-8")
        # Read back: if a concurrent process won the race, its PID is inside.
        if lock.read_text(encoding="utf-8").strip() != str(os.getpid()):
            return None
    except OSError as exc:
        log.warning("[memory] consolidation lock unavailable: %s", exc)
        return None
    return previous


def _rollback_consolidation_lock(previous_mtime: float) -> None:
    """A failed run gives the slot back instead of burning the whole interval."""
    lock = _consolidation_lock_path()
    try:
        if previous_mtime:
            os.utime(lock, (previous_mtime, previous_mtime))
        else:
            lock.unlink(missing_ok=True)
    except OSError:
        pass


def maybe_schedule_consolidation(*, provider: str, session_id: str, user_id: str, workspace_id: str) -> bool:
    """Kick off background memory curation at most once per 24h."""
    previous = _acquire_consolidation_lock()
    if previous is None:
        return False
    _EXECUTOR.submit(
        _consolidate,
        provider=provider,
        session_id=session_id,
        user_id=user_id,
        workspace_id=workspace_id,
        previous_mtime=previous,
    )
    return True


def _consolidate(*, provider: str, session_id: str, user_id: str, workspace_id: str, previous_mtime: float) -> None:
    try:
        records = memory_store.list_records(user_id=user_id, workspace_id=workspace_id)
        if len(records) < 2:
            return  # nothing to merge; keep the slot consumed until next interval
        from LLM.llm_config_manager import get_config_manager, get_llm_client

        manager = get_config_manager()
        selected = provider or manager.get_default_provider()
        config = manager.get_config(selected) if selected else None
        if not config:
            _rollback_consolidation_lock(previous_mtime)
            return
        client = get_llm_client(selected)
        response = client.chat.completions.create(
            model=config.model,
            temperature=0,
            max_tokens=4000,
            messages=[
                {"role": "system", "content": _CONSOLIDATE_SYSTEM},
                {"role": "user", "content": json.dumps({
                    "today": time.strftime("%Y-%m-%d"),
                    "memory": [
                        {key: record.get(key, "") for key in
                         ("name", "type", "title", "body", "updated_at", "last_seen_at")}
                        for record in records
                    ],
                }, ensure_ascii=False)},
            ],
        )
        raw = str(response.choices[0].message.content or "").strip()
        if not raw:
            return
        operations = _parse_json_payload(raw).get("ops") or []
        if not isinstance(operations, list) or len(operations) > _CONSOLIDATE_MAX_OPS:
            raise ValueError("invalid consolidation operation list")
        updated, archived = _apply_consolidation_ops(
            operations, user_id=user_id, workspace_id=workspace_id,
        )
        log.info("[memory] consolidation done updated=%d archived=%d", updated, archived)
        if updated or archived:
            _push_notice(session_id, f"记忆整理完成：更新 {updated} 条、归档 {archived} 条")
    except Exception as exc:
        _rollback_consolidation_lock(previous_mtime)
        log.warning("[memory] consolidation failed: %s", exc)


def _apply_consolidation_ops(operations: list[Any], *, user_id: str, workspace_id: str) -> tuple[int, int]:
    """Apply curation ops (update/archive only); one bad op never fails the batch."""
    updated = archived = 0
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        op = str(operation.get("op") or "").lower()
        name = str(operation.get("name") or "")
        try:
            if op == "update":
                if memory_store.update_record(
                    name, operation, user_id=user_id, workspace_id=workspace_id,
                    actor="consolidation", automatic=True,
                ):
                    updated += 1
            elif op == "archive":
                if memory_store.archive_record(
                    name, user_id=user_id, workspace_id=workspace_id,
                    actor="consolidation",
                ):
                    archived += 1
        except ValueError as exc:
            log.warning("[memory] consolidation op rejected name=%s: %s", name, exc)
    return updated, archived
