"""Safe layered project-instruction loading."""
from __future__ import annotations

import os
import re
import threading
from pathlib import Path

from infrastructure.paths import data_path

MAX_INSTRUCTION_CHARS = 25_000
MAX_INCLUDE_DEPTH = 5
_INCLUDE_RE = re.compile(r"^\s*@include\s+<([^>]+)>\s*$")

# ── mtime cache ──────────────────────────────────────────────────────────────
# Key: (workspace_id, (mtime_USER, mtime_WORKSPACE, mtime_LOCAL))
# Value: rendered instruction-section string.
# 0.0 means the file did not exist at last sampling.
# @include targets are NOT individually tracked; editing the parent file
# changes its mtime and invalidates the entry naturally.
_cache: dict[tuple, str] = {}
_cache_lock = threading.Lock()


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except (OSError, ValueError):
        return False


def _workspace_root(workspace_id: str) -> Path | None:
    if not workspace_id:
        return None
    from data.workspace import workspace_manager

    root = workspace_manager.root_for_workspace(str(workspace_id))
    return Path(root).resolve(strict=False) if root else None


def _expand(path: Path, root: Path, *, visited: set[Path], depth: int) -> str:
    resolved = path.resolve(strict=False)
    if not _within(resolved, root):
        return "<!-- @include blocked: path outside root -->"
    if depth > MAX_INCLUDE_DEPTH:
        return "<!-- @include blocked: maximum depth reached -->"
    if resolved in visited:
        return "<!-- @include blocked: cycle detected -->"
    if not resolved.is_file():
        return f"<!-- @include missing: {path.name} -->"
    try:
        text = resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return f"<!-- @include unreadable: {path.name} -->"

    visited.add(resolved)
    lines: list[str] = []
    for line in text.splitlines():
        match = _INCLUDE_RE.match(line)
        if not match:
            lines.append(line)
            continue
        raw = match.group(1).strip()
        candidate = (resolved.parent / raw).resolve(strict=False)
        if not _within(candidate, root):
            lines.append("<!-- @include blocked: path outside root -->")
        elif not candidate.exists():
            lines.append(f"<!-- @include missing: {raw} -->")
        else:
            lines.append(_expand(candidate, root, visited=visited, depth=depth + 1))
    visited.remove(resolved)
    return "\n".join(lines)


def load_instruction_section(*, workspace_id: str = "", user_id: str = "") -> str:
    """Return a bounded, trusted instructions block or an empty string.

    Results are cached by (workspace_id, mtime-tuple).  A file being created,
    modified, or deleted invalidates the entry automatically; @include targets
    are not individually tracked but their parent file's mtime covers the common
    edit-and-save workflow.
    """
    del user_id  # Reserved for future per-user data-root partitioning.
    user_root = data_path().resolve(strict=False)
    candidates: list[tuple[str, Path, Path]] = [
        ("USER", user_root / "ZHIXI.md", user_root),
    ]
    workspace_root = _workspace_root(workspace_id)
    if workspace_root:
        candidates.extend([
            ("WORKSPACE", workspace_root / "ZHIXI.md", workspace_root),
            ("LOCAL", workspace_root / "ZHIXI.local.md", workspace_root),
        ])

    mtime_key = tuple(_mtime(path) for _, path, _ in candidates)
    cache_key = (workspace_id, mtime_key)
    with _cache_lock:
        if cache_key in _cache:
            return _cache[cache_key]

    sections: list[str] = []
    for label, path, root in candidates:
        if not path.is_file():
            continue
        content = _expand(path, root, visited=set(), depth=0).strip()
        if content:
            sections.append(f"[{label} INSTRUCTIONS]\n{content}")
    if not sections:
        result = ""
    else:
        content = "\n\n---\n\n".join(sections)
        if len(content) > MAX_INSTRUCTION_CHARS:
            content = content[:MAX_INSTRUCTION_CHARS] + "\n<!-- instructions truncated -->"
        result = f"\n\n[PROJECT INSTRUCTIONS]\n{content}\n[END PROJECT INSTRUCTIONS]"

    with _cache_lock:
        # Evict stale entries for the same workspace to bound cache size.
        stale = [k for k in _cache if k[0] == workspace_id and k != cache_key]
        for k in stale:
            del _cache[k]
        _cache[cache_key] = result
    return result
