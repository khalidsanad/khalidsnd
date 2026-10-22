"""Licensed Binance USD-M perpetual history via CoinAPI (read-only).

CoinAPI is a distinct data vendor. Its normalized OHLCV bars may differ from
native exchange bars; never silently label the results as Binance REST data.
An API key is required and must be configured outside the repository.
"""

from __future__ import annotations

from datetime import datetime, timezone
import math
import re
from threading import RLock
import time

import numpy as np
import pandas as pd
import requests

from data_sources import (CORE_SYMBOLS, ESTABLISHED_SYMBOLS, DataError,
                          INTERVAL_MINUTES, validate_symbol)

COINAPI_BASE = "https://rest.coinapi.io"
PERIODS = {"5m": "5MIN", "15m": "15MIN", "1h": "1HRS", "4h": "4HRS"}
_ASSET = re.compile(r"^[A-Z]{2,12}$")


def _fetch(path: str, key: str, params: dict | None = None):
    if not key or not key.strip():
        raise DataError("CoinAPI key is missing. Add COINAPI_KEY in Streamlit Cloud app secrets.")
    try:
        response = requests.get(
            f"{COINAPI_BASE}{path}", params=params,
            headers={"X-CoinAPI-Key": key.strip(),
                     "User-Agent": "FuturesResearchDashboard/2.3 (paper research)"},
            timeout=(5, 25))
        if response.status_code in (401, 403):
            raise DataError("CoinAPI rejected the key or this data entitlement (HTTP "
                            f"{response.status_code}). Verify the subscription and app secret.")
        if response.status_code == 429:
            raise DataError("CoinAPI rate/credit limit reached (HTTP 429). Try later or review usage.")
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        raise DataError(f"CoinAPI request failed ({path}): {exc}") from exc
    except ValueError as exc:
        raise DataError("CoinAPI returned invalid JSON") from exc


def coinapi_symbol_id(symbol: str) -> str:
    """The BinanceFTS perpetual ID; never use Binance spot or dated futures."""
    validate_symbol(symbol)
    base = symbol.removesuffix("USDT")
    if not _ASSET.fullmatch(base):
        raise ValueError("CoinAPI perpetual symbols must have a regular base asset")
    return f"BINANCEFTS_PERP_{base}_USDT"


def coinapi_candles(symbol: str, count: int, interval: str, key: str,
                    *, now: datetime | None = None) -> pd.DataFrame:
    """Return fully closed bars with the same schema as get_candles().

    Fetch one bounded ascending window, then validate continuity separately in
    the application's data_quality guard.  No fabricated bars or interpolations.
    """
    coinapi_id = coinapi_symbol_id(symbol)
    if interval not in PERIODS:
        raise ValueError("Unsupported candle timeframe")
    if not 300 <= count <= 6000:
        raise ValueError("Count must be between 300 and 6000")
    instant = pd.Timestamp(now or datetime.now(timezone.utc))
    if instant.tzinfo is None:
        raise ValueError("Current time must carry a timezone")
    instant = instant.tz_convert("UTC")
    # A strict completed-period cutoff avoids mixing an active candle into a
    # backtest, regardless of the provider's real-time aggregation delay.
    minutes = INTERVAL_MINUTES[interval]
    cutoff = instant.floor(f"{minutes}min")
    start = cutoff - pd.Timedelta(minutes=(count + 72) * minutes)
    rows = _fetch(
        f"/v1/ohlcv/{coinapi_id}/history", key,
        {"period_id": PERIODS[interval],
         "time_start": start.isoformat(), "time_end": cutoff.isoformat(),
         "limit": count + 72})
    return _parse_candles(rows, interval, cutoff, count, symbol)


