"""Learned execution-cost calibration: NNLS fitting behavior, byte-identical
fallback to the fixed cost formula, and — critically — temporal isolation:
corrupting FUTURE paper-trading fills must never change a PAST fold's
fitted calibration parameters. This is the walk-forward/PIT-safety
guarantee tested directly, as a unit test (mirrors tests/test_ml_weights.py's
leakage tripwire for the analogous ML-weights feature).
"""
from __future__ import annotations

import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from daybreak.backtest.cost_calibration import (
    build_calibration_inputs,
    fit_cost_params,
    walkforward_calibration_schedule,
)
from daybreak.backtest.costs import trade_cost
from daybreak.backtest.engine import run_backtest
from daybreak.config import load_config
from daybreak.data.store import PITStore
from daybreak.data.synthetic import stamp

CFG = load_config()
REPO_ROOT = Path(__file__).resolve().parent.parent


def _calib_cfg():
    return {"costs": {"calibration": {
        "min_fills": 100,
        "spread_scale_bounds": [0.25, 4.0],
        "slippage_k_bounds": [0.025, 1.0],
    }}}


def _synthetic_inputs(n, realized_fn, seed) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    spread_est = rng.uniform(0.0002, 0.005, n)
    sqrt_participation = rng.uniform(0.0, 0.3, n)
    return pd.DataFrame({
        "date": pd.bdate_range("2024-01-02", periods=n),
        "symbol": [f"SYN{i % 10:03d}" for i in range(n)],
        "side": ["buy"] * n,
        "realized_cost_pct": realized_fn(spread_est, sqrt_participation, rng),
        "spread_est": spread_est,
        "sqrt_participation": sqrt_participation,
    })


# --- 1: NNLS recovers known parameters --------------------------------------

def test_fit_recovers_known_params():
    a_true, k_true = 2.0, 0.3

    def realized(spread_est, sqrt_participation, rng):
        noise = rng.normal(0, 0.0003, len(spread_est))
        return a_true * spread_est + k_true * sqrt_participation + noise

    inputs = _synthetic_inputs(600, realized, seed=1)
    fit = fit_cost_params(inputs, _calib_cfg())
    assert fit is not None
    assert fit["n_samples"] == 600
    assert abs(fit["spread_scale"] - a_true) < 0.3
    assert abs(fit["slippage_k"] - k_true) < 0.1


# --- 2: hard min_fills gate --------------------------------------------------

def test_below_min_fills_returns_none():
    def realized(spread_est, sqrt_participation, rng):
        return 2.0 * spread_est + 0.3 * sqrt_participation

    inputs = _synthetic_inputs(99, realized, seed=2)  # cfg min_fills=100
    assert fit_cost_params(inputs, _calib_cfg()) is None


# --- 3: calib=None is byte-identical to the pre-existing fixed formula ------

def test_trade_cost_without_calib_is_byte_identical():
    grid = [
        (10_000, 100, "buy", 100.5, 99.5, 1e7),
        (5_000, 50, "sell", 55.2, 54.8, 2e6),
        (250_000, 1000, "buy", 12.0, 11.5, 5e8),
        (0, 0, "buy", 101.0, 99.0, 1e7),
        (1_000, 10, "sell", 30.0, 29.0, 1e5),
        (75_000, 300, "sell", 200.0, 195.0, 1e9),
    ]
    for order_dollars, shares, side, bar_high, bar_low, adv_dollars in grid:
        with_none = trade_cost(order_dollars, shares, side, bar_high, bar_low,
                               adv_dollars, CFG, calib=None)
        without_kw = trade_cost(order_dollars, shares, side, bar_high, bar_low,
                                adv_dollars, CFG)
        assert with_none == without_kw, (order_dollars, shares, side)

    # The pre-existing cost-model test file must also still pass, unmodified,
    # with this feature's changes to trade_cost() in place.
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_costs.py", "-q"],
        cwd=REPO_ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


# --- 4: mean-negative realized cost clips at the configured floor ----------

def test_negative_slippage_sample_clips_at_floor():
    def realized(spread_est, sqrt_participation, rng):
        noise = rng.normal(0, 0.0005, len(spread_est))
        return -0.01 + noise  # price improvement dominates; no positive relation

    inputs = _synthetic_inputs(500, realized, seed=4)
    fit = fit_cost_params(inputs, _calib_cfg())
    assert fit is not None
    bounds = _calib_cfg()["costs"]["calibration"]
    assert abs(fit["spread_scale"] - bounds["spread_scale_bounds"][0]) < 1e-6
    assert abs(fit["slippage_k"] - bounds["slippage_k_bounds"][0]) < 1e-6


# --- 5: dormant (no slippage_log data yet) -> backtest is unchanged --------

def test_backtest_unchanged_when_log_empty(cfg_store):
    from daybreak.regime.state import compute_regime
    from daybreak.signals.compute import compute_signal_panels

    cfg, store = cfg_store
    assert "slippage_log" not in store.tables()

    panels = compute_signal_panels(store, cfg)
    regime = compute_regime(store, cfg)

    cfg_on = deepcopy(cfg)
    cfg_on["costs"]["calibration"]["enabled"] = True
    cfg_off = deepcopy(cfg)
    cfg_off["costs"]["calibration"]["enabled"] = False

    result_on = run_backtest(store, cfg_on, panels, regime)
    result_off = run_backtest(store, cfg_off, panels, regime)
    pd.testing.assert_frame_equal(result_on.daily, result_off.daily)


# --- 6: leakage tripwire ------------------------------------------------

