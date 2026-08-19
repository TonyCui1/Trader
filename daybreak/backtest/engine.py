"""Phase 5 — event-driven daily simulator.

Realism rules (spec):
- Decisions at 10:30 ET use signal/regime data through the PRIOR close
  (signal_lag=1; the validation lag test reruns with signal_lag=2).
- Fills at the day's actual 10:30 minute-bar VWAP — never the daily close —
  at UNadjusted prices, whole shares only.
- Every fill pays the cost model. Cash earns the risk-free rate.
- The circuit breaker halts trading; a halt flattens the book at the NEXT
  10:30 (you cannot trade at a close you haven't seen).

Portfolio valuation for sizing uses the T-1 close: that is known at decision
time and is not a model input, so it is never extra-lagged by the lag test.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from ..data.store import PITStore
from ..portfolio.breaker import CircuitBreaker
from ..portfolio.construct import target_portfolio
from ..regime.state import OFF
from .costs import trade_cost
from .metrics import compute_metrics

ADV_LOOKBACK = 63
MIN_NAMES_FOR_START = 20


@dataclass
class BacktestResult:
    daily: pd.DataFrame
    trades: pd.DataFrame
    exposures: pd.DataFrame
    metrics: dict
    config_hash: str
    warnings: list[str] = field(default_factory=list)


def run_backtest(store: PITStore, cfg: dict, panels: dict, regime: pd.Series,
                 signal_lag: int = 1, cost_multiplier: float = 1.0,
                 composite_hook: Callable[[int, pd.Series], pd.Series] | None = None,
                 ) -> BacktestResult:
    from ..signals.compute import SIGNAL_NAMES

    prices = store.read_latest("prices", keys=["symbol", "date"])
    prices["date"] = pd.to_datetime(prices["date"])
    stocks = prices[prices["symbol"] != "SPY"]
    close = stocks.pivot_table(index="date", columns="symbol", values="close").sort_index()
    high = stocks.pivot_table(index="date", columns="symbol", values="high").sort_index()
    low = stocks.pivot_table(index="date", columns="symbol", values="low").sort_index()
    adj = stocks.pivot_table(index="date", columns="symbol", values="adj_close").sort_index()

    warnings: list[str] = []
    minute = store.read_latest("minute_1030", keys=["symbol", "date"])
    if not minute.empty:
        minute["date"] = pd.to_datetime(minute["date"])
        vwap = (minute[minute["symbol"] != "SPY"]
                .pivot_table(index="date", columns="symbol", values="vwap_1030")
                .reindex(index=close.index, columns=close.columns))
    else:
        vwap = pd.DataFrame(np.nan, index=close.index, columns=close.columns)

    # Fill-price fallback: where no real 10:30 minute bar exists (historical
    # live-mode data — the free tier cannot backfill minute bars at scale),
    # approximate the 10:30 price as open + 35% of the day's open->close move.
    # Real 10:30 bars accumulate daily once paper trading starts, and the
    # reconciliation slippage log measures how good this proxy is.
    opens = stocks.pivot_table(index="date", columns="symbol", values="open").sort_index()
    proxy = opens + 0.35 * (close - opens)
    n_missing = int((vwap.isna() & close.notna()).to_numpy().sum())
    n_total = int(close.notna().to_numpy().sum())
    if n_missing:
        warnings.append(
            f"{n_missing}/{n_total} bar-days ({n_missing / max(n_total, 1):.0%}) "
            "had no 10:30 minute bar; fills used the daily-bar proxy")
    vwap = vwap.fillna(proxy)

    dollar_vol = close * stocks.pivot_table(index="date", columns="symbol",
                                            values="volume").sort_index()
    adv = dollar_vol.rolling(ADV_LOOKBACK, min_periods=10).mean()

    vol60 = adj.pct_change(fill_method=None).rolling(
        cfg["portfolio"]["vol_lookback_days"]).std()

    macro = store.read_latest("macro", keys=["date"])
    macro["date"] = pd.to_datetime(macro["date"])
    rf = macro.set_index("date")["rf_daily"].reindex(close.index).fillna(0.0)

    french = store.read_latest("french_factors", keys=["date"])
    if not french.empty:
        french["date"] = pd.to_datetime(french["date"])
        rf = french.set_index("date")["rf_daily"].reindex(close.index).ffill().fillna(rf)

    earnings = store.read("earnings_calendar")
    earnings_by_day: dict[pd.Timestamp, set] = {}
    if not earnings.empty:
        earnings["earnings_date"] = pd.to_datetime(earnings["earnings_date"])
        for _, row in earnings.iterrows():
            earnings_by_day.setdefault(row["earnings_date"].normalize(), set()).add(row["symbol"])
    else:
        warnings.append("earnings_calendar is empty — the earnings blackout "
                        "filter is inactive (run the full `make ingest`)")

    from ..data.universe import latest_security_master
    master = latest_security_master(store)
    sectors = pd.Series(dict(zip(master["symbol"], master["sector"])))

    # precompute PIT universe membership per month (avoids per-day DB reads)
    from ..data.universe import non_stock_symbols
    etfs = non_stock_symbols(store)
    uni_all = store.read("universe")
    uni_by_month: dict[pd.Timestamp, set] = {}
    if not uni_all.empty:
        uni_all["month"] = pd.to_datetime(uni_all["month"])
        for m, grp in uni_all.groupby("month"):
            uni_by_month[m] = set(grp["symbol"]) - etfs
    uni_months = sorted(uni_by_month)

    composite_panel = panels["composite"].reindex(index=close.index, columns=close.columns)
    # per-signal panels can have a different date index than `close` (e.g.
    # compute_signal_panels excludes ETF symbols entirely, which can drop a
    # date with no non-ETF price rows) — reindex once so `.iloc[prev_i]`
    # below, which walks close.index positions, stays in bounds.
    signal_panels = {n: panels[n].reindex(index=close.index, columns=close.columns)
                     for n in SIGNAL_NAMES}
    regime = regime.reindex(close.index).ffill().fillna("ON")

    # start when enough names have a composite score (warmup) and >= config start
    valid = (composite_panel.notna().sum(axis=1) >= MIN_NAMES_FOR_START)
    start_cfg = pd.Timestamp(str(cfg["backtest"]["start"]))
    startable = valid[valid & (valid.index >= start_cfg)]
    if startable.empty:
        startable = valid[valid]
        warnings.append("backtest.start predates signal warmup; started at first valid day")
    if startable.empty:
        raise RuntimeError("no dates with enough composite coverage to run")
    dates = close.index[close.index >= startable.index[0]]
    dates = dates[signal_lag:] if signal_lag >= len(dates) - 1 else dates
    end_cfg = cfg["backtest"]["end"]
    if end_cfg:
        dates = dates[dates <= pd.Timestamp(str(end_cfg))]

    breaker = CircuitBreaker(
        cfg, cooldown_days=cfg["portfolio"].get("circuit_breaker_cooldown_days"))
    cash = float(cfg["backtest"]["initial_equity"])
    positions: dict[str, list] = {}  # sym -> [shares, avg_cost]
    daily_rows, trade_rows, expo_rows = [], [], []

    date_pos = {d: i for i, d in enumerate(close.index)}

    for t in dates:
        i = date_pos[t]
        prev_i = i - signal_lag
        val_i = i - 1
        if prev_i < 0 or val_i < 0:
            continue

        cash *= 1 + float(rf.iloc[i])

        prev_close_row = close.iloc[val_i]
        equity_est = cash + sum(sh * prev_close_row.get(sym, np.nan)
                                for sym, (sh, _) in positions.items()
                                if not np.isnan(prev_close_row.get(sym, np.nan)))

        trades_today: list[dict] = []
        halted = breaker.halted and not breaker.restart_flag

        if halted and positions:
            # flatten at today's 10:30
            for sym in list(positions):
                sh, avg = positions[sym]
                px = vwap.at[t, sym] if sym in vwap.columns else np.nan
                if np.isnan(px):
                    trade_rows.append({"date": t, "symbol": sym, "side": "sell",
                                       "shares": 0, "price": np.nan, "cost": 0.0,
                                       "realized_gain": 0.0, "note": "untradeable_hold"})
                    continue
                notional = sh * px
                cost = trade_cost(notional, sh, "sell", high.at[t, sym],
                                  low.at[t, sym], adv.iat[prev_i, adv.columns.get_loc(sym)],
                                  cfg, cost_multiplier)
                cash += notional - cost
                trades_today.append({"date": t, "symbol": sym, "side": "sell",
                                     "shares": sh, "price": px, "cost": cost,
                                     "realized_gain": (px - avg) * sh - cost,
                                     "note": "breaker_flatten"})
                del positions[sym]
        elif not halted:
            comp = composite_panel.iloc[prev_i].copy()
            month_keys = [m for m in uni_months if m <= t]
            if month_keys:
                comp = comp[comp.index.intersection(uni_by_month[month_keys[-1]])]
            if composite_hook is not None:
                comp = composite_hook(prev_i, comp)

            state = regime.iloc[prev_i]
            vols = vol60.iloc[prev_i]

            # blackout: earnings today or within the next N trading days
            # (the price index IS the trading calendar)
            n_black = cfg["portfolio"]["earnings_blackout_days"]
            upcoming = close.index[i:i + n_black + 1]
            blackout: set = set()
            for d in upcoming:
                blackout |= earnings_by_day.get(d.normalize(), set())

            current_w = pd.Series(
                {sym: sh * prev_close_row.get(sym, 0.0) / equity_est
                 for sym, (sh, _) in positions.items()}, dtype=float
            ).reindex(comp.index).fillna(0.0) if equity_est > 0 else pd.Series(0.0, index=comp.index)
            # names held but outside today's scored set must remain visible
            for sym, (sh, _) in positions.items():
                if sym not in current_w.index:
                    px_prev = prev_close_row.get(sym, np.nan)
                    current_w.loc[sym] = (sh * px_prev / equity_est
                                          if not np.isnan(px_prev) else 0.0)
                    comp.loc[sym] = np.nan
            current_w = current_w.fillna(0.0)

            target = target_portfolio(comp, vols.reindex(comp.index), sectors,
                                      current_w, state, blackout, cfg)

            deltas = (target - current_w).sort_values()  # sells first frees cash
            for sym, dw in deltas.items():
                if np.isnan(dw) or abs(dw) < 1e-9:
                    continue
                px = vwap.at[t, sym] if sym in vwap.columns else np.nan
                if np.isnan(px) or px <= 0:
                    trade_rows.append({"date": t, "symbol": sym, "side": "n/a",
                                       "shares": 0, "price": np.nan, "cost": 0.0,
                                       "realized_gain": 0.0, "note": "no_1030_bar"})
                    continue
                dollars = dw * equity_est
                adv_d = adv.at[close.index[prev_i], sym] if sym in adv.columns else np.nan
                adv_d = adv_d if not np.isnan(adv_d) else 1e6
                if dollars < 0:
                    held = positions.get(sym, [0, 0.0])
                    sh = min(int(abs(dollars) / px), held[0])
                    # a full exit must not strand a sub-1-share remainder
                    if abs(dollars) >= 0.99 * held[0] * px:
                        sh = held[0]
                    if sh <= 0:
                        continue
                    notional = sh * px
                    cost = trade_cost(notional, sh, "sell", high.at[t, sym],
                                      low.at[t, sym], adv_d, cfg, cost_multiplier)
                    cash += notional - cost
                    gain = (px - held[1]) * sh - cost
                    held[0] -= sh
                    if held[0] == 0:
                        positions.pop(sym, None)
                    trades_today.append({"date": t, "symbol": sym, "side": "sell",
                                         "shares": sh, "price": px, "cost": cost,
                                         "realized_gain": gain, "note": ""})
                else:
                    sh = int(dollars / px)
                    if sh <= 0:
                        continue
                    notional = sh * px
                    cost = trade_cost(notional, sh, "buy", high.at[t, sym],
                                      low.at[t, sym], adv_d, cfg, cost_multiplier)
                    if notional + cost > cash:
                        sh = int((cash * 0.995) / px)
                        if sh <= 0:
                            continue
                        notional = sh * px
                        cost = trade_cost(notional, sh, "buy", high.at[t, sym],
                                          low.at[t, sym], adv_d, cfg, cost_multiplier)
                    cash -= notional + cost
                    held = positions.setdefault(sym, [0, 0.0])
                    held[1] = (held[1] * held[0] + px * sh) / (held[0] + sh)
                    held[0] += sh
                    trades_today.append({"date": t, "symbol": sym, "side": "buy",
                                         "shares": sh, "price": px, "cost": cost,
                                         "realized_gain": 0.0, "note": ""})

            held_syms = target[target > 0]
            expo_row = {"date": t}
            for n in SIGNAL_NAMES:
                sig_row = signal_panels[n].iloc[prev_i]
                expo_row[n] = float(np.nansum(
                    [w * sig_row.get(sym, np.nan) for sym, w in held_syms.items()]))
            expo_rows.append(expo_row)

        trade_rows.extend(trades_today)
        close_row = close.iloc[i]
        eod_equity = cash + sum(sh * close_row.get(sym, prev_close_row.get(sym, 0.0))
                                for sym, (sh, _) in positions.items())
        turnover = sum(tr["shares"] * tr["price"] for tr in trades_today
                       if not np.isnan(tr["price"])) / equity_est if equity_est > 0 else 0.0
        n_buys = sum(1 for tr in trades_today if tr["side"] == "buy")
        n_sells = sum(1 for tr in trades_today if tr["side"] == "sell")
        if halted:
            decision = "CASH_HALTED"
        elif n_buys or n_sells:
            decision = "TRADE"
        elif positions:
            decision = "HOLD"
        else:
            decision = "CASH"
        daily_rows.append({
            "date": t, "equity": eod_equity, "cash": cash,
            "n_positions": len(positions), "regime": regime.iloc[prev_i],
            "turnover": turnover, "n_buys": n_buys, "n_sells": n_sells,
            "decision": decision, "rf_daily": float(rf.iloc[i]),
            "costs_today": sum(tr["cost"] for tr in trades_today),
        })
        breaker.check(eod_equity, t)

    daily = pd.DataFrame(daily_rows).set_index("date")
    trades = pd.DataFrame(trade_rows)
    exposures = pd.DataFrame(expo_rows).set_index("date") if expo_rows else pd.DataFrame()
    metrics = compute_metrics(daily, trades, cfg)
    if breaker.trip_log:
        metrics["circuit_breaker_trips"] = breaker.trip_log
        warnings.append(
            f"circuit breaker tripped {len(breaker.trip_log)}x "
            f"(backtest auto-resumes after "
            f"{cfg['portfolio'].get('circuit_breaker_cooldown_days')} trading days; "
            f"live trading requires manual restart): {breaker.trip_log}")
    return BacktestResult(daily, trades, exposures, metrics,
                          cfg["_config_hash"], warnings)
