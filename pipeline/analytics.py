"""Candlestick classification and walk-forward statistics for TQQQ.

All predictive statistics use only prices after the signal candle as the outcome;
patterns are classified without using future bars.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

HORIZONS = (1, 3, 5, 10)

PATTERN_SPECS: dict[str, dict[str, str]] = {
    "morning_star": {"label": "明けの明星", "bias": "bullish", "group": "multi_day"},
    "evening_star": {"label": "宵の明星", "bias": "bearish", "group": "multi_day"},
    "three_white_soldiers": {"label": "赤三兵", "bias": "bullish", "group": "multi_day"},
    "three_black_crows": {"label": "黒三兵", "bias": "bearish", "group": "multi_day"},
    "bullish_engulfing": {"label": "強気の包み足", "bias": "bullish", "group": "two_day"},
    "bearish_engulfing": {"label": "弱気の包み足", "bias": "bearish", "group": "two_day"},
    "piercing_line": {"label": "切り込み線", "bias": "bullish", "group": "two_day"},
    "dark_cloud_cover": {"label": "かぶせ線", "bias": "bearish", "group": "two_day"},
    "bullish_harami": {"label": "強気のはらみ足", "bias": "bullish", "group": "two_day"},
    "bearish_harami": {"label": "弱気のはらみ足", "bias": "bearish", "group": "two_day"},
    "hammer": {"label": "ハンマー", "bias": "bullish", "group": "single_day"},
    "hanging_man": {"label": "首吊り線", "bias": "bearish", "group": "single_day"},
    "inverted_hammer": {"label": "逆ハンマー", "bias": "bullish", "group": "single_day"},
    "shooting_star": {"label": "流れ星", "bias": "bearish", "group": "single_day"},
    "bullish_marubozu": {"label": "陽の丸坊主", "bias": "bullish", "group": "single_day"},
    "bearish_marubozu": {"label": "陰の丸坊主", "bias": "bearish", "group": "single_day"},
    "doji": {"label": "十字線", "bias": "neutral", "group": "single_day"},
    "spinning_top": {"label": "コマ", "bias": "neutral", "group": "single_day"},
    "bullish_candle": {"label": "陽線", "bias": "bullish", "group": "basic"},
    "bearish_candle": {"label": "陰線", "bias": "bearish", "group": "basic"},
}

# Stronger/more specific patterns take precedence when selecting the day's primary signal.
PATTERN_PRIORITY = [
    "morning_star", "evening_star", "three_white_soldiers", "three_black_crows",
    "bullish_engulfing", "bearish_engulfing", "piercing_line", "dark_cloud_cover",
    "bullish_harami", "bearish_harami", "hammer", "hanging_man", "inverted_hammer",
    "shooting_star", "bullish_marubozu", "bearish_marubozu", "doji", "spinning_top",
    "bullish_candle", "bearish_candle",
]


def _safe_float(value: Any) -> float:
    try:
        v = float(value)
        return v if math.isfinite(v) else float("nan")
    except (TypeError, ValueError):
        return float("nan")


def classify_candles(frame: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with pattern IDs and daily market-regime labels added."""
    if frame.empty:
        out = frame.copy()
        out["patterns"] = []
        out["regime"] = []
        return out

    out = frame.copy().sort_index()
    out.columns = [str(c).lower().replace(" ", "_") for c in out.columns]
    required = {"open", "high", "low", "close"}
    missing = required - set(out.columns)
    if missing:
        raise ValueError(f"OHLC columns missing: {', '.join(sorted(missing))}")

    o = pd.to_numeric(out["open"], errors="coerce").to_numpy(dtype=float)
    h = pd.to_numeric(out["high"], errors="coerce").to_numpy(dtype=float)
    l = pd.to_numeric(out["low"], errors="coerce").to_numpy(dtype=float)
    c = pd.to_numeric(out["close"], errors="coerce").to_numpy(dtype=float)
    n = len(out)
    patterns_by_row: list[list[str]] = [[] for _ in range(n)]

    def add(i: int, pattern: str) -> None:
        if 0 <= i < n and pattern not in patterns_by_row[i]:
            patterns_by_row[i].append(pattern)

    for i in range(n):
        vals = (o[i], h[i], l[i], c[i])
        if not all(math.isfinite(x) for x in vals) or h[i] < l[i] or min(vals) <= 0:
            continue
        candle_range = max(h[i] - l[i], 1e-12)
        body = abs(c[i] - o[i])
        upper = max(0.0, h[i] - max(o[i], c[i]))
        lower = max(0.0, min(o[i], c[i]) - l[i])
        body_ratio = body / candle_range
        upper_ratio = upper / candle_range
        lower_ratio = lower / candle_range

        if c[i] > o[i]:
            add(i, "bullish_candle")
        elif c[i] < o[i]:
            add(i, "bearish_candle")
        if body_ratio <= 0.10:
            add(i, "doji")
        elif body_ratio <= 0.30 and upper_ratio >= 0.15 and lower_ratio >= 0.15:
            add(i, "spinning_top")
        if body_ratio >= 0.80:
            add(i, "bullish_marubozu" if c[i] > o[i] else "bearish_marubozu")

        recent_trend = 0
        if i >= 3 and all(math.isfinite(x) for x in c[i-3:i+1]):
            change = c[i] - c[i-3]
            threshold = max(c[i-3] * 0.005, 0.01)
            recent_trend = 1 if change > threshold else (-1 if change < -threshold else 0)

        long_lower = lower >= max(body * 2.0, candle_range * 0.48) and upper <= candle_range * 0.25 and body_ratio <= 0.38
        long_upper = upper >= max(body * 2.0, candle_range * 0.48) and lower <= candle_range * 0.25 and body_ratio <= 0.38
        if long_lower:
            add(i, "hammer" if recent_trend <= 0 else "hanging_man")
        if long_upper:
            add(i, "inverted_hammer" if recent_trend <= 0 else "shooting_star")

        if i >= 1 and all(math.isfinite(x) for x in (o[i-1], c[i-1])):
            prev_bull = c[i-1] > o[i-1]
            curr_bull = c[i] > o[i]
            prev_body_low, prev_body_high = sorted((o[i-1], c[i-1]))
            curr_body_low, curr_body_high = sorted((o[i], c[i]))
            if prev_bull != curr_bull and curr_body_low <= prev_body_low and curr_body_high >= prev_body_high:
                add(i, "bullish_engulfing" if curr_bull else "bearish_engulfing")
            if curr_body_low >= prev_body_low and curr_body_high <= prev_body_high and abs(c[i-1]-o[i-1]) > 0:
                if not prev_bull and curr_bull:
                    add(i, "bullish_harami")
                elif prev_bull and not curr_bull:
                    add(i, "bearish_harami")
            prev_mid = (o[i-1] + c[i-1]) / 2
            if not prev_bull and curr_bull and o[i] < c[i-1] and c[i] > prev_mid and c[i] < o[i-1]:
                add(i, "piercing_line")
            if prev_bull and not curr_bull and o[i] > c[i-1] and c[i] < prev_mid and c[i] > o[i-1]:
                add(i, "dark_cloud_cover")

        if i >= 2:
            op, cp = o[i-2], c[i-2]
            om, cm = o[i-1], c[i-1]
            on, cn = o[i], c[i]
            if all(math.isfinite(x) for x in (op, cp, om, cm, on, cn)):
                b0, b1, b2 = abs(cp-op), abs(cm-om), abs(cn-on)
                if cp < op and b0 > 0 and b1 <= b0 * 0.55 and cn > on and cn >= (op+cp)/2:
                    add(i, "morning_star")
                if cp > op and b0 > 0 and b1 <= b0 * 0.55 and cn < on and cn <= (op+cp)/2:
                    add(i, "evening_star")
                if cp > op and cm > om and cn > on and cp < cm < cn and b0 > 0 and b1 > 0 and b2 > 0:
                    add(i, "three_white_soldiers")
                if cp < op and cm < om and cn < on and cp > cm > cn and b0 > 0 and b1 > 0 and b2 > 0:
                    add(i, "three_black_crows")

    out["patterns"] = patterns_by_row
    out["upper_shadow_pct"] = [
        round(max(0.0, h[i] - max(o[i], c[i])) / max(h[i]-l[i], 1e-12) * 100, 2)
        if all(math.isfinite(x) for x in (o[i], h[i], l[i], c[i])) else None for i in range(n)
    ]
    out["lower_shadow_pct"] = [
        round(max(0.0, min(o[i], c[i]) - l[i]) / max(h[i]-l[i], 1e-12) * 100, 2)
        if all(math.isfinite(x) for x in (o[i], h[i], l[i], c[i])) else None for i in range(n)
    ]
    out["body_pct"] = [
        round(abs(c[i]-o[i]) / max(h[i]-l[i], 1e-12) * 100, 2)
        if all(math.isfinite(x) for x in (o[i], h[i], l[i], c[i])) else None for i in range(n)
    ]

    # Context is deliberately deterministic and built only from market data known at each date.
    qqq_ret5 = pd.to_numeric(out.get("qqq_close", pd.Series(index=out.index, dtype=float)), errors="coerce").pct_change(5)
    vix = pd.to_numeric(out.get("vix_close", pd.Series(index=out.index, dtype=float)), errors="coerce")
    vix_ret5 = vix.pct_change(5)
    rolling_vol = pd.to_numeric(out["close"], errors="coerce").pct_change().rolling(20, min_periods=15).std() * np.sqrt(252) * 100
    regimes: list[str] = []
    for i in range(n):
        qret = _safe_float(qqq_ret5.iloc[i])
        v = _safe_float(vix.iloc[i])
        vret = _safe_float(vix_ret5.iloc[i])
        vol = _safe_float(rolling_vol.iloc[i])
        if (math.isfinite(v) and v >= 30) or (math.isfinite(qret) and qret <= -0.04):
            regimes.append("risk_off")
        elif (math.isfinite(v) and v >= 25) or (math.isfinite(vret) and vret >= 0.20) or (math.isfinite(vol) and vol >= 80):
            regimes.append("high_volatility")
        elif math.isfinite(qret) and qret >= 0.03 and (not math.isfinite(v) or v < 25):
            regimes.append("risk_on")
        else:
            regimes.append("neutral")
    out["regime"] = regimes
    return out


