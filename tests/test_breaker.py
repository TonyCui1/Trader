"""Circuit breaker: trips at the drawdown threshold, stays halted until the
manual restart flag is set (spec Phase 4)."""
from copy import deepcopy

from daybreak.config import load_config
from daybreak.portfolio.breaker import CircuitBreaker

CFG = load_config()


def test_trips_at_threshold_and_requires_manual_restart():
    b = CircuitBreaker(CFG)
    assert not b.check(100_000, "d1")
    assert not b.check(95_000, "d2")          # -5%: fine
    assert b.check(87_000, "d3")              # -13%: tripped
    assert b.trip_info["drawdown"] >= CFG["portfolio"]["circuit_breaker_dd"]
    # recovery does NOT resume trading without the flag
    assert b.check(99_000, "d4")
    assert b.check(120_000, "d5")


def test_manual_restart_resumes_and_resets_high_water():
    cfg = deepcopy(CFG)
    b = CircuitBreaker(cfg)
    b.check(100_000, "d1")
    assert b.check(80_000, "d2")              # tripped
    b.restart_flag = True                     # human sets restart_after_halt
    assert not b.check(80_000, "d3")          # resumes
    assert b.high_water == 80_000             # HWM reset at restart
    assert not b.check(75_000, "d4")          # -6.25% from new HWM: fine


def test_backtest_cooldown_auto_resumes():
    cfg = deepcopy(CFG)
    b = CircuitBreaker(cfg, cooldown_days=3)
    b.check(100_000, "d1")
    assert b.check(85_000, "d2")              # tripped (-15%)
    assert b.check(85_000, "d3")              # halted day 1
    assert b.check(85_000, "d4")              # halted day 2
    assert not b.check(85_000, "d5")          # cooldown reached: resumed
    assert b.high_water == 85_000             # HWM reset on resume
    assert len(b.trip_log) == 1
    assert b.check(74_000, "d6")              # can trip again from new HWM
    assert len(b.trip_log) == 2
