"""A2A 1.0 wire models for the local, session-scoped agent transport.

This module does not call a model or perform network requests. The team store
owns durable tasks; the existing delegated executor supplies their results.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import uuid

PROTOCOL_VERSION = "1.0"
TERMINAL_STATES = frozenset({"completed", "failed", "canceled", "rejected"})
MAX_MESSAGE_CHARS = 20_000
MAX_HISTORY = 50


class A2AError(ValueError):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def text_message(
    text: str, *, role: str = "user", context_id: str = "", task_id: str = "",
    metadata: dict | None = None,
) -> dict:
    message = {
        "messageId": uuid.uuid4().hex,
        "role": "ROLE_USER" if role == "user" else "ROLE_AGENT", "parts": [{"text": str(text)}],
    }
    if context_id:
        message["contextId"] = context_id
    if task_id:
        message["taskId"] = task_id
    if metadata:
        message["metadata"] = deepcopy(metadata)
    return message


def validate_message(value) -> dict:
    """Accept only the text input modes advertised by our Agent Card."""
    if not isinstance(value, dict) or "kind" in value or value.get("role") != "ROLE_USER":
        raise A2AError(-32602, "message must be an A2A user Message")
    message_id = value.get("messageId")
    if not isinstance(message_id, str) or not message_id.strip() or len(message_id) > 200:
        raise A2AError(-32602, "messageId must be a non-empty string of at most 200 characters")
    for key in ("contextId", "taskId"):
        if key in value and (not isinstance(value[key], str) or not value[key] or len(value[key]) > 200):
            raise A2AError(-32602, f"{key} must be a non-empty string of at most 200 characters")
    parts = value.get("parts")
    if not isinstance(parts, list) or not 1 <= len(parts) <= 20:
        raise A2AError(-32602, "message.parts must contain 1-20 text parts")
    for part in parts:
        if (not isinstance(part, dict) or not isinstance(part.get("text"), str)
                or any(key in part for key in ("kind", "raw", "url", "data"))):
            raise A2AError(-32005, "Only text parts are supported")
    text = message_text(value)
    if not text.strip() or len(text) > MAX_MESSAGE_CHARS:
        raise A2AError(-32602, f"message text must contain 1-{MAX_MESSAGE_CHARS} characters")
    if "metadata" in value and not isinstance(value["metadata"], dict):
        raise A2AError(-32602, "message.metadata must be an object")
    return deepcopy(value)


def message_text(message: dict) -> str:
    return "\n".join(part.get("text", "") for part in message.get("parts", []) if isinstance(part, dict) and isinstance(part.get("text"), str))


def task_state(task: dict) -> str:
    return str(task.get("status", {}).get("state", "")).removeprefix("TASK_STATE_").lower().replace("_", "-")


def new_task(message: dict, *, context_id: str, member_name: str) -> dict:
    task_id = uuid.uuid4().hex
    incoming = deepcopy(message)
    incoming.update({"taskId": task_id, "contextId": context_id})
    return {
        "id": task_id, "contextId": context_id,
        "status": {"state": "TASK_STATE_SUBMITTED", "timestamp": timestamp()},
        "history": [incoming], "artifacts": [],
        "metadata": {"member_name": member_name},
    }


def set_task_state(task: dict, state: str, *, text: str = "") -> None:
    if task_state(task) in TERMINAL_STATES:
        raise A2AError(-32004, "Task is already terminal; submit a new message without taskId")
    if state not in {"submitted", "working", "input-required", "auth-required", *TERMINAL_STATES}:
        raise ValueError("Invalid task state: " + state)
    status = {"state": "TASK_STATE_" + state.upper().replace("-", "_"), "timestamp": timestamp()}
    if text:
        message = text_message(text, role="agent", context_id=task["contextId"], task_id=task["id"])
        status["message"] = message
        task.setdefault("history", []).append(message)
        task["history"] = task["history"][-MAX_HISTORY:]
    task["status"] = status


def finish_task(task: dict, text: str, *, ok: bool) -> dict:
    if not str(text).strip():
        text, ok = "Agent produced no final output.", False
    set_task_state(task, "completed" if ok else "failed", text=text)
    if ok:
        task["artifacts"] = [{
            "artifactId": uuid.uuid4().hex, "name": "result",
            "parts": [{"text": text}],
        }]
    return task["status"]["message"]


def public_task(task: dict, history_length: int | None = None) -> dict:
    result = deepcopy(task)
    if history_length is not None:
        if isinstance(history_length, bool) or not isinstance(history_length, int) or history_length < 0:
            raise A2AError(-32602, "historyLength must be a non-negative integer")
        result["history"] = result.get("history", [])[-history_length:] if history_length else []
    return result


def agent_card(member: dict, *, url: str) -> dict:
    from agent.teams.profiles import DELEGATED_READ_TOOLS

    profile = member.get("profile") or {}
    configured = profile.get("allowed_tools")
    tools = sorted(DELEGATED_READ_TOOLS if configured is None else DELEGATED_READ_TOOLS.intersection(configured))
    return {
        "name": member["name"],
        "description": str(member.get("instructions") or member.get("role") or "Bounded delegated analyst"),
        "supportedInterfaces": [{"url": url, "protocolBinding": "JSONRPC", "protocolVersion": PROTOCOL_VERSION}],
        "version": "1.0.0",
        "capabilities": {"streaming": False, "pushNotifications": False, "extendedAgentCard": False},
        "defaultInputModes": ["text/plain"], "defaultOutputModes": ["text/plain"],
        "skills": [{
            "id": str(member.get("agent_id") or member["name"]),
            "name": str(member.get("role") or "analyst"),
            "description": "Execute a bounded task with an isolated prompt and session-scoped read-only tools.",
            "tags": ["analysis", "read-only", *tools],
        }],
    }
