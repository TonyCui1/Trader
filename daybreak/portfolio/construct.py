"""Phase 4 — portfolio construction. Long-only v1.

Order of operations for each decision:
1. Regime OFF -> 100% cash, done.
2. Candidate pool = top decile of composite z, then best `max_positions` names.
3. Earnings blackout: names reporting within the next N trading days cannot be
   NEW positions (existing ones may be held or exited, never added to).
4. Vol-targeted sizing: weight proportional to 1/vol60, scaled so estimated
   portfolio vol hits the annual target (single-correlation approximation,
   rho=0.3, documented), then multiplied by the regime exposure factor.
5. Caps: 8% per name, 25% per sector, redistribution passes, excess to cash.
6. Conviction gate: increases require composite z > threshold; every trade
   (in or out) must move the position by more than min_trade_pct of portfolio,
   else HOLD. Exits are NOT z-gated (selling low-z names is the point) — only
   size-gated. This interpretation is documented here deliberately.
7. Turnover brake: if total |trades| exceeds max_daily_turnover, all trades
   scale down proportionally.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..regime.state import EXPOSURE, OFF

RHO = 0.3  # assumed average pairwise correlation for the vol-target estimate


def target_portfolio(composite: pd.Series, vols: pd.Series, sectors: pd.Series,
                     current: pd.Series, regime_state: str,
                     blackout: set[str], cfg: dict) -> pd.Series:
    p = cfg["portfolio"]
    idx = composite.index
    current = current.reindex(idx).fillna(0.0)

    if regime_state == OFF:
        return pd.Series(0.0, index=idx)

    scored = composite.dropna()
    if scored.empty:
        return current  # no information -> no trade (CASH is the default)

    # 2. top decile, then best max_positions
    cutoff = scored.quantile(1 - p["top_fraction"])
    pool = scored[scored >= cutoff].sort_values(ascending=False)
    selected = list(pool.head(p["max_positions"]).index)

    # 3. blackout: strip names that would be NEW positions
    selected = [s for s in selected
                if not (s in blackout and current.get(s, 0.0) <= 0)]

    if not selected:
        return pd.Series(0.0, index=idx)

    # 4. inverse-vol weights, vol-targeted, regime-scaled
    v = vols.reindex(selected)
    v = v.fillna(v.median()).clip(lower=1e-4)
    w = (1 / v) / (1 / v).sum()
    ann = v * np.sqrt(252)
    wsig = (w * ann)
    port_vol = float(np.sqrt((wsig ** 2).sum() + RHO * (wsig.sum() ** 2 - (wsig ** 2).sum())))
    scale = min(p["target_vol_annual"] / max(port_vol, 1e-9), 1.0)  # long-only, no leverage
    w = w * scale * EXPOSURE[regime_state]

    # 5. caps with redistribution passes; residual stays in cash
    sec = sectors.reindex(selected).fillna("Unknown")
    for _ in range(3):
        w = w.clip(upper=p["max_weight_per_name"])
        over = w.groupby(sec).sum()
        for s_name, tot in over[over > p["max_weight_per_sector"]].items():
            in_sec = sec[sec == s_name].index
            w[in_sec] *= p["max_weight_per_sector"] / tot
    target = pd.Series(0.0, index=idx)
    target[w.index] = w.values

    # 3b. blackout names may be held or exited, never added to
    for sym in blackout:
        if sym in target.index:
            target[sym] = min(target[sym], current.get(sym, 0.0))

    # 6. conviction + min-trade gates. The min-trade gate applies to
    # ADJUSTMENTS only: a full exit (target 0) always executes, however small —
    # otherwise sub-threshold dust positions accumulate without bound and the
    # max_positions cap is violated in spirit.
    trades = target - current
    for sym in trades.index:
        t = trades[sym]
        if t == 0:
            continue
        if abs(t) <= p["min_trade_pct"] and target[sym] > 0:
            target[sym] = current[sym]          # small adjustment -> HOLD
        elif t > 0 and (pd.isna(composite.get(sym)) or composite.get(sym, 0) <= p["conviction_z_min"]):
            target[sym] = current[sym]          # increases need conviction

    # 7. turnover brake. Full exits are served FIRST from the turnover budget
    # and never scaled — scaling an exit recreates the dust problem the
    # min-trade exemption above solves. Adjustments share what remains.
    trades = target - current
    exit_mask = (target == 0) & (current > 0)
    exit_turnover = current[exit_mask].sum()
    other = trades[~exit_mask]
    other_turnover = other.abs().sum()
    budget = max(p["max_daily_turnover"] - exit_turnover, 0.0)
    if other_turnover > budget:
        scaled = current[~exit_mask] + other * (budget / other_turnover
                                                if other_turnover > 0 else 0.0)
        target[~exit_mask] = scaled

    return target
