#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Automatic cleanup of runtime artifacts (uploads/ and outputs/).

Why this exists
---------------
Without TTL pruning, `uploads/` grows unbounded with every uploaded
spreadsheet (users routinely upload 50+ MB Excel files), and
`outputs/charts/` collects an HTML file per chart generation.
Disk pressure aside, these directories also accumulate sensitive
user data (PII in source data, embedded credentials in chart titles, etc.).

Policy
------
Each rule is `(directory, max_age_days)`. A file older than `max_age_days`
(by mtime) is deleted on the next sweep. Sub-directories are walked.
Empty leaf directories are pruned after their files are.

Defaults are conservative — they keep enough history for normal workflows
(re-opening last week's analysis works fine) while bounding worst-case growth.

Overrides
---------
Set environment variables before `setup_cleanup` runs:
    BAA_CLEANUP_UPLOAD_DAYS=14   # default 30
    BAA_CLEANUP_OUTPUT_DAYS=30   # default 90
    BAA_CLEANUP_DISABLED=1       # skip cleanup entirely (e.g. dev work)
    BAA_CLEANUP_INTERVAL_HOURS=6 # default 24

Threading
---------
A daemon thread sleeps `interval_hours` between sweeps. It dies with the
process; no shutdown signal needed. The very first sweep runs synchronously
during `setup_cleanup()` so anything stale at boot disappears immediately.
"""
import logging
import os
import threading
import time
from pathlib import Path
from typing import Iterable, Tuple
from infrastructure.artifact_lifecycle import (
    prune_registry_for_paths,
    soft_delete_session_group,
)
from infrastructure.paths import data_root

log = logging.getLogger(__name__)

# (relative path, max age in days, "label for logs")
# Session/ and Dashboard/ are intentionally NOT swept by these age rules —
# those are user-saved artifacts with their own UI delete buttons, and losing
# them silently would be a worse failure than disk growth. The only exception
# is _sweep_stale_autosaves below: long-idle autosave_*.json go to the
# recoverable session trash, never straight to unlink.
DEFAULT_RULES: Tuple[Tuple[str, int, str], ...] = (
    ("uploads",          int(os.environ.get("BAA_CLEANUP_UPLOAD_DAYS", "30")), "uploads"),
    ("outputs/charts",   int(os.environ.get("BAA_CLEANUP_OUTPUT_DAYS", "90")), "outputs/charts"),
    ("outputs/exports",  int(os.environ.get("BAA_CLEANUP_OUTPUT_DAYS", "90")), "outputs/exports"),
)


def _sweep_one(root: Path, max_age_days: int, label: str, base_dir: Path | None = None) -> Tuple[int, int]:
    """Delete files older than max_age_days under root. Returns (n_files_removed, bytes_freed).

    When `base_dir` is provided, every removed file's registry entry (if any)
    is dropped too, so the artifact registry never reports "registered but
    missing" for files the sweeper physically deleted.
    """
    if not root.exists() or not root.is_dir():
        return 0, 0
    cutoff = time.time() - (max_age_days * 86400)
    removed = 0
    freed = 0
    removed_paths: set[str] = set()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            mtime = path.stat().st_mtime
            if mtime < cutoff:
                size = path.stat().st_size
                path.unlink()
                removed += 1
                freed += size
                if base_dir is not None:
                    try:
                        removed_paths.add(str(path.relative_to(base_dir)).replace("\\", "/"))
                    except ValueError:
                        pass
        except OSError as exc:
            log.warning("[cleanup] cannot remove %s: %s", path, exc)
    # Prune empty directories (deepest first).
    # 防御：显式检查目录为空再 rmdir——不能依赖「rmdir 对非空目录必然失败」的假设
    # （部分环境/沙箱下 rmdir 会递归删除非空目录，导致误删新文件）。
    for path in sorted(root.rglob("*"), key=lambda p: -len(p.parts)):
        if path.is_dir():
            try:
                if not any(path.iterdir()):
                    path.rmdir()
            except OSError:
                pass
    if removed_paths:
        try:
            pruned = prune_registry_for_paths(removed_paths)
            if pruned:
                log.info("[cleanup] registry: dropped %d active entry/ies for swept files", pruned)
        except Exception as exc:
            log.warning("[cleanup] registry prune failed: %s", exc)
    if removed:
        log.info("[cleanup] %s: removed %d file(s), freed %.1f MB (>%d days old)",
                 label, removed, freed / 1024 / 1024, max_age_days)
    return removed, freed


def _sweep_stale_autosaves(session_dir: Path, max_age_days: int) -> int:
    """Soft-delete autosaves untouched for max_age_days (manual saves exempt).

    Uses the session trash (not unlink) so an accidental sweep stays
    recoverable for the trash retention window.
    """
    if max_age_days <= 0 or not session_dir.is_dir():
        return 0
    cutoff = time.time() - (max_age_days * 86400)
    removed = 0
    for path in session_dir.glob("autosave_*.json"):
        try:
            if path.stat().st_mtime >= cutoff:
                continue
            soft_delete_session_group(session_dir, path.name)
            removed += 1
        except (OSError, ValueError, FileNotFoundError) as exc:
            log.warning("[cleanup] stale autosave %s skipped: %s", path.name, exc)
    if removed:
        log.info("[cleanup] session autosaves: soft-deleted %d group(s) (>%d days idle)",
                 removed, max_age_days)
    return removed


def run_cleanup(base_dir: Path, rules: Iterable[Tuple[str, int, str]] = DEFAULT_RULES) -> None:
    """Run one sweep across all rules. Safe to call repeatedly."""
    total_removed = 0
    total_freed = 0
    for rel, days, label in rules:
        n, b = _sweep_one(base_dir / rel, days, label, base_dir)
        total_removed += n
        total_freed += b
    total_removed += _sweep_stale_autosaves(
        base_dir / "outputs" / "Session",
        int(os.environ.get("BAA_AUTOSAVE_IDLE_DAYS", "30")),
    )
    # Archived conversations (session trash) are NEVER auto-reclaimed: a
    # conversation can only be archived or emptied by the user, per product
    # decision (Settings → Storage). Artifacts/uploads/memory trash are still
    # governed by the retention preset through the UI.
    if total_removed == 0:
        log.debug("[cleanup] sweep complete — nothing to remove")


def _cleanup_loop(base_dir: Path, interval_hours: int, rules) -> None:
    """Daemon-thread entry: sleep, sweep, repeat."""
    interval_sec = interval_hours * 3600
    while True:
        time.sleep(interval_sec)
        try:
            run_cleanup(base_dir, rules)
        except Exception as exc:  # never let cleanup crash kill the thread
            log.exception("[cleanup] sweep raised: %s", exc)


def setup_cleanup(base_dir: Path | None = None) -> None:
    """Install the cleanup daemon. Call once at app startup.

    Performs one synchronous sweep before returning, then spawns a daemon
    thread to repeat every `BAA_CLEANUP_INTERVAL_HOURS` (default 24).
    Honors `BAA_CLEANUP_DISABLED=1` for opting out entirely.
    """
    if os.environ.get("BAA_CLEANUP_DISABLED") == "1":
        log.info("[cleanup] disabled via BAA_CLEANUP_DISABLED=1")
        return

    base = base_dir or data_root()
    rules = DEFAULT_RULES
    interval = int(os.environ.get("BAA_CLEANUP_INTERVAL_HOURS", "24"))

    log.info("[cleanup] policies: %s", ", ".join(f"{r[2]}>{r[1]}d" for r in rules))

    # First sweep runs now so boot-time disk pressure is addressed immediately.
    try:
        run_cleanup(base, rules)
    except Exception as exc:
        log.exception("[cleanup] startup sweep failed: %s", exc)

    t = threading.Thread(
        target=_cleanup_loop, args=(base, interval, rules),
        name="baa-cleanup", daemon=True,
    )
    t.start()
    log.info("[cleanup] daemon started (re-sweeps every %dh)", interval)
