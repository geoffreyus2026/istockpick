import logging
import os
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_QLIB_READY = False
_QLIB_INIT_ATTEMPTED = False


def _init_qlib() -> bool:
    global _QLIB_READY, _QLIB_INIT_ATTEMPTED
    if _QLIB_INIT_ATTEMPTED:
        return _QLIB_READY
    _QLIB_INIT_ATTEMPTED = True
    provider_uri = (os.getenv("QLIB_PROVIDER_URI") or "").strip()
    region = (os.getenv("QLIB_REGION") or "us").strip().lower()
    if not provider_uri:
        return False
    try:
        import qlib  # type: ignore

        qlib.init(provider_uri=provider_uri, region=region.upper())
        _QLIB_READY = True
    except Exception as exc:
        logger.warning("Qlib initialization failed: %s", exc)
        _QLIB_READY = False
    return _QLIB_READY


def engine_name() -> str:
    return "qlib" if _init_qlib() else "pandas"


def moving_average(series: pd.Series, window: int) -> float:
    return float(series.rolling(window=window).mean().iloc[-1])


def rsi(series: pd.Series, period: int = 14) -> float:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(window=period).mean()
    loss = (-delta.clip(upper=0)).rolling(window=period).mean()
    rs = gain / loss.replace(0, np.nan)
    result = 100 - (100 / (1 + rs))
    value = result.iloc[-1]
    return float(50.0 if pd.isna(value) else value)


def macd(series: pd.Series) -> dict:
    exp1 = series.ewm(span=12, adjust=False).mean()
    exp2 = series.ewm(span=26, adjust=False).mean()
    macd_line = exp1 - exp2
    signal = macd_line.ewm(span=9, adjust=False).mean()
    histogram = macd_line - signal
    return {
        "macd": float(macd_line.iloc[-1]),
        "signal": float(signal.iloc[-1]),
        "histogram": float(histogram.iloc[-1]),
    }


def bollinger_bands(series: pd.Series, period: int = 20) -> dict:
    sma = series.rolling(window=period).mean()
    std = series.rolling(window=period).std()
    upper = sma + 2 * std
    lower = sma - 2 * std
    middle = sma.iloc[-1]
    return {
        "upper_band": float(upper.iloc[-1]),
        "middle_band": float(middle),
        "lower_band": float(lower.iloc[-1]),
        "band_width": float((upper.iloc[-1] - lower.iloc[-1]) / middle) if middle else 0.0,
    }


def atr(data: pd.DataFrame, period: int = 14) -> pd.Series:
    high_low = data["High"] - data["Low"]
    high_close = (data["High"] - data["Close"].shift()).abs()
    low_close = (data["Low"] - data["Close"].shift()).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return true_range.rolling(window=period).mean()


def volume_summary(data: pd.DataFrame) -> dict:
    volume_sma = data["Volume"].rolling(window=20).mean()
    current_volume = float(data["Volume"].iloc[-1])
    signed_volume = np.sign(data["Close"].diff().fillna(0)) * data["Volume"].fillna(0)
    obv = signed_volume.cumsum()
    prior = float(obv.iloc[-20]) if len(obv) >= 20 else float(obv.iloc[0])
    return {
        "volume_ratio": float(current_volume / volume_sma.iloc[-1]) if volume_sma.iloc[-1] else 1.0,
        "obv_trend": float(obv.iloc[-1] - prior),
        "volume_sma": float(volume_sma.iloc[-1]),
    }


def support_resistance(data: pd.DataFrame, window: int = 20) -> dict:
    rolling_high = data["High"].rolling(window=window, center=True).max()
    rolling_low = data["Low"].rolling(window=window, center=True).min()
    resistance = float(rolling_high.max())
    support = float(rolling_low.min())
    current = float(data["Close"].iloc[-1])
    return {
        "support": support,
        "resistance": resistance,
        "current_price": current,
        "distance_to_support": float((current - support) / support) if support else 0.0,
        "distance_to_resistance": float((resistance - current) / resistance) if resistance else 0.0,
    }


def volatility_summary(data: pd.DataFrame, period: int = 20) -> dict:
    returns = data["Close"].pct_change()
    historical_vol = returns.rolling(window=period).std() * np.sqrt(252)
    current_vol = historical_vol.iloc[-1]
    current_vol = float(0.0 if pd.isna(current_vol) else current_vol)
    atr_series = atr(data, period)
    atr_value = atr_series.iloc[-1]
    return {
        "historical_volatility": current_vol,
        "atr": float(0.0 if pd.isna(atr_value) else atr_value),
        "volatility_percentile": float((historical_vol <= historical_vol.iloc[-1]).mean()) if len(historical_vol.dropna()) else 0.0,
    }


def build_snapshot(data: pd.DataFrame, symbol: str, asset_type: str = "stock", name: Optional[str] = None) -> dict:
    closes = data["Close"].dropna()
    volumes = data["Volume"].fillna(0)
    price = float(closes.iloc[-1])
    prev_close = float(closes.iloc[-2]) if len(closes) > 1 else price
    change_pct = ((price - prev_close) / prev_close * 100) if prev_close else 0.0
    ma50 = moving_average(closes, 50)
    ma200 = moving_average(closes, 200)
    volume = volume_summary(data)

    trend = "NEUTRAL"
    threshold = 3 if asset_type == "crypto" else 2
    if change_pct > threshold:
        trend = "BULLISH"
    elif change_pct < -threshold:
        trend = "BEARISH"

    open_price = float(data["Open"].dropna().iloc[-1]) if not data["Open"].dropna().empty else price
    high_price = float(data["High"].dropna().iloc[-1]) if not data["High"].dropna().empty else price
    low_price = float(data["Low"].dropna().iloc[-1]) if not data["Low"].dropna().empty else price

    return {
        "symbol": symbol,
        "name": name or symbol,
        "asset_type": asset_type,
        "price": price,
        "change_pct": change_pct,
        "volume_ratio": volume["volume_ratio"],
        "trend": trend,
        "fifty_day_avg": ma50,
        "two_hundred_day_avg": ma200,
        "open": open_price,
        "high": high_price,
        "low": low_price,
        "volume": float(volumes.iloc[-1]) if len(volumes) else 0.0,
        "feature_engine": engine_name(),
    }
