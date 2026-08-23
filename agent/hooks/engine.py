"""Synchronous hook engine used by the Flask streaming path."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from collections import deque
from dataclasses import dataclass
from threading import BoundedSemaphore, Lock
from typing import Iterable

from .executors import execute_action
from .models import Hook, HookContext, ToolRejectedError

log = logging.getLogger(__name__)
# IO-bound hook execution (HTTP/command/prompt actions spend most time waiting).
# Bumped from 4 → 8: hooks are network/IO heavy, GIL is released during waits,
# and DuckDB is not touched by hook executors, so higher concurrency is safe.
_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="baa-hook")
_BACKGROUND_CAPACITY = 32
_BACKGROUND_SLOTS = BoundedSemaphore(_BACKGROUND_CAPACITY)


class OnceRegistry:
    """Process-local, thread-safe reservation for once hooks.

    The registry outlives individual HookEngine instances, which are recreated
    for lifecycle endpoints and each chat turn.  A reservation prevents a slow
    asynchronous hook from being scheduled twice before its first run finishes.
    """

    def __init__(self, max_completed: int = 10_000) -> None:
        self._lock = Lock()
        self._running: set[tuple[str, str]] = set()
        self._completed: set[tuple[str, str]] = set()
        self._completed_order: deque[tuple[str, str]] = deque()
        self._max_completed = max(1, int(max_completed))

    def reserve(self, hook: Hook, ctx: HookContext) -> tuple[str, str] | None:
        if not hook.once:
            return ("", "")
        scope = hook.once_scope
        if scope == "turn":
            scope_id = ctx.turn_id or ctx.session_id or "anonymous"
        elif scope == "session":
            scope_id = ctx.session_id or "anonymous"
        else:
            scope_id = "global"
        key = (hook.id, f"{scope}:{scope_id}")
        with self._lock:
            if key in self._running or key in self._completed:
                return None
            self._running.add(key)
        return key

    def complete(self, key: tuple[str, str]) -> None:
        if not key[0]:
            return
        with self._lock:
            self._running.discard(key)
            self._completed.add(key)
            self._completed_order.append(key)
            while len(self._completed_order) > self._max_completed:
                self._completed.discard(self._completed_order.popleft())

    def release(self, key: tuple[str, str]) -> None:
        if key[0]:
            with self._lock:
                self._running.discard(key)


_ONCE_REGISTRY = OnceRegistry()


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
        once_registry: OnceRegistry | None = None,
    ):
        self.enabled = bool(enabled)
        self.allow_command_hooks = bool(allow_command_hooks)
        self.fire_and_forget_side_effects = bool(fire_and_forget_side_effects)
        self.hooks = list(hooks or [])
        self._once_registry = once_registry or _ONCE_REGISTRY
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
            reservation = self._once_registry.reserve(hook, hook_ctx)
            if reservation is None:
                continue
            if hook.async_exec or self._should_fire_and_forget(event, hook):
                self._submit_background(hook, hook_ctx, reservation)
            else:
                self._run_single_safely(hook, hook_ctx, reservation)
        return self.drain_notifications()

    def run_pre_tool_hooks(self, ctx: HookContext) -> ToolRejectedError | None:
        event_ctx = ctx.child(event_name="pre_tool_use")
        for hook in self.find_matching_hooks("pre_tool_use", event_ctx):
            reservation = self._once_registry.reserve(hook, event_ctx)
            if reservation is None:
                continue
            if self._should_fire_and_forget("pre_tool_use", hook):
                self._submit_background(hook, event_ctx, reservation)
                continue
            notification = self._run_single_safely(hook, event_ctx, reservation)
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

    def _submit_background(
        self, hook: Hook, ctx: HookContext, reservation: tuple[str, str]
    ) -> bool:
        if not _BACKGROUND_SLOTS.acquire(blocking=False):
            self._once_registry.release(reservation)
            output = f"background hook queue is full (capacity={_BACKGROUND_CAPACITY})"
            self._notifications.append(HookNotification(hook.id, hook.event, output, False))
            log.warning("[hooks] %s id=%s event=%s", output, hook.id, hook.event)
            return False
        try:
            _EXECUTOR.submit(self._run_single_background, hook, ctx, reservation)
        except RuntimeError:
            _BACKGROUND_SLOTS.release()
            self._once_registry.release(reservation)
            log.exception("[hooks] failed to schedule hook id=%s event=%s", hook.id, hook.event)
            return False
        return True

    def _run_single_background(self, hook: Hook, ctx: HookContext, reservation: tuple[str, str]) -> None:
        try:
            result = execute_action(hook.action, ctx, allow_command=self.allow_command_hooks)
            notification = HookNotification(hook.id, hook.event, str(result.output or ""), bool(result.success))
            self._record_trigger(hook, notification, ctx, hook.action.type)
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
            self._once_registry.complete(reservation)
            _BACKGROUND_SLOTS.release()

    def _run_single_safely(
        self, hook: Hook, ctx: HookContext, reservation: tuple[str, str]
    ) -> HookNotification:
        try:
            result = execute_action(hook.action, ctx, allow_command=self.allow_command_hooks)
            output = str(result.output or "")
            success = bool(result.success)
        except Exception as exc:
            log.exception("[hooks] hook failed id=%s event=%s", hook.id, hook.event)
            output = str(exc)
            success = False
        hook.mark_executed()
        self._once_registry.complete(reservation)
        if hook.action.type == "prompt" and success and output.strip():
            self._prompt_messages.append(output.strip())
        notification = HookNotification(hook.id, hook.event, output, success)
        self._notifications.append(notification)
        self._record_trigger(hook, notification, ctx, hook.action.type)
        return notification

    @staticmethod
    def _record_trigger(hook: Hook, notification: HookNotification, ctx: HookContext, action_type: str) -> None:
        if hook.internal_endpoint or ctx.extra.get("test_mode"):
            return
        try:
            from data.hooks_store import record_hook_trigger

            record_hook_trigger(hook, notification, ctx, action_type=action_type)
        except Exception:
            log.exception("[hooks] failed to persist trigger record id=%s", notification.hook_id)
