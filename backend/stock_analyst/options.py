"""Options chain, snapshot, and scoring backed by OpenBB option chains."""

import logging
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from .market_data import get_options_chain_dataset, get_price_history
from .qlib_engine import build_snapshot

logger = logging.getLogger(__name__)

OPTIONS_WEIGHT_DEFAULTS = {
    "base_score": 50.0,
    "trend_bullish": 12.0,
    "trend_bearish": 12.0,
    "high_volume_bonus": 6.0,
    "ma_bullish_bonus": 5.0,
    "ma_bearish_penalty": 5.0,
    "price_above_ma_bonus": 4.0,
    "price_below_ma_penalty": 4.0,
    "volume_ratio_threshold": 1.5,
    "put_call_ratio_bullish_bonus": 8.0,
    "put_call_ratio_bearish_penalty": 8.0,
    "put_call_ratio_threshold": 0.7,
    "high_iv_penalty": 5.0,
    "iv_threshold": 0.5,
    "sentiment_buy_threshold": 65.0,
    "sentiment_sell_threshold": 35.0,
    "action_buy_threshold": 65.0,
    "action_sell_threshold": 35.0,
}


def _row_value(row: pd.Series, *keys, default=0):
    lowered = {str(k).strip().lower(): v for k, v in row.to_dict().items()}
    for key in keys:
        if key.lower() in lowered and lowered[key.lower()] is not None:
            return lowered[key.lower()]
    return default