def _build_price_minute_tables(symbols, dates, seed):
    rng = np.random.default_rng(seed)
    n_days = len(dates)
    price_rows, minute_rows = [], []
    for sym in symbols:
        p0 = rng.uniform(20, 200)
        rets = rng.normal(0.0003, 0.01, n_days)
        close = p0 * np.cumprod(1 + rets)
        opens = np.empty(n_days)
        opens[0] = p0
        opens[1:] = close[:-1] * (1 + rng.normal(0, 0.003, n_days - 1))
        high = np.maximum(opens, close) * (1 + np.abs(rng.normal(0, 0.004, n_days)))
        low = np.minimum(opens, close) * (1 - np.abs(rng.normal(0, 0.004, n_days)))
        vol = rng.uniform(1e6, 5e6, n_days).astype(np.int64)
        intraday = close / opens - 1
        vwap = opens * (1 + 0.4 * intraday + rng.normal(0, 0.0015, n_days))
        price_rows.append(pd.DataFrame({
            "date": dates, "symbol": sym, "open": opens, "high": high,
            "low": low, "close": close, "adj_close": close, "volume": vol,
        }))
        minute_rows.append(pd.DataFrame({
            "date": dates, "symbol": sym, "vwap_1030": vwap,
            "volume_1030": (vol * 0.03).astype(np.int64),
        }))
    return pd.concat(price_rows, ignore_index=True), pd.concat(minute_rows, ignore_index=True)


def _build_fills(symbols, dates, minute, seed):
    rng = np.random.default_rng(seed)
    vwap_by = minute.set_index(["symbol", "date"])["vwap_1030"]
    rows = []
    for d in dates:
        for sym in symbols:
            if rng.random() > 0.35:  # not every symbol trades every day
                continue
            vwap = vwap_by.get((sym, d))
            if vwap is None or not np.isfinite(vwap):
                continue
            side = "buy" if rng.random() < 0.5 else "sell"
            sign = 1 if side == "buy" else -1
            base_dev = rng.normal(0.0008, 0.0006)  # realistic small realized slippage
            fill_price = vwap * (1 + sign * base_dev)
            rows.append({
                "date": d, "symbol": sym, "side": side,
                "intended_qty": int(rng.uniform(50, 500)), "filled": True,
                "fill_price": round(float(fill_price), 4),
                "limit_price": round(float(vwap), 4),
                "slippage_vs_limit": round(float(sign * base_dev), 6),
            })
    return pd.DataFrame(rows)


def _corrupt_fills(fills, minute, factor):
    """Multiply each fill's realized slippage vs. vwap_1030 by `factor`,
    keeping date/symbol/side/intended_qty identical — simulates a corrupted
    (e.g. mis-recorded) fill price for these rows."""
    vwap_by = minute.set_index(["symbol", "date"])["vwap_1030"]
    out = fills.copy()
    new_prices = []
    for _, row in out.iterrows():
        vwap = vwap_by.get((row["symbol"], row["date"]))
        sign = 1 if row["side"] == "buy" else -1
        orig_dev = sign * (row["fill_price"] - vwap) / vwap
        new_prices.append(round(float(vwap * (1 + sign * orig_dev * factor)), 4))
    out["fill_price"] = new_prices
    return out


def _make_store(data_dir, prices, minute, fills):
    store = PITStore(data_dir)
    ts = pd.Timestamp("2024-01-01")
    store.append("prices", stamp(prices, ts, ts))
    store.append("minute_1030", stamp(minute, ts, ts))
    if not fills.empty:
        store.append("slippage_log", stamp(fills, ts, ts))
    return store


def test_future_fill_corruption_does_not_change_past_calibration(tmp_path):
    symbols = [f"SYN{i:03d}" for i in range(8)]
    dates = pd.bdate_range("2023-01-02", periods=260)
    prices, minute = _build_price_minute_tables(symbols, dates, seed=11)

    fills_all = _build_fills(symbols, dates, minute, seed=12)
    assert len(fills_all) >= 300  # enough to cross the min_fills=100 gate mid-period

    cutoff_date = dates[int(len(dates) * 0.65)]
    fills_early = fills_all[fills_all["date"] < cutoff_date].reset_index(drop=True)
    fills_late = fills_all[fills_all["date"] >= cutoff_date].reset_index(drop=True)
    fills_late_corrupt = _corrupt_fills(fills_late, minute, factor=50.0)

    fills_b = pd.concat([fills_early, fills_late_corrupt], ignore_index=True)

    store_a = _make_store(tmp_path / "store_a", prices, minute, fills_all)
    store_b = _make_store(tmp_path / "store_b", prices, minute, fills_b)

    cfg = _calib_cfg()
    schedule_a = walkforward_calibration_schedule(store_a, cfg, dates)
    schedule_b = walkforward_calibration_schedule(store_b, cfg, dates)

    safe_cutoff = cutoff_date - pd.Timedelta(days=30)
    safe_folds = schedule_a.index[schedule_a.index <= safe_cutoff]
    assert len(safe_folds) > 0, "test setup should span at least one safe fold"

    cols = ["spread_scale", "slippage_k", "n_samples", "source"]
    pd.testing.assert_frame_equal(schedule_a.loc[safe_folds, cols],
                                  schedule_b.loc[safe_folds, cols])

    # sanity: the tripwire is only meaningful if calibration actually
    # activated (fitted) at some point before the safe cutoff
    assert (schedule_a.loc[safe_folds, "source"] == "fitted").any()

    # and a genuine sanity check that corruption really did perturb inputs
    inputs_a = build_calibration_inputs(store_a, cfg)
    inputs_b = build_calibration_inputs(store_b, cfg)
    late_a = inputs_a[inputs_a["date"] >= cutoff_date]["realized_cost_pct"]
    late_b = inputs_b[inputs_b["date"] >= cutoff_date]["realized_cost_pct"]
    assert not late_a.reset_index(drop=True).equals(late_b.reset_index(drop=True))
