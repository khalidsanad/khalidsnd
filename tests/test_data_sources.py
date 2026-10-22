"""No-network checks for public symbol discovery and variable candle intervals."""

from unittest import TestCase
from unittest.mock import patch

import pandas as pd

from data_sources import (data_quality, get_candles, get_contract_catalog,
                          get_live_price, screen_contracts, validate_symbol, DataError)


class MarketCatalogTests(TestCase):
    def test_active_liquid_aged_usdt_perpetual_only(self):
        current = 1_800_000_000_000

        def contract(symbol, base, days=350, status="TRADING", kind="PERPETUAL", subtype=None):
            return {"symbol": symbol, "baseAsset": base, "quoteAsset": "USDT",
                    "status": status, "contractType": kind,
                    "onboardDate": current - days * 86_400_000,
                    "underlyingSubType": subtype or []}

        exchange = {"symbols": [contract("BTCUSDT", "BTC"),
                                 contract("LINKUSDT", "LINK"),
                                 contract("NEWUSDT", "NEW", days=4),
                                 contract("MEMEUSDT", "MEME", subtype=["MEME"]),
                                 contract("ALPHAUSDT", "ALPHA"),
                                 contract("1000PEPEUSDT", "1000PEPE"),
                                 contract("XRPUSDT", "XRP", kind="CURRENT_QUARTER"),
                                 contract("STOPUSDT", "STOP", status="BREAK")]}
        tickers = [{"symbol": sym, "quoteVolume": "50000000", "lastPrice": "10",
                    "priceChangePercent": "-2"}
                   for sym in ("BTCUSDT", "LINKUSDT", "NEWUSDT", "MEMEUSDT",
                               "ALPHAUSDT", "1000PEPEUSDT", "XRPUSDT", "STOPUSDT")]

        def fetch(path, params=None):
            return {"/fapi/v1/exchangeInfo": exchange,
                    "/fapi/v1/ticker/24hr": tickers,
                    "/fapi/v1/time": {"serverTime": current}}[path]

        with patch("data_sources._get", side_effect=fetch):
            records = get_contract_catalog()
        self.assertEqual({x["symbol"] for x in records}, {"BTCUSDT", "LINKUSDT", "NEWUSDT"})
        screened = screen_contracts(records, min_age_days=180, min_volume_usdt=20_000_000)
        self.assertEqual({x["symbol"] for x in screened}, {"BTCUSDT", "LINKUSDT"})

    def test_symbol_syntax_rejects_invalid_symbols(self):
        for candidate in ("", "BTC/USDT", "BTCUSDT?limit=3", "btcUSDT", "../../etc", "ETHBTC"):
            with self.assertRaises(ValueError):
                validate_symbol(candidate)
        validate_symbol("BTCUSDT")


class CandleTests(TestCase):
    def test_completed_5m_bars_and_requested_interval(self):
        now = 1_800_000_000_000
        first_open = (now // 300_000 - 10) * 300_000
        requests = []

        def fake_get(path, params=None):
            if path == "/fapi/v1/time":
                return {"serverTime": now}
            requests.append(params)
            return [[first_open + i * 300_000, "10", "12", "9", "11", "100",
                     first_open + i * 300_000 + 299_999] + ["0"] * 5
                    for i in range(12)]

        with patch("data_sources._get", side_effect=fake_get):
            frame = get_candles("BTCUSDT", 300, "5m")
        self.assertEqual(requests[0]["interval"], "5m")
        self.assertLess(frame["close_time"].max(), pd.to_datetime(now, unit="ms", utc=True))
        self.assertEqual(data_quality(frame, "5m")["gaps"], 0)

    def test_gap_detection_uses_selected_interval(self):
        idx = pd.DatetimeIndex(["2026-01-01T00:00:00Z", "2026-01-01T00:05:00Z",
                                "2026-01-01T00:15:00Z"])
        df = pd.DataFrame({"close_time": idx}, index=idx)
        self.assertEqual(data_quality(df, "5m")["gaps"], 1)


class PriceRestTests(TestCase):
    def test_rest_fallback_uses_v2_and_actual_exchange_timestamp(self):
        exchange_ms = 1_800_000_000_123
        with patch("data_sources._get", return_value={"symbol": "BTCUSDT",
                  "price": "82532.10", "time": exchange_ms}) as request:
            quote = get_live_price("BTCUSDT")
        request.assert_called_once_with("/fapi/v2/ticker/price", {"symbol": "BTCUSDT"})
        self.assertEqual(quote["price"], 82532.10)
        self.assertEqual(int(quote["exchange_utc"].timestamp() * 1000), exchange_ms)

    def test_rest_fallback_rejects_bad_price_or_wrong_symbol(self):
        for payload in ({"symbol": "ETHUSDT", "price": "82532", "time": 1800000000123},
                        {"symbol": "BTCUSDT", "price": "nan", "time": 1800000000123},
                        {"symbol": "BTCUSDT", "price": "82532"}):
            with patch("data_sources._get", return_value=payload):
                with self.assertRaises(DataError):
                    get_live_price("BTCUSDT")