def _parse_candles(rows: object, interval: str, cutoff: pd.Timestamp,
                   count: int, symbol: str) -> pd.DataFrame:
    """Validate provider bars regardless of whether they were full or incremental."""
    minutes = INTERVAL_MINUTES[interval]
    if not isinstance(rows, list) or not rows:
        raise DataError(f"CoinAPI returned no {interval} historical bars for {symbol}")
    if not all(isinstance(item, dict) for item in rows):
        raise DataError("Unexpected CoinAPI candle response")
    frame = pd.DataFrame(rows)
    field_names = {"time_period_start": "open_time", "time_period_end": "period_end",
                   "price_open": "open", "price_high": "high", "price_low": "low",
                   "price_close": "close", "volume_traded": "volume"}
    if any(key not in frame.columns for key in field_names):
        raise DataError("CoinAPI candle response is missing OHLCV fields")
    frame = frame[list(field_names)].rename(columns=field_names)
    try:
        opens = pd.to_datetime(frame.pop("open_time"), utc=True, errors="raise")
        ends = pd.to_datetime(frame.pop("period_end"), utc=True, errors="raise")
        for column in ("open", "high", "low", "close", "volume"):
            frame[column] = pd.to_numeric(frame[column], errors="raise")
    except (ValueError, TypeError, OverflowError) as exc:
        raise DataError("Invalid CoinAPI candle values or timestamps") from exc
    frame.index = pd.DatetimeIndex(opens, name="open_time_utc")
    frame["period_end"] = pd.DatetimeIndex(ends)
    frame = frame.sort_index()
    if frame.index.has_duplicates:
        raise DataError("Duplicate CoinAPI candle timestamps")
    duration = pd.Timedelta(minutes=minutes)
    if not (frame["period_end"] == frame.index + duration).all():
        raise DataError("Unexpected CoinAPI candle interval boundaries")
    # Last millisecond is the exchange-style inclusive close timestamp used by
    # the existing dashboard.  Provider's period_end itself is exclusive.
    frame = frame.loc[frame["period_end"] <= cutoff].tail(count).copy()
    frame["close_time"] = frame.pop("period_end") - pd.Timedelta(milliseconds=1)
    numeric = frame[["open", "high", "low", "close", "volume"]]
    valid = numeric.notna().all(axis=1)
    valid &= np.isfinite(numeric.to_numpy(dtype=float)).all(axis=1)
    valid &= (frame["low"] > 0) & (frame["volume"] >= 0)
    valid &= frame["high"] >= frame[["open", "close", "low"]].max(axis=1)
    valid &= frame["low"] <= frame[["open", "close", "high"]].min(axis=1)
    if frame.empty or not valid.all():
        raise DataError("CoinAPI candle validation failed")
    return frame


def _recent_candles(symbol: str, interval: str, key: str,
                    from_open: pd.Timestamp, cutoff: pd.Timestamp) -> pd.DataFrame:
    minutes = INTERVAL_MINUTES[interval]
    number = int((cutoff - from_open) / pd.Timedelta(minutes=minutes))
    if number <= 0 or number > 1000:
        raise ValueError("Incremental history window is outside its supported size")
    rows = _fetch(f"/v1/ohlcv/{coinapi_symbol_id(symbol)}/history", key,
                  {"period_id": PERIODS[interval],
                   "time_start": from_open.isoformat(),
                   "time_end": cutoff.isoformat(), "limit": number + 2})
    return _parse_candles(rows, interval, cutoff, number + 2, symbol)


