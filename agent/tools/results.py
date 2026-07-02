# -*- coding: utf-8 -*-
"""Structured tool result envelopes.

Tool messages are still sent to the LLM as strings for provider compatibility,
but the string now carries a compact JSON envelope with a readable text body.
This gives logs/UI/tests a stable contract without forcing every tool
implementation to return a new object type.
"""
from __future__ import annotations

import logging
import hashlib
import os
import uuid
import re
from datetime import datetime
from pathlib import Path
from infrastructure.paths import data_path

log = logging.getLogger(__name__)

import json
from dataclasses import dataclass, field
from typing import Any

TOOL_RESULT_CHAR_BUDGET = 2_000
TOOL_RESULT_PREVIEW_CHARS = 2_000
TOOL_RESULT_PREVIEW_LINES = 40
TOOL_RESULT_READ_MAX_CHARS = 4_000
_GLOBAL_RESULT_ROOT = data_path("outputs", "tool_results")


@dataclass(frozen=True)
class ToolResultPolicy:
    persist_threshold: int
    preview_chars: int
    preview_lines: int = 40


_DEFAULT_RESULT_POLICY = ToolResultPolicy(2_000, 2_000, 40)
_TOOL_RESULT_POLICIES = {
    "get_schema": ToolResultPolicy(2_000, 2_000, 60),
    "get_table_detail": ToolResultPolicy(3_000, 3_000, 60),
    "query_data": ToolResultPolicy(2_000, 2_000, 20),
    "run_analysis": ToolResultPolicy(3_000, 3_000, 40),
    "profile_data": ToolResultPolicy(3_000, 3_000, 40),
    "clean_data": ToolResultPolicy(3_000, 3_000, 40),
    "workspace_glob": ToolResultPolicy(2_000, 2_000, 40),
    "workspace_grep": ToolResultPolicy(2_000, 2_000, 40),
    "workspace_read_file": ToolResultPolicy(2_000, 2_000, 60),
    "read_tool_result": ToolResultPolicy(5_000, 4_600, 120),
    "export_excel": ToolResultPolicy(800, 800, 20),
    "export_report": ToolResultPolicy(800, 800, 20),
    "generate_ppt": ToolResultPolicy(800, 800, 20),
    "generate_dashboard": ToolResultPolicy(800, 800, 20),
}


def tool_result_policy(tool: str) -> ToolResultPolicy:
    return _TOOL_RESULT_POLICIES.get(str(tool or ""), _DEFAULT_RESULT_POLICY)


def classify_tool_error(raw: Any, tool: str = "") -> str:
    """Best-effort error taxonomy for tool outputs."""
    text = str(raw or "").strip()
    lower = text.lower()
    if not text:
        return ""
    if text.startswith("[ARG ERROR]"):
        return "argument_error"
    if "sql validation failed" in lower:
        return "sql_validation_error"
    if text.startswith("SQL Error:"):
        if "no such column" in lower or "column" in lower and "not found" in lower:
            return "field_not_found"
        if "no such table" in lower or "table" in lower and "not found" in lower:
            return "table_not_found"
        if "syntax" in lower or "parser" in lower:
            return "sql_syntax_error"
        return "sql_execution_error"
    if "no data source" in lower or "连接已断开" in text:
        return "datasource_disconnected"
    if "permission" in lower or "权限" in text:
        return "permission_error"
    if tool == "get_schema":
        if text.startswith("ERROR:") or text.startswith("工具执行错误"):
            return "tool_error"
        return ""
    if (
        lower.startswith("query returned no rows")
        or lower.startswith("empty result")
        or lower.startswith("no rows returned")
    ):
        return "empty_result"
    if text.startswith("[MCP ERROR]"):
        return "mcp_error"
    if text.startswith("ERROR:") or text.startswith("工具执行错误"):
        return "tool_error"
    return ""


