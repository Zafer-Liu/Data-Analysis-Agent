"""Blueprint: system utilities — zip-based auto-update from GitHub."""
import logging
import shutil
import tempfile
import urllib.request
import urllib.error
import zipfile
from pathlib import Path
from typing import Tuple, List

from flask import Blueprint, jsonify

log = logging.getLogger(__name__)

bp = Blueprint("system", __name__)

# Project root: api/system.py → api/ → project root
PROJECT_ROOT = Path(__file__).parent.parent

# GitHub archive URL (no git required — works for zip installs too)
ARCHIVE_URL = "https://github.com/Zafer-Liu/VizPilot_AI/archive/refs/heads/main.zip"
# The prefix inside the zip (GitHub always uses {repo}-{branch}/)
ZIP_PREFIX = "VizPilot_AI-main/"

# Paths (relative to project root) that must NEVER be overwritten during update
# — user data, local config, runtime outputs
PROTECTED = {
    "uploads",
    "outputs",
    "LLM/llm_config.json",
    ".git",
    "__pycache__",
}


def _is_protected(rel: Path) -> bool:
    """Return True if this relative path should never be overwritten."""
    parts = rel.parts
    for guard in PROTECTED:
        guard_parts = Path(guard).parts
        if parts[: len(guard_parts)] == guard_parts:
            return True
    # Also skip .pyc and IDE folders
    if any(p.startswith("__pycache__") or p.endswith(".pyc") for p in parts):
        return True
    if any(p in {".idea", ".vscode", ".DS_Store"} for p in parts):
        return True
    return False


def _download_zip(url: str, dest: Path, timeout: int = 90) -> None:
    """Download *url* to *dest* with a progress-friendly timeout."""
    req = urllib.request.Request(url, headers={"User-Agent": "VizPilotUpdater/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as f:
        shutil.copyfileobj(resp, f)


def _apply_update(zip_path: Path) -> Tuple[List[str], List[str], List[str]]:
    """
    Extract *zip_path* and copy new files over PROJECT_ROOT,
    skipping PROTECTED paths.

    Returns
    -------
    updated : list of files that were overwritten
    added   : list of files that are new
    skipped : list of protected / unchanged files that were skipped
    """
    updated, added, skipped = [], [], []

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)

        # Extract the zip
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tmp)

        # GitHub zips put everything under e.g. "VizPilot_AI-main/"
        src_root = tmp / ZIP_PREFIX.rstrip("/")
        if not src_root.is_dir():
            # Fallback: find the single top-level directory
            children = [p for p in tmp.iterdir() if p.is_dir()]
            if children:
                src_root = children[0]
            else:
                raise RuntimeError("无法在压缩包中找到项目根目录。")

        for src_file in src_root.rglob("*"):
            if not src_file.is_file():
                continue

            rel = src_file.relative_to(src_root)

            if _is_protected(rel):
                skipped.append(str(rel))
                continue

            dst_file = PROJECT_ROOT / rel

            # Read new content
            new_bytes = src_file.read_bytes()

            if dst_file.exists():
                old_bytes = dst_file.read_bytes()
                if old_bytes == new_bytes:
                    # Identical — no need to overwrite
                    continue
                dst_file.write_bytes(new_bytes)
                updated.append(str(rel))
            else:
                dst_file.parent.mkdir(parents=True, exist_ok=True)
                dst_file.write_bytes(new_bytes)
                added.append(str(rel))

    return updated, added, skipped


@bp.post("/api/system/update")
def zip_update():
    """
    Download the latest archive from GitHub and apply it to the project.
    Strategy: download zip → extract → smart-copy (skip protected paths).
    Works whether or not the project has a .git directory.

    Returns JSON:
      { ok, output, already_up_to_date, updated, added, skipped, error }
    """
    log.info("[update] downloading archive from %s", ARCHIVE_URL)

    with tempfile.TemporaryDirectory() as tmp_dir:
        zip_path = Path(tmp_dir) / "update.zip"

        # ── Step 1: Download ──────────────────────────────────────────────
        try:
            _download_zip(ARCHIVE_URL, zip_path, timeout=90)
            log.info("[update] downloaded %.1f KB", zip_path.stat().st_size / 1024)
        except urllib.error.URLError as exc:
            msg = f"下载失败：{exc.reason}"
            log.error("[update] %s", msg)
            return jsonify({"ok": False, "output": msg, "already_up_to_date": False,
                            "updated": [], "added": [], "skipped": []})
        except Exception as exc:
            msg = f"下载时发生错误：{exc}"
            log.error("[update] %s", exc)
            return jsonify({"ok": False, "output": msg, "already_up_to_date": False,
                            "updated": [], "added": [], "skipped": []})

        # ── Step 2: Apply ─────────────────────────────────────────────────
        try:
            updated, added, skipped = _apply_update(zip_path)
        except Exception as exc:
            msg = f"解压 / 写入时发生错误：{exc}"
            log.error("[update] %s", exc)
            return jsonify({"ok": False, "output": msg, "already_up_to_date": False,
                            "updated": [], "added": [], "skipped": []})

    already = len(updated) == 0 and len(added) == 0

    # ── Build human-readable output ───────────────────────────────────────
    lines = []
    if already:
        lines.append("✅ 已是最新版本，无文件变更。")
    else:
        lines.append(f"✅ 更新完成：{len(updated)} 个文件已更新，{len(added)} 个新文件。")
    if updated:
        lines.append("\n📝 已更新文件：")
        lines.extend(f"  {f}" for f in sorted(updated))
    if added:
        lines.append("\n➕ 新增文件：")
        lines.extend(f"  {f}" for f in sorted(added))
    if skipped:
        lines.append(f"\n🔒 已跳过受保护路径（{len(skipped)} 项，含用户数据/配置）")

    output = "\n".join(lines)
    log.info("[update] done — updated=%d added=%d skipped=%d",
             len(updated), len(added), len(skipped))

    return jsonify({
        "ok": True,
        "output": output,
        "already_up_to_date": already,
        "updated": updated,
        "added": added,
        "skipped": skipped,
    })
