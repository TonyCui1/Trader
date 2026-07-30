"""Phase 2 — signal computation. Exactly six signals, no more (spec cap: 10).

Every panel is indexed by "as-of close date": row d is computable using only
data available by day d's close (price shifts see closes through d; fundamental
joins are as-of on ingested_at, which already carries the 90-day fiscal lag).
The engine enforces the T-1 rule by reading row t-1 for a decision on day t.

Composite = equal-weight average of z-scores. Weights are NOT fitted in v1
(spec: equal weights cannot be overfit).

PEAD uses a seasonal-random-walk SUE (YoY EPS change over the std of trailing
YoY changes) — actuals only, no consensus estimates, keeping it PIT-safe
(PLAN.md gap 3).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..data.store import PITStore

SIGNAL_NAMES = ["momentum", "reversal", "value", "quality", "lowvol", "pead"]


def compute_signal_panels(store: PITStore, cfg: dict) -> dict[str, pd.DataFrame]:
    """Returns {signal_name: DataFrame[date x symbol]} of winsorized z-scores,
    plus 'composite'."""
    s = cfg["signals"]
    prices = store.read_latest("prices", keys=["symbol", "date"])
    prices = prices[prices["symbol"] != "SPY"]
    prices["date"] = pd.to_datetime(prices["date"])
    P = prices.pivot_table(index="date", columns="symbol", values="adj_close").sort_index()
    rets = P.pct_change(fill_method=None)

    from ..data.universe import latest_security_master
    master = latest_security_master(store)
    sector_of = dict(zip(master["symbol"], master["sector"]))
    sectors = pd.Series({c: sector_of.get(c, "Unknown") for c in P.columns})

    raw: dict[str, pd.DataFrame] = {}
    raw["momentum"] = P.shift(s["momentum"]["skip_days"]) / P.shift(s["momentum"]["lookback_days"]) - 1
    raw["reversal"] = -(P / P.shift(s["reversal"]["lookback_days"]) - 1)
    raw["lowvol"] = -rets.rolling(s["lowvol"]["lookback_days"]).std()

    fund = _fundamental_panels(store, cfg, P)
    raw["value"] = fund["value"]      # sector-neutralized inside
    raw["quality"] = fund["quality"]
    raw["pead"] = fund["pead"]

    w = s["winsor_z"]
    z: dict[str, pd.DataFrame] = {}
    for name in SIGNAL_NAMES:
        panel = raw[name]
        if name == "value":
            z[name] = panel.clip(-w, w)  # already sector-neutral z
        else:
            z[name] = _xsection_z(panel).clip(-w, w)

    stack = np.dstack([z[n].reindex(index=P.index, columns=P.columns).to_numpy()
                       for n in SIGNAL_NAMES])
    n_avail = (~np.isnan(stack)).sum(axis=2)
    import warnings as _warnings
    with _warnings.catch_warnings():
        _warnings.simplefilter("ignore", category=RuntimeWarning)  # all-NaN slices
        composite = np.where(n_avail >= 3, np.nanmean(stack, axis=2), np.nan)
    z["composite"] = pd.DataFrame(composite, index=P.index, columns=P.columns)
    z["_sectors"] = sectors.to_frame("sector")
    return z


def _xsection_z(panel: pd.DataFrame) -> pd.DataFrame:
    mu = panel.mean(axis=1)
    sd = panel.std(axis=1).replace(0, np.nan)
    return panel.sub(mu, axis=0).div(sd, axis=0)


def _sector_neutral_z(panel: pd.DataFrame, sectors: pd.Series) -> pd.DataFrame:
    out = pd.DataFrame(np.nan, index=panel.index, columns=panel.columns)
    for sec in sectors.unique():
        cols = sectors[sectors == sec].index.intersection(panel.columns)
        if len(cols) < 3:
            continue
        sub = panel[cols]
        mu = sub.mean(axis=1)
        sd = sub.std(axis=1).replace(0, np.nan)
        out[cols] = sub.sub(mu, axis=0).div(sd, axis=0)
    return out


def _fundamental_panels(store: PITStore, cfg: dict, P: pd.DataFrame
                        ) -> dict[str, pd.DataFrame]:
    """Daily value/quality/pead panels from quarterly fundamentals via as-of
    joins on ingested_at (which embeds the 90-day fiscal lag)."""
    from ..data.universe import latest_security_master
    s = cfg["signals"]
    f = store.read("fundamentals")
    master = latest_security_master(store)
    sector_of = dict(zip(master["symbol"], master["sector"]))
    dates = P.index
    nan_panel = pd.DataFrame(np.nan, index=dates, columns=P.columns)
    if f.empty:
        return {"value": nan_panel, "quality": nan_panel.copy(), "pead": nan_panel.copy()}

    f = f.copy()
    f["fiscal_end"] = pd.to_datetime(f["fiscal_end"])
    f["available"] = pd.to_datetime(f["ingested_at"])
    f = f.sort_values(["symbol", "fiscal_end"])

    g = f.groupby("symbol", group_keys=False)
    f["ttm_ni"] = g["net_income"].rolling(4, min_periods=4).sum().reset_index(level=0, drop=True)
    f["ttm_fcf"] = g["fcf"].rolling(4, min_periods=4).sum().reset_index(level=0, drop=True)
    f["gp_assets"] = f["gross_profit"] / f["total_assets"]
    f["accruals"] = (f["net_income"] - f["fcf"]) / f["total_assets"]
    f["eps_yoy"] = g["eps"].diff(4)
    lb = s["pead"]["sue_lookback_quarters"]
    f["sue"] = f["eps_yoy"] / g["eps_yoy"].rolling(lb, min_periods=4).std().reset_index(level=0, drop=True)

    cols = ["ttm_ni", "ttm_fcf", "gp_assets", "accruals", "sue", "shares"]
    daily = {c: pd.DataFrame(np.nan, index=dates, columns=P.columns) for c in cols}
    event_date = pd.DataFrame(pd.NaT, index=dates, columns=P.columns)

    for sym, grp in f.groupby("symbol"):
        if sym not in P.columns:
            continue
        grp = grp.sort_values("available")
        merged = pd.merge_asof(
            pd.DataFrame({"date": dates}), grp[["available"] + cols],
            left_on="date", right_on="available", direction="backward")
        for c in cols:
            daily[c][sym] = merged[c].to_numpy()
        event_date[sym] = merged["available"].to_numpy()

    mcap = P * daily["shares"]
    ey = daily["ttm_ni"] / mcap
    fy = daily["ttm_fcf"] / mcap
    sectors = pd.Series({c: sector_of.get(c, "Unknown") for c in P.columns})
    value = (_sector_neutral_z(ey, sectors) + _sector_neutral_z(fy, sectors)) / 2

    quality = _xsection_z(daily["gp_assets"]) - _xsection_z(daily["accruals"])

    # PEAD: SUE decays linearly to 0 over decay_days trading days post-event.
    decay_days = s["pead"]["decay_days"]
    day_num = pd.Series(np.arange(len(dates)), index=dates)
    event_num = event_date.apply(lambda col: col.map(
        lambda d: day_num.get(pd.Timestamp(d), np.nan) if pd.notna(d)
        else np.nan))
    # events landing on non-trading days: map to next trading day index
    for sym in event_num.columns:
        nums = event_num[sym]
        missing = nums.isna() & event_date[sym].notna()
        if missing.any():
            ed = pd.to_datetime(event_date[sym][missing])
            event_num.loc[missing, sym] = ed.map(
                lambda d: float(day_num[day_num.index >= d].iloc[0])
                if (day_num.index >= d).any() else np.nan)
    age = pd.DataFrame(day_num.to_numpy()[:, None] - event_num.to_numpy(),
                       index=dates, columns=P.columns)
    decay = (1 - age / decay_days).clip(lower=0)
    pead = daily["sue"] * decay

    return {"value": value, "quality": quality, "pead": pead}


def lag_panels(panels: dict[str, pd.DataFrame], extra_days: int) -> dict[str, pd.DataFrame]:
    """Shift every signal panel by extra trading days (validation lag test)."""
    out = {}
    for k, v in panels.items():
        out[k] = v if k.startswith("_") else v.shift(extra_days)
    return out
