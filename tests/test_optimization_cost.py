"""Synthetic contract regressions; no provider calls or real billing claims."""
from __future__ import annotations

import copy
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from LLM.prompt_cache import (
    PromptCachePolicy, apply_prompt_cache_policy, build_cacheable_prompt,
    cache_scope_user_id, stable_prompt_cache_key,
)
from agent.context_optimization import assemble_stable_context, recommend_context_optimization, recommend_role_limits
from agent.teams.model_policy import resolve_profile_model
from agent.token_metrics import build_prompt_breakdown, finalize_prompt_breakdown
from data.cost_baseline import load_manifest, load_trials, summarize, validate_trial
from data.cost_event_store import CostEventStore, model_cost_event_id
from data.optimization_trials import evaluate_optimization_gate, import_real_trials, manifest_fingerprint, prompt_fingerprint
from scripts.run_optimization_baseline import run_offline_baseline


FIXTURE = Path(__file__).parent / "fixtures" / "optimization_cost" / "manifest.json"


class CostLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="baa-ledger-test-")
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "events.sqlite3"
        self.store = CostEventStore(self.path)
        self.receipt = dict(event_id="receipt-a", occurred_at=datetime.now(timezone.utc).isoformat(),
                            scope_type="session", scope_id="s", session_id="s", workspace_id="w",
                            model_call_id="provider-call-a", attempt=1, provider="synthetic", model="unit-model",
                            status="succeeded", measurement_mode="actual_model", usage_source="provider_reported",
                            input_tokens=100, output_tokens=20, cached_input_tokens=0, cache_write_tokens=0,
                            estimated_cost=0.012, currency="USD", price_version="unit-price-v1", tool_call_count=2)

    def test_missing_usage_is_unknown_but_provider_zero_is_real(self):
        self.store.record(**dict(self.receipt, input_tokens=None, output_tokens=None, usage_source="unavailable"))
        unknown = self.store.summary(session_id="s")
        self.assertIsNone(unknown["input_tokens"])
        self.assertIsNone(unknown["estimated_cost"])
        self.assertEqual(1, unknown["unknown_usage_event_count"])
        self.store.record(**dict(self.receipt, input_tokens=0, output_tokens=0, estimated_cost=0))
        real_zero = self.store.summary(session_id="s")
        self.assertEqual(0, real_zero["input_tokens"])
        self.assertEqual(0, real_zero["estimated_cost"])
        self.assertEqual(1, real_zero["actual_usage_event_count"])

    def test_new_receipt_replay_dedupes_and_does_not_erase_usage(self):
        self.store.record(**self.receipt)
        self.store.record(**dict(self.receipt, event_id="transport-replay", input_tokens=None, output_tokens=None,
                                 cached_input_tokens=None, cache_write_tokens=None, estimated_cost=None,
                                 tool_call_count=0, status="unknown", usage_source="unavailable"))
        rows = self.store.list_events(session_id="s")
        self.assertEqual(1, len(rows))
        self.assertEqual("receipt-a", rows[0]["event_id"])
        self.assertEqual(100, rows[0]["input_tokens"])
        self.assertEqual(2, rows[0]["tool_call_count"])
        self.assertEqual(0.012, rows[0]["estimated_cost"])

    def test_real_retry_attempt_and_failed_usage_remain_charged(self):
        self.store.record(**dict(self.receipt, status="failed"))
        self.store.record(**dict(self.receipt, event_id="receipt-b", attempt=2, retry_of_event_id="receipt-a"))
        summary = self.store.summary(session_id="s")
        self.assertEqual(2, summary["event_count"])
        self.assertEqual(200, summary["input_tokens"])
        self.assertAlmostEqual(0.024, summary["estimated_cost"])
        self.assertEqual(1, self.store.summary(status="failed")["event_count"])
        self.assertEqual(1, self.store.summary(status="succeeded")["event_count"])

    def test_same_call_visible_in_all_scopes_without_double_charge(self):
        self.store.record(**self.receipt)
        self.store.record(**dict(self.receipt, event_id="team-receipt", scope_type="team_task", scope_id="task", parent_scope_id="plan"))
        self.store.record(**dict(self.receipt, event_id="workflow-receipt", scope_type="workflow_node", scope_id="node", parent_scope_id="run"))
        self.assertEqual(1, self.store.summary(session_id="s")["event_count"])
        for scope, identifier, parent in (("session", "s", ""), ("team_task", "task", "plan"), ("workflow_node", "node", "run")):
            row = self.store.list_events(scope_type=scope, scope_id=identifier, parent_scope_id=parent)[0]
            self.assertEqual(100, row["input_tokens"])
            self.assertEqual(3, len(row["scopes"]))
        # Scope predicates must match one association, not a mix of two scopes.
        self.assertEqual([], self.store.list_events(scope_type="workflow_node", parent_scope_id="plan"))

    def test_owner_isolation_and_receipt_conflicts(self):
        self.store.record(**self.receipt)
        self.store.record(**dict(self.receipt, event_id="owner-b", workspace_id="other"))
        self.assertEqual(2, self.store.summary()["event_count"])
        self.assertEqual(1, self.store.summary(workspace_id="w")["event_count"])
        for changed in ({"workspace_id": "alien"}, {"attempt": 2}, {"model": "different"}, {"measurement_mode": "offline_contract"}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                self.store.record(**dict(self.receipt, **changed))

    def test_actual_and_offline_counters_are_never_combined(self):
        self.store.record(**self.receipt)
        self.store.record(**dict(self.receipt, event_id="offline", model_call_id="local-op", measurement_mode="offline_contract", usage_source="offline_adapter"))
        summary = self.store.summary()
        self.assertTrue(summary["mixed_measurements"])
        self.assertIsNone(summary["input_tokens"])
        self.assertIsNone(summary["known_input_tokens"])
        self.assertIsNone(summary["estimated_cost"])
        self.assertEqual(100, summary["measurement_totals"]["actual_model"]["input_tokens"])
        self.assertIsNone(summary["measurement_totals"]["offline_contract"]["estimated_cost"])
        self.assertEqual(2, len(self.store.cache_trends()))

    def test_partial_totals_show_known_subtotal_without_false_complete_cost(self):
        self.store.record(**self.receipt)
        self.store.record(**dict(self.receipt, event_id="missing", model_call_id="missing-call", input_tokens=None, output_tokens=None))
        summary = self.store.summary()
        self.assertIsNone(summary["input_tokens"])
        self.assertEqual(100, summary["known_input_tokens"])
        self.assertIsNone(summary["estimated_cost"])
        self.assertEqual(1, summary["priced_event_count"])

    def test_corrected_usage_invalidates_stale_cost_until_repriced(self):
        self.store.record(**self.receipt)
        self.store.record(**dict(self.receipt, event_id="corrected-receipt", input_tokens=110, estimated_cost=None))
        row = self.store.list_events()[0]
        self.assertEqual(110, row["input_tokens"])
        self.assertIsNone(row["estimated_cost"])

    def test_concurrent_store_instances_dedupe_the_same_attempt(self):
        second = CostEventStore(self.path)
        def record(index):
            (self.store if index % 2 else second).record(**dict(self.receipt, event_id=f"receipt-{index}"))
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(record, range(12)))
        self.assertEqual(1, self.store.summary()["event_count"])

    def test_legacy_defaults_migrate_without_inventing_zero_usage(self):
        legacy = Path(self.temporary.name) / "legacy.sqlite3"
        # Start from the old table layout without any provenance/availability fields.
        with closing(sqlite3.connect(legacy)) as conn:
            conn.execute("""CREATE TABLE cost_events (
                event_id TEXT PRIMARY KEY, occurred_at TEXT NOT NULL, workspace_id TEXT NOT NULL DEFAULT '',
                session_id TEXT NOT NULL DEFAULT '', scope_type TEXT NOT NULL, scope_id TEXT NOT NULL DEFAULT '',
                parent_scope_id TEXT NOT NULL DEFAULT '', provider TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '',
                input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
                cached_input_tokens INTEGER NOT NULL DEFAULT 0, cache_write_tokens INTEGER NOT NULL DEFAULT 0,
                tool_call_count INTEGER NOT NULL DEFAULT 0, latency_ms INTEGER, estimated_cost REAL,
                currency TEXT NOT NULL DEFAULT '', price_version TEXT NOT NULL DEFAULT '', attribution_json TEXT NOT NULL DEFAULT '{}')""")
            conn.execute("INSERT INTO cost_events(event_id,occurred_at,scope_type,estimated_cost) VALUES ('old','2026-11-14T00:00:00Z','session',0)")
            conn.commit()
        migrated = CostEventStore(legacy)
        row = migrated.list_events()[0]
        self.assertEqual("legacy_unknown", row["measurement_mode"])
        self.assertEqual("legacy_unknown", row["usage_source"])
        self.assertIsNone(row["input_tokens"])
        self.assertIsNone(row["estimated_cost"])
        self.assertEqual(1, CostEventStore(legacy).summary()["event_count"])

    def add_old_scope_receipt(self, *, conflicting=False):
        with closing(sqlite3.connect(self.path)) as conn:
            conn.row_factory = sqlite3.Row
            row = dict(conn.execute("SELECT * FROM cost_events WHERE event_id='receipt-a'").fetchone())
            row.update(event_id="receipt-z", scope_type="team_task", scope_id="old-task", parent_scope_id="old-plan")
            if conflicting:
                row["input_tokens"] += 1
            conn.execute("INSERT INTO cost_events (" + ",".join(row) + ") VALUES (" + ",".join("?" for _ in row) + ")", list(row.values()))
            conn.commit()

    def test_previous_per_scope_receipts_migrate_to_one_call_and_keep_retry_links(self):
        self.store.record(**self.receipt)
        self.add_old_scope_receipt()
        self.store.record(**dict(self.receipt, event_id="retry", attempt=2, retry_of_event_id="receipt-z"))
        migrated = CostEventStore(self.path)
        self.assertEqual(2, migrated.summary()["event_count"])
        self.assertAlmostEqual(0.024, migrated.summary()["estimated_cost"])
        self.assertEqual(1, migrated.summary(scope_type="team_task", scope_id="old-task")["event_count"])
        rows = {row["event_id"]: row for row in migrated.list_events()}
        self.assertEqual("receipt-a", rows["retry"]["retry_of_event_id"])
        self.assertEqual(2, len(rows["receipt-a"]["scopes"]))

    def test_conflicting_old_receipts_abort_migration_without_data_loss(self):
        self.store.record(**self.receipt)
        self.add_old_scope_receipt(conflicting=True)
        with self.assertRaisesRegex(ValueError, "reconciliation"):
            CostEventStore(self.path)
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual(2, conn.execute("SELECT COUNT(*) FROM cost_events").fetchone()[0])
            self.assertEqual([100, 101], [row[0] for row in conn.execute("SELECT input_tokens FROM cost_events ORDER BY event_id")])

    def test_cost_mixed_currency_and_price_version_is_unavailable(self):
        self.store.record(**self.receipt)
        self.store.record(**dict(self.receipt, event_id="different-price", model_call_id="call-b", price_version="v2"))
        self.assertIsNone(self.store.summary()["estimated_cost"])

    def test_event_ids_and_attempts_are_scoped_and_strict(self):
        first = model_cost_event_id("a", workspace_id="w", session_id="s")
        self.assertEqual(first, model_cost_event_id("a", workspace_id="w", session_id="s"))
        self.assertEqual(first, model_cost_event_id("a", workspace_id="w", session_id="s", scope_type="team_task", scope_id="task"))
        self.assertNotEqual(first, model_cost_event_id("a", workspace_id="other", session_id="s"))
        self.assertNotEqual(first, model_cost_event_id("a", workspace_id="w", session_id="s", attempt=2))
        for invalid in (True, 0, -1, 1.5, "bad"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                model_cost_event_id("a", attempt=invalid)

    def test_attribution_is_numeric_only(self):
        self.store.record(**dict(self.receipt, attribution={"system_tokens_est": 8, "prompt": "private", "history_tokens_est": float("nan")}))
        self.assertEqual({"system_tokens_est": 8}, self.store.list_events()[0]["attribution"])


class PromptAndPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tools = [{"type": "function", "function": {"name": "query_data", "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "maximum": 10}}}}}]
        self.key_args = dict(provider="synthetic", model="unit", workflow_stage="analysis", tools=self.tools,
                             stable_prefix=[{"role": "system", "content": "stable rules"}], stable_revision="rules-v1")

    def test_cache_fingerprint_tracks_schema_prefix_revision_and_order(self):
        key = stable_prompt_cache_key(**self.key_args)
        for name, change in (("schema", {"tools": [dict(self.tools[0], description="changed schema")]}),
                             ("prefix", {"stable_prefix": "different rules"}), ("revision", {"stable_revision": "v2"}),
                             ("model", {"model": "another"}), ("stage", {"workflow_stage": "report"})):
            with self.subTest(name=name):
                self.assertNotEqual(key, stable_prompt_cache_key(**dict(self.key_args, **change)))
        reordered = json.loads(json.dumps(self.tools, sort_keys=True))
        self.assertEqual(key, stable_prompt_cache_key(**dict(self.key_args, tools=reordered)))
        self.assertNotIn("stable rules", key)

    def test_stable_assembly_keeps_dynamic_state_out_of_identity_and_copies_inputs(self):
        stable = [{"role": "system", "content": "rules"}]
        dynamic = [{"role": "system", "content": "knowledge v1"}]
        history = [{"role": "user", "content": "question one"}]
        args = dict(stable_rules=stable, dynamic_context=dynamic, history=history, tools=self.tools, provider="synthetic", model="unit")
        first = assemble_stable_context(**args)
        second = assemble_stable_context(**dict(args, history=[{"role": "user", "content": "question two"}]))
        self.assertEqual(first["cache_key"], second["cache_key"])
        self.assertEqual(stable + dynamic + history, first["messages"])
        first["messages"][0]["content"] = "mutated"
        self.assertEqual("rules", stable[0]["content"])
        with self.assertRaises(ValueError):
            build_cacheable_prompt(stable_messages=history, dynamic_messages=[], tools=[], provider="synthetic", model="unit")

    def test_cache_scope_uses_unambiguous_owner_encoding(self):
        self.assertNotEqual(cache_scope_user_id(user_id="a|b", workspace_id="c"), cache_scope_user_id(user_id="a", workspace_id="b|c"))
        policy = PromptCachePolicy(enabled=True, mode="openai")
        first, metadata = apply_prompt_cache_policy({}, policy=policy, cache_key="x", user_id="owner", workspace_id="w1")
        second, _ = apply_prompt_cache_policy({}, policy=policy, cache_key="x", user_id="owner", workspace_id="w2")
        self.assertNotEqual(first["prompt_cache_key"], second["prompt_cache_key"])
        self.assertTrue(metadata["scope_isolated"])
        self.assertNotIn("owner", first["prompt_cache_key"])

    def test_nullable_provider_usage_and_cache_billing(self):
        breakdown = build_prompt_breakdown([], [])
        for missing in (None, {}, {"prompt_tokens": -1, "completion_tokens": "bad"}):
            row = finalize_prompt_breakdown(breakdown, missing)
            self.assertFalse(row["usage_available"])
            self.assertIsNone(row["actual_prompt_tokens"])
            self.assertEqual("unavailable", row["usage_source"])
        row = finalize_prompt_breakdown(breakdown, {"input_tokens": 10, "output_tokens": 2, "cache_read_input_tokens": 50, "cache_creation_input_tokens": 5})
        self.assertEqual(65, row["actual_prompt_tokens"])
        self.assertEqual(67, row["actual_total_tokens"])
        self.assertTrue(row["usage_complete"])
        zero = finalize_prompt_breakdown(breakdown, {"prompt_tokens": 0, "completion_tokens": 0})
        self.assertTrue(zero["usage_available"])
        self.assertEqual(0, zero["actual_total_tokens"])
        self.assertIsNone(zero["cached_input_tokens"])

    def test_offline_and_inconsistent_usage_remain_explicit(self):
        row = finalize_prompt_breakdown({}, {"prompt_tokens": 10, "completion_tokens": 2}, usage_source="offline_adapter")
        self.assertEqual("offline_contract", row["measurement_mode"])
        row = finalize_prompt_breakdown({}, {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 9})
        self.assertFalse(row["usage_complete"])
        row = finalize_prompt_breakdown({}, {"prompt_tokens": 10, "completion_tokens": 2}, usage_source="unavailable")
        self.assertIsNone(row["actual_prompt_tokens"])

    def test_estimates_do_not_double_count_tool_history_or_trigger_policy(self):
        messages = [{"role": "system", "content": "rule"}, {"role": "tool", "content": "x" * 400}, {"role": "user", "content": "q"}]
        row = build_prompt_breakdown(messages, self.tools, current_user_message="q")
        self.assertGreater(row["history_chars"], row["tool_result_chars"])
        recommendations = recommend_context_optimization({"history_tokens_est": 50000, "tool_result_tokens_est": 8000, "system_tokens_est": 7000}, max_recommendations=2)
        self.assertEqual(2, len(recommendations["recommendations"]))
        self.assertFalse(recommendations["estimates_are_additive"])
        self.assertFalse(recommendations["applied"])
        self.assertFalse(recommendations["gate_eligible"])

    def test_role_limits_intersect_caps_and_preserve_zero_tools(self):
        recommendation = recommend_role_limits({"max_output_tokens": 2000, "max_tool_calls": 0}, requested_output_tokens=3000, requested_tool_calls=12)
        self.assertEqual(2000, recommendation["max_output_tokens"])
        self.assertEqual(0, recommendation["max_tool_calls"])
        self.assertTrue(recommendation["review_required"])
        self.assertFalse(recommendation["applied"])
        with self.assertRaises(ValueError):
            recommend_role_limits({"max_tool_calls": -1})

    def test_model_mapping_requires_approval_matching_gate_and_same_provider(self):
        profile = {"model_tier": "low_cost"}
        mapping = {"version": 1, "policy_id": "context-v1", "approved": True,
                   "tiers": {"low_cost": {"provider": "synthetic", "model": "tested"}}}
        gate = {"eligible": True, "eligibility": "eligible", "policy_id": "context-v1", "measurement_mode": "actual_model",
                "manifest_hash": "sha256:unit", "comparison_hash": "sha256:unit", "reasons": [],
                "models": [{"provider": "synthetic", "model": "tested"}]}
        args = dict(profile=profile, inherited_model="selected", inherited_provider="synthetic", tier_mapping=mapping, gate=gate)
        self.assertTrue(resolve_profile_model(**args)["applied"])
        self.assertEqual("tested", resolve_profile_model(**args)["model"])
        for changed in ({"tier_mapping": None}, {"gate": None}, {"gate": dict(gate, eligible=False)},
                        {"gate": dict(gate, policy_id="wrong")}, {"gate": dict(gate, models=[])},
                        {"tier_mapping": dict(mapping, approved=False)}, {"inherited_provider": "another-provider"}):
            with self.subTest(changed=changed):
                result = resolve_profile_model(**dict(args, **changed))
                self.assertFalse(result["applied"])
                self.assertEqual("selected", result["model"])


class TrialGateTests(unittest.TestCase):
    def setUp(self):
        self.manifest = load_manifest(FIXTURE)
        self.before, self.after = self.synthetic_actual_format("before"), self.synthetic_actual_format("after")

    def synthetic_actual_format(self, variant):
        """Unit-only fabricated receipts exercise validation, never savings reports."""
        rows = []
        for case in self.manifest["cases"]:
            for index in range(3):
                identity = f"{variant}-{case['id']}-{index}"
                rows.append({
                    "case_id": case["id"], "trial_id": identity, "pair_id": f"pair-{index}", "variant": variant,
                    "policy_id": "context-v1" if variant == "after" else "baseline", "manifest_hash": manifest_fingerprint(self.manifest),
                    "snapshot_id": case["snapshot_id"], "prompt_hash": prompt_fingerprint(case["prompt"]),
                    "measurement_mode": "actual_model", "usage_source": "operator_verified", "usage_complete": True,
                    "status": "succeeded", "provider": "synthetic", "model": "unit-model", "price_version": "unit-price-v1",
                    "currency": "USD", "cache_config": "unit-cache", "team_config": "unit-team", "runtime_version": "unit-runtime", "tool_contract_version": "unit-tools-v1",
                    "input_tokens": 100 if variant == "before" else 80, "output_tokens": 20,
                    "cached_input_tokens": 0, "cache_write_tokens": 0, "estimated_cost": 0.012 if variant == "before" else 0.010,
                    "cost_source": "price_table", "elapsed_ms": 10 if variant == "before" else 9,
                    "tool_call_count": 2, "retry_count": 0, "rework_count": 0, "delivery_count": 1,
                    "quality_pass": True, "evidence_complete": True, "delivery_pass": True,
                    "model_call_ids": [identity], "evidence_refs": ["sha256:synthetic"],
                    "assertions": {key: True for key in case["expected_evidence"]},
                })
        return rows

    def gate(self):
        return evaluate_optimization_gate(self.manifest, self.before, self.after)

    def test_valid_paired_format_is_eligible_in_a_pure_unit_test(self):
        gate = self.gate()
        self.assertTrue(gate["eligible"], gate["reasons"])
        self.assertEqual(42, gate["pair_count"])
        self.assertEqual(14, gate["case_count"])
        self.assertEqual(1, len(gate["models"]))

    def test_offline_trials_cannot_enable_real_model_policy(self):
        self.after[0].update(measurement_mode="offline_contract", usage_source="offline_adapter", estimated_cost=None)
        self.assertFalse(self.gate()["eligible"])
        with self.assertRaises(ValueError):
            import_real_trials(self.manifest, self.after)

    def test_missing_or_partial_usage_cannot_enable(self):
        for field, value in (("input_tokens", None), ("output_tokens", None), ("cached_input_tokens", None),
                             ("cache_write_tokens", None), ("cached_input_tokens", 999),
                             ("estimated_cost", None), ("usage_complete", False)):
            candidate = copy.deepcopy(self.after)
            candidate[0][field] = value
            with self.subTest(field=field):
                self.assertFalse(evaluate_optimization_gate(self.manifest, self.before, candidate)["eligible"])

    def test_each_snapshot_model_price_and_switch_must_match(self):
        for field in ("snapshot_id", "prompt_hash", "model", "provider", "price_version", "currency", "cache_config", "team_config", "runtime_version", "tool_contract_version"):
            candidate = copy.deepcopy(self.after)
            candidate[0][field] = "changed"
            with self.subTest(field=field):
                self.assertFalse(evaluate_optimization_gate(self.manifest, self.before, candidate)["eligible"])

    def test_minimum_counts_and_exact_trial_pairing_are_required(self):
        self.assertFalse(evaluate_optimization_gate(self.manifest, self.before, self.after[:-1])["eligible"])
        self.after[-1]["pair_id"] = "not-paired"
        self.assertFalse(self.gate()["eligible"])

    def test_receipt_replay_is_not_a_new_sample(self):
        self.after[1]["model_call_ids"] = self.after[0]["model_call_ids"]
        self.assertFalse(self.gate()["eligible"])
        self.after = self.synthetic_actual_format("after")
        self.after[0]["model_call_ids"] = self.before[0]["model_call_ids"]
        self.assertIn("call_attempt_reused_across_variants", self.gate()["reasons"])

    def test_quality_evidence_delivery_retry_and_rework_fail_closed(self):
        for changes in ({"quality_pass": False}, {"evidence_complete": False}, {"delivery_pass": False},
                        {"evidence_refs": []}, {"assertions": {}}, {"status": "failed"},
                        {"delivery_count": 2}, {"retry_count": 1}, {"rework_count": 1}):
            candidate = copy.deepcopy(self.after)
            candidate[0].update(changes)
            with self.subTest(changes=changes):
                self.assertFalse(evaluate_optimization_gate(self.manifest, self.before, candidate)["eligible"])

    def test_pair_cost_regression_cannot_hide_behind_better_median(self):
        self.after[0]["estimated_cost"] = 0.013
        self.assertFalse(self.gate()["eligible"])

    def test_no_savings_and_latency_regression_are_rejected(self):
        for row in self.after:
            row["estimated_cost"] = 0.012
        self.assertIn("stable_cost_reduction_below_one_percent", self.gate()["reasons"])
        self.after = self.synthetic_actual_format("after")
        for row in self.after[:3]:
            row["elapsed_ms"] = 11
        self.assertFalse(self.gate()["eligible"])

    def test_raw_content_nonfinite_metrics_and_nested_leaks_are_rejected(self):
        row = self.after[0]
        for changes in ({"prompt": "private"}, {"assertions": {"messages": []}},
                        {"input_tokens": float("nan")}, {"input_tokens": 1.5}, {"input_tokens": "80"},
                        {"cache_config": {"nested": {"answer": "private"}}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_trial(dict(row, **changes), strict=True)

    def test_offline_and_actual_summary_metrics_remain_separate(self):
        offline = copy.deepcopy(self.before)
        for row in offline:
            row.update(trial_id="offline-" + row["trial_id"], measurement_mode="offline_contract", usage_source="unavailable",
                       input_tokens=None, output_tokens=None, cached_input_tokens=None, cache_write_tokens=None, estimated_cost=None, usage_complete=False)
        summary = summarize(self.manifest, [*self.before, *offline])
        self.assertIsNone(summary["cases"][0]["median_input_tokens"])
        self.assertIsNone(summary["cases"][0]["median_estimated_cost"])
        actual = summary["measurement_summaries"]["actual_model:actual"]
        self.assertEqual(100, actual["cases"][0]["median_input_tokens"])
        self.assertIsNone(summary["measurement_summaries"]["offline_contract:unavailable"]["cases"][0]["median_input_tokens"])

    def test_currency_changes_across_cases_do_not_produce_a_cost_total(self):
        for row in [*self.before, *self.after]:
            if row["case_id"] == self.manifest["cases"][-1]["id"]:
                row["currency"] = "CNY"
        gate = self.gate()
        self.assertFalse(gate["eligible"])
        self.assertIn("comparison_currencies_differ", gate["reasons"])
        self.assertIsNone(gate["sum_case_median_before_cost"])

    def test_jsonl_import_rejects_unsupported_fields(self):
        with tempfile.TemporaryDirectory(prefix="baa-trial-import-") as temporary:
            path = Path(temporary) / "trials.jsonl"
            path.write_text(json.dumps(dict(self.after[0], raw_response="private")), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_trials(path)


class OfflineBaselineTests(unittest.TestCase):
    def test_fourteen_scenarios_execute_three_trials_and_reject_real_cost_gate(self):
        with tempfile.TemporaryDirectory(prefix="baa-offline-test-") as temporary:
            with patch.dict(os.environ, {"BAA_DATA_DIR": str(Path(temporary) / "data")}):
                result = run_offline_baseline(FIXTURE, output_dir=Path(temporary) / "report")
            self.assertEqual(42, len(result["trials"]))
            self.assertEqual(14, result["summary"]["quality_gate_passed_case_count"],
                             [(row["case_id"], row["assertions"], row["failure_stage"]) for row in result["trials"] if not row["quality_pass"]])
            self.assertFalse(result["gate"]["eligible"])
            for row in result["trials"]:
                self.assertGreater(row["elapsed_ms"], 0)
                self.assertGreater(row["tool_call_count"], 0)
                self.assertTrue(row["evidence_refs"])
                self.assertIsNone(row["input_tokens"])
                self.assertIsNone(row["estimated_cost"])
            imported = load_trials(Path(temporary) / "report/trials.jsonl")
            self.assertEqual(result["trials"], imported)

    def test_changed_snapshot_rejected_and_wrong_expected_value_fails_assertions(self):
        with tempfile.TemporaryDirectory(prefix="baa-snapshot-test-") as temporary:
            root = Path(temporary)
            manifest = load_manifest(FIXTURE)
            snapshot = json.loads((FIXTURE.parent / "snapshot.json").read_text(encoding="utf-8"))
            snapshot["expected"]["window_gross"] = 999
            content = json.dumps(snapshot).encode("utf-8")
            (root / "snapshot.json").write_bytes(content)
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                run_offline_baseline(root / "manifest.json")
            digest = hashlib.sha256(content).hexdigest()
            manifest["snapshot_sha256"] = digest
            for case in manifest["cases"]:
                case["snapshot_id"] = "sha256:" + digest
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            result = run_offline_baseline(root / "manifest.json")
            single = [row for row in result["trials"] if row["case_id"] == "single-table-v1"]
            self.assertTrue(all(row["quality_pass"] is False for row in single))
            self.assertTrue(all(row["status"] == "failed" for row in single))


if __name__ == "__main__":
    unittest.main()
