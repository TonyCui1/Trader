"""Optional learned recalibration of the execution-cost model (opt-in; the
fixed formula in `costs.py` remains the validated default until enough real
paper-trading fills exist — see costs.calibration in config.yaml).

Background (spec Phase 7): `execute/reconcile.py` appends every real fill's
realized slippage vs. the 10:30 VWAP reference to the append-only PIT table
`slippage_log`. That is the ground truth this module uses to recalibrate the
two free parameters of the fixed cost formula:

    cost_pct = spread_scale * half_spread_pct(bar) + slippage_k * sqrt(order / ADV)

`spread_scale` defaults to 1.0 and `slippage_k` to `costs.slippage_k` (0.1)
in the unfitted formula — this module fits both from data via one
non-negative least squares regression (scipy.optimize.nnls), never a general
model over raw features (same "simple models before deep ones" scope
discipline as signals/ml_weights.py).

Walk-forward, PIT-safe (mirrors ml_weights.py's discipline): refit monthly
using only slippage-log fills strictly before the fold's start. A fold's
fit is applied only to dates within that fold. Before enough real fills
exist for a first fit (costs.calibration.min_fills), and whenever a later
refit is degenerate, dates fall back to the PREVIOUS fold's fitted params if
any exist yet, else to the fixed formula (calib=None) — never silently to
NaN or to zero.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import nnls

from .costs import half_spread_pct

# Same ADV definition as backtest/engine.py: 63-trading-day rolling mean of
# close * volume, min_periods=10 (kept as a local constant, not imported from
# engine.py, to avoid a circular import — engine.py imports this module).
ADV_LOOKBACK = 63

INPUT_COLUMNS = ["date", "symbol", "side", "realized_cost_pct",
                 "spread_est", "sqrt_participation"]
SCHEDULE_COLUMNS = ["spread_scale", "slippage_k", "n_samples", "source"]


def build_calibration_inputs(store, cfg: dict) -> pd.DataFrame:
    """Real fills from `slippage_log`, joined to their 10:30 VWAP reference
    price and point-in-time ADV, turned into regression-ready rows.

    `adv_dollars` (and thus `sqrt_participation`) uses the ADV value as of
    the trading day strictly BEFORE the fill's date (T-1) — the same value
    that would have been available to the backtest at decision time — so
    this is PIT-safe by construction.
    """
    empty = pd.DataFrame(columns=INPUT_COLUMNS)

    log = store.read_latest("slippage_log", keys=["date", "symbol", "side"])
    if log.empty:
        return empty
    log = log[log["filled"] == True].copy()  # noqa: E712
    if log.empty:
        return empty
    log["date"] = pd.to_datetime(log["date"])

    minute = store.read_latest("minute_1030", keys=["symbol", "date"])
    if minute.empty:
        return empty
    minute = minute.copy()
    minute["date"] = pd.to_datetime(minute["date"])

    prices = store.read_latest("prices", keys=["symbol", "date"])
    if prices.empty:
        return empty
    prices = prices.copy()
    prices["date"] = pd.to_datetime(prices["date"])

    close = prices.pivot_table(index="date", columns="symbol", values="close").sort_index()
    volume = prices.pivot_table(index="date", columns="symbol", values="volume").sort_index()
    high = prices.pivot_table(index="date", columns="symbol", values="high").sort_index()
    low = prices.pivot_table(index="date", columns="symbol", values="low").sort_index()
    dollar_vol = close * volume
    adv = dollar_vol.rolling(ADV_LOOKBACK, min_periods=10).mean()
    # shift(1): the value on row i becomes the value computed through row
    # i-1, i.e. "as of the trading day strictly before this row's date".
    adv_prev = adv.shift(1)

    merged = log.merge(minute[["symbol", "date", "vwap_1030"]],
                       on=["symbol", "date"], how="left")

    def _lookup(panel: pd.DataFrame, sym, d):
        if sym not in panel.columns or d not in panel.index:
            return np.nan
        return panel.at[d, sym]

    merged["adv_dollars"] = [_lookup(adv_prev, s, d)
                            for s, d in zip(merged["symbol"], merged["date"])]
    merged["high"] = [_lookup(high, s, d)
                      for s, d in zip(merged["symbol"], merged["date"])]
    merged["low"] = [_lookup(low, s, d)
                     for s, d in zip(merged["symbol"], merged["date"])]

    merged = merged.dropna(subset=["vwap_1030", "adv_dollars", "high", "low"])
    if merged.empty:
        return empty

    sign = np.where(merged["side"] == "buy", 1.0, -1.0)
    merged["realized_cost_pct"] = (
        sign * (merged["fill_price"] - merged["vwap_1030"]) / merged["vwap_1030"])
    merged["spread_est"] = [half_spread_pct(h, l)
                            for h, l in zip(merged["high"], merged["low"])]
    merged["sqrt_participation"] = np.sqrt(
        merged["intended_qty"] * merged["fill_price"] / merged["adv_dollars"])

    return merged[INPUT_COLUMNS].reset_index(drop=True)


def fit_cost_params(inputs: pd.DataFrame, cfg: dict) -> dict | None:
    """Fit {spread_scale, slippage_k} via non-negative least squares.

    Returns None (caller keeps the previously active params, or the fixed
    formula) if there are fewer than costs.calibration.min_fills rows —
    the hard gate against fitting on too little data.
    """
    calib_cfg = cfg["costs"]["calibration"]
    if len(inputs) < calib_cfg["min_fills"]:
        return None
    X = inputs[["spread_est", "sqrt_participation"]].to_numpy(dtype=float)
    y = inputs["realized_cost_pct"].to_numpy(dtype=float)
    coef, _residual = nnls(X, y)
    a = float(np.clip(coef[0], *calib_cfg["spread_scale_bounds"]))
    k = float(np.clip(coef[1], *calib_cfg["slippage_k_bounds"]))
    return {"spread_scale": a, "slippage_k": k, "n_samples": len(inputs)}


def walkforward_calibration_schedule(store, cfg: dict,
                                     dates: pd.DatetimeIndex) -> pd.DataFrame:
    """Monthly walk-forward fit schedule, indexed by fold_start.

    `build_calibration_inputs` is built ONCE up front and then filtered
    per fold by `date < fold_start` (not rebuilt per fold) — this is what
    makes the PIT guarantee cheap to verify: a fold's row can only ever
    depend on slippage-log rows strictly before that fold's start.
    """
    inputs = build_calibration_inputs(store, cfg)
    fold_starts = _fold_starts_monthly(dates)

    active: dict | None = None
    rows = []
    for fold_start in fold_starts:
        train = inputs[inputs["date"] < fold_start] if not inputs.empty else inputs
        fit = fit_cost_params(train, cfg)
        if fit is not None:
            active = fit
            rows.append({"fold_start": fold_start, "spread_scale": active["spread_scale"],
                        "slippage_k": active["slippage_k"], "n_samples": active["n_samples"],
                        "source": "fitted"})
        elif active is not None:
            # degenerate refit: keep the previous fold's fitted params
            # rather than reverting to the fixed formula (ml_weights.py's
            # "keep previous weights" rule).
            rows.append({"fold_start": fold_start, "spread_scale": active["spread_scale"],
                        "slippage_k": active["slippage_k"], "n_samples": active["n_samples"],
                        "source": "fixed_fallback"})
        else:
            rows.append({"fold_start": fold_start, "spread_scale": None,
                        "slippage_k": None, "n_samples": len(train),
                        "source": "fixed_fallback"})

    return pd.DataFrame(rows).set_index("fold_start")[SCHEDULE_COLUMNS]


def _fold_starts_monthly(dates: pd.DatetimeIndex) -> list[pd.Timestamp]:
    if len(dates) == 0:
        return []
    start = pd.Timestamp(pd.Timestamp(dates.min()).replace(day=1, hour=0, minute=0,
                                                            second=0, microsecond=0, nanosecond=0))
    end = pd.Timestamp(pd.Timestamp(dates.max()).replace(day=1, hour=0, minute=0,
                                                          second=0, microsecond=0, nanosecond=0))
    starts = []
    cur = start
    while cur <= end:
        starts.append(cur)
        cur = cur + pd.DateOffset(months=1)
    return starts
