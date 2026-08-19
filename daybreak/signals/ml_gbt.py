"""Optional gradient-boosted-tree signal combination (opt-in via
signals.weighting_mode: gbt_walkforward; ml_walkforward and equal remain
the rollback values — see config.yaml).

Scope, deliberately narrow: this combines the SAME six existing z-scored
signals (momentum, reversal, value, quality, lowvol, pead) that
ml_weights.py combines linearly, but through a shallow gradient-boosted
tree so mild non-linearities and pairwise/triple interactions between the
signals can be expressed. It does not invent new signals, does not use
deep learning, and never sees raw prices as a feature (prices are used
only to build the label).

Label: identical to ml_weights.py — each stock-day's forward return in
excess of that day's cross-sectional mean, isolating stock-selection
skill from market beta (the regime layer handles market timing).

Walk-forward, purged, embargoed (spec Phase 3 ML requirement): identical
discipline to ml_weights.py. Refit periodically (default: annually) using
only data STRICTLY before the fold's start, with an embargo so no
training sample's own forward-return window overlaps the fold boundary. A
fold's fitted model is applied ONLY to dates within that fold — never to
the data used to fit it. Before enough history exists for a first fit,
dates fall back to plain equal weight (explicit, not silently NaN), and
that fallback is numerically identical to what `equal` mode produces.

HYPERPARAMETERS ARE DELIBERATELY HARDCODED MODULE CONSTANTS (GBT_PARAMS
and MIN_FIT_ROWS below). They must NEVER be tuned per fold and must NEVER
be exposed via config. This repo's ML ethos is that zero hyperparameter
search is one less axis of overfitting: the numbers below were chosen
once, for the structural reasons documented inline, and the only thing
config controls is walk-forward SCHEDULING (see signals.gbt in
config.yaml).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

SIGNAL_NAMES = ["momentum", "reversal", "value", "quality", "lowvol", "pead"]

GBT_PARAMS = dict(
    max_depth=3,            # 6 features -> depth 3 captures pairwise/triple interactions
                            # without memorizing stock-day noise
    max_leaf_nodes=8,
    max_iter=50,            # few trees; keeps total complexity near "linear + mild interactions"
    learning_rate=0.05,     # slow shrinkage; 50 * 0.05 keeps the ensemble deliberately under-fit
    min_samples_leaf=200,   # every leaf averages >=200 stock-days
    l2_regularization=1.0,
    max_bins=64,            # features are winsorized z-scores in [-3,3]; coarse bins reduce
                            # split-point noise
    early_stopping=False,   # its internal RANDOM validation split would violate temporal
                            # discipline -- must stay off
    random_state=0,         # bit-reproducibility (decision-log reproducibility requirement)
)
MIN_FIT_ROWS = 5000  # stock-day training rows required before fitting; 10x ml_weights.py's
                     # 500-row NNLS gate because a tree model has far more capacity, and
                     # 5000 = 25x min_samples_leaf so tree structure isn't starved


def fit_walkforward_gbt(z: dict[str, pd.DataFrame], prices: pd.DataFrame,
                        cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (composite panel, importance_log DataFrame[fold_start x signal]).

    z: {signal_name: DataFrame[date x symbol]} of z-scored signals (as
       produced by compute_signal_panels, BEFORE composite averaging).
    prices: adjusted close panel, same index/columns, used only to build
       forward-return labels for fitting — never fed to the model as a
       feature.
    """
    m = cfg["signals"].get("gbt", {})
    horizon = int(m.get("label_horizon_days", 21))
    embargo = int(m.get("embargo_days", horizon))  # must be >= horizon to purge correctly
    min_train_years = float(m.get("min_train_years", 3))
    refit = m.get("refit_frequency", "annual")
    winsor_z = float(cfg["signals"].get("winsor_z", 3.0))

    dates = prices.index
    fwd_ret = prices.shift(-horizon) / prices - 1
    label = fwd_ret.sub(fwd_ret.mean(axis=1), axis=0)  # cross-sectional excess return

    feats = np.dstack([z[n].reindex(index=dates, columns=prices.columns).to_numpy()
                       for n in SIGNAL_NAMES])  # (T, N, 6)

    fold_starts = _fold_starts(dates, refit)
    first_start = dates[0] + pd.DateOffset(years=min_train_years)
    importance_log = []
    composite = pd.DataFrame(np.nan, index=dates, columns=prices.columns)

    equal_imp = np.full(len(SIGNAL_NAMES), 1.0 / len(SIGNAL_NAMES))
    active_model = None
    active_importance = equal_imp
    active_fold_label = "equal_weight_warmup"
    active_n_rows = 0

    label_np = label.to_numpy()

    for i, fold_start in enumerate(fold_starts):
        fold_end = fold_starts[i + 1] if i + 1 < len(fold_starts) else dates[-1] + pd.Timedelta(days=1)
        if fold_start >= first_start:
            cutoff = fold_start - pd.Timedelta(days=embargo)
            train_mask = dates < cutoff
            # purge: a training sample's label needs `horizon` future days
            # fully available before the cutoff; dates < cutoff already
            # guarantees date + horizon <= fold_start - 1 as long as
            # embargo >= horizon (enforced by config), so no extra step needed.
            X_tr = feats[train_mask].reshape(-1, len(SIGNAL_NAMES))
            y_tr = label_np[train_mask].reshape(-1)
            model = _fit_gbt(X_tr, y_tr)
            if model is not None:
                ok = ~np.isnan(y_tr)   # same row filter _fit_gbt applied
                X_ok, y_ok = X_tr[ok], y_tr[ok]
                active_model = model
                active_importance = _permutation_importance(model, X_ok, y_ok)
                active_fold_label = str(fold_start.date())
                active_n_rows = int(len(y_ok))
            # else: keep whatever model was previously active (too little data
            # / degenerate fit) rather than silently going to NaN — exactly the
            # rule ml_weights.py uses for its degenerate NNLS fits.
        in_fold = (dates >= fold_start) & (dates < fold_end)
        fold_feats = feats[in_fold]
        n_avail = (~np.isnan(fold_feats)).sum(axis=2)

        if active_model is None:
            # Warmup: the exact equal-weight NaN-safe mean of the 6 z panels,
            # numerically identical to compute.py's `equal` branch.
            import warnings as _warnings
            with _warnings.catch_warnings():
                _warnings.simplefilter("ignore", category=RuntimeWarning)  # all-NaN slices
                row = np.where(n_avail >= 3, np.nanmean(fold_feats, axis=2), np.nan)
            composite.loc[in_fold] = row
        else:
            fold_dates = dates[in_fold]
            preds = np.full(fold_feats.shape[:2], np.nan)
            for t in range(fold_feats.shape[0]):
                # NaN features are kept: HistGradientBoostingRegressor routes
                # missing values natively, which is exactly why it was chosen.
                preds[t] = active_model.predict(fold_feats[t])
            preds = np.where(n_avail >= 3, preds, np.nan)
            pred_panel = pd.DataFrame(preds, index=fold_dates, columns=prices.columns)
            row = _xsection_z(pred_panel).clip(-winsor_z, winsor_z)
            composite.loc[in_fold] = row.to_numpy()

        importance_log.append({"fold_start": fold_start, "source_fold": active_fold_label,
                               "n_train_rows": active_n_rows,
                               **dict(zip(SIGNAL_NAMES, active_importance))})

    return composite, pd.DataFrame(importance_log).set_index("fold_start")