@dataclass
class ToolResultEnvelope:
    tool: str
    ok: bool = True
    error: str = ""
    summary: str = ""
    data: Any = ""
    sources: list[dict] = field(default_factory=list)
    artifacts: list[dict] = field(default_factory=list)
    debug: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "type": "tool_result",
            "tool": self.tool,
            "ok": self.ok,
            "error": self.error,
            "summary": self.summary,
            "data": self.data,
            "sources": self.sources,
            "artifacts": self.artifacts,
            "debug": self.debug,
        }

    def to_model_text(self) -> str:
        """Provider-safe tool content.

        The first line is intentionally human-readable so older prompt habits
        still work; the JSON block gives future code a stable structure.
        """
        readable = self.summary or str(self.data)[:240]
        # Model payload omits debug and the duplicate summary. Full audit data
        # remains available through ``to_dict`` and SSE tool events.
        data = {
            "type": "tool_result",
            "ok": self.ok,
            "error": self.error,
            "data": self.data,
            "sources": self.sources,
            "artifacts": self.artifacts,
        }
        return (
            f"[TOOL_RESULT] {self.tool} {'OK' if self.ok else 'ERROR'}: {readable}\n"
            + json.dumps(data, ensure_ascii=False, default=str)
        )


def _json_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, default=str, indent=2)


def _preview_text(
    text: str,
    *,
    max_chars: int = TOOL_RESULT_PREVIEW_CHARS,
    max_lines: int = TOOL_RESULT_PREVIEW_LINES,
) -> str:
    max_chars = max(80, int(max_chars))
    max_lines = max(1, int(max_lines))
    lines = text.splitlines()
    preview = "\n".join(lines[:max_lines])
    suffix = (
        f"\n…[full result persisted; {len(text):,} chars / "
        f"{len(lines):,} lines total]"
    )
    body_cap = max(0, max_chars - len(suffix))
    preview = preview[:body_cap]
    return (preview + suffix)[-max_chars:] if not body_cap else preview + suffix


def _result_root(runtime: Any = None) -> Path:
    if runtime is not None and getattr(runtime, "cache_dir", None):
        return Path(runtime.cache_dir) / "tool_results"
    return _GLOBAL_RESULT_ROOT


def persist_large_tool_result(
    session_id: str,
    tool: str,
    raw: Any,
    *,
    runtime: Any = None,
    threshold: int = TOOL_RESULT_CHAR_BUDGET,
    deduplicate: bool = False,
    preview_chars: int = TOOL_RESULT_PREVIEW_CHARS,
    preview_lines: int = TOOL_RESULT_PREVIEW_LINES,
) -> tuple[Any, dict | None, dict]:
    """Persist oversized tool data and return a bounded model preview.

    The artifact file is self-describing and content-address-verified. The
    opaque artifact id is used for lookup; local filesystem paths never enter
    the model-visible envelope.
    """
    text = _json_text(raw)
    encoded = text.encode("utf-8")
    if len(text) <= max(1, int(threshold)):
        return raw, None, {"persisted": False, "chars": len(text)}

    digest = hashlib.sha256(encoded).hexdigest()
    artifact_id = f"tr_{digest[:32]}" if deduplicate else f"tr_{uuid.uuid4().hex}"
    root = _result_root(runtime)
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{artifact_id}.json"
    temp = root / f".{artifact_id}.{os.getpid()}.tmp"
    record = {
        "version": 1,
        "artifact_id": artifact_id,
        "session_id": session_id,
        "workspace_id": str(getattr(runtime, "workspace_id", "") or ""),
        "tool": tool,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "size_bytes": len(encoded),
        "sha256": digest,
        "content_type": "text/plain; charset=utf-8",
        "data": text,
    }
    if not target.exists():
        temp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        temp.replace(target)
    artifact = {
        "type": "tool_result",
        "artifact_id": artifact_id,
        "name": f"{tool} 完整结果",
        "uri": f"artifact://tool-result/{artifact_id}",
        "url": f"/api/session/{session_id}/tool-results/{artifact_id}",
        "size_bytes": len(encoded),
        "sha256": digest,
        "workspace_id": str(getattr(runtime, "workspace_id", "") or ""),
        "session_id": session_id,
    }
    debug = {
        "persisted": True,
        "artifact_id": artifact_id,
        "original_chars": len(text),
        "preview_chars": min(len(text), int(preview_chars)),
    }
    return _preview_text(
        text,
        max_chars=preview_chars,
        max_lines=preview_lines,
    ), artifact, debug


