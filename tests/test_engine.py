"""Offline regression tests, using entirely synthetic prices."""

import unittest

import numpy as np
import pandas as pd

from backtest import run_backtest
from strategy import RiskSettings, build_features, directional_trade_plans, timeframe_snapshot


def fake_prices(size=1200):
    index = pd.date_range("2025-01-01", periods=size, freq="h", tz="UTC", name="open_time_utc")
    closes = 100 + np.arange(size) * 0.08 + np.sin(np.arange(size) / 8) * 2
    opens = closes - 0.25
    return pd.DataFrame({"open": opens, "high": np.maximum(opens, closes) + 0.9,
                         "low": np.minimum(opens, closes) - 0.9, "close": closes,
                         "volume": 1000 + np.arange(size) % 70}, index=index)


class NoLookAheadTests(unittest.TestCase):
    def test_fast_timeframe_snapshot_does_not_modify_backtest_signal(self):
        hourly = fake_prices()
        before = build_features(hourly)["long_setup"].copy()
        five_min = fake_prices(900)
        five_min.index = pd.date_range("2025-01-01", periods=900, freq="5min",
                                       tz="UTC", name="open_time_utc")
        info = timeframe_snapshot(five_min, "5m")
        self.assertEqual(info["Timeframe"], "5m")
        self.assertIn(info["Trend"], ("Bullish", "Bearish", "Mixed"))
        pd.testing.assert_series_equal(before, build_features(hourly)["long_setup"])

    def test_binance_millisecond_index_compatible_with_asof_join(self):
        # Reproduces the pandas 3.0+ bug observed with Binance's millisecond
        # datetime index (left key was us, right key was ms).
        frame = fake_prices()
        frame.index = frame.index.as_unit("ms")
        features = build_features(frame)
        self.assertEqual(len(features), len(frame))
        self.assertTrue(features["four_hour_completed_at"].notna().any())
        self.assertTrue((features.dropna(subset=["four_hour_completed_at"])["four_hour_completed_at"]
                         <= features.dropna(subset=["four_hour_completed_at"])["decision_time"]).all())

    def test_four_hour_completed_before_or_at_decision_time(self):
        features = build_features(fake_prices())
        available = features.dropna(subset=["four_hour_completed_at"])
        self.assertTrue((available["four_hour_completed_at"] <= available["decision_time"]).all())

    def test_unfinished_four_hour_bar_never_leaks_future_price(self):
        original = fake_prices()
        changed = original.copy()
        # Hour 967 is later in the same [964,968) four-hour block as hour 965.
        changed.iloc[967, changed.columns.get_loc("close")] += 200
        changed.iloc[967, changed.columns.get_loc("high")] += 200
        a, b = build_features(original), build_features(changed)
        for key in ("close_4h", "ema50_4h", "ema200_4h", "long_setup"):
            self.assertEqual(a.iloc[965][key], b.iloc[965][key])

    def test_hour_ending_on_boundary_can_see_completed_4h(self):
        feature = build_features(fake_prices())
        self.assertEqual(feature.iloc[963]["four_hour_completed_at"], feature.iloc[963]["decision_time"])
        self.assertLess(feature.iloc[962]["four_hour_completed_at"], feature.iloc[962]["decision_time"])


class ExecutionTests(unittest.TestCase):
    def fixture(self):
        index = pd.date_range("2025-01-01", periods=3, freq="h", tz="UTC", name="open_time_utc")
        frame = pd.DataFrame({"open": [100., 100., 101.], "high": [101., 101., 110.],
                              "low": [99., 99., 90.], "close": [100., 100., 105.],
                              "atr": [1., 1., 1.], "long_setup": [False, True, True],
                              "rsi_ok": [True] * 3, "macd_ok": [True] * 3,
                              "adx_ok": [True] * 3, "volume_ok": [True] * 3}, index=index)
        frame["decision_time"] = frame.index + pd.Timedelta(hours=1)
        return frame

    def test_next_bar_and_stop_first_on_ambiguous_candle(self):
        trades, _, _ = run_backtest(self.fixture(), RiskSettings(taker_fee_pct=0, slippage_pct=0,
                                                                   leverage_cap=100))
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades.iloc[0]["entry"], 101.)
        self.assertEqual(trades.iloc[0]["entry_utc"], self.fixture().index[2])
        self.assertTrue(trades.iloc[0]["exit_reason"].startswith("STOP"))
        self.assertAlmostEqual(trades.iloc[0]["net_pnl_usdt"], -10.)

    def test_no_same_candle_entry_on_signal(self):
        frame = self.fixture().iloc[:2]
        trades, _, report = run_backtest(frame, RiskSettings())
        self.assertTrue(trades.empty)
        self.assertFalse(report["open_trade"])


class DirectionalTradePlanTests(unittest.TestCase):
    def test_three_targets_correct_on_both_sides(self):
        settings = RiskSettings(stop_atr=1.8)
        long, short = directional_trade_plans(100.0, 2.0, settings)
        self.assertEqual(long["Direction"], "LONG")
        self.assertEqual(short["Direction"], "SHORT")
        self.assertEqual(long["Entry"], short["Entry"])
        for record, sign in ((long, 1), (short, -1)):
            self.assertAlmostEqual(record["SL"], 100 - sign * 3.6)
            for i in (1, 2, 3):
                self.assertAlmostEqual(record[f"TP{i}"], 100 + sign * 3.6 * i)

    def test_stops_adjust_with_atr_not_backtest_target_ratio(self):
        a = directional_trade_plans(100., 1.0, RiskSettings(stop_atr=1.0, reward_risk=1.0))
        b = directional_trade_plans(100., 1.0, RiskSettings(stop_atr=2.0, reward_risk=3.0))
        self.assertAlmostEqual(a[0]["SL"], 99.0)
        self.assertAlmostEqual(b[0]["SL"], 98.0)
        self.assertAlmostEqual(b[0]["TP2"], 104.0)

    def test_rejects_nonpositive_or_impossible_prices(self):
        for entry, atr in ((-1., 1.), (0., 1.), (100., 0.), (100., float("nan")),
                           (100., 25.)):
            with self.assertRaises(ValueError):
                directional_trade_plans(entry, atr, RiskSettings(stop_atr=1.8))


if __name__ == "__main__":
    unittest.main()
