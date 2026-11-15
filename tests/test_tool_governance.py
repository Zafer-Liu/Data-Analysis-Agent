import ast
import inspect
import textwrap
import unittest
from unittest.mock import Mock

from agent.agent import BusinessAgent
from agent.tools.exposure import (
    MAX_VISIBLE_TOOLS,
    discover_tool_names_for_query,
    filter_tools_for_turn,
    limit_tools_for_turn,
)
from agent.tools.job_migration import (
    validate_job_migration_plan,
    validate_job_regression_matrix,
)
from agent.tools.parallel import is_parallel_safe_tool
from agent.tools.registry import BUILTIN_TOOL_REGISTRY
from agent.tools.schemas import AGENT_TOOLS
from agent.workflow_stage import WorkflowStageContext, filter_tools_for_stage
from data.session import ChatSession


def _names(schemas):
    return [schema["function"]["name"] for schema in schemas]


class ToolDiscoveryGovernanceTests(unittest.TestCase):
    def test_discovered_tools_expire_after_three_turns(self):
        session = ChatSession()
        session.record_discovered_tools("读取工作区说明文件")
        self.assertIn("workspace_read_file", session.discovered_tools)

        session.record_discovered_tools("")
        session.record_discovered_tools("")
        self.assertIn("workspace_read_file", session.discovered_tools)

        session.record_discovered_tools("")
        self.assertNotIn("workspace_read_file", session.discovered_tools)
        self.assertEqual(3, len(session.discovered_tool_turns))

    def test_legacy_discovery_state_restores_into_bounded_window(self):
        session = ChatSession()
        state = session.capture_rewind_state()
        state.pop("discovered_tool_turns", None)
        state["discovered_tools"] = [f"legacy_{index}" for index in range(30)]
        session.restore_rewind_state(state)
        self.assertEqual(20, len(session.discovered_tools))
        self.assertEqual([session.discovered_tools], session.discovered_tool_turns)

    def test_visible_tools_have_hard_cap_and_keep_current_intent(self):
        discovered = [
            spec.name for spec in BUILTIN_TOOL_REGISTRY.all() if spec.discoverable
        ]
        exposed = filter_tools_for_turn(
            AGENT_TOOLS,
            user_message="创建团队并分析数据",
            discovered_tools=discovered,
            has_data_source=True,
            has_workspace=True,
            teams_enabled=True,
        )
        limited = limit_tools_for_turn(
            exposed,
            user_message="创建团队并分析数据",
            recent_discovered_tools=discovered,
        )
        self.assertLessEqual(len(limited), MAX_VISIBLE_TOOLS)
        self.assertIn("team_create", _names(limited))
        self.assertIn("ask_user", _names(limited))
        self.assertIn("get_schema", _names(limited))
        self.assertIn("query_data", _names(limited))

    def test_cap_counts_mcp_tools(self):
        fake_mcp = [
            {
                "type": "function",
                "function": {
                    "name": f"mcp__demo__tool_{index}",
                    "description": "demo",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
            for index in range(8)
        ]
        limited = limit_tools_for_turn(
            AGENT_TOOLS[:18] + fake_mcp,
            recent_discovered_tools=[],
        )
        self.assertEqual(MAX_VISIBLE_TOOLS, len(limited))
        self.assertEqual(8, sum(name.startswith("mcp__") for name in _names(limited)))

    def test_common_intents_do_not_cross_capability_families(self):
        chart = discover_tool_names_for_query("生成销售图表")
        self.assertFalse(chart.intersection({
            "display_diagram", "edit_diagram", "get_diagram",
        }))

        workspace = discover_tool_names_for_query("搜索工作区文件里的错误")
        self.assertIn("workspace_grep", workspace)
        self.assertNotIn("browser_search", workspace)

        webpage = discover_tool_names_for_query("打开网页 https://example.com")
        self.assertIn("browser_navigate", webpage)
        self.assertNotIn("workspace_read_file", webpage)
        docs = discover_tool_names_for_query("读取 API 文档 https://example.com/docs")
        self.assertIn("browse_webpage", docs)
        self.assertNotIn("browser_navigate", docs)

    def test_crud_intent_exposes_requested_action(self):
        self.assertEqual(
            {"team_status"},
            discover_tool_names_for_query("查看团队状态").intersection({
                "team_create", "team_delete", "team_list", "team_status",
            }),
        )
        self.assertEqual(
            {"workflow_create"},
            discover_tool_names_for_query("创建工作流").intersection({
                "workflow_create", "workflow_create_custom", "workflow_list",
                "workflow_start", "workflow_status",
            }),
        )
        self.assertEqual(
            {"load_feishu_bitable"},
            discover_tool_names_for_query("读取飞书多维表格").intersection({
                "create_feishu_bitable", "list_feishu_bitable_tables",
                "load_feishu_bitable", "append_feishu_bitable_records",
                "update_feishu_bitable_record",
            }),
        )

    def test_discovered_utility_survives_data_inspection_stage(self):
        exposed = filter_tools_for_turn(
            AGENT_TOOLS,
            user_message="修改工作区文件",
            has_data_source=True,
            has_workspace=True,
        )
        staged = filter_tools_for_stage(
            exposed, WorkflowStageContext("inspect"),
        )
        self.assertIn("workspace_edit_file", _names(staged))


class ToolRegistryGovernanceTests(unittest.TestCase):
    def test_registry_schema_and_job_policies_are_complete(self):
        BUILTIN_TOOL_REGISTRY.validate_schema_names(AGENT_TOOLS)
        validate_job_migration_plan()
        validate_job_regression_matrix()
        for spec in BUILTIN_TOOL_REGISTRY.all():
            if spec.side_effect == "external":
                self.assertNotEqual("none", spec.confirmation, spec.name)
            if spec.concurrency_safe:
                self.assertEqual("none", spec.side_effect, spec.name)

    def test_superseded_model_tools_are_internal_only(self):
        names = set(BUILTIN_TOOL_REGISTRY.names())
        schema_names = set(_names(AGENT_TOOLS))
        self.assertNotIn("workspace_command", names)
        self.assertNotIn("workspace_command", schema_names)
        self.assertNotIn("get_shape_library", names)
        self.assertNotIn("get_shape_library", schema_names)

    def test_workspace_bash_absorbs_json_validation(self):
        from agent.tools.workspace.bash import WorkspaceBashService

        spec = BUILTIN_TOOL_REGISTRY.get("workspace_bash")
        self.assertIsNotNone(spec)
        self.assertIn("json_validate", spec.discovery_keywords)
        discovered = discover_tool_names_for_query("在工作区运行 json_validate 检查配置文件")
        self.assertIn("workspace_bash", discovered)
        self.assertNotIn("workspace_command", discovered)

        service = WorkspaceBashService.__new__(WorkspaceBashService)
        service.session_id = "test"
        service.files = Mock()
        service.files.command.return_value = {"operation": "json_validate", "valid": True}
        result = service.execute("json validate config.json")
        service.files.command.assert_called_once_with("json_validate", "config.json", timeout=30)
        self.assertTrue(result["output"]["valid"])

    def test_shape_library_is_resolved_internally(self):
        from agent.tools.business.diagram import _inspect_shape_libraries

        result = _inspect_shape_libraries(
            '<mxCell style="shape=mxgraph.basic.star;" vertex="1" />'
        )
        self.assertTrue(result["ok"])
        self.assertEqual(["basic"], result["libraries"])

    def test_mcp_tools_are_sequential_by_default(self):
        self.assertFalse(is_parallel_safe_tool("mcp__demo__external_write"))

    def test_every_registered_tool_has_a_main_dispatch_branch(self):
        tree = ast.parse(textwrap.dedent(inspect.getsource(BusinessAgent.run)))
        handled = set()
        prefixes = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Compare)
                and isinstance(node.left, ast.Name)
                and node.left.id == "name"
            ):
                for comparator in node.comparators:
                    handled.update(
                        child.value
                        for child in ast.walk(comparator)
                        if isinstance(child, ast.Constant)
                        and isinstance(child.value, str)
                    )
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "startswith"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "name"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                prefixes.add(node.args[0].value)
        handled.update(
            spec.name
            for spec in BUILTIN_TOOL_REGISTRY.all()
            if any(spec.name.startswith(prefix) for prefix in prefixes)
        )
        self.assertEqual(set(), set(BUILTIN_TOOL_REGISTRY.names()) - handled)

    def test_teams_mode_does_not_expose_all_team_crud_tools(self):
        visible = _names(filter_tools_for_turn(
            AGENT_TOOLS,
            user_message="你好",
            has_workspace=True,
            teams_enabled=True,
        ))
        self.assertIn("team_delegate", visible)
        self.assertNotIn("team_create", visible)
        self.assertNotIn("team_delete", visible)
        self.assertNotIn("send_message", visible)


