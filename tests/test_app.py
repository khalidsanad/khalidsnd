"""Launch smoke test without external network connections."""

import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from pathlib import Path

import numpy as np
import pandas as pd
from streamlit.testing.v1 import AppTest


class DashboardSmokeTest(unittest.TestCase):
    def test_dashboard_renders_from_mock_public_data(self):
        def fake_candles(symbol, count=2400, interval="1h"):
            freq = {"5m": "5min", "15m": "15min", "1h": "h"}[interval]
            periods = 1200 if interval == "1h" else 800
            times = pd.date_range("2025-01-01", periods=periods, freq=freq,
                                  tz="UTC", name="open_time_utc")
            closes = 100 + np.arange(len(times)) * 0.05 + np.sin(np.arange(len(times)) / 12)
            bar_duration = pd.Timedelta(minutes={"5m": 5, "15m": 15, "1h": 60}[interval])
            return pd.DataFrame({
                "open": closes - 0.3, "high": closes + 0.8,
                "low": closes - 0.8, "close": closes,
                "volume": [1000.] * len(times),
                "close_time": times + bar_duration - pd.Timedelta(milliseconds=1),
            }, index=times)
        snapshot = {"as_of_utc": "2025-03-01T00:00:00+00:00", "errors": [],
                    "premium": {"markPrice": "125", "lastFundingRate": "0.0001"},
                    "interest": {"openInterest": "200"},
                    "ticker": {"priceChangePercent": "1.7"}}
        catalog = [{"symbol": "BTCUSDT", "base": "BTC", "quote_volume_usdt": 500_000_000.,
                    "last_price": 100., "change_pct": 1., "listing_days": 1000,
                    "established_watchlist": True, "core": True}]
        with patch("data_sources.get_candles", side_effect=fake_candles), \
             patch("data_sources.get_contract_catalog", return_value=catalog), \
             patch("live_feed.LiveFuturesFeed.start"), \
             patch("data_sources.get_live_price", return_value={"price": 123.45,
                   "exchange_utc": datetime.now(timezone.utc),
                   "as_of_utc": datetime.now(timezone.utc)}), \
             patch("data_sources.get_derivatives_context", return_value=snapshot), \
             patch("data_sources.get_news", return_value=([], [])):
            app = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "app.py"), default_timeout=30).run()
            self.assertFalse(app.exception, str(app.exception))
            scan_buttons = [button for button in app.button if button.label == "Scan the original four markets"]
            self.assertEqual(len(scan_buttons), 1)
            app = scan_buttons[0].click().run()
            self.assertFalse(app.exception, str(app.exception))
            chart_radios = [radio for radio in app.radio if radio.label == "Chart timeframe"]
            self.assertEqual(len(chart_radios), 1)
            app = chart_radios[0].set_value("5m").run()
            entry_radios = [radio for radio in app.radio if radio.label == "Entry price reference"]
            self.assertEqual(len(entry_radios), 1)
            app = entry_radios[0].set_value("Last completed 1h close").run()
        self.assertFalse(app.exception, str(app.exception))


if __name__ == "__main__":
    unittest.main()
