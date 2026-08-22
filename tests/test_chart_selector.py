import unittest

from LLM.chart_selector import format_selection_result, select_charts


class ChartSelectorTests(unittest.TestCase):
    def test_relationship_intent_prefers_positive_scatter_candidate(self):
        candidates = select_charts(
            "客户年龄与消费金额的关系", ["客户年龄", "消费金额"]
        )

        self.assertEqual("Scatter_Plot", candidates[0]["chart_id"])
        self.assertGreater(candidates[0]["_score"], 0)

    def test_unmatched_intent_returns_no_candidates(self):
        candidates = select_charts("zzqv_unmatched_intent")

        self.assertEqual([], candidates)

    def test_positive_candidates_do_not_include_zero_score_fillers(self):
        candidates = select_charts("line chart", top_n=3)

        self.assertTrue(candidates)
        self.assertTrue(
            all(candidate["_score"] > 0 for candidate in candidates)
        )

    def test_empty_candidates_request_ask_user_clarification(self):
        result = format_selection_result([])

        self.assertIn("ask_user", result)
        self.assertIn("confirm", result.lower())
        self.assertNotIn("generate_chart", result)
        self.assertNotIn("complete list", result)

    def test_monthly_sales_trend_still_prefers_line_chart(self):
        candidates = select_charts("各月销售额趋势")

        self.assertEqual("Line_Chart", candidates[0]["chart_id"])


if __name__ == "__main__":
    unittest.main()
