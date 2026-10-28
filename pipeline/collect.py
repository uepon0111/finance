"""Daily data pipeline for the TQQQ Candle Lab GitHub Pages site."""
from __future__ import annotations

import json
import hashlib
import logging
import math
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf

try:
    import feedparser
except ImportError:  # fail soft; price analytics should still run
    feedparser = None

try:  # Works both as `python pipeline/collect.py` and as an imported package in tests.
    from .analytics import (
        HORIZONS, PATTERN_SPECS, classify_candles, compute_pattern_statistics,
        context_statistics, confidence_grade, primary_pattern, serialize_bars,
    )
except ImportError:  # pragma: no cover - used by direct script execution
    from analytics import (
        HORIZONS, PATTERN_SPECS, classify_candles, compute_pattern_statistics,
        context_statistics, confidence_grade, primary_pattern, serialize_bars,
    )

ROOT = Path(__file__).resolve().parents[1]
SITE_DATA = ROOT / "site" / "data"
LATEST_PATH = SITE_DATA / "latest.json"
HISTORY_PATH = SITE_DATA / "history.json"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"
TZ_NY = ZoneInfo("America/New_York")
UTC = timezone.utc

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(levelname)s: %(message)s")
log = logging.getLogger("tqqq-candle-lab")

NEWS_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "monetary_policy": ("federal reserve", "fed", "interest rate", "rate cut", "rate hike", "powell", "treasury yield", "bond yield", "fomc"),
    "inflation": ("inflation", "cpi", "ppi", "pce", "consumer price", "producer price"),
    "employment": ("jobs report", "unemployment", "nonfarm payroll", "payrolls", "employment data", "jobless claims"),
    "technology": ("nvidia", "semiconductor", "chipmaker", "artificial intelligence", " ai ", "nasdaq-100", "big tech"),
    "geopolitics_trade": ("tariff", "trade war", "sanction", "geopolit", "iran", "israel", "ukraine", "middle east", "china trade"),
    "earnings": ("earnings", "revenue", "guidance", "quarterly results", "profit warning"),
    "market_volatility": ("selloff", "sell-off", "market crash", "risk-off", "risk off", "volatility", "market rally", "record high", "drawdown"),
    "macro_growth": ("recession", "gdp", "economic growth", "consumer spending", "manufacturing", "business activity"),
}
POSITIVE_WORDS = ("beats estimates", "beat estimates", "surges", "rally", "rallies", "record high", "rate cut", "cooling inflation", "strong earnings", "upgrade")
NEGATIVE_WORDS = ("misses estimates", "missed estimates", "plunges", "selloff", "sell-off", "downgrade", "recession", "war escalates", "tariffs rise", "hot inflation", "rate hike")


def now_utc_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    tmp.replace(path)


def fetch_symbol(symbol: str) -> pd.DataFrame:
    """Download max available daily adjusted OHLCV; yfinance may occasionally change column shapes."""
    log.info("Fetching daily data for %s through yfinance", symbol)
    df = yf.Ticker(symbol).history(period="max", interval="1d", auto_adjust=True, actions=False, raise_errors=True)
    if df is None or df.empty:
        raise RuntimeError(f"No data returned for {symbol}")
    if isinstance(df.columns, pd.MultiIndex):
        # Defensive normalization for upstream yfinance shape changes.
        df.columns = [str(c[0] if isinstance(c, tuple) else c) for c in df.columns]
    df.columns = [str(c).lower() for c in df.columns]
    required = ["open", "high", "low", "close"]
    if any(c not in df.columns for c in required):
        raise RuntimeError(f"Unexpected data columns for {symbol}")
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_localize(None)
    df.index = pd.to_datetime(df.index).normalize()
    keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    result = df[keep].copy()
    result = result[~result.index.duplicated(keep="last")].sort_index()
    result = result.apply(pd.to_numeric, errors="coerce")
    result = result.dropna(subset=required)
    if len(result) < 50:
        raise RuntimeError(f"Insufficient rows for {symbol}: {len(result)}")
    return result


