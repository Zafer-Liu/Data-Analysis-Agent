"""Hook event names supported by the application."""

from __future__ import annotations

from enum import Enum


class HookEvent(str, Enum):
    STARTUP = "startup"
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    USER_PROMPT_SUBMIT = "user_prompt_submit"
    TURN_START = "turn_start"
    TURN_END = "turn_end"
    TOOL_CALL = "tool_call"
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    PERMISSION_REQUEST = "permission_request"
    SUBAGENT_START = "subagent_start"
    SUBAGENT_STOP = "subagent_stop"
    PRE_COMPACT = "pre_compact"
    POST_COMPACT = "post_compact"
    STOP = "stop"
    ERROR = "error"


SUPPORTED_EVENTS = {event.value for event in HookEvent}

EVENT_ALIASES = {
    "sessionstart": HookEvent.SESSION_START.value,
    "session_start": HookEvent.SESSION_START.value,
    "sessionend": HookEvent.SESSION_END.value,
    "session_end": HookEvent.SESSION_END.value,
    "userpromptsubmit": HookEvent.USER_PROMPT_SUBMIT.value,
    "user_prompt_submit": HookEvent.USER_PROMPT_SUBMIT.value,
    "turnbegin": HookEvent.TURN_START.value,
    "turn_begin": HookEvent.TURN_START.value,
    "turnstart": HookEvent.TURN_START.value,
    "turn_start": HookEvent.TURN_START.value,
    "turnend": HookEvent.TURN_END.value,
    "turn_end": HookEvent.TURN_END.value,
    "toolcall": HookEvent.TOOL_CALL.value,
    "tool_call": HookEvent.TOOL_CALL.value,
    "pretooluse": HookEvent.PRE_TOOL_USE.value,
    "pre_tool_use": HookEvent.PRE_TOOL_USE.value,
    "posttooluse": HookEvent.POST_TOOL_USE.value,
    "post_tool_use": HookEvent.POST_TOOL_USE.value,
    "permissionrequest": HookEvent.PERMISSION_REQUEST.value,
    "permission_request": HookEvent.PERMISSION_REQUEST.value,
    "subagentstart": HookEvent.SUBAGENT_START.value,
    "subagent_start": HookEvent.SUBAGENT_START.value,
    "subagentstop": HookEvent.SUBAGENT_STOP.value,
    "subagent_stop": HookEvent.SUBAGENT_STOP.value,
    "precompact": HookEvent.PRE_COMPACT.value,
    "pre_compact": HookEvent.PRE_COMPACT.value,
    "postcompact": HookEvent.POST_COMPACT.value,
    "post_compact": HookEvent.POST_COMPACT.value,
    "stop": HookEvent.STOP.value,
    "startup": HookEvent.STARTUP.value,
    "error": HookEvent.ERROR.value,
}


def normalize_event_name(value: str) -> str:
    raw = str(value or "").strip()
    key = raw.replace("-", "_").replace(" ", "_")
    compact = key.replace("_", "").lower()
    return EVENT_ALIASES.get(compact, EVENT_ALIASES.get(key.lower(), raw))