def _fold_starts(dates: pd.DatetimeIndex, refit: str) -> list[pd.Timestamp]:
    if refit == "annual":
        years = sorted(dates.year.unique())
        return [pd.Timestamp(f"{y}-01-01") for y in years]
    raise ValueError(f"unsupported refit_frequency: {refit!r}")


def _xsection_z(panel: pd.DataFrame) -> pd.DataFrame:
    """Local copy of compute.py's cross-sectional z helper, so this module
    stays standalone (same convention ml_weights.py follows)."""
    mu = panel.mean(axis=1)
    sd = panel.std(axis=1).replace(0, np.nan)
    return panel.sub(mu, axis=0).div(sd, axis=0)


def _fit_gbt(X2: np.ndarray, y2: np.ndarray) -> HistGradientBoostingRegressor | None:
    """X2: (rows, 6) flattened features, y2: (rows,) labels, both already
    time-masked to the training window. Rows with a NaN LABEL are dropped;
    rows with NaN FEATURES are KEPT (HistGradientBoostingRegressor handles
    missing features natively). Returns None — caller keeps the prior
    model — if there's too little data to fit."""
    ok = ~np.isnan(y2)
    X2, y2 = X2[ok], y2[ok]
    if len(y2) < MIN_FIT_ROWS:  # a tree model needs far more rows than NNLS
        return None
    return HistGradientBoostingRegressor(**GBT_PARAMS).fit(X2, y2)


def _permutation_importance(model, X2: np.ndarray, y2: np.ndarray) -> np.ndarray:
    """Per-signal permutation importance, shape (6,), non-negative, summing
    to 1. Fully deterministic: fixed-seed subsample and fixed-seed column
    shuffles drawn sequentially from one rng, so the transparency log is
    reproducible (decision-log reproducibility requirement)."""
    rng = np.random.default_rng(0)
    n = len(y2)
    if n > 50_000:
        idx = rng.choice(n, size=50_000, replace=False)
        X_sub, y_sub = X2[idx], y2[idx]
    else:
        X_sub, y_sub = X2, y2

    mse_base = float(np.mean((model.predict(X_sub) - y_sub) ** 2))

    deltas = np.zeros(len(SIGNAL_NAMES))
    for j in range(len(SIGNAL_NAMES)):
        X_perm = X_sub.copy()
        X_perm[:, j] = X_sub[rng.permutation(len(X_sub)), j]
        mse_perm = float(np.mean((model.predict(X_perm) - y_sub) ** 2))
        deltas[j] = max(mse_perm - mse_base, 0.0)

    total = deltas.sum()
    if total > 0:
        return deltas / total
    return np.full(len(SIGNAL_NAMES), 1.0 / len(SIGNAL_NAMES))  # degenerate -> equal
