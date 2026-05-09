"""Blueprint: system utilities — git pull update, etc."""
import logging
import subprocess
from pathlib import Path

from flask import Blueprint, jsonify

log = logging.getLogger(__name__)

bp = Blueprint("system", __name__)

# Project root — two levels up from this file (api/system.py → api/ → project root)
PROJECT_ROOT = Path(__file__).parent.parent

REMOTE_URL = "https://github.com/Zafer-Liu/VizPilot_AI.git"
REMOTE_BRANCH = "main"


@bp.post("/api/system/update")
def git_pull():
    """
    Pull latest changes from the upstream GitHub repository.
    Returns JSON:
      { ok, output, already_up_to_date, returncode }
    """
    log.info("[update] git pull %s %s", REMOTE_URL, REMOTE_BRANCH)
    try:
        result = subprocess.run(
            ["git", "pull", REMOTE_URL, REMOTE_BRANCH],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(PROJECT_ROOT),
        )
        combined = (result.stdout + result.stderr).strip()
        ok = result.returncode == 0
        already = ok and "Already up to date" in combined
        log.info("[update] returncode=%d  output=%r", result.returncode, combined[:200])
        return jsonify({
            "ok": ok,
            "output": combined,
            "already_up_to_date": already,
            "returncode": result.returncode,
        })
    except FileNotFoundError:
        msg = "未找到 git 命令，请确认已安装 Git 并加入 PATH。"
        log.error("[update] %s", msg)
        return jsonify({"ok": False, "output": msg, "already_up_to_date": False, "returncode": -1})
    except subprocess.TimeoutExpired:
        msg = "拉取超时（60 秒），请检查网络连接后重试。"
        log.error("[update] timeout")
        return jsonify({"ok": False, "output": msg, "already_up_to_date": False, "returncode": -1})
    except Exception as exc:
        msg = f"发生错误：{exc}"
        log.error("[update] %s", exc)
        return jsonify({"ok": False, "output": msg, "already_up_to_date": False, "returncode": -1})
