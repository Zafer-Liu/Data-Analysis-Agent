"""Fixed dataset child dispatch without a second desktop window or server."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from data.import_pipeline import _worker_command
from infrastructure.resource_budget import BudgetSpec

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("baa_dataset_desktop_launcher", ROOT / "packaging" / "desktop_launcher.py")
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


class DatasetPackagingTests(unittest.TestCase):
    def test_real_fixed_entry_plans_a_file_without_starting_the_application(self):
        with tempfile.TemporaryDirectory(prefix="baa-entry-") as directory:
            root = Path(directory)
            source = root / "input.csv"
            source.write_text("id,amount\n001,10\n002,20\n", encoding="utf-8", newline="\n")
            output, request = root / "result.json", root / "request.json"
            arguments = {"input_path": str(source), "owner_id": "local-default", "session_id": "entry-test",
                         "workspace_id": "", "options": {}, "budget": BudgetSpec().to_dict()}
            request.write_text(json.dumps({"mode": "plan", "arguments": arguments, "result_path": str(output)}),
                               encoding="utf-8", newline="\n")
            result = subprocess.run([sys.executable, "-I", "-B", str(ROOT / "packaging" / "desktop_launcher.py"),
                                     "--baa-dataset-worker", str(request)], cwd=ROOT, capture_output=True,
                                    text=True, timeout=30, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
            self.assertEqual(0, result.returncode, result.stderr)
            response = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(response["ok"])
            self.assertEqual("entry-test", response["plan"]["session_id"])
            self.assertNotIn("desktop ->", result.stderr)
            self.assertFalse((root / ".deps_installed").exists())

    def test_frozen_entry_dispatches_the_fixed_request_and_never_imports_app(self):
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "argv", ["desktop.exe", "--baa-dataset-worker", "request.json"]), \
                patch.object(launcher.multiprocessing, "freeze_support"), patch.dict(sys.modules, {"app": None}), \
                patch("data.import_pipeline._worker_main") as worker:
            command = _worker_command("request.json", ROOT)
            self.assertEqual("--baa-dataset-worker", command[1])
            self.assertNotIn("-c", command)
            self.assertEqual(0, launcher.main())
            worker.assert_called_once_with("request.json")

    def test_invalid_child_arguments_return_without_starting_the_ui(self):
        with patch.object(sys, "argv", ["desktop", "--baa-dataset-worker"]), patch.dict(sys.modules, {"app": None}), \
                patch.object(launcher.multiprocessing, "freeze_support"), patch("data.import_pipeline._worker_main") as worker:
            self.assertEqual(2, launcher.main())
            worker.assert_not_called()


if __name__ == "__main__":
    unittest.main()
