"""P5 macOS app/DMG build script must keep the same release boundary."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = ROOT / "packaging" / "build_macos.sh"
PYINSTALLER_SPEC = ROOT / "packaging" / "business_agent.spec"
AUDIT_SCRIPT = ROOT / "packaging" / "audit_artifact.py"


class MacOSPackagingTests(unittest.TestCase):
    def test_build_script_enforces_native_runner_and_audits(self):
        script = BUILD_SCRIPT.read_text(encoding="utf-8")
        for required in (
            'uname -s',
            'macOS packages must be built on a native macOS runner.',
            'build_manifest.py',
            'staging-audit.json',
            'business_agent.spec',
            'app-audit.json',
            'BAA_ONEDIR_SELF_TEST',
            'dmg-root-audit.json',
            'dmg-audit.json',
            'BusinessAnalyticsAgent-macOS-$ARCH.dmg',
            '"unsigned": True',
        ):
            self.assertIn(required, script)

    def test_build_script_does_not_package_runtime_state_or_mcp(self):
        script = BUILD_SCRIPT.read_text(encoding="utf-8").lower()
        self.assertNotIn("$project_root/uploads", script)
        self.assertNotIn("$project_root/outputs", script)
        self.assertNotIn("$project_root/mcp", script)
        self.assertNotIn("requirements.txt", script)
        self.assertIn("--allow-contained-symlinks", script)

    def test_spec_declares_macos_bundle_metadata(self):
        spec = PYINSTALLER_SPEC.read_text(encoding="utf-8")
        self.assertIn('if sys.platform == "darwin":', spec)
        self.assertIn('name="Business Analytics Agent.app"', spec)
        self.assertIn('bundle_identifier="com.businessanalytics.agent"', spec)
        self.assertIn('"LSMinimumSystemVersion": "12.0"', spec)

    def test_audit_allows_only_explicit_contained_symlink_mode(self):
        audit = AUDIT_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("--allow-contained-symlinks", audit)
        self.assertIn("symbolic links must resolve inside the package", audit)
        self.assertIn("allow_contained_symlinks: bool = False", audit)


if __name__ == "__main__":
    unittest.main()
