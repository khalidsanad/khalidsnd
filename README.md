# Binance Futures Research Desk 2.2

An explainable, beginner-friendly **Python web dashboard on your own computer** for Binance USDT-margined perpetual futures. The original BTC, ETH, SOL and BNB watchlist is preserved, and active Binance contracts can now be added through a listing-age and liquidity screen.

## Publish as a shareable website (Streamlit Community Cloud)

1. Upload the text project files to a GitHub repository. Keep `.venv`, any API keys, and private settings out of the repository.
2. Go to https://share.streamlit.io and sign in with GitHub.
3. Choose **Create app / Deploy an app** using this repository and its `main` branch.
4. Set the entrypoint to **`app.py`**, use **Python 3.12** if the selector is available, and deploy. The `requirements.txt` in this folder specifies all necessary Python packages.
5. Verify that the public page displays **fresh** Binance data, not a connection error or a REST fallback. Binance may restrict its public API for some hosting locations; if endpoints are inaccessible, the app cannot be considered successfully deployed for market research. Do not work around regional restrictions.
6. Share the resulting `*.streamlit.app` URL. The public version is for education and paper research only; it does not trade.

Streamlit Community Cloud may pause an inactive app or limit resources, so it is **not** appropriate for unattended trading, urgent alerts, or low-latency execution. Every visitor's browser may trigger independent market-data requests; respect Binance service limits. Custom domains beyond the `streamlit.app` subdomain need a separate hosting or redirect arrangement.

**Research and paper backtesting only.** There is no account connection, API key, auto-trading or live order submission. No strategy is guaranteed to make money. Margin and futures losses can be severe.

## Start here — Windows (Khaled)

1. Download the project's ZIP file and extract it. Open the extracted `binance_futures_research` folder (the one containing `app.py`).
2. Make sure Python is installed. In **PowerShell** (not inside the `>>>` Python prompt), check:

   ```powershell
   py --version
   ```

3. Double-click **`START_WINDOWS.bat`**. On the first launch it installs the required packages, then starts your dashboard in the browser. Keep the terminal window open.
4. If the browser does not open automatically, visit `http://localhost:8501`.
5. Stop it by pressing **Ctrl+C** inside the terminal window.

Manual PowerShell alternative, from the project folder:

```powershell
py -m pip install -r requirements.txt
py -m streamlit run app.py
```

If Windows blocks the launcher, use the manual commands above. If `py` is not found, reinstall Python from the official Python website or use `python` instead. Do not paste terminal commands into Python's `>>>` prompt; type `exit()` first.

If package installation fails on a very new Python version, install the current supported Python 3.13 or 3.14 and run `py -3.13 -m pip install -r requirements.txt` followed by `py -3.13 -m streamlit run app.py` (adjust to the version you installed).

## Start on macOS

1. Extract this ZIP. Open Terminal, type `cd ` (including the space), drag the extracted **folder** into Terminal, and press Return.
2. Run these commands separately:

   ```bash
   python3 -m venv .venv
   .venv/bin/python -m pip install -r requirements.txt
   .venv/bin/python -m streamlit run app.py
   ```

3. Open `http://localhost:8501` in your browser. Keep Terminal open while using the app; press **Control+C** to stop it.

### Upgrade an existing Mac installation to 2.1 (live WebSocket prices)

1. Stop the running dashboard in Terminal with **Control+C**.
2. From this ZIP, copy **`app.py`**, **`data_sources.py`**, **`live_feed.py`**, and **`requirements.txt`** into your existing app folder, choosing **Replace** for existing files. Keep your existing `strategy.py` (unchanged from version 2.0). **Do not** replace or delete `.venv`.
3. In Terminal, still in the project folder, run the two commands below separately, then refresh `http://localhost:8501`:

   ```bash
   .venv/bin/python -m pip install -r requirements.txt
   .venv/bin/python -m streamlit run app.py
   ```

The new package **`websocket-client`** is required for streaming. There are no Binance API keys or account permissions involved.

### Upgrade from version 2.1 to 2.2 (long/short 3-target table)

1. Stop Streamlit in Terminal with **Control+C**.
2. Copy **`app.py`** and **`strategy.py`** from this ZIP into your existing dashboard folder and choose **Replace**. Keep `.venv` and all other files unchanged.
3. In Terminal, from your project folder, run:

   ```bash
   .venv/bin/python -m streamlit run app.py
   ```

No additional packages are needed when upgrading from version 2.1.

## How to use it

