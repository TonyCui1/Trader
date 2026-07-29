"""Phase 7 — 11:00 ET reconciliation job.

Cancels orders still unfilled after the configured window, compares intended
vs. filled, and logs realized slippage vs. the model's estimate. That slippage
log is the ground truth that recalibrates the cost model's `k` (spec).
"""
from __future__ import annotations

import argparse
import sys

import pandas as pd

from ..config import load_config
from ..data.store import PITStore
from ..report.decision_log import DecisionLog
from .broker import AlpacaBroker, DryRunBroker


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    cfg = load_config()
    store = PITStore(cfg["run"]["data_dir"])
    log = DecisionLog(cfg["run"]["data_dir"])

    recs = [r for r in log.read_all() if r.get("orders")]
    if not recs:
        print("no order-bearing decisions to reconcile")
        return 0
    latest = recs[-1]
    date = latest["decision_date"]

    broker = DryRunBroker(cfg["run"]["data_dir"]) if args.dry_run else AlpacaBroker(cfg)
    if not args.dry_run:
        broker.cancel_open_orders()  # unfilled past the window -> cancel

    fills = {o["client_order_id"]: o for o in broker.get_orders(date)}
    rows = []
    for o in latest["orders"]:
        f = fills.get(o["client_order_id"])
        filled = f is not None and f.get("fill_price")
        fill_px = float(f["fill_price"]) if filled else None
        slip = None
        if filled:
            ref = o["limit_price"]
            sign = 1 if o["side"] == "buy" else -1
            slip = sign * (fill_px - ref) / ref
        rows.append({"date": pd.Timestamp(date), "symbol": o["symbol"],
                     "side": o["side"], "intended_qty": o["qty"],
                     "filled": bool(filled), "fill_price": fill_px,
                     "limit_price": o["limit_price"],
                     "slippage_vs_limit": round(slip, 6) if slip is not None else None})
    df = pd.DataFrame(rows)
    df["source_ts"] = pd.Timestamp.utcnow().tz_localize(None)
    df["ingested_at"] = pd.Timestamp.utcnow().tz_localize(None)
    store.append("slippage_log", df)
    unfilled = int((~df["filled"]).sum())
    print(f"[{date}] reconciled {len(df)} orders; unfilled: {unfilled}; "
          f"slippage rows appended to slippage_log")
    return 0


if __name__ == "__main__":
    sys.exit(main())
