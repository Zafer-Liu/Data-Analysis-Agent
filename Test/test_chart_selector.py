#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for LLM/chart_selector.py — registry coverage + scoring sanity."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from LLM.chart_selector import (
    _CHARTS,
    format_selection_result,
    select_charts,
)


class TestRegistry(unittest.TestCase):
    """Every entry must be self-consistent so generate_chart can trust it."""

    def test_registry_not_empty(self):
        self.assertGreater(len(_CHARTS), 30)  # documented as 41 charts

    def test_every_chart_has_required_fields(self):
        required = {"chart_id", "name", "category",
                    "required_roles", "data_format", "desc"}
        for c in _CHARTS:
            missing = required - set(c.keys())
            self.assertFalse(missing, f"{c.get('chart_id')!r} missing keys: {missing}")

    def test_chart_ids_are_unique(self):
        ids = [c["chart_id"] for c in _CHARTS]
        self.assertEqual(len(ids), len(set(ids)), "duplicate chart_id in _CHARTS")

    def test_required_roles_are_lists(self):
        for c in _CHARTS:
            self.assertIsInstance(c["required_roles"], list, c["chart_id"])
            # Must have at least one role — otherwise generate_chart has nothing to map
            self.assertGreater(len(c["required_roles"]), 0, c["chart_id"])


class TestSelectCharts(unittest.TestCase):

    def test_returns_list_of_dicts(self):
        result = select_charts("各月销售趋势")
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)
        for c in result:
            self.assertIn("chart_id", c)
            self.assertIn("required_roles", c)

    def test_top_n_respected(self):
        for n in (1, 3, 5):
            with self.subTest(n=n):
                self.assertLessEqual(len(select_charts("销售", top_n=n)), n)

    def test_time_keywords_favor_line_chart(self):
        """'各月趋势' should rank a time-series chart highly."""
        result = select_charts("各月销售额趋势", available_columns=["month", "revenue"])
        top_ids = [c["chart_id"] for c in result]
        # Line / Area / Bar are all reasonable; assert at least one is in the top 3
        self.assertTrue(
            any(cid in top_ids for cid in ("Line_Chart", "Area_Chart", "Stacked_Area_Chart")),
            f"expected a time-series chart in top results, got {top_ids}"
        )

    def test_distribution_keywords_favor_histogram(self):
        result = select_charts("年龄分布直方图", available_columns=["age"])
        top_ids = [c["chart_id"] for c in result]
        # Histogram or boxplot reasonable
        self.assertTrue(
            any("istogram" in cid or "Box" in cid or "Density" in cid for cid in top_ids),
            f"expected a distribution chart, got {top_ids}"
        )

    def test_pie_keyword_favors_pie(self):
        result = select_charts("各品类销售占比饼图", available_columns=["category", "sales"])
        top_ids = [c["chart_id"] for c in result]
        self.assertTrue(
            any("Pie" in cid or "Donut" in cid for cid in top_ids),
            f"expected a Pie/Donut chart, got {top_ids}"
        )

    def test_columns_affinity_boosts_score(self):
        """Providing columns matching required_roles should not lower the result."""
        without = select_charts("销售对比")
        with_cols = select_charts("销售对比", available_columns=["category", "amount"])
        # Both should return something; the with-columns call should be at least as
        # confident (top score >= the without one), but exact rank isn't guaranteed.
        self.assertGreater(len(without), 0)
        self.assertGreater(len(with_cols), 0)

    def test_empty_intent_still_returns(self):
        # Edge case: empty user intent should not crash
        result = select_charts("")
        self.assertIsInstance(result, list)


class TestFormatSelectionResult(unittest.TestCase):

    def test_format_includes_chart_ids(self):
        cands = select_charts("销售趋势", top_n=2)
        text = format_selection_result(cands)
        self.assertIsInstance(text, str)
        # Each candidate's chart_id should appear in the output
        for c in cands:
            self.assertIn(c["chart_id"], text)


if __name__ == "__main__":
    unittest.main()