1. In the sidebar choose either **Established watchlist** or **Expanded Binance screen**. The latter reads Binance USDT perpetual symbols and filters them for at least 180 days listed and at least 20 million USDT of 24h trading volume by default. Select the symbol to analyze.
2. Read the **Market overview** for closed-candle price, current rule state and funding/open interest snapshots. Click **Scan the original four markets** to compare states. The separate top panel uses Binance USDⓈ-M Futures WebSocket trades for last price, and a 1s WebSocket for mark/index prices. The panel redraws around once per second.
3. Open **Charts & signal** for candlesticks, moving averages, RSI, MACD, the decision reasons and indicative entry/SL/TP. Choose a **5m, 15m, 1h or 4h chart**.
   Under the chart is a separate **Long & short trade-planning table**. It shows entry, SL, TP1, TP2 and TP3 for both hypothetical directions. Choose between a fresh Binance WebSocket last trade and the completed 1h close as the entry price reference. Short levels are **not** short trade signals or backtested results.
4. Open **Multi-timeframe** to see separate 5m, 15m, 1h and 4h trend/momentum summaries, and **Binance markets** for the screened coin list.
5. Open **Backtesting** to examine the closed trades, trade logs, costs, drawdown and equity curve. The default is 1,000 USDT and 1% risk budget/trade; this is *not* a cash balance linked to Binance.
6. Open **News & catalysts** to see recent linked headlines; do your own research into whether they caused a move.
7. Open **Decision history** to see and download each model-rule transition as a CSV.
8. Use **Refresh Binance data** if you need updated public candle and news information. The streaming panel updates on its own.

### Compare your price to Binance correctly

1. On Binance choose **USDⓈ-M Futures**, the **same USDT perpetual symbol** (for example BTCUSDT), and the **Last Price** display. Do not compare our futures last price to Binance Spot or the mark/index number.
2. Look for the green **LAST TRADED (LIVE)** label and the actual **exchange trade time** in the dashboard. A one-second visual delay or a small difference while BTC is moving is normal; an identical price at every moment cannot be guaranteed.
3. **Orange REST fallback** means the streaming socket is not producing fresh trades. The app then requests the public Binance REST V2 quote, checks its exchange-side trade timestamp, and labels it as a snapshot. Do **not** mistake that for a streaming or executable quote.
4. If the stream or REST source is stale/unavailable, the app displays a warning or **Unavailable**, rather than labeling an old number live. Check internet connectivity and whether Binance public data is accessible from your region. Check that your Mac's clock is synchronized if you see timestamp warnings.

The **last completed 1h close** and **indicative entry/SL/TP** are historical research values, **not live trade prices or instructions to place an order**. Paper backtesting does not use the live feed.

## Exact strategy

We use 1-hour Binance USDⓈ-M futures candles and 4-hour candles reconstructed from four **completed** 1-hour candles on UTC clock boundaries. The analysis evaluates a 1-hour candle **only after it closes**. A 4-hour candle is only joined to a signal at or after its completion, not from its opening timestamp.

- Mandatory: 4h EMA50 > EMA200 and 4h close > EMA200; 1h EMA20 > EMA50 and 1h close > EMA20.
- Volatility guardrail: ATR14 / close between 0.15% and 5%.
- At least three of these four: RSI14 in [50, 70]; MACD(12,26,9) histogram > 0; ADX14 >= 18; current 1h volume >= 80% of 20h average.
- A fresh `LONG SETUP` is an observed transition out of `WAIT`. Backtests enter at the **next** candle open, after assumed adverse slippage, and allow one open position on the selected market.
- Reference entry = latest completed close; indicative stop = entry − 1.8 × ATR; target = entry + 1.5 × (entry − stop). These are example planning levels, not exchange orders. Actual backtests anchor levels to the next open.
- Risk-based quantity = minimum of `(equity × risk% / stop distance)` and `(equity × leverage cap / entry)`. Fees and slippage can make the **true loss exceed** the gross risk budget.
- If the 1-hour price range touches both SL and TP, the test pessimistically records SL first. Stop gaps use the worse candle open.

### Version 2.0 additions

- **5m and 15m indicators:** The dashboard obtains only finished five-minute/fifteen-minute candles from Binance and calculates EMA20/50/200, RSI14, MACD histogram, ADX14, ATR14 and volume-relative-to-average. These are **additional market context**, not an untested replacement for the original 1-hour signal/backtest.
- **Selectable charts:** 5m, 15m, 1h and 4h candle charts. The 4h chart is built from four fully closed 1-hour periods.
- **Live price (upgraded in 2.1):** The main quote now follows public Binance aggregated futures trades over WebSocket (server pushes around 100ms; Streamlit displays around once per second). Streaming mark/index prices are shown separately. A REST V2 snapshot is shown only as a labeled fallback, and never as a guaranteed tradable execution price.
- **Broader market universe:** Binance `exchangeInfo` and all-symbol 24h ticker feed supply actively trading USDT perpetuals, with configurable volume threshold, minimum 180 days listing history and basic exclusions for numerical multiplier/premarket/meme/alpha tags. The **screen does not verify legitimacy or prevent scams**; new and low-liquidity markets are excluded by design.

