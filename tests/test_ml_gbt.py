"""Gradient-boosted-tree signal combination: fitting behavior, the
equal-weight warmup fallback, determinism, and — critically — temporal
isolation. Corrupting FUTURE data must never change a PAST fold's fitted
model or composite values. That is the walk-forward/purge/embargo
guarantee tested directly as a unit test, mirroring
tests/test_ml_weights.py's tripwire for the linear NNLS variant.
"""
from __future__ import annotations

from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from daybreak.signals.compute import SIGNAL_NAMES, compute_signal_panels
from daybreak.signals.ml_gbt import (MIN_FIT_ROWS, _fit_gbt,
                                     fit_walkforward_gbt)


# Local copy of test_ml_weights.py's generator (that file copies rather than
# imports too — these test modules stay standalone). Defaults are bigger here:
# MIN_FIT_ROWS is 5000 (10x the NNLS gate) and min_train_years defaults to 3,
# so a fold only actually FITS once >3 calendar years precede it AND the
# pre-cutoff window holds 5000+ stock-days. 1400 business days (~5.4y, into
# 2025) x 60 symbols clears both with room to spare.
def _synthetic_zpanels(n_days=1400, n_syms=60, seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2020-01-02", periods=n_days)
    syms = [f"X{i}" for i in range(n_syms)]
    z = {name: pd.DataFrame(rng.normal(0, 1, (n_days, n_syms)), index=dates, columns=syms)
         for name in SIGNAL_NAMES}
    prices = pd.DataFrame(100 * np.cumprod(1 + rng.normal(0.0003, 0.01, (n_days, n_syms)), axis=0),
                          index=dates, columns=syms)
    return z, prices


def _equal_weight_composite(z, prices):
    """Exactly compute.py's `equal` branch: NaN-safe mean of the 6 z panels,
    NaN wherever fewer than 3 signals were available."""
    stack = np.dstack([z[n].reindex(index=prices.index, columns=prices.columns).to_numpy()
                       for n in SIGNAL_NAMES])
    n_avail = (~np.isnan(stack)).sum(axis=2)
    import warnings as _warnings
    with _warnings.catch_warnings():
        _warnings.simplefilter("ignore", category=RuntimeWarning)
        comp = np.where(n_avail >= 3, np.nanmean(stack, axis=2), np.nan)
    return pd.DataFrame(comp, index=prices.index, columns=prices.columns)


def _cfg(**gbt):
    base = {"label_horizon_days": 21, "embargo_days": 21,
            "min_train_years": 3, "refit_frequency": "annual"}
    base.update(gbt)
    return {"signals": {"winsor_z": 3.0, "gbt": base}}


def test_gbt_learns_nonlinear_interaction():
    """The whole reason for this module: the label switches x0 on and off
    depending on the sign of x1. A linear/NNLS fit can only ever express a
    fixed multiple of x0 and so cannot represent the gating; the tree
    should recover the gated component itself."""
    rng = np.random.default_rng(1)
    n = 30_000
    X = rng.normal(0, 1, (n, 6))
    interaction = X[:, 0] * (X[:, 1] > 0)
    y = 0.05 * interaction + rng.normal(0, 0.02, n)

    model = _fit_gbt(X, y)
    assert model is not None
    pred = model.predict(X)
    assert np.corrcoef(pred, interaction)[0, 1] > 0.1


def test_below_min_rows_returns_none():
    rng = np.random.default_rng(2)
    n = MIN_FIT_ROWS - 1
    X = rng.normal(0, 1, (n, 6))
    y = rng.normal(0, 1, n)
    assert _fit_gbt(X, y) is None


def test_warmup_period_uses_equal_weight():
    # ~2 years of data with min_train_years=3 -> no fold ever fits.
    z, prices = _synthetic_zpanels(n_days=500, n_syms=30, seed=5)
    # punch some holes so the n_avail >= 3 gate is actually exercised
    for name in SIGNAL_NAMES[:4]:
        z[name].iloc[10:20, :5] = np.nan

    composite, log = fit_walkforward_gbt(z, prices, _cfg())
    first_fold = log.iloc[0]
    assert first_fold["source_fold"] == "equal_weight_warmup"
    assert first_fold["n_train_rows"] == 0
    assert (log["source_fold"] == "equal_weight_warmup").all()

    expected = _equal_weight_composite(z, prices)
    pd.testing.assert_frame_equal(composite, expected)


def test_fitted_fold_composite_is_zscored():
    z, prices = _synthetic_zpanels()
    composite, log = fit_walkforward_gbt(z, prices, _cfg())
    fitted = log[log["source_fold"] != "equal_weight_warmup"]
    assert not fitted.empty, "expected at least one fold to actually fit"
    assert (fitted["n_train_rows"] >= MIN_FIT_ROWS).all()

    fold_start = fitted.index[0]
    rows = composite.loc[composite.index >= fold_start]
    means = rows.mean(axis=1).dropna()
    assert len(means) > 0
    # Cross-sectional z-scoring puts the mean at exactly 0 (~1e-17 on most
    # dates); the residual comes from the winsor clip afterwards — shaving a
    # single +5 outlier back to +3 across 60 symbols moves the mean by up to
    # ~2/60 = 0.033. So: zero up to clipping, not merely "small".
    assert np.allclose(means, 0.0, atol=0.05)
    unclipped = rows[(rows.abs() < 3.0 - 1e-9).all(axis=1)]
    assert len(unclipped) > 0
    assert np.allclose(unclipped.mean(axis=1), 0.0, atol=1e-10)
    vals = rows.to_numpy()
    vals = vals[~np.isnan(vals)]
    assert (vals >= -3.0 - 1e-9).all() and (vals <= 3.0 + 1e-9).all()


def test_importance_nonnegative_sums_to_one():
    z, prices = _synthetic_zpanels()
    _, log = fit_walkforward_gbt(z, prices, _cfg())
    fitted = log[log["source_fold"] != "equal_weight_warmup"]
    assert not fitted.empty
    imp = fitted[SIGNAL_NAMES]
    assert (imp.to_numpy() >= 0).all()
    assert np.allclose(imp.sum(axis=1), 1.0)


def test_fit_is_deterministic():
    rng = np.random.default_rng(7)
    X = rng.normal(0, 1, (MIN_FIT_ROWS * 2, 6))
    y = 0.03 * X[:, 0] * (X[:, 2] > 0) + rng.normal(0, 0.02, len(X))
    holdout = rng.normal(0, 1, (500, 6))
    m1 = _fit_gbt(X.copy(), y.copy())
    m2 = _fit_gbt(X.copy(), y.copy())
    assert m1 is not None and m2 is not None
    assert np.array_equal(m1.predict(holdout), m2.predict(holdout))


def test_future_corruption_does_not_change_past_models_or_composite():
    """The core leakage tripwire for this module: corrupt only the LAST
    stretch of data, rerun, and verify every fold whose fit window is
    strictly before the corrupted region is byte-identical to the
    uncorrupted run — proving the purge/embargo boundary actually holds.

    Sized deliberately larger than the ml_weights.py tripwire: MIN_FIT_ROWS
    is 5000 (vs its 500), so we need 60 symbols over 1400 business days for
    a real (non-warmup) fit to land WELL BEFORE the corruption point —
    otherwise every 'safe' fold would be a trivial equal-weight warmup fold
    and the test would prove nothing about the fitted path.
    """
    z, prices = _synthetic_zpanels(n_days=1400, n_syms=60, seed=3)
    cfg = _cfg()
    composite_a, log_a = fit_walkforward_gbt(z, prices, cfg)

    corrupt_from = prices.index[-200]  # corrupt only the final ~200 days
    rng = np.random.default_rng(99)
    z2 = {}
    for name, panel in z.items():
        p2 = panel.copy()
        mask = p2.index >= corrupt_from
        p2.loc[mask] = rng.normal(0, 5, (mask.sum(), p2.shape[1]))
        z2[name] = p2
    prices2 = prices.copy()
    tail = prices2.index >= corrupt_from
    prices2.loc[tail] = prices2.loc[tail] * (1 + rng.normal(0, 0.2, prices2.loc[tail].shape))

    composite_b, log_b = fit_walkforward_gbt(z2, prices2, cfg)

    safe_folds = log_a.index[log_a.index < corrupt_from - pd.Timedelta(days=30)]
    # the tripwire is only meaningful if a genuinely FITTED fold is in scope
    assert (log_a.loc[safe_folds, "source_fold"] != "equal_weight_warmup").any()
    pd.testing.assert_frame_equal(log_a.loc[safe_folds], log_b.loc[safe_folds])

    safe_dates = composite_a.index[composite_a.index < corrupt_from - pd.Timedelta(days=30)]
    pd.testing.assert_frame_equal(composite_a.loc[safe_dates], composite_b.loc[safe_dates])


def test_gbt_runs_end_to_end_on_fixture(cfg_store):
    """Plumbing check. The shared fixture spans only ~2 years, so with
    min_train_years=3 this lands in warmup/equal-weight territory — that is
    fine and expected; what's under test is that the compute.py branch runs
    and emits the transparency log."""
    cfg, store = cfg_store
    cfg = deepcopy(cfg)
    cfg["signals"]["weighting_mode"] = "gbt_walkforward"
    cfg["signals"].setdefault("gbt", {})
    panels = compute_signal_panels(store, cfg)
    assert "composite" in panels
    assert "_gbt_importance" in panels
    assert set(panels["_gbt_importance"].columns) >= set(SIGNAL_NAMES) | {"source_fold"}


def test_other_modes_unaffected(cfg_store):
    """The new branch must not leak into the rollback paths."""
    cfg, store = cfg_store
    for mode in ("equal", "ml_walkforward"):
        c = deepcopy(cfg)
        c["signals"]["weighting_mode"] = mode
        c["signals"].setdefault("ml", {})
        panels = compute_signal_panels(store, c)
        assert "composite" in panels
        assert "_gbt_importance" not in panels