def _wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1 + z*z/n
    center = (p + z*z/(2*n)) / denom
    margin = z * math.sqrt((p*(1-p) + z*z/(4*n)) / n) / denom
    return max(0.0, center-margin), min(1.0, center+margin)


def confidence_grade(n: int, successes: int, baseline: float = 0.5, neutral: bool = False) -> dict[str, Any]:
    """Conservative grade for reproducibility; not an investment probability."""
    if n <= 0:
        return {"grade": "D", "label": "データ不足", "lower_95_pct": None, "upper_95_pct": None, "n": 0}
    low, high = _wilson(successes, n)
    hit = successes / n
    # Neutral patterns are graded against the historical frequency of small absolute returns,
    # not against a directional 50% chance threshold.
    if neutral:
        if n >= 100 and low > baseline + 0.02:
            grade, label = "A", "比較的強い根拠"
        elif n >= 40 and low > baseline:
            grade, label = "B", "一定の根拠"
        elif n >= 10:
            grade, label = "C", "限定的な根拠"
        else:
            grade, label = "D", "データ不足"
    elif n >= 100 and low > max(0.52, baseline + 0.02):
        grade, label = "A", "比較的強い根拠"
    elif n >= 40 and low > max(0.50, baseline):
        grade, label = "B", "一定の根拠"
    elif n >= 10:
        grade, label = "C", "限定的な根拠"
    else:
        grade, label = "D", "データ不足"
    return {
        "grade": grade,
        "label": label,
        "hit_rate_pct": round(hit * 100, 2),
        "lower_95_pct": round(low * 100, 2),
        "upper_95_pct": round(high * 100, 2),
        "n": int(n),
    }