def get_options_chain(symbol: str, expiry: Optional[str] = None) -> dict:
    """Fetch options chain for a symbol. If expiry is None, use the nearest
    available expiration date."""
    symbol = (symbol or "").strip().upper()
    dataset = get_options_chain_dataset(symbol, expiry=expiry)
    raw = dataset.get("raw")
    if not isinstance(raw, pd.DataFrame) or raw.empty:
        raise ValueError(f"No options available for {symbol}")

    lower_cols = {str(col).lower(): col for col in raw.columns}
    expiry_col = lower_cols.get("expiration") or lower_cols.get("expiry")
    option_type_col = lower_cols.get("optiontype") or lower_cols.get("option_type") or lower_cols.get("side")

    available_expiries = []
    if expiry_col is not None:
        values = raw[expiry_col].dropna().astype(str).tolist()
        available_expiries = sorted(dict.fromkeys(values))[:20]

    target_expiry = expiry or dataset.get("expiry") or (available_expiries[0] if available_expiries else None)
    filtered = raw
    if expiry_col is not None and target_expiry:
        filtered = raw[raw[expiry_col].astype(str) == target_expiry].copy()
    if filtered.empty:
        filtered = raw.copy()

    calls_data = []
    calls_df = filtered
    if option_type_col is not None:
        calls_df = filtered[filtered[option_type_col].astype(str).str.lower().str.startswith("c")]
    if not calls_df.empty:
        for _, row in calls_df.iterrows():
            calls_data.append({
                "strike": float(_row_value(row, "strike")),
                "lastPrice": float(_row_value(row, "lastPrice", "last_price", "mark", default=0)),
                "bid": float(_row_value(row, "bid", default=0)),
                "ask": float(_row_value(row, "ask", default=0)),
                "volume": int(_row_value(row, "volume", default=0)) if str(_row_value(row, "volume", default=0)) != "nan" else 0,
                "openInterest": int(_row_value(row, "openInterest", "open_interest", "openInterestVolume", default=0)) if str(_row_value(row, "openInterest", "open_interest", "openInterestVolume", default=0)) != "nan" else 0,
                "impliedVolatility": float(_row_value(row, "impliedVolatility", "implied_volatility", "iv", default=0)),
                "inTheMoney": bool(_row_value(row, "inTheMoney", "in_the_money", default=False)),
            })

    puts_data = []
    puts_df = filtered
    if option_type_col is not None:
        puts_df = filtered[filtered[option_type_col].astype(str).str.lower().str.startswith("p")]
    if not puts_df.empty:
        for _, row in puts_df.iterrows():
            puts_data.append({
                "strike": float(_row_value(row, "strike")),
                "lastPrice": float(_row_value(row, "lastPrice", "last_price", "mark", default=0)),
                "bid": float(_row_value(row, "bid", default=0)),
                "ask": float(_row_value(row, "ask", default=0)),
                "volume": int(_row_value(row, "volume", default=0)) if str(_row_value(row, "volume", default=0)) != "nan" else 0,
                "openInterest": int(_row_value(row, "openInterest", "open_interest", "openInterestVolume", default=0)) if str(_row_value(row, "openInterest", "open_interest", "openInterestVolume", default=0)) != "nan" else 0,
                "impliedVolatility": float(_row_value(row, "impliedVolatility", "implied_volatility", "iv", default=0)),
                "inTheMoney": bool(_row_value(row, "inTheMoney", "in_the_money", default=False)),
            })

    total_call_oi = sum(c["openInterest"] for c in calls_data)
    total_put_oi = sum(p["openInterest"] for p in puts_data)
    put_call_ratio = (total_put_oi / total_call_oi) if total_call_oi > 0 else 0.0

    total_call_vol = sum(c["volume"] for c in calls_data)
    total_put_vol = sum(p["volume"] for p in puts_data)

    all_iv = [c["impliedVolatility"] for c in calls_data + puts_data if c["impliedVolatility"] > 0]
    avg_iv = sum(all_iv) / len(all_iv) if all_iv else 0.0

    # Max pain: strike where total option value is minimized for holders
    max_pain = _compute_max_pain(calls_data, puts_data)

    return {
        "symbol": symbol,
        "expiry": target_expiry,
        "available_expiries": available_expiries,
        "calls_count": len(calls_data),
        "puts_count": len(puts_data),
        "calls": calls_data,
        "puts": puts_data,
        "summary": {
            "put_call_ratio": round(put_call_ratio, 4),
            "total_call_oi": total_call_oi,
            "total_put_oi": total_put_oi,
            "total_call_volume": total_call_vol,
            "total_put_volume": total_put_vol,
            "avg_implied_volatility": round(avg_iv, 4),
            "max_pain": max_pain,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _compute_max_pain(calls: list[dict], puts: list[dict]) -> Optional[float]:
    """Find the strike where total losses for option holders is maximized
    (i.e., maximum pain for buyers)."""
    strikes = sorted({c["strike"] for c in calls} | {p["strike"] for p in puts})
    if not strikes:
        return None

    min_pain = float("inf")
    max_pain_strike = None

    for strike in strikes:
        call_pain = sum(max(0, strike - c["strike"]) * c["openInterest"] for c in calls)
        put_pain = sum(max(0, p["strike"] - strike) * p["openInterest"] for p in puts)
        total = call_pain + put_pain
        if total < min_pain:
            min_pain = total
            max_pain_strike = strike

    return max_pain_strike


def get_options_snapshot(symbol: str) -> dict:
    """Underlying stock snapshot enriched with nearest-expiry options summary."""
    symbol = (symbol or "").strip().upper()
    hist = get_price_history(symbol, period="1y", asset_type="stock")
    if hist is None or hist.empty:
        raise ValueError(f"No historical data for {symbol}")

    # Fetch options summary for nearest expiry
    options_summary = {}
    try:
        chain = get_options_chain(symbol)
        options_summary = chain.get("summary", {})
        options_summary["expiry"] = chain.get("expiry")
    except Exception as exc:
        logger.warning("Options chain fetch failed for %s: %s", symbol, exc)

    snapshot = build_snapshot(hist, symbol, asset_type="option", name=symbol)
    snapshot["options_summary"] = options_summary
    return snapshot


def get_options_sentiment(snapshot: dict, weights: Optional[dict] = None) -> dict:
    """Compute sentiment for an options-focused analysis using put/call ratio and IV."""
    w = dict(OPTIONS_WEIGHT_DEFAULTS)
    if weights:
        for k, v in weights.items():
            if k in w:
                w[k] = float(v)

    score = w["base_score"]
    drivers = []

    if snapshot["trend"] == "BULLISH":
        score += w["trend_bullish"]
        drivers.append("positive underlying momentum")
    elif snapshot["trend"] == "BEARISH":
        score -= w["trend_bearish"]
        drivers.append("negative underlying momentum")

    if snapshot.get("volume_ratio", 1) > w["volume_ratio_threshold"]:
        score += w["high_volume_bonus"]
        drivers.append("elevated trading volume")

    if snapshot["fifty_day_avg"] > snapshot["two_hundred_day_avg"]:
        score += w["ma_bullish_bonus"]
        drivers.append("50D MA above 200D MA")
    else:
        score -= w["ma_bearish_penalty"]
        drivers.append("50D MA below 200D MA")

    # Put/call ratio signal
    opts = snapshot.get("options_summary", {})
    pcr = opts.get("put_call_ratio", 0)
    if pcr > 0:
        if pcr < w["put_call_ratio_threshold"]:
            score += w["put_call_ratio_bullish_bonus"]
            drivers.append(f"low put/call ratio ({pcr:.2f}) — bullish signal")
        elif pcr > 1.0:
            score -= w["put_call_ratio_bearish_penalty"]
            drivers.append(f"high put/call ratio ({pcr:.2f}) — bearish signal")

    # High IV penalty
    avg_iv = opts.get("avg_implied_volatility", 0)
    if avg_iv > w["iv_threshold"]:
        score -= w["high_iv_penalty"]
        drivers.append(f"elevated implied volatility ({avg_iv:.2%})")

    score = max(0, min(100, score))
    label = "neutral"
    if score >= w["sentiment_buy_threshold"]:
        label = "bullish"
    elif score <= w["sentiment_sell_threshold"]:
        label = "bearish"

    return {
        "score": score,
        "label": label,
        "summary": f"Options sentiment appears {label} (score {score}/100).",
        "drivers": drivers,
        "weights_used": w,
    }


def get_options_recommendation(snapshot: dict, sentiment: dict, weights: Optional[dict] = None) -> dict:
    """BUY/SELL/HOLD recommendation for options."""
    w = dict(OPTIONS_WEIGHT_DEFAULTS)
    if weights:
        for k, v in weights.items():
            if k in w:
                w[k] = float(v)

    score = sentiment["score"]
    if snapshot["price"] > snapshot["fifty_day_avg"]:
        score += w["price_above_ma_bonus"]
    else:
        score -= w["price_below_ma_penalty"]
    score = max(0, min(100, score))

    action = "HOLD"
    if score >= w["action_buy_threshold"]:
        action = "BUY"
    elif score <= w["action_sell_threshold"]:
        action = "SELL"

    return {
        "action": action,
        "confidence": score,
        "summary": f"AI recommendation: {action} based on options flow, IV, and underlying trend.",
        "disclaimer": "For informational purposes only, not financial advice. Options involve significant risk.",
        "weights_used": w,
    }
