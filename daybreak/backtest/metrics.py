"""Performance metrics (spec Phase 5). All numbers are net — the engine only
ever hands this module net equity. Includes the short-term-gains tax estimate
at the configurable rate.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

ANN = 252


def compute_metrics(daily: pd.DataFrame, trades: pd.DataFrame, cfg: dict) -> dict:
    eq = daily["equity"]
    rf = daily["rf_daily"] if "rf_daily" in daily else 0.0
    rets = eq.pct_change().fillna(0.0)
    excess = rets - rf

    sd = excess.std()
    downside = excess[excess < 0].std()
    running_max = eq.cummax()
    dd = (eq / running_max - 1).min()

    realized = trades["realized_gain"].sum() if "realized_gain" in trades else 0.0
    tax = max(0.0, realized) * cfg["costs"]["tax_rate_short_term"]

    total_costs = float(trades["cost"].sum()) if not trades.empty else 0.0
    gross_gain = float(eq.iloc[-1] - eq.iloc[0] + total_costs)
    years = max(len(eq) / ANN, 1e-9)

    return {
        "start": str(daily.index[0].date()), "end": str(daily.index[-1].date()),
        "days": len(daily),
        "net_total_return": round(float(eq.iloc[-1] / eq.iloc[0] - 1), 4),
        "net_annual_return": round(float((eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1), 4),
        "net_sharpe": round(float(excess.mean() / sd * np.sqrt(ANN)) if sd > 0 else 0.0, 3),
        "net_sortino": round(float(excess.mean() / downside * np.sqrt(ANN))
                             if downside and downside > 0 else 0.0, 3),
        "max_drawdown": round(float(dd), 4),
        "avg_daily_turnover": round(float(daily["turnover"].mean()), 4),
        "pct_days_in_cash": round(float((daily["n_positions"] == 0).mean()), 4),
        "total_costs": round(total_costs, 2),
        "costs_pct_of_gross_gain": (round(total_costs / abs(gross_gain), 4)
                                    if abs(gross_gain) > 1e-9 else None),
        "realized_st_gains": round(float(realized), 2),
        "tax_estimate": round(float(tax), 2),
        "net_total_return_after_tax": round(
            float((eq.iloc[-1] - tax) / eq.iloc[0] - 1), 4),
    }


def per_year(daily: pd.DataFrame) -> pd.DataFrame:
    eq = daily["equity"]
    out = []
    for year, grp in eq.groupby(eq.index.year):
        r = grp.pct_change().fillna(0.0)
        out.append({"year": year,
                    "net_return": round(float(grp.iloc[-1] / grp.iloc[0] - 1), 4),
                    "sharpe": round(float(r.mean() / r.std() * np.sqrt(ANN))
                                    if r.std() > 0 else 0.0, 2),
                    "pnl": round(float(grp.iloc[-1] - grp.iloc[0]), 2)})
    return pd.DataFrame(out)


def per_regime(daily: pd.DataFrame) -> pd.DataFrame:
    rets = daily["equity"].pct_change().fillna(0.0)
    out = []
    for state, grp in rets.groupby(daily["regime"]):
        out.append({"regime": state, "days": len(grp),
                    "ann_return": round(float(grp.mean() * ANN), 4),
                    "sharpe": round(float(grp.mean() / grp.std() * np.sqrt(ANN))
                                    if grp.std() > 0 else 0.0, 2)})
    return pd.DataFrame(out)