def _parse_published(entry: Any) -> str | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        try:
            import calendar
            return datetime.fromtimestamp(calendar.timegm(parsed), UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        except (OverflowError, TypeError, ValueError):
            pass
    raw = entry.get("published") or entry.get("updated")
    if raw:
        try:
            stamp = pd.to_datetime(raw, utc=True, errors="coerce")
            if not pd.isna(stamp):
                return stamp.to_pydatetime().replace(microsecond=0).isoformat().replace("+00:00", "Z")
        except Exception:
            return None
    return None


def classify_headline(title: str) -> tuple[list[str], str]:
    lower = f" {title.lower()} "
    categories = [category for category, keys in NEWS_CATEGORY_KEYWORDS.items() if any(k in lower for k in keys)]
    positive = sum(1 for word in POSITIVE_WORDS if word in lower)
    negative = sum(1 for word in NEGATIVE_WORDS if word in lower)
    if positive > negative:
        tone = "positive_keyword_signal"
    elif negative > positive:
        tone = "negative_keyword_signal"
    else:
        tone = "mixed_or_unknown"
    return categories, tone


def fetch_news(max_items: int = 35) -> list[dict[str, Any]]:
    """Fetch attributable headlines from Google News RSS; headlines are not treated as verified facts."""
    if feedparser is None:
        log.warning("feedparser unavailable; skipping news context")
        return []
    terms = [
        'TQQQ OR "Nasdaq-100" OR Nasdaq technology stocks',
        'Federal Reserve OR inflation OR Treasury yields US markets',
        'semiconductor AI tariffs US stock market risk',
    ]
    after = (datetime.now(UTC).date() - timedelta(days=90)).isoformat()
    before = (datetime.now(UTC).date() + timedelta(days=1)).isoformat()
    articles: dict[str, dict[str, Any]] = {}
    for q in terms:
        query = f"{q} after:{after} before:{before}"
        url = "https://news.google.com/rss/search?q=" + quote_plus(query) + "&hl=en-US&gl=US&ceid=US:en"
        try:
            res = requests.get(url, timeout=18, headers={"User-Agent": "TQQQ-Candle-Lab/1.0 (RSS reader)"})
            res.raise_for_status()
            feed = feedparser.parse(res.content)
        except Exception as exc:
            log.warning("A news query failed (%s); continuing with other sources", type(exc).__name__)
            continue
        for entry in feed.entries:
            title = re.sub(r"\s+", " ", str(entry.get("title", ""))).strip()
            link = str(entry.get("link", "")).strip()
            if not title or not link or link in articles:
                continue
            published = _parse_published(entry)
            source_obj = entry.get("source") or {}
            source_name = str(source_obj.get("title", "Google News RSS"))[:120]
            cats, tone = classify_headline(title)
            articles[link] = {
                "id": "news-" + str(len(articles) + 1),
                "title": title[:400],
                "url": link,
                "published_at": published,
                "published_date": published[:10] if published else None,
                "source": source_name,
                "categories": cats,
                "keyword_tone": tone,
                "tone_method": "headline keyword heuristic; not verified sentiment",
            }
    sorted_articles = sorted(articles.values(), key=lambda x: x.get("published_at") or "", reverse=True)
    selected = sorted_articles[:max_items]
    # Reassign stable IDs after sorting so AI citations refer to the visible items.
    for item in selected:
        item["id"] = "event-" + hashlib.sha256(item["url"].encode("utf-8")).hexdigest()[:12]
    log.info("Collected %d recent headline references", len(selected))
    return selected


def _gemini_keys() -> list[str]:
    raw = os.getenv("GEMINI_API_KEYS", "").strip()
    if not raw:
        single = os.getenv("GEMINI_API_KEY", "").strip()
        return [single] if single else []
    try:
        value = json.loads(raw)
        if isinstance(value, list):
            return list(dict.fromkeys(str(x).strip() for x in value if str(x).strip()))
    except json.JSONDecodeError:
        pass
    return list(dict.fromkeys(x.strip() for x in raw.split(",") if x.strip()))


def _parse_json_response(text: str) -> dict[str, Any]:
    txt = text.strip()
    txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt, flags=re.IGNORECASE)
    match = re.search(r"\{.*\}", txt, flags=re.DOTALL)
    if not match:
        raise ValueError("Gemini response did not contain a JSON object")
    value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("Gemini response JSON is not an object")
    return value