def _direction_for(pattern_id: str, mean_return: float) -> str:
    # Direction for neutral patterns is fixed by definition, never inferred after seeing future returns.
    return PATTERN_SPECS.get(pattern_id, {}).get("bias", "neutral")


def _stats_for_returns(
    pattern_id: str, returns: list[float], baseline_up_pct: float | None = None,
    baseline_flat_pct: float | None = None,
) -> dict[str, Any]:
    clean = [float(x) for x in returns if math.isfinite(float(x))]
    if not clean:
        return {
            "n": 0, "up_count": 0, "down_count": 0, "flat_count": 0,
            "up_rate_pct": None, "down_rate_pct": None, "avg_return_pct": None,
            "median_return_pct": None, "avg_up_pct": None, "avg_down_pct": None,
            "expected_direction": "neutral", "hit_rate_pct": None,
            "grade": "D", "grade_label": "データ不足", "ci95_low_pct": None,
            "ci95_high_pct": None, "min_return_pct": None, "max_return_pct": None,
        }
    ups = [x for x in clean if x > 0]
    downs = [x for x in clean if x < 0]
    flats = len(clean) - len(ups) - len(downs)
    mean_ret = float(np.mean(clean))
    direction = _direction_for(pattern_id, mean_ret)
    if direction == "bullish":
        successes = len(ups)
        baseline = (baseline_up_pct / 100) if baseline_up_pct is not None else 0.5
    elif direction == "bearish":
        successes = len(downs)
        baseline = (1 - baseline_up_pct / 100) if baseline_up_pct is not None else 0.5
    else:
        successes = sum(abs(x) <= 0.5 for x in clean)
        baseline = (baseline_flat_pct / 100) if baseline_flat_pct is not None else 0.1
    grade_data = confidence_grade(len(clean), successes, baseline, neutral=(direction == "neutral"))
    return {
        "n": len(clean),
        "up_count": len(ups),
        "down_count": len(downs),
        "flat_count": flats,
        "up_rate_pct": round(len(ups) / len(clean) * 100, 2),
        "down_rate_pct": round(len(downs) / len(clean) * 100, 2),
        "avg_return_pct": round(mean_ret, 4),
        "median_return_pct": round(float(np.median(clean)), 4),
        "avg_up_pct": round(float(np.mean(ups)), 4) if ups else None,
        "avg_down_pct": round(float(np.mean(downs)), 4) if downs else None,
        "expected_direction": direction,
        "hit_rate_pct": round(successes / len(clean) * 100, 2),
        "grade": grade_data["grade"],
        "grade_label": grade_data["label"],
        "ci95_low_pct": grade_data["lower_95_pct"],
        "ci95_high_pct": grade_data["upper_95_pct"],
        "min_return_pct": round(min(clean), 4),
        "max_return_pct": round(max(clean), 4),
    }


