"""Circuit breaker (spec Phase 4): drawdown from high-water mark beyond the
threshold -> flatten to cash, halt, and stay halted until a human sets
`portfolio.restart_after_halt: true` in config. Automated, not discretionary.

Backtest exception: a multi-year simulation with a strict manual restart
degenerates into cash-forever after the first trip, so the ENGINE passes
`cooldown_days` (portfolio.circuit_breaker_cooldown_days) and the breaker
auto-resumes after that many trading days, resetting the high-water mark.
Live/paper execution never passes a cooldown — there the restart stays
manual, exactly as the spec demands. Every trip is recorded in trip_log.
"""
from __future__ import annotations


class CircuitBreaker:
    def __init__(self, cfg: dict, cooldown_days: int | None = None):
        self.threshold = cfg["portfolio"]["circuit_breaker_dd"]
        self.restart_flag = bool(cfg["portfolio"]["restart_after_halt"])
        self.cooldown_days = cooldown_days
        self.high_water = float("-inf")
        self.halted = False
        self.days_halted = 0
        self.trip_info: dict | None = None
        self.trip_log: list[dict] = []

    def check(self, equity: float, date=None) -> bool:
        """Update with today's equity. Returns True if trading is halted."""
        if self.halted:
            self.days_halted += 1
            cooled = (self.cooldown_days is not None
                      and self.days_halted >= self.cooldown_days)
            if self.restart_flag or cooled:
                # resume and reset the high-water mark
                self.halted = False
                self.days_halted = 0
                self.high_water = equity
            else:
                return True
        self.high_water = max(self.high_water, equity)
        dd = 1 - equity / self.high_water if self.high_water > 0 else 0.0
        if dd >= self.threshold:
            self.halted = True
            self.days_halted = 0
            self.trip_info = {"date": str(date), "equity": equity,
                              "high_water": self.high_water, "drawdown": round(dd, 4)}
            self.trip_log.append(self.trip_info)
        return self.halted
