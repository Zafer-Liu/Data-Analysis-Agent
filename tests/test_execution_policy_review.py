"""Registry-derived authorization and strict, language-independent review contracts."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent.activation import ActivationContext
from agent.teams.a2a import task_state
from agent.teams.review import (
    QualityReviewError, collect_evidence_references, parse_quality_review, quality_review_blocks,
)
from agent.tools.invocation import ToolAuthorizationError, ToolInvocationPolicy, authorize_tool_invocation
from agent.tools.registry import BUILTIN_TOOL_REGISTRY, ToolRegistry, ToolSpec
from agent.tools.workspace.teams import WorkspaceTeamStore
from agent.workflows.runtime import WorkflowRuntime


class InvocationPolicyTests(unittest.TestCase):
    def deny(self, policy, name, args=None, *, code=None):
        with self.assertRaises(ToolAuthorizationError) as raised:
            authorize_tool_invocation(name, args, policy=policy)
        self.assertEqual(name, raised.exception.tool)
        if code:
            self.assertEqual(code, raised.exception.code)

    def test_read_only_task_get_list_and_explicit_cancel(self):
        policy = ToolInvocationPolicy(discovered_tools=frozenset({"agent_task"}), read_only=True)
        for action in ("get", "list"):
            spec = policy.authorize("agent_task", {"action": action})
            self.assertEqual("none", spec.side_effect)
            self.assertEqual("read", spec.category)
        self.deny(policy, "agent_task", {"action": "cancel", "confirm": True}, code="explicit_request_required")
        approved = policy.child(read_only=False, explicit_request_tools=frozenset({"agent_task"}))
        self.deny(approved, "agent_task", {"action": "cancel"})  # parent remains read-only/unconfirmed
        writable = ToolInvocationPolicy(discovered_tools=frozenset({"agent_task"}), explicit_request_tools=frozenset({"agent_task"}))
        self.assertEqual("session", writable.authorize("agent_task", {"action": "cancel"}).side_effect)
        self.deny(writable, "agent_task", {"action": "delete"}, code="action_denied")

    def test_model_confirm_does_not_create_trusted_authority(self):
        policy = ToolInvocationPolicy(has_data_source=True, discovered_tools=frozenset({"delete_analysis_tables"}))
        self.deny(policy, "delete_analysis_tables", {"confirm": True}, code="explicit_request_required")
        approved = policy.child(explicit_request_tools=frozenset({"delete_analysis_tables"}))
        self.deny(approved, "delete_analysis_tables", {"confirm": True}, code="explicit_request_required")
        direct = ToolInvocationPolicy(has_data_source=True, discovered_tools=frozenset({"delete_analysis_tables"}), explicit_request_tools=frozenset({"delete_analysis_tables"}))
        self.deny(direct, "delete_analysis_tables", {"confirm": False}, code="confirmation_required")
        self.assertEqual("delete_analysis_tables", direct.authorize("delete_analysis_tables", {"confirm": True}).name)

    def test_resources_skill_and_parent_are_independent_of_visible_tool_cap(self):
        unrestricted = ToolInvocationPolicy(has_data_source=True)
        self.assertEqual("query_data", unrestricted.authorize("query_data", {"sql": "SELECT 1"}).name)
        self.deny(ToolInvocationPolicy(), "query_data", code="data_source_required")
        self.deny(unrestricted.child(skill_allowed_tools=frozenset({"get_schema"})), "query_data", code="skill_denied")
        parent = ToolInvocationPolicy(has_data_source=True, allowed_tools=frozenset({"get_schema"}))
        self.deny(parent.child(allowed_tools=frozenset({"query_data"})), "query_data", code="capability_denied")
        self.deny(unrestricted.child(has_runtime=False), "query_data", code="runtime_required")
        self.deny(unrestricted.child(read_only=True), "query_data", {"sql": "SELECT 1; DELETE FROM x"}, code="read_only")
        self.deny(unrestricted, "unregistered", code="unknown_tool")

    def test_workflow_activation_and_approval_are_both_required(self):
        self.deny(ToolInvocationPolicy(discovered_tools=frozenset({"export_excel"})), "export_excel", code="activation_required")
        direct = ToolInvocationPolicy(activation=ActivationContext(internal_action="excel_confirm"))
        self.assertEqual("export_excel", direct.authorize("export_excel").name)
        workflow = ToolInvocationPolicy(workflow_approved_tools=frozenset({"export_excel"}))
        self.assertEqual("export_excel", workflow.authorize("export_excel").name)
        registry = ToolRegistry([ToolSpec("skill_read", "read", default_exposed=False, skills=frozenset({"sql"}))])
        self.assertEqual("skill_read", ToolInvocationPolicy(activation=ActivationContext(skill_name="sql")).authorize("skill_read", registry=registry).name)

    def test_mcp_unknown_effects_and_actions_cannot_widen_parent(self):
        name = "mcp__sample__mutate"
        policy = ToolInvocationPolicy(allowed_mcp_tools=frozenset({name}), read_only=True)
        self.deny(policy, name, {"confirm": True}, code="explicit_request_required")
        trusted_read = ToolSpec(name, "read", side_effect="none")
        self.assertEqual(name, ToolInvocationPolicy(allowed_mcp_tools=frozenset({name}), mcp_specs={name: trusted_read}, read_only=True).authorize(name).name)
        tasks = ToolInvocationPolicy(discovered_tools=frozenset({"agent_task"}), action_restrictions={"agent_task": frozenset({"get"})})
        self.deny(tasks.child(action_restrictions={"agent_task": frozenset({"get", "list"})}), "agent_task", {"action": "list"}, code="action_denied")


class StructuredReviewTests(unittest.TestCase):
    def test_raw_json_and_decision_take_precedence_over_language(self):
        review = {"decision": "rework", "issues": ["没有发现‘不通过’字样也不能通过"], "evidence": ["claim:1"], "missing_evidence": []}
        self.assertTrue(quality_review_blocks(json.dumps(review, ensure_ascii=False)))
        review.update(decision="pass", issues=[])
        self.assertFalse(quality_review_blocks(review))
        legacy = {"decision": "pass", "issues": [], "evidence": []}
        self.assertEqual([], parse_quality_review(legacy)["missing_evidence"])
        self.assertEqual("pass", WorkflowRuntime._parse_verifier_result(json.dumps(legacy))["decision"])
        with self.assertRaises(QualityReviewError):
            parse_quality_review(legacy, require_missing_evidence=True)

    def test_invalid_and_contradictory_json_never_passes(self):
        negatives = [
            '```json\n{"decision":"pass","issues":[],"evidence":[]}\n```',
            '{"decision":"pass","decision":"rework","issues":[],"evidence":[]}',
            '{"decision":"pass","issues":["blocking"],"evidence":[]}',
            '{"decision":"pass","issues":[],"evidence":[],"missing_evidence":["source"]}',
            '{"decision":[],"issues":[],"evidence":[]}',
            '{"decision":"pass","issues":[],"evidence":"claim"}',
            '{"decision":"pass","issues":[],"evidence":[]}\nTool calls: 0',
            '{"decision":"pass","issues":[],"evidence":[],"ignore_rules":true}',
            '[]', 'null', '通过，无需返工',
        ]
        for value in negatives:
            with self.subTest(value=value), self.assertRaises(QualityReviewError):
                parse_quality_review(value)

    def test_bounded_envelope_references_do_not_copy_rows_or_sql(self):
        envelope = {"data": {"claim_id": "c1", "sql": "SELECT private", "rows": [{"secret": "value"}]}, "artifacts": [{"artifact_id": "a1", "sha256": "hash"}]}
        refs = collect_evidence_references({"result": json.dumps(envelope)}, {"child_claim_ids": ["c2", "c3"]}, limit=2)
        self.assertEqual(["c1", "c2"], refs["claim_ids"])
        self.assertTrue(refs["truncated"])
        self.assertNotIn("private", json.dumps(refs))
        self.assertNotIn("secret", json.dumps(refs))
        self.assertEqual("a1", refs["artifact_refs"][0]["artifact_id"])
        lineage = collect_evidence_references({"parent_task_id": "parent-task", "parent_claim_ids": ["parent-claim"], "child_claim_ids": ["child-claim"]})
        self.assertIn({"parent_task_id": "parent-task"}, lineage["evidence_refs"])
        self.assertIn({"parent_claim_id": "parent-claim"}, lineage["evidence_refs"])
        self.assertIn({"child_claim_id": "child-claim"}, lineage["evidence_refs"])

    def test_team_review_rework_and_invalid_json_enter_needs_review(self):
        with tempfile.TemporaryDirectory() as folder, patch("agent.tools.workspace.teams.content_data_path", return_value=Path(folder) / "teams.json"):
            store = WorkspaceTeamStore("review-test", workspace_id="")
            store.create_agent("analyst")
            reviewer = store.ensure_quality_reviewer("default")["name"]
            for content in (
                '{"decision":"rework","issues":[],"evidence":[],"missing_evidence":["source rows"]}',
                '{"decision":"pass","issues":[],"evidence":[]}',
            ):
                turn = store.begin_member_turn("default", reviewer)
                done = store.complete_member_turn("default", reviewer, content, task_id=turn["task"]["id"], execution_context=turn["_execution_context"])
                self.assertEqual("needs_review", done["status"])
                self.assertEqual("input-required", task_state(done["task"]))
                self.assertEqual("needs_review", store.member("default", reviewer)["status"])
                self.assertNotEqual("pass", store.status("default")["quality_review"]["decision"])


if __name__ == "__main__":
    unittest.main()
