"""P3 offline frozen dependency/resource smoke contract."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packaging"))
from frozen_smoke import run_frozen_smoke  # noqa: E402


class FrozenSmokeTests(unittest.TestCase):
    def test_source_runtime_executes_same_offline_dependency_checks(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"BAA_DATA_DIR": tmp}, clear=False
        ):
            code = run_frozen_smoke()
            report_path = Path(tmp) / "outputs" / "build-smoke.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(code, 0, report)
        self.assertTrue(report["ok"])
        self.assertTrue(report["checks"]["chart_generation"]["ok"])
        self.assertGreaterEqual(report["checks"]["analysis_registry"]["count"], 10)


if __name__ == "__main__":
    unittest.main()