def compute_pattern_statistics(frame: pd.DataFrame) -> dict[str, Any]:
    """Stats per candle pattern and forward horizon, using full available history."""
    if frame.empty:
        return {"patterns": [], "baseline": {str(h): None for h in HORIZONS}}
    close = pd.to_numeric(frame["close"], errors="coerce")
    baseline: dict[str, float | None] = {}
    baseline_flat: dict[str, float | None] = {}
    for h in HORIZONS:
        ret = ((close.shift(-h) / close) - 1) * 100
        clean = ret.dropna()
        baseline[str(h)] = round(float((clean > 0).mean() * 100), 2) if len(clean) else None
        baseline_flat[str(h)] = round(float((clean.abs() <= 0.5).mean() * 100), 2) if len(clean) else None

    # List all occurrence indices per type.
    occ: dict[str, list[int]] = {key: [] for key in PATTERN_SPECS}
    for i, row_patterns in enumerate(frame["patterns"].tolist()):
        for p in row_patterns if isinstance(row_patterns, list) else []:
            if p in occ:
                occ[p].append(i)

    result: list[dict[str, Any]] = []
    for pattern_id, indices in occ.items():
        horizons: dict[str, Any] = {}
        valid_indices: list[int] = []
        for idx in indices:
            if idx < len(frame) - 1:
                valid_indices.append(idx)
        for h in HORIZONS:
            returns = []
            for idx in indices:
                if idx + h < len(frame):
                    p0 = _safe_float(close.iloc[idx])
                    p1 = _safe_float(close.iloc[idx+h])
                    if math.isfinite(p0) and math.isfinite(p1) and p0 > 0:
                        returns.append((p1 / p0 - 1) * 100)
            stat = _stats_for_returns(pattern_id, returns, baseline.get(str(h)), baseline_flat.get(str(h)))
            horizons[str(h)] = stat
        occurrence_records = []
        for idx in indices[::-1]:
            date = frame.index[idx]
            date_text = pd.Timestamp(date).strftime("%Y-%m-%d")
            close_val = _safe_float(close.iloc[idx])
            forward = {}
            for h in HORIZONS:
                if idx+h < len(frame) and close_val > 0:
                    forward[str(h)] = round((_safe_float(close.iloc[idx+h]) / close_val - 1) * 100, 4)
                else:
                    forward[str(h)] = None
            occurrence_records.append({
                "date": date_text,
                "close": round(close_val, 4) if math.isfinite(close_val) else None,
                "regime": str(frame["regime"].iloc[idx]) if "regime" in frame else "neutral",
                "forward_returns_pct": forward,
            })
        result.append({
            "id": pattern_id,
            **PATTERN_SPECS[pattern_id],
            "occurrences": len(indices),
            "frequency_pct": round(len(indices) / len(frame) * 100, 2),
            "latest_date": occurrence_records[0]["date"] if occurrence_records else None,
            "horizons": horizons,
            "recent_occurrences": occurrence_records[:60],
            "occurrence_history": occurrence_records,
        })
    result.sort(key=lambda x: (PATTERN_PRIORITY.index(x["id"]) if x["id"] in PATTERN_PRIORITY else 999, x["label"]))
    return {"patterns": result, "baseline": baseline, "baseline_flat": baseline_flat}


