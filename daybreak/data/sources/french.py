"""Ken French data library: daily risk-free rate (and factor benchmarks).

Used as the backtest cash rate (spec Phase 5). NOTE (PLAN.md gap 5): the
French library publishes with weeks of lag, so live/paper cash accrual should
use ^IRX/BIL instead — this series is for historical simulation only.
"""
from __future__ import annotations

import io
import zipfile

import pandas as pd
import requests

from ..store import PITStore

URL = ("https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
       "F-F_Research_Data_Factors_daily_CSV.zip")


def ingest_french_rf(store: PITStore, cfg: dict) -> None:
    r = requests.get(URL, timeout=120)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        raw = z.read(z.namelist()[0]).decode("latin-1")
    lines = [ln for ln in raw.splitlines() if ln[:8].strip().isdigit()]
    recs = []
    for ln in lines:
        parts = [p.strip() for p in ln.split(",")]
        recs.append({"date": pd.Timestamp(parts[0]),
                     "mkt_rf": float(parts[1]) / 100, "smb": float(parts[2]) / 100,
                     "hml": float(parts[3]) / 100, "rf_daily": float(parts[4]) / 100})
    df = pd.DataFrame(recs)
    start = pd.Timestamp(str(cfg["backtest"]["start"])) - pd.Timedelta(days=400)
    df = df[df["date"] >= start]
    now = pd.Timestamp.utcnow().tz_localize(None)
    df["source_ts"] = df["date"]
    df["ingested_at"] = now
    store.append("french_factors", df)
    print(f"[french] {len(df)} factor/RF rows")
