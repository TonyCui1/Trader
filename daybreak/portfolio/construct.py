"""Phase 4 — portfolio construction. Long-only v1.

Order of operations for each decision:
1. Regime OFF -> 100% cash, done.
2. Candidate pool = top decile of composite z, then best `max_positions` names.
3. Earnings blackout: names reporting within the next N trading days cannot be
   NEW positions (existing ones may be held or exited, never added to).
4. Sizing (portfolio.sizing_mode, default "vol_target"):
   - "vol_target": weight proportional to 1/vol60, scaled so estimated
     portfolio vol hits target_vol_annual (single-correlation approximation,
     rho=0.3, documented). This scale moves day to day with recent realized
     vol — it deploys MORE capital specifically when the market has been
     calm, which is why raising target_vol_annual doesn't scale gains and
     losses evenly; it disproportionately amplifies calm stretches.
   - "fixed_fraction": weight proportional to 1/vol60 (for diversification
     across the selected names), scaled to a CONSTANT
     portfolio.fixed_fraction_target regardless of recent volatility — the
     "just deploy a steady chunk of capital" mode. This removes the vol-based
     throttle-down during choppy markets; the regime exposure factor (below)
     and the circuit breaker are the only remaining defenses against a rough
     stretch, so this mode should be validated on real data (does drawdown
     stay acceptable?) before ever going live.
   Both modes are then multiplied by the regime exposure factor.
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

    # 2. hysteresis selection (thermostat gap against boundary churn):
    #    ENTER only from the top `top_fraction`; KEEP a held name until it
    #    falls below the wider `exit_fraction` line. Names drifting between
    #    the two lines are held untouched — noise pays no trading costs.
    entry_cut = scored.quantile(1 - p["top_fraction"])
    exit_cut = scored.quantile(1 - p["exit_fraction"])
    held = [s for s in current[current > 0].index
            if s in scored.index and scored[s] >= exit_cut]

    pool = scored[scored >= entry_cut].sort_values(ascending=False)
    # 3. blackout: strip names that would be NEW positions (holds may stay)
    entries = [s for s in pool.index
               if s not in held and not (s in blackout and current.get(s, 0.0) <= 0)]
    selected = held + entries[:max(0, p["max_positions"] - len(held))]

    if not selected:
        return pd.Series(0.0, index=idx)

    # 4. inverse-vol weights, sized per portfolio.sizing_mode, regime-scaled
    v = vols.reindex(selected)
    v = v.fillna(v.median()).clip(lower=1e-4)
    w = (1 / v) / (1 / v).sum()
    sizing_mode = p.get("sizing_mode", "vol_target")
    if sizing_mode == "fixed_fraction":
        # constant deployed fraction regardless of recent volatility —
        # diversification still comes from inverse-vol relative weights, but
        # the day-to-day calm-vs-choppy throttle is gone (see module docstring)
        scale = min(max(p.get("fixed_fraction_target", 0.9), 0.0), 1.0)
    else:
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
