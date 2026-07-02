"""Deterministic hard-summary replacement at workflow-stage boundaries."""
from __future__ import annotations

import json
import re
from typing import Iterable

from .tools.results import extract_tool_result_references


STAGE_ARCHIVE_MARKER = "[STAGE_ARCHIVED]"
_MIN_ARCHIVE_CHARS = 800
_MAX_HARD_SUMMARY_CHARS = 720

_ARCHIVE_BY_STAGE = {
    "analyze": frozenset({
        "workspace_status", "workspace_glob", "workspace_grep",
        "query_knowledge",
    }),
    "visualize": frozenset({
        "workspace_status", "workspace_glob", "workspace_grep",
        "query_knowledge", "get_schema", "get_table_detail",
        "profile_data", "clean_data",
    }),
    "propose_output": frozenset({
        "workspace_status", "workspace_glob", "workspace_grep",
        "query_knowledge", "get_schema", "get_table_detail",
        "profile_data", "clean_data",
    }),
    "generate_output": frozenset({
        "workspace_status", "workspace_glob", "workspace_grep",
        "query_knowledge", "get_schema", "get_table_detail",
        "profile_data", "clean_data", "select_chart",
    }),
    "verify": frozenset({
        "workspace_status", "workspace_glob", "workspace_grep",
        "query_knowledge", "get_schema", "get_table_detail",
        "profile_data", "clean_data", "select_chart",
        "query_data", "run_analysis", "create_analysis_table",
    }),
}

_ANCHOR_LINE_RE = re.compile(
    r"(?:\btable\b|\bcolumns?\b|\brows?\b|\bsource\b|\bfile\b|"
    r"表名|数据表|字段|列名|行数|来源|文件|artifact://|workspace://)",
    re.IGNORECASE,
)
_FILE_RE = re.compile(
    r"(?<![\w.-])[\w\u4e00-\u9fff ._-]+\.(?:csv|xlsx?|xlsm|docx|pdf|pptx|json)\b",
    re.IGNORECASE,
)


def _tool_call_names(messages: Iterable[dict]) -> dict[str, str]:
    names: dict[str, str] = {}
    for message in messages:
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or ():
            call_id = str(call.get("id") or "")
            name = str(((call.get("function") or {}).get("name") or ""))
            if call_id and name:
                names[call_id] = name
    return names


def _json_payload(text: str) -> dict:
    _, separator, tail = str(text or "").partition("\n")
    if not separator:
        return {}
    try:
        value = json.loads(tail)
        return value if isinstance(value, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _hard_summary(tool: str, content: str) -> str:
    lines = str(content or "").splitlines()
    headline = lines[0][:280] if lines else f"[TOOL_RESULT] {tool}"
    payload = _json_payload(content)
    anchors: list[str] = []

    error = str(payload.get("error") or "").strip()
    if error:
        anchors.append(f"error={error[:120]}")

    for source in payload.get("sources") or ():
        if isinstance(source, dict):
            compact = {
                key: source.get(key)
                for key in ("source", "table", "name", "rows", "sql")
                if source.get(key) not in (None, "")
            }
            if compact:
                anchors.append(json.dumps(compact, ensure_ascii=False, default=str)[:260])

    refs = extract_tool_result_references(content)
    if refs:
        anchors.append("artifacts=" + ", ".join(refs))

    data = payload.get("data")
    data_text = data if isinstance(data, str) else json.dumps(
        data, ensure_ascii=False, default=str,
    )
    for line in str(data_text or "").splitlines():
        clean = " ".join(line.split())
        if clean and _ANCHOR_LINE_RE.search(clean):
            anchors.append(clean[:240])
        if len(anchors) >= 5:
            break
    if len(anchors) < 5:
        for match in _FILE_RE.findall(str(data_text or "")):
            anchors.append(str(match).strip()[:160])
            if len(anchors) >= 5:
                break

    body = "\n".join(dict.fromkeys(anchors))
    summary = (
        f"{headline}\n{STAGE_ARCHIVE_MARKER} tool={tool}; "
        "full preview removed after workflow-stage transition."
    )
    if body:
        summary += "\n[HARD_ANCHORS]\n" + body
    return summary[:_MAX_HARD_SUMMARY_CHARS]


def compact_completed_stage_results(
    messages: list[dict],
    *,
    stage: str,
    turn_start_idx: int = 0,
) -> tuple[list[dict], dict]:
    """Replace safe, completed-stage result previews with hard summaries.

    Tool call/result messages remain in place, so provider tool-call atomicity
    is preserved. Current-stage quantitative results are deliberately retained.
    """
    archive_tools = _ARCHIVE_BY_STAGE.get(str(stage or ""), frozenset())
    if not archive_tools:
        return messages, {"stage": stage, "archived": 0, "saved_chars": 0}

    names = _tool_call_names(messages[max(0, int(turn_start_idx)):])
    updated = list(messages)
    archived = 0
    saved_chars = 0
    for index in range(max(0, int(turn_start_idx)), len(messages)):
        message = messages[index]
        if message.get("role") != "tool":
            continue
        tool = names.get(str(message.get("tool_call_id") or ""), "")
        content = str(message.get("content") or "")
        if (
            tool not in archive_tools
            or STAGE_ARCHIVE_MARKER in content
            or len(content) < _MIN_ARCHIVE_CHARS
        ):
            continue
        replacement = _hard_summary(tool, content)
        if len(replacement) >= len(content):
            continue
        updated[index] = {**message, "content": replacement}
        archived += 1
        saved_chars += len(content) - len(replacement)
    return updated, {
        "stage": stage,
        "archived": archived,
        "saved_chars": saved_chars,
    }