def context_statistics(frame: pd.DataFrame, pattern_id: str, regime: str, horizon: int = 5) -> dict[str, Any]:
    """Historical results for the same pattern under a matching market regime."""
    if frame.empty or "patterns" not in frame or "regime" not in frame:
        return _stats_for_returns(pattern_id, [])
    close = pd.to_numeric(frame["close"], errors="coerce")
    returns: list[float] = []
    for i, row_patterns in enumerate(frame["patterns"].tolist()):
        if i + horizon >= len(frame) or str(frame["regime"].iloc[i]) != regime:
            continue
        if not isinstance(row_patterns, list) or pattern_id not in row_patterns:
            continue
        p0 = _safe_float(close.iloc[i])
        p1 = _safe_float(close.iloc[i+horizon])
        if math.isfinite(p0) and math.isfinite(p1) and p0 > 0:
            returns.append((p1 / p0 - 1) * 100)
    base_series = ((close.shift(-horizon) / close) - 1) * 100
    baseline_up = float((base_series.dropna() > 0).mean() * 100) if len(base_series.dropna()) else None
    baseline_flat = float((base_series.dropna().abs() <= 0.5).mean() * 100) if len(base_series.dropna()) else None
    return _stats_for_returns(pattern_id, returns, baseline_up, baseline_flat)


def primary_pattern(patterns: list[str]) -> str | None:
    for candidate in PATTERN_PRIORITY:
        if candidate in patterns:
            return candidate
    return patterns[0] if patterns else None


def serialize_bars(frame: pd.DataFrame, limit: int | None = 730) -> list[dict[str, Any]]:
    """Compact JSON-friendly candle rows for charting and date lookup."""
    if frame.empty:
        return []
    data = frame.tail(limit) if limit is not None else frame
    rows = []
    close_all = pd.to_numeric(frame["close"], errors="coerce")
    for ix, row in data.iterrows():
        index_pos = frame.index.get_loc(ix)
        item = {"date": pd.Timestamp(ix).strftime("%Y-%m-%d")}
        for col in ("open", "high", "low", "close"):
            val = _safe_float(row.get(col))
            item[col] = round(val, 4) if math.isfinite(val) else None
        vol = _safe_float(row.get("volume"))
        item["volume"] = int(vol) if math.isfinite(vol) and vol >= 0 else None
        item["patterns"] = row.get("patterns", []) if isinstance(row.get("patterns", []), list) else []
        item["regime"] = str(row.get("regime", "neutral"))
        fwd = {}
        base_close = _safe_float(close_all.iloc[index_pos])
        for horizon in HORIZONS:
            future_pos = index_pos + horizon
            if future_pos < len(frame) and math.isfinite(base_close) and base_close > 0:
                future_close = _safe_float(close_all.iloc[future_pos])
                fwd[str(horizon)] = round((future_close / base_close - 1) * 100, 4) if math.isfinite(future_close) else None
            else:
                fwd[str(horizon)] = None
        item["forward_returns_pct"] = fwd
        for col, name in (("qqq_close", "qqq_close"), ("vix_close", "vix_close"),
                          ("body_pct", "body_pct"), ("upper_shadow_pct", "upper_shadow_pct"),
                          ("lower_shadow_pct", "lower_shadow_pct")):
            val = _safe_float(row.get(col))
            item[name] = round(val, 4) if math.isfinite(val) else None
        rows.append(item)
    return rows
