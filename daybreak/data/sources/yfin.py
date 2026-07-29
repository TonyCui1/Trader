"""yfinance loaders.

WARNING — NOT POINT-IN-TIME (spec Phase 1 explicitly flags this): yfinance
serves current-snapshot fundamentals and silently restates history. Rows are
stored with ingested_at = now (honest: that IS when we saw them), so backfilled
fundamentals are only usable by backtests via the 90-day fiscal lag, and the
whole source is structured for a PIT vendor swap later.
"""
from __future__ import annotations

import pandas as pd

from ..store import PITStore


def ingest_yf_macro(store: PITStore, cfg: dict) -> None:
    import yfinance as yf
    start = str(cfg["backtest"]["start"])
    data = yf.download(["^VIX", "HYG", "LQD"], start=start, progress=False,
                       auto_adjust=False)["Close"]
    df = pd.DataFrame({
        "date": data.index.tz_localize(None) if data.index.tz else data.index,
        "vix": data["^VIX"].values,
        "hyg_close": data["HYG"].values,
        "lqd_close": data["LQD"].values,
    }).dropna()
    df["rf_daily"] = 0.0  # replaced by French RF for backtests, ^IRX for live
    df["source_ts"] = pd.to_datetime(df["date"]) + pd.Timedelta(hours=20)
    now = pd.Timestamp.utcnow().tz_localize(None)
    df["ingested_at"] = df["source_ts"].where(df["source_ts"] < now, now)
    store.append("macro", df)
    print(f"[yfinance] macro: {len(df)} rows")


def ingest_yf_fundamentals(store: PITStore, cfg: dict) -> None:
    import yfinance as yf
    master = store.read("security_master")
    now = pd.Timestamp.utcnow().tz_localize(None)
    rows = []
    for sym in master["symbol"].tolist():
        try:
            t = yf.Ticker(sym)
            q = t.quarterly_income_stmt
            bs = t.quarterly_balance_sheet
            cf = t.quarterly_cashflow
            info_shares = t.info.get("sharesOutstanding")
            for col in q.columns:
                rows.append({
                    "symbol": sym, "fiscal_end": pd.Timestamp(col),
                    "eps": _get(q, "Diluted EPS", col),
                    "net_income": _get(q, "Net Income", col),
                    "gross_profit": _get(q, "Gross Profit", col),
                    "total_assets": _get(bs, "Total Assets", col),
                    "fcf": _get(cf, "Free Cash Flow", col),
                    "shares": info_shares,
                })
        except Exception as e:  # per-symbol failures must not kill the run
            print(f"[yfinance] WARN {sym}: {e}")
    if not rows:
        return
    df = pd.DataFrame(rows).dropna(subset=["fiscal_end"])
    df["source_ts"] = df["fiscal_end"]
    df["ingested_at"] = now  # honest: snapshot data, seen today
    store.append("fundamentals", df)
    print(f"[yfinance] fundamentals: {len(df)} rows (NOT point-in-time — flagged)")


def _get(frame: pd.DataFrame, row: str, col) -> float | None:
    try:
        v = frame.loc[row, col]
        return float(v) if pd.notna(v) else None
    except (KeyError, TypeError):
        return None
