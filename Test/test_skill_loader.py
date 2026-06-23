import tempfile
import unittest
from pathlib import Path

from agent.skills import SkillDependencyError, SkillExecutor, SkillLoader


def _write_skill(root: Path, folder: str, name: str, body: str = "Run $ARGUMENTS", **meta) -> Path:
    target = root / folder
    target.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"name: {name}", f"description: {meta.get('description', name)}"]
    if "allowed_tools" in meta:
        lines.append("allowedTools:")
        lines.extend(f"  - {tool}" for tool in meta["allowed_tools"])
    lines.extend(["---", body])
    path = target / "SKILL.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


class TestSkillLoader(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.builtin = self.root / "builtin"
        self.user = self.root / "user"
        self.workspace = self.root / "workspace"

    def tearDown(self):
        self.temp.cleanup()

    def test_workspace_overrides_user_then_builtin(self):
        _write_skill(self.builtin, "shared", "shared", description="builtin")
        _write_skill(self.user, "shared", "shared", description="user")
        _write_skill(self.workspace, "shared", "shared", description="workspace")
        loader = SkillLoader(
            builtin_dir=self.builtin, user_dir=self.user, workspace_dir=self.workspace,
        )
        skill = loader.load_all()["shared"]
        self.assertEqual(skill.description, "workspace")
        self.assertEqual(skill.source, "workspace")

    def test_invalid_skill_is_diagnostic_not_startup_failure(self):
        bad = self.builtin / "bad"
        bad.mkdir(parents=True)
        (bad / "SKILL.md").write_text("not frontmatter", encoding="utf-8")
        loader = SkillLoader(builtin_dir=self.builtin, user_dir=self.user)
        self.assertEqual(loader.load_all(), {})
        self.assertEqual(len(loader.diagnostics()), 1)
        self.assertIn("frontmatter", loader.diagnostics()[0].error)

    def test_hot_reload_uses_last_valid_cache_on_parse_error(self):
        path = _write_skill(self.builtin, "live", "live", body="version one")
        loader = SkillLoader(builtin_dir=self.builtin, user_dir=self.user)
        loader.load_all()
        path.write_text("broken", encoding="utf-8")
        self.assertEqual(loader.get("live").prompt, "version one")
        path.write_text("---\nname: live\ndescription: Live\n---\nversion two", encoding="utf-8")
        self.assertEqual(loader.get("live").prompt, "version two")

    def test_resources_are_indexed_but_scripts_are_never_imported(self):
        skill_path = _write_skill(self.builtin, "resourceful", "resourceful")
        marker = self.root / "executed.txt"
        references = skill_path.parent / "references"
        scripts = skill_path.parent / "scripts"
        assets = skill_path.parent / "assets"
        references.mkdir()
        scripts.mkdir()
        assets.mkdir()
        (references / "schema.md").write_text("schema", encoding="utf-8")
        (assets / "template.txt").write_text("template", encoding="utf-8")
        (scripts / "danger.py").write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n",
            encoding="utf-8",
        )
        loader = SkillLoader(builtin_dir=self.builtin, user_dir=self.user)
        skill = loader.load_all()["resourceful"]
        self.assertEqual({item.kind for item in skill.resources}, {"references", "scripts", "assets"})
        self.assertFalse(marker.exists())
        activation = SkillExecutor().activate(skill, "request")
        self.assertIn("references/schema.md", activation.prompt)
        self.assertFalse(marker.exists())

    def test_allowed_tools_only_restrict_already_exposed_tools(self):
        _write_skill(
            self.builtin, "restricted", "restricted",
            allowed_tools=["get_schema", "generate_ppt"],
        )
        skill = SkillLoader(builtin_dir=self.builtin, user_dir=self.user).load_all()["restricted"]
        activation = SkillExecutor().activate(skill, "analyze")
        self.assertEqual(
            activation.filter_exposed_tools({"get_schema", "query_data"}),
            {"get_schema"},
        )

    def test_unknown_allowed_tool_is_rejected_by_executor(self):
        _write_skill(
            self.builtin, "unknown-tool", "unknown-tool",
            allowed_tools=["definitely_not_registered"],
        )
        skill = SkillLoader(builtin_dir=self.builtin, user_dir=self.user).load_all()["unknown-tool"]
        with self.assertRaises(SkillDependencyError):
            SkillExecutor().activate(skill, "analyze")


if __name__ == "__main__":
    unittest.main()
