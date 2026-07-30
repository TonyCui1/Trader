"""Point-in-time universe builder (Phase 1).

Top-N US stocks by trailing dollar volume, refreshed monthly. The membership
list *as of each month* is stored with that month's ingested_at — today's
universe is never applied to the past. Delisted names naturally remain in
historical months' rows (spec: store the universe as of each date).
"""
from __future__ import annotations

import pandas as pd

from .store import PITStore


EXCLUDED_SECTORS = {"ETF"}  # funds are not stocks: out of the universe entirely


def latest_security_master(store: PITStore) -> pd.DataFrame:
    """One row per symbol, newest ingested_at wins (sector enrichment appends
    fresh rows on top of the append-only history)."""
    m = store.read("security_master")
    if m.empty:
        return m
    return (m.sort_values("ingested_at")
            .drop_duplicates(subset="symbol", keep="last"))


def non_stock_symbols(store: PITStore) -> set:
    """Symbols flagged as funds/ETFs in the latest security master. The spec's
    universe is top-N *stocks* (Russell 1000 proxy); Alpaca's us_equity asset
    class also contains ETFs, which `make sectors` flags via quoteType."""
    m = latest_security_master(store)
    if m.empty:
        return set()
    return set(m[m["sector"].isin(EXCLUDED_SECTORS)]["symbol"])


def build_universe(store: PITStore, cfg: dict, fixture: bool = False) -> int:
    prices = store.read_latest("prices", keys=["symbol", "date"])
    master = latest_security_master(store)
    if prices.empty or master.empty:
        raise RuntimeError("prices/security_master must be ingested before universe build")

    size = cfg["universe"]["fixture_size"] if fixture else cfg["universe"]["size"]
    lookback = cfg["universe"]["adv_lookback_days"]
    sector_of = dict(zip(master["symbol"], master["sector"]))
    etfs = non_stock_symbols(store)

    prices = prices[(prices["symbol"] != "SPY")
                    & ~prices["symbol"].isin(etfs)].copy()
    prices["date"] = pd.to_datetime(prices["date"])
    prices["dollar_vol"] = prices["close"] * prices["volume"]

    month_starts = (prices["date"].dt.to_period("M").drop_duplicates()
                    .dt.to_timestamp().sort_values())
    rows = []
    for m in month_starts:
        window = prices[(prices["date"] < m)
                        & (prices["date"] >= m - pd.Timedelta(days=lookback * 2))]
        if window.empty:
            continue
        adv = (window.groupby("symbol")["dollar_vol"].mean()
               .sort_values(ascending=False).head(size))
        for rank, (sym, val) in enumerate(adv.items(), start=1):
            rows.append({"month": m, "symbol": sym, "rank": rank,
                         "adv_dollars": round(float(val), 2),
                         "sector": sector_of.get(sym, "Unknown")})
    if not rows:
        return 0
    df = pd.DataFrame(rows)
    df["source_ts"] = df["month"]
    df["ingested_at"] = df["month"]  # membership known at month start
    return store.append("universe", df)


def universe_for_date(store: PITStore, date: pd.Timestamp,
                      as_of: pd.Timestamp | None = None) -> pd.DataFrame:
    uni = store.read("universe", as_of=as_of)
    if uni.empty:
        return uni
    uni = uni[~uni["symbol"].isin(non_stock_symbols(store))]
    uni["month"] = pd.to_datetime(uni["month"])
    months = uni["month"][uni["month"] <= pd.Timestamp(date)]
    if months.empty:
        return pd.DataFrame()
    return uni[uni["month"] == months.max()]