def load_tool_result_artifact(
    artifact_id: str, *, runtime: Any = None, workspace_root: Path | None = None,
) -> dict | None:
    """Load and verify an artifact from the active workspace or global store."""
    if not artifact_id.startswith("tr_") or not artifact_id[3:].isalnum():
        return None
    roots = []
    if runtime is not None and getattr(runtime, "cache_dir", None):
        roots.append(Path(runtime.cache_dir) / "tool_results")
    elif workspace_root is not None:
        roots.append(Path(workspace_root) / ".baa_cache" / "tool_results")
    roots.append(_GLOBAL_RESULT_ROOT)
    for root in roots:
        path = root / f"{artifact_id}.json"
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            text = str(record.get("data", ""))
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if record.get("artifact_id") == artifact_id and digest == record.get("sha256"):
                return record
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    return None


class ToolResultAccessError(ValueError):
    """Artifact is unavailable, corrupt, or outside the current session scope."""


def read_tool_result_artifact(
    artifact_id: str,
    *,
    allowed_artifacts: list[dict],
    session_id: str,
    workspace_id: str = "",
    runtime: Any = None,
    workspace_root: Path | None = None,
    offset: int = 0,
    limit: int = TOOL_RESULT_READ_MAX_CHARS,
    query: str = "",
) -> dict:
    """Read a verified, session-authorized artifact without exposing local paths."""
    if not str(session_id or ""):
        raise ToolResultAccessError("active session is required")
    artifact = next(
        (
            item for item in allowed_artifacts
            if str(item.get("artifact_id") or "") == str(artifact_id or "")
        ),
        None,
    )
    if artifact is None:
        raise ToolResultAccessError("artifact is not available in this session")
    expected_workspace = str(artifact.get("workspace_id") or "")
    active_workspace = str(workspace_id or "")
    if expected_workspace and expected_workspace != active_workspace:
        raise ToolResultAccessError("artifact belongs to a different workspace")
    record = load_tool_result_artifact(
        artifact_id,
        runtime=runtime,
        workspace_root=workspace_root,
    )
    if record is None:
        raise ToolResultAccessError("artifact is missing or failed SHA-256 verification")
    if str(record.get("workspace_id") or "") != expected_workspace:
        raise ToolResultAccessError("artifact workspace metadata mismatch")
    expected_session = str(artifact.get("session_id") or "")
    if expected_session and expected_session != str(record.get("session_id") or ""):
        raise ToolResultAccessError("artifact session metadata mismatch")
    expected_sha = str(artifact.get("sha256") or "")
    if expected_sha and expected_sha != str(record.get("sha256") or ""):
        raise ToolResultAccessError("artifact SHA-256 metadata mismatch")

    text = str(record.get("data") or "")
    bounded_limit = max(1, min(TOOL_RESULT_READ_MAX_CHARS, int(limit or 4000)))
    clean_query = str(query or "").strip()
    if clean_query:
        lower_text = text.lower()
        needle = clean_query.lower()
        positions: list[int] = []
        cursor = 0
        while len(positions) < 5:
            found = lower_text.find(needle, cursor)
            if found < 0:
                break
            positions.append(found)
            cursor = found + max(1, len(needle))
        if not positions:
            content = ""
        else:
            per_match = max(120, bounded_limit // len(positions))
            snippets = []
            for position in positions:
                start = max(0, position - per_match // 3)
                end = min(len(text), start + per_match)
                snippets.append(
                    f"[match at char {position}]\n{text[start:end]}"
                )
            content = "\n\n".join(snippets)[:bounded_limit]
        return {
            "artifact_id": artifact_id,
            "tool": str(record.get("tool") or ""),
            "sha256": str(record.get("sha256") or ""),
            "query": clean_query,
            "match_count": len(positions),
            "total_chars": len(text),
            "returned_chars": len(content),
            "content": content,
        }

    bounded_offset = max(0, min(len(text), int(offset or 0)))
    content = text[bounded_offset:bounded_offset + bounded_limit]
    next_offset = bounded_offset + len(content)
    return {
        "artifact_id": artifact_id,
        "tool": str(record.get("tool") or ""),
        "sha256": str(record.get("sha256") or ""),
        "offset": bounded_offset,
        "limit": bounded_limit,
        "total_chars": len(text),
        "returned_chars": len(content),
        "next_offset": next_offset if next_offset < len(text) else None,
        "content": content,
    }


def extract_tool_result_references(text: str) -> list[str]:
    """Extract stable artifact URIs from a model-facing tool envelope."""
    refs = re.findall(r"artifact://tool-result/tr_[a-fA-F0-9]+", text or "")
    return list(dict.fromkeys(refs))


def truncate_tool_result_preserving_refs(text: str, cap: int) -> str:
    """Bound a tool message without discarding its recoverable artifact ids."""
    if len(text) <= cap:
        return text
    refs = extract_tool_result_references(text)
    suffix = f"\n…[result truncated for history, {len(text):,} chars total]"
    if refs:
        suffix += "\n[RECOVERABLE_ARTIFACTS] " + ", ".join(refs)
    return text[:cap] + suffix


def truncate_tool_result_to_total_cap(text: str, cap: int) -> str:
    """Bound the complete preview, including its recovery suffix, to ``cap``.

    Unlike ``truncate_tool_result_preserving_refs`` the cap here applies to the
    final string. This is useful for aggregate payload budgeting.
    """
    cap = max(120, int(cap))
    if len(text) <= cap:
        return text
    refs = extract_tool_result_references(text)
    suffix = f"\n…[result truncated for payload, {len(text):,} chars total]"
    if refs:
        suffix += "\n[RECOVERABLE_ARTIFACTS] " + ", ".join(refs)
    if len(suffix) >= cap:
        # Keep the recovery reference tail when space is extremely tight.
        return suffix[-cap:]
    return text[:cap - len(suffix)] + suffix


def _summarize_content(content: Any, max_chars: int = 220) -> str:
    if isinstance(content, str):
        text = content.strip()
    else:
        text = json.dumps(content, ensure_ascii=False, default=str)
    text = " ".join(text.split())
    return text[:max_chars] + ("..." if len(text) > max_chars else "")


def make_tool_result(
    tool: str,
    raw: Any,
    *,
    ok: bool | None = None,
    error: str = "",
    summary: str = "",
    sources: list[dict] | None = None,
    artifacts: list[dict] | None = None,
    debug: dict | None = None,
    session_id: str = "",
    runtime: Any = None,
    result_char_budget: int | None = None,
) -> ToolResultEnvelope:
    error = error or classify_tool_error(raw, tool=tool)
    if ok is None:
        ok = not bool(error)
    policy = tool_result_policy(tool)
    model_data = raw
    result_artifact = None
    budget_debug = {}
    if session_id and ok:
        try:
            threshold = (
                max(1, int(result_char_budget))
                if result_char_budget is not None
                else policy.persist_threshold
            )
            preview_chars = (
                min(policy.preview_chars, threshold)
                if result_char_budget is not None
                else policy.preview_chars
            )
            model_data, result_artifact, budget_debug = persist_large_tool_result(
                session_id, tool, raw, runtime=runtime, threshold=threshold,
                deduplicate=tool == "get_schema",
                preview_chars=preview_chars,
                preview_lines=policy.preview_lines,
            )
        except Exception as exc:
            log.warning("[tool-result] persist failed tool=%s: %s", tool, exc)
            budget_debug = {"persisted": False, "error": str(exc)}
    elif not ok:
        error_text = _json_text(raw)
        if len(error_text) > 1_500:
            model_data = error_text[:1_500] + "\n…[error details truncated]"
    result_artifacts = list(artifacts or [])
    if result_artifact is not None:
        result_artifacts.append(result_artifact)
    result_debug = dict(debug or {})
    if budget_debug:
        result_debug["result_budget"] = budget_debug
    return ToolResultEnvelope(
        tool=tool,
        ok=ok,
        error=error,
        summary=summary or _summarize_content(raw),
        data=model_data,
        sources=list(sources or []),
        artifacts=result_artifacts,
        debug=result_debug,
    )
