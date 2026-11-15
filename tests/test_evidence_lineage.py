"""Offline evidence replay, integrity and scope regressions using synthetic data."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent.tools.results import (
    ToolResultAccessError, load_tool_result_artifact, make_tool_result,
    persist_evidence_claim, persist_large_tool_result, read_evidence_claim,
    read_tool_result_artifact,
)


class EvidenceLineageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = SimpleNamespace(cache_dir=self.root, workspace_id="synthetic-workspace")

    def read(self, grant, **kwargs):
        return read_evidence_claim(grant["claim_id"], allowed_artifacts=[grant], session_id="session-a",
                                   workspace_id=self.runtime.workspace_id, runtime=self.runtime, **kwargs)

    def record(self, grant):
        # The test reads its own isolated storage, never an application data directory.
        return json.loads((self.root / "evidence_claims" / f"{grant['claim_id']}.json").read_text(encoding="utf-8"))

    def test_full_sql_result_and_used_semantics_survive_bounded_replay(self):
        sql = "SELECT 1 /*" + "synthetic-sql " * 2200 + "*/"
        raw = "refund_rate=12.5%;\n" + "synthetic-row\n" * 3000 + "tail-result-proof"
        semantics = {"state": "ready", "definitions": [{"revision_id": "fixed-revision", "path": "metrics.refund_rate"}]}
        args = {"sql": sql, "calculation": {"numerator": 5, "denominator": 40, "unit": "percent"}}
        envelope = make_tool_result("query_data", raw, session_id="session-a", runtime=self.runtime,
                                    args=args, semantic_context=semantics,
                                    execution_context={"run_id": "run-synthetic", "job_id": "job-synthetic"})
        grant = next(item for item in envelope.artifacts if item["type"] == "evidence_claim")
        record = self.record(grant)
        self.assertEqual(record["version"], 2)
        self.assertEqual(record["sql"], sql)
        self.assertEqual(record["args"]["calculation"]["denominator"], 40)
        self.assertEqual(record["semantic_context"], semantics)
        self.assertEqual(record["execution_context"]["run_id"], "run-synthetic")
        semantics["definitions"][0]["revision_id"] = "mutated-after-persistence"
        self.assertEqual(self.record(grant)["semantic_context"]["definitions"][0]["revision_id"], "fixed-revision")
        for section, expected in (("sql", sql), ("result", raw)):
            parts, cursor = [], 0
            while True:
                page = self.read(grant, section=section, offset=cursor, limit=999999)
                self.assertLessEqual(page["returned_chars"], 4000)
                parts.append(page["content"])
                if page["next_offset"] is None:
                    break
                cursor = page["next_offset"]
            self.assertEqual("".join(parts), expected)
        self.assertLessEqual(self.read(grant)["returned_chars"], 4000)
        self.assertLess(len(str(envelope.data)), len(raw))

    def test_small_results_are_replayable_and_unsupported_tool_without_parents_is_skipped(self):
        grant = persist_evidence_claim("session-a", "query_data", "7", runtime=self.runtime)
        self.assertEqual(self.read(grant, section="result")["content"], "7")
        self.assertIsNone(persist_evidence_claim("session-a", "get_schema", "columns", runtime=self.runtime))

    def test_claim_and_artifact_require_matching_session_workspace_and_explicit_grant(self):
        grant = persist_evidence_claim("session-a", "query_data", "secret-synthetic", runtime=self.runtime)
        record = self.record(grant)
        artifact = record["result_artifact"]
        for session, workspace in (("session-b", self.runtime.workspace_id), ("session-a", "other"), ("session-a", "")):
            with self.assertRaises(ToolResultAccessError):
                read_evidence_claim(grant["claim_id"], allowed_artifacts=[grant], session_id=session,
                                    workspace_id=workspace, runtime=self.runtime)
            with self.assertRaises(ToolResultAccessError):
                read_tool_result_artifact(artifact["artifact_id"], allowed_artifacts=[artifact], session_id=session,
                                          workspace_id=workspace, runtime=self.runtime)
        with self.assertRaises(ToolResultAccessError):
            read_evidence_claim(grant["claim_id"], allowed_artifacts=[], session_id="session-a",
                                workspace_id=self.runtime.workspace_id, runtime=self.runtime)
        with self.assertRaises(ToolResultAccessError):
            self.read({**grant, "claim_id": "../outside"})

    def test_deduplication_is_scoped_and_keeps_same_scope_reuse(self):
        raw = "synthetic-schema" * 1000
        _, first, _ = persist_large_tool_result("session-a", "get_schema", raw, runtime=self.runtime, deduplicate=True)
        _, same, _ = persist_large_tool_result("session-a", "get_schema", raw, runtime=self.runtime, deduplicate=True)
        _, other, _ = persist_large_tool_result("session-b", "get_schema", raw, runtime=self.runtime, deduplicate=True)
        self.assertEqual(first["artifact_id"], same["artifact_id"])
        self.assertNotEqual(first["artifact_id"], other["artifact_id"])
        self.assertEqual(load_tool_result_artifact(other["artifact_id"], runtime=self.runtime)["session_id"], "session-b")

    def test_parent_claims_preserve_handoff_and_reject_unavailable_scope(self):
        parent = persist_evidence_claim("session-a", "query_data", "7", runtime=self.runtime)
        envelope = make_tool_result("delegate_to_agent", "member-result", session_id="session-a", runtime=self.runtime,
                                    parent_claim_ids=[parent["claim_id"]])
        child = next(item for item in envelope.artifacts if item["type"] == "evidence_claim")
        self.assertEqual(self.record(child)["parent_claim_ids"], [parent["claim_id"]])
        with self.assertRaises(ToolResultAccessError):
            persist_evidence_claim("session-b", "query_data", "x", runtime=self.runtime,
                                   parent_claim_ids=[parent["claim_id"]])
        with self.assertRaises(ValueError):
            persist_evidence_claim("session-a", "query_data", "x", runtime=self.runtime,
                                   parent_claim_ids=["../parent"])

    def test_corrupted_claim_and_result_are_rejected(self):
        grant = persist_evidence_claim("session-a", "query_data", "7", runtime=self.runtime)
        record = self.record(grant)
        artifact = record["result_artifact"]
        path = self.root / "tool_results" / f"{artifact['artifact_id']}.json"
        result = json.loads(path.read_text(encoding="utf-8"))
        result["data"] = "tampered"
        path.write_text(json.dumps(result), encoding="utf-8")
        with self.assertRaises(ToolResultAccessError):
            self.read(grant, section="result")
        record["sql"] = "tampered"
        (self.root / "evidence_claims" / f"{grant['claim_id']}.json").write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaises(ToolResultAccessError):
            self.read(grant)

    def test_transport_replay_deduplicates_results_and_same_invocation_claims(self):
        args = {"sql": "SELECT 7"}
        first = make_tool_result("query_data", "synthetic-result" * 1000, session_id="session-a", runtime=self.runtime,
                                 args=args, execution_context={"run_id": "run-a", "tool_call_id": "call-a"})
        second = make_tool_result("query_data", "synthetic-result" * 1000, session_id="session-a", runtime=self.runtime,
                                  args=args, execution_context={"run_id": "run-a", "tool_call_id": "call-a"})
        self.assertEqual(first.artifacts, second.artifacts)
        self.assertEqual(len(list((self.root / "tool_results").iterdir())), 1)
        self.assertEqual(len(list((self.root / "evidence_claims").iterdir())), 1)
        third = make_tool_result("query_data", "synthetic-result" * 1000, session_id="session-a", runtime=self.runtime,
                                 args=args, execution_context={"run_id": "run-a", "tool_call_id": "call-b"})
        self.assertNotEqual(first.artifacts[-1]["claim_id"], third.artifacts[-1]["claim_id"])

    def test_corrupt_deduplicated_result_is_repaired_and_mismatched_reuse_is_rejected(self):
        _, artifact, _ = persist_large_tool_result("session-a", "query_data", "7", runtime=self.runtime,
                                                  force=True, deduplicate=True)
        target = self.root / "tool_results" / f"{artifact['artifact_id']}.json"
        record = json.loads(target.read_text(encoding="utf-8"))
        record["data"] = "tampered"
        target.write_text(json.dumps(record), encoding="utf-8")
        _, repaired, _ = persist_large_tool_result("session-a", "query_data", "7", runtime=self.runtime,
                                                  force=True, deduplicate=True)
        self.assertEqual(load_tool_result_artifact(repaired["artifact_id"], runtime=self.runtime)["data"], "7")
        _, unrelated, _ = persist_large_tool_result("session-a", "query_data", "different", runtime=self.runtime, force=True)
        forged_hint = {**unrelated, "sha256": repaired["sha256"]}
        grant = persist_evidence_claim("session-a", "query_data", "7", runtime=self.runtime, artifacts=[forged_hint])
        self.assertEqual(self.read(grant, section="result")["content"], "7")

    def test_missing_grant_hash_and_wrong_runtime_are_rejected(self):
        grant = persist_evidence_claim("session-a", "query_data", "7", runtime=self.runtime)
        without_hash = {key: value for key, value in grant.items() if key != "sha256"}
        with self.assertRaises(ToolResultAccessError):
            self.read(without_hash)
        wrong_runtime = SimpleNamespace(cache_dir=self.root, workspace_id="wrong-runtime")
        with self.assertRaises(ToolResultAccessError):
            read_evidence_claim(grant["claim_id"], allowed_artifacts=[grant], session_id="session-a",
                                workspace_id=self.runtime.workspace_id, runtime=wrong_runtime)
        self.assertEqual(self.record(grant)["replay_scope"], "stored_result_only")
        artifact = self.record(grant)["result_artifact"]
        missing_sha = {key: value for key, value in artifact.items() if key != "sha256"}
        with self.assertRaises(ToolResultAccessError):
            read_tool_result_artifact(artifact["artifact_id"], allowed_artifacts=[missing_sha], session_id="session-a",
                                      workspace_id=self.runtime.workspace_id, runtime=self.runtime)

    def test_workspace_root_recovery_and_nonzero_pages_are_consistent(self):
        workspace = self.root / "workspace"
        runtime = SimpleNamespace(cache_dir=workspace / ".baa_cache", workspace_id=self.runtime.workspace_id)
        grant = persist_evidence_claim("session-a", "query_data", "0123456789", runtime=runtime)
        page = read_evidence_claim(grant["claim_id"], allowed_artifacts=[grant], session_id="session-a",
                                   workspace_id=runtime.workspace_id, workspace_root=workspace,
                                   section="result", offset=3, limit=4)
        self.assertEqual(page["content"], "3456")
        self.assertEqual(page["next_offset"], 7)
        self.assertEqual(page["claim_id"], grant["claim_id"])
        self.assertEqual(page["section"], "result")
        no_cache_runtime = SimpleNamespace(workspace_id=runtime.workspace_id)
        recovered = read_evidence_claim(grant["claim_id"], allowed_artifacts=[grant], session_id="session-a",
                                        workspace_id=runtime.workspace_id, runtime=no_cache_runtime,
                                        workspace_root=workspace, section="result", offset=3, limit=4)
        self.assertEqual(recovered["content"], page["content"])

    def test_partial_history_keeps_claim_recovery_reference(self):
        from agent.tools.results import truncate_tool_result_to_total_cap
        grant = persist_evidence_claim("session-a", "query_data", "7", runtime=self.runtime)
        text = "synthetic-text" * 1000 + " " + grant["uri"]
        bounded = truncate_tool_result_to_total_cap(text, 500)
        self.assertLessEqual(len(bounded), 500)
        self.assertIn(grant["uri"], bounded)

    def test_failed_atomic_replace_leaves_no_claim_or_partial_json(self):
        # Persist the reusable result before simulating a claim-only disk failure.
        _, artifact, _ = persist_large_tool_result("session-a", "query_data", "7", runtime=self.runtime, force=True)
        with patch("agent.tools.results.os.replace", side_effect=OSError("synthetic disk error")):
            with self.assertRaisesRegex(OSError, "synthetic disk error"):
                persist_evidence_claim("session-a", "query_data", "7", runtime=self.runtime, artifacts=[artifact])
        self.assertEqual(list((self.root / "evidence_claims").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