### Version 2.2: planning levels for LONG and SHORT

- Both hypothetical directions share one selected **entry reference**: a fresh USDⓈ-M Futures WebSocket last-traded price or the latest completed 1h close.
- Stop distance: `completed 1h ATR14 × Stop width (ATR)` from the sidebar (default `1.8 × ATR`). Long SL is below entry; short SL is above entry.
- Three take-profit levels: **TP1 = 1R**, **TP2 = 2R**, **TP3 = 3R** in the corresponding direction, where R is the entry-to-stop distance. A plan is hidden when its values would be invalid or data are stale.
- The table explicitly labels the short-side assessment **NOT TESTED**. The original rule state (`LONG SETUP` or `WAIT`) and the long-only backtest are unchanged. The original Target / stop distance slider still governs the old single-target backtest, not this independent three-target planning table.
- The live-basis table can change each second and is **not a standing order proposal**. Trades are never submitted. It does not include order-book bid/ask, fees, funding, slippage, liquidation, or position scaling.

## What is and isn't included

Included: EMA20/50/200, Wilder-type RSI14/ATR14/ADX14, MACD, volume ratio, completed higher timeframe, fast-timeframe context panels, live trade/mark/index WebSocket prices with REST V2 fallback, screened Binance listings, fee/slippage-aware next-bar paper entries, backtest metrics, historical rule transition audit, source-linked news, current public funding/open interest/24h price change.

Not included: real order execution, tick-size and quantity filter validation, trading fees by user's VIP tier, actual historical funding cash flows, liquidation/maintenance margin, bid/ask spread dynamics, market depth, synchronized multi-asset portfolio risk, alternative data backfills or out-of-sample optimization. Crypto pairs can all fall together. New-listing and low-activity contracts are excluded, but no screening method guarantees legitimacy or safety.

Funding and open interest are **present-time snapshots**, not known at earlier test timestamps, so they do not influence past trades. Headline keywords are **possible factors only**. They are not historical causal labels, cannot explain every price movement, do not verify the linked article's accuracy and **do not change trading signals**. Recent Google News RSS headlines may be delayed, incomplete, unavailable or aggregated; no claim of exhaustive real-time news monitoring is made.

There is no single 'best' technical-analysis method. This deterministic research model is designed to be inspected, criticized, and properly validated on unseen time periods before any consideration of live futures.

## Privacy and data

- **Binance**: public unauthenticated endpoint `https://fapi.binance.com` (`/fapi/v1/time`, `/fapi/v1/klines`, `/fapi/v1/exchangeInfo`, `/fapi/v2/ticker/price`, `/fapi/v1/premiumIndex`, `/fapi/v1/openInterest`, `/fapi/v1/ticker/24hr`) and public WebSocket market streams at `wss://fstream.binance.com/market/stream`. The price data may be unavailable in regions where Binance is restricted. We do not attempt to work around restrictions.
- **News**: public Google News RSS search. Links open news coverage; source title, time, and heuristic topical category are shown.
- No passwords, secrets, account statements or Binance order capabilities are collected. Streamlit runs locally on your computer.

Docs: [Binance USDⓈ-M API](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data) · [Streamlit installation](https://docs.streamlit.io/get-started/installation/command-line).

## Developers and tests

```powershell
py -m unittest discover -s tests -v
```

Files:

- `app.py` — dashboard and charts.
- `data_sources.py` — public Binance and RSS loading with validation.
- `live_feed.py` — reconnecting thread-safe public Binance Futures WebSocket data.
- `strategy.py` — feature calculation, closed-bar alignment, long/short hypothetical level arithmetic, and human-readable decisions.
- `backtest.py` — execution simulation and performance report.
- `tests/test_engine.py` — timing and execution regression tests.
- `tests/test_data_sources.py` — multi-interval data and Binance market screening tests.
- `tests/test_app.py` — dashboard launch and chart controls smoke test.
- `tests/test_live_feed.py` — WebSocket format, feed isolation and freshness checks.

**Version 2.2 — added a 2-direction, 3-target planning table under Charts & signal. WebSocket live quotes and the original completed-candle backtest remain intact. Educational research prototype; not investment advice.**
