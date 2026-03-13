import logging
import os
import tempfile
from datetime import date, datetime, timedelta
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)

_OPENBB_CLIENT = None
_OPENBB_IMPORT_ERROR: Optional[Exception] = None


def _configure_openbb_runtime_env():
    home_dir = os.path.expanduser(os.getenv("HOME", "~"))
    if not os.access(home_dir, os.W_OK):
        temp_home = os.path.join(tempfile.gettempdir(), "openbb_home")
        os.environ.setdefault("HOME", temp_home)
    os.environ.setdefault("OPENBB_HOME", os.path.join(os.environ["HOME"], ".openbb_platform"))


def _is_openbb_runtime_failure(exc: Exception) -> bool:
    message = str(exc)
    return (
        isinstance(exc, (ImportError, PermissionError, AttributeError))
        or "OBBject_EquityInfo" in message
        or ".openbb_platform" in message
    )


def _get_openbb_client():
    global _OPENBB_CLIENT, _OPENBB_IMPORT_ERROR
    if _OPENBB_CLIENT is not None:
        return _OPENBB_CLIENT
    if _OPENBB_IMPORT_ERROR is not None:
        raise _OPENBB_IMPORT_ERROR
    try:
        _configure_openbb_runtime_env()
        from openbb import obb  # type: ignore

        _OPENBB_CLIENT = obb
        return _OPENBB_CLIENT
    except Exception as exc:
        _OPENBB_IMPORT_ERROR = exc
        raise


def _to_df(payload: Any) -> pd.DataFrame:
    if payload is None:
        return pd.DataFrame()
    if isinstance(payload, pd.DataFrame):
        return payload.copy()
    to_df = getattr(payload, "to_df", None)
    if callable(to_df):
        df = to_df()
        if isinstance(df, pd.DataFrame):
            return df.copy()
    results = getattr(payload, "results", None)
    if isinstance(results, list):
        return pd.DataFrame(results)
    if isinstance(payload, list):
        return pd.DataFrame(payload)
    if isinstance(payload, dict):
        return pd.DataFrame([payload])
    return pd.DataFrame()


def _normalize_history_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

    renamed = df.copy()
    renamed.columns = [str(col).strip().lower() for col in renamed.columns]
    column_map = {
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume",
    }
    renamed = renamed.rename(columns=column_map)
    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col not in renamed.columns:
            renamed[col] = 0.0
        renamed[col] = pd.to_numeric(renamed[col], errors="coerce")

    if not isinstance(renamed.index, pd.DatetimeIndex):
        date_col = None
        for candidate in ("date", "datetime", "timestamp"):
            if candidate in renamed.columns:
                date_col = candidate
                break
        if date_col is not None:
            renamed[date_col] = pd.to_datetime(renamed[date_col], errors="coerce", utc=True)
            renamed = renamed.set_index(date_col)
    renamed = renamed.dropna(subset=["Close"]).sort_index()
    return renamed


def _history_start(period: str = "1y") -> date:
    period = (period or "1y").strip().lower()
    today = datetime.utcnow().date()
    if period.endswith("y") and period[:-1].isdigit():
        return today - timedelta(days=365 * max(1, int(period[:-1])))
    if period.endswith("mo") and period[:-2].isdigit():
        return today - timedelta(days=30 * max(1, int(period[:-2])))
    if period.endswith("d") and period[:-1].isdigit():
        return today - timedelta(days=max(1, int(period[:-1])))
    return today - timedelta(days=365)


def _openbb_historical_path(asset_type: str) -> tuple[str, ...]:
    at = (asset_type or "stock").lower()
    if at == "crypto":
        return ("crypto", "price", "historical")
    return ("equity", "price", "historical")


def _openbb_call(path: tuple[str, ...], **kwargs):
    client = _get_openbb_client()
    target = client
    for attr in path:
        target = getattr(target, attr)
    return target(**kwargs)


def _history_from_openbb(symbol: str, period: str = "1y", asset_type: str = "stock") -> pd.DataFrame:
    try:
        provider = os.getenv("OPENBB_PRICE_PROVIDER", "yfinance").strip() or "yfinance"
        start_date = _history_start(period)
        kwargs = {
            "symbol": symbol,
            "start_date": start_date.isoformat(),
            "provider": provider,
        }
        payload = _openbb_call(_openbb_historical_path(asset_type), **kwargs)
        df = _to_df(payload)
        normalized = _normalize_history_df(df)
        if normalized.empty:
            raise ValueError(f"OpenBB returned no historical data for {symbol}")
        return normalized
    except Exception as exc:
        if not _is_openbb_runtime_failure(exc):
            raise
        logger.warning("OpenBB historical fetch is unavailable for %s (%s): %s", symbol, asset_type, exc)
        return _history_from_yfinance(symbol, period=period)


