"""Blueprint: system utilities — GitHub Releases version check & update."""
import json
import logging
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import urllib.request
import urllib.error
import zipfile
from pathlib import Path
from typing import Tuple, List
from urllib.parse import urlsplit

from flask import Blueprint, jsonify, request
from infrastructure.paths import is_frozen, resource_root

log = logging.getLogger(__name__)

bp = Blueprint("system", __name__)
_directory_picker_lock = threading.Lock()

# Project root: api/system.py → api/ → project root
PROJECT_ROOT = resource_root()

# ── Current version (keep in sync with templates/agent_chat.html footer) ──
CURRENT_VERSION = "v1.1.0"

# ── GitHub Releases API ──
GITHUB_OWNER = "Zafer-Liu"
GITHUB_REPO = "Data-Analysis-Agent"
RELEASES_API = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"

# GitHub archive URL (no git required — works for zip installs too)
ARCHIVE_URL = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/archive/refs/heads/main.zip"
# The prefix inside the zip: GitHub always uses {repo}-{branch}/
ZIP_PREFIX = f"{GITHUB_REPO}-main/"

# Paths (relative to project root) that must NEVER be overwritten during update
# — user data, local config, runtime outputs, local-only documentation
PROTECTED = {
    # Runtime data — user uploads / generated outputs
    "uploads",
    "outputs",
    # User configuration — credentials, API keys, connection strings
    "LLM/llm_config.json",
    "LLM/mcp_config.json",
    "data/datasource_config.json",
    ".env",
    # Local compatibility patches — machine-specific, must not be overwritten.
    "infrastructure/local_patches.py",
    # VCS / IDE metadata
    ".git",
    "__pycache__",
}


def _is_local_same_origin_request() -> bool:
    """Only let the browser on this machine open a native folder dialog."""
    remote = (request.remote_addr or "").split("%", 1)[0]
    if remote not in {"127.0.0.1", "::1"}:
        return False

    if request.headers.get("Sec-Fetch-Site", "").lower() == "cross-site":
        return False

    origin = request.headers.get("Origin")
    if origin:
        try:
            if urlsplit(origin).netloc.lower() != request.host.lower():
                return False
        except ValueError:
            return False
    return True


def _select_directory_windows(initial_dir: str = "") -> str:
    """Open the Windows-native directory chooser."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError as exc:
        raise RuntimeError("当前 Python 未安装 Tk 组件，请手动输入完整绝对路径。") from exc

    start = Path(initial_dir).expanduser() if initial_dir else Path.home()
    if not start.is_dir():
        start = Path.home()

    root = tk.Tk()
    try:
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(
            parent=root,
            initialdir=str(start),
            mustexist=True,
            title="选择要挂载的工作目录",
        )
    finally:
        root.destroy()

    return str(Path(selected).resolve()) if selected else ""


_MACOS_DIRECTORY_SCRIPT = """
on run argv
    set startFolder to POSIX file (item 1 of argv)
    set selectedFolder to choose folder with prompt "选择要挂载的工作目录" default location startFolder
    return POSIX path of selectedFolder
