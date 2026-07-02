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
            "Resolve release tag",
            'gh release create "$RELEASE_TAG"',
            "--target ${{ github.sha }}",
        ):
            self.assertIn(required, text)

    def test_build_jobs_generate_frontend_bundle_before_packaging(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertGreaterEqual(text.count("pnpm/action-setup@v4"), 2)
        self.assertGreaterEqual(text.count("version: 11.7.0"), 2)
        self.assertGreaterEqual(text.count("actions/setup-node@v4"), 2)
        self.assertGreaterEqual(text.count("cache: pnpm"), 2)
        self.assertGreaterEqual(text.count("pnpm install --frozen-lockfile"), 2)
        self.assertGreaterEqual(text.count("pnpm run build"), 2)
        self.assertNotIn("cache: npm", text)
        self.assertNotIn("npm ci", text)
        self.assertNotIn("corepack enable", text)
        self.assertLess(text.index("pnpm run build"), text.index("packaging/build_windows.ps1"))
        self.assertLess(text.rindex("pnpm run build"), text.index("packaging/build_macos.sh"))

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

    def test_release_uploads_only_installers_and_can_rerun(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("Duplicate release asset name after normalization", text)
        self.assertIn("find release-artifacts -type f \\( -name '*.exe' -o -name '*.dmg' \\)", text)
        self.assertIn('gh release view "$RELEASE_TAG"', text)
        self.assertIn('gh release delete-asset "$RELEASE_TAG" "$asset" -y', text)
        self.assertIn('gh release upload "$RELEASE_TAG" "${files[@]}" --clobber', text)


if __name__ == "__main__":
    unittest.main()
