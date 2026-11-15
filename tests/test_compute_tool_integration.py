"""Tool authorization, remote planning and dialect-aware application routing."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import pandas as pd

from agent.tools.business.data import DataToolsMixin
from agent.validate import validate_sql_structured, validate_tool_args
from agent.tools.registry import BUILTIN_TOOL_REGISTRY
from agent.tools.schemas import AGENT_TOOLS
from agent.tools.job_migration import validate_job_migration_plan


def finish(generator):
    while True:
        try:
            next(generator)
        except StopIteration as result:
            return result.value


class Agent(DataToolsMixin):
    def __init__(self, source):
        self.data_source = source
        self._all_sources = [source]
        self._job_runner = None


class ComputeToolIntegrationTests(unittest.TestCase):
    def test_target_dialects_are_strict_read_only(self):
        for dialect in ("trino", "hive", "spark"):
            self.assertEqual("passed", validate_sql_structured("SELECT COUNT(*) FROM orders", dialect=dialect, strict=True).status)
            for sql in ("DELETE FROM orders", "CREATE TABLE x AS SELECT 1", "SELECT 1; SELECT 2"):
                with self.subTest(dialect=dialect, sql=sql):
                    self.assertEqual("failed", validate_sql_structured(sql, dialect=dialect, strict=True).status)
        self.assertEqual("failed", validate_sql_structured("SELECT * FROM (", dialect="trino", strict=True).status)
        self.assertIsNotNone(validate_tool_args("query_data", {"sql": "DELETE FROM orders"}, sql_dialect="hive"))

    def test_cluster_source_validator_runs_before_execution(self):
        source = SimpleNamespace(name="cluster", query_dialect="trino", list_tables=lambda: ["orders"],
                                 validate_query=Mock(side_effect=ValueError("permission denied: table outside scope")),
                                 execute_query=Mock())
        agent = Agent(source)
        self.assertEqual("trino", agent._query_dialect_for_sql("SELECT * FROM orders"))
        text, refs = agent._tool_query_data_with_refs("SELECT * FROM orders")
        self.assertIn("permission denied", text)
        source.execute_query.assert_not_called()
        self.assertEqual("cluster", refs[0]["source"])

    def test_budget_failure_cannot_silently_select_another_source(self):
        first = SimpleNamespace(name="first", list_tables=lambda: ["orders"],
                                execute_query=Mock(return_value=(pd.DataFrame(), "budget_exceeded: result bytes")))
        second = SimpleNamespace(name="second", list_tables=lambda: ["orders"], execute_query=Mock())
        agent = Agent(first)
        agent._all_sources = [first, second]
        text, _refs = agent._tool_query_data_with_refs("SELECT * FROM orders")
        self.assertIn("budget_exceeded", text)
        second.execute_query.assert_not_called()

    def test_training_preparation_does_not_approve_or_submit(self):
        service = SimpleNamespace(plan=Mock(return_value={"plan_id": "p", "approved": False}), approve=Mock(), submit=Mock())
        agent = Agent(None)
        agent._training_service = lambda: (service, {"owner_id": "u", "session_id": "s", "workspace_id": ""})
        result = json.loads(agent._tool_prepare_training({"dataset_id": "d", "analysis_name": "Torch_MLP"}))
        self.assertFalse(result["submitted"])
        service.approve.assert_not_called()
        service.submit.assert_not_called()

    def test_remote_dispatch_requires_grant_and_does_not_read_the_source(self):
        source = SimpleNamespace(execute_query=Mock())
        service = SimpleNamespace(submit=Mock(return_value={"id": "remote-run", "status": "queued"}))
        agent = Agent(source)
        agent._training_service = lambda: (service, {"owner_id": "u", "session_id": "s", "workspace_id": ""})
        with self.assertRaises(PermissionError):
            finish(agent._tool_run_analysis_with_refs("Torch_MLP", "SELECT * FROM orders", "target", analysis_options={"execution_target": "remote"}))
        result, refs = finish(agent._tool_run_analysis_with_refs(
            "Torch_MLP", "SELECT * FROM orders", "target", analysis_options={
                "execution_target": "remote", "approved_plan_id": "p", "grant_id": "g", "idempotency_key": "once"}))
        self.assertFalse(json.loads(result)["complete"])
        self.assertEqual([], refs)
        source.execute_query.assert_not_called()
        service.submit.assert_called_once()

    def test_all_explicit_remote_targets_require_approval_before_source_reads(self):
        source = SimpleNamespace(execute_query=Mock())
        for target in ("remote", "remote(connection-id)", {"kind": "remote", "connection_id": "connection-id"}):
            with self.subTest(target=target):
                agent = Agent(source)
                with self.assertRaises(PermissionError):
                    agent._tool_run_analysis("Torch_MLP", "SELECT * FROM orders", "target", analysis_options={"execution_target": target})
                with self.assertRaises(PermissionError):
                    finish(agent._tool_run_analysis_with_refs("Torch_MLP", "SELECT * FROM orders", "target", analysis_options={"execution_target": target}))
        source.execute_query.assert_not_called()

    def test_new_training_tool_is_registered_and_only_prepares(self):
        names = {schema["function"]["name"] for schema in AGENT_TOOLS}
        self.assertIn("prepare_training", names)
        spec = BUILTIN_TOOL_REGISTRY.get("prepare_training")
        self.assertFalse(spec.default_exposed)
        self.assertEqual("none", spec.side_effect)
        validate_job_migration_plan()


if __name__ == "__main__":
    unittest.main()
