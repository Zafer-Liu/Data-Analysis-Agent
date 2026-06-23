import tempfile
import unittest
from pathlib import Path

from agent.activation import ActivationContext
from agent.commands import CommandDef, CommandRegistry, CommandType
from agent.skills import SkillDef, SkillRegistry, load_skills


def _skill(name: str = "shared-name") -> SkillDef:
    return SkillDef(name, "Reusable workflow", "Do the workflow", Path("SKILL.md"))


def _command(name: str = "shared-name", aliases=()) -> CommandDef:
    return CommandDef(
        name=name, description="Explicit action", type=CommandType.PROMPT,
        aliases=tuple(aliases), prompt="Handle $ARGUMENTS",
    )


class TestActivationContracts(unittest.TestCase):
    def test_skill_and_command_namespaces_are_independent(self):
        skills = SkillRegistry([_skill()])
        commands = CommandRegistry([_command()])
        self.assertIsNotNone(skills.get("shared-name"))
        self.assertIsNotNone(commands.get("shared-name"))

    def test_activation_is_mutually_exclusive(self):
        with self.assertRaises(ValueError):
            ActivationContext(skill_name="funnel-analysis", command_name="sql")
        self.assertEqual(ActivationContext(skill_name="funnel-analysis").kind, "skill")
        self.assertEqual(ActivationContext(command_name="sql").to_dict(), {
            "kind": "command", "name": "sql",
        })

    def test_command_alias_resolves_to_canonical_name(self):
        registry = CommandRegistry([_command("status", ("s",))])
        self.assertEqual(registry.canonical_name("s"), "status")

    def test_command_name_and_alias_conflicts_fail(self):
        registry = CommandRegistry([_command("status", ("s",))])
        with self.assertRaises(ValueError):
            registry.register(_command("s"))
        with self.assertRaises(ValueError):
            registry.register(_command("second", ("status",)))

    def test_duplicate_skill_name_fails(self):
        registry = SkillRegistry([_skill("cohort")])
        with self.assertRaises(ValueError):
            registry.register(_skill("cohort"))

    def test_invalid_command_type_and_skill_source_fail(self):
        with self.assertRaises(ValueError):
            CommandDef("bad", "Bad", "prompt", prompt="text")
        with self.assertRaises(ValueError):
            SkillDef("bad", "Bad", "text", Path("SKILL.md"), source="remote")

    def test_legacy_skill_loader_import_remains_compatible(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "sample"
            folder.mkdir()
            (folder / "SKILL.md").write_text(
                "---\nname: sample-skill\ndescription: Sample\n---\nRun $ARGUMENTS",
                encoding="utf-8",
            )
            self.assertIn("sample-skill", load_skills(Path(tmp)))


if __name__ == "__main__":
    unittest.main()
