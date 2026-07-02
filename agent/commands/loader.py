"""Multi-source Command loader with protected built-in definitions."""
from __future__ import annotations

import logging
import os
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from .catalog import builtin_commands
from .models import CommandDef, CommandSource
from .parser import CommandError, parse_command_file
from .registry import CommandRegistry

log = logging.getLogger(__name__)
DEFAULT_USER_DIR = Path(os.getenv("BAA_COMMANDS_DIR", "~/.baa/commands")).expanduser()


@dataclass(frozen=True)
class CommandDiagnostic:
    path: str
    source: str
    error: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "source": self.source, "error": self.error}


class CommandLoader:
    """Load protected built-ins plus Workspace > user Markdown commands."""

    _cache_lock = threading.RLock()
    _cache: OrderedDict[tuple, tuple[tuple, CommandRegistry, tuple[CommandDiagnostic, ...]]] = (
        OrderedDict()
    )
    _cache_limit = 64

    def __init__(
        self,
        *,
        builtins: tuple[CommandDef, ...] | None = None,
        user_dir: Path | None = None,
        workspace_dir: Path | None = None,
    ) -> None:
        self.builtins = builtins if builtins is not None else builtin_commands()
        self.user_dir = user_dir if user_dir is not None else DEFAULT_USER_DIR
        self.workspace_dir = workspace_dir
        self._registry = CommandRegistry()
        self._diagnostics: list[CommandDiagnostic] = []

    @staticmethod
    def _directory_fingerprint(root: Path | None) -> tuple:
        if root is None:
            return ()
        try:
            if not root.is_dir():
                return (str(root), "missing")
            entries = []
            for path in sorted(root.rglob("*.md"), key=lambda item: str(item).lower()):
                stat = path.lstat()
                entries.append((
                    str(path.relative_to(root)).replace("\\", "/"),
                    stat.st_mtime_ns,
                    stat.st_size,
                    path.is_symlink(),
                ))
            return (str(root.resolve()), tuple(entries))
        except OSError as exc:
            return (str(root), "unreadable", type(exc).__name__)

    def _fingerprint(self) -> tuple:
        return (
            self._directory_fingerprint(self.user_dir),
            self._directory_fingerprint(self.workspace_dir),
        )

    def _cache_key(self) -> tuple:
        return (
            self.builtins,
            str(self.user_dir.resolve(strict=False)),
            str(self.workspace_dir.resolve(strict=False)) if self.workspace_dir else "",
        )

    def _scan(self, root: Path | None, source: CommandSource) -> list[CommandDef]:
        if root is None or not root.is_dir():
            return []
        found: list[CommandDef] = []
        for path in sorted(root.rglob("*.md"), key=lambda item: str(item).lower()):
            try:
                found.append(parse_command_file(root, path, source=source))
            except CommandError as exc:
                self._diagnostics.append(CommandDiagnostic(str(path), source, str(exc)))
                log.warning("[commands] skipping %s command %s: %s", source, path, exc)
        return found

    def _build_snapshot(self) -> CommandRegistry:
        self._diagnostics = []
        merged: dict[str, CommandDef] = {command.name: command for command in self.builtins}
        protected = {command.name for command in self.builtins if command.protected}
        for root, source in ((self.user_dir, "user"), (self.workspace_dir, "workspace")):
            for command in self._scan(root, source):
                if command.name in protected:
                    self._diagnostics.append(CommandDiagnostic(
                        str(command.path or ""), source,
                        f"cannot override protected built-in command: {command.name}",
                    ))
                    continue
                merged[command.name] = command

        registry = CommandRegistry()
        for command in merged.values():
            try:
                registry.register(command)
            except ValueError as exc:
                self._diagnostics.append(CommandDiagnostic(
                    str(command.path or "<builtin>"), command.source, str(exc),
                ))
        return registry.freeze()

    def load(self) -> CommandRegistry:
        key = self._cache_key()
        fingerprint = self._fingerprint()
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached is not None and cached[0] == fingerprint:
                self._cache.move_to_end(key)
                self._registry = cached[1]
                self._diagnostics = list(cached[2])
                return self._registry

        # Build outside the cache lock, then verify that no directory changed
        # during the scan before publishing the immutable snapshot.
        registry = self._build_snapshot()
        final_fingerprint = self._fingerprint()
        if final_fingerprint != fingerprint:
            registry = self._build_snapshot()
            final_fingerprint = self._fingerprint()

        diagnostics = tuple(self._diagnostics)
        with self._cache_lock:
            self._cache[key] = (final_fingerprint, registry, diagnostics)
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_limit:
                self._cache.popitem(last=False)
        self._registry = registry
        return registry

    @classmethod
    def clear_cache(cls) -> None:
        with cls._cache_lock:
            cls._cache.clear()

    def diagnostics(self) -> tuple[CommandDiagnostic, ...]:
        return tuple(self._diagnostics)

    @property
    def registry(self) -> CommandRegistry:
        return self._registry
