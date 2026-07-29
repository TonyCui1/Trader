"""Portfolio rules: caps, earnings blackout, conviction gate, turnover brake."""
import pandas as pd

from daybreak.config import load_config
from daybreak.portfolio.construct import target_portfolio
from daybreak.regime.state import OFF, ON

CFG = load_config()

SYMS = [f"S{i}" for i in range(30)]


def _inputs():
    composite = pd.Series([3.0 - i * 0.1 for i in range(30)], index=SYMS)
    vols = pd.Series(0.02, index=SYMS)
    sectors = pd.Series((["Tech"] * 15) + (["Health"] * 15), index=SYMS)
    current = pd.Series(0.0, index=SYMS)
    return composite, vols, sectors, current


def test_off_regime_means_all_cash():
    c, v, s, cur = _inputs()
    t = target_portfolio(c, v, s, cur, OFF, set(), CFG)
    assert (t == 0).all()


def test_name_and_sector_caps():
    c, v, s, cur = _inputs()
    t = target_portfolio(c, v, s, cur, ON, set(), CFG)
    assert (t <= CFG["portfolio"]["max_weight_per_name"] + 1e-9).all()
    by_sector = t.groupby(s).sum()
    assert (by_sector <= CFG["portfolio"]["max_weight_per_sector"] + 1e-9).all()


def test_max_positions():
    c, v, s, cur = _inputs()
    t = target_portfolio(c, v, s, cur, ON, set(), CFG)
    assert (t > 0).sum() <= CFG["portfolio"]["max_positions"]


def test_earnings_blackout_blocks_new_but_allows_held():
    c, v, s, cur = _inputs()
    held = cur.copy()
    held["S0"] = 0.05
    blackout = {"S0", "S1"}
    t = target_portfolio(c, v, s, held, ON, blackout, CFG)
    assert t["S1"] == 0.0                    # new position blocked
    assert t["S0"] >= 0.0                    # existing may be held/exited
    # existing blackout position must never be ADDED to
    assert t["S0"] <= held["S0"] + 1e-9


def test_conviction_gate_blocks_low_z_increases():
    c, v, s, cur = _inputs()
    c[:] = 0.3                               # everyone below conviction_z_min
    t = target_portfolio(c, v, s, cur, ON, set(), CFG)
    assert (t == 0).all()                    # nothing tradable -> stays CASH


def test_turnover_brake():
    c, v, s, cur = _inputs()
    t = target_portfolio(c, v, s, cur, ON, set(), CFG)
    turnover = (t - cur).abs().sum()
    assert turnover <= CFG["portfolio"]["max_daily_turnover"] + 1e-9
