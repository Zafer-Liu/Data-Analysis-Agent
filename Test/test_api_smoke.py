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
        self.assertIn("static/dist/chat-app.js", body)
        self.assertIn("static/vendor/vue.global.prod.js", body)
        self.assertNotIn("unpkg.com", body)

    def test_health_is_minimal_and_does_not_expose_config(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json(), {
            "ok": True,
            "service": "business-analytics-agent",
            "status": "healthy",
        })

    def test_browser_security_headers_and_local_cors(self):
        page = self.client.get("/")
        self.assertEqual(page.headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(page.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(page.headers.get("Referrer-Policy"), "no-referrer")
        page_csp = page.headers.get("Content-Security-Policy", "")
        self.assertIn("frame-ancestors 'none'", page_csp)
        self.assertNotIn("https://unpkg.com", page_csp)
        self.assertIn("script-src 'self';", page_csp)

        chart = self.client.get("/api/chart/missing-fe0-chart")
        chart_csp = chart.headers.get("Content-Security-Policy", "")
        self.assertIn("frame-ancestors 'self'", chart_csp)
        self.assertNotIn("X-Frame-Options", chart.headers)

        allowed = self.client.get(
            "/api/health",
            headers={"Origin": "http://127.0.0.1:5001"},
        )
        self.assertEqual(
            allowed.headers.get("Access-Control-Allow-Origin"),
            "http://127.0.0.1:5001",
        )
        rejected = self.client.get(
            "/api/health",
            headers={"Origin": "https://attacker.example"},
        )
        self.assertIsNone(rejected.headers.get("Access-Control-Allow-Origin"))

        blocked_write = self.client.post(
            "/api/session/new",
            headers={"Origin": "https://attacker.example"},
        )
        self.assertEqual(blocked_write.status_code, 403)
        allowed_write = self.client.post(
            "/api/session/new",
            headers={"Origin": "http://127.0.0.1:5001"},
        )
        self.assertEqual(allowed_write.status_code, 200)

    def test_session_new_returns_id(self):
        r = self.client.post("/api/session/new")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertIn("session_id", data)
        self.assertTrue(data["session_id"])

    def test_chat_rejects_object_message_without_500(self):
        r = self.client.post(
            "/api/session/invalid-message/chat",
            json={"message": {"label": "整体概览", "value": "overview"}},
        )
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["code"], "invalid_message_type")

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
        # workspace.css was merged into chat.css in FE2-C and removed.
        r = self.client.get("/static/css/agent_chat.css")
        self.assertEqual(r.status_code, 200)
        body = r.get_data(as_text=True)
        for part in ("tokens", "layout", "chat", "modals", "kb"):
            self.assertIn(f"parts/{part}.css", body, f"missing @import for {part}")
        self.assertNotIn("parts/workspace.css", body,
                         "workspace.css was merged into chat.css and must not appear as a separate @import")

    def test_chart_detail_template_has_been_removed(self):
        root = Path(__file__).resolve().parents[1]
        self.assertFalse((root / "templates/chart-detail.html").exists())

    def test_theme_bootstrap_and_dark_mode_contract(self):
        root = Path(__file__).resolve().parents[1]
        chat_html = self.client.get("/").get_data(as_text=True)
        dashboard_html = self.client.get("/dashboard/frontend-contract").get_data(as_text=True)
        tokens_css = self.client.get("/static/css/parts/tokens.css").get_data(as_text=True)
        theme_js = (root / "frontend" / "core" / "theme.js").read_text(encoding="utf-8")
        self.assertIn("static/js/theme_bootstrap.js", chat_html)
        self.assertIn("static/js/theme_bootstrap.js", dashboard_html)
        self.assertLess(
            chat_html.index("js/theme_bootstrap.js"),
            chat_html.index("css/agent_chat.css"),
        )
        self.assertLess(
            dashboard_html.index("js/theme_bootstrap.js"),
            dashboard_html.index("css/parts/tokens.css"),
        )
        self.assertIn('data-theme="dark"', tokens_css)
        self.assertIn("function toggleTheme()", theme_js)
        self.assertIn("localStorage.setItem(STORAGE_KEY, theme)", theme_js)
        self.assertIn('id="theme-toggle"', chat_html)

    def test_fe1_vite_entries_and_api_client_contract(self):
        root = Path(__file__).resolve().parents[1]
        chat_html = self.client.get("/").get_data(as_text=True)
        dashboard_html = self.client.get("/dashboard/frontend-contract").get_data(as_text=True)

        self.assertIn(
            'type="module" src="/static/dist/chat-app.js?v=fe1-chat-app-4"', chat_html)
        self.assertIn('type="module" src="/static/dist/dashboard.js"', dashboard_html)
        self._check("/static/dist/chat-app.js", min_size=200000)
        self._check("/static/dist/dashboard.js", min_size=50)

        manifest_path = root / "static" / "dist" / ".vite" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertNotIn("frontend/entries/chat.js", manifest)
        self.assertTrue(manifest["frontend/entries/dashboard.js"]["isEntry"])

        api_client = (root / "frontend" / "core" / "api-client.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('credentials: "same-origin"', api_client)
        self.assertIn('headers.set("Content-Type", JSON_CONTENT_TYPE)', api_client)
        self.assertIn("throw new ApiError", api_client)

    def test_fe1_core_bundle_replaces_legacy_dom_theme_overlay_scripts(self):
        root = Path(__file__).resolve().parents[1]
        html = self.client.get("/").get_data(as_text=True)

        self.assertIn("static/dist/chat-app.js", html)
        for legacy_name in ("dom", "theme", "overlay"):
            self.assertNotIn(f"static/js/modules/{legacy_name}.js", html)
            self.assertFalse((root / "static" / "js" / "modules" / f"{legacy_name}.js").exists())

        self._check("/static/dist/chat-app.js", min_size=200000)

    def test_fe1_feature_bundle_replaces_models_skills_and_slash_scripts(self):
        root = Path(__file__).resolve().parents[1]
        html = self.client.get("/").get_data(as_text=True)

        for feature_name in ("models", "skills", "slash"):
            legacy_path = f"static/js/modules/{feature_name}.js"
            self.assertNotIn(legacy_path, html)
            self.assertFalse((root / legacy_path).exists())
            self.assertTrue(
                (root / "frontend" / "features" / f"{feature_name}.js").exists()
            )

        self.assertLessEqual(html.count("<script"), 6)
        self._check("/static/dist/chat-app.js", min_size=200000)

    def test_fe1_workspace_teams_and_panel_modules_replace_legacy_scripts(self):
        root = Path(__file__).resolve().parents[1]
        html = self.client.get("/").get_data(as_text=True)

        for feature_name in ("workspace", "teams"):
            legacy_path = f"static/js/modules/{feature_name}.js"
            self.assertNotIn(legacy_path, html)
            self.assertFalse((root / legacy_path).exists())
            self.assertTrue(
                (root / "frontend" / "features" / f"{feature_name}.js").exists()
            )

        for legacy_name, feature_name in (
            ("static/js/knowledge_panel.js", "knowledge"),
            ("static/js/mcp_settings.js", "mcp"),
        ):
            self.assertNotIn(legacy_name, html)
            self.assertFalse((root / legacy_name).exists())
            self.assertTrue(
                (root / "frontend" / "features" / f"{feature_name}.js").exists()
            )

        self.assertIn("static/dist/chat-app.js", html)
        self.assertNotIn("static/dist/panels.js", html)
        self.assertLessEqual(html.count("<script"), 6)
        self._check("/static/dist/chat-app.js", min_size=200000)

    def test_fe1_chat_stream_module_replaces_legacy_script(self):
        root = Path(__file__).resolve().parents[1]
        html = self.client.get("/").get_data(as_text=True)

        self.assertNotIn("static/js/modules/chat_stream.js", html)
        self.assertFalse((root / "static" / "js" / "modules" / "chat_stream.js").exists())
        self.assertTrue((root / "frontend" / "features" / "chat-stream.js").exists())
        self.assertIn("static/dist/chat-app.js", html)
        self.assertNotIn("static/dist/stream.js", html)
        self._check("/static/dist/chat-app.js", min_size=200000)

    def test_fe1_vue_islands_bundle_replaces_legacy_script(self):
        root = Path(__file__).resolve().parents[1]
        html = self.client.get("/").get_data(as_text=True)

        self.assertNotIn("static/js/modules/vue_app.js", html)
        self.assertFalse((root / "static" / "js" / "modules" / "vue_app.js").exists())
        self.assertTrue((root / "frontend" / "features" / "vue-app.js").exists())
        ui_dir = root / "frontend" / "features" / "ui"
        self.assertEqual(
            {path.name for path in ui_dir.glob("*-ui.js")},
            {
                "global-ui.js",
                "job-history-ui.js",
                "chat-ui.js",
                "settings-ui.js",
                "knowledge-ui.js",
                "mcp-ui.js",
                "workspace-ui.js",
            },
        )
        self.assertIn("static/dist/chat-app.js", html)
        self.assertNotIn("static/dist/ui.js", html)
        self._check("/static/dist/chat-app.js", min_size=200000)
        ui_source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(ui_dir.glob("*-ui.js"))
        )
        self.assertNotIn("window.BAA.vue", ui_source)
        registry = (
            root / "frontend" / "core" / "ui-registry.js"
        ).read_text(encoding="utf-8")
        self.assertIn("export function registerUiIsland", registry)
        self.assertIn("export function getUiIsland", registry)

        event_bus = (
            root / "frontend" / "core" / "event-bus.js"
        ).read_text(encoding="utf-8")
        app_store = (
            root / "frontend" / "core" / "app-store.js"
        ).read_text(encoding="utf-8")
        self.assertIn("export const eventBus", event_bus)
        self.assertIn("function once(eventName, listener)", event_bus)
        self.assertIn("export const appStore", app_store)
        self.assertIn('eventBus.emit("state:change", change)', app_store)
        self.assertIn('eventBus.emit(`state:${String(key)}`, change)', app_store)
        self.assertIn('api ? "ui:registered" : "ui:unregistered"', registry)

    def test_sidebar_does_not_render_a_horizontal_scroll_gutter(self):
        layout_css = self.client.get("/static/css/parts/layout.css").get_data(
            as_text=True
        )
        sidebar_main = layout_css.split(".sb-main {", 1)[1].split("}", 1)[0]
        self.assertIn("overflow-y: auto", sidebar_main)
        self.assertIn("overflow-x: hidden", sidebar_main)

    def test_css_parts(self):
        # Each part must be real CSS (not an HTML 404 page).
        # workspace.css was merged into chat.css in FE2-C and no longer exists separately.
        for name, min_size in [
            ("tokens", 5000), ("layout", 2000), ("chat", 20000),
            ("modals", 10000), ("kb", 4000),
        ]:
            self._check(f"/static/css/parts/{name}.css", min_size=min_size)

    def test_app_js(self):
        root = Path(__file__).resolve().parents[1]
        app_js = (root / "frontend" / "legacy" / "app.js").read_text(encoding="utf-8")
        self.assertGreater(len(app_js), 1000)

    def test_vendor_marked(self):
        self._check("/static/vendor/marked.min.js", min_size=10000)

    def test_vendor_purify(self):
        self._check("/static/vendor/purify.min.js", min_size=10000)

    def test_module_chat_stream(self):
        self._check("/static/dist/chat-app.js", min_size=200000)

    def test_module_command_handlers(self):
        root = Path(__file__).resolve().parents[1]
        handlers = (
            root / "frontend" / "legacy" / "command_handlers.js"
        ).read_text(encoding="utf-8")
        self.assertGreater(len(handlers), 500)

    def test_preview_multiselect_and_custom_delete_dialog_are_wired(self):
        html = self.client.get("/").get_data(as_text=True)
        root = Path(__file__).resolve().parents[1]
        preview_js = (root / "frontend" / "legacy" / "preview.js").read_text(
            encoding="utf-8"
        )
        sessions_js = (root / "frontend" / "legacy" / "sessions.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('id="preview-use-table"', html)
        self.assertIn('id="ov-delete-session"', html)
        self.assertIn("selectedTables", preview_js)
        self.assertIn("confirmDeleteSavedSession", sessions_js)
        delete_fn = sessions_js.split("async function deleteSavedSession", 1)[1].split(
            "async function confirmDeleteSavedSession", 1
        )[0]
        self.assertNotIn("confirm(", delete_fn)

    def test_agent_activity_has_no_reasoning_to_tool_gap(self):
        root = Path(__file__).resolve().parents[1]
        stream_js = (root / "frontend" / "features" / "chat-stream.js").read_text(
            encoding="utf-8"
        )
        vue_js = (root / "frontend" / "features" / "ui" / "chat-ui.js").read_text(
            encoding="utf-8"
        )
        reasoning_handler = stream_js.split("function _onReasoning", 1)[1].split("function _onText", 1)[0]
        start_tool = vue_js.split("function startTool", 1)[1].split("function endTool", 1)[0]
        self.assertIn("_showToolActivity(ctx)", reasoning_handler)
        self.assertNotIn("_hideToolActivity(ctx)", reasoning_handler)
        self.assertIn('item.kind === "step"', start_tool)
        self.assertIn("hideToolActivity(target, { delayMs: 0 })", start_tool)

    def test_module_vue_app(self):
        self._check("/static/dist/chat-app.js", min_size=200000)

    def test_module_theme(self):
        self._check("/static/dist/chat-app.js", min_size=200000)

    def test_fe0_dialog_chart_and_dashboard_contracts(self):
        root = Path(__file__).resolve().parents[1]
        html = self.client.get("/").get_data(as_text=True)
        dashboard_html = self.client.get("/dashboard/frontend-contract").get_data(as_text=True)
        overlay_js = (root / "frontend" / "core" / "overlay.js").read_text(encoding="utf-8")
        dashboard_js = self.client.get("/static/js/dashboard.js").get_data(as_text=True)
        dashboard_css = self.client.get("/static/css/dashboard.css").get_data(as_text=True)
        vue_js = (root / "frontend" / "features" / "ui" / "chat-ui.js").read_text(
            encoding="utf-8"
        )
        chat_stream_js = (
            root / "frontend" / "features" / "chat-stream.js"
        ).read_text(encoding="utf-8")

        self.assertIn("js/theme_bootstrap.js", html)
        self.assertIn("js/theme_bootstrap.js", dashboard_html)
        self.assertIn('dialog.setAttribute("role", "dialog")', overlay_js)
        self.assertIn("layout.inert = overlayStack.length > 0", overlay_js)
        self.assertIn('event.key === "Escape"', overlay_js)
        # 图表 iframe 必须保留 allow-same-origin，否则浏览器把同源 iframe 降级
        # 为 opaque origin，Plotly 初始化时读取 computed style / storage 抛
        # SecurityError，导致聊天区图表空白（全屏新标签页无 sandbox 不受影响）。
        # 安全性由图表端点 CSP (connect-src 'none' + frame-ancestors 'self')
        # 兜底：iframe 内脚本无法外发数据，也无法被外部站点嵌入。
        # 禁止 allow-top-navigation —— 它才是能让 iframe 影响父窗口的危险 token。
        self.assertIn('sandbox="allow-scripts allow-same-origin"', dashboard_js)
        self.assertNotIn("allow-top-navigation", dashboard_js)
        self.assertIn('sandbox: "allow-scripts allow-same-origin"', vue_js)
        self.assertNotIn("allow-top-navigation", vue_js)
        self.assertIn(
            'iframe.setAttribute("sandbox", "allow-scripts allow-same-origin")',
            chat_stream_js,
        )
        self.assertNotIn("allow-top-navigation", chat_stream_js)
        self.assertIn("@media (max-width: 640px)", dashboard_css)
        self.assertIn("staticGrid: compactDashboard", dashboard_js)

    def test_chart_endpoint_iframe_security_contract(self):
        """图表端点必须用严格 CSP 兜底，iframe 无需 opaque-origin 隔离。

        回归：上一轮把图表 iframe sandbox 收紧为只有 allow-scripts，浏览器把
        同源 iframe 降级为 opaque origin，Plotly 初始化失败，聊天区图表空白。
        正确做法是保留 allow-same-origin（同源 iframe 本就同源，加它只是不降级），
        用图表端点 CSP 防止数据外泄与点击劫持。
        """
        from api.state import chart_store

        cid = "__smoke_chart_security__"
        chart_store[cid] = "<!doctype html><html><body>plot</body></html>"
        try:
            r = self.client.get(f"/api/chart/{cid}")
            self.assertEqual(r.status_code, 200)
            csp = r.headers.get("Content-Security-Policy", "")
            # iframe 内脚本无法外发数据，也无法被外部站点嵌入
            self.assertIn("connect-src 'none'", csp)
            self.assertIn("frame-ancestors 'self'", csp)
            # 不允许加载远程脚本，防止图表 HTML 注入后拉取外部 payload
            self.assertNotIn("https://", csp)
            self.assertEqual(r.headers.get("X-Content-Type-Options"), "nosniff")
            self.assertEqual(r.headers.get("Referrer-Policy"), "no-referrer")
        finally:
            (chart_store._dir / f"{cid}.html").unlink(missing_ok=True)


class TestIndexIntegrity(unittest.TestCase):
    """Every data-action in the rendered HTML must have a handler registered in app.js."""

    @classmethod
    def setUpClass(cls):
        cls.client = create_app().test_client()

    def test_actions_all_mapped(self):
        import re
        html = self.client.get("/").get_data(as_text=True)
        actions = set(re.findall(r'data-action="([^:"]+)', html))
        root = Path(__file__).resolve().parents[1]
        app_js = (root / "frontend" / "legacy" / "app.js").read_text(
            encoding="utf-8"
        )
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
