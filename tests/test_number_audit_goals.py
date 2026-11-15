"""Offline numeric provenance regression cases for the optimization engine."""
from __future__ import annotations

import json
import unittest

from agent.number_audit import (
    audit_report_numbers, collect_number_evidence, number_evidence_gaps,
)


class NumericProvenanceGoalTests(unittest.TestCase):
    def test_wrong_integer_is_reported_without_substring_match(self):
        self.assertEqual((["3956"], 1), audit_report_numbers("3956 rows", ["13956 rows"]))
        self.assertEqual((["2956"], 1), audit_report_numbers("2956 rows", ["1956 rows"]))

    def test_existing_noise_and_rounding_suppression(self):
        text = "2013-2017, GB 3095-2012, PM2.5, v1.3, 2026 年，3 行，1.9，295.5 万。均值40.7。"
        self.assertEqual(([], 0), audit_report_numbers(text, ["40.73"]))
        self.assertEqual(([], 0), audit_report_numbers("减少556行", ["2954669, 2954113"]))

    def test_percentage_requires_units_and_actual_rate_evidence(self):
        self.assertEqual((["27%"], 1), audit_report_numbers("占比27%", ['{"count": 27}']))
        self.assertEqual(([], 0), audit_report_numbers("占比26.9%", ['{"missing_pct": 26.94}']))
        self.assertEqual(([], 0), audit_report_numbers("占比2.69%", ["比率2.694% "]))
        self.assertEqual(([], 0), audit_report_numbers("占比26.9%", ['{"missing_rate": 0.2694}']))

    def test_only_declared_operand_relationship_can_derive_rate(self):
        arbitrary = '{"price": 25, "row_count": 100}'
        self.assertEqual((["25%"], 1), audit_report_numbers("25%", [arbitrary]))
        self.assertEqual(([], 0), audit_report_numbers("25%", ['{"numerator":25,"denominator":100}']))
        mismatch = '{"numerator":25,"denominator":100,"numerator_unit":"元","denominator_unit":"行"}'
        self.assertEqual((["25%"], 1), audit_report_numbers("25%", [mismatch]))
        self.assertEqual(([], 0), audit_report_numbers("同比增长25%", ['{"previous":100,"current":125}']))

    def test_explicit_same_unit_fraction_is_bounded_to_own_sentence(self):
        self.assertEqual(([], 0), audit_report_numbers("25行 / 100行，占比25%", ["25 100"]))
        self.assertEqual((["25%"], 1), audit_report_numbers("25元 / 100行，占比25%", ["25 100"]))
        self.assertEqual((["25%"], 1), audit_report_numbers("25行 / 100行。另一项占比25%", ["25 100"]))
        self.assertEqual((["25%"], 1), audit_report_numbers("25行 / 100行，占比25%", [], ["25 100"]))

    def test_percent_gap_is_specific(self):
        gaps = number_evidence_gaps("缺失率88%", ["100 rows"])
        self.assertEqual("88%", gaps[0]["value"])
        self.assertIn("denominator", gaps[0]["reason"])

    def test_delegate_summary_does_not_certify_itself(self):
        messages = [
            {"role": "assistant", "tool_calls": [{"id": "d", "function": {
                "name": "agent_delegate", "arguments": "{}",
            }}]},
            {"role": "tool", "tool_call_id": "d", "content": json.dumps({
                "type": "tool_result", "tool": "agent_delegate", "ok": True,
                "data": {"result": "2956 rows, 88%", "tool_events": [{"result": "1956 rows"}]},
            })},
        ]
        evidence, context = collect_number_evidence(messages)
        self.assertEqual([], evidence)
        self.assertEqual((["2956", "88%"], 2), audit_report_numbers("2956 rows, 88%", evidence, context))
        # Only the executor's private child-tool channel provides the data.
        self.assertEqual((["2956", "88%"], 2), audit_report_numbers("2956 rows, 88%", ["1956 rows"]))

    def test_envelope_summary_debug_args_and_repair_prompts_are_not_evidence(self):
        messages = [
            {"role": "tool", "content": json.dumps({"type": "tool_result", "ok": True,
                "tool": "query_data", "summary": "2956 rows", "data": "1956 rows",
                "debug": {"token_count": 9999}})},
            {"role": "assistant", "content": "2956 rows"},
            {"role": "user", "content": "[QUALITY GATE] Unsupported 2956, 88%"},
        ]
        evidence, context = collect_number_evidence(messages)
        self.assertEqual(["1956 rows"], evidence)
        self.assertEqual((["2956", "88%"], 2), audit_report_numbers("2956 rows, 88%", evidence, context))


if __name__ == "__main__":
    unittest.main()