def gemini_assessment(
    keys: list[str], model: str, quote: dict[str, Any], current_patterns: list[str],
    statistics: dict[str, Any], context: dict[str, Any], news: list[dict[str, Any]],
    regime: str,
) -> dict[str, Any]:
    if not keys:
        return {"status": "not_configured", "grade": "D", "grade_label": "Gemini未設定", "direction": "unknown",
                "confidence_pct": None, "summary": "Gemini APIキーが設定されていないため、AI検証は未実施です。",
                "reasons": [], "risk_factors": [], "cited_source_ids": [], "model": None, "evaluated_at": now_utc_iso()}

    evidence = []
    for article in news[:18]:
        evidence.append({
            "source_id": article["id"], "title": article["title"],
            "published_at": article.get("published_at"), "source": article.get("source"),
            "url": article["url"], "categories": article.get("categories", []),
            "headline_keyword_tone": article.get("keyword_tone"),
        })
    payload = {
        "ticker": "TQQQ",
        "as_of_date": quote.get("date"),
        "close_adjusted_usd": quote.get("close"),
        "daily_change_pct": quote.get("change_pct"),
        "market_regime": regime,
        "today_patterns": [{"id": p, "label": PATTERN_SPECS.get(p, {}).get("label", p), "bias": PATTERN_SPECS.get(p, {}).get("bias", "neutral")} for p in current_patterns],
        "historical_statistics_5_sessions": statistics,
        "historical_same_regime_statistics_5_sessions": context,
        "recent_news_source_records": evidence,
    }
    prompt = """あなたは市場データ検証アシスタントです。TQQQはNasdaq-100の日次リターンの3倍を目指すレバレッジETFです。投資助言や売買指示ではなく、根拠の限界を明示した検証レポートを作成してください。
以下のJSON内の情報だけを根拠として使い、外部事実・URL・日付を創作しないでください。ニュース見出しは事実確定ではなく、出典を開いて確認が必要な参考情報です。統計的サンプル数、勝率、平均リターン、95%区間、同じ市場局面のサンプル数を区別してください。データ不足や相反する根拠がある場合は確信度を下げてください。過去の成績は将来を保証しません。
JSONだけを返してください。スキーマ:
{"direction":"bullish|bearish|neutral|unknown","confidence_pct":0-100,"summary":"日本語で2文以内","reasons":["日本語の根拠"],"risk_factors":["日本語のリスク"],"cited_source_ids":["入力にあるsource_idのみ"],"horizon_sessions":5}
入力データ:
""" + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    model_name = model.strip() or "gemini-3.8-flash"
    last_status: int | None = None
    for key_index, key in enumerate(keys, 1):
        endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{quote_plus(model_name)}:generateContent"
        try:
            response = requests.post(
                endpoint,
                params={"key": key},
                json={
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.15, "responseMimeType": "application/json"},
                },
                timeout=55,
            )
        except requests.RequestException as exc:
            log.warning("Gemini network error for configured key slot %d (%s)", key_index, type(exc).__name__)
            continue
        if response.status_code != 200:
            last_status = response.status_code
            log.warning("Gemini request failed with HTTP %d for configured key slot %d", response.status_code, key_index)
            # Failover is only across keys/projects the owner is authorized to use and subject to each quota/terms.
            if response.status_code in (401, 403, 429, 500, 502, 503, 504):
                continue
            break
        try:
            obj = response.json()
            parts = obj["candidates"][0]["content"]["parts"]
            text = "\n".join(str(part.get("text", "")) for part in parts)
            parsed = _parse_json_response(text)
        except (ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError):
            log.warning("Gemini response could not be parsed; not using unvalidated output")
            return {"status": "invalid_response", "grade": "D", "grade_label": "AI応答を検証できず", "direction": "unknown",
                    "confidence_pct": None, "summary": "Geminiの応答形式を検証できなかったため、結果を採用していません。",
                    "reasons": [], "risk_factors": [], "cited_source_ids": [], "model": model_name, "evaluated_at": now_utc_iso()}
        allowed_ids = {x["id"] for x in news[:18]}  # only sources actually supplied to Gemini are eligible
        citations = [x for x in parsed.get("cited_source_ids", []) if isinstance(x, str) and x in allowed_ids]
        direction = parsed.get("direction") if parsed.get("direction") in ("bullish", "bearish", "neutral", "unknown") else "unknown"
        try:
            confidence = int(max(0, min(100, float(parsed.get("confidence_pct", 0)))))
        except (TypeError, ValueError):
            confidence = 0
        def safe_strings(k: str, max_items: int = 5) -> list[str]:
            value = parsed.get(k, [])
            if not isinstance(value, list):
                return []
            return [str(x)[:500] for x in value[:max_items] if isinstance(x, (str, int, float))]
        return {
            "status": "ok", "grade": "pending_calibration", "grade_label": "評価中",
            "direction": direction, "confidence_pct": confidence,
            "summary": str(parsed.get("summary", "AIによる要約はありません。"))[:1000],
            "reasons": safe_strings("reasons"), "risk_factors": safe_strings("risk_factors"),
            "cited_source_ids": citations, "model": model_name, "key_slot_used": key_index,
            "evaluated_at": now_utc_iso(), "horizon_sessions": 5,
        }
    return {"status": "rate_limited_or_unavailable", "grade": "D", "grade_label": "Gemini検証なし", "direction": "unknown",
            "confidence_pct": None, "summary": "設定済みGeminiキーで検証できませんでした。統計データ自体は表示しています。",
            "reasons": [], "risk_factors": [f"API応答コード: {last_status}" if last_status else "API接続を確立できませんでした。"],
            "cited_source_ids": [], "model": model_name, "evaluated_at": now_utc_iso()}


