"""Seed files: small, critical state persisted in the repo itself.

Ephemeral runners (GitHub Actions) rebuild the PIT store from cache or from
scratch. The security master (symbols + sector labels + ETF flags) is tiny
but expensive to recreate (hours of yfinance crawling), so each successful
run exports it to seeds/security_master.csv and commits it; a fresh store
bootstraps from the seed instead of re-ranking/re-crawling.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..config import REPO_ROOT
from .store import PITStore

SEED_MASTER = REPO_ROOT / "seeds" / "security_master.csv"


def export_security_master(store: PITStore) -> Path:
    from .universe import latest_security_master
    m = latest_security_master(store)
    SEED_MASTER.parent.mkdir(parents=True, exist_ok=True)
    m[["symbol", "sector"]].sort_values("symbol").to_csv(SEED_MASTER, index=False)
    return SEED_MASTER


def load_seed_master() -> pd.DataFrame | None:
    if not SEED_MASTER.exists():
        return None
    df = pd.read_csv(SEED_MASTER)
    return df if {"symbol", "sector"}.issubset(df.columns) and len(df) else None
