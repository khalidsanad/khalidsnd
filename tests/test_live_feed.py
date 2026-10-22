"""No-network verification of Futures WebSocket events and freshness rules."""

import json
from datetime import datetime, timezone
import time
import unittest

from live_feed import LiveFuturesFeed, parse_stream_message, stream_url


class LiveFeedTests(unittest.TestCase):
    def _raw(self, event, stream="btcusdt@aggTrade"):
        return json.dumps({"stream": stream, "data": event})

    def test_urls_use_binance_current_market_feed_and_no_auth(self):
        self.assertEqual(stream_url("BTCUSDT"),
                         "wss://fstream.binance.com/market/stream?streams="
                         "btcusdt@aggTrade/btcusdt@markPrice@1s")

    def test_trade_and_mark_are_parsed_separately(self):
        timestamp = int(time.time() * 1000)
        trade = {"e": "aggTrade", "s": "BTCUSDT", "st": 1, "T": timestamp,
                 "a": 123, "p": "82532.10"}
        mark = {"e": "markPriceUpdate", "s": "BTCUSDT", "E": timestamp,
                "p": "82527.00", "i": "82540.00"}
        self.assertEqual(parse_stream_message(self._raw(trade), "BTCUSDT")[1]["price"], 82532.10)
        parsed = parse_stream_message(self._raw(mark, "btcusdt@markPrice@1s"), "BTCUSDT")
        self.assertEqual(parsed[1]["mark"], 82527.)
        self.assertEqual(parsed[1]["index"], 82540.)

    def test_wrong_market_symbol_and_nonfinite_prices_rejected(self):
        timestamp = int(time.time() * 1000)
        base = {"e": "aggTrade", "s": "BTCUSDT", "T": timestamp,
                "a": 123, "p": "82532.10"}
        for edit in ({"s": "ETHUSDT"}, {"st": 2}, {"p": "nan"}, {"p": "-1"}):
            self.assertIsNone(parse_stream_message(self._raw({**base, **edit}), "BTCUSDT"))

    def test_newer_trade_wins_old_trade_ignored(self):
        timestamp = int(time.time() * 1000)
        feed = LiveFuturesFeed("BTCUSDT")  # deliberately does not start a network worker
        feed._on_open(None)
        event = {"e": "aggTrade", "s": "BTCUSDT", "T": timestamp,
                 "a": 200, "p": "82532.10"}
        feed._on_message(None, self._raw(event))
        old = {**event, "T": timestamp - 50, "p": "80000.00"}
        feed._on_message(None, self._raw(old))
        snapshot = feed.snapshot()
        self.assertEqual(snapshot["trade"]["price"], 82532.10)
        self.assertTrue(snapshot["trade"]["fresh"])

    def test_disconnected_or_stale_does_not_count_as_live(self):
        timestamp = int(time.time() * 1000)
        feed = LiveFuturesFeed("BTCUSDT")
        feed._on_open(None)
        feed._on_message(None, self._raw({"e": "aggTrade", "s": "BTCUSDT",
                                        "T": timestamp, "a": 1, "p": "82532.10"}))
        self.assertTrue(feed.snapshot()["trade"]["fresh"])
        feed._on_close(None)
        self.assertFalse(feed.snapshot()["trade"]["fresh"])
        feed._on_open(None)
        with feed._lock:
            feed._trade["received_monotonic"] = time.monotonic() - 20
        self.assertFalse(feed.snapshot()["trade"]["fresh"])


if __name__ == "__main__":
    unittest.main()
