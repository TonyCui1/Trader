"""Cost model unit tests (spec testing requirements)."""
import math

from daybreak.backtest.costs import half_spread_pct, trade_cost
from daybreak.config import load_config

CFG = load_config()


def test_zero_order_costs_nothing():
    assert trade_cost(0, 0, "buy", 101, 99, 1e7, CFG) == 0.0


def test_slippage_scales_with_sqrt_participation():
    base = trade_cost(10_000, 100, "buy", 100.5, 99.5, 1e7, CFG)
    big = trade_cost(40_000, 400, "buy", 100.5, 99.5, 1e7, CFG)
    # 4x notional -> slippage component scales by 4*sqrt(4)=8x, spread by 4x
    spread = half_spread_pct(100.5, 99.5)
    slip_base = base - 10_000 * spread
    slip_big = big - 40_000 * spread
    assert math.isclose(slip_big / slip_base, 8.0, rel_tol=0.01)


def test_sell_pays_regulatory_fees():
    buy = trade_cost(10_000, 100, "buy", 100.5, 99.5, 1e7, CFG)
    sell = trade_cost(10_000, 100, "sell", 100.5, 99.5, 1e7, CFG)
    expected_fees = (10_000 * CFG["costs"]["sec_fee_per_dollar_sold"]
                     + 100 * CFG["costs"]["taf_fee_per_share_sold"])
    assert math.isclose(sell - buy, expected_fees, rel_tol=1e-9)


def test_half_spread_clipped():
    assert half_spread_pct(100.001, 100.0) >= 0.0002
    assert half_spread_pct(150, 50) <= 0.005