class CoinAPICandleStore:
    """Shared process-local cache with one-credit-sized incremental updates.

    Streamlit caches final render results separately; this store amortizes the
    expensive initial history across timeframes, visitors and refresh clicks.
    A new process may require one new full download. No disk or API key cache.
    """

    def __init__(self):
        self._lock = RLock()
        self._entries: dict[tuple[str, str], dict] = {}

    def get(self, symbol: str, count: int, interval: str, key: str,
            *, now: datetime | None = None) -> pd.DataFrame:
        coinapi_symbol_id(symbol)
        if interval not in PERIODS or not 300 <= count <= 6000:
            raise ValueError("Unsupported timeframe or candle count")
        instant = pd.Timestamp(now or datetime.now(timezone.utc))
        if instant.tzinfo is None:
            raise ValueError("Current time must carry a timezone")
        cutoff = instant.tz_convert("UTC").floor(f"{INTERVAL_MINUTES[interval]}min")
        key_tuple = (symbol, interval)
        with self._lock:
            entry = self._entries.get(key_tuple)
            if entry is None or entry["requested"] < count:
                frame = coinapi_candles(symbol, count, interval, key,
                                        now=instant.to_pydatetime())
                self._entries[key_tuple] = {"frame": frame,
                                            "requested": count,
                                            "error": None,
                                            "last_attempt": time.monotonic()}
            else:
                frame = entry["frame"]
                period = pd.Timedelta(minutes=INTERVAL_MINUTES[interval])
                if (entry.get("error")
                        and time.monotonic() - entry["last_attempt"] < 60):
                    raise DataError(entry["error"])
                # CoinAPI candles may arrive a few seconds after their period
                # ends.  Retry at most once a minute while the latest is late.
                if (frame.index[-1] + period <= cutoff
                        and time.monotonic() - entry["last_attempt"] >= 60):
                    entry["last_attempt"] = time.monotonic()
                    remaining = int((cutoff - frame.index[-1]) / period)
                    try:
                        if remaining > 1000:
                            frame = coinapi_candles(symbol, max(count, len(frame)),
                                                    interval, key, now=instant.to_pydatetime())
                        else:
                            recent = _recent_candles(symbol, interval, key, frame.index[-1], cutoff)
                            frame = pd.concat([frame, recent])
                            frame = frame[~frame.index.duplicated(keep="last")].sort_index()
                            frame = frame.tail(6000)
                    except DataError as exc:
                        entry["error"] = str(exc)
                        raise
                    entry["frame"] = frame
                    entry["error"] = None
            return frame.tail(count).copy()


def coinapi_contract_catalog(key: str) -> list[dict]:
    """Discover active BinanceFTS perps, using only provider-verified fields."""
    symbols = _fetch("/v1/symbols/BINANCEFTS/active", key)
    if not isinstance(symbols, list):
        raise DataError("CoinAPI returned an invalid active-symbol catalog")
    now = datetime.now(timezone.utc)
    result = []
    seen = set()
    for item in symbols:
        if not isinstance(item, dict):
            continue
        base = item.get("asset_id_base")
        venue_symbol = item.get("symbol_id_exchange")
        if (item.get("exchange_id") != "BINANCEFTS"
                or item.get("symbol_type") != "PERPETUAL"
                or item.get("asset_id_quote") != "USDT"
                or not isinstance(base, str) or not _ASSET.fullmatch(base)):
            continue
        symbol = f"{base}USDT"
        # Native exchange symbol is checked to avoid mapped/multiplier pairs.
        if (venue_symbol != symbol or symbol in seen or "ALPHA" in symbol):
            continue
        identifier = item.get("symbol_id")
        if identifier != coinapi_symbol_id(symbol):
            continue
        try:
            first = pd.Timestamp(item.get("data_trade_start") or item["data_start"])
            if first.tzinfo is None:
                first = first.tz_localize("UTC")
            age_days = (now - first.to_pydatetime()).total_seconds() / 86400
            price = float(item["price"])
            volume = float(item["volume_1day_usd"])
            if not all(map(math.isfinite, (price, volume, age_days))):
                continue
            if price <= 0 or volume < 0 or age_days < 0:
                continue
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        seen.add(symbol)
        result.append({"symbol": symbol, "base": base, "quote_volume_usdt": volume,
                       "last_price": price, "change_pct": None,
                       "listing_days": int(age_days),
                       "established_watchlist": symbol in ESTABLISHED_SYMBOLS,
                       "core": symbol in CORE_SYMBOLS})
    result.sort(key=lambda v: (not v["core"], not v["established_watchlist"],
                               -v["quote_volume_usdt"]))
    return result
