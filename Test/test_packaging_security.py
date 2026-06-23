"""P0 clean-staging and sensitive-artifact policy tests."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging"
sys.path.insert(0, str(PACKAGING))

from audit_artifact import audit  # noqa: E402
from build_manifest import ManifestPolicyError, build_staging  # noqa: E402
from package_policy import classify_path  # noqa: E402


class PackagingSecurityTests(unittest.TestCase):
    def _source(self, root: Path) -> Path:
        source = root / "source"
        source.mkdir()
        (source / "app.py").write_text("print('safe')\n", encoding="utf-8")
        (source / "LICENSE").write_text("test license\n", encoding="utf-8")
        for name in (
            "agent", "commands", "skills", "LLM", "data", "packaging", "static", "templates"
        ):
            (source / name).mkdir()
        (source / "agent" / "core.py").write_text("VALUE = 1\n", encoding="utf-8")
        (source / "commands" / "clear.md").write_text("# clear\n", encoding="utf-8")
        (source / "skills" / "SKILL.md").write_text("# skill\n", encoding="utf-8")
        (source / "packaging" / "desktop_launcher.py").write_text(
            "VALUE = 'desktop'\n", encoding="utf-8"
        )
        return source

    def test_staging_is_allowlisted_and_ignores_top_level_runtime_trees(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._source(root)
            for name in ("MCP", "uploads", "outputs"):
                directory = source / name
                directory.mkdir()
                (directory / "private.txt").write_text("must not ship", encoding="utf-8")
            (source / "LLM" / "llm_config.json").write_text(
                '{"api_key":"sk-' + "A" * 40 + '"}', encoding="utf-8"
            )
            (source / "data" / "datasource_config.json").write_text(
                '{"password":"private"}', encoding="utf-8"
            )
            nested_runtime = source / "agent" / "outputs"
            nested_runtime.mkdir()
            (nested_runtime / "private.log").write_text("private", encoding="utf-8")
            staging = root / "staging"

            manifest = build_staging(source, staging)

            paths = {item["path"] for item in manifest["files"]}
            self.assertIn("commands/clear.md", paths)
            self.assertIn("skills/SKILL.md", paths)
            self.assertIn("packaging/desktop_launcher.py", paths)
            self.assertFalse(any(path.lower().startswith("mcp/") for path in paths))
            self.assertFalse(any(path.startswith(("uploads/", "outputs/")) for path in paths))
            self.assertNotIn("LLM/llm_config.json", paths)
            self.assertNotIn("data/datasource_config.json", paths)
            self.assertFalse(any("agent/outputs" in path for path in paths))
            self.assertTrue(any("agent/outputs/" in item for item in manifest["excluded"]))
            self.assertTrue(audit(staging)["ok"])

    def test_forbidden_data_inside_an_allowed_tree_blocks_before_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._source(root)
            (source / "data" / "customer.xlsx").write_bytes(b"private workbook")
            staging = root / "staging"

            with self.assertRaises(ManifestPolicyError):
                build_staging(source, staging)

            self.assertFalse(staging.exists())

    def test_audit_detects_secret_content_and_forbidden_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "artifact"
            target.mkdir()
            (target / "safe.py").write_text("VALUE = 1\n", encoding="utf-8")
            (target / "leak.txt").write_text("token=sk-" + "Z" * 40, encoding="utf-8")
            (target / ".env").write_text("PASSWORD=private", encoding="utf-8")

            report = audit(target)

            self.assertFalse(report["ok"])
            self.assertTrue(any("openai_style_key" in item for item in report["findings"]))
            self.assertTrue(any(".env" in item for item in report["findings"]))

    def test_zip_traversal_and_sensitive_entry_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "artifact.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("safe/app.py", "print('safe')")
                output.writestr("../outputs/session.json", json.dumps({"private": True}))

            report = audit(archive)

            self.assertFalse(report["ok"])
            self.assertTrue(any("invalid relative packaging path" in item for item in report["findings"]))

    def test_only_reviewed_public_dependency_assets_bypass_document_suffix_block(self):
        self.assertEqual(
            classify_path("_internal/certifi/cacert.pem")[0], "allow"
        )
        self.assertEqual(
            classify_path("Contents/Frameworks/docx/templates/default.docx")[0], "allow"
        )
        self.assertEqual(
            classify_path("Business Analytics Agent.app/Contents/Frameworks/certifi/cacert.pem")[0],
            "allow",
        )
        self.assertEqual(
            classify_path("agent/certifi/cacert.pem")[0], "deny"
        )
        self.assertEqual(
            classify_path("_internal/random/default.docx")[0], "deny"
        )


if __name__ == "__main__":
    unittest.main()