class SqlValidationStructuredTests(unittest.TestCase):
    """Verify the A6 three-state SQL validation: writes allowed, memory-only."""

    def test_select_query_passes(self):
        from agent.validate import validate_sql_structured
        result = validate_sql_structured("SELECT a, b FROM orders WHERE a > 1")
        self.assertEqual(result.status, "passed")
        self.assertFalse(result.used_heuristic)

    def test_write_statements_pass(self):
        """A6: data-mutating SQL is allowed (executes against in-memory cache)."""
        from agent.validate import validate_sql_structured
        for sql in (
            "DROP TABLE orders",
            "CREATE TABLE foo (i INT)",
            "CREATE OR REPLACE TABLE foo AS SELECT 1 AS x",
            "CREATE VIEW v AS SELECT 1",
            "INSERT INTO t VALUES (1)",
            "UPDATE t SET a = 1 WHERE b > 2",
            "DELETE FROM t WHERE a IS NULL",
            "ALTER TABLE t ADD COLUMN x INT",
            "TRUNCATE TABLE t",
        ):
            result = validate_sql_structured(sql)
            self.assertEqual("passed", result.status, sql)
            self.assertFalse(result.used_heuristic, sql)

    def test_system_statements_fail(self):
        """A6: system-level statements stay blocked even though writes pass."""
        from agent.validate import validate_sql_structured
        for sql in (
            "PRAGMA memory_limit='1GB'",
            "INSTALL httpfs",
            "LOAD httpfs",
            "ATTACH 'x.db' AS y",
            "SET memory_limit='1GB'",
            "BEGIN",
            "CREATE FUNCTION f() AS SELECT 1",
            "CREATE INDEX idx ON t(i)",
            "CREATE SCHEMA s",
            "DROP INDEX idx",
            "EXPLAIN SELECT 1",
        ):
            result = validate_sql_structured(sql)
            self.assertEqual("failed", result.status, sql)
            self.assertTrue(result.message, sql)

    def test_copy_to_file_fails(self):
        from agent.validate import validate_sql_structured
        result = validate_sql_structured("COPY orders TO 'out.csv' (FORMAT CSV)")
        self.assertEqual("failed", result.status)

    def test_multi_statement_fails(self):
        from agent.validate import validate_sql_structured
        result = validate_sql_structured("SELECT 1; DROP TABLE orders")
        self.assertEqual(result.status, "failed")
        self.assertIn("多语句", result.message)

    def test_banned_function_fails(self):
        from agent.validate import validate_sql_structured
        result = validate_sql_structured("SELECT * FROM read_csv('/etc/passwd')")
        self.assertEqual(result.status, "failed")

    def test_write_with_network_url_fails(self):
        from agent.validate import validate_sql_structured
        result = validate_sql_structured(
            "CREATE TABLE u AS SELECT * FROM read_csv('http://evil.example.com/x.csv')"
        )
        self.assertEqual("failed", result.status)

    def test_validate_tool_args_backward_compat(self):
        """validate_tool_args must still return an error string or None."""
        from agent.validate import validate_tool_args
        self.assertIsNone(validate_tool_args("query_data", {"sql": "SELECT 1"}))
        self.assertIsNone(validate_tool_args("query_data", {"sql": "DROP TABLE t"}))
        err = validate_tool_args("query_data", {"sql": "PRAGMA threads=2"})
        self.assertIsInstance(err, str)
        self.assertIn("validation failed", err)

    def test_sqlglot_unavailable_degrades_with_code(self):
        """sqlglot missing → degraded + SQLG-001 code in the message."""
        from unittest.mock import patch
        import agent.validate as v

        with patch.dict("sys.modules", {"sqlglot": None}):
            result = v.validate_sql_structured("SELECT 1")
        self.assertEqual("degraded", result.status)
        self.assertTrue(result.used_heuristic)
        self.assertEqual("SQLG-001", result.code)
        self.assertIn("SQLG-001", result.message)

    def test_sqlglot_parse_failure_degrades_with_code(self):
        """Unparseable SQL → heuristic + degraded + SQLG-002 code."""
        from unittest.mock import patch
        import agent.validate as v

        with patch.object(v, "_validate_sql_heuristic", return_value=None), \
                patch("sqlglot.parse", side_effect=ValueError("boom")):
            result = v.validate_sql_structured("SELECT 1")
        self.assertEqual("degraded", result.status)
        self.assertEqual("SQLG-002", result.code)
        self.assertIn("SQLG-002", result.message)

    def test_infer_sql_purpose_bilingual(self):
        """Timeline purpose labels follow the UI language (A7)."""
        from agent.agent import _infer_sql_purpose
        self.assertEqual("查询", _infer_sql_purpose("SELECT 1", lang="zh"))
        self.assertIn("聚合统计", _infer_sql_purpose(
            "SELECT a, COUNT(*) FROM t GROUP BY a", lang="zh"))
        self.assertIn("aggregate", _infer_sql_purpose(
            "SELECT a, COUNT(*) FROM t GROUP BY a", lang="en"))
        self.assertIn("join", _infer_sql_purpose(
            "SELECT * FROM a JOIN b ON a.i = b.i", lang="en"))
        self.assertIn("insert", _infer_sql_purpose(
            "INSERT INTO t VALUES (1)", lang="en"))
        self.assertIn("t", _infer_sql_purpose("DELETE FROM t", lang="en"))

    def test_purpose_param_present_on_sql_tools(self):
        """All SQL_TOOLS must expose the optional purpose parameter."""
        from agent.validate import SQL_TOOLS
        for schema in AGENT_TOOLS:
            fn = schema.get("function", {})
            if fn.get("name") in SQL_TOOLS:
                props = fn["parameters"]["properties"]
                self.assertIn("purpose", props, f"{fn['name']} missing purpose param")


if __name__ == "__main__":
    unittest.main()