def _quote_from_openbb(symbol: str) -> dict:
    try:
        provider = os.getenv("OPENBB_QUOTE_PROVIDER", "yfinance").strip() or "yfinance"
        payload = _openbb_call(("equity", "price", "quote"), symbol=symbol, provider=provider)
        df = _to_df(payload)
        if df.empty:
            raise ValueError(f"OpenBB returned no quote data for {symbol}")
        row = df.iloc[0].to_dict()
        return {str(k): v for k, v in row.items()}
    except Exception as exc:
        if not _is_openbb_runtime_failure(exc):
            raise
        logger.warning("OpenBB quote fetch is unavailable for %s: %s", symbol, exc)
        return _quote_from_yfinance(symbol)


def _search_from_openbb(query: str) -> Optional[str]:
    try:
        provider = os.getenv("OPENBB_SEARCH_PROVIDER", "nasdaq").strip() or "nasdaq"
        payload = _openbb_call(("equity", "search"), query=query, provider=provider)
        df = _to_df(payload)
        if df.empty:
            return None
        symbol_col = next((col for col in df.columns if str(col).lower() in {"symbol", "ticker"}), None)
        if symbol_col is None:
            return None
        for value in df[symbol_col].tolist():
            candidate = str(value or "").strip().upper()
            if candidate:
                return candidate
        return None
    except Exception as exc:
        if not _is_openbb_runtime_failure(exc):
            raise
        logger.warning("OpenBB symbol search is unavailable for %s: %s", query, exc)
        return _search_from_yfinance(query)


def _profile_from_openbb(symbol: str) -> dict:
    try:
        provider = os.getenv("OPENBB_PROFILE_PROVIDER", "fmp").strip() or "fmp"
        payload = _openbb_call(("equity", "profile"), symbol=symbol, provider=provider)
        df = _to_df(payload)
        if df.empty:
            return {}
        return {str(k): v for k, v in df.iloc[0].to_dict().items()}
    except Exception as exc:
        if not _is_openbb_runtime_failure(exc):
            raise
        logger.warning("OpenBB profile fetch is unavailable for %s: %s", symbol, exc)
        return _fundamentals_from_yfinance(symbol).get("profile", {})


def _fundamentals_from_openbb(symbol: str) -> dict:
    try:
        metrics_provider = os.getenv("OPENBB_FUNDAMENTAL_PROVIDER", "fmp").strip() or "fmp"
        profile = _profile_from_openbb(symbol)
        metrics_df = _to_df(
            _openbb_call(("equity", "fundamental", "metrics"), symbol=symbol, provider=metrics_provider)
        )
        ratios_df = _to_df(
            _openbb_call(("equity", "fundamental", "ratios"), symbol=symbol, provider=metrics_provider)
        )
        income_df = _to_df(
            _openbb_call(("equity", "fundamental", "income"), symbol=symbol, provider=metrics_provider)
        )
        balance_df = _to_df(
            _openbb_call(("equity", "fundamental", "balance"), symbol=symbol, provider=metrics_provider)
        )
        cash_df = _to_df(
            _openbb_call(("equity", "fundamental", "cash"), symbol=symbol, provider=metrics_provider)
        )
        return {
            "profile": profile,
            "metrics": metrics_df,
            "ratios": ratios_df,
            "income_statement": income_df,
            "balance_sheet": balance_df,
            "cash_flow": cash_df,
        }
    except Exception as exc:
        if not _is_openbb_runtime_failure(exc):
            raise
        logger.warning("OpenBB fundamentals fetch is unavailable for %s: %s", symbol, exc)
        return _fundamentals_from_yfinance(symbol)


def _options_chain_from_openbb(symbol: str, expiry: Optional[str] = None) -> dict:
    try:
        provider = os.getenv("OPENBB_OPTIONS_PROVIDER", "yfinance").strip() or "yfinance"
        kwargs = {"symbol": symbol, "provider": provider}
        if expiry:
            kwargs["expiration"] = expiry
        payload = _openbb_call(("derivatives", "options", "chains"), **kwargs)
        df = _to_df(payload)
        if df.empty:
            raise ValueError(f"OpenBB returned no options chain for {symbol}")
        normalized = df.copy()
        normalized.columns = [str(col).strip() for col in normalized.columns]
        return {
            "rows": normalized.to_dict(orient="records"),
            "raw": normalized,
        }
    except Exception as exc:
        if not _is_openbb_runtime_failure(exc):
            raise
        logger.warning("OpenBB options chain fetch is unavailable for %s: %s", symbol, exc)
        return _options_chain_from_yfinance(symbol, expiry=expiry)


def _history_from_yfinance(symbol: str, period: str = "1y") -> pd.DataFrame:
    import yfinance as yf

    ticker = yf.Ticker(symbol)
    return _normalize_history_df(ticker.history(period=period, interval="1d", auto_adjust=False))


def _quote_from_yfinance(symbol: str) -> dict:
    import yfinance as yf

    ticker = yf.Ticker(symbol)
    info = ticker.fast_info if hasattr(ticker, "fast_info") else {}
    if hasattr(info, "items"):
        return dict(info.items())
    return dict(info or {})


