"""Backtest CLI: `make backtest`.

Prints NET metrics only (spec core rule 4), the two required benchmarks
(SPY buy-and-hold, SPY with the same regime filter), per-signal attribution,
and the Sharpe>threshold warning banner (spec core rule 5).
Writes reports/backtest_metrics.json + reports/equity_curve.csv.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import load_config
from ..data.store import PITStore
from ..regime.state import EXPOSURE, compute_regime
from ..signals.compute import SIGNAL_NAMES, compute_signal_panels
from .engine import BacktestResult, run_backtest
from .metrics import ANN

SPY_SWITCH_COST = 0.0002  # flat 2bp per unit exposure change for the benchmark


def spy_benchmarks(store: PITStore, regime: pd.Series, daily_index: pd.DatetimeIndex,
                   rf: pd.Series) -> dict:
    prices = store.read_latest("prices", keys=["symbol", "date"])
    prices["date"] = pd.to_datetime(prices["date"])
    spy = (prices[prices["symbol"] == "SPY"].set_index("date")["adj_close"]
           .sort_index().reindex(daily_index).ffill())
    spy_ret = spy.pct_change().fillna(0.0)
    rf = rf.reindex(daily_index).fillna(0.0)

    out = {}
    for name, series in {
        "spy_buy_hold": spy_ret,
        "spy_with_regime_filter": _regime_filtered(spy_ret, regime, rf, daily_index),
    }.items():
        ex = series - rf
        sd = ex.std()
        eq = (1 + series).cumprod()
        out[name] = {
            "net_annual_return": round(float((1 + series.mean()) ** ANN - 1), 4),
            "net_sharpe": round(float(ex.mean() / sd * np.sqrt(ANN)) if sd > 0 else 0.0, 3),
            "max_drawdown": round(float((eq / eq.cummax() - 1).min()), 4),
        }
    return out


def _regime_filtered(spy_ret: pd.Series, regime: pd.Series, rf: pd.Series,
                     idx: pd.DatetimeIndex) -> pd.Series:
    expo = regime.map(EXPOSURE).reindex(idx).ffill().fillna(1.0).shift(1).fillna(1.0)
    switch_cost = expo.diff().abs().fillna(0.0) * SPY_SWITCH_COST
    return expo * spy_ret + (1 - expo) * rf - switch_cost


def attribution(result: BacktestResult) -> dict:
    if result.exposures.empty:
        return {}
    port_ret = result.daily["equity"].pct_change().shift(-1)
    out = {}
    for n in SIGNAL_NAMES:
        if n not in result.exposures:
            continue
        expo = result.exposures[n].reindex(result.daily.index).fillna(0.0)
        contrib = (expo * port_ret).mean() * ANN
        out[n] = {"avg_exposure": round(float(expo.mean()), 3),
                  "naive_annual_contribution": round(float(contrib), 4)}
    return out


def main(argv: list[str] | None = None) -> int:
    cfg = load_config()
    store = PITStore(cfg["run"]["data_dir"])
    if "prices" not in store.tables():
        print("No data ingested. Run `make ingest-fixture` (or `make ingest`).")
        return 1

    panels = compute_signal_panels(store, cfg)
    regime = compute_regime(store, cfg)
    result = run_backtest(store, cfg, panels, regime)

    m = result.metrics
    if m["net_sharpe"] > cfg["backtest"]["sharpe_warning_level"]:
        print("=" * 72)
        print(f"!! WARNING: net Sharpe {m['net_sharpe']} > "
              f"{cfg['backtest']['sharpe_warning_level']} — TREAT AS A BUG until"
              " walk-forward and lag tests pass (spec core rule 5).")
        print("=" * 72)

    bench = spy_benchmarks(store, regime, result.daily.index, result.daily["rf_daily"])
    attr = attribution(result)

    print(f"\ndaybreak backtest  [config {result.config_hash}]")
    print(json.dumps(m, indent=2))
    print("\nbenchmarks:")
    print(json.dumps(bench, indent=2))
    print("\nper-signal attribution (diagnostic):")
    print(json.dumps(attr, indent=2))
    for w in result.warnings:
        print(f"WARN: {w}")

    reports = Path("reports")
    reports.mkdir(exist_ok=True)
    with open(reports / "backtest_metrics.json", "w") as f:
        json.dump({"config_hash": result.config_hash, "metrics": m,
                   "benchmarks": bench, "attribution": attr}, f, indent=2)
    result.daily.to_csv(reports / "equity_curve.csv")
    result.trades.to_csv(reports / "trades.csv", index=False)
    print(f"\nwrote reports/backtest_metrics.json, equity_curve.csv, trades.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
