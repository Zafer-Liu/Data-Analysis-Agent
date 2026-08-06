"""Hook action executors."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import urllib.request

from .models import Action, ActionResult, HookContext


def execute_action(action: Action, ctx: HookContext, *, allow_command: bool = False) -> ActionResult:
    if action.type == "prompt":
        return ActionResult(output=ctx.expand(action.message), success=True)
    if action.type == "http":
        return _execute_http(action, ctx)
    if action.type == "command":
        if not allow_command:
            return ActionResult(output="command hooks are disabled", success=False)
        return _execute_command(action, ctx)
    return ActionResult(output=f"unsupported hook action: {action.type}", success=False)


def _execute_command(action: Action, ctx: HookContext) -> ActionResult:
    command = ctx.expand(action.command)
    try:
        args = shlex.split(command, posix=os.name != "nt")
    except ValueError as exc:
        return ActionResult(output=f"invalid command syntax: {exc}", success=False)
    if not args:
        return ActionResult(output="command is empty", success=False)
    try:
        completed = subprocess.run(
            args,
            shell=False,
            capture_output=True,
            text=True,
            timeout=action.timeout,
        )
    except subprocess.TimeoutExpired:
        return ActionResult(output=f"command timed out after {action.timeout}s", success=False)
    output = (completed.stdout or completed.stderr or "").strip()
    return ActionResult(output=output[:4000], success=completed.returncode == 0)


def _execute_http(action: Action, ctx: HookContext) -> ActionResult:
    url = ctx.expand(action.url)
    if not url.startswith(("http://", "https://")):
        return ActionResult(output="http action only supports http:// and https:// URLs", success=False)
    method = (action.method or "POST").upper()
    body = ctx.expand(action.body)
    data = None
    headers = {"Content-Type": "application/json", **ctx.expand(action.headers)}
    if body is not None and method not in {"GET", "HEAD"}:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    try:
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=action.timeout) as response:
            output = response.read(4096).decode("utf-8", errors="replace")
            return ActionResult(output=output, success=200 <= response.status < 300)
    except Exception as exc:
        return ActionResult(output=str(exc), success=False)
