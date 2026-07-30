"""Leakage tripwire (spec testing requirements, CI-mandatory):

A synthetic strategy with a KNOWN future leak — the composite score for day t
is day t's own return, i.e. perfect foresight of the fill day — must be caught
by the lag test: its Sharpe collapses when inputs are lagged one extra day.
If this test ever fails, the validation harness can no longer detect leaks.
"""
import numpy as np
import pandas as pd
import pytest

from daybreak.backtest.engine import run_backtest
from daybreak.regime.state import compute_regime
from daybreak.signals.compute import compute_signal_panels


@pytest.fixture(scope="module")
def leaky_setup(cfg_store):
    from copy import deepcopy
    cfg, store = cfg_store
    # The tripwire verifies the harness CAN catch a leak, independent of the
    # production trading throttles: with tight min-trade/turnover caps a
    # daily-horizon leak cannot be traded fast enough to look brilliant, so
    # the tripwire runs with permissive execution settings.
    cfg = deepcopy(cfg)
    cfg["portfolio"]["min_trade_pct"] = 0.01
    cfg["portfolio"]["max_daily_turnover"] = 0.20
    panels = compute_signal_panels(store, cfg)
    regime = compute_regime(store, cfg)
    prices = store.read("prices")
    prices["date"] = pd.to_datetime(prices["date"])
    P = (prices[prices["symbol"] != "SPY"]
         .pivot_table(index="date", columns="symbol", values="adj_close")
         .sort_index())
    rets = P.pct_change(fill_method=None)
    return cfg, store, panels, regime, rets


def _leak_hook(rets):
    def hook(prev_i: int, comp: pd.Series) -> pd.Series:
        # deliberate leak: score = NEXT day's return relative to the panel row
        # the engine asked for. With signal_lag=1 that is the FILL day's return
        # (perfect foresight); with signal_lag=2 it is stale, so the edge dies.
        leak_row = min(prev_i + 1, len(rets.index) - 1)
        leaked = rets.iloc[leak_row].reindex(comp.index)
        return leaked * 100  # scale into strong-conviction z territory
    return hook


def test_lag_test_catches_planted_leak(leaky_setup):
    cfg, store, panels, regime, rets = leaky_setup
    base = run_backtest(store, cfg, panels, regime,
                        composite_hook=_leak_hook(rets))
    lagged = run_backtest(store, cfg, panels, regime, signal_lag=2,
                          composite_hook=_leak_hook(rets))
    s1 = base.metrics["net_sharpe"]
    s2 = lagged.metrics["net_sharpe"]
    assert s1 > 1.0, f"planted leak should look great un-lagged, got {s1}"
    drop = (s1 - s2) / abs(s1)
    assert drop > cfg["validate"]["lag_sharpe_drop_max"], (
        f"lag test failed to catch a planted leak (base={s1}, lagged={s2})")
