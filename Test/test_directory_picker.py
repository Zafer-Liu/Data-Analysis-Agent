"""Tests for the guarded native work-directory picker endpoint."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

from api.system import bp
from api.system import _select_directory_macos, _select_directory_native


class DirectoryPickerApiTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.register_blueprint(bp)
        self.client = app.test_client()

    @patch("api.system._select_directory_native", return_value=r"D:\数据\财务分析")
    def test_returns_exact_native_path(self, picker):
        response = self.client.post(
            "/api/system/select-directory",
            json={},
            headers={"Origin": "http://localhost"},
            base_url="http://localhost",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["path"], r"D:\数据\财务分析")
        self.assertFalse(response.get_json()["cancelled"])
        picker.assert_called_once_with("")

    @patch("api.system._select_directory_native")
    def test_rejects_cross_origin_request(self, picker):
        response = self.client.post(
            "/api/system/select-directory",
            json={},
            headers={"Origin": "https://attacker.example"},
            base_url="http://localhost",
        )

        self.assertEqual(response.status_code, 403)
        picker.assert_not_called()

    @patch("api.system._select_directory_native", return_value="")
    def test_cancel_is_not_an_error(self, _picker):
        response = self.client.post(
            "/api/system/select-directory",
            json={},
            base_url="http://localhost",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["cancelled"])

    @patch("api.system._select_directory_macos", return_value="/Users/test/Data")
    @patch("api.system.sys.platform", "darwin")
    def test_native_dispatches_to_macos(self, picker):
        self.assertEqual(_select_directory_native("/Users/test"), "/Users/test/Data")
        picker.assert_called_once_with("/Users/test")

    @patch("api.system.subprocess.run")
    def test_macos_picker_passes_initial_path_as_argv(self, run):
        with tempfile.TemporaryDirectory() as tmp:
            selected = Path(tmp) / "Selected Folder"
            run.return_value = SimpleNamespace(
                returncode=0,
                stdout=str(selected) + "\n",
                stderr="",
            )
            result = _select_directory_macos(tmp)

        self.assertEqual(result, str(selected.resolve()))
        args = run.call_args.args[0]
        self.assertEqual(args[0], "osascript")
        self.assertEqual(args[-1], str(Path(tmp)))
        self.assertFalse(run.call_args.kwargs.get("shell", False))

    @patch("api.system.subprocess.run")
    def test_macos_cancel_is_not_an_error(self, run):
        run.return_value = SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="execution error: User canceled. (-128)\n",
        )
        self.assertEqual(_select_directory_macos(""), "")

    @patch("api.system.subprocess.run")
    def test_macos_picker_failure_is_user_friendly(self, run):
        run.return_value = SimpleNamespace(returncode=1, stdout="", stderr="permission denied")
        with self.assertRaisesRegex(RuntimeError, "无法打开 macOS 目录选择器"):
            _select_directory_macos("")


if __name__ == "__main__":
    unittest.main()
