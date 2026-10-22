"""Offline checks for licensed historical BinanceFTS candle integration."""

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
from streamlit.testing.v1 import AppTest

from coinapi_data import (CoinAPICandleStore, coinapi_candles, coinapi_contract_catalog,
                          coinapi_symbol_id, _fetch)
from data_sources import DataError, data_quality


class CoinAPICandlesTests(unittest.TestCase):
    def test_symbol_mapping_is_binance_usdm_perpetual_not_spot(self):
        self.assertEqual(coinapi_symbol_id("BTCUSDT"), "BINANCEFTS_PERP_BTC_USDT")
        with self.assertRaises(ValueError):
            coinapi_symbol_id("BTC/USDT")
        with self.assertRaises(ValueError):
            coinapi_symbol_id("1000PEPEUSDT")

    def test_missing_key_never_makes_external_request(self):
        with patch("coinapi_data.requests.get") as request:
            with self.assertRaisesRegex(DataError, "key is missing"):
                _fetch("/v1/symbols/BINANCEFTS/active", "")
            request.assert_not_called()

    def test_header_hides_key_and_rate_limit_is_reported(self):
        with patch("coinapi_data.requests.get") as get:
            response = get.return_value
            response.status_code = 429
            with self.assertRaisesRegex(DataError, "rate/credit limit"):
                _fetch("/v1/ohlcv/BINANCEFTS_PERP_BTC_USDT/history", "private-test-key")
            self.assertEqual(get.call_args.kwargs["headers"]["X-CoinAPI-Key"],
                             "private-test-key")
            self.assertNotIn("private-test-key", get.call_args.args[0])

    def test_1h_candles_drop_open_bar_and_preserve_utc_boundaries(self):
        now = datetime(2026, 10, 22, 8, 25, tzinfo=timezone.utc)
        start = now.replace(hour=5, minute=0)
        rows = []
        for hour in range(4):
            open_at = start + timedelta(hours=hour)
            rows.append({"time_period_start": open_at.isoformat(),
                         "time_period_end": (open_at + timedelta(hours=1)).isoformat(),
                         "price_open": 80 + hour, "price_high": 83 + hour,
                         "price_low": 79 + hour, "price_close": 82 + hour,
                         "volume_traded": 123.0})
        with patch("coinapi_data._fetch", return_value=rows) as fetch:
            result = coinapi_candles("BTCUSDT", 300, "1h", "fake", now=now)
        self.assertEqual(fetch.call_args.args[0],
                         "/v1/ohlcv/BINANCEFTS_PERP_BTC_USDT/history")
        self.assertEqual(fetch.call_args.args[2]["period_id"], "1HRS")
        self.assertEqual(len(result), 3)  # 08:00–09:00 still open
        self.assertEqual(result.index[-1], pd.Timestamp("2026-10-22T07:00:00Z"))
        self.assertEqual(result["close_time"].iloc[-1],
                         pd.Timestamp("2026-10-22T07:59:59.999Z"))
        self.assertEqual(data_quality(result)["gaps"], 0)

    def test_bad_bar_is_rejected_not_fabricated(self):
        rows = [{"time_period_start": "2026-10-22T05:00:00Z",
                 "time_period_end": "2026-10-22T06:00:00Z", "price_open": 10,
                 "price_high": 9, "price_low": 8, "price_close": 10,
                 "volume_traded": 300}]
        with patch("coinapi_data._fetch", return_value=rows):
            with self.assertRaisesRegex(DataError, "validation failed"):
                coinapi_candles("BTCUSDT", 300, "1h", "fake",
                                now=datetime(2026, 10, 22, 8, tzinfo=timezone.utc))

    def test_shared_store_reuses_history_then_updates_just_new_bars(self):
        def frame_at(hours):
            times = pd.DatetimeIndex(
                [pd.Timestamp(f"2026-10-22T{hour:02}:00:00Z") for hour in hours],
                name="open_time_utc")
            return pd.DataFrame({
                "open": [100.] * len(hours), "high": [101.] * len(hours),
                "low": [99.] * len(hours), "close": [100.] * len(hours),
                "volume": [10.] * len(hours),
                "close_time": times + pd.Timedelta(hours=1) - pd.Timedelta(milliseconds=1),
            }, index=times)

        store = CoinAPICandleStore()
        first = datetime(2026, 10, 22, 8, 25, tzinfo=timezone.utc)
        later = datetime(2026, 10, 22, 9, 25, tzinfo=timezone.utc)
        base = frame_at([5, 6, 7])
        recent = frame_at([7, 8])
        with patch("coinapi_data.coinapi_candles", return_value=base) as full, \
             patch("coinapi_data._recent_candles", return_value=recent) as incremental, \
             patch("coinapi_data.time.monotonic") as monotonic:
            monotonic.return_value = 100.0
            self.assertEqual(len(store.get("BTCUSDT", 300, "1h", "fake", now=first)), 3)
            monotonic.return_value = 170.0
            merged = store.get("BTCUSDT", 300, "1h", "fake", now=later)
            self.assertEqual(len(merged), 4)
            store.get("BTCUSDT", 300, "1h", "fake", now=later)
        self.assertEqual(full.call_count, 1)
        self.assertEqual(incremental.call_count, 1)
        self.assertEqual(merged.index[-1], pd.Timestamp("2026-10-22T08:00:00Z"))


