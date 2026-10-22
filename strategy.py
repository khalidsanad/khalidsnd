"""Causal, transparent long-only research strategy on 1h/closed 4h bars."""

from __future__ import annotations

from dataclasses import dataclass
import math
import numpy as np
import pandas as pd


CHECKS = (
    ("trend_4h_ok", "4h EMA50 > EMA200 and close > EMA200"),
    ("trend_1h_ok", "1h EMA20 > EMA50 and close > EMA20"),
    ("rsi_ok", "1h RSI between 50 and 70"),
    ("macd_ok", "1h MACD histogram above zero"),
    ("adx_ok", "1h ADX at least 18"),
    ("volume_ok", "Volume at least 80% of 20-bar average"),
    ("volatility_ok", "ATR between 0.15% and 5% of price"),
)


@dataclass(frozen=True)
class RiskSettings:
    capital: float = 1000.0
    risk_pct: float = 1.0
    leverage_cap: float = 2.0
    stop_atr: float = 1.8
    reward_risk: float = 1.5
    taker_fee_pct: float = 0.05
    slippage_pct: float = 0.02


def _wilder(x: pd.Series, n: int = 14) -> pd.Series:
    return x.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    frame = df.copy()
    c, h, l = frame["close"], frame["high"], frame["low"]
    for n in (20, 50, 200):
        frame[f"ema{n}"] = c.ewm(span=n, adjust=False, min_periods=n).mean()

    delta = c.diff()
    up = _wilder(delta.clip(lower=0))
    down = _wilder(-delta.clip(upper=0))
    rs = up / down.replace(0, np.nan)
    frame["rsi"] = (100 - 100 / (1 + rs)).where(down != 0, 100.0)
    frame.loc[(down == 0) & (up == 0), "rsi"] = 50.0

    prev = c.shift(1)
    tr = pd.concat([h - l, (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
    frame["atr"] = _wilder(tr)
    pos_move, neg_move = h.diff(), -l.diff()
    plus_dm = pd.Series(np.where((pos_move > neg_move) & (pos_move > 0), pos_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((neg_move > pos_move) & (neg_move > 0), neg_move, 0.0), index=df.index)
    plus_di = 100 * _wilder(plus_dm) / frame["atr"].replace(0, np.nan)
    minus_di = 100 * _wilder(minus_dm) / frame["atr"].replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    frame["adx"] = _wilder(dx)

    ema12 = c.ewm(span=12, adjust=False, min_periods=12).mean()
    ema26 = c.ewm(span=26, adjust=False, min_periods=26).mean()
    frame["macd"] = ema12 - ema26
    signal = frame["macd"].ewm(span=9, adjust=False, min_periods=9).mean()
    frame["macd_hist"] = frame["macd"] - signal
    frame["volume_ratio"] = frame["volume"] / frame["volume"].rolling(20, min_periods=20).mean().replace(0, np.nan)
    frame["atr_pct"] = frame["atr"] / c
    return frame


def closed_four_hour_bars(one_hour: pd.DataFrame) -> pd.DataFrame:
    """4h candle timestamp is its closing time; incomplete 4h groups are excluded.

    Grouping uses exchange-aligned UTC [00:00,04:00), [04:00,08:00), etc.
    Only four consecutive, complete 1h candles form a valid 4h candle.
    """
    aggregate = one_hour.resample("4h", label="right", closed="left", origin="start_day").agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), volume=("volume", "sum"), count=("close", "count"))
    aggregate = aggregate[aggregate["count"] == 4].copy()
    # Four rows do not guarantee consecutive hours if an upstream timestamp is missing.
    time_span = one_hour.index.to_series().resample(
        "4h", label="right", closed="left", origin="start_day").agg(lambda x: (x.max() - x.min()).total_seconds() / 3600 if len(x) else -1)
    aggregate = aggregate[time_span.reindex(aggregate.index) == 3].drop(columns="count")
    aggregate.index.name = "four_hour_completed_at"
    return aggregate


def build_features(one_hour: pd.DataFrame) -> pd.DataFrame:
    one_hour = one_hour.sort_index().copy()
    if one_hour.index.tz is None:
        raise ValueError("UTC timezone-aware candles required")
    # Binance gives millisecond timestamps. With pandas 3, resampling may keep
    # that resolution while arithmetic on the 1h time creates microseconds.
    # merge_asof needs identical datetime units on both sides of its join.
    one_hour.index = one_hour.index.as_unit("ns")
    hourly = add_indicators(one_hour)
    h4 = add_indicators(closed_four_hour_bars(one_hour))
    h4 = h4[["close", "ema50", "ema200"]].rename(columns={"close": "close_4h", "ema50": "ema50_4h", "ema200": "ema200_4h"})
    h4["four_hour_completed_at"] = h4.index
    # The 1h candle opening at 03:00 is only decidable at 04:00.
    # merge_asof reads a 4h bar ONLY when its closing time <= this decision time.
    hourly["decision_time"] = hourly.index + pd.Timedelta(hours=1)
    joined = pd.merge_asof(hourly.reset_index().sort_values("decision_time"),
                           h4.reset_index(drop=True).sort_values("four_hour_completed_at"),
                           left_on="decision_time", right_on="four_hour_completed_at", direction="backward")
    joined = joined.set_index("open_time_utc").sort_index()
    required = ("ema200", "rsi", "adx", "macd_hist", "volume_ratio", "atr", "ema200_4h")
    joined["ready"] = joined[list(required)].notna().all(axis=1)
    joined["trend_4h_ok"] = (joined["ema50_4h"] > joined["ema200_4h"]) & (joined["close_4h"] > joined["ema200_4h"])
    joined["trend_1h_ok"] = (joined["ema20"] > joined["ema50"]) & (joined["close"] > joined["ema20"])
    joined["rsi_ok"] = joined["rsi"].between(50, 70, inclusive="both")
    joined["macd_ok"] = joined["macd_hist"] > 0
    joined["adx_ok"] = joined["adx"] >= 18
    joined["volume_ok"] = joined["volume_ratio"] >= 0.8
    joined["volatility_ok"] = joined["atr_pct"].between(0.0015, 0.05, inclusive="both")
    optional = ("rsi_ok", "macd_ok", "adx_ok", "volume_ok")
    joined["strength"] = joined[list(optional)].sum(axis=1)
    joined["long_setup"] = (joined["ready"] & joined["trend_4h_ok"] & joined["trend_1h_ok"]
                            & joined["volatility_ok"] & (joined["strength"] >= 3))
    joined["state"] = np.where(joined["long_setup"], "LONG SETUP", "WAIT")
    return joined


def reasons(row: pd.Series) -> list[str]:
    if not bool(row["ready"]):
        return ["Not enough completed history for the 4h EMA200 and other indicators"]
    result = []
    for key, label in CHECKS:
        result.append(("PASS: " if bool(row[key]) else "FAIL: ") + label)
    result.append(f"Optional confirmations: {int(row['strength'])}/4 (need 3)")
    return result


def changes_log(df: pd.DataFrame) -> pd.DataFrame:
    """Record EVERY observed condition transition and state transition, not only trades."""
    records = []
    previous = None
    for timestamp, row in df.iterrows():
        current = {key: bool(row[key]) for key, _ in CHECKS}
        current["ready"] = bool(row["ready"])
        current["long_setup"] = bool(row["long_setup"])
        changed = [key for key, value in current.items() if previous is not None and previous[key] != value]
        if changed:
            labels = dict(CHECKS) | {"ready": "Indicators warmed up", "long_setup": "Overall setup state"}
            explanation = "; ".join(f"{labels[key]}: {'ON' if current[key] else 'OFF'}" for key in changed)
            records.append({"decision_time_utc": row["decision_time"], "state": row["state"],
                            "changed_reasons": explanation, "price": float(row["close"])})
        previous = current
    return pd.DataFrame(records, columns=["decision_time_utc", "state", "changed_reasons", "price"])


def proposed_levels(last_close: float, atr: float, settings: RiskSettings) -> dict:
    entry = last_close
    stop = entry - atr * settings.stop_atr
    target = entry + (entry - stop) * settings.reward_risk
    dollars_risked = settings.capital * settings.risk_pct / 100
    quantity = min(dollars_risked / (entry - stop),
                   settings.capital * settings.leverage_cap / entry)
    return {"entry_reference": entry, "stop": stop, "target": target, "qty": quantity,
            "notional": quantity * entry, "gross_risk": quantity * (entry - stop),
            "max_risk_budget": dollars_risked}


def directional_trade_plans(entry: float, atr: float, settings: RiskSettings) -> list[dict]:
    """Symmetrical LONG/SHORT *planning scenarios*, not validated short signals.

    Each side uses the same positive ATR stop distance; targets lie 1R, 2R,
    3R from the reference.  This function never changes strategy decisions or
    backtest trades and refuses impossible nonpositive target/stop prices.
    """
    values = (entry, atr, settings.stop_atr)
    if not all(math.isfinite(x) and x > 0 for x in values):
        raise ValueError("Entry, ATR and stop multiplier must all be positive finite numbers")
    distance = atr * settings.stop_atr
    if not math.isfinite(distance) or entry <= 3 * distance:
        raise ValueError("ATR stop is too large for valid positive short targets")
    return [
        {"Direction": "LONG", "Entry": entry, "SL": entry - distance,
         "TP1": entry + distance, "TP2": entry + 2 * distance,
         "TP3": entry + 3 * distance},
        {"Direction": "SHORT", "Entry": entry, "SL": entry + distance,
         "TP1": entry - distance, "TP2": entry - 2 * distance,
         "TP3": entry - 3 * distance},
    ]


def timeframe_snapshot(candles: pd.DataFrame, name: str) -> dict:
    """Read-only momentum overview using a completed bar; NOT a new trade signal.

    This intentionally does not alter historical 1h trades. A new fast-entry
    strategy would need independent, causally aligned backtesting first.
    """
    frame = add_indicators(candles)
    row = frame.iloc[-1]
    required = ["ema20", "ema50", "ema200", "rsi", "adx", "macd_hist", "atr_pct"]
    if row[required].isna().any():
        trend = "Warming up"
    elif row["ema20"] > row["ema50"] and row["close"] > row["ema200"]:
        trend = "Bullish"
    elif row["ema20"] < row["ema50"] and row["close"] < row["ema200"]:
        trend = "Bearish"
    else:
        trend = "Mixed"
    end = row["close_time"] if "close_time" in row else frame.index[-1]
    return {"Timeframe": name, "Trend": trend,
            "RSI14": round(float(row["rsi"]), 1) if pd.notna(row["rsi"]) else None,
            "ADX14": round(float(row["adx"]), 1) if pd.notna(row["adx"]) else None,
            "MACD hist": round(float(row["macd_hist"]), 5) if pd.notna(row["macd_hist"]) else None,
            "ATR %": round(float(row["atr_pct"]) * 100, 3) if pd.notna(row["atr_pct"]) else None,
            "Volume / avg": round(float(row["volume_ratio"]), 2) if pd.notna(row["volume_ratio"]) else None,
            "Closed at (UTC)": str(end)}
