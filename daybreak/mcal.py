"""Market calendar utilities.

The decision time — 10:30:00 ET — is HARDCODED here per spec core rule 3.
It must never be moved into config, parameterized, or optimized.
All session logic uses the NYSE exchange calendar in America/New_York,
never the server-local clock.
"""
from __future__ import annotations

from datetime import time
from functools import lru_cache
from zoneinfo import ZoneInfo

import pandas as pd
import pandas_market_calendars as pmc

ET = ZoneInfo("America/New_York")
DECISION_TIME = time(10, 30, 0)  # spec core rule 3: hardcoded, never optimized


@lru_cache(maxsize=8)
def _schedule(start: str, end: str) -> pd.DataFrame:
    return pmc.get_calendar("NYSE").schedule(start_date=start, end_date=end)


def trading_days(start: str, end: str) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(_schedule(start, end).index)


def is_trading_day(d: pd.Timestamp) -> bool:
    d = pd.Timestamp(d).normalize()
    return d in trading_days(str(d.date()), str(d.date()))


def decision_timestamp(d: pd.Timestamp) -> pd.Timestamp:
    """The 10:30:00 ET decision moment for a given trading date."""
    d = pd.Timestamp(d)
    return pd.Timestamp.combine(d.normalize(), DECISION_TIME).tz_localize(ET)


def next_trading_days(d: pd.Timestamp, n: int) -> pd.DatetimeIndex:
    """The next n trading days strictly after date d (for earnings blackout)."""
    d = pd.Timestamp(d).normalize()
    horizon = d + pd.Timedelta(days=30)
    days = trading_days(str(d.date()), str(horizon.date()))
    return days[days > d][:n]