def _directional_hit(direction: str, ret: float | None) -> bool | None:
    if ret is None or not math.isfinite(float(ret)) or direction not in ("bullish", "bearish", "neutral"):
        return None
    if direction == "bullish":
        return ret > 0
    if direction == "bearish":
        return ret < 0
    return abs(ret) <= 0.5


def update_snapshot_outcomes(history: dict[str, Any], close_series: pd.Series) -> dict[str, Any]:
    """Attach realized forward returns to old runs; this is the feedback loop for evaluation."""
    dates = [pd.Timestamp(x).strftime("%Y-%m-%d") for x in close_series.index]
    pos = {d: i for i, d in enumerate(dates)}
    closes = [float(x) if math.isfinite(float(x)) else None for x in close_series.tolist()]
    for snap in history.get("snapshots", []):
        idx = pos.get(snap.get("as_of_date"))
        if idx is None or not closes[idx]:
            continue
        base = closes[idx]
        outcomes = snap.setdefault("forward_returns_pct", {})
        for h in HORIZONS:
            j = idx + h
            if j < len(closes) and closes[j] is not None and base and base > 0:
                outcomes[str(h)] = round((closes[j] / base - 1) * 100, 4)
        gemini = snap.get("gemini") or {}
        if gemini.get("status") == "ok":
            ret = outcomes.get(str(gemini.get("horizon_sessions", 5)))
            hit = _directional_hit(gemini.get("direction", "unknown"), ret)
            snap["gemini_validation"] = {"tested": hit is not None, "hit": hit, "realized_return_pct": ret}
        else:
            snap["gemini_validation"] = {"tested": False, "hit": None, "realized_return_pct": None}
    return history


def evaluate_gemini_history(history: dict[str, Any]) -> dict[str, Any]:
    snapshots = history.get("snapshots", [])
    result: dict[str, Any] = {"overall": {"tested": 0, "correct": 0, "accuracy_pct": None}, "by_horizon": {}}
    rows = [s for s in snapshots if (s.get("gemini_validation") or {}).get("tested")]
    correct = sum(1 for s in rows if s["gemini_validation"].get("hit") is True)
    result["overall"] = {"tested": len(rows), "correct": correct, "accuracy_pct": round(correct / len(rows) * 100, 2) if rows else None}
    for horizon in (1, 3, 5, 10):
        tested: list[tuple[float, bool]] = []
        for snap in snapshots:
            gem = snap.get("gemini") or {}
            outcomes = snap.get("forward_returns_pct") or {}
            # Evaluate the same recorded direction at several horizons to expose time-horizon sensitivity.
            if gem.get("status") != "ok":
                continue
            ret = outcomes.get(str(horizon))
            hit = _directional_hit(gem.get("direction", "unknown"), ret)
            if hit is not None:
                tested.append((float(ret), hit))
        result["by_horizon"][str(horizon)] = {
            "tested": len(tested), "correct": sum(1 for _, ok in tested if ok),
            "accuracy_pct": round(sum(1 for _, ok in tested if ok) / len(tested) * 100, 2) if tested else None,
            "mean_realized_return_pct": round(sum(ret for ret, _ in tested) / len(tested), 4) if tested else None,
        }
    return result


def _grade_gemini(ai: dict[str, Any], statistical: dict[str, Any], context: dict[str, Any], evaluation: dict[str, Any]) -> None:
    if ai.get("status") != "ok":
        ai["grade"] = "D"
        ai["grade_label"] = "検証なし"
        return
    confidence = int(ai.get("confidence_pct") or 0)
    eval_row = evaluation.get("by_horizon", {}).get("5", {})
    tested = int(eval_row.get("tested") or 0)
    accuracy = eval_row.get("accuracy_pct")
    if (confidence >= 85 and statistical.get("grade") == "A" and context.get("grade") in ("A", "B")
            and tested >= 20 and accuracy is not None and accuracy >= 65):
        grade, label = "A", "過去検証・統計ともに条件を充足"
    elif confidence >= 70 and statistical.get("grade") in ("A", "B") and context.get("grade") in ("A", "B"):
        grade, label = "B", "統計との整合性あり・AI継続検証中"
    elif confidence >= 50 and statistical.get("grade") != "D":
        grade, label = "C", "参考所見・確度は限定的"
    else:
        grade, label = "D", "根拠不足または相反"
    ai["grade"] = grade
    ai["grade_label"] = label
    ai["calibration"] = {"historical_tested_5d": tested, "historical_accuracy_5d_pct": accuracy}


