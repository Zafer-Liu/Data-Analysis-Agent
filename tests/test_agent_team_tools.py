import unittest

from agent.prompts import PromptContext, SUBAGENT_RULES, TEAMS_RULES, get_system_prompt
from agent.tools.exposure import (
    MAX_VISIBLE_TOOLS,
    discover_tool_names_for_query,
    filter_tools_for_turn,
    limit_tools_for_turn,
)
from agent.tools.job_migration import (
    TOOL_JOB_MIGRATION_PLAN,
    validate_job_migration_plan,
    validate_job_regression_matrix,
)
from agent.tools.parallel import is_parallel_safe_tool
from agent.tools.registry import BUILTIN_TOOL_REGISTRY
from agent.tools.schemas import AGENT_TOOLS
from agent.workflow_stage import WorkflowStageContext, filter_tools_for_stage


def _names(schemas):
    return [schema["function"]["name"] for schema in schemas]


class AgentTeamToolGovernanceTests(unittest.TestCase):
    def test_core_subagents_need_no_workspace_or_teams_preference(self):
        core = {"agent_create", "agent_delegate"}
        deferred = {
            "agent_task", "team_create", "team_delete", "team_list", "team_status",
            "send_message", "team_plan_create", "team_delegate",
        }
        for has_workspace in (False, True):
            for teams_enabled in (False, True):
                with self.subTest(workspace=has_workspace, teams=teams_enabled):
                    visible = set(_names(filter_tools_for_turn(
                        AGENT_TOOLS,
                        user_message="你好",
                        has_workspace=has_workspace,
                        teams_enabled=teams_enabled,
                    )))
                    self.assertTrue(core <= visible)
                    coordination = {"team_status", "team_delegate"} if teams_enabled else set()
                    self.assertEqual(coordination, visible & deferred)

    def test_team_discovery_works_without_workspace_or_teams_preference(self):
        core = {"agent_create", "agent_delegate"}
        family = core | {
            "agent_task", "team_create", "team_delete", "team_list", "team_status",
            "send_message", "team_plan_create", "team_delegate",
        }
        for message, expected in (
            ("创建子Agent", {"agent_create"}),
            ("创建团队", {"team_create"}),
            ("创建团队，暂不执行", {"team_create"}),
            ("删除团队", {"team_delete", "team_status"}),
            ("查看团队状态", {"team_status"}),
            ("列出团队", {"team_list"}),
            ("发送团队消息", {"send_message"}),
            ("并行团队分析", {"team_delegate"}),
            ("创建团队计划", {"team_plan_create"}),
            ("查看子 Agent 任务", {"agent_task"}),
            ("取消 A2A Task", {"agent_task"}),
            ("agent_task", {"agent_task"}),
        ):
            with self.subTest(message=message):
                discovered = discover_tool_names_for_query(message)
                visible = set(_names(filter_tools_for_turn(
                    AGENT_TOOLS, user_message=message,
                )))
                self.assertEqual(expected, discovered & family)
                self.assertEqual(core | expected, visible & family)
                if "agent_task" in expected:
                    self.assertFalse(discovered & {
                        "task_create", "task_get", "task_list", "task_update",
                    })

    def test_subagent_lifecycle_survives_stages_and_respects_skill_allowlist(self):
        lifecycle = {"agent_create", "agent_delegate", "agent_task"}
        exposed = filter_tools_for_turn(AGENT_TOOLS, discovered_tools=["agent_task"])
        for stage in ("inspect", "analyze", "visualize", "propose_output", "generate_output"):
            with self.subTest(stage=stage):
                staged = filter_tools_for_stage(exposed, WorkflowStageContext(stage))
                self.assertTrue(lifecycle <= set(_names(staged)))
        restricted = filter_tools_for_turn(
            AGENT_TOOLS,
            discovered_tools=list(lifecycle),
            teams_enabled=True,
            skill_allowed_tools=frozenset({"ask_user"}),
        )
        self.assertEqual(["ask_user"], _names(restricted))

    def test_cap_keeps_core_subagents_and_current_intent_alongside_mcp(self):
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
        discovered = [
            spec.name for spec in BUILTIN_TOOL_REGISTRY.all() if spec.discoverable
        ]
        message = "创建团队并分析数据"
        exposed = filter_tools_for_turn(
            AGENT_TOOLS + fake_mcp,
            user_message=message,
            discovered_tools=discovered,
            has_data_source=True,
            allowed_mcp_tools=frozenset(_names(fake_mcp)),
        )
        limited = limit_tools_for_turn(
            exposed,
            user_message=message,
            recent_discovered_tools=discovered[-20:],
        )
        visible = set(_names(limited))
        self.assertEqual(MAX_VISIBLE_TOOLS, len(limited))
        self.assertEqual(8, sum(name.startswith("mcp__") for name in visible))
        self.assertTrue({
            "agent_create", "agent_delegate", "team_create", "ask_user",
            "get_schema", "query_data",
        } <= visible)

    def test_subagent_registry_policies_and_migration(self):
        BUILTIN_TOOL_REGISTRY.validate_schema_names(AGENT_TOOLS)
        validate_job_migration_plan()
        validate_job_regression_matrix()
        for name in (
            "agent_create", "agent_delegate", "agent_task", "team_create",
            "team_delete", "team_list", "team_status", "send_message",
            "team_plan_create", "team_delegate",
        ):
            with self.subTest(tool=name):
                spec = BUILTIN_TOOL_REGISTRY.get(name)
                self.assertIsNotNone(spec)
                self.assertTrue(spec.requires_runtime)
                self.assertFalse(spec.requires_workspace)
                self.assertFalse(spec.requires_data_source)
                if spec.category in {"write", "analysis"}:
                    self.assertEqual("session", spec.side_effect)
        for name, category in (("agent_create", "write"), ("agent_delegate", "analysis")):
            spec = BUILTIN_TOOL_REGISTRY.get(name)
            self.assertTrue(spec.default_exposed)
            self.assertEqual(category, spec.category)
            self.assertEqual("none", spec.confirmation)
        task = BUILTIN_TOOL_REGISTRY.get("agent_task")
        self.assertFalse(task.default_exposed)
        self.assertFalse(task.concurrency_safe)
        self.assertFalse(is_parallel_safe_tool("agent_task"))
        for name in ("agent_create", "agent_task"):
            self.assertEqual("keep_sync", TOOL_JOB_MIGRATION_PLAN[name].status)

    def test_subagent_schema_contracts(self):
        functions = {schema["function"]["name"]: schema["function"] for schema in AGENT_TOOLS}
        profiles = {
            "inherit", "data-scout", "sql-analyst", "knowledge-researcher",
            "quality-reviewer", "report-writer",
        }
        for name, required in (("agent_create", ["name"]), ("agent_delegate", ["prompt"])):
            with self.subTest(tool=name):
                params = functions[name]["parameters"]
                props = params["properties"]
                self.assertEqual(required, params["required"])
                self.assertEqual("default", props["team_name"]["default"])
                self.assertIn("role", props)
                self.assertIn("instructions", props)
                self.assertEqual(profiles, set(props["profile"]["enum"]))
        delegate = functions["agent_delegate"]["parameters"]["properties"]
        self.assertEqual("analyst", delegate["member_name"]["default"])
        self.assertIn("description", delegate)
        task = functions["agent_task"]["parameters"]
        self.assertEqual(["team_name", "task_id"], task["required"])
        self.assertEqual(["get", "cancel"], task["properties"]["action"]["enum"])
        self.assertEqual("get", task["properties"]["action"]["default"])
        member = functions["team_create"]["parameters"]["properties"]["members"]["items"]
        self.assertEqual(profiles, set(member["properties"]["profile"]["enum"]))
        self.assertEqual(["name"], member["required"])

    def test_subagent_rules_are_always_present_with_optional_team_preference(self):
        for teams_enabled in (False, True):
            with self.subTest(teams=teams_enabled):
                prompt = get_system_prompt(PromptContext(teams_enabled=teams_enabled))
                self.assertIn(SUBAGENT_RULES.strip(), prompt)
                self.assertEqual(teams_enabled, TEAMS_RULES.strip() in prompt)
                if teams_enabled:
                    self.assertLess(
                        prompt.index(SUBAGENT_RULES.strip()),
                        prompt.index(TEAMS_RULES.strip()),
                    )


if __name__ == "__main__":
    unittest.main()
