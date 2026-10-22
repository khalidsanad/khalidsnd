"""Bar-by-bar paper backtest; explicitly does not simulate the futures exchange."""

from __future__ import annotations

import numpy as np
import pandas as pd

from strategy import RiskSettings


def run_backtest(df: pd.DataFrame, settings: RiskSettings) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Enter on next candle open after a newly appearing LONG SETUP.

    With ambiguous 1h OHLC (stop and target both hit), assume stop first.
    Slippage is always charged adversely; stop gaps fill at the worse open.
    Mark-to-market includes estimated exit cost; funding/liquidation excluded.
    """
    balance = float(settings.capital)
    fee_rate = settings.taker_fee_pct / 100
    slip_rate = settings.slippage_pct / 100
    position = None
    trades, equity_points = [], []

    for i, (when, bar) in enumerate(df.iterrows()):
        if position is None and i >= 2:
            signal = bool(df["long_setup"].iloc[i - 1])
            new_signal = signal and not bool(df["long_setup"].iloc[i - 2])
            previous = df.iloc[i - 1]
            if new_signal and pd.notna(previous["atr"]) and previous["atr"] > 0:
                entry = float(bar["open"]) * (1 + slip_rate)
                gap = float(previous["atr"]) * settings.stop_atr
                stop, target = entry - gap, entry + gap * settings.reward_risk
                if stop > 0 and balance > 0:
                    qty = min(balance * settings.risk_pct / 100 / gap,
                              balance * settings.leverage_cap / entry)
                    if qty > 0:
                        position = {"entry_time": when, "signal_time": previous["decision_time"],
                                    "entry": entry, "stop": stop, "target": target,
                                    "qty": qty, "entry_fee": qty * entry * fee_rate,
                                    "explanation": " | ".join(
                                        [name for name in ("4h trend", "1h trend", "RSI", "MACD", "ADX", "volume")
                                         if (name in ("4h trend", "1h trend") or
                                             bool(previous[{"RSI": "rsi_ok", "MACD": "macd_ok", "ADX": "adx_ok", "volume": "volume_ok"}[name]]))])}

        if position is not None:
            p = position
            raw_exit, reason = None, None
            if float(bar["low"]) <= p["stop"]:
                raw_exit = min(float(bar["open"]), p["stop"])
                reason = "STOP (or both hit; pessimistic stop-first)"
            elif float(bar["high"]) >= p["target"]:
                raw_exit = p["target"]
                reason = "TARGET"
            if raw_exit is not None:
                exit_price = raw_exit * (1 - slip_rate)
                exit_fee = p["qty"] * exit_price * fee_rate
                gross = (exit_price - p["entry"]) * p["qty"]
                net = gross - p["entry_fee"] - exit_fee
                balance += net
                trades.append({"signal_utc": p["signal_time"], "entry_utc": p["entry_time"],
                               "exit_utc": when + pd.Timedelta(hours=1),
                               "entry": p["entry"], "exit": exit_price, "stop": p["stop"],
                               "target": p["target"], "quantity": p["qty"],
                               "fees_usdt": p["entry_fee"] + exit_fee,
                               "net_pnl_usdt": net, "balance_after": balance,
                               "exit_reason": reason, "entry_reason": p["explanation"]})
                position = None

        mtm = balance
        if position is not None:
            p = position
            mark_exit = float(bar["close"]) * (1 - slip_rate)
            mtm += (mark_exit - p["entry"]) * p["qty"] - p["entry_fee"] - (p["qty"] * mark_exit * fee_rate)
        equity_points.append({"time_utc": when + pd.Timedelta(hours=1), "equity_usdt": mtm})

    trades_df = pd.DataFrame(trades)
    equity_df = pd.DataFrame(equity_points)
    curve = equity_df["equity_usdt"]
    dd = (curve / curve.cummax() - 1).min() * 100 if len(curve) else 0.0
    won = trades_df["net_pnl_usdt"] > 0 if len(trades_df) else pd.Series(dtype=bool)
    profits = trades_df.loc[won, "net_pnl_usdt"].sum() if len(trades_df) else 0.0
    losses = trades_df.loc[~won, "net_pnl_usdt"].sum() if len(trades_df) else 0.0
    summary = {
        "closed_trades": len(trades_df),
        "wins_pct": 100 * won.mean() if len(trades_df) else np.nan,
        "net_realized_usdt": balance - settings.capital,
        "last_equity_usdt": float(curve.iloc[-1]) if len(curve) else settings.capital,
        "return_pct": 100 * (float(curve.iloc[-1]) / settings.capital - 1) if len(curve) else 0.0,
        "max_drawdown_pct": float(dd),
        "profit_factor": profits / abs(losses) if losses < 0 else (np.inf if profits > 0 else np.nan),
        "open_trade": position is not None,
    }
    return trades_df, equity_df, summary
