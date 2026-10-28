"""Tests for history evaluation, event matching, and safe Gemini source validation."""
from __future__ import annotations

import importlib.util
import json
import sys
import types
import unittest
from unittest.mock import patch

import pandas as pd

# Keep these helper tests runnable offline without importing/installing yfinance.
try:
    import yfinance  # noqa: F401
except ImportError:  # pragma: no cover - local offline test environment only
    stub = types.ModuleType("yfinance")
    stub.Ticker = lambda *_args, **_kwargs: None
    sys.modules.setdefault("yfinance", stub)

from pipeline.collect import (  # noqa: E402
    evaluate_gemini_history,
    event_conditioned_statistics,
    gemini_assessment,
    update_snapshot_outcomes,
)


class PipelineHistoryTests(unittest.TestCase):
    def test_event_statistics_match_pattern_regime_and_news_categories(self):
        rows = [
            {"primary_pattern_id": "hammer", "market_regime": "risk_off", "event_categories": ["inflation"], "forward_returns_pct": {"5": 2.0}},
            {"primary_pattern_id": "hammer", "market_regime": "risk_off", "event_categories": ["inflation", "monetary_policy"], "forward_returns_pct": {"5": 1.0}},
            {"primary_pattern_id": "hammer", "market_regime": "risk_off", "event_categories": ["inflation"], "forward_returns_pct": {"5": -1.0}},
            {"primary_pattern_id": "hammer", "market_regime": "risk_on", "event_categories": ["inflation"], "forward_returns_pct": {"5": 5.0}},
            {"primary_pattern_id": "doji", "market_regime": "risk_off", "event_categories": ["inflation"], "forward_returns_pct": {"5": 4.0}},
            {"primary_pattern_id": "hammer", "market_regime": "risk_off", "event_categories": ["earnings"], "forward_returns_pct": {"5": 3.0}},
        ]
        result = event_conditioned_statistics(
            {"snapshots": rows}, "hammer", "risk_off", {"inflation"}, 55.0, 8.0, 5
        )
        self.assertEqual(result["n"], 3)
        self.assertEqual(result["up_rate_pct"], 66.67)
        self.assertEqual(result["down_rate_pct"], 33.33)
        self.assertEqual(result["categories_matched"], ["inflation"])

    def test_update_snapshot_outcomes_and_walk_forward_accuracy(self):
        index = pd.bdate_range("2026-01-05", periods=6)
        closes = pd.Series([100.0, 101.0, 99.0, 103.0, 104.0, 106.0], index=index)
        history = {"snapshots": [{
            "as_of_date": index[0].strftime("%Y-%m-%d"),
            "gemini": {"status": "ok", "direction": "bullish", "horizon_sessions": 5},
            "forward_returns_pct": {},
        }]}
        result = update_snapshot_outcomes(history, closes)
        snap = result["snapshots"][0]
        self.assertAlmostEqual(snap["forward_returns_pct"]["1"], 1.0, places=4)
        self.assertAlmostEqual(snap["forward_returns_pct"]["5"], 6.0, places=4)
        self.assertTrue(snap["gemini_validation"]["tested"])
        self.assertTrue(snap["gemini_validation"]["hit"])
        evaluation = evaluate_gemini_history(result)
        self.assertEqual(evaluation["overall"]["tested"], 1)
        self.assertEqual(evaluation["overall"]["accuracy_pct"], 100.0)
        self.assertEqual(evaluation["by_horizon"]["10"]["tested"], 0)

    def test_gemini_fails_over_and_only_accepts_ids_sent_in_prompt(self):
        news = [{
            "id": f"event-{i}", "title": f"Headline {i}", "url": f"https://example.com/{i}",
            "published_at": "2026-10-27T12:00:00Z", "source": "Example Source", "categories": ["technology"],
            "keyword_tone": "mixed_or_unknown",
        } for i in range(20)]
        payload = {
            "candidates": [{"content": {"parts": [{"text": json.dumps({
                "direction": "bullish", "confidence_pct": 63,
                "summary": "参考所見です。", "reasons": ["統計と整合"],
                "risk_factors": ["サンプル数に注意"],
                "cited_source_ids": ["event-1", "event-19", "untrusted-id"],
            }, ensure_ascii=False)}]}}]
        }

        class FakeResponse:
            def __init__(self, status_code, obj=None):
                self.status_code = status_code
                self._obj = obj

            def json(self):
                return self._obj

        with patch("pipeline.collect.requests.post", side_effect=[FakeResponse(429), FakeResponse(200, payload)]) as post:
            result = gemini_assessment(
                ["authorized-key-slot-1", "authorized-key-slot-2"], "gemini-3.8-flash",
                {"date": "2026-10-27", "close": 55, "change_pct": 1.2}, ["hammer"], {}, {}, news, "neutral",
            )
        self.assertEqual(post.call_count, 2)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["key_slot_used"], 2)
        # Gemini only received the first 18 article records, so later or invented IDs are rejected.
        self.assertEqual(result["cited_source_ids"], ["event-1"])


if __name__ == "__main__":
    unittest.main()
