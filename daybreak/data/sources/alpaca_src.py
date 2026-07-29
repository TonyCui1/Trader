"""Alpaca market data ingest (live mode). Requires ALPACA_API_KEY /
ALPACA_SECRET_KEY env vars and the free market-data plan or better.

NOTE (PLAN.md gap 2): the free plan serves the IEX feed only (~2-3% of
consolidated volume). Minute-bar VWAPs for thin names may be unreliable —
symbols whose 10:25-10:30 window has zero prints are quarantined rather than
stored with a fabricated price.
"""
from __future__ import annotations

import os

import pandas as pd
import requests

from ..quality import gate_prices
from ..store import PITStore

DATA_URL = "https://data.alpaca.markets/v2"


def _headers() -> dict:
    key, secret = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError(
            "Live ingest needs ALPACA_API_KEY and ALPACA_SECRET_KEY env vars. "
            "Use `make ingest-fixture` for the synthetic dataset.")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def fetch_daily_bars(symbols: list[str], start: str, end: str,
                     verbose: bool = False) -> pd.DataFrame:
    """Daily bars via the IEX feed (the free plan; SIP needs a subscription)."""
    rows = []
    n_chunks = (len(symbols) + 199) // 200
    for ci, chunk_start in enumerate(range(0, len(symbols), 200)):
        chunk = symbols[chunk_start:chunk_start + 200]
        page_token = None
        while True:
            params = {"symbols": ",".join(chunk), "timeframe": "1Day",
                      "start": start, "end": end, "adjustment": "all",
                      "feed": "iex", "limit": 10000}
            if page_token:
                params["page_token"] = page_token
            r = requests.get(f"{DATA_URL}/stocks/bars", params=params,
                             headers=_headers(), timeout=60)
            r.raise_for_status()
            payload = r.json()
            for sym, bars in (payload.get("bars") or {}).items():
                for b in bars:
                    rows.append({"date": pd.Timestamp(b["t"]).tz_convert(None).normalize(),
                                 "symbol": sym, "open": b["o"], "high": b["h"],
                                 "low": b["l"], "close": b["c"],
                                 "adj_close": b["c"], "volume": b["v"]})
            page_token = payload.get("next_page_token")
            if not page_token:
                break
        if verbose:
            print(f"[alpaca] bars chunk {ci + 1}/{n_chunks} "
                  f"({len(rows)} rows so far)", flush=True)
    return pd.DataFrame(rows)


def fetch_active_symbols() -> list[str]:
    """All active, tradable US common stocks from the trading API (paper)."""
    r = requests.get("https://paper-api.alpaca.markets/v2/assets",
                     params={"status": "active", "asset_class": "us_equity"},
                     headers=_headers(), timeout=120)
    r.raise_for_status()
    out = []
    for a in r.json():
        sym = a.get("symbol", "")
        if (a.get("tradable") and a.get("exchange") in ("NYSE", "NASDAQ", "ARCA", "AMEX")
                and sym.isalpha() and len(sym) <= 5):
            out.append(sym)
    return sorted(set(out))


def fetch_1030_minute_bars(symbols: list[str], date: str) -> pd.DataFrame:
    """VWAP of the 10:25-10:30 ET window for one date."""
    rows = []
    start = f"{date}T14:25:00Z"  # 10:25 ET in UTC during EDT; calendar-safe
    end = f"{date}T15:31:00Z"
    for chunk_start in range(0, len(symbols), 200):
        chunk = symbols[chunk_start:chunk_start + 200]
        r = requests.get(f"{DATA_URL}/stocks/bars",
                         params={"symbols": ",".join(chunk), "timeframe": "1Min",
                                 "start": start, "end": end, "limit": 10000},
                         headers=_headers(), timeout=60)
        r.raise_for_status()
        for sym, bars in (r.json().get("bars") or {}).items():
            window = [b for b in bars
                      if pd.Timestamp(b["t"]).tz_convert("America/New_York").time().hour == 10
                      and 25 <= pd.Timestamp(b["t"]).tz_convert("America/New_York").time().minute <= 30]
            vol = sum(b["v"] for b in window)
            if vol > 0:
                vwap = sum(b["vw"] * b["v"] for b in window) / vol
                rows.append({"date": pd.Timestamp(date), "symbol": sym,
                             "vwap_1030": round(vwap, 4), "volume_1030": vol})
    return pd.DataFrame(rows)


def ingest_alpaca_prices(store: PITStore, cfg: dict) -> None:
    # Bootstrap the symbol list: pull every active US common stock, rank by
    # recent dollar volume, and keep ~1.2x the target universe size so the
    # monthly universe builder has headroom. (The most-actives screener caps
    # at 100 names, so it cannot seed a Russell-1000-style universe.)
    master = store.read("security_master")
    now = pd.Timestamp.utcnow().tz_localize(None)
    if master.empty:
        all_syms = fetch_active_symbols()
        print(f"[alpaca] {len(all_syms)} active US equities; ranking by recent "
              "dollar volume (one-time bootstrap, a few minutes)...", flush=True)
        recent_start = str((pd.Timestamp.utcnow() - pd.Timedelta(days=45)).date())
        recent = fetch_daily_bars(all_syms, recent_start,
                                  str(pd.Timestamp.utcnow().date()), verbose=True)
        if recent.empty:
            raise RuntimeError("bootstrap bars came back empty")
        advd = (recent.assign(dv=recent["close"] * recent["volume"])
                .groupby("symbol")["dv"].mean().sort_values(ascending=False))
        keep = int(cfg["universe"]["size"] * 1.2)
        symbols = advd.head(keep).index.tolist()
        sm = pd.DataFrame({"symbol": symbols, "sector": "Unknown"})
        sm["source_ts"] = now
        sm["ingested_at"] = now
        store.append("security_master", sm)
        print(f"[alpaca] kept top {len(symbols)} by dollar volume "
              "(sectors start as 'Unknown'; run `make sectors` to enrich)")
    else:
        symbols = master["symbol"].tolist()

    start = str(cfg["backtest"]["start"])
    end = str(pd.Timestamp.utcnow().date())
    print(f"[alpaca] fetching daily history {start} -> {end} for "
          f"{len(symbols)} symbols...", flush=True)
    bars = fetch_daily_bars(symbols + ["SPY"], start, end, verbose=True)
    if bars.empty:
        raise RuntimeError("Alpaca returned no bars")
    clean, quarantined = gate_prices(bars, corp_actions=None)
    now = pd.Timestamp.utcnow().tz_localize(None)
    for name, df in (("prices", clean), ("quarantine", quarantined)):
        if df.empty:
            continue
        df = df.copy()
        df["source_ts"] = pd.to_datetime(df["date"]) + pd.Timedelta(hours=20)
        df["ingested_at"] = df["source_ts"].where(df["source_ts"] < now, now)
        store.append(name, df)
    print(f"[alpaca] {len(clean)} bars stored, {len(quarantined)} quarantined")
