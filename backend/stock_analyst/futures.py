"""Futures snapshot and scoring backed by OpenBB history adapters."""

import logging
import re
from datetime import datetime, timezone
from typing import Optional

from .market_data import get_company_profile, get_price_history
from .qlib_engine import build_snapshot

logger = logging.getLogger(__name__)

_FUTURES_PATTERN = re.compile(r"^[A-Z0-9]+=F$", re.IGNORECASE)
_KNOWN_FUTURES = {
    "ES": "E-Mini S&P 500",
    "NQ": "E-Mini NASDAQ 100",
    "YM": "E-Mini Dow",
    "RTY": "E-Mini Russell 2000",
    "GC": "Gold",
    "SI": "Silver",
    "CL": "Crude Oil WTI",
    "NG": "Natural Gas",
    "ZB": "US Treasury Bond",
    "ZN": "10-Year T-Note",
    "ZC": "Corn",
    "ZS": "Soybeans",
    "ZW": "Wheat",
    "HG": "Copper",
    "PL": "Platinum",
    "PA": "Palladium",
    "KC": "Coffee",
    "CT": "Cotton",
    "SB": "Sugar",
    "CC": "Cocoa",
    "BTC": "Bitcoin Futures",
    "ETH": "Ether Futures",
}

FUTURES_WEIGHT_DEFAULTS = {
    "base_score": 50.0,
    "trend_bullish": 20.0,
    "trend_bearish": 20.0,
    "high_volume_bonus": 10.0,
    "ma_bullish_bonus": 6.0,
    "ma_bearish_penalty": 6.0,
    "price_above_ma_bonus": 5.0,
    "price_below_ma_penalty": 5.0,
    "volume_ratio_threshold": 1.8,
    "sentiment_buy_threshold": 65.0,
    "sentiment_sell_threshold": 35.0,
    "action_buy_threshold": 65.0,
    "action_sell_threshold": 35.0,
}


def is_futures_symbol(symbol: str) -> bool:
    s = (symbol or "").strip().upper()
    if _FUTURES_PATTERN.fullmatch(s):
        return True
    return s in _KNOWN_FUTURES


def normalize_futures_symbol(symbol: str) -> str:
    """Ensure futures symbols end with =F for yfinance."""
    s = (symbol or "").strip().upper()
    if _FUTURES_PATTERN.fullmatch(s):
        return s
    if s in _KNOWN_FUTURES:
        return f"{s}=F"
    return s


def get_futures_snapshot(symbol: str) -> dict:
    """Fetch futures price data via OpenBB with yfinance fallback."""
    yf_symbol = normalize_futures_symbol(symbol)
    hist = get_price_history(yf_symbol, period="1y", asset_type="future")
    if hist is None or hist.empty:
        raise ValueError(f"No historical data for {yf_symbol}")

    base_symbol = yf_symbol.replace("=F", "") if "=F" in yf_symbol else yf_symbol
    contract_name = _KNOWN_FUTURES.get(base_symbol, base_symbol)
    profile = get_company_profile(yf_symbol)
    snapshot = build_snapshot(hist, yf_symbol, asset_type="future", name=profile.get("name") or contract_name)
    snapshot["name"] = snapshot.get("name") or contract_name
    return snapshot


def get_futures_sentiment(snapshot: dict, weights: Optional[dict] = None) -> dict:
    """Compute sentiment for a futures contract."""
    w = dict(FUTURES_WEIGHT_DEFAULTS)
    if weights:
        for k, v in weights.items():
            if k in w:
                w[k] = float(v)

    score = w["base_score"]
    drivers = []

    if snapshot["trend"] == "BULLISH":
        score += w["trend_bullish"]
        drivers.append("positive price momentum")
    elif snapshot["trend"] == "BEARISH":
        score -= w["trend_bearish"]
        drivers.append("negative price momentum")

    if snapshot.get("volume_ratio", 1) > w["volume_ratio_threshold"]:
        score += w["high_volume_bonus"]
        drivers.append("elevated trading volume")

    if snapshot["fifty_day_avg"] > snapshot["two_hundred_day_avg"]:
        score += w["ma_bullish_bonus"]
        drivers.append("50D MA above 200D MA")
    else:
        score -= w["ma_bearish_penalty"]
        drivers.append("50D MA below 200D MA")

    score = max(0, min(100, score))
    label = "neutral"
    if score >= w["sentiment_buy_threshold"]:
        label = "bullish"
    elif score <= w["sentiment_sell_threshold"]:
        label = "bearish"

    return {
        "score": score,
        "label": label,
        "summary": f"Futures sentiment appears {label} (score {score}/100).",
        "drivers": drivers,
        "weights_used": w,
    }


def get_futures_recommendation(snapshot: dict, sentiment: dict, weights: Optional[dict] = None) -> dict:
    """BUY/SELL/HOLD recommendation for futures."""
    w = dict(FUTURES_WEIGHT_DEFAULTS)
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
        "summary": f"AI recommendation: {action} based on futures trend and volume dynamics.",
        "disclaimer": "For informational purposes only, not financial advice. Futures involve substantial risk.",
        "weights_used": w,
    }
