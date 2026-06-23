"""P1 resource/runtime path compatibility and packaged-data isolation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from infrastructure import paths


ROOT = Path(__file__).resolve().parents[1]


class RuntimePathTests(unittest.TestCase):
    def test_source_mode_preserves_project_root_layout(self):
        environment = dict(os.environ)
        for name in ("BAA_DATA_DIR", "BAA_RESOURCE_DIR", "VERCEL"):
            environment.pop(name, None)
        with patch.dict(os.environ, environment, clear=True), patch.object(
            paths.sys, "frozen", False, create=True
        ):
            self.assertEqual(paths.resource_root(), ROOT)
            self.assertEqual(paths.data_root(), ROOT)
            self.assertEqual(paths.data_path("uploads"), ROOT / "uploads")
            self.assertEqual(
                paths.runtime_config_path("llm_config.json", "LLM/llm_config.json"),
                ROOT / "LLM" / "llm_config.json",
            )

    def test_explicit_data_override_is_absolute_and_isolates_configs(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"BAA_DATA_DIR": tmp}, clear=False
        ):
            expected = Path(tmp).resolve()
            self.assertEqual(paths.data_root(), expected)
            self.assertEqual(paths.data_path("outputs", "jobs"), expected / "outputs" / "jobs")
            self.assertEqual(
                paths.runtime_config_path("llm_config.json", "LLM/llm_config.json"),
                expected / "config" / "llm_config.json",
            )
        with patch.dict(os.environ, {"BAA_DATA_DIR": "relative/path"}, clear=False):
            with self.assertRaises(ValueError):
                paths.data_root()

    def test_frozen_windows_and_macos_use_native_user_data_locations(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            environment = dict(os.environ)
            environment.pop("BAA_DATA_DIR", None)
            environment.pop("VERCEL", None)
            environment["LOCALAPPDATA"] = str(base / "Local")
            with patch.dict(os.environ, environment, clear=True), patch.object(
                paths.sys, "frozen", True, create=True
            ), patch.object(paths.sys, "platform", "win32"):
                self.assertEqual(
                    paths.data_root(),
                    (base / "Local" / "BusinessAnalyticsAgent").resolve(),
                )

            environment.pop("LOCALAPPDATA", None)
            with patch.dict(os.environ, environment, clear=True), patch.object(
                paths.sys, "frozen", True, create=True
            ), patch.object(paths.sys, "platform", "darwin"), patch(
                "infrastructure.paths.Path.home", return_value=base / "home"
            ):
                self.assertEqual(
                    paths.data_root(),
                    (base / "home" / "Library" / "Application Support"
                     / "BusinessAnalyticsAgent").resolve(),
                )

    def test_bundle_resource_root_uses_meipass_without_creating_data(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            paths.sys, "_MEIPASS", tmp, create=True
        ):
            root = Path(tmp).resolve()
            self.assertEqual(paths.resource_root(), root)
            self.assertEqual(paths.resource_path("templates"), root / "templates")

    def test_override_routes_all_runtime_stores_without_migrating_source_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "runtime"
            source_sentinel = ROOT / "uploads" / ".p1-source-sentinel"
            source_sentinel.parent.mkdir(parents=True, exist_ok=True)
            source_sentinel.write_text("do-not-copy", encoding="utf-8")
            script = r'''
import json
from api.datasource import UPLOAD_DIR
from api.saved_sessions import SAVE_DIR
from api.output import _EXPORT_DIR
from api.state import _CHARTS_DIR
from api.dashboard import _DASHBOARD_DIR
from api.knowledge import _KB_DIR as API_KB_DIR
from Function.Knowledge.knowledge_base import _KB_DIR as FUNCTION_KB_DIR
from agent.tools.results import _GLOBAL_RESULT_ROOT
from LLM.llm_config_manager import LLM_CONFIG_FILE
from LLM.mcp_config_manager import MCP_CONFIG_FILE
from data.datasource_config_manager import _CONFIG_FILE
from data.jobs_store import JobsStore
from data.workspace import workspace_manager

store = JobsStore()
payload = {
    "upload": str(UPLOAD_DIR),
    "sessions": str(SAVE_DIR),
    "exports": str(_EXPORT_DIR),
    "charts": str(_CHARTS_DIR),
    "dashboard": str(_DASHBOARD_DIR),
    "api_kb": str(API_KB_DIR),
    "function_kb": str(FUNCTION_KB_DIR),
    "results": str(_GLOBAL_RESULT_ROOT),
    "llm_config": str(LLM_CONFIG_FILE),
    "mcp_config": str(MCP_CONFIG_FILE),
    "datasource_config": str(_CONFIG_FILE),
    "jobs": str(store.path),
    "workspace_index": str(workspace_manager.metadata_store.index_path),
    "system_uploads": str(workspace_manager.system_workspace.policy("uploads").path),
    "system_outputs": str(workspace_manager.system_workspace.policy("outputs").path),
}
store.close()
print("PATH_PAYLOAD=" + json.dumps(payload))
'''
            environment = dict(os.environ)
            environment["BAA_DATA_DIR"] = str(data_root)
            environment["BAA_CLEANUP_DISABLED"] = "1"
            environment.pop("VERCEL", None)
            try:
                completed = subprocess.run(
                    [sys.executable, "-c", script],
                    cwd=ROOT,
                    env=environment,
                    text=True,
                    capture_output=True,
                    check=True,
                    timeout=60,
                )
                line = next(
                    item for item in completed.stdout.splitlines()
                    if item.startswith("PATH_PAYLOAD=")
                )
                payload = json.loads(line.split("=", 1)[1])
                expected_root = data_root.resolve()
                for label, raw_path in payload.items():
                    resolved = Path(raw_path).resolve(strict=False)
                    self.assertTrue(
                        resolved == expected_root or expected_root in resolved.parents,
                        f"{label} escaped BAA_DATA_DIR: {resolved}",
                    )
                self.assertFalse((data_root / "uploads" / source_sentinel.name).exists())
                self.assertEqual(source_sentinel.read_text(encoding="utf-8"), "do-not-copy")
            finally:
                source_sentinel.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
