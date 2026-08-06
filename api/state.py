"""Shared singletons — import from here, never instantiate elsewhere."""
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from data.session import SessionManager
from LLM.llm_config_manager import get_config_manager
from LLM.mcp_config_manager import get_mcp_config_manager
from data.datasource_config_manager import get_datasource_config_manager
from data.workspace import workspace_manager
from infrastructure.paths import data_path


class _ChartStore:
    """Write-through chart store — persists HTML to disk so charts survive restarts."""

    def __init__(self, store_dir: Path):
        self._dir = store_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def __setitem__(self, cid: str, html: str):
        (self._dir / f"{cid}.html").write_text(html, encoding="utf-8")

    def path_for(self, cid: str) -> Path:
        return self._dir / f"{cid}.html"

    def __getitem__(self, cid: str) -> str:
        p = self._dir / f"{cid}.html"
        if not p.exists():
            raise KeyError(cid)
        return p.read_text(encoding="utf-8")

    def __contains__(self, cid: object) -> bool:
        return (self._dir / f"{cid}.html").exists()

    def get(self, cid: str, default=None):
        p = self._dir / f"{cid}.html"
        return p.read_text(encoding="utf-8") if p.exists() else default


def _resolve_writable_dir(preferred: Path) -> Path:
    """Return *preferred* if writable, otherwise fall back to /tmp equivalent.

    Vercel Serverless (and similar read-only runtimes) mount the deploy
    directory as read-only.  /tmp is the only writable location there.
    """
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        # Quick write-test so we don't silently fail later
        test = preferred / ".write_test"
        test.write_text("ok")
        test.unlink()
        return preferred
    except OSError as e:
        log.warning("[state] chart store dir %s not writable, using fallback: %s", preferred, e)
        fallback = Path("/tmp") / "baa" / preferred.name
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


_CHARTS_DIR = _resolve_writable_dir(data_path("outputs", "charts"))

session_manager: SessionManager = SessionManager()
config_manager = get_config_manager()
mcp_config_manager = get_mcp_config_manager()
datasource_config_manager = get_datasource_config_manager()
chart_store: _ChartStore = _ChartStore(_CHARTS_DIR)

# workspace_manager 已从 data.workspace 导入（模块级单例），直接可用


def check_session_ownership(sid: str) -> tuple[bool, str]:
    """Verify the authenticated user owns *sid*.  Returns (allowed, user_id).

    In local / non-cloud mode all sessions are shared; returns (True, "").
    In cloud mode the session's owner_user_id must match the Flask session uid.

    Callers that already have the ChatSession object should compare
    ``sess.owner_user_id`` directly rather than going through this helper.
    """
    is_cloud = bool(os.environ.get("RAILWAY_PROJECT_ID")) or os.environ.get("VERCEL") == "1"
    if not is_cloud:
        return True, ""

    from .auth import current_user
    auth_user = current_user()
    if not auth_user:
        return False, ""
    user_id = auth_user["id"]

    sess = session_manager.get(sid)
    if sess is None:
        # Session doesn't exist yet — allowed (GET-based lazy creation)
        return True, user_id
    owner = getattr(sess, "owner_user_id", "")
    if not owner:
        # Legacy session created before isolation was added — adopt it
        sess.owner_user_id = user_id
        return True, user_id
    if owner != user_id:
        return False, user_id
    return True, user_id


def require_session_ownership(f):
    """Decorator: require that the authenticated user owns the session in *sid*.

    The decorated function MUST have ``sid`` as its first argument.
    Returns a 403 JSON error when ownership fails.

    Usage::

        @bp.post("/api/session/<sid>/something")
        @require_session_ownership
        def something(sid: str):
            ...
    """
    from functools import wraps
    from flask import jsonify

    @wraps(f)
    def wrapper(sid: str, *args, **kwargs):
        allowed, user_id = check_session_ownership(sid)
        if not allowed:
            return jsonify({"error": "无权访问此会话", "code": "forbidden"}), 403
        return f(sid, *args, **kwargs)
    return wrapper
