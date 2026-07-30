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


def ingest_yf_fundamentals(store: PITStore, cfg: dict,
                           symbols: list[str] | None = None) -> None:
    import yfinance as yf
    if symbols is None:
        symbols = store.read("security_master")["symbol"].tolist()
    now = pd.Timestamp.utcnow().tz_localize(None)
    print(f"[yfinance] crawling fundamentals for {len(symbols)} symbols "
          "(slow — expect 1-3s each)...", flush=True)
    rows, earn_rows = [], []
    for i, sym in enumerate(symbols):
        if i and i % 50 == 0:
            print(f"[yfinance] {i}/{len(symbols)} done", flush=True)
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
            try:
                ed = t.get_earnings_dates(limit=8)
                if ed is not None and not ed.empty:
                    for d in ed.index:
                        earn_rows.append({"symbol": sym,
                                          "earnings_date": pd.Timestamp(d).tz_localize(None).normalize()})
            except Exception:
                pass  # earnings dates are best-effort; blackout degrades gracefully
        except Exception as e:  # per-symbol failures must not kill the run
            print(f"[yfinance] WARN {sym}: {e}")
    if rows:
        df = pd.DataFrame(rows).dropna(subset=["fiscal_end"])
        df["source_ts"] = df["fiscal_end"]
        df["ingested_at"] = now  # honest: snapshot data, seen today
        store.append("fundamentals", df)
        print(f"[yfinance] fundamentals: {len(df)} rows (NOT point-in-time — flagged)")
    if earn_rows:
        edf = pd.DataFrame(earn_rows).drop_duplicates()
        edf["source_ts"] = edf["earnings_date"]
        edf["ingested_at"] = now
        store.append("earnings_calendar", edf)
        print(f"[yfinance] earnings calendar: {len(edf)} rows")


def _get(frame: pd.DataFrame, row: str, col) -> float | None:
    try:
        v = frame.loc[row, col]
        return float(v) if pd.notna(v) else None
    except (KeyError, TypeError):
        return None


def enrich_sectors(store: PITStore, cfg: dict) -> None:
    """Fill in sector labels for security_master rows marked 'Unknown'.

    Appends fresh rows (the store is append-only); consumers read the latest
    row per symbol. Slow (~1-2s/symbol via yfinance) — run once, ideally
    overnight, before trusting sector caps and value sector-neutralization.
    """
    import yfinance as yf
    from ..universe import latest_security_master

    master = latest_security_master(store)
    todo = master[master["sector"].isin(["Unknown", "", None])]["symbol"].tolist()
    if not todo:
        print("[sectors] nothing to enrich")
        return
    print(f"[sectors] enriching {len(todo)} symbols (slow)...", flush=True)
    rows = []
    for i, sym in enumerate(todo):
        if i and i % 50 == 0:
            print(f"[sectors] {i}/{len(todo)} done", flush=True)
        try:
            sector = yf.Ticker(sym).info.get("sector") or "Unknown"
        except Exception:
            sector = "Unknown"
        if sector != "Unknown":
            rows.append({"symbol": sym, "sector": sector})
    if rows:
        df = pd.DataFrame(rows)
        now = pd.Timestamp.utcnow().tz_localize(None)
        df["source_ts"] = now
        df["ingested_at"] = now
        store.append("security_master", df)
    print(f"[sectors] enriched {len(rows)}/{len(todo)} symbols")


if __name__ == "__main__":
    import sys as _sys

    from ...config import load_config as _load
    _cfg = _load()
    enrich_sectors(PITStore(_cfg["run"]["data_dir"]), _cfg)
    _sys.exit(0)
