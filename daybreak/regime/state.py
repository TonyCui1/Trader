"""Phase 3 — regime layer. Three states, few parameters, all stated in advance.

Rules (spec, verbatim — do not add indicators in v1):
- SPY below its 200-day MA                                  -> REDUCED
- SPY below 200-day MA AND HYG/LQD below its own 100-day MA -> OFF
- VIX > 30                                                  -> at least REDUCED

OFF -> 100% cash. REDUCED -> half target exposure.
Row d of the returned series is computed from closes through day d; the engine
uses row t-1 for a decision on day t.
"""
from __future__ import annotations

import pandas as pd

from ..data.store import PITStore

ON, REDUCED, OFF = "ON", "REDUCED", "OFF"
EXPOSURE = {ON: 1.0, REDUCED: 0.5, OFF: 0.0}


def compute_regime(store: PITStore, cfg: dict) -> pd.Series:
    r = cfg["regime"]
    prices = store.read_latest("prices", keys=["symbol", "date"])
    spy = (prices[prices["symbol"] == "SPY"]
           .assign(date=lambda d: pd.to_datetime(d["date"]))
           .set_index("date")["close"].sort_index())
    macro = (store.read_latest("macro", keys=["date"])
             .assign(date=lambda d: pd.to_datetime(d["date"]))
             .set_index("date").sort_index())

    spy_ma = spy.rolling(r["spy_ma_days"], min_periods=1).mean()
    credit = macro["hyg_close"] / macro["lqd_close"]
    credit_ma = credit.rolling(r["credit_ma_days"], min_periods=1).mean()

    idx = spy.index
    below_spy = spy < spy_ma
    below_credit = (credit < credit_ma).reindex(idx).ffill().fillna(False)
    vix_high = (macro["vix"] > r["vix_reduced_threshold"]).reindex(idx).ffill().fillna(False)

    state = pd.Series(ON, index=idx)
    state[below_spy] = REDUCED
    state[below_spy & below_credit] = OFF
    state[(state == ON) & vix_high] = REDUCED
    return state