def event_conditioned_statistics(
    history: dict[str, Any], pattern_id: str, regime: str, current_categories: set[str],
    baseline_up_pct: float | None, baseline_flat_pct: float | None, horizon: int = 5,
) -> dict[str, Any]:
    """Outcome stats for prior snapshots with the same pattern, regime and overlapping headline categories."""
    if not current_categories:
        return {"n": 0, "grade": "D", "grade_label": "イベント一致例なし", "hit_rate_pct": None,
                "avg_return_pct": None, "up_rate_pct": None, "down_rate_pct": None}
    rows = []
    for snap in history.get("snapshots", []):
        if snap.get("primary_pattern_id") != pattern_id or snap.get("market_regime") != regime:
            continue
        older_categories = set(snap.get("event_categories") or [])
        if not current_categories.intersection(older_categories):
            continue
        ret = (snap.get("forward_returns_pct") or {}).get(str(horizon))
        try:
            ret = float(ret)
        except (TypeError, ValueError):
            continue
        if math.isfinite(ret):
            rows.append(ret)
    n = len(rows)
    if not rows:
        return {"n": 0, "grade": "D", "grade_label": "イベント一致例なし", "hit_rate_pct": None,
                "avg_return_pct": None, "up_rate_pct": None, "down_rate_pct": None}
    bias = PATTERN_SPECS.get(pattern_id, {}).get("bias", "neutral")
    if bias == "bullish":
        successes = sum(x > 0 for x in rows)
        baseline = (baseline_up_pct if baseline_up_pct is not None else 50) / 100
        neutral = False
    elif bias == "bearish":
        successes = sum(x < 0 for x in rows)
        baseline = 1 - (baseline_up_pct if baseline_up_pct is not None else 50) / 100
        neutral = False
    else:
        successes = sum(abs(x) <= 0.5 for x in rows)
        baseline = (baseline_flat_pct if baseline_flat_pct is not None else 10) / 100
        neutral = True
    grade = confidence_grade(n, successes, baseline, neutral=neutral)
    return {
        "n": n, "grade": grade["grade"], "grade_label": grade["label"],
        "hit_rate_pct": round(successes / n * 100, 2),
        "ci95_low_pct": grade.get("lower_95_pct"), "ci95_high_pct": grade.get("upper_95_pct"),
        "avg_return_pct": round(sum(rows) / n, 4),
        "up_rate_pct": round(sum(x > 0 for x in rows) / n * 100, 2),
        "down_rate_pct": round(sum(x < 0 for x in rows) / n * 100, 2),
        "categories_matched": sorted(current_categories),
    }


def _safe_close(row: pd.Series, key: str) -> float | None:
    try:
        v = float(row[key])
        return round(v, 4) if math.isfinite(v) else None
    except (KeyError, TypeError, ValueError):
        return None


