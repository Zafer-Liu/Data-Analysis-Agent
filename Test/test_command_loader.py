import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from flask import Flask

from agent.commands import (
    CommandAvailabilityContext, CommandAvailabilityProvider,
    CommandDef, CommandDispatcher, CommandDispatchError, CommandLoader,
    CommandRegistry, CommandType, parse_slash_command,
)
from api.commands import _public_command, _public_diagnostic, bp as commands_bp


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
        local_ui = CommandDef(
            "clear", "Clear", CommandType.LOCAL_UI,
            handler_key="client:clear",
        )
        backend = CommandDef(
            "compact", "Compact", CommandType.BACKEND,
            handler_key="server:compact", protected=True, uses_model=True,
        )
        registry = CommandRegistry((prompt, local, local_ui, backend))
        dispatcher = CommandDispatcher(
            registry, {
                "client:status": lambda args, context: (args, context),
                "client:clear": lambda args, context: "cleared",
                "server:compact": lambda args, context: "compacted",
            },
        )
        rendered = await dispatcher.dispatch("review", "sales")
        self.assertEqual(rendered.prompt, "Review sales")
        self.assertEqual(
            dispatcher.prepare_agent_turn("review", "profit").prompt,
            "Review profit",
        )
        handled = await dispatcher.dispatch("s", "verbose", {"sid": "one"})
        self.assertEqual(handled.value, ("verbose", {"sid": "one"}))
        self.assertEqual((await dispatcher.dispatch("clear")).value, "cleared")
        self.assertEqual((await dispatcher.dispatch("compact")).value, "compacted")
        with self.assertRaises(CommandDispatchError):
            dispatcher.prepare_agent_turn("compact")

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
        self.assertIn("stop", names)
        self.assertIn("data", names)
        self.assertIn("jobs", names)
        self.assertIn("teams", names)
        self.assertNotIn("sql", names)
        self.assertNotIn("funnel-analysis", names)
        self.assertTrue(all("available" in item for item in payload["commands"]))
        by_name = {item["name"]: item for item in payload["commands"]}
        self.assertEqual(by_name["compact"]["type"], "backend")
        self.assertTrue(by_name["compact"]["uses_model"])
        self.assertNotIn("client_action", by_name["compact"])
        self.assertEqual(by_name["clear"]["type"], "local-ui")
        self.assertEqual(by_name["clear"]["client_action"], "clear")
        self.assertEqual(by_name["help"]["aliases"], ["h", "?"])
        self.assertEqual(by_name["compact"]["aliases"], ["c"])
        self.assertEqual(payload["diagnostic_count"], len(payload["diagnostics"]))

    def test_availability_provider_evaluates_session_and_workspace_rules(self):
        registry = CommandLoader().load()
        provider = CommandAvailabilityProvider()
        unavailable = CommandAvailabilityContext(
            history_length=1,
            model_available=False,
            workspace_mounted=False,
        )
        self.assertEqual(
            provider.evaluate(registry.get("compact"), unavailable).code,
            "not_enough_context",
        )
        self.assertEqual(
            provider.evaluate(registry.get("checkpoint"), unavailable).code,
            "workspace_required",
        )
        ready = CommandAvailabilityContext(
            history_length=4,
            model_available=True,
            workspace_mounted=True,
        )
        self.assertTrue(provider.evaluate(registry.get("compact"), ready).available)
        self.assertTrue(provider.evaluate(registry.get("teams"), ready).available)

    def test_public_diagnostics_remove_absolute_host_paths(self):
        diagnostic = _public_diagnostic(SimpleNamespace(
            path=r"C:\Users\private-user\.baa\commands\broken.md",
            source="user",
            error="missing description",
        ))
        self.assertEqual(diagnostic["path"], "broken.md")
        self.assertNotIn("private-user", str(diagnostic))

    def test_loader_reuses_immutable_snapshot_and_refreshes_on_change(self):
        _write_command(self.user, "first.md", "First $ARGUMENTS")
        first = CommandLoader(builtins=(), user_dir=self.user).load()
        cached = CommandLoader(builtins=(), user_dir=self.user).load()
        self.assertIs(first, cached)
        self.assertTrue(first.frozen)
        with self.assertRaises(RuntimeError):
            first.register(CommandDef(
                "late", "Late", CommandType.PROMPT, prompt="Late",
            ))

        _write_command(self.user, "second.md", "Second $ARGUMENTS")
        refreshed = CommandLoader(builtins=(), user_dir=self.user).load()
        self.assertIsNot(first, refreshed)
        self.assertIsNotNone(refreshed.get("second"))

    def test_command_metadata_limits_and_prompt_size_diagnostics(self):
        with self.assertRaisesRegex(ValueError, "description exceeds"):
            CommandDef(
                "oversized",
                "x" * 501,
                CommandType.PROMPT,
                prompt="Run",
            )
        prompt = CommandDef(
            "large-prompt",
            "Large prompt",
            CommandType.PROMPT,
            prompt="x" * 28_001,
        )
        public = _public_command(prompt, None)
        self.assertEqual(public["prompt_chars"], 28_001)
        self.assertGreater(public["prompt_tokens_est"], 8_000)
        self.assertTrue(public["prompt_size_warning"])
        self.assertNotIn("prompt", public)

    def test_builtin_aliases_resolve_to_canonical_commands(self):
        registry = CommandLoader().load()
        self.assertEqual(registry.get("?").name, "help")
        self.assertEqual(registry.get("c").name, "compact")
        self.assertEqual(registry.get("session").name, "sessions")
        self.assertEqual(registry.get("n").name, "new")

    def test_custom_prompt_defaults_to_optional_arguments(self):
        _write_command(self.user, "review.md", "Review $ARGUMENTS")
        command = CommandLoader(builtins=(), user_dir=self.user).load().get("review")
        self.assertEqual(command.arguments, "optional")

    def test_custom_command_cannot_declare_trusted_fields(self):
        _write_command(self.user, "safe.md")
        path = self.user / "unsafe-handler.md"
        path.write_text(
            "---\ndescription: Unsafe\nhandler-key: server:anything\n---\nPrompt",
            encoding="utf-8",
        )
        loader = CommandLoader(builtins=(), user_dir=self.user)
        registry = loader.load()
        self.assertIsNotNone(registry.get("safe"))
        self.assertIsNone(registry.get("unsafe-handler"))
        self.assertIn("trusted fields", loader.diagnostics()[0].error)

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
