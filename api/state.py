"""Shared singletons — import from here, never instantiate elsewhere."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.session import SessionManager
from LLM.llm_config_manager import get_config_manager
from LLM.mcp_config_manager import get_mcp_config_manager
from data.datasource_config_manager import get_datasource_config_manager


class _ChartStore:
    """Write-through chart store — persists HTML to disk so charts survive restarts."""

    def __init__(self, store_dir: Path):
        self._dir = store_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def __setitem__(self, cid: str, html: str):
        (self._dir / f"{cid}.html").write_text(html, encoding="utf-8")

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
    except OSError:
        fallback = Path("/tmp") / "baa" / preferred.name
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


_CHARTS_DIR = _resolve_writable_dir(Path(__file__).parent.parent / "outputs" / "charts")

session_manager: SessionManager = SessionManager()
config_manager = get_config_manager()
mcp_config_manager = get_mcp_config_manager()
datasource_config_manager = get_datasource_config_manager()
chart_store: _ChartStore = _ChartStore(_CHARTS_DIR)
