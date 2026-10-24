# -*- coding: utf-8 -*-
"""Restricted Bash-like facade for workspace tools.

This borrows MewCode's compact command UX and timeout/error shape without
exposing ``create_subprocess_shell``. Every accepted command maps to an
existing workspace-scoped operation; shell operators and unknown executables
are rejected before execution.
"""
from __future__ import annotations

import os
import re
import shlex
from typing import Any

from .files import WorkspaceToolError, WorkspaceToolService

MAX_BASH_TIMEOUT = 120
_SHELL_OPERATOR_RE = re.compile(r"(?:\r|\n|&&|\|\||[|;<>`]|\$\()")


class WorkspaceBashService:
    """Execute a small, auditable command vocabulary inside one workspace."""

    def __init__(self, session_id: str, *, workspace_id: str | None = None) -> None:
        self.session_id = session_id
        self.files = WorkspaceToolService(session_id, workspace_id=workspace_id)

    @staticmethod
    def _tokens(command: str) -> list[str]:
        if not isinstance(command, str) or not command.strip():
            raise WorkspaceToolError("command is required")
        if _SHELL_OPERATOR_RE.search(command):
            raise WorkspaceToolError(
                "shell operators, pipes, redirection, substitutions, and command chaining are not supported"
            )
        try:
            tokens = shlex.split(command, posix=True)
        except ValueError as exc:
            raise WorkspaceToolError(f"invalid command quoting: {exc}") from exc
        if not tokens:
            raise WorkspaceToolError("command is required")
        return tokens

    @staticmethod
    def _result(command: str, output: Any) -> dict:
        return {"command": command, "exit_code": 0, "output": output}

    def execute(self, command: str, timeout: int = 30, *, confirm: bool = False) -> dict:
        tokens = self._tokens(command)
        timeout = max(1, min(int(timeout), MAX_BASH_TIMEOUT))
        executable = os.path.basename(tokens[0]).lower()
        if executable.endswith(".exe"):
            executable = executable[:-4]
        args = tokens[1:]

        if executable == "pwd" and not args:
            runtime = self.files._runtime()
            return self._result(command, {"path": "workspace://user", "name": runtime.workdir.name})

        if executable in {"ls", "dir"} and len(args) <= 1:
            return self._result(command, self.files.glob("**/*", args[0] if args else ""))

        if executable == "cat" and len(args) == 1:
            return self._result(command, self.files.read_file(args[0]))

        if executable == "rg" and 1 <= len(args) <= 2:
            return self._result(command, self.files.grep(args[0], args[1] if len(args) == 2 else "."))

        if executable in {"sha256sum", "shasum"} and len(args) == 1:
            return self._result(command, self.files.command("checksum", args[0], timeout=timeout))

        if executable in {"json_validate", "json-validate"} and len(args) == 1:
            return self._result(command, self.files.command("json_validate", args[0], timeout=timeout))

        if executable == "json" and len(args) == 2 and args[0].lower() == "validate":
            return self._result(command, self.files.command("json_validate", args[1], timeout=timeout))

        if executable == "git" and args:
            subcommand = args[0].lower()
            if subcommand == "status" and len(args) == 1:
                return self._result(command, self.files.command("git_status", timeout=timeout))
            if subcommand == "log" and len(args) == 1:
                return self._result(command, self.files.command("git_log", timeout=timeout))
            if subcommand == "diff" and len(args) <= 2:
                return self._result(
                    command, self.files.command("git_diff", args[1] if len(args) == 2 else ".", timeout=timeout),
                )
            raise WorkspaceToolError("only read-only git status, git log, and git diff [path] are supported")

        if executable in {"python", "python3"} and len(args) in {2, 3} and args[:2] == ["-m", "compileall"]:
            return self._result(
                command, self.files.command("python_compile", args[2] if len(args) == 3 else ".", timeout=timeout),
            )

        if executable in {"rm", "del"} and len(args) == 1:
            return self._result(command, self.files.delete_file(args[0], confirm=confirm))

        if executable in {"mv", "move", "ren", "rename"} and len(args) == 2:
            return self._result(
                command, self.files.move_file(args[0], args[1], confirm_overwrite=confirm),
            )

        raise WorkspaceToolError(
            "unsupported command; allowed: pwd, ls/dir, cat, rg, sha256sum, "
            "json_validate/json validate, git status/log/diff, python -m compileall, "
            "rm/del, mv/move/ren"
        )
