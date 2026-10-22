"""Binance Futures Research Desk 2.2 — no authentication, no trading."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from backtest import run_backtest
from data_sources import (CORE_SYMBOLS, ESTABLISHED_SYMBOLS, DataError, data_quality,
                          get_candles, get_contract_catalog, get_derivatives_context,
                          get_live_price, get_news, screen_contracts)
from live_feed import LiveFuturesFeed
from strategy import (RiskSettings, add_indicators, build_features, changes_log,
                      closed_four_hour_bars, directional_trade_plans,
                      proposed_levels, reasons, timeframe_snapshot)


st.set_page_config(page_title="Futures Research Desk", page_icon="📈", layout="wide",
                   initial_sidebar_state="expanded")
st.markdown("""
<style>
.stApp {background: #0b1220; color: #eef3fc;}
[data-testid="stSidebar"] {background: #111d2e;}
[data-testid="stMetric"] {background: #14243a; border: 1px solid #253b55;
  border-radius: 11px; padding: 15px;}
.small-note {color:#a9bad1; font-size:0.92rem;}
.app-banner {border-radius:14px; padding:19px 23px; margin-bottom:16px;
 background:linear-gradient(115deg,#183d55,#152039);border:1px solid #2c5d6d;}
.app-banner h2 {margin:0;color:#effbff;} .app-banner p {margin:7px 0 0;color:#b5cbd8;}
</style>
""", unsafe_allow_html=True)


@st.cache_data(ttl=600, show_spinner=False)
def load_asset(symbol: str, bars: int) -> pd.DataFrame:
    return get_candles(symbol, bars)


@st.cache_data(ttl=240, show_spinner=False)
def load_fast(symbol: str, interval: str, bars: int = 800) -> pd.DataFrame:
    return get_candles(symbol, bars, interval=interval)


@st.cache_data(ttl=1800, show_spinner=False)
def load_catalog() -> list[dict]:
    return get_contract_catalog()


@st.cache_data(ttl=3, show_spinner=False)
def load_price(symbol: str) -> dict:
    return get_live_price(symbol)


@st.cache_data(ttl=300, show_spinner=False)
def load_context(symbol: str) -> dict:
    return get_derivatives_context(symbol)


@st.cache_data(ttl=900, show_spinner=False)
def load_news(symbol: str):
    return get_news(symbol)


def price_format(price: float) -> str:
    if price >= 10:
        return f"${price:,.2f}"
    if price >= 1:
        return f"${price:,.4f}"
    if price >= 0.01:
        return f"${price:,.6f}"
    return f"${price:,.8f}"


def pct_format(value: float) -> str:
    return "N/A" if pd.isna(value) else f"{value:.1f}%"


def indicator_chart(frame: pd.DataFrame, symbol: str, levels: dict | None,
                    interval: str = "1h") -> go.Figure:
    recent = frame.tail(180).copy()
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.04,
                        row_heights=[0.65, 0.19, 0.16])
    fig.add_trace(go.Candlestick(x=recent.index, open=recent["open"], high=recent["high"],
                                 low=recent["low"], close=recent["close"], name=f"{interval} candles",
                                 increasing_line_color="#24c49f", decreasing_line_color="#ff6c7c"), row=1, col=1)
    for key, color in (("ema20", "#ffd36b"), ("ema50", "#59a6ff"), ("ema200", "#b07cff")):
        fig.add_trace(go.Scatter(x=recent.index, y=recent[key], name=key.upper(),
                                 line=dict(color=color, width=1.6)), row=1, col=1)
    if levels:
        for k, color, name in (("entry_reference", "#eceff4", "Reference"),
                               ("stop", "#ff7186", "Indicative stop"),
                               ("target", "#20d5b5", "Indicative target")):
            fig.add_hline(y=levels[k], line_dash="dot", line_color=color,
                          annotation_text=name, row=1, col=1)
    fig.add_trace(go.Scatter(x=recent.index, y=recent["rsi"], name="RSI 14",
                             line=dict(color="#ecb96a")), row=2, col=1)
    fig.add_hline(y=70, line_dash="dash", line_color="#c6586e", row=2, col=1)
    fig.add_hline(y=50, line_dash="dash", line_color="#566a88", row=2, col=1)
    fig.add_trace(go.Bar(x=recent.index, y=recent["macd_hist"], name="MACD histogram",
                         marker_color=["#24c49f" if x >= 0 else "#ff6c7c" for x in recent["macd_hist"].fillna(0)]), row=3, col=1)
    fig.update_layout(height=730, template="plotly_dark", paper_bgcolor="#0b1220",
                      plot_bgcolor="#0f1a2c", font_color="#d4e0ed", title=f"{symbol} / {interval} completed candles (UTC)",
                      margin=dict(l=15, r=12, t=45, b=10), xaxis_rangeslider_visible=False,
                      legend=dict(orientation="h", y=1.04))
    fig.update_yaxes(gridcolor="#203249")
    fig.update_xaxes(gridcolor="#203249")
    return fig


catalog_error = None
try:
    full_catalog = load_catalog()
except (DataError, ValueError, KeyError, TypeError) as exc:
    catalog_error = str(exc)
    full_catalog = []

with st.sidebar:
    st.title("Research controls")
    st.caption("Public data only · no live orders")
    coverage = st.radio("Binance markets", ["Established watchlist", "Expanded Binance screen"],
                        index=1, help="The broader list is based on liquidity and listing age, not a safety guarantee.")
    min_vol_m = st.select_slider("Min 24h USDT volume (millions)", options=[5, 10, 20, 50, 100], value=20)
    eligible_catalog = screen_contracts(full_catalog, min_age_days=180,
                                        min_volume_usdt=min_vol_m * 1_000_000)
    if coverage == "Established watchlist":
        eligible_catalog = [x for x in eligible_catalog if x["symbol"] in ESTABLISHED_SYMBOLS]
    selected_options = [x["symbol"] for x in eligible_catalog]
    if not selected_options:
        # A clearly labeled fallback for temporary catalog failure. These
        # four symbols are revalidated by the candles endpoint when selected.
        selected_options = list(CORE_SYMBOLS)
    symbol = st.selectbox("Currency", selected_options,
                          format_func=lambda s: s.replace("USDT", "/USDT"))
    st.caption(f"{len(selected_options)} selectable pairs · active USDT perpetuals · ≥180 days listed")
    bars = st.select_slider("History (1h candles)", options=[1200, 2400, 3600, 4800, 6000], value=2400)
    st.divider()
    st.subheader("Paper risk settings")
    capital = st.number_input("Starting capital (USDT)", min_value=100., max_value=1_000_000., value=1000., step=100.)
    risk_pct = st.slider("Max risk budget per trade (%)", 0.25, 2.0, 1.0, 0.25)
    leverage_cap = st.slider("Notional / equity cap (x)", 1.0, 3.0, 2.0, 0.5)
    stop_atr = st.slider("Stop width (ATR)", 1.0, 3.0, 1.8, 0.2)
    rr = st.slider("Target / stop distance", 1.0, 3.0, 1.5, 0.25)
    with st.expander("Fees and slippage"):
        fee = st.number_input("Fee each side (%)", 0., 0.5, 0.05, step=0.01, format="%.2f")
        slip = st.number_input("Adverse slippage each side (%)", 0., 0.5, 0.02, step=0.01, format="%.2f")
    settings = RiskSettings(capital=capital, risk_pct=risk_pct, leverage_cap=leverage_cap,
                            stop_atr=stop_atr, reward_risk=rr, taker_fee_pct=fee, slippage_pct=slip)
    if st.button("↻ Refresh Binance data", width="stretch"):
        load_asset.clear()
        load_fast.clear()
        load_catalog.clear()
        load_price.clear()
        load_context.clear()
        load_news.clear()
        st.session_state.pop("four_market_scan", None)
        st.rerun()
    st.caption("Live stream shown ~1s · REST fallback 3s · fast candles 4 min · 1h candles 10 min")

st.markdown('''<div class="app-banner"><h2>Futures Research Desk 2.2</h2>
<p>Binance USDT perpetual market screen · 5m / 15m / 1h / 4h analysis · explainable long-only 1h research signals</p></div>''',
            unsafe_allow_html=True)
st.warning("EDUCATIONAL / PAPER TESTING ONLY. This app does not place orders. Crypto futures can cause rapid losses, and no indicator method is reliably 'best' in all markets.", icon="⚠️")
if catalog_error:
    st.warning("Market catalog could not refresh. Showing the original four symbols until Binance listings are available. " + catalog_error)


@st.fragment(run_every="1s")
def live_quote_panel(selected: str):
    """WebSocket-fed prices; Streamlit repaints only this panel every ~1 second."""
    feed = st.session_state.get("active_live_feed")
    if feed is None or feed.symbol != selected:
        if feed is not None:
            feed.stop()
        feed = LiveFuturesFeed(selected)
        st.session_state["active_live_feed"] = feed
        feed.start()
    current = feed.snapshot()
    trade = current["trade"]
    mark = current["mark"]
    with st.container(border=True):
        last_col, mark_col, index_col = st.columns([1.3, 1, 1])
        if trade is not None and trade["fresh"]:
            last_col.metric(f"{selected} · LAST TRADED (LIVE)", price_format(trade["price"]))
            last_col.caption(f"🟢 Futures WebSocket · trade {trade['event_utc']:%H:%M:%S} UTC · received {trade['age_seconds']:.1f}s ago")
        else:
            # Clearly labeled fallback: the REST price is never passed off as
            # an active WebSocket feed or an instantly executable quote.
            try:
                fallback = load_price(selected)
                age = (datetime.now(timezone.utc) - fallback["exchange_utc"]).total_seconds()
                if not -2 <= age <= 10:
                    raise DataError(f"REST trade timestamp is {age:.1f}s from local clock")
                last_col.metric(f"{selected} · LAST TRADED (REST)", price_format(fallback["price"]))
                last_col.caption(f"🟠 REST fallback · exchange trade {fallback['exchange_utc']:%H:%M:%S} UTC · {age:.1f}s old")
                st.warning("Real-time WebSocket is connecting or unavailable. REST fallback is a snapshot; do not treat it as a live trading quote.")
            except (DataError, ValueError, KeyError, TypeError) as exc:
                last_col.metric(f"{selected} · LAST TRADED", "Unavailable")
                st.error(f"No verified current price. Wait for a live connection or check network access. {exc}")
        if mark is not None and mark["fresh"]:
            mark_col.metric("MARK PRICE (LIVE)", price_format(mark["mark"]))
            index_col.metric("INDEX PRICE (LIVE)", price_format(mark["index"]))
            mark_col.caption(f"🟢 Exchange event {mark['event_utc']:%H:%M:%S} UTC")
        else:
            mark_col.metric("MARK PRICE", "Unavailable")
            index_col.metric("INDEX PRICE", "Unavailable")
            mark_col.caption("Waiting for fresh mark/index data")
        if current["error"]:
            st.caption(f"WebSocket connection: {current['error']}")
        st.caption("Compare with Binance **USDⓈ-M Futures** for the same contract and price type. "
                   "Spot, futures last traded, mark and index prices can differ. "
                   "The displayed live quote is not a bid/ask or a guaranteed execution price. "
                   "The 1h entry/SL/TP model below updates from **completed candles**, not each trade.")


live_quote_panel(symbol)


@st.fragment(run_every="1s")
def bidirectional_plans_panel(selected: str, closed_close: float, closed_at,
                              latest_atr: float, risk: RiskSettings,
                              long_setup: bool, reliable: bool):
    """Interactive read-only levels; only the LIVE basis repaints each second."""
    st.subheader("Long & short trade-planning table")
    basis = st.radio("Entry price reference", ["Live Binance Futures last trade", "Last completed 1h close"],
                     horizontal=True, key="plan_entry_basis")
    if not reliable:
        st.error("The 1h indicators or candle data are incomplete/stale; trade-plan levels are unavailable.")
        return
    if basis == "Live Binance Futures last trade":
        feed = st.session_state.get("active_live_feed")
        snap = feed.snapshot() if feed is not None and feed.symbol == selected else None
        quote = snap.get("trade") if snap else None
        if quote is None or not quote["fresh"]:
            st.warning("No fresh WebSocket trade price. The table is intentionally hidden until live data return. "
                       "You can select the completed 1h close for a historical research reference.")
            return
        entry = float(quote["price"])
        timestamp_label = f"Binance USDⓈ-M Futures trade at {quote['event_utc']:%Y-%m-%d %H:%M:%S} UTC"
    else:
        entry = closed_close
        timestamp_label = f"Last completed 1h candle at {closed_at:%Y-%m-%d %H:%M} UTC (not live)"
    try:
        plans = directional_trade_plans(entry, latest_atr, risk)
    except ValueError as exc:
        st.warning(f"Cannot calculate valid risk levels for this market: {exc}")
        return
    report = pd.DataFrame(plans)
    report.insert(1, "Model assessment", ["LONG SETUP" if long_setup else "WAIT", "NOT TESTED"])
    st.dataframe(report.style.format({name: price_format for name in ("Entry", "SL", "TP1", "TP2", "TP3")}),
                 hide_index=True, width="stretch")
    st.caption(f"Reference: **{timestamp_label}** · Stop distance = 1h ATR × {risk.stop_atr:.1f}. "
               "TP1 / TP2 / TP3 = 1R / 2R / 3R from entry, where R is the entry-to-stop price distance.")
    st.warning("These are **hypothetical scenarios, not buy/sell recommendations**. The SHORT side has no "
               "validated entry signal or backtest. LONG is only a model setup when explicitly marked LONG SETUP; "
               "WAIT means no approved new long entry. Live-referenced levels move with every price update. "
               "Execution prices, spreads, fees, slippage and funding are not included in this table.")
    st.caption("The sidebar's 'Target / stop distance' controls the original long-only backtest target; "
               "it does not alter these 1R / 2R / 3R planning tiers. No orders are placed.")


with st.spinner(f"Loading {symbol} candles from Binance Futures…"):
    try:
        candles = load_asset(symbol, bars)
        quality = data_quality(candles)
        df = build_features(candles)
    except (DataError, ValueError, KeyError, TypeError, pd.errors.ParserError) as exc:
        st.error(f"Cannot load validated Binance futures data: {exc}")
        st.info("Check your connection and Binance availability in your region, then click Refresh. No fabricated prices or signals are substituted.")
        st.stop()

# Fast indicators use completed exchange 5m/15m candles. They are advisory
# snapshots only, never silently substituted into the 1h historical backtest.
fast_frames = {}
fast_errors = []
for timeframe in ("5m", "15m"):
    try:
        candidate = load_fast(symbol, timeframe)
        if data_quality(candidate, timeframe)["gaps"]:
            raise DataError(f"Missing {timeframe} candles")
        fast_frames[timeframe] = candidate
    except (DataError, ValueError, KeyError, TypeError) as exc:
        fast_errors.append(f"{timeframe}: {exc}")

latest = df.iloc[-1]
last_decision = pd.Timestamp(latest["decision_time"])
stale = (pd.Timestamp.now(tz="UTC") - last_decision) > pd.Timedelta(hours=2)
if stale:
    st.error("Market data may be stale; do not interpret the current setup as actionable.")
if quality["gaps"]:
    st.error(f"Detected {quality['gaps']} 1h candle timestamp gaps. Backtest may be unreliable; investigate missing data.")
if not bool(latest["ready"]):
    st.warning("Insufficient completed 4h indicator history. Increase the candle history setting.")

market_tab, chart_tab, frames_tab, test_tab, universe_tab, news_tab, log_tab, method_tab = st.tabs(
    ["Market overview", "Charts & signal", "Multi-timeframe", "Backtesting", "Binance markets",
     "News & catalysts", "Decision history", "Method & limitations"])

with market_tab:
    st.subheader("Market overview")
    st.markdown("The original four pairs remain the core watchlist. The expanded market list is pulled from active Binance USDT perpetual contracts, screened for at least 180 days of history and a minimum 24-hour trading volume. **Binance listing is not a safety endorsement.**")
    st.caption("Choose any screened pair from the sidebar. The core research decision remains long-only and based on completed 1h/4h candles.")
    metric_cols = st.columns(4)
    metric_cols[0].metric("Last completed 1h close (NOT LIVE)", price_format(float(latest["close"])))
    metric_cols[1].metric("Rule state", str(latest["state"]))
    metric_cols[2].metric("ATR volatility (1h)", pct_format(float(latest["atr_pct"]) * 100))
    metric_cols[3].metric("ADX trend strength", f"{float(latest['adx']):.1f}" if pd.notna(latest["adx"]) else "N/A")
    st.caption(f"Last analyzed candle completed at {last_decision:%Y-%m-%d %H:%M} UTC · {len(df):,} 1h candles checked · {quality['gaps']} gaps")
    if st.button("Scan the original four markets", width="stretch"):
        comparison = []
        with st.spinner("Checking the four approved pairs using completed candles…"):
            for candidate in CORE_SYMBOLS:
                try:
                    candidate_candles = load_asset(candidate, bars)
                    row = build_features(candidate_candles).iloc[-1]
                    gaps = data_quality(candidate_candles)["gaps"]
                    comparison.append({"Pair": candidate, "1h close (USDT)": round(float(row["close"]), 4),
                                       "State": "DATA GAP" if gaps else row["state"],
                                       "RSI14": round(float(row["rsi"]), 1) if pd.notna(row["rsi"]) else None,
                                       "ADX14": round(float(row["adx"]), 1) if pd.notna(row["adx"]) else None,
                                       "Last close UTC": str(row["decision_time"])})
                except (DataError, ValueError, KeyError) as exc:
                    comparison.append({"Pair": candidate, "State": f"Unavailable: {exc}"})
        st.session_state["four_market_scan"] = comparison
    if "four_market_scan" in st.session_state:
        st.dataframe(pd.DataFrame(st.session_state["four_market_scan"]),
                     hide_index=True, width="stretch")
    st.subheader("Current futures-market context (snapshots, not backtest inputs)")
    ctx = load_context(symbol)
    premium, interest, ticker = ctx.get("premium", {}), ctx.get("interest", {}), ctx.get("ticker", {})
    columns = st.columns(4)
    try:
        columns[0].metric("Mark price", price_format(float(premium["markPrice"])))
    except (KeyError, ValueError, TypeError):
        columns[0].metric("Mark price", "N/A")
    try:
        columns[1].metric("Last funding rate", f"{float(premium['lastFundingRate']) * 100:.4f}%")
    except (KeyError, ValueError, TypeError):
        columns[1].metric("Last funding rate", "N/A")
    try:
        columns[2].metric("Open interest (base units)", f"{float(interest['openInterest']):,.0f}")
    except (KeyError, ValueError, TypeError):
        columns[2].metric("Open interest", "N/A")
    try:
        columns[3].metric("24h price change", f"{float(ticker['priceChangePercent']):+.2f}%")
    except (KeyError, ValueError, TypeError):
        columns[3].metric("24h price change", "N/A")
    st.caption(f"Snapshots checked around {ctx['as_of_utc']} UTC. Funding can add costs; open interest is a snapshot, not evidence of future direction.")
    if ctx["errors"]:
        st.warning("Some derivative snapshots were unavailable: " + " | ".join(ctx["errors"]))

with chart_tab:
    st.subheader("Current rule-based decision")
    left, right = st.columns([1, 1])
    with left:
        if bool(latest["long_setup"]) and not stale and not quality["gaps"]:
            st.success("LONG SETUP — evaluate at the NEXT 1h candle, never an automatic order")
        else:
            st.info("WAIT — no fresh validated long setup")
        for explanation in reasons(latest):
            if explanation.startswith("PASS:"):
                st.markdown("🟢 " + explanation.removeprefix("PASS: "))
            elif explanation.startswith("FAIL:"):
                st.markdown("🔴 " + explanation.removeprefix("FAIL: "))
            else:
                st.markdown("ℹ️ " + explanation)
    with right:
        st.markdown("#### Indicative entry, SL & TP")
        if bool(latest["ready"]) and pd.notna(latest["atr"]) and float(latest["atr"]) > 0:
            levels = proposed_levels(float(latest["close"]), float(latest["atr"]), settings)
            a, b, c = st.columns(3)
            a.metric("Entry reference", price_format(levels["entry_reference"]))
            b.metric("Stop-loss", price_format(levels["stop"]))
            c.metric("Take-profit", price_format(levels["target"]))
            st.write(f"Example size: **{levels['qty']:.6f} {symbol[:-4]}** · notional **${levels['notional']:,.2f}** · gross price-stop exposure **${levels['gross_risk']:,.2f}**")
            st.caption("Entry uses last CLOSED candle close as a reference, not a guaranteed execution price. Next open, fees, slippage, gaps and funding change realized risk. Levels are illustrative even during WAIT.")
        else:
            levels = None
            st.caption("Levels unavailable until enough completed history is present.")
    chart_data = {"1h": add_indicators(candles),
                  "4h": add_indicators(closed_four_hour_bars(candles))}
    for interval in ("5m", "15m"):
        if interval in fast_frames:
            chart_data[interval] = add_indicators(fast_frames[interval])
    intervals = [name for name in ("5m", "15m", "1h", "4h") if name in chart_data]
    chart_interval = st.radio("Chart timeframe", intervals, horizontal=True,
                              index=intervals.index("1h"), key="selected_chart_interval")
    if chart_interval != "1h":
        st.caption(f"{chart_interval} chart is for additional analysis. Proposed SL/TP and historical entries remain based on the original 1h strategy.")
    st.plotly_chart(indicator_chart(chart_data[chart_interval], symbol,
                                   levels if chart_interval == "1h" else None,
                                   chart_interval), width="stretch")
    bidirectional_plans_panel(symbol, float(latest["close"]), last_decision,
                              float(latest["atr"]), settings, bool(latest["long_setup"]),
                              bool(latest["ready"]) and not stale and not quality["gaps"]
                              and pd.notna(latest["atr"]) and float(latest["atr"]) > 0)
    with st.expander("Indicator readings and exact filter alignment"):
        items = {"1h RSI": latest["rsi"], "1h MACD histogram": latest["macd_hist"],
                 "1h ATR": latest["atr"], "Volume vs 20h": latest["volume_ratio"],
                 "4h close": latest["close_4h"], "4h EMA50": latest["ema50_4h"],
                 "4h EMA200": latest["ema200_4h"]}
        st.dataframe(pd.DataFrame({"Indicator": list(items), "Value": list(items.values())}), hide_index=True)
        st.write(f"Most recent completed 4h bar used: **{latest['four_hour_completed_at']}**; signal decision time: **{latest['decision_time']}**")

with frames_tab:
    st.subheader("Four-timeframe momentum monitor")
    st.info("The 5m and 15m readings help inspect short-term momentum, but they do **not** change the 1h LONG SETUP / WAIT decision or the historical backtest. That requires a separately validated entry model.")
    readings = []
    for interval in ("5m", "15m"):
        if interval in fast_frames:
            readings.append(timeframe_snapshot(fast_frames[interval], interval))
    readings.append(timeframe_snapshot(candles, "1h"))
    readings.append(timeframe_snapshot(closed_four_hour_bars(candles), "4h"))
    st.dataframe(pd.DataFrame(readings), hide_index=True, width="stretch")
    if fast_errors:
        st.warning("Some fast timeframes could not be validated: " + " | ".join(fast_errors))
    st.markdown("**Reading the panel:** Bullish means EMA20 is above EMA50 and price is above EMA200; Bearish is the inverse; Mixed means no clear alignment. RSI, ADX, MACD and ATR show separate aspects of momentum and volatility. These descriptions are not buy/sell orders.")

with universe_tab:
    st.subheader("Binance USDT perpetual market explorer")
    st.caption("Contracts are read from Binance exchangeInfo, joined to Binance's current 24h tickers, and filtered by listing age, quote volume and contract status. Volume is in USDT equivalent; numbers are snapshots, not guaranteed liquidity for an order.")
    if eligible_catalog:
        table = pd.DataFrame([{"Pair": x["symbol"], "Last (USDT)": x["last_price"],
                               "24h change %": x["change_pct"],
                               "24h volume (M USDT)": round(x["quote_volume_usdt"] / 1_000_000, 1),
                               "Days listed": x["listing_days"],
                               "Watchlist": "Core" if x["core"] else ("Established" if x["established_watchlist"] else "Screened")}
                              for x in eligible_catalog])
        st.dataframe(table, hide_index=True, width="stretch")
        st.caption(f"{len(eligible_catalog)} active screened markets available with current sidebar criteria. To inspect a pair, select it in the sidebar.")
    else:
        st.warning("No verified Binance listings meet the filter, or the catalog is unavailable. The app can still try the original four pairs.")
    st.warning("The filter excludes new listings, low-activity pairs, numerical multiplier tokens and obvious meme/alpha labels. It cannot detect fraud, market manipulation, correlated crashes or all other hazards. Do your own fundamental research before treating any screened asset as suitable.")

with test_tab:
    st.subheader(f"Historical paper backtest · {symbol}")
    if quality["gaps"]:
        st.error("Backtest blocked because input has missing 1h candles.")
    else:
        trades, eq, summary = run_backtest(df, settings)
        columns = st.columns(5)
        columns[0].metric("Closed trades", f"{summary['closed_trades']}")
        columns[1].metric("Win rate", pct_format(summary["wins_pct"]))
        columns[2].metric("Total return (MTM)", f"{summary['return_pct']:+.2f}%")
        columns[3].metric("Max drawdown", f"{summary['max_drawdown_pct']:.2f}%")
        pf = summary["profit_factor"]
        columns[4].metric("Profit factor", "N/A" if pd.isna(pf) else ("∞" if pf == float("inf") else f"{pf:.2f}"))
        if summary["open_trade"]:
            st.info("One paper trade remains open at the end; ending equity includes its marked-to-market value, but realized profit excludes it.")
        if summary["closed_trades"] < 30:
            st.warning("Fewer than 30 completed trades: too small a sample for robust conclusions. A profitable result would not establish an edge.")
        fig = go.Figure(go.Scatter(x=eq["time_utc"], y=eq["equity_usdt"],
                                   mode="lines", line=dict(color="#22d3b1", width=2.4), name="Paper equity"))
        fig.update_layout(height=330, template="plotly_dark", paper_bgcolor="#0b1220",
                          plot_bgcolor="#101f31", xaxis_title="UTC", yaxis_title="USDT",
                          margin=dict(l=10, r=10, t=12, b=10))
        st.plotly_chart(fig, width="stretch")
        st.caption("Entry: next 1h open after a newly appearing long setup, with adverse slippage. Stop/target: fixed ATR levels. If both touch in one candle, stop is counted first. Fees included; funding, liquidation, exchange quantity/tick filters and portfolio-level exposure NOT simulated.")
        if len(trades):
            st.dataframe(trades.sort_values("entry_utc", ascending=False), hide_index=True, width="stretch")
            st.download_button("Download completed trade log (CSV)", trades.to_csv(index=False).encode(),
                               file_name=f"{symbol}_paper_trades.csv", mime="text/csv")
        else:
            st.info("There were no completed trades under these rules and this date range. Change history length or review the signal conditions; do not tune solely to improve the past score.")

with news_tab:
    st.subheader("Linked headlines and possible catalysts")
    st.info("News headlines are retrieved from recent Google News RSS results for this asset and macro events. These are possible explanations to investigate, NOT verified causes of price moves, directional trading signals, or exhaustive coverage of all relevant news.")
    with st.spinner("Retrieving linked headlines…"):
        news, warnings = load_news(symbol)
    if warnings:
        st.warning(" | ".join(warnings))
    if news:
        st.caption("Each link opens the news source via the publisher/aggregator. Check the full article and its publication time before drawing conclusions.")
        for item in news:
            with st.container(border=True):
                time_str = item["published_utc"].strftime("%Y-%m-%d %H:%M UTC") if pd.notna(item["published_utc"]) else "Date not supplied"
                st.markdown(f"**{item['title']}**")
                st.caption(f"{time_str} · {item['topic']} · Potential topic: {item['potential_factors']}")
                st.link_button("Read linked coverage ↗", item["url"])
    else:
        st.warning("No current headlines could be retrieved. This is not proof that no market-moving news exists.")

with log_tab:
    st.subheader("Explainable decision changes")
    audit = changes_log(df)
    st.caption("The log records every transition in seven indicator conditions and the overall WAIT / LONG SETUP state. This explains the *model's* decisions, not every real-world price move. All timestamps are UTC.")
    if len(audit):
        st.dataframe(audit.sort_values("decision_time_utc", ascending=False),
                     hide_index=True, width="stretch")
        st.download_button("Download decision audit (CSV)", audit.to_csv(index=False).encode(),
                           file_name=f"{symbol}_decision_audit.csv", mime="text/csv")
    else:
        st.info("No rule transitions in the loaded history.")
    st.caption("A news story or funding snapshot is never retroactively assigned to a historical signal; news and present-day derivatives snapshots are kept separate to avoid future-data leakage.")

with method_tab:
    st.subheader("Method, assumptions and validation")
    st.markdown("""
**Original core watchlist:** BTCUSDT, ETHUSDT, SOLUSDT and BNBUSDT perpetual futures, preserved for comparison with the earlier model.

**Expanded Binance universe:** Pull active USDT-margined perpetual contracts from `/fapi/v1/exchangeInfo`, join with the 24h ticker data, and apply trading-age and quote-volume filters. The original four pairs remain core; other Binance-listed assets are **screened candidates**, not verified legitimate, non-scam or low-risk projects.

**Extra timeframes:** Fetch only completed 5-minute and 15-minute candles, and display their EMA20/50/200, RSI14, MACD histogram, ADX14, ATR14 and volume ratio. Also show 1h and completed 4h counterparts. Fast timeframes are observational and are **not** inserted into the 1h historical trading model without dedicated multi-timeframe validation.

**Updating quote:** Binance's public USDⓈ-M WebSocket sends aggregated trades around every 100 milliseconds and mark/index values around every second. The dashboard repaints around once per second. It shows only fresh stream data; on interruption, a clearly marked V2 REST snapshot may appear. A disconnected or stale stream is **not** represented as a live price. No last price is a guaranteed executable bid/ask quote.

**Timing:** Evaluate only a completed 1-hour candle. Resample 4h bars aligned to 00:00/04:00/08:00 UTC, and use a 4h bar only after all four constituent hourly bars have completed. Historical entries occur at the next candle open.

**Mandatory long filters:** 4h EMA50 > EMA200 and close > EMA200; 1h EMA20 > EMA50 and close > EMA20; 1h ATR/price between 0.15% and 5%.

**Confirmations (3 of 4):** RSI14 50–70, MACD(12,26,9) histogram positive, ADX14 ≥ 18, volume ≥ 0.8× its 20-hour mean. A new setup is a transition from WAIT to LONG SETUP. It is **not** a buy instruction.

**Risk design:** Illustrative long entry reference = last completed 1h close. Stop distance = 1.8× ATR14 (adjustable). Target distance = stop distance × 1.5 (adjustable). Quantity capped by the smaller of risk budget ÷ stop distance and equity × notional cap ÷ entry price.

**Backtest execution:** Next hourly open plus adverse slippage; one position per selected symbol, only on new setups. Entry and exit taker fees modeled. Stop gaps fill at the worse open. If SL and TP both touch in one candle, SL wins. Equity curve includes a mark-to-market estimate for an open trade.

**Significant limitations:** No funding-payment simulation, liquidation engine, maintenance margin, stop order mechanics, spread/order-book impact, price/quantity exchange filters, portfolio risk correlation, outage handling, or walk-forward/out-of-sample validation. No survival-bias-free universe. These make results unsuitable for live capital decisions. Each selected instrument is analyzed individually, not as a jointly constrained portfolio.

**Source reliability:** Binance public USDⓈ-M REST market data; recent Google News search RSS for cited news links. An article's relevance is based on headline words, not tested causal impact. The app is not guaranteed to detect *every* material news item or price-change reason.

**Data errors:** Network failures are displayed; fabricated fallback prices are never used. Candle gaps block performance results. Fetches may be geoblocked or rate-limited.
""")
    st.markdown("Official references: [Binance Futures API](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data) · [Streamlit setup](https://docs.streamlit.io/get-started/installation/command-line)")
