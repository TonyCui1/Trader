"""Circuit breaker (spec Phase 4): drawdown from high-water mark beyond the
threshold -> flatten to cash, halt, and stay halted until a human sets
`portfolio.restart_after_halt: true` in config. Automated, not discretionary.
"""
from __future__ import annotations


class CircuitBreaker:
    def __init__(self, cfg: dict):
        self.threshold = cfg["portfolio"]["circuit_breaker_dd"]
        self.restart_flag = bool(cfg["portfolio"]["restart_after_halt"])
        self.high_water = float("-inf")
        self.halted = False
        self.trip_info: dict | None = None

    def check(self, equity: float, date=None) -> bool:
        """Update with today's equity. Returns True if trading is halted."""
        if self.halted:
            if self.restart_flag:
                # manual restart: resume and reset the high-water mark
                self.halted = False
                self.high_water = equity
            else:
                return True
        self.high_water = max(self.high_water, equity)
        dd = 1 - equity / self.high_water if self.high_water > 0 else 0.0
        if dd >= self.threshold:
            self.halted = True
            self.trip_info = {"date": str(date), "equity": equity,
                              "high_water": self.high_water, "drawdown": round(dd, 4)}
        return self.halted
