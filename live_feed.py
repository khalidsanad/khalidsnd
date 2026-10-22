"""Public Binance USD-M Futures trade and mark/index WebSocket feed.

Uses Binance's current /market/ streaming paths; never authenticates or trades.
The background worker owns its socket, while Streamlit reads thread-safe copies.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from threading import Event, Lock, Thread
import time

import websocket

from data_sources import validate_symbol


STREAM_BASE = "wss://fstream.binance.com/market/stream?streams="
STALE_AFTER_SECONDS = 5.0


def stream_url(symbol: str) -> str:
    """Subscribe to trades (100ms) and the 1-second mark/index feed."""
    validate_symbol(symbol)
    pair = symbol.lower()
    return f"{STREAM_BASE}{pair}@aggTrade/{pair}@markPrice@1s"


def parse_stream_message(raw: str, symbol: str) -> tuple[str, dict] | None:
    """Validate and normalize one combined-stream event; ignore other events."""
    try:
        envelope = json.loads(raw)
        item = envelope.get("data", envelope)
        if not isinstance(item, dict) or item.get("s") != symbol:
            return None
        # Newer Binance streams can combine USD-M and COIN-M contract types.
        if item.get("st", 1) != 1:
            return None
        event = item.get("e")
        if event == "aggTrade":
            price = float(item["p"])
            when_ms = int(item["T"])
            sequence = int(item["a"])
            if not math.isfinite(price) or price <= 0 or when_ms <= 0:
                return None
            return "trade", {"price": price, "event_ms": when_ms,
                             "sequence": sequence}
        if event == "markPriceUpdate":
            mark = float(item["p"])
            index = float(item["i"])
            when_ms = int(item["E"])
            if not all(math.isfinite(p) and p > 0 for p in (mark, index)) or when_ms <= 0:
                return None
            return "mark", {"mark": mark, "index": index, "event_ms": when_ms}
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return None
    return None


class LiveFuturesFeed:
    """One reconnecting WebSocket stream for the selected Binance USD-M pair."""

    def __init__(self, symbol: str):
        validate_symbol(symbol)
        self.symbol = symbol
        self._lock = Lock()
        self._stop = Event()
        self._thread: Thread | None = None
        self._socket: websocket.WebSocketApp | None = None
        self._connected = False
        self._last_error: str | None = None
        self._trade: dict | None = None
        self._mark: dict | None = None

    def start(self) -> None:
        with self._lock:
            if self._thread is not None:
                return
            self._thread = Thread(target=self._worker, daemon=True,
                                  name=f"futures-quotes-{self.symbol}")
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            socket = self._socket
        if socket is not None:
            socket.close()

    def _on_open(self, _socket) -> None:
        with self._lock:
            self._connected = True
            self._last_error = None

    def _on_message(self, _socket, raw: str) -> None:
        parsed = parse_stream_message(raw, self.symbol)
        if parsed is None:
            return
        kind, data = parsed
        now = time.monotonic()
        data["received_monotonic"] = now
        with self._lock:
            previous = self._trade if kind == "trade" else self._mark
            # A delayed/out-of-order event must not roll the price backward.
            if previous is not None:
                if data["event_ms"] < previous["event_ms"]:
                    return
                if (kind == "trade" and data["event_ms"] == previous["event_ms"]
                        and data["sequence"] <= previous["sequence"]):
                    return
            if kind == "trade":
                self._trade = data
            else:
                self._mark = data

    def _on_error(self, _socket, error) -> None:
        with self._lock:
            self._last_error = str(error)[:180]

    def _on_close(self, _socket, *_args) -> None:
        with self._lock:
            self._connected = False

    def _worker(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                socket = websocket.WebSocketApp(
                    stream_url(self.symbol), on_open=self._on_open,
                    on_message=self._on_message, on_error=self._on_error,
                    on_close=self._on_close)
                with self._lock:
                    self._socket = socket
                # TLS certificate verification is left ON (library default).
                socket.run_forever(ping_interval=30, ping_timeout=10)
            except Exception as error:  # Background thread cannot crash the UI.
                with self._lock:
                    self._last_error = str(error)[:180]
            finally:
                with self._lock:
                    self._connected = False
                    self._socket = None
            if self._stop.wait(backoff):
                break
            backoff = min(backoff * 2, 30.0)

    def snapshot(self) -> dict:
        """Return only fresh quotes as tradable-looking numbers; flag stale data."""
        now = time.monotonic()
        utc_now = datetime.now(timezone.utc)
        with self._lock:
            trade = dict(self._trade) if self._trade else None
            mark = dict(self._mark) if self._mark else None
            connected = self._connected
            error = self._last_error
        for reading in (trade, mark):
            if reading:
                reading["age_seconds"] = max(0., now - reading["received_monotonic"])
                reading["event_utc"] = datetime.fromtimestamp(reading["event_ms"] / 1000,
                                                               tz=timezone.utc)
                reading["exchange_age_seconds"] = (utc_now - reading["event_utc"]).total_seconds()
                reading["fresh"] = (connected and reading["age_seconds"] <= STALE_AFTER_SECONDS
                                    and -2 <= reading["exchange_age_seconds"] <= STALE_AFTER_SECONDS)
        return {"symbol": self.symbol, "connected": connected, "trade": trade,
                "mark": mark, "error": error}
