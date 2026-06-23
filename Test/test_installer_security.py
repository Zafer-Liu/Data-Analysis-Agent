"""P4 Windows installer must wrap only the audited PyInstaller onedir."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SETUP_SCRIPT = ROOT / "installer" / "setup.iss"
BUILD_SCRIPT = ROOT / "packaging" / "build_windows.ps1"
PYINSTALLER_SPEC = ROOT / "packaging" / "business_agent.spec"


class InstallerSecurityTests(unittest.TestCase):
    def test_installer_has_exactly_one_onedir_source(self):
        script = SETUP_SCRIPT.read_text(encoding="utf-8")
        source_lines = [line for line in script.splitlines() if line.startswith("Source:")]
        self.assertEqual(len(source_lines), 1)
        self.assertIn(r'Source: "{#OnedirSource}\*"', source_lines[0])

    def test_legacy_python_bootstrap_and_runtime_dirs_are_absent(self):
        script = SETUP_SCRIPT.read_text(encoding="utf-8")
        lowered = script.lower()
        for forbidden in ("launch.bat", "pythonminver", "pip install", "{app}\\uploads", "{app}\\outputs"):
            self.assertNotIn(forbidden, lowered)

    def test_per_user_install_runs_frozen_executable(self):
        script = SETUP_SCRIPT.read_text(encoding="utf-8")
        self.assertIn(r"DefaultDirName={localappdata}\Programs\BusinessAnalyticsAgent", script)
        self.assertIn("PrivilegesRequired=lowest", script)
        self.assertIn('#define AppExeName "BusinessAnalyticsAgent.exe"', script)
        self.assertIn(r'Filename: "{app}\{#AppExeName}"', script)
        spec = PYINSTALLER_SPEC.read_text(encoding="utf-8")
        self.assertIn('STAGING / "static" / "Images" / "icon.png"', spec)

    def test_build_script_enforces_staging_audits_and_frozen_smoke(self):
        script = BUILD_SCRIPT.read_text(encoding="utf-8")
        for required in (
            "build_manifest.py",
            "staging-audit.json",
            "business_agent.spec",
            "onedir-audit.json",
            "BAA_ONEDIR_SELF_TEST",
            "PrepareOnly",
            "installer-audit.json",
            "Get-FileHash",
        ):
            self.assertIn(required, script)
        self.assertNotIn("requirements.txt", script)


if __name__ == "__main__":
    unittest.main()
