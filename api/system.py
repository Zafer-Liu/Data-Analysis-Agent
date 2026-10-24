"""Blueprint: system utilities — GitHub Releases version check & update."""
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.error
import zipfile
from pathlib import Path
from typing import Any, Tuple, List
from urllib.parse import urlsplit

from flask import Blueprint, jsonify, request
from infrastructure.paths import is_frozen, resource_root

log = logging.getLogger(__name__)

bp = Blueprint("system", __name__)
_directory_picker_lock = threading.Lock()

# Project root: api/system.py → api/ → project root
PROJECT_ROOT = resource_root()

# ── Current version (keep in sync with templates/agent_chat.html footer) ──
CURRENT_VERSION = "v1.4.0"

# ── GitHub Releases API ──
GITHUB_OWNER = "Zafer-Liu"
GITHUB_REPO = "Data-Analysis-Agent"
RELEASES_API = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
_UPDATE_CACHE_TTL_SECONDS = 15 * 60
_update_check_cache: dict[str, Any] = {"ts": 0.0, "payload": None}

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
    "LLM/embedding_config.json",
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
    m = re.match(r"v?(\d+)\.(\d+)\.(\d+)", str(tag or ""))
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)


def _github_headers(*, api: bool = True) -> dict[str, str]:
    headers = {
        "User-Agent": f"{GITHUB_REPO}/{CURRENT_VERSION} (+https://github.com/{GITHUB_OWNER}/{GITHUB_REPO})",
        "Accept": "application/vnd.github+json" if api else "text/html,application/xhtml+xml",
    }
    if api:
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    token = (os.environ.get("BAA_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()
    if api and token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _release_payload_from_api(data: dict[str, Any]) -> dict[str, Any]:
    latest_tag = str(data.get("tag_name") or "")
    assets = []
    for asset in (data.get("assets") or []):
        assets.append({
            "name": asset.get("name", ""),
            "size": asset.get("size", 0),
            "download_url": asset.get("browser_download_url", ""),
        })
    return {
        "ok": True,
        "source": "github_api",
        "current_version": CURRENT_VERSION,
        "latest_version": latest_tag,
        "has_update": _parse_version(latest_tag) > _parse_version(CURRENT_VERSION),
        "release_url": data.get("html_url", RELEASES_PAGE),
        "release_notes": data.get("body", ""),
        "published_at": data.get("published_at", ""),
        "assets": assets,
    }


def _release_payload_from_tag(latest_tag: str, *, warning: str = "") -> dict[str, Any]:
    latest_tag = str(latest_tag or "")
    release_url = (
        f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases/tag/{latest_tag}"
        if latest_tag else RELEASES_PAGE
    )
    payload = {
        "ok": True,
        "source": "github_releases_page",
        "current_version": CURRENT_VERSION,
        "latest_version": latest_tag or "未知",
        "has_update": bool(latest_tag) and _parse_version(latest_tag) > _parse_version(CURRENT_VERSION),
        "release_url": release_url,
        "release_notes": "",
        "published_at": "",
        "assets": [],
    }
    if warning:
        payload["warning"] = warning
    return payload


def _cache_update_payload(payload: dict[str, Any]) -> None:
    _update_check_cache["ts"] = time.time()
    _update_check_cache["payload"] = dict(payload)


def _cached_update_payload(max_age: int = _UPDATE_CACHE_TTL_SECONDS) -> dict[str, Any] | None:
    payload = _update_check_cache.get("payload")
    ts = float(_update_check_cache.get("ts") or 0.0)
    if not isinstance(payload, dict) or time.time() - ts > max_age:
        return None
    cached = dict(payload)
    cached["cached"] = True
    return cached


def _is_github_rate_limit(exc: Exception) -> bool:
    if not isinstance(exc, urllib.error.HTTPError):
        return "rate limit" in str(exc).lower()
    if exc.code not in {403, 429}:
        return False
    if (exc.headers.get("X-RateLimit-Remaining") or "") == "0":
        return True
    try:
        body = exc.read(4096).decode("utf-8", "replace").lower()
    except Exception:
        body = ""
    return "rate limit" in body or "api rate limit exceeded" in body


def _fetch_latest_release_via_api() -> dict[str, Any]:
    req = urllib.request.Request(RELEASES_API, headers=_github_headers(api=True))
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return _release_payload_from_api(data)


def _fetch_latest_release_via_page(warning: str = "") -> dict[str, Any]:
    req = urllib.request.Request(RELEASES_PAGE, headers=_github_headers(api=False))
    with urllib.request.urlopen(req, timeout=15) as resp:
        final_url = resp.geturl()
        body = resp.read(512_000).decode("utf-8", "replace")
    match = re.search(r"/releases/tag/([^/?#\"']+)", final_url) or re.search(
        rf"/{re.escape(GITHUB_OWNER)}/{re.escape(GITHUB_REPO)}/releases/tag/([^/?#\"']+)",
        body,
    )
    latest_tag = match.group(1) if match else ""
    return _release_payload_from_tag(latest_tag, warning=warning)


@bp.get("/api/system/check-update")
def check_update():
    """Query GitHub Releases for the latest version with cache and fallback."""
    cached = _cached_update_payload()
    if cached is not None:
        return jsonify(cached)

    try:
        payload = _fetch_latest_release_via_api()
        _cache_update_payload(payload)
        return jsonify(payload)
    except Exception as exc:
        rate_limited = _is_github_rate_limit(exc)
        log.warning("[check-update] GitHub API failed: %s", exc)
        cached = _cached_update_payload(max_age=24 * 60 * 60)
        if cached is not None:
            cached["stale"] = True
            cached["warning"] = "GitHub API 暂时受限，已显示最近一次成功检查的结果。"
            return jsonify(cached)
        if rate_limited:
            try:
                payload = _fetch_latest_release_via_page(
                    warning="GitHub API 已限流，已通过 Releases 页面确认最新版本；资产列表可能暂不可用。"
                )
                _cache_update_payload(payload)
                return jsonify(payload)
            except Exception as fallback_exc:
                log.warning("[check-update] GitHub releases page fallback failed: %s", fallback_exc)
        message = (
            "GitHub API 请求次数已达上限，请稍后重试；也可以直接打开 Releases 页面查看最新版本。"
            if rate_limited else "检查更新失败，请稍后重试。"
        )
        return jsonify({
            "ok": False,
            "code": "github_rate_limited" if rate_limited else "github_update_check_failed",
            "error": message,
            "current_version": CURRENT_VERSION,
            "release_url": RELEASES_PAGE,
            "retryable": True,
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



# -- BGE embedding model download ---------------------------------------------

_BGE_REPO = "BAAI/bge-small-zh-v1.5"
_BGE_FILES = [
    "config.json",
    "pytorch_model.bin",
    "special_tokens_map.json",
    "tokenizer.json",
    "vocab.txt",
]
_bge_download_lock = threading.Lock()
_bge_download_state: dict[str, Any] = {"active": False}


def _bge_model_dir() -> Path:
    """Return the snapshot directory where BGE model files are expected."""
    cache = Path(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    ) / "baa_models" / "models--BAAI--bge-small-zh-v1.5" / "snapshots"
    if cache.exists():
        dirs = [d for d in cache.iterdir() if d.is_dir()]
        if dirs:
            return dirs[0]
    return cache / "7999e1d3359715c523056ef9478215996d"


def _bge_files_complete() -> bool:
    """Check whether all required BGE model files exist on disk."""
    d = _bge_model_dir()
    return all((d / f).exists() for f in _BGE_FILES)


@bp.get("/api/system/bge-model/status")
def bge_model_status():
    """Check whether the BGE embedding model is available locally."""
    neural = False
    init_error = ""
    try:
        from Function.Knowledge.neural_embedder import is_neural, get_init_error
        neural = is_neural()
        if not neural:
            init_error = get_init_error()
    except Exception as exc:
        init_error = str(exc)
    return jsonify({
        "ok": True,
        "installed": _bge_files_complete(),
        "neural_active": neural,
        "init_error": init_error,
        "model_dir": str(_bge_model_dir()),
    })


@bp.post("/api/system/bge-model/download")
def bge_model_download():
    """Download BGE-small-zh model files from HuggingFace.

    Runs synchronously; the browser shows a spinner during the ~91 MB download.
    Returns the result once all files are written to disk.
    """
    if not _bge_download_lock.acquire(blocking=False):
        return jsonify({"ok": False, "error": "Download already in progress."}), 409

    _bge_download_state["active"] = True
    try:
        target = _bge_model_dir()
        target.mkdir(parents=True, exist_ok=True)

        base = f"https://huggingface.co/{_BGE_REPO}/resolve/main"
        errors = []
        for fname in _BGE_FILES:
            dst = target / fname
            if dst.exists() and dst.stat().st_size > 0:
                continue
            url = f"{base}/{fname}"
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent": "Data-Analysis-Agent/1.0",
                })
                with urllib.request.urlopen(req, timeout=300) as resp, open(dst, "wb") as f:
                    shutil.copyfileobj(resp, f)
                log.info("[bge-download] %s - %.1f KB", fname, dst.stat().st_size / 1024)
            except Exception as exc:
                log.error("[bge-download] %s failed: %s", fname, exc)
                errors.append(f"{fname}: {exc}")

        if errors:
            return jsonify({
                "ok": False,
                "error": "Some files failed: " + "; ".join(errors),
                "downloaded": [f for f in _BGE_FILES if (target / f).exists()],
            })

        # Re-init neural embedder so it picks up newly downloaded files.
        try:
            from Function.Knowledge.neural_embedder import reset_for_newly_installed_model
            reset_for_newly_installed_model()
            from Function.Knowledge.neural_embedder import is_neural
            neural_now = is_neural()
        except Exception:
            neural_now = False

        return jsonify({
            "ok": True,
            "message": "BGE-small-zh model downloaded. Neural embedding activated.",
            "neural_active": neural_now,
            "model_dir": str(target),
        })
    finally:
        _bge_download_state["active"] = False
        _bge_download_lock.release()


@bp.get("/api/system/embed-mode")
def embed_mode_get():
    """Return current embedding mode and status info."""
    try:
        from Function.Knowledge.neural_embedder import get_embed_info, is_neural, get_init_error
        info = get_embed_info()
        return jsonify({
            "ok": True,
            "mode": info.get("mode", "auto"),
            "active": info.get("active", "hash"),
            "dim": info.get("dim", 384),
            "model": info.get("model", ""),
            "cloud_url": info.get("cloud_url", ""),
            "cloud_available": info.get("cloud_available", False),
            "cloud_configured": info.get("cloud_configured", False),
            "cloud_status": info.get("cloud_status", "unavailable"),
            "local_available": info.get("local_available", False),
            "installed": _bge_files_complete(),
            "neural_active": is_neural(),
            "init_error": get_init_error(),
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.post("/api/system/embed-mode")
def embed_mode_set():
    """Set the embedding mode at runtime.

    Body: {"mode": "auto|cloud|local|hash"}
    """
    data = request.get_json(silent=True) or {}
    mode = str(data.get("mode", "")).strip().lower()
    if mode not in ("auto", "cloud", "local", "hash"):
        return jsonify({"ok": False, "error": "Invalid mode. Use: auto, cloud, local, hash"}), 400
    try:
        from Function.Knowledge.neural_embedder import set_embed_mode, get_embed_info
        set_embed_mode(mode)
        info = get_embed_info()
        return jsonify({
            "ok": True,
            "mode": info.get("mode", mode),
            "active": info.get("active", ""),
            "dim": info.get("dim", 0),
            "model": info.get("model", ""),
            "cloud_available": info.get("cloud_available", False),
            "cloud_configured": info.get("cloud_configured", False),
            "cloud_status": info.get("cloud_status", "unavailable"),
            "local_available": info.get("local_available", False),
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.get("/api/system/embed-cloud-config")
def embed_cloud_config_get():
    from Function.Knowledge.neural_embedder import get_cloud_config
    return jsonify({"ok": True, **get_cloud_config()})


@bp.put("/api/system/embed-cloud-config")
def embed_cloud_config_set():
    data = request.get_json(silent=True) or {}
    try:
        from Function.Knowledge.neural_embedder import configure_cloud
        config = configure_cloud(
            url=data.get("url", ""),
            model=data.get("model", ""),
            token=data.get("token"),
            clear_token=bool(data.get("clear_token", False)),
            verify=bool(data.get("test", False)),
        )
        tested = config.pop("test", None)
        return jsonify({"ok": True, **config, "test": tested})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        log.warning("[embed-cloud-config] failed: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 502


@bp.post("/api/system/embed-rebuild")
def embed_rebuild():
    """Incrementally rebuild changed knowledge and Skill vectors."""
    try:
        from Function.Knowledge.knowledge_base import KnowledgeBase, normalize_user_id
        from agent.skill_discovery import rebuild_skill_embeddings
        from agent.skills import SkillLoader

        uid = normalize_user_id(request.args.get("user_id", ""))
        kb = KnowledgeBase(user_id=uid)
        try:
            result = kb.rebuild_all_embeddings()
        finally:
            kb.close()
        skills = [skill.to_public_dict() for skill in SkillLoader().load_all().values()]
        result.setdefault("document_chunks", result.get("rebuilt", 0))
        result.setdefault("structured_records", 0)
        result["skills"] = rebuild_skill_embeddings(skills)
        result["total_rebuilt"] = (
            result.get("document_chunks", 0)
            + result.get("structured_records", 0)
            + result.get("skills", 0)
        )
        return jsonify({"ok": True, **result})
    except Exception as exc:
        log.error("[embed-rebuild] failed: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500

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
