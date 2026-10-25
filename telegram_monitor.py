
import os
import json
import urllib.request
from datetime import datetime, timezone

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
INTERVALS = ["4h", "1h", "5m"]

def get_candles(symbol, interval):
    url = (
        "https://fapi.binance.com/fapi/v1/klines"
        f"?symbol={symbol}&interval={interval}&limit=120"
    )

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "FuturesMonitor/1.0"}
    )

    with urllib.request.urlopen(request, timeout=15) as response:
        candles = json.load(response)

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    closed = [
        candle for candle in candles
        if int(candle[6]) < now_ms
    ]

    if len(closed) < 100:
        raise RuntimeError("Insufficient closed candles")

    latest = closed[-1]
    interval_ms = {
        "5m": 300000,
        "1h": 3600000,
        "4h": 14400000
    }[interval]

    if now_ms - int(latest[6]) > interval_ms * 2:
        raise RuntimeError("Market data is stale")

    return closed

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = json.dumps({
        "chat_id": CHAT_ID,
        "text": message
    }).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.load(response)

    if not result.get("ok"):
        raise RuntimeError("Telegram message failed")

lines = ["📊 Binance Futures Market Check", ""]

for symbol in SYMBOLS:
    lines.append(f"🪙 {symbol}")

    for interval in INTERVALS:
        try:
            candles = get_candles(symbol, interval)
            close = float(candles[-1][4])
            lines.append(f"  {interval}: {close:,.4f} USDT")
        except Exception as error:
            lines.append(f"  {interval}: DATA UNAVAILABLE")
            print(f"{symbol} {interval}: {error}")

    lines.append("")

lines.append("Data check only — no trading signals yet.")

send_telegram("\n".join(lines))
print("Market check completed")
