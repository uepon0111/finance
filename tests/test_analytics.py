import unittest

import numpy as np
import pandas as pd

from pipeline.analytics import (
    classify_candles,
    compute_pattern_statistics,
    confidence_grade,
    context_statistics,
    serialize_bars,
)


class CandleClassificationTests(unittest.TestCase):
    def setUp(self):
        self.index = pd.date_range("2024-01-02", periods=7, freq="B")
        self.frame = pd.DataFrame(
            {
                "open":   [12.0, 11.0, 10.0, 9.5, 9.4, 10.0, 10.0],
                "high":   [12.4, 11.3, 10.2, 9.8, 10.2, 10.6, 10.5],
                "low":    [11.8, 10.7, 9.7, 8.0, 9.3, 9.8, 9.5],
                "close":  [11.0, 10.1, 9.8, 9.4, 10.0, 10.5, 10.02],
                "volume": [100, 110, 120, 130, 140, 150, 160],
                "qqq_close": [400, 398, 395, 392, 397, 399, 400],
                "vix_close": [18, 19, 22, 24, 22, 20, 19],
            },
            index=self.index,
        )

    def test_detects_expected_single_and_multi_day_shapes(self):
        result = classify_candles(self.frame)
        self.assertIn("hammer", result.iloc[3]["patterns"])
        self.assertIn("bullish_engulfing", result.iloc[4]["patterns"])
        # Last candle has a very small body relative to its full range.
        self.assertIn("doji", result.iloc[6]["patterns"])
        self.assertTrue(set(result["regime"]).issubset({"risk_on", "risk_off", "high_volatility", "neutral"}))

    def test_past_classification_does_not_depend_on_future_bars(self):
        original = classify_candles(self.frame)
        changed = self.frame.copy()
        changed.iloc[6, changed.columns.get_loc("open")] = 5
        changed.iloc[6, changed.columns.get_loc("high")] = 20
        changed.iloc[6, changed.columns.get_loc("low")] = 4
        changed.iloc[6, changed.columns.get_loc("close")] = 19
        recalculated = classify_candles(changed)
        for i in range(6):
            self.assertEqual(original.iloc[i]["patterns"], recalculated.iloc[i]["patterns"])


class StatisticsTests(unittest.TestCase):
    def setUp(self):
        idx = pd.date_range("2023-01-02", periods=25, freq="B")
        close = [100.0]
        changes = [1.0, -0.5, 1.5, 0.2, -1.0, 1.2, 0.4, -0.3, 1.0, 0.1,
                   -0.8, 1.6, 0.5, -0.4, 1.1, 0.9, -0.2, 1.4, 0.2, -0.7,
                   1.3, 0.4, -0.6, 1.0]
        for move in changes:
            close.append(close[-1] * (1 + move / 100))
        self.frame = pd.DataFrame({
            "open": np.array(close[:-1]),
            "high": np.maximum(close[:-1], close[1:]) * 1.005,
            "low": np.minimum(close[:-1], close[1:]) * .995,
            "close": close[1:],
            "patterns": [["bullish_candle"] if i % 2 == 0 else ["bearish_candle"] for i in range(24)],
            "regime": ["neutral" if i % 3 else "risk_on" for i in range(24)],
        }, index=idx[1:])

    def test_forward_returns_and_pattern_statistics(self):
        stats = compute_pattern_statistics(self.frame)
        self.assertEqual(len(stats["patterns"]), 20)
        bullish = next(item for item in stats["patterns"] if item["id"] == "bullish_candle")
        self.assertGreater(bullish["occurrences"], 0)
        self.assertGreaterEqual(bullish["horizons"]["5"]["n"], 0)
        self.assertIn(bullish["horizons"]["5"]["grade"], {"A", "B", "C", "D"})
        bars = serialize_bars(self.frame, 10)
        self.assertIn("forward_returns_pct", bars[0])
        self.assertIsNone(bars[-1]["forward_returns_pct"]["1"])

    def test_context_statistics_only_uses_same_regime_and_pattern(self):
        result = context_statistics(self.frame, "bullish_candle", "risk_on", 3)
        self.assertGreater(result["n"], 0)
        self.assertLessEqual(result["n"], int(sum("bullish_candle" in x for x in self.frame["patterns"])))

    def test_grade_is_conservative_for_small_samples(self):
        self.assertEqual(confidence_grade(3, 3)["grade"], "D")
        self.assertEqual(confidence_grade(15, 12)["grade"], "C")
        self.assertIn(confidence_grade(120, 100)["grade"], {"A", "B", "C"})


if __name__ == "__main__":
    unittest.main()
