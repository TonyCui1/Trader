"""Cost model (spec Phase 5). Every backtest number is NET of these — gross
performance is never displayed anywhere (spec core rule 4).

- half-spread: crude range-based estimate from the daily bar (no quote data on
  the free tier), clipped to [2bp, 50bp]. The paper-trading slippage log is the
  ground truth that recalibrates this and `k` (spec Phase 7).
- slippage: k * sqrt(order_dollars / ADV_dollars)
- fees: SEC fee (sells, per dollar) + TAF (sells, per share); $0 commission.
"""
from __future__ import annotations

import math


def half_spread_pct(bar_high: float, bar_low: float) -> float:
    mid = (bar_high + bar_low) / 2
    if not math.isfinite(mid) or mid <= 0 or bar_high < bar_low:
        return 0.0005  # fallback when the bar is missing/degenerate
    est = 0.1 * (bar_high - bar_low) / mid
    return min(max(est, 0.0002), 0.005)


def trade_cost(order_dollars: float, shares: int, side: str,
               bar_high: float, bar_low: float, adv_dollars: float,
               cfg: dict, cost_multiplier: float = 1.0) -> float:
    """Total dollar cost of one fill (always >= 0)."""
    if order_dollars <= 0 or shares <= 0:
        return 0.0
    c = cfg["costs"]
    spread = half_spread_pct(bar_high, bar_low)
    slip = c["slippage_k"] * math.sqrt(order_dollars / max(adv_dollars, 1.0))
    cost = order_dollars * (spread + slip) * cost_multiplier
    cost += c["commission_per_trade"]
    if side == "sell":
        cost += order_dollars * c["sec_fee_per_dollar_sold"]
        cost += shares * c["taf_fee_per_share_sold"]
    return cost
