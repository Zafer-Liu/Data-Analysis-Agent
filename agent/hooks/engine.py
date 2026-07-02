"""Synchronous hook engine used by the Flask streaming path."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Iterable

from .executors import execute_action
from .models import Hook, HookContext, ToolRejectedError

log = logging.getLogger(__name__)
_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="baa-hook")


@dataclass
class HookNotification:
    hook_id: str
    event: str
    output: str = ""
    success: bool = True

    def to_event(self) -> dict:
        return {
            "type": "hook_event",
            "hook_id": self.hook_id,
            "event": self.event,
            "ok": self.success,
            "output": self.output[:500],
        }


class HookEngine:
    def __init__(
        self,
        hooks: Iterable[Hook],
        *,
        enabled: bool = True,
        allow_command_hooks: bool = False,
        fire_and_forget_side_effects: bool = False,
    ):
        self.enabled = bool(enabled)
        self.allow_command_hooks = bool(allow_command_hooks)
        self.fire_and_forget_side_effects = bool(fire_and_forget_side_effects)
        self.hooks = list(hooks or [])
        self._prompt_messages: list[str] = []
        self._notifications: list[HookNotification] = []

    def find_matching_hooks(self, event: str, ctx: HookContext) -> list[Hook]:
        if not self.enabled:
            return []
        return [hook for hook in self.hooks if hook.event == event and hook.should_run(ctx)]

    def run_hooks(self, event: str, ctx: HookContext) -> list[HookNotification]:
        matched = self.find_matching_hooks(event, ctx.child(event_name=event))
        for hook in matched:
            hook_ctx = ctx.child(event_name=event)
            if hook.async_exec or self._should_fire_and_forget(event, hook):
                _EXECUTOR.submit(self._run_single_background, hook, hook_ctx)
            else:
                self._run_single_safely(hook, hook_ctx)
        return self.drain_notifications()

    def run_pre_tool_hooks(self, ctx: HookContext) -> ToolRejectedError | None:
        event_ctx = ctx.child(event_name="pre_tool_use")
        for hook in self.find_matching_hooks("pre_tool_use", event_ctx):
            if self._should_fire_and_forget("pre_tool_use", hook):
                _EXECUTOR.submit(self._run_single_background, hook, event_ctx)
                continue
            notification = self._run_single_safely(hook, event_ctx)
            if hook.reject:
                reason = (notification.output if notification else "") or "tool call rejected by hook"
                return ToolRejectedError(event_ctx.tool_name, reason, hook.id)
        return None

    def drain_prompt_messages(self) -> list[str]:
        items = self._prompt_messages[:]
        self._prompt_messages.clear()
        return items

    def drain_notifications(self) -> list[HookNotification]:
        items = self._notifications[:]
        self._notifications.clear()
        return items

    def _should_fire_and_forget(self, event: str, hook: Hook) -> bool:
        if not self.fire_and_forget_side_effects:
            return False
        if hook.reject:
            return False
        return hook.action.type in {"http", "command"}

    def _run_single_background(self, hook: Hook, ctx: HookContext) -> None:
        try:
            result = execute_action(hook.action, ctx, allow_command=self.allow_command_hooks)
            if not result.success:
                log.warning(
                    "[hooks] background hook failed id=%s event=%s output=%s",
                    hook.id,
                    hook.event,
                    str(result.output or "")[:500],
                )
        except Exception:
            log.exception("[hooks] background hook failed id=%s event=%s", hook.id, hook.event)
        finally:
            hook.mark_executed()

    def _run_single_safely(self, hook: Hook, ctx: HookContext) -> HookNotification:
        try:
            result = execute_action(hook.action, ctx, allow_command=self.allow_command_hooks)
            output = str(result.output or "")
            success = bool(result.success)
        except Exception as exc:
            log.exception("[hooks] hook failed id=%s event=%s", hook.id, hook.event)
            output = str(exc)
            success = False
        hook.mark_executed()
        if hook.action.type == "prompt" and success and output.strip():
            self._prompt_messages.append(output.strip())
        notification = HookNotification(hook.id, hook.event, output, success)
        self._notifications.append(notification)
        return notification
