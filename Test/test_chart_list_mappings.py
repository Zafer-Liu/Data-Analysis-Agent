import unittest

import pandas as pd

from Function.Charts_generation.chart_generate import generate_chart


class TestChartListMappings(unittest.TestCase):
    def assert_chart_ok(self, chart_type, df, mapping):
        result = generate_chart(df=df, chart_type=chart_type, mapping=mapping)
        self.assertTrue(result.get("success"), result)
        self.assertIn("Plotly.newPlot", result["html"])

    def test_line_accepts_series_list_as_wide_columns(self):
        df = pd.DataFrame({
            "Week_Num": [20, 21],
            "99 Pro": [10, 12],
            "FL/Cloud": [20, 18],
        })
        self.assert_chart_ok("Line_Chart", df, {
            "x": "Week_Num", "series": ["99 Pro", "FL/Cloud"],
        })

    def test_grouped_accepts_y_list(self):
        df = pd.DataFrame({
            "mode": ["99 Pro", "FL/Cloud"],
            "Orders/TSH": [2.2, 0.96],
            "IPO (R$)": [8.06, 10.26],
        })
        self.assert_chart_ok("Grouped_Bar_Chart", df, {
            "x": "mode", "y": ["Orders/TSH", "IPO (R$)"],
        })

    def test_grouped_preserves_series_with_multiple_metrics(self):
        df = pd.DataFrame({
            "Week_Num": [20, 20, 21, 21],
            "courier_type": ["99 Pro", "FL", "99 Pro", "FL"],
            "share": [0.3, 0.7, 0.35, 0.65],
            "ipo": [8.0, 10.0, 7.8, 9.8],
        })
        result = generate_chart(
            df=df,
            chart_type="Grouped_Bar_Chart",
            mapping={"x": "Week_Num", "series": "courier_type", "y": ["share", "ipo"]},
        )
        self.assertTrue(result.get("success"), result)
        self.assertIn("99 Pro", result["html"])
        self.assertIn("share", result["html"])

    def test_grouped_recovers_sql_series_alias_without_numeric_colorbar(self):
        df = pd.DataFrame({
            "x": [20, 20, 21, 21, 22, 22, 23, 23],
            "series": ["99 Pro", "FL/Cloud"] * 4,
            "y": [8.03, 10.48, 8.34, 10.31, 8.49, 10.20, 6.94, 9.64],
        })
        result = generate_chart(
            df=df,
            chart_type="Grouped_Bar_Chart",
            mapping={"x": "x", "y": "y", "series": "mode"},
        )
        self.assertTrue(result.get("success"), result)
        self.assertIn('"name":"99 Pro"', result["html"])
        self.assertIn('"name":"FL\\u002fCloud"', result["html"])
        self.assertNotIn('"coloraxis":"coloraxis"', result["html"])

    def test_stacked_accepts_y_list(self):
        df = pd.DataFrame({
            "Week_Num": [20, 21],
            "99 Pro": [10, 12],
            "FL/Cloud": [20, 18],
        })
        self.assert_chart_ok("Stacked_Bar_Chart", df, {
            "x": "Week_Num", "y": ["99 Pro", "FL/Cloud"],
        })

    def test_stacked_accepts_series_alias_in_long_format(self):
        df = pd.DataFrame({
            "x": ["W20", "W20", "W21", "W21"],
            "mode": ["99 Pro", "FL", "99 Pro", "FL"],
            "y": [10, 20, 12, 18],
        })
        self.assert_chart_ok("Stacked_Bar_Chart", df, {
            "x": "x", "y": "y", "series": "mode",
        })

    def test_stacked_recovers_logged_wide_mapping(self):
        df = pd.DataFrame({
            "date_key": ["2026-06-01", "2026-06-02"],
            "order_99pro": [10, 12],
            "order_cloud": [20, 18],
            "order_ol": [3, 4],
        })
        result = generate_chart(
            df=df,
            chart_type="Stacked_Bar_Chart",
            mapping={
                "color": "#003f5c,#2f9bca,#95c8d8",
                "series": "mode",
                "x": "date_key",
                "y": "order_99pro,order_cloud,order_ol",
            },
        )
        self.assertTrue(result.get("success"), result)
        self.assertIn('"barmode":"stack"', result["html"])
        self.assertIn('"name":"order_99pro"', result["html"])
        self.assertIn('"name":"order_cloud"', result["html"])
        self.assertIn('"name":"order_ol"', result["html"])

    def test_stacked_area_ignores_style_objects_and_splits_y_columns(self):
        df = pd.DataFrame({
            "date_key": ["2026-06-01", "2026-06-02", "2026-06-03"],
            "order_99pro": [10, 12, 11],
            "order_cloud": [20, 18, 19],
            "order_ol": [3, 4, 5],
        })
        self.assert_chart_ok("Stacked_Area_Chart", df, {
            "x": "date_key",
            "y": "order_99pro,order_cloud,order_ol",
            "series": [
                {"name": "99 Pro", "color": "#003f5c"},
                {"name": "Cloud/FL", "color": "#2f9bca"},
                {"name": "OL", "color": "#95c8d8"},
            ],
        })

    def test_grouped_accepts_categories_alias_and_style_objects(self):
        df = pd.DataFrame({
            "date_key": ["2026-06-01", "2026-06-02"],
            "order_99pro": [10, 12],
            "order_cloud": [20, 18],
            "order_ol": [3, 4],
        })
        self.assert_chart_ok("Grouped_Bar_Chart", df, {
            "categories": "date_key",
            "y": "order_99pro,order_cloud,order_ol",
            "series": [{"name": "99 Pro", "color": "#003f5c"}],
        })

    def test_line_ignores_style_objects_and_splits_y_columns(self):
        df = pd.DataFrame({
            "date_key": ["2026-06-01", "2026-06-02"],
            "share_99pro": [20.1, 21.2],
            "share_cloud": [75.0, 73.8],
            "share_ol": [4.9, 5.0],
        })
        self.assert_chart_ok("Line_Chart", df, {
            "x": "date_key",
            "y": "share_99pro,share_cloud,share_ol",
            "series": [{"name": "99 Pro份额", "color": "#003f5c"}],
        })


if __name__ == "__main__":
    unittest.main()