class CoinAPICatalogTests(unittest.TestCase):
    def test_only_provider_verified_active_usdt_perps_are_selected(self):
        def record(base="BTC", native="BTCUSDT", typ="PERPETUAL",
                   quote="USDT", days=400):
            return {"exchange_id": "BINANCEFTS", "symbol_type": typ,
                    "asset_id_quote": quote, "asset_id_base": base,
                    "symbol_id_exchange": native,
                    "symbol_id": f"BINANCEFTS_{'PERP' if typ == 'PERPETUAL' else 'SPOT'}_{base}_{quote}",
                    "data_start": (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(),
                    "price": 82000, "volume_1day_usd": 75_000_000}
        rows = [record(), record(base="ETH", native="ETHUSDT"),
                record(base="BTC", native="BTCUSD", quote="USD"),
                record(base="SOL", native="SOLUSDT", typ="SPOT"),
                record(base="1000PEPE", native="1000PEPEUSDT")]
        with patch("coinapi_data._fetch", return_value=rows):
            catalog = coinapi_contract_catalog("fake")
        self.assertEqual({x["symbol"] for x in catalog}, {"BTCUSDT", "ETHUSDT"})
        self.assertTrue(all(x["listing_days"] >= 399 for x in catalog))


class LicensedAppSmokeTest(unittest.TestCase):
    def test_dashboard_renders_with_private_coinapi_key_and_mock_history(self):
        def fake_candles(symbol, count, interval, _key):
            minutes = {"5m": 5, "15m": 15, "1h": 60}[interval]
            periods = 1200 if interval == "1h" else 800
            idx = pd.date_range("2026-01-01", periods=periods,
                                freq=f"{minutes}min", tz="UTC", name="open_time_utc")
            prices = 100 + np.arange(periods) * 0.03 + np.sin(np.arange(periods) / 10)
            return pd.DataFrame({"open": prices - 0.2, "high": prices + 0.8,
                                 "low": prices - 0.8, "close": prices,
                                 "volume": [1000.] * periods,
                                 "close_time": idx + pd.Timedelta(minutes=minutes)
                                               - pd.Timedelta(milliseconds=1)}, index=idx)

        catalog = [{"symbol": "BTCUSDT", "base": "BTC", "quote_volume_usdt": 70_000_000,
                    "last_price": 80_000, "change_pct": None,
                    "listing_days": 1000, "established_watchlist": True, "core": True}]
        with patch.dict("os.environ", {"COINAPI_KEY": "unit-test-key"}), \
             patch("coinapi_data.CoinAPICandleStore.get", side_effect=fake_candles), \
             patch("coinapi_data.coinapi_contract_catalog", return_value=catalog), \
             patch("live_feed.LiveFuturesFeed.start"), \
             patch("data_sources.get_live_price", return_value={
                 "price": 80_000, "exchange_utc": datetime.now(timezone.utc)}), \
             patch("data_sources.get_news", return_value=([], [])):
            test = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "app.py"),
                                     default_timeout=30).run()
        self.assertFalse(test.exception, str(test.exception))


if __name__ == "__main__":
    unittest.main()