def _search_from_yfinance(query: str) -> Optional[str]:
    import yfinance as yf

    search = yf.Search(query=query, max_results=5, news_count=0)
    quotes = search.quotes or []
    for quote in quotes:
        symbol = str(quote.get("symbol") or "").strip().upper()
        if symbol:
            return symbol
    return None


def _fundamentals_from_yfinance(symbol: str) -> dict:
    import yfinance as yf

    ticker = yf.Ticker(symbol)
    return {
        "profile": ticker.info or {},
        "metrics": pd.DataFrame([ticker.info or {}]),
        "ratios": pd.DataFrame([ticker.info or {}]),
        "income_statement": ticker.financials,
        "balance_sheet": ticker.balance_sheet,
        "cash_flow": ticker.cashflow,
        "earnings": getattr(ticker, "earnings", pd.DataFrame()),
        "quarterly_earnings": getattr(ticker, "quarterly_earnings", pd.DataFrame()),
    }


def _options_chain_from_yfinance(symbol: str, expiry: Optional[str] = None) -> dict:
    import yfinance as yf

    ticker = yf.Ticker(symbol)
    expirations = ticker.options
    if not expirations:
        raise ValueError(f"No options available for {symbol}")
    target_expiry = expiry or expirations[0]
    if target_expiry not in expirations:
        raise ValueError(f"Expiry {target_expiry} not available for {symbol}")
    chain = ticker.option_chain(target_expiry)
    calls = chain.calls.copy() if chain.calls is not None else pd.DataFrame()
    if not calls.empty:
        calls["optionType"] = "call"
    puts = chain.puts.copy() if chain.puts is not None else pd.DataFrame()
    if not puts.empty:
        puts["optionType"] = "put"
    raw = pd.concat([calls, puts], ignore_index=True, sort=False)
    return {
        "rows": raw.to_dict(orient="records"),
        "raw": raw,
        "expiry": target_expiry,
        "available_expiries": list(expirations[:20]),
    }


def get_price_history(symbol: str, period: str = "1y", asset_type: str = "stock") -> pd.DataFrame:
    try:
        return _history_from_openbb(symbol, period=period, asset_type=asset_type)
    except Exception as exc:
        logger.warning("OpenBB historical fetch failed for %s (%s): %s", symbol, asset_type, exc)
        return _history_from_yfinance(symbol, period=period)


def search_symbol(query: str) -> Optional[str]:
    query = (query or "").strip()
    if not query:
        return None
    try:
        return _search_from_openbb(query)
    except Exception as exc:
        logger.warning("OpenBB symbol search failed for %s: %s", query, exc)
        try:
            return _search_from_yfinance(query)
        except Exception:
            return None


def get_latest_price(symbol: str, asset_type: str = "stock") -> Optional[float]:
    try:
        quote = _quote_from_openbb(symbol)
        for key in ("last_price", "lastPrice", "price", "regularMarketPrice", "close"):
            value = quote.get(key)
            if value not in (None, ""):
                return float(value)
    except Exception as exc:
        logger.warning("OpenBB quote fetch failed for %s: %s", symbol, exc)
    try:
        quote = _quote_from_yfinance(symbol)
        for key in ("lastPrice", "last_price", "regularMarketPrice", "price"):
            value = quote.get(key)
            if value not in (None, ""):
                return float(value)
    except Exception:
        pass
    try:
        hist = get_price_history(symbol, period="5d", asset_type=asset_type)
        if not hist.empty:
            return float(hist["Close"].dropna().iloc[-1])
    except Exception:
        return None
    return None


def get_price_near_date(symbol: str, trade_date: str, asset_type: str = "stock") -> Optional[float]:
    try:
        start = datetime.strptime(trade_date[:10], "%Y-%m-%d").date()
    except Exception:
        return None
    try:
        hist = get_price_history(symbol, period="10y", asset_type=asset_type)
        if hist.empty:
            return None
        window = hist.loc[str(start):str(start + timedelta(days=5))]
        if window.empty:
            return None
        return float(window["Close"].dropna().iloc[0])
    except Exception:
        return None


def get_company_profile(symbol: str) -> dict:
    try:
        return _profile_from_openbb(symbol)
    except Exception as exc:
        logger.warning("OpenBB profile fetch failed for %s: %s", symbol, exc)
        return _fundamentals_from_yfinance(symbol).get("profile", {})


def get_fundamental_dataset(symbol: str) -> dict:
    try:
        return _fundamentals_from_openbb(symbol)
    except Exception as exc:
        logger.warning("OpenBB fundamentals fetch failed for %s: %s", symbol, exc)
        return _fundamentals_from_yfinance(symbol)


def get_options_chain_dataset(symbol: str, expiry: Optional[str] = None) -> dict:
    try:
        return _options_chain_from_openbb(symbol, expiry=expiry)
    except Exception as exc:
        logger.warning("OpenBB options chain fetch failed for %s: %s", symbol, exc)
        return _options_chain_from_yfinance(symbol, expiry=expiry)
