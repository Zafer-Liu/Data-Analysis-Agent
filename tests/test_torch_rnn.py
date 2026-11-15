import unittest

import numpy as np
import pandas as pd

try:
    import torch  # noqa: F401 — optional dependency probe

    _TORCH_AVAILABLE = True
except ImportError:
    _TORCH_AVAILABLE = False

from Function.Analyze.registry import get as get_analysis


def _synthetic_monthly_df(n: int = 60, seed: int = 7) -> pd.DataFrame:
    """趋势 + 年度季节性 + 噪声的月度序列。"""
    rng = np.random.RandomState(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="MS")
    t = np.arange(n)
    values = 100 + 0.5 * t + 10 * np.sin(2 * np.pi * t / 12) + rng.normal(0, 1.5, n)
    return pd.DataFrame({"month": idx.strftime("%Y-%m"), "sales": values})


@unittest.skipUnless(_TORCH_AVAILABLE, "PyTorch 未安装（可选依赖 requirements-dl.txt）")
class TorchRNNTests(unittest.TestCase):
    """Torch_RNN：PyTorch GRU/LSTM 时序建模（M2）专项测试。"""

    def test_gru_forecast_default_cell(self):
        entry = get_analysis("Torch_RNN")
        result_df, loss_df, metrics_df, markdown = entry["run"](
            _synthetic_monthly_df(), target_column="sales"
        )

        forecast = result_df[result_df["segment"] == "forecast"]
        self.assertEqual(12, len(forecast))
        self.assertFalse(forecast["y_pred"].isna().any())
        self.assertTrue((forecast["lower_ci"] <= forecast["y_pred"]).all())
        self.assertTrue((forecast["y_pred"] <= forecast["upper_ci"]).all())
        self.assertEqual(list(range(1, len(loss_df) + 1)), list(loss_df["epoch"]))
        self.assertGreaterEqual(len(loss_df), 10)      # 完整训练轮次曲线
        metrics = set(metrics_df["metric"])
        self.assertIn("训练设备", metrics)
        self.assertIn("模型类型", metrics)
        model_type = str(metrics_df[metrics_df["metric"] == "模型类型"]["value"].iloc[0])
        self.assertIn("GRU", model_type)
        self.assertIn("PyTorch", markdown)

    def test_lstm_cell_selection(self):
        entry = get_analysis("Torch_RNN")
        result_df, _loss_df, metrics_df, _markdown = entry["run"](
            _synthetic_monthly_df(), target_column="sales", groupby_column="lstm"
        )

        model_type = str(metrics_df[metrics_df["metric"] == "模型类型"]["value"].iloc[0])
        self.assertIn("LSTM", model_type)
        self.assertEqual(12, len(result_df[result_df["segment"] == "forecast"]))

    def test_custom_steps_and_progress_callback(self):
        entry = get_analysis("Torch_RNN")
        messages: list[tuple[int, str]] = []
        result_df, *_ = entry["run"](
            _synthetic_monthly_df(), target_column="sales", n_deciles=6,
            progress_callback=lambda pct, msg: messages.append((pct, msg)),
        )

        self.assertEqual(6, len(result_df[result_df["segment"] == "forecast"]))
        self.assertTrue(messages)
        pcts = [pct for pct, _ in messages]
        self.assertEqual(pcts, sorted(pcts))            # 进度单调不减
        self.assertGreaterEqual(pcts[-1], 90)           # 训练完成前持续上报

    def test_missing_target_column_raises(self):
        entry = get_analysis("Torch_RNN")
        with self.assertRaises(ValueError):
            entry["run"](_synthetic_monthly_df(), target_column="missing")

    def test_non_numeric_target_raises(self):
        df = _synthetic_monthly_df()
        df["sales"] = df["sales"].astype(str)
        entry = get_analysis("Torch_RNN")
        with self.assertRaises(ValueError):
            entry["run"](df, target_column="sales")

    def test_insufficient_points_raises(self):
        entry = get_analysis("Torch_RNN")
        with self.assertRaises(ValueError):
            entry["run"](_synthetic_monthly_df(n=12), target_column="sales")


if __name__ == "__main__":
    unittest.main()
