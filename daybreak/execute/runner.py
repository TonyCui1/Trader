"""Phase 7 — the 10:25 ET decision job (`make paper-dry-run` / cron entry).

Sequence (spec): pull fresh data -> compute decision -> submit limit orders at
10:30 (limit = last trade +/- 0.3%) -> unfilled orders cancelled after 15 min
(reconcile job). Kill switch: a file-based flag checked before ANY order.

Failure posture (PLAN.md gap 6): any exception, stale data, or missing input
means today's decision is HOLD — never retry into stale data. The decision log
records the failure explicitly.
"""
from __future__ import annotations

import argparse
import sys
import traceback

import numpy as np
import pandas as pd

from ..config import load_config
from ..data.store import PITStore
from ..data.universe import universe_for_date
from ..mcal import next_trading_days
from ..portfolio.construct import target_portfolio
from ..regime.state import compute_regime
from ..report.decision_log import DecisionLog
from ..signals.compute import compute_signal_panels
from .broker import AlpacaBroker, DryRunBroker, client_order_id


def kill_switch_active(cfg: dict) -> bool:
    from pathlib import Path
    return Path(cfg["execute"]["kill_switch_file"]).exists()


def compute_decision(store: PITStore, cfg: dict, current_shares: dict[str, int]
                     ) -> dict:
    """Pure decision function: store + current positions -> orders + context."""
    panels = compute_signal_panels(store, cfg)
    regime = compute_regime(store, cfg)

    prices = store.read("prices")
    prices["date"] = pd.to_datetime(prices["date"])
    stocks = prices[prices["symbol"] != "SPY"]
    close = stocks.pivot_table(index="date", columns="symbol", values="close").sort_index()
    adj = stocks.pivot_table(index="date", columns="symbol", values="adj_close").sort_index()
    vol60 = adj.pct_change(fill_method=None).rolling(
        cfg["portfolio"]["vol_lookback_days"]).std()

    t_prev = close.index[-1]           # latest ingested close = T-1
    decision_date = next_trading_days(t_prev, 1)[0]

    comp = panels["composite"].loc[t_prev].copy()
    uni = universe_for_date(store, decision_date)
    if not uni.empty:
        comp = comp[comp.index.intersection(uni["symbol"])]
    state = regime.loc[t_prev]

    earnings = store.read("earnings_calendar")
    earnings["earnings_date"] = pd.to_datetime(earnings["earnings_date"])
    upcoming = set(next_trading_days(
        decision_date, cfg["portfolio"]["earnings_blackout_days"])) | {decision_date}
    blackout = set(earnings[earnings["earnings_date"].isin(upcoming)]["symbol"])

    last_close = close.loc[t_prev]
    pos_value = {s: sh * last_close.get(s, 0.0) for s, sh in current_shares.items()}
    equity = cfg["backtest"]["initial_equity"] if not pos_value else None
    # dry-run equity: cash state handled by broker; here weights need equity —
    # runner passes broker equity in, so recompute weights there. For the pure
    # function we treat provided current_shares at last close + implied cash.
    equity = sum(pos_value.values()) + max(
        cfg["backtest"]["initial_equity"] - sum(pos_value.values()), 0)

    current_w = pd.Series({s: v / equity for s, v in pos_value.items()},
                          dtype=float).reindex(comp.index).fillna(0.0)
    for s, v in pos_value.items():
        if s not in current_w.index:
            current_w.loc[s] = v / equity
            comp.loc[s] = np.nan

    from ..data.universe import latest_security_master
    master = latest_security_master(store)
    sectors = pd.Series(dict(zip(master["symbol"], master["sector"])))

    target = target_portfolio(comp, vol60.loc[t_prev].reindex(comp.index),
                              sectors, current_w, state, blackout, cfg)
    deltas = (target - current_w)
    orders = []
    for sym, dw in deltas[deltas.abs() > 1e-9].sort_values().items():
        px = last_close.get(sym, np.nan)
        if np.isnan(px) or px <= 0:
            continue
        side = "buy" if dw > 0 else "sell"
        qty = int(abs(dw) * equity / px)
        if side == "sell":
            qty = min(qty, current_shares.get(sym, 0))
        if qty <= 0:
            continue
        offset = cfg["execute"]["limit_offset_pct"]
        limit = px * (1 + offset) if side == "buy" else px * (1 - offset)
        orders.append({"symbol": sym, "side": side, "qty": qty,
                       "limit_price": round(float(limit), 2),
                       "client_order_id": client_order_id(
                           str(decision_date.date()), sym, side, qty)})

    top = comp.dropna().sort_values(ascending=False).head(10)
    return {
        "decision_date": str(decision_date.date()),
        "data_through": str(pd.Timestamp(t_prev).date()),
        "regime": state,
        "decision": "TRADE" if orders else ("HOLD" if current_shares else "CASH"),
        "orders": orders,
        "top_composite": {k: round(float(v), 3) for k, v in top.items()},
        "reasoning": (f"regime={state}; {len(orders)} orders pass conviction/"
                      f"blackout/caps/turnover gates; CASH is the default."),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="compute + log the decision, simulate fills locally")
    args = ap.parse_args(argv)
    cfg = load_config()
    store = PITStore(cfg["run"]["data_dir"])
    log = DecisionLog(cfg["run"]["data_dir"])

    if "prices" not in store.tables():
        print("No data ingested. Run `make ingest-fixture` (or `make ingest`).")
        return 1

    if kill_switch_active(cfg):
        log.append({"decision": "HALT_KILL_SWITCH", "config_hash": cfg["_config_hash"],
                    "note": f"kill switch file present: {cfg['execute']['kill_switch_file']}"})
        print("KILL SWITCH ACTIVE — no orders. Logged.")
        return 3

    broker = DryRunBroker(cfg["run"]["data_dir"]) if args.dry_run else AlpacaBroker(cfg)

    try:
        decision = compute_decision(store, cfg, broker.get_positions())
    except Exception:
        rec = log.append({"decision": "HOLD_ON_ERROR",
                          "config_hash": cfg["_config_hash"],
                          "error": traceback.format_exc()[-2000:]})
        print("ERROR during decision computation — defaulting to HOLD "
              "(never trade on broken inputs). Logged.")
        return 4

    results = []
    for o in decision["orders"]:
        results.append(broker.submit_order(o["symbol"], o["side"], o["qty"],
                                           o["limit_price"], o["client_order_id"]))
    if args.dry_run:
        broker.save()

    inputs_hash = "|".join(store.table_hash(t) for t in sorted(store.tables()))
    rec = log.append({**decision, "config_hash": cfg["_config_hash"],
                      "inputs_hash": inputs_hash[:64],
                      "submitted": results, "mode": "dry-run" if args.dry_run else "paper"})
    print(f"[{decision['decision_date']}] decision={decision['decision']} "
          f"regime={decision['regime']} orders={len(decision['orders'])} "
          f"(mode={'dry-run' if args.dry_run else 'paper'})")
    for o in decision["orders"][:10]:
        print(f"  {o['side']:4s} {o['qty']:>6d} {o['symbol']:8s} limit {o['limit_price']}")
    if len(decision["orders"]) > 10:
        print(f"  ... and {len(decision['orders']) - 10} more")
    print(f"decision log: {log.path} (chain valid: {log.verify_chain()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