def run() -> None:
    SITE_DATA.mkdir(parents=True, exist_ok=True)
    old_latest = _read_json(LATEST_PATH, {})
    history = _read_json(HISTORY_PATH, {"schema_version": 1, "snapshots": []})
    if not isinstance(history, dict) or not isinstance(history.get("snapshots"), list):
        history = {"schema_version": 1, "snapshots": []}

    # Historical daily OHLC is fetched from public Yahoo Finance data via yfinance.
    tqqq = fetch_symbol("TQQQ")
    qqq = fetch_symbol("QQQ")["close"].rename("qqq_close")
    try:
        vix = fetch_symbol("^VIX")["close"].rename("vix_close")
    except Exception as exc:
        log.warning("VIX series unavailable (%s); regime labels will use partial context", type(exc).__name__)
        vix = pd.Series(dtype=float, name="vix_close")

    frame = tqqq.rename(columns={"open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume"}).copy()
    frame = frame.join(qqq, how="left")
    if not vix.empty:
        frame = frame.join(vix, how="left")
    else:
        frame["vix_close"] = float("nan")
    frame["qqq_close"] = frame["qqq_close"].ffill()
    frame["vix_close"] = frame["vix_close"].ffill()
    frame = classify_candles(frame)
    stats = compute_pattern_statistics(frame)
    latest_row = frame.iloc[-1]
    latest_date = pd.Timestamp(frame.index[-1]).strftime("%Y-%m-%d")
    previous_close = float(frame["close"].iloc[-2]) if len(frame) > 1 else float("nan")
    latest_close = float(latest_row["close"])
    change_pct = ((latest_close / previous_close) - 1) * 100 if math.isfinite(previous_close) and previous_close > 0 else None
    current_patterns = latest_row["patterns"] if isinstance(latest_row["patterns"], list) else []
    pattern_id = primary_pattern(current_patterns) or "bullish_candle"
    pattern_info = next((x for x in stats["patterns"] if x["id"] == pattern_id), None)
    pattern_h5 = (pattern_info or {}).get("horizons", {}).get("5", {})
    regime = str(latest_row.get("regime", "neutral"))
    cstats = context_statistics(frame, pattern_id, regime, 5)
    # Baseline is same-horizon unconditional up frequency (for interpreting directional hit rates).
    base5 = stats["baseline"].get("5")
    base_hit = (100 - base5) if pattern_h5.get("expected_direction") == "bearish" and base5 is not None else base5
    statistical = {
        "grade": pattern_h5.get("grade", "D"), "grade_label": pattern_h5.get("grade_label", "データ不足"),
        "pattern_id": pattern_id, "pattern_label": PATTERN_SPECS.get(pattern_id, {}).get("label", pattern_id),
        "sample_count": pattern_h5.get("n", 0), "hit_rate_pct": pattern_h5.get("hit_rate_pct"),
        "up_rate_pct": pattern_h5.get("up_rate_pct"), "down_rate_pct": pattern_h5.get("down_rate_pct"),
        "avg_return_pct": pattern_h5.get("avg_return_pct"), "avg_up_pct": pattern_h5.get("avg_up_pct"),
        "avg_down_pct": pattern_h5.get("avg_down_pct"), "ci95_low_pct": pattern_h5.get("ci95_low_pct"),
        "ci95_high_pct": pattern_h5.get("ci95_high_pct"), "unconditional_up_rate_pct": base5,
        "direction": pattern_h5.get("expected_direction", "unknown"),
    }
    context = {
        "grade": cstats.get("grade", "D"), "grade_label": cstats.get("grade_label", "データ不足"),
        "pattern_id": pattern_id, "pattern_label": PATTERN_SPECS.get(pattern_id, {}).get("label", pattern_id),
        "market_regime": regime, "sample_count": cstats.get("n", 0),
        "hit_rate_pct": cstats.get("hit_rate_pct"), "up_rate_pct": cstats.get("up_rate_pct"),
        "down_rate_pct": cstats.get("down_rate_pct"), "avg_return_pct": cstats.get("avg_return_pct"),
        "avg_up_pct": cstats.get("avg_up_pct"), "avg_down_pct": cstats.get("avg_down_pct"),
        "ci95_low_pct": cstats.get("ci95_low_pct"), "ci95_high_pct": cstats.get("ci95_high_pct"),
    }

    news = fetch_news()
    history = update_snapshot_outcomes(history, frame["close"])
    current_event_categories = {cat for article in news for cat in article.get("categories", [])}
    event_context = event_conditioned_statistics(
        history, pattern_id, regime, current_event_categories,
        base5, stats.get("baseline_flat", {}).get("5"), 5,
    )
    if event_context.get("n", 0) >= 10:
        context = {
            "grade": event_context["grade"], "grade_label": event_context["grade_label"],
            "pattern_id": pattern_id, "pattern_label": PATTERN_SPECS.get(pattern_id, {}).get("label", pattern_id),
            "market_regime": regime, "sample_count": event_context["n"],
            "hit_rate_pct": event_context.get("hit_rate_pct"), "up_rate_pct": event_context.get("up_rate_pct"),
            "down_rate_pct": event_context.get("down_rate_pct"), "avg_return_pct": event_context.get("avg_return_pct"),
            "ci95_low_pct": event_context.get("ci95_low_pct"), "ci95_high_pct": event_context.get("ci95_high_pct"),
            "evidence_basis": "matching_pattern_regime_and_news_categories",
        }
    else:
        context["evidence_basis"] = "same_market_regime_fallback"
    context["regime_sample_count"] = cstats.get("n", 0)
    context["event_matched_samples"] = event_context.get("n", 0)
    context["current_event_categories"] = sorted(current_event_categories)
    context["event_match_grade"] = event_context.get("grade", "D")
    context["event_match_grade_label"] = event_context.get("grade_label", "イベント一致例なし")
    # Reuse an already successful assessment for the same market date, preventing duplicate calls from pushes/manual runs.
    existing_ai = None
    if old_latest.get("as_of_date") == latest_date and isinstance(old_latest.get("confidence"), dict):
        candidate = old_latest["confidence"].get("gemini")
        if isinstance(candidate, dict) and candidate.get("status") == "ok":
            existing_ai = candidate
    keys = _gemini_keys()
    model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
    quote = {"date": latest_date, "close": round(latest_close, 4), "change_pct": round(change_pct, 4) if change_pct is not None else None}
    if existing_ai:
        ai = existing_ai
        log.info("Reusing same-date Gemini assessment to avoid duplicate API calls")
    else:
        ai = gemini_assessment(keys, model, quote, current_patterns, statistical, context, news, regime)

    allowed_sources = {n["id"]: n for n in news}
    existing_archive_map = {x.get("id"): x for x in history.get("news_archive", []) if isinstance(x, dict) and x.get("id")}
    allowed_sources.update(existing_archive_map)
    cited = [allowed_sources[sid] for sid in ai.get("cited_source_ids", []) if sid in allowed_sources]
    history = update_snapshot_outcomes(history, frame["close"])
    # Keep attributable event references in a bounded archive so historic chart dates can link back to sources.
    existing_archive = history.get("news_archive", [])
    if not isinstance(existing_archive, list):
        existing_archive = []
    archive_by_id = {x.get("id"): x for x in existing_archive if isinstance(x, dict) and x.get("id")}
    for article in news:
        archive_by_id[article["id"]] = article
    history["news_archive"] = sorted(archive_by_id.values(), key=lambda x: x.get("published_at") or "", reverse=True)[:2500]
    prior_snapshots = [x for x in history["snapshots"] if x.get("as_of_date") != latest_date]
    snapshot = {
        "as_of_date": latest_date,
        "close": round(latest_close, 4),
        "daily_change_pct": round(change_pct, 4) if change_pct is not None else None,
        "primary_pattern_id": pattern_id,
        "pattern_ids": current_patterns,
        "market_regime": regime,
        "event_categories": sorted({cat for article in news for cat in article.get("categories", [])}),
        "source_ids": [article["id"] for article in news],
        "gemini": ai,
        "forward_returns_pct": {},
        "created_at": now_utc_iso(),
    }
    history["snapshots"] = (prior_snapshots + [snapshot])[-1500:]
    history = update_snapshot_outcomes(history, frame["close"])
    evaluation = evaluate_gemini_history(history)
    _grade_gemini(ai, statistical, context, evaluation)
    # Keep the adjusted grade in history too (the actual direction and confidence stay unmodified).
    for snap in history["snapshots"]:
        if snap.get("as_of_date") == latest_date:
            snap["gemini"] = ai
            break

    bars = serialize_bars(frame, limit=None)
    all_pattern_by_id = {p["id"]: p for p in stats["patterns"]}
    today_pattern_stats = [all_pattern_by_id[p] for p in current_patterns if p in all_pattern_by_id]
    recent_rows = frame.tail(10)
    recent_changes = []
    for ix, row in recent_rows.iterrows():
        i = frame.index.get_loc(ix)
        prev = float(frame["close"].iloc[i-1]) if i > 0 else None
        close = float(row["close"])
        recent_changes.append({
            "date": pd.Timestamp(ix).strftime("%Y-%m-%d"),
            "close": round(close, 4),
            "change_pct": round((close / prev - 1) * 100, 4) if prev and prev > 0 else None,
            "patterns": row["patterns"], "regime": row["regime"],
        })

    latest = {
        "schema_version": 1,
        "status": "ok",
        "ticker": "TQQQ",
        "generated_at": now_utc_iso(),
        "as_of_date": latest_date,
        "data_range": {"start": pd.Timestamp(frame.index[0]).strftime("%Y-%m-%d"), "end": latest_date, "rows": int(len(frame))},
        "sources": [
            {"name": "Yahoo Finance via yfinance", "url": "https://finance.yahoo.com/quote/TQQQ/history/", "role": "調整後日足OHLCV。公開データの提供状況や仕様は変更される可能性があります。"},
            {"name": "ProShares TQQQ overview", "url": "https://www.proshares.com/our-etfs/leveraged-and-inverse/tqqq", "role": "TQQQの日次3倍目標と長期保有時の乖離リスクの確認。"},
            {"name": "Google News RSS", "url": "https://news.google.com/", "role": "情勢把握用の見出し参照。見出しは本文や一次情報での確認が必要です。"},
            {"name": "Gemini API", "url": "https://ai.google.dev/gemini-api/docs", "role": "統計と提供済みニュース参照の補助的な整合性検証。"},
        ],
        "quote": {
            "close": round(latest_close, 4), "previous_close": round(previous_close, 4) if math.isfinite(previous_close) else None,
            "change_pct": round(change_pct, 4) if change_pct is not None else None,
            "qqq_close": _safe_close(latest_row, "qqq_close"), "vix_close": _safe_close(latest_row, "vix_close"),
            "market_regime": regime,
        },
        "current_patterns": current_patterns,
        "current_pattern_labels": [PATTERN_SPECS[p]["label"] for p in current_patterns if p in PATTERN_SPECS],
        "confidence": {"statistics": statistical, "context": context, "gemini": ai},
        "current_pattern_statistics": today_pattern_stats,
        "pattern_statistics": stats["patterns"],
        "baseline": stats["baseline"],
        "bars": bars,
        "recent_changes": recent_changes,
        "news": news,
        "gemini_cited_sources": cited,
        "history_evaluation": evaluation,
        "methodology": {
            "price_basis": "yfinanceのauto_adjust=Trueによる調整後OHLC（日足）。分足は対象外。",
            "horizons_sessions": list(HORIZONS),
            "return_formula": "signal-day close to close after N trading sessions: (future_close / signal_close - 1) × 100",
            "confidence": "A-Dはサンプル数とWilson 95%区間等から算出する再現性の目安。将来確率や利益保証ではありません。",
            "context": "QQQの5営業日変化率、VIX水準・変化、TQQQの実現ボラティリティから市場局面をラベル化。実ニュースの蓄積は運用開始後に増えます。",
            "news_tone": "見出し内の簡易キーワードによる参考タグ。記事内容や因果関係を検証したものではありません。",
            "self_improvement": "価格データは毎回再計算し、過去のAI方向判定は1/3/5/10営業日後に実績を追記して精度を評価します。判定基準自体を自動で恣意的に書き換えません。",
        },
        "disclaimer": "分析・教育目的の情報です。売買推奨ではありません。TQQQは日次3倍を目指すレバレッジETFで、複数日にわたるリターンは指数の単純な3倍から大きく乖離し得ます。",
    }
    _write_json(LATEST_PATH, latest)
    _write_json(HISTORY_PATH, history)
    log.info("Wrote latest.json (%d bars, %d patterns) and history.json (%d snapshots)", len(bars), len(stats["patterns"]), len(history["snapshots"]))

    try:
        drive_backup(latest, history)
    except Exception as exc:
        # Google Drive is a secondary backup; a Drive setup issue should not destroy price data.
        log.warning("Google Drive backup did not complete (%s); local JSON outputs are preserved", type(exc).__name__)


