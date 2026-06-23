#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Smoke tests for the Flask API surface — boots the app with test_client and
hits the public routes without spinning up an LLM or external data source.

These catch:
  - Blueprint registration regressions
  - Static asset routing breaks (vendor / modules / css)
  - HTML template render errors
  - Trivial route-method mismatches
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api import create_app
from api import saved_sessions
from api.chat import _resolve_data_context
from data.session import ChatSession
from data.sources.sql import SQLDataSource


class _ContextSource(SQLDataSource):
    """Remote SQL metadata double for preview-context tests."""
    name = "warehouse"

    def __init__(self, tables):
        self._tables = tables

    def list_tables(self):
        return list(self._tables)


class TestPreviewAnalysisContext(unittest.TestCase):
    def test_valid_selected_table_is_resolved(self):
        sess = ChatSession()
        source_id = sess.add_source(_ContextSource(["orders"]))
        context = _resolve_data_context(sess, {
            "tables": [{"source_id": source_id, "table": "orders"}],
        })
        self.assertEqual(context["tables"][0]["source_name"], "warehouse")
        self.assertEqual(context["tables"][0]["query_table"], "orders")

    def test_context_uses_prefixed_name_when_sources_collide(self):
        sess = ChatSession()
        sess.add_source(_ContextSource(["orders"]))
        source_id = sess.add_source(_ContextSource(["orders"]))
        context = _resolve_data_context(sess, {
            "source_id": source_id,
            "table": "orders",
        })
        self.assertEqual(context["tables"][0]["query_table"], "src2__orders")

    def test_multiple_selected_tables_are_resolved(self):
        sess = ChatSession()
        source_id = sess.add_source(_ContextSource(["orders", "customers"]))
        context = _resolve_data_context(sess, {"tables": [
            {"source_id": source_id, "table": "orders"},
            {"source_id": source_id, "table": "customers"},
        ]})
        self.assertEqual(
            [item["table"] for item in context["tables"]],
            ["orders", "customers"],
        )

    def test_cross_source_selection_uses_merged_prefixes(self):
        sess = ChatSession()
        source1 = sess.add_source(_ContextSource(["orders"]))
        source2 = sess.add_source(_ContextSource(["customers"]))
        context = _resolve_data_context(sess, {"tables": [
            {"source_id": source1, "table": "orders"},
            {"source_id": source2, "table": "customers"},
        ]})
        self.assertEqual(
            [item["query_table"] for item in context["tables"]],
            ["src1__orders", "src2__customers"],
        )

    def test_unknown_or_inactive_source_is_ignored(self):
        sess = ChatSession()
        source_id = sess.add_source(_ContextSource(["orders"]))
        sess.toggle_source(source_id)
        self.assertIsNone(_resolve_data_context(sess, {
            "source_id": source_id,
            "table": "orders",
        }))


class TestAppBoots(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.client = cls.app.test_client()

    def test_index_renders(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        body = r.get_data(as_text=True)
        # Must include the chat skeleton + module loader
        self.assertIn("messages", body)
        self.assertIn("static/js/app.js", body)

    def test_health_is_minimal_and_does_not_expose_config(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json(), {
            "ok": True,
            "service": "business-analytics-agent",
            "status": "healthy",
        })

    def test_session_new_returns_id(self):
        r = self.client.post("/api/session/new")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertIn("session_id", data)
        self.assertTrue(data["session_id"])

    def test_models_endpoint(self):
        r = self.client.get("/api/models")
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.get_json(), dict)

    def test_models_defaults_endpoint(self):
        r = self.client.get("/api/models/defaults")
        self.assertEqual(r.status_code, 200)
        # Built-in providers (deepseek/openai/claude) should be in defaults
        data = r.get_json()
        self.assertIn("deepseek", data)
        self.assertIn("openai", data)

    def test_saved_sessions_endpoint(self):
        r = self.client.get("/api/saved-sessions")
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.get_json(), list)

    def test_datasource_configs_endpoint(self):
        r = self.client.get("/api/datasource-configs")
        self.assertEqual(r.status_code, 200)

    def test_mcp_servers_endpoint(self):
        r = self.client.get("/api/mcp/servers")
        self.assertEqual(r.status_code, 200)

    def test_skills_catalog_endpoint(self):
        r = self.client.get("/api/skills")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertIsInstance(data.get("skills"), list)
        self.assertTrue(any(skill.get("name") == "funnel-analysis" for skill in data["skills"]))
        self.assertTrue(all("prompt" not in skill for skill in data["skills"]))


