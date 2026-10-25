
import os
import json
import urllib.request

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

if not TOKEN or not CHAT_ID:
    raise RuntimeError("Telegram secrets are missing")

message = (
    "✅ Binance Futures Monitor\n"
    "Telegram connection successful!\n"
    "Next step: Binance market alerts."
)

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

print("Telegram test message sent successfully")
