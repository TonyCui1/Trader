"""ML-fitted signal weights: NNLS fitting behavior and, critically, temporal
isolation — corrupting FUTURE data must never change a PAST fold's fitted
weights or composite values. This is the walk-forward/purge/embargo
guarantee tested directly, as a unit test, rather than only indirectly via
the backtest-level lag test.
"""
from __future__ import annotations

from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from daybreak.signals.compute import SIGNAL_NAMES, compute_signal_panels
from daybreak.signals.ml_weights import _fit_nnls, fit_walkforward_composite


def _synthetic_zpanels(n_days=900, n_syms=40, seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2020-01-02", periods=n_days)
    syms = [f"X{i}" for i in range(n_syms)]
    z = {name: pd.DataFrame(rng.normal(0, 1, (n_days, n_syms)), index=dates, columns=syms)
         for name in SIGNAL_NAMES}
    prices = pd.DataFrame(100 * np.cumprod(1 + rng.normal(0.0003, 0.01, (n_days, n_syms)), axis=0),
                          index=dates, columns=syms)
    return z, prices


def test_nnls_favors_the_informative_feature():
    rng = np.random.default_rng(1)
    n = 2000
    X = np.zeros((n, 1, 6))
    X[:, 0, 0] = rng.normal(0, 1, n)          # informative: momentum-like
    X[:, 0, 1:] = rng.normal(0, 1, (n, 5))    # pure noise
    y = (X[:, 0, 0] * 0.05 + rng.normal(0, 1, n))[:, None]  # label correlated with feature 0 only
    w = _fit_nnls(X, y)
    assert w is not None
    assert w[0] == max(w)          # informative feature gets the most weight
    assert np.isclose(w.sum(), 1.0)
    assert (w >= 0).all()          # non-negative by construction


def test_nnls_returns_none_below_min_sample_size():
    rng = np.random.default_rng(2)
    X = rng.normal(0, 1, (10, 1, 6))
    y = rng.normal(0, 1, (10, 1))
    assert _fit_nnls(X, y) is None


def test_warmup_period_uses_equal_weight():
    z, prices = _synthetic_zpanels()
    cfg = {"signals": {"ml": {"min_train_years": 2, "refit_frequency": "annual"}}}
    composite, weight_log = fit_walkforward_composite(z, prices, cfg)
    first_fold = weight_log.iloc[0]
    assert first_fold["source_fold"] == "equal_weight_warmup"
    for name in SIGNAL_NAMES:
        assert np.isclose(first_fold[name], 1 / len(SIGNAL_NAMES))


def test_fold_weights_sum_to_one():
    z, prices = _synthetic_zpanels()
    cfg = {"signals": {"ml": {"min_train_years": 1, "refit_frequency": "annual"}}}
    _, weight_log = fit_walkforward_composite(z, prices, cfg)
    sums = weight_log[SIGNAL_NAMES].sum(axis=1)
    assert np.allclose(sums, 1.0)


def test_future_corruption_does_not_change_past_weights_or_composite():
    """The core leakage tripwire for this module: corrupt only the LAST
    stretch of data, refit, and verify every fold whose fit window is
    strictly before the corrupted region is byte-identical to the
    uncorrupted run — proving the purge/embargo boundary actually holds."""
    z, prices = _synthetic_zpanels(n_days=1200, seed=3)
    cfg = {"signals": {"ml": {"min_train_years": 2, "refit_frequency": "annual",
                              "label_horizon_days": 21, "embargo_days": 21}}}
    composite_a, weights_a = fit_walkforward_composite(z, prices, cfg)

    corrupt_from = prices.index[-200]  # corrupt only the final ~200 days
    rng = np.random.default_rng(99)
    z2 = {}
    for name, panel in z.items():
        p2 = panel.copy()
        mask = p2.index >= corrupt_from
        p2.loc[mask] = rng.normal(0, 5, (mask.sum(), p2.shape[1]))
        z2[name] = p2
    prices2 = prices.copy()
    prices2.loc[prices2.index >= corrupt_from] = (
        prices2.loc[prices2.index >= corrupt_from] * (1 + rng.normal(0, 0.2, prices2.loc[prices2.index >= corrupt_from].shape)))

    composite_b, weights_b = fit_walkforward_composite(z2, prices2, cfg)

    safe_folds = weights_a.index[weights_a.index < corrupt_from - pd.Timedelta(days=30)]
    pd.testing.assert_frame_equal(weights_a.loc[safe_folds], weights_b.loc[safe_folds])

    safe_dates = composite_a.index[composite_a.index < corrupt_from - pd.Timedelta(days=30)]
    pd.testing.assert_frame_equal(composite_a.loc[safe_dates], composite_b.loc[safe_dates])


def test_ml_walkforward_runs_end_to_end_on_fixture(cfg_store):
    cfg, store = cfg_store
    cfg = deepcopy(cfg)
    cfg["signals"]["weighting_mode"] = "ml_walkforward"
    cfg["signals"].setdefault("ml", {})
    panels = compute_signal_panels(store, cfg)
    assert "composite" in panels
    assert "_ml_weights" in panels
    assert set(panels["_ml_weights"].columns) >= set(SIGNAL_NAMES) | {"source_fold"}


def test_equal_mode_is_unaffected_by_ml_module_existing(cfg_store):
    """Confirms the ml_weights.py module coexists with the plain equal-weight
    path (used as a rollback value) without adding ml-only outputs to it."""
    cfg, store = cfg_store
    cfg = deepcopy(cfg)
    cfg["signals"]["weighting_mode"] = "equal"
    panels = compute_signal_panels(store, cfg)
    assert "_ml_weights" not in panels