def _drive_service(account: dict[str, str]):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    creds = Credentials(
        token=None,
        refresh_token=account["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=account["client_id"],
        client_secret=account["client_secret"],
        scopes=[DRIVE_SCOPE],
    )
    creds.refresh(Request())
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _drive_folder(service, name: str = "TQQQ Candle Lab") -> str:
    escaped = name.replace("'", "\\'")
    response = service.files().list(
        q=f"name='{escaped}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
        spaces="drive", pageSize=100, fields="files(id,name)",
    ).execute()
    files = response.get("files", [])
    if files:
        return files[0]["id"]
    created = service.files().create(body={"name": name, "mimeType": "application/vnd.google-apps.folder"}, fields="id").execute()
    return created["id"]


def _drive_upsert(service, folder_id: str, filename: str, data: Any) -> None:
    import io
    from googleapiclient.http import MediaIoBaseUpload
    escaped = filename.replace("'", "\\'")
    listed = service.files().list(
        q=f"name='{escaped}' and '{folder_id}' in parents and trashed=false",
        spaces="drive", pageSize=100, fields="files(id,name)",
    ).execute().get("files", [])
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    media = MediaIoBaseUpload(io.BytesIO(payload), mimetype="application/json", resumable=False)
    if listed:
        service.files().update(fileId=listed[0]["id"], media_body=media, fields="id,name").execute()
    else:
        service.files().create(body={"name": filename, "parents": [folder_id], "mimeType": "application/json"}, media_body=media, fields="id,name").execute()


def drive_backup(latest: dict[str, Any], history: dict[str, Any]) -> None:
    raw = os.getenv("DRIVE_ACCOUNTS_JSON", "").strip()
    if not raw:
        log.info("DRIVE_ACCOUNTS_JSON not configured; skipping Drive backup")
        return
    try:
        accounts = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("DRIVE_ACCOUNTS_JSON is not valid JSON") from exc
    if not isinstance(accounts, list) or not accounts:
        log.warning("No configured Drive accounts; skipping Drive backup")
        return
    errors = []
    for ix, account in enumerate(accounts, 1):
        if not isinstance(account, dict) or not all(account.get(k) for k in ("client_id", "client_secret", "refresh_token")):
            log.warning("Drive account slot %d is missing required OAuth fields", ix)
            continue
        try:
            service = _drive_service(account)
            folder_id = _drive_folder(service)
            _drive_upsert(service, folder_id, "latest.json", latest)
            _drive_upsert(service, folder_id, "history.json", history)
            log.info("Google Drive backup completed using configured account slot %d", ix)
            return
        except Exception as exc:
            status = getattr(getattr(exc, "resp", None), "status", None)
            # Try another explicitly configured/authorized account if storage or API quota is unavailable.
            log.warning("Drive account slot %d failed (HTTP %s, %s); trying next configured slot if any", ix, status or "n/a", type(exc).__name__)
            errors.append(f"slot{ix}:{status or type(exc).__name__}")
    raise RuntimeError("All configured Drive account slots failed: " + ",".join(errors))


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        log.error("Data pipeline failed (%s): %s", type(exc).__name__, str(exc)[:250])
        sys.exit(1)