class TestStaticAssets(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.client = create_app().test_client()

    def _check(self, path, min_size=100):
        r = self.client.get(path)
        self.assertEqual(r.status_code, 200, f"{path} returned {r.status_code}")
        self.assertGreaterEqual(len(r.get_data()), min_size, f"{path} suspiciously small")

    def test_main_css_entry(self):
        # The entry file is a thin @import shim — small by design.
        # We check it loads and lists all the part files.
        r = self.client.get("/static/css/agent_chat.css")
        self.assertEqual(r.status_code, 200)
        body = r.get_data(as_text=True)
        for part in ("tokens", "layout", "chat", "modals", "kb"):
            self.assertIn(f"parts/{part}.css", body, f"missing @import for {part}")

    def test_css_parts(self):
        # Each part must be real CSS (not an HTML 404 page).
        for name, min_size in [
            ("tokens", 5000), ("layout", 2000), ("chat", 10000),
            ("modals", 10000), ("kb", 4000),
        ]:
            self._check(f"/static/css/parts/{name}.css", min_size=min_size)

    def test_app_js(self):
        self._check("/static/js/app.js", min_size=1000)

    def test_vendor_marked(self):
        self._check("/static/vendor/marked.min.js", min_size=10000)

    def test_vendor_purify(self):
        self._check("/static/vendor/purify.min.js", min_size=10000)

    def test_module_chat_stream(self):
        self._check("/static/js/modules/chat_stream.js", min_size=1000)

    def test_preview_multiselect_and_custom_delete_dialog_are_wired(self):
        html = self.client.get("/").get_data(as_text=True)
        preview_js = self.client.get("/static/js/modules/preview.js").get_data(as_text=True)
        sessions_js = self.client.get("/static/js/modules/sessions.js").get_data(as_text=True)
        self.assertIn('id="preview-use-table"', html)
        self.assertIn('id="ov-delete-session"', html)
        self.assertIn("selectedTables", preview_js)
        self.assertIn("confirmDeleteSavedSession", sessions_js)
        delete_fn = sessions_js.split("async function deleteSavedSession", 1)[1].split(
            "async function confirmDeleteSavedSession", 1
        )[0]
        self.assertNotIn("confirm(", delete_fn)

    def test_agent_activity_has_no_reasoning_to_tool_gap(self):
        stream_response = self.client.get("/static/js/modules/chat_stream.js")
        vue_response = self.client.get("/static/js/modules/vue_app.js")
        try:
            stream_js = stream_response.get_data(as_text=True)
            vue_js = vue_response.get_data(as_text=True)
        finally:
            stream_response.close()
            vue_response.close()
        reasoning_handler = stream_js.split("function _onReasoning", 1)[1].split("function _onText", 1)[0]
        start_tool = vue_js.split("function startTool", 1)[1].split("function endTool", 1)[0]
        self.assertIn("_showToolActivity(ctx)", reasoning_handler)
        self.assertNotIn("_hideToolActivity(ctx)", reasoning_handler)
        self.assertIn("item.kind !== ACTIVITY_KIND", start_tool)
        self.assertNotIn("hideToolActivity(target)", start_tool)

    def test_module_vue_app(self):
        self._check("/static/js/modules/vue_app.js", min_size=1000)

    def test_module_theme(self):
        self._check("/static/js/modules/theme.js", min_size=500)


class TestIndexIntegrity(unittest.TestCase):
    """Every data-action in the rendered HTML must have a handler registered in app.js."""

    @classmethod
    def setUpClass(cls):
        cls.client = create_app().test_client()

    def test_actions_all_mapped(self):
        import re
        html = self.client.get("/").get_data(as_text=True)
        actions = set(re.findall(r'data-action="([^:"]+)', html))
        app_js = self.client.get("/static/js/app.js").get_data(as_text=True)
        m = re.search(r"const ACTIONS = \{(.+?)\n  \};", app_js, re.S)
        self.assertIsNotNone(m, "ACTIONS table not found in app.js")
        registered = set(re.findall(r"^\s{4}(\w+):", m.group(1), re.M))
        missing = actions - registered
        self.assertFalse(missing, f"HTML uses unregistered actions: {missing}")

    def test_no_inline_handlers_remain(self):
        import re
        html = self.client.get("/").get_data(as_text=True)
        inline = re.findall(r'on(?:click|change|input|keydown)="[^"]+"', html)
        self.assertEqual(inline, [], f"inline handlers found: {inline}")


class TestSavedSessionRename(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_save_dir = saved_sessions.SAVE_DIR
        saved_sessions.SAVE_DIR = Path(self.tmp.name)
        saved_sessions.SAVE_DIR.mkdir(parents=True, exist_ok=True)
        self.client = create_app().test_client()
        self.file = saved_sessions.SAVE_DIR / "sample.json"
        self.file.write_text(json.dumps({
            "name": "Old name",
            "saved_at": "2026-06-17T08:00:00",
            "history": [{"role": "user", "content": "hello"}],
        }), encoding="utf-8")

    def tearDown(self):
        saved_sessions.SAVE_DIR = self.old_save_dir
        self.tmp.cleanup()

    def test_rename_updates_display_name(self):
        r = self.client.post("/api/saved-sessions/sample.json/rename", json={"name": "New name"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["name"], "New name")
        data = json.loads(self.file.read_text(encoding="utf-8"))
        self.assertEqual(data["name"], "New name")
        self.assertIn("renamed_at", data)

        listed = self.client.get("/api/saved-sessions").get_json()
        self.assertEqual(listed[0]["name"], "New name")

    def test_rename_patch_compat(self):
        r = self.client.patch("/api/saved-sessions/sample.json", json={"name": "Compat name"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["name"], "Compat name")

    def test_rename_rejects_empty_name(self):
        r = self.client.post("/api/saved-sessions/sample.json/rename", json={"name": "  "})
        self.assertEqual(r.status_code, 400)

    def test_rename_missing_file(self):
        r = self.client.post("/api/saved-sessions/missing.json/rename", json={"name": "New"})
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
