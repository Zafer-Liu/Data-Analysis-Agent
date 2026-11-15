"""Release preflight must fail for missing or empty required regressions."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("required_regressions", ROOT / "scripts" / "run_required_regressions.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class RequiredRegressionManifestTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "tests").mkdir()
        self.payload = {
            "version": 1,
            "python": ["tests/test_example.py"],
            "frontend": ["tests/example.test.mjs"],
            "fixtures": [],
        }
        for name in ("test_example.py", "example.test.mjs"):
            (self.root / "tests" / name).write_text("", encoding="utf-8")

    def manifest(self):
        (self.root / "tests" / "required-regressions.json").write_text(json.dumps(self.payload), encoding="utf-8")
        return runner.load_manifest(self.root)

    def test_valid_manifest_lists_both_suites(self):
        result = self.manifest()
        self.assertEqual(self.payload["python"], result["python"])
        self.assertEqual(self.payload["frontend"], result["frontend"])

    def test_missing_required_file_is_an_error(self):
        (self.root / "tests" / "test_example.py").unlink()
        with self.assertRaisesRegex(runner.RegressionManifestError, "missing"):
            self.manifest()

    def test_missing_fixture_is_an_error(self):
        self.payload["fixtures"] = ["tests/fixtures/missing.json"]
        with self.assertRaisesRegex(runner.RegressionManifestError, "missing"):
            self.manifest()

    def test_empty_suite_cannot_silently_pass(self):
        self.payload["python"] = []
        with self.assertRaisesRegex(runner.RegressionManifestError, "empty"):
            self.manifest()

    def test_paths_cannot_escape_the_tests_directory(self):
        for name in ("../test_example.py", "tests/../test_example.py", "/tmp/test_example.py", "tests\\test_example.py"):
            with self.subTest(path=name):
                self.payload["python"] = [name]
                with self.assertRaises(runner.RegressionManifestError):
                    self.manifest()

    def test_duplicates_and_unknown_fields_are_rejected(self):
        self.payload["fixtures"] = list(self.payload["python"])
        with self.assertRaisesRegex(runner.RegressionManifestError, "Duplicate"):
            self.manifest()
        self.payload["fixtures"] = []
        self.payload["pyhton"] = []
        with self.assertRaisesRegex(runner.RegressionManifestError, "fields"):
            self.manifest()

    def test_discovery_of_zero_tests_is_a_failure(self):
        with patch.object(unittest.defaultTestLoader, "discover", return_value=unittest.TestSuite()):
            with self.assertRaisesRegex(runner.RegressionManifestError, "no tests"):
                runner.run_python(self.root, self.payload["python"])


if __name__ == "__main__":
    unittest.main()
