"""Data quality gate (Phase 1): reject/quarantine suspect bars, log everything.

Quarantined rows go to the `quarantine` table with a reason — never silently
dropped, never mixed into clean tables.
"""
from __future__ import annotations

import pandas as pd


def gate_prices(bars: pd.DataFrame, corp_actions: pd.DataFrame | None = None
                ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split incoming daily bars into (clean, quarantined-with-reason).

    Rules (spec Phase 1):
    - zero/negative volume
    - non-positive or missing OHLC
    - >50% single-day move with no matching corporate action on that date
    """
    df = bars.sort_values(["symbol", "date"]).copy()
    reasons = pd.Series("", index=df.index)

    bad_volume = df["volume"] <= 0
    reasons[bad_volume] += "zero_or_negative_volume;"

    bad_price = (
        df[["open", "high", "low", "close"]].le(0).any(axis=1)
        | df[["open", "high", "low", "close"]].isna().any(axis=1)
    )
    reasons[bad_price] += "bad_ohlc;"

    prev_close = df.groupby("symbol")["close"].shift(1)
    move = (df["close"] / prev_close - 1).abs()
    big_move = move > 0.50

    if corp_actions is not None and not corp_actions.empty:
        ca_keys = set(zip(corp_actions["symbol"], pd.to_datetime(corp_actions["date"]).dt.date))
        has_ca = df.apply(
            lambda r: (r["symbol"], pd.Timestamp(r["date"]).date()) in ca_keys, axis=1
        )
    else:
        has_ca = pd.Series(False, index=df.index)
    unexplained = big_move & ~has_ca
    reasons[unexplained] += "gt50pct_move_no_corp_action;"

    quarantined = df[reasons != ""].copy()
    quarantined["quarantine_reason"] = reasons[reasons != ""]
    clean = df[reasons == ""].copy()
    return clean, quarantined
