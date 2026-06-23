"""P6 release workflow keeps build artifacts bounded and gated."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "build-release.yml"


class ReleaseWorkflowTests(unittest.TestCase):
    def test_workflow_has_expected_jobs_and_triggers(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        for required in (
            "workflow_dispatch:",
            'tags:',
            '- "v*"',
            "build-windows:",
            "build-macos:",
            "release:",
            "needs: [build-windows, build-macos]",
            "if: startsWith(github.ref, 'refs/tags/v')",
        ):
            self.assertIn(required, text)

    def test_windows_build_uses_existing_audited_script_and_inno(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("choco install innosetup --no-progress -y", text)
        self.assertIn("packaging/build_windows.ps1 -Version $env:PACKAGE_VERSION", text)
        self.assertIn("BusinessAnalyticsAgent-Windows-x64.exe", text)
        self.assertIn("build/windows-package/reports/*.json", text)

    def test_macos_build_uses_existing_audited_script(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('bash packaging/build_macos.sh --version "$PACKAGE_VERSION"', text)
        self.assertIn("BusinessAnalyticsAgent-macOS-*.dmg", text)
        self.assertIn("build/macos-package/reports/*.json", text)

    def test_uploads_exclude_staging_runtime_state_and_secret_materials(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        upload_sections = [
            line.strip()
            for line in text.splitlines()
            if "build/windows-package/" in line or "build/macos-package/" in line
        ]
        joined = "\n".join(upload_sections).lower()
        for forbidden in (
            "staging",
            "pyinstaller-dist",
            "pyinstaller-work",
            "dmg-root",
            "uploads",
            "outputs",
            "mcp",
            ".p12",
            ".pfx",
            ".mobileprovision",
        ):
            self.assertNotIn(forbidden, joined)
        self.assertIn("installer/businessanalyticsagent-windows-x64.exe", joined)
        self.assertIn("dmg/businessanalyticsagent-macos-*.dmg", joined)
        self.assertIn("reports/*.json", joined)

    def test_release_verifies_boundary_and_marks_unsigned(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        for forbidden_check in (
            "-path '*/staging/*'",
            "-path '*/MCP/*'",
            "-path '*/uploads/*'",
            "-path '*/outputs/*'",
            "-name '*.p12'",
            "-name '*.mobileprovision'",
        ):
            self.assertIn(forbidden_check, text)
        self.assertIn("Unsigned Business Analytics Agent desktop test packages", text)
        self.assertIn("gh release create", text)


if __name__ == "__main__":
    unittest.main()
