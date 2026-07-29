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


def fetch_daily_bars(symbols: list[str], start: str, end: str) -> pd.DataFrame:
    rows = []
    for chunk_start in range(0, len(symbols), 200):
        chunk = symbols[chunk_start:chunk_start + 200]
        page_token = None
        while True:
            params = {"symbols": ",".join(chunk), "timeframe": "1Day",
                      "start": start, "end": end, "adjustment": "all",
                      "limit": 10000}
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
    return pd.DataFrame(rows)


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
    # Bootstrap symbol list: most-active US equities via the screener, else
    # reuse an existing security_master.
    master = store.read("security_master")
    if master.empty:
        r = requests.get(f"{DATA_URL}/../v1beta1/screener/stocks/most-actives",
                         params={"by": "volume", "top": 1000},
                         headers=_headers(), timeout=60)
        r.raise_for_status()
        symbols = [x["symbol"] for x in r.json().get("most_actives", [])]
        sm = pd.DataFrame({"symbol": symbols, "sector": "Unknown"})
        sm["source_ts"] = pd.Timestamp.utcnow().tz_localize(None)
        sm["ingested_at"] = pd.Timestamp.utcnow().tz_localize(None)
        store.append("security_master", sm)
    else:
        symbols = master["symbol"].tolist()

    start = str(cfg["backtest"]["start"])
    end = str(pd.Timestamp.utcnow().date())
    bars = fetch_daily_bars(symbols + ["SPY"], start, end)
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
