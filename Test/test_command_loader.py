import tempfile
import unittest
from pathlib import Path

from flask import Flask

from agent.commands import (
    CommandDef, CommandDispatcher, CommandDispatchError, CommandLoader,
    CommandRegistry, CommandType, parse_slash_command,
)
from api.commands import bp as commands_bp


def _write_command(root: Path, relative: str, body: str = "Handle $ARGUMENTS", **meta) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"description: {meta.get('description', relative)}"]
    if aliases := meta.get("aliases"):
        lines.append("aliases:")
        lines.extend(f"  - {alias}" for alias in aliases)
    if command_type := meta.get("type"):
        lines.append(f"type: {command_type}")
    lines.extend(["---", body])
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


class TestCommandLoader(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.user = self.root / "user"
        self.workspace = self.root / "workspace"

    def tearDown(self):
        self.temp.cleanup()

    def test_workspace_overrides_user_custom_command(self):
        _write_command(self.user, "review.md", "user")
        _write_command(self.workspace, "review.md", "workspace")
        loader = CommandLoader(builtins=(), user_dir=self.user, workspace_dir=self.workspace)
        self.assertEqual(loader.load().get("review").prompt, "workspace")

    def test_nested_path_becomes_namespace_and_alias_resolves(self):
        _write_command(self.user, "git/log.md", aliases=["history"])
        registry = CommandLoader(builtins=(), user_dir=self.user).load()
        self.assertEqual(registry.get("history").name, "git:log")

    def test_protected_builtin_cannot_be_overridden(self):
        builtin = CommandDef(
            "status", "Status", CommandType.LOCAL,
            handler_key="client:status", protected=True,
        )
        _write_command(self.workspace, "status.md", "malicious")
        loader = CommandLoader(
            builtins=(builtin,), user_dir=self.user, workspace_dir=self.workspace,
        )
        registry = loader.load()
        self.assertEqual(registry.get("status").type, CommandType.LOCAL)
        self.assertIn("protected", loader.diagnostics()[0].error)

    def test_custom_command_cannot_declare_backend_handler(self):
        _write_command(self.user, "unsafe.md", type="backend")
        loader = CommandLoader(builtins=(), user_dir=self.user)
        self.assertIsNone(loader.load().get("unsafe"))
        self.assertIn("type: prompt", loader.diagnostics()[0].error)

    async def test_dispatcher_renders_prompt_and_dispatches_typed_handler(self):
        prompt = CommandDef(
            "review", "Review", CommandType.PROMPT, prompt="Review $ARGUMENTS",
        )
        local = CommandDef(
            "status", "Status", CommandType.LOCAL, aliases=("s",),
            handler_key="client:status",
        )
        registry = CommandRegistry((prompt, local))
        dispatcher = CommandDispatcher(
            registry, {"client:status": lambda args, context: (args, context)},
        )
        rendered = await dispatcher.dispatch("review", "sales")
        self.assertEqual(rendered.prompt, "Review sales")
        self.assertEqual(
            dispatcher.prepare_agent_turn("review", "profit").prompt,
            "Review profit",
        )
        handled = await dispatcher.dispatch("s", "verbose", {"sid": "one"})
        self.assertEqual(handled.value, ("verbose", {"sid": "one"}))

    async def test_missing_handler_and_unknown_command_fail_closed(self):
        local = CommandDef(
            "status", "Status", CommandType.LOCAL, handler_key="client:status",
        )
        dispatcher = CommandDispatcher(CommandRegistry((local,)))
        with self.assertRaises(CommandDispatchError):
            await dispatcher.dispatch("status")
        with self.assertRaises(CommandDispatchError):
            await dispatcher.dispatch("missing")

    def test_parser_recognizes_slash_and_arguments(self):
        parsed = parse_slash_command(" /git:log last week ")
        self.assertTrue(parsed.is_command)
        self.assertEqual((parsed.name, parsed.arguments), ("git:log", "last week"))
        self.assertFalse(parse_slash_command("normal message").is_command)

    def test_commands_api_uses_registry_catalog(self):
        app = Flask(__name__)
        app.register_blueprint(commands_bp)
        response = app.test_client().get("/api/commands")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        names = {item["name"] for item in payload["commands"]}
        self.assertIn("status", names)
        self.assertIn("help", names)
        self.assertNotIn("sql", names)
        self.assertNotIn("funnel-analysis", names)
        self.assertTrue(all("available" in item for item in payload["commands"]))
        self.assertTrue(all("client_action" in item for item in payload["commands"]))

    def test_project_keeps_skill_and_command_content_in_separate_roots(self):
        project = Path(__file__).resolve().parents[1]
        self.assertTrue((project / "skills" / "funnel-analysis" / "SKILL.md").is_file())
        self.assertTrue((project / "skills" / "sql" / "SKILL.md").is_file())
        self.assertTrue((project / "commands" / "help.md").is_file())
        registry = CommandLoader().load()
        self.assertIsNone(registry.get("sql"))
        self.assertEqual(registry.get("help").path.parent, project / "commands")
        self.assertTrue(registry.get("help").protected)


if __name__ == "__main__":
    unittest.main()
