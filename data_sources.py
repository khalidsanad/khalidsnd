"""Read-only public market/news data. No credentials or order endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
import math
import re
from urllib.parse import quote

import feedparser
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = "https://fapi.binance.com"
CORE_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT")
ESTABLISHED_SYMBOLS = CORE_SYMBOLS + (
    "XRPUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT", "LTCUSDT", "DOTUSDT",
    "BCHUSDT", "TRXUSDT", "XLMUSDT", "AAVEUSDT", "UNIUSDT", "NEARUSDT",
)
SYMBOLS = CORE_SYMBOLS  # Backward-compatible name for the original four.
INTERVAL_MINUTES = {"5m": 5, "15m": 15, "1h": 60, "4h": 240}
_SYMBOL_FORMAT = re.compile(r"^[A-Z0-9]{2,20}USDT$")


class DataError(RuntimeError):
    """Raised when an upstream source cannot be trusted or reached."""


def _session() -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = "FuturesResearchDashboard/1.0 (educational; no orders)"
    retries = Retry(total=2, connect=2, read=1, backoff_factor=0.8,
                    status_forcelist=(500, 502, 503, 504))
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session


def _get(path: str, params: dict | None = None):
    try:
        response = _session().get(f"{BASE_URL}{path}", params=params, timeout=(5, 15))
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        raise DataError(f"Binance request failed ({path}): {exc}") from exc


def server_time_ms() -> int:
    raw = _get("/fapi/v1/time")
    return int(raw["serverTime"])


def validate_symbol(symbol: str) -> None:
    if not isinstance(symbol, str) or not _SYMBOL_FORMAT.fullmatch(symbol):
        raise ValueError("Only valid Binance USDT futures symbols are accepted")


def get_candles(symbol: str, count: int = 2400, interval: str = "1h") -> pd.DataFrame:
    """Load the newest N fully closed Binance USD-M candles, oldest first.

    The caller selects symbols from get_contract_catalog; no raw symbol input is
    accepted in the UI. This routine also validates symbol syntax defensively.
    """
    validate_symbol(symbol)
    if interval not in INTERVAL_MINUTES:
        raise ValueError("Unsupported candle timeframe")
    if not 300 <= count <= 6000:
        raise ValueError("Count must be between 300 and 6000")

    current_ms = server_time_ms()
    end_ms = current_ms
    rows = []
    while len(rows) < count + 1:
        size = min(1500, count + 1 - len(rows))
        batch = _get("/fapi/v1/klines", {"symbol": symbol, "interval": interval,
                                         "limit": size, "endTime": end_ms})
        if not isinstance(batch, list) or not batch:
            break
        rows = batch + rows
        earlier = int(batch[0][0]) - 1
        if earlier >= end_ms:
            raise DataError("Pagination did not advance")
        end_ms = earlier
        if len(batch) < size:
            break

    if not rows:
        raise DataError(f"No candles returned for {symbol}")
    frame = pd.DataFrame(rows)
    frame = frame.iloc[:, [0, 1, 2, 3, 4, 5, 6]].copy()
    frame.columns = ["open_ms", "open", "high", "low", "close", "volume", "close_ms"]
    frame["open_ms"] = pd.to_numeric(frame["open_ms"], errors="raise").astype("int64")
    frame["close_ms"] = pd.to_numeric(frame["close_ms"], errors="raise").astype("int64")
    for col in ("open", "high", "low", "close", "volume"):
        frame[col] = pd.to_numeric(frame[col], errors="raise")
    frame = frame.drop_duplicates("open_ms").sort_values("open_ms")
    frame = frame.loc[frame["close_ms"] < current_ms].tail(count).copy()
    frame.index = pd.to_datetime(frame.pop("open_ms"), unit="ms", utc=True)
    frame.index.name = "open_time_utc"
    frame["close_time"] = pd.to_datetime(frame.pop("close_ms"), unit="ms", utc=True)

    valid = (frame["low"] > 0) & (frame["volume"] >= 0)
    valid &= frame["high"] >= frame[["open", "close", "low"]].max(axis=1)
    valid &= frame["low"] <= frame[["open", "close", "high"]].min(axis=1)
    if not valid.all() or frame.empty:
        raise DataError("Exchange candle validation failed")
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise DataError("Duplicate or unsorted candle timestamps")
    return frame


def data_quality(frame: pd.DataFrame, interval: str = "1h") -> dict:
    if interval not in INTERVAL_MINUTES:
        raise ValueError("Unsupported timeframe")
    gaps_min = frame.index.to_series().diff().dt.total_seconds().div(60)
    gaps = int((gaps_min.dropna() != INTERVAL_MINUTES[interval]).sum())
    return {"gaps": gaps, "count": len(frame),
            "last_complete_utc": str(frame["close_time"].iloc[-1])}


def get_derivatives_context(symbol: str) -> dict:
    """Snapshots only: deliberately not fed into a historical backtest."""
    validate_symbol(symbol)
    context = {"as_of_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "errors": []}
    endpoints = (("premium", "/fapi/v1/premiumIndex"),
                 ("interest", "/fapi/v1/openInterest"),
                 ("ticker", "/fapi/v1/ticker/24hr"))
    for key, path in endpoints:
        try:
            context[key] = _get(path, {"symbol": symbol})
        except (DataError, KeyError, TypeError) as exc:
            context["errors"].append(str(exc))
    return context


def get_live_price(symbol: str) -> dict:
    """Independent public REST fallback, including Binance trade timestamp.

    Binance marks v1 ticker/price as deprecated; use v2 and do not describe
    the local retrieval time as if it were the actual exchange trade time.
    """
    validate_symbol(symbol)
    value = _get("/fapi/v2/ticker/price", {"symbol": symbol})
    try:
        if not isinstance(value, dict) or value["symbol"] != symbol:
            raise ValueError("wrong symbol")
        price = float(value["price"])
        if not math.isfinite(price) or price <= 0:
            raise ValueError("invalid price")
        event_ms = int(value["time"])
        if event_ms <= 0:
            raise ValueError("invalid trade time")
        exchange_utc = datetime.fromtimestamp(event_ms / 1000, tz=timezone.utc)
    except (ValueError, TypeError, KeyError, OverflowError, OSError) as exc:
        raise DataError("Invalid Binance Futures REST price data") from exc
    return {"price": price, "exchange_utc": exchange_utc,
            "as_of_utc": datetime.now(timezone.utc)}


def get_contract_catalog() -> list[dict]:
    """Discover active USDT perpetual listings and their 24h quote liquidity.

    A liquidity/history screen is an aid, NOT a safety rating or endorsement.
    No prices, ages or trading eligibility are invented on data failure.
    """
    exchange = _get("/fapi/v1/exchangeInfo")
    tickers = _get("/fapi/v1/ticker/24hr")
    if not isinstance(exchange, dict) or not isinstance(exchange.get("symbols"), list):
        raise DataError("Unexpected Binance exchangeInfo payload")
    if not isinstance(tickers, list):
        raise DataError("Unexpected 24h ticker payload")
    by_symbol = {x.get("symbol"): x for x in tickers if isinstance(x, dict)}
    current_ms = server_time_ms()
    records = []
    for info in exchange["symbols"]:
        if not isinstance(info, dict):
            continue
        symbol, base = info.get("symbol", ""), info.get("baseAsset", "")
        if (info.get("status") != "TRADING" or info.get("contractType") != "PERPETUAL"
                or info.get("quoteAsset") != "USDT" or not isinstance(symbol, str)
                or not _SYMBOL_FORMAT.fullmatch(symbol)):
            continue
        # Exclude leveraged multiplier contracts and obvious launchpad/meme tags.
        # This cannot identify every risky or problematic project.
        subtypes = [str(x).upper() for x in (info.get("underlyingSubType") or [])]
        if (not re.fullmatch(r"[A-Z]{2,12}", str(base)) or "ALPHA" in symbol
                or "MEME" in subtypes or "PREMARKET" in subtypes):
            continue
        ticker = by_symbol.get(symbol)
        if not isinstance(ticker, dict):
            continue
        try:
            volume = float(ticker["quoteVolume"])
            last_price = float(ticker["lastPrice"])
            change = float(ticker["priceChangePercent"])
            onboard_ms = int(info["onboardDate"])
        except (ValueError, KeyError, TypeError):
            continue
        age_days = (current_ms - onboard_ms) / 86_400_000
        if not (volume >= 0 and last_price > 0 and age_days >= 0):
            continue
        records.append({"symbol": symbol, "base": base, "quote_volume_usdt": volume,
                        "last_price": last_price, "change_pct": change,
                        "listing_days": int(age_days),
                        "established_watchlist": symbol in ESTABLISHED_SYMBOLS,
                        "core": symbol in CORE_SYMBOLS})
    records.sort(key=lambda v: (not v["core"], not v["established_watchlist"], -v["quote_volume_usdt"]))
    return records


def screen_contracts(catalog: list[dict], min_age_days: int = 180,
                     min_volume_usdt: float = 20_000_000, max_results: int = 80) -> list[dict]:
    """Present established watchlist first, then Binance contracts by volume."""
    eligible = [x for x in catalog if x["listing_days"] >= min_age_days
                and x["quote_volume_usdt"] >= min_volume_usdt]
    eligible.sort(key=lambda x: (not x["core"], not x["established_watchlist"],
                                 -x["quote_volume_usdt"]))
    return eligible[:max_results]


NEWS_QUERIES = {
    "BTCUSDT": 'Bitcoin OR BTC cryptocurrency',
    "ETHUSDT": 'Ethereum OR ETH cryptocurrency',
    "SOLUSDT": 'Solana OR SOL cryptocurrency',
    "BNBUSDT": 'BNB OR Binance Coin cryptocurrency',
    "MACRO": 'cryptocurrency regulation OR federal reserve OR crypto ETF OR inflation',
}


def possible_relevance(title: str) -> list[str]:
    lower = title.lower()
    terms = {
        "Regulatory / legal": ("sec ", "regulat", "lawsuit", "ban ", "court", "legislation"),
        "Macro / rates": ("federal reserve", "fed ", "inflation", "cpi", "jobs report", "rates"),
        "ETF / institutions": ("etf", "institution", "blackrock", "fund inflow", "fund outflow"),
        "Security / operations": ("hack", "exploit", "outage", "breach", "liquidat"),
        "Network / protocol": ("upgrade", "mainnet", "network", "validator", "staking"),
    }
    return [label for label, needles in terms.items() if any(x in lower for x in needles)] or ["General market news"]


def get_news(symbol: str, max_items: int = 28) -> tuple[list[dict], list[str]]:
    """Recent linked headlines. Heuristic relevance; NOT inferred price causation."""
    validate_symbol(symbol)
    findings, warnings, seen = [], [], set()
    for topic in (symbol, "MACRO"):
        asset_query = NEWS_QUERIES.get(topic, f"{symbol.removesuffix('USDT')} cryptocurrency")
        query = quote(asset_query + " when:7d")
        url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
        try:
            response = _session().get(url, timeout=(5, 15))
            response.raise_for_status()
            feed = feedparser.parse(response.content)
            if feed.bozo and not feed.entries:
                raise ValueError("Invalid RSS feed")
            for entry in feed.entries:
                link = str(entry.get("link", ""))
                title = str(entry.get("title", "")).strip()
                if not link.startswith("https://") or not title:
                    continue
                key = title.casefold()
                if key in seen:
                    continue
                seen.add(key)
                published = entry.get("published_parsed")
                ts = (pd.Timestamp(datetime(*published[:6], tzinfo=timezone.utc))
                      if published else pd.NaT)
                findings.append({"published_utc": ts, "title": title, "url": link,
                                 "topic": "Asset" if topic == symbol else "Macro",
                                 "potential_factors": ", ".join(possible_relevance(title))})
        except (requests.RequestException, ValueError, TypeError) as exc:
            warnings.append(f"News feed {topic} unavailable: {exc}")
    findings.sort(key=lambda item: item["published_utc"] if pd.notna(item["published_utc"])
                  else pd.Timestamp("1970-01-01", tz="UTC"), reverse=True)
    return findings[:max_items], warnings
