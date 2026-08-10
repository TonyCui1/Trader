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


def test_full_exits_bypass_min_trade_gate():
    c, v, s, cur = _inputs()
    held = cur.copy()
    held["S29"] = 0.015                       # dust position, below min_trade_pct
    c["S29"] = -2.0                           # score collapsed: not in target
    t = target_portfolio(c, v, s, held, ON, set(), CFG)
    assert t["S29"] == 0.0                    # exited despite being tiny


def test_hysteresis_keeps_gray_zone_holdings():
    c, v, s, cur = _inputs()
    # 30 names scored 3.0 down to 0.1; entry line = top 10% (~top 3),
    # exit line = top 20% (~top 6).
    held = cur.copy()
    held["S4"] = 0.05                         # held, in the gray zone (rank 5)
    t = target_portfolio(c, v, s, held, ON, set(), CFG)
    assert t["S4"] > 0.0                      # kept: above exit line
    # a held name clearly below the exit line is sold
    held2 = cur.copy()
    held2["S25"] = 0.05                       # held, rank 26: well below exit line
    t2 = target_portfolio(c, v, s, held2, ON, set(), CFG)
    assert t2["S25"] == 0.0


def test_fixed_fraction_mode_deploys_constant_regardless_of_vol():
    from copy import deepcopy
    cfg = deepcopy(CFG)
    cfg["portfolio"]["sizing_mode"] = "fixed_fraction"
    cfg["portfolio"]["fixed_fraction_target"] = 0.5
    c, v, s, cur = _inputs()
    low_vol = v.copy()          # calm market
    high_vol = v * 50           # choppy market (recent vol much higher)
    t_low = target_portfolio(c, low_vol, s, cur, ON, set(), cfg)
    t_high = target_portfolio(c, high_vol, s, cur, ON, set(), cfg)
    # fixed_fraction: total deployed is the same regardless of recent vol
    # (caps may still trim slightly, but both hit the same pre-cap target)
    assert abs(t_low.sum() - t_high.sum()) < 1e-6


def test_vol_target_mode_deploys_less_in_choppy_markets():
    c, v, s, cur = _inputs()
    low_vol = v.copy()
    high_vol = v * 50
    t_low = target_portfolio(c, low_vol, s, cur, ON, set(), CFG)
    t_high = target_portfolio(c, high_vol, s, cur, ON, set(), CFG)
    # vol_target (default/live mode): choppier market -> less deployed,
    # this is the asymmetry that motivated adding fixed_fraction as an
    # alternative, documented here so it can't silently regress
    assert t_high.sum() < t_low.sum()


def test_default_sizing_mode_is_vol_target():
    assert CFG["portfolio"].get("sizing_mode", "vol_target") == "vol_target"