end run
""".strip()


def _select_directory_macos(initial_dir: str = "") -> str:
    """Open the macOS Finder directory chooser without invoking a shell."""
    start = Path(initial_dir).expanduser() if initial_dir else Path.home()
    if not start.is_dir():
        start = Path.home()
    completed = subprocess.run(
        ["osascript", "-e", _MACOS_DIRECTORY_SCRIPT, "--", str(start)],
        text=True,
        capture_output=True,
        timeout=300,
        check=False,
    )
    if completed.returncode != 0:
        error = (completed.stderr or "").strip()
        if "-128" in error or "User canceled" in error:
            return ""
        raise RuntimeError(f"无法打开 macOS 目录选择器：{error or 'osascript failed'}")
    selected = completed.stdout.strip()
    return str(Path(selected).expanduser().resolve()) if selected else ""


def _select_directory_native(initial_dir: str = "") -> str:
    """Open the platform-native directory chooser and return an absolute path."""
    if sys.platform == "darwin":
        return _select_directory_macos(initial_dir)
    if os.name == "nt":
        return _select_directory_windows(initial_dir)
    raise RuntimeError("当前平台不支持原生目录选择器，请手动输入完整绝对路径。")


@bp.post("/api/system/select-directory")
def select_directory():
    """Open a native picker on the local Windows or macOS host.

    A normal browser file input intentionally hides the selected directory's
    absolute path.  Since this application runs locally, a guarded backend
    dialog is the only reliable way to obtain the actual mount path.
    """
    if os.environ.get("VERCEL") or not _is_local_same_origin_request():
        return jsonify({
            "ok": False,
            "error": "原生目录选择仅允许从运行服务的本机页面调用，请手动输入路径。",
        }), 403

    body = request.get_json(silent=True) or {}
    initial_dir = str(body.get("initial_path") or "").strip()
    if initial_dir and not Path(initial_dir).is_dir():
        initial_dir = ""

    if not _directory_picker_lock.acquire(blocking=False):
        return jsonify({"ok": False, "error": "目录选择窗口已打开。"}), 409
    try:
        selected = _select_directory_native(initial_dir)
    except Exception as exc:
        log.exception("[directory-picker] failed")
        return jsonify({"ok": False, "error": str(exc)}), 500
    finally:
        _directory_picker_lock.release()

    return jsonify({"ok": True, "path": selected, "cancelled": not bool(selected)})


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


def _rmtree_safe(path: str) -> None:
    """
    Best-effort recursive delete — tolerates locked files on Windows.

    On Windows, antivirus scanners and Flask's file-watcher can briefly lock
    newly-extracted files (e.g. result.html in chart directories), causing
    shutil.rmtree to raise PermissionError (WinError 32).  We handle this by
    trying to remove the read-only bit and retrying; if the file is still
    locked we simply skip it — the OS will reclaim the temp space eventually.
    """
    def _onerror(func, p, exc_info):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except Exception:
            pass  # give up gracefully — temp dir, not critical

    shutil.rmtree(path, onerror=_onerror)


def _download_zip(url: str, dest: Path, timeout: int = 90) -> None:
    """Download *url* to *dest* with a progress-friendly timeout."""
    req = urllib.request.Request(url, headers={"User-Agent": "Data-Analysis-Agent/1.0"})
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

    # Use mkdtemp + manual cleanup so _rmtree_safe handles Windows file locks.
    tmp_dir = tempfile.mkdtemp()
    try:
        tmp = Path(tmp_dir)

        # Extract the zip
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tmp)

        # GitHub zips put everything under e.g. "Data-Analysis-Agent-main/"
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

    finally:
        _rmtree_safe(tmp_dir)

    return updated, added, skipped


def _parse_version(tag: str) -> tuple:
    """Parse 'v1.2.3' into (1, 2, 3) for comparison."""
    import re
    m = re.match(r"v?(\d+)\.(\d+)\.(\d+)", str(tag or ""))
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)


@bp.get("/api/system/check-update")
def check_update():
    """Query GitHub Releases API for the latest version.

    Returns JSON: { ok, current_version, latest_version, has_update,
                    release_url, release_notes, published_at, assets }
    """
    try:
        req = urllib.request.Request(RELEASES_API, headers={
            "User-Agent": f"{GITHUB_REPO}/1.0",
            "Accept": "application/vnd.github.v3+json",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        log.warning("[check-update] GitHub API failed: %s", exc)
        return jsonify({
            "ok": False,
            "error": str(exc),
            "current_version": CURRENT_VERSION,
        })

    latest_tag = data.get("tag_name", "")
    has_update = _parse_version(latest_tag) > _parse_version(CURRENT_VERSION)

    assets = []
    for a in (data.get("assets") or []):
        assets.append({
            "name": a.get("name", ""),
            "size": a.get("size", 0),
            "download_url": a.get("browser_download_url", ""),
        })

    return jsonify({
        "ok": True,
        "current_version": CURRENT_VERSION,
        "latest_version": latest_tag,
        "has_update": has_update,
        "release_url": data.get("html_url", RELEASES_PAGE),
        "release_notes": data.get("body", ""),
        "published_at": data.get("published_at", ""),
        "assets": assets,
    })


@bp.post("/api/system/update")
def zip_update():
    """
    Download the latest archive from GitHub and apply it to the project.
    Strategy: download zip → extract → smart-copy (skip protected paths).
    Works whether or not the project has a .git directory.

    Returns JSON:
      { ok, output, already_up_to_date, updated, added, skipped, error }
    """
    if is_frozen():
        message = "桌面安装包不支持覆盖应用文件，请下载并安装新版本。"
        return jsonify({
            "ok": False,
            "output": message,
            "error": message,
            "already_up_to_date": False,
            "updated": [],
            "added": [],
            "skipped": [],
        }), 409

    log.info("[update] downloading archive from %s", ARCHIVE_URL)

    # Use mkdtemp + manual cleanup so _rmtree_safe handles Windows file locks.
    tmp_dir = tempfile.mkdtemp()
    try:
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

    finally:
        _rmtree_safe(tmp_dir)

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


@bp.get("/api/proxy-image")
def proxy_image():
    """Proxy an external image URL through the backend.

    Some image hosts (e.g. Aliyun OSS with referer policy) block direct
    browser access. Fetching through the backend avoids the referer check
    and streams the image bytes back to the frontend.

    Query params:
        url  — the full image URL to fetch (must be http/https)

    Security:
        - Only http/https URLs are accepted
        - 10 MB size cap to prevent abuse
        - Timeout of 30 s
    """
    from flask import request as _req, Response as _Resp
    url = (_req.args.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return jsonify({"error": "Invalid URL"}), 400

    _SIZE_CAP = 10 * 1024 * 1024  # 10 MB

    # Infer mimetype from URL extension as fallback
    def _guess_mime(u: str) -> str:
        u_lower = u.lower().split("?")[0]
        if u_lower.endswith(".png"):  return "image/png"
        if u_lower.endswith(".webp"): return "image/webp"
        if u_lower.endswith(".gif"):  return "image/gif"
        if u_lower.endswith(".svg"):  return "image/svg+xml"
        return "image/jpeg"  # default

    # Try a list of Referer values — some OSS buckets allow no-referer
    # or require the platform's own domain.
    _referers = [
        "https://www.atlascloud.ai/",
        "https://atlascloud.ai/",
        "",   # no Referer — some policies allow empty referer
    ]

    last_exc: Exception | None = None
    for referer in _referers:
        try:
            headers = {"User-Agent": "Mozilla/5.0 (compatible; BAA-proxy/1.0)"}
            if referer:
                headers["Referer"] = referer
            req_obj = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req_obj, timeout=30) as resp:
                ct = resp.headers.get("Content-Type", "") or ""
                # If OSS returns octet-stream, guess from URL
                if "octet-stream" in ct or not ct.startswith("image/"):
                    mimetype = _guess_mime(url)
                else:
                    mimetype = ct.split(";")[0].strip()
                data = resp.read(_SIZE_CAP)
            log.info("[proxy-image] fetched %d bytes (referer=%r) from %s",
                     len(data), referer or "(none)", url[:80])
            return _Resp(
                data,
                status=200,
                mimetype=mimetype,
                headers={
                    "Cache-Control": "public, max-age=3600",
                    # Force browser to display inline, not download
                    "Content-Disposition": "inline",
                    "X-Content-Type-Options": "nosniff",
                },
            )
        except urllib.error.HTTPError as exc:
            log.warning("[proxy-image] HTTP %d (referer=%r) for %s",
                        exc.code, referer or "(none)", url[:80])
            last_exc = exc
            if exc.code != 403:
                break   # only retry 403 (referer policy); other errors are final
        except Exception as exc:
            log.warning("[proxy-image] failed (referer=%r) for %s: %s",
                        referer or "(none)", url[:80], exc)
            last_exc = exc
            break

    code = getattr(last_exc, "code", 502)
    return jsonify({"error": f"Remote server error: {last_exc}"}), 502


@bp.get("/api/instruction")
def get_instruction():
    """Return the user guide Markdown so the frontend can render it.

    Kept as JSON (rather than text/markdown) so the response can also carry a
    consistent {ok, error} envelope when the file is missing — front-end
    error handling is uniform across endpoints.
    """
    path = PROJECT_ROOT / "Information" / "Instruction.md"
    if not path.exists():
        return jsonify({"ok": False, "error": "Information/Instruction.md not found"}), 404
    try:
        return jsonify({"ok": True, "markdown": path.read_text(encoding="utf-8")})
    except OSError as exc:
        log.error("[instruction] read failed: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500
