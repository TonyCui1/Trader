"""Optional ML-fitted signal weights (opt-in; equal-weight remains the
validated live default — see signals.weighting_mode in config.yaml).

Scope, deliberately narrow (spec: "simple models before deep ones"):
this fits ONE non-negative weight per existing signal (6 numbers total),
not a general model over raw features. It does not invent new signals,
does not use deep learning, and cannot assign a signal negative weight
(a signal working backwards would mean its underlying rationale is
wrong, which should be investigated, not silently flipped).

Method: non-negative least squares (scipy.optimize.nnls) regressing each
stock-day's forward return (in excess of that day's cross-sectional mean,
to isolate stock-selection skill from pure market beta — the regime layer
already handles market-wide timing separately) on the six z-scored
signals. Zero hyperparameters to tune, which itself removes a whole axis
of overfitting risk.

Walk-forward, purged, embargoed (spec Phase 3 ML requirement):
refit periodically (default: annually) using only data that is STRICTLY
before the fold's start, with an embargo so no training sample's own
forward-return window overlaps the fold boundary. A fold's fitted
weights are then applied ONLY to dates within that fold — never to the
data used to fit them. Before enough history exists for a first fit,
dates fall back to equal weight (explicit, not silently NaN).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import nnls

SIGNAL_NAMES = ["momentum", "reversal", "value", "quality", "lowvol", "pead"]


def fit_walkforward_composite(z: dict[str, pd.DataFrame], prices: pd.DataFrame,
                              cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (composite panel, weights_used DataFrame[fold_start x signal]).

    z: {signal_name: DataFrame[date x symbol]} of z-scored signals (as
       produced by compute_signal_panels, BEFORE composite averaging).
    prices: adjusted close panel, same index/columns, used only to build
       forward-return labels for fitting — never fed to the model as a
       feature.
    """
    m = cfg["signals"].get("ml", {})
    horizon = int(m.get("label_horizon_days", 21))
    embargo = int(m.get("embargo_days", horizon))  # must be >= horizon to purge correctly
    min_train_years = float(m.get("min_train_years", 2))
    refit = m.get("refit_frequency", "annual")

    dates = prices.index
    fwd_ret = prices.shift(-horizon) / prices - 1
    label = fwd_ret.sub(fwd_ret.mean(axis=1), axis=0)  # cross-sectional excess return

    feats = np.dstack([z[n].reindex(index=dates, columns=prices.columns).to_numpy()
                       for n in SIGNAL_NAMES])  # (T, N, 6)

    fold_starts = _fold_starts(dates, refit)
    first_start = dates[0] + pd.DateOffset(years=min_train_years)
    weight_log = []
    composite = pd.DataFrame(np.nan, index=dates, columns=prices.columns)

    equal_w = np.full(len(SIGNAL_NAMES), 1.0 / len(SIGNAL_NAMES))
    active_w = equal_w
    active_fold_label = "equal_weight_warmup"

    for i, fold_start in enumerate(fold_starts):
        fold_end = fold_starts[i + 1] if i + 1 < len(fold_starts) else dates[-1] + pd.Timedelta(days=1)
        if fold_start >= first_start:
            cutoff = fold_start - pd.Timedelta(days=embargo)
            train_mask = dates < cutoff
            # purge: a training sample's label needs `horizon` future days
            # fully available before the cutoff, i.e. before dates < cutoff
            # already guarantees date + horizon <= fold_start - 1 as long as
            # embargo >= horizon (enforced above), so no extra step needed.
            w = _fit_nnls(feats[train_mask], label.to_numpy()[train_mask])
            if w is not None:
                active_w, active_fold_label = w, str(fold_start.date())
            # else: keep the previous fold's weights (degenerate fit — too
            # little variance to solve) rather than silently going to NaN
        in_fold = (dates >= fold_start) & (dates < fold_end)
        fold_feats = feats[in_fold]
        avail = ~np.isnan(fold_feats)
        n_avail = avail.sum(axis=2)
        # weighted average over available signals only (NaN-safe, mirrors
        # the equal-weight version's nanmean): sum(value*weight)/sum(weight)
        # for whichever signals are present that day.
        weighted = np.nansum(fold_feats * active_w, axis=2)
        denom = (avail * active_w).sum(axis=2)
        with np.errstate(invalid="ignore", divide="ignore"):
            row = np.where((denom > 0) & (n_avail >= 3), weighted / denom, np.nan)
        composite.loc[in_fold] = row
        weight_log.append({"fold_start": fold_start, "source_fold": active_fold_label,
                           **dict(zip(SIGNAL_NAMES, active_w))})

    return composite, pd.DataFrame(weight_log).set_index("fold_start")


def _fold_starts(dates: pd.DatetimeIndex, refit: str) -> list[pd.Timestamp]:
    if refit == "annual":
        years = sorted(dates.year.unique())
        return [pd.Timestamp(f"{y}-01-01") for y in years]
    raise ValueError(f"unsupported refit_frequency: {refit!r}")


def _fit_nnls(X: np.ndarray, y: np.ndarray) -> np.ndarray | None:
    """X: (T, N, 6) feature cube, y: (T, N) labels, both already time-masked
    to the training window. Stacks into a plain (rows, 6) regression,
    dropping any row with a NaN feature or label. Returns None (caller
    keeps prior weights) if there's too little data to fit."""
    X2 = X.reshape(-1, X.shape[-1])
    y2 = y.reshape(-1)
    ok = ~(np.isnan(X2).any(axis=1) | np.isnan(y2))
    X2, y2 = X2[ok], y2[ok]
    if len(y2) < 500:  # not enough stock-days for a stable 6-parameter fit
        return None
    coef, _residual = nnls(X2, y2)
    if coef.sum() <= 0:
        return None  # degenerate: no signal had a positive fit; keep prior weights
    return coef / coef.sum()
