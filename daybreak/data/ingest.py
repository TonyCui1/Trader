"""Daily ingest job (Phase 1). Idempotent: re-running never duplicates or
mutates rows (dedupe lives in PITStore.append).

Two modes:
- `--fixture`: generate the deterministic synthetic dataset (no keys needed).
- default: live ingest from Alpaca/yfinance/French (requires ALPACA_API_KEY /
  ALPACA_SECRET_KEY env vars for prices).

Backfilled rows carry honest historical ingested_at values:
- daily bars for day D        -> ingested_at = D 20:00 ET (after the close)
- 10:30 minute bar for day D  -> ingested_at = D 10:31 ET (execution data,
                                 never a signal input)
- fundamentals for fiscal Q   -> ingested_at = fiscal_end + fundamental_lag_days
- earnings calendar entries   -> ingested_at = 30 days before the event
- macro closes for day D      -> ingested_at = D 20:00 ET
"""
from __future__ import annotations

import argparse
import sys

import pandas as pd

from ..config import load_config
from . import synthetic
from .quality import gate_prices
from .store import PITStore
from .universe import build_universe


def _et_evening(dates: pd.Series) -> pd.Series:
    return pd.to_datetime(dates) + pd.Timedelta(hours=20)


def ingest_fixture(cfg: dict) -> PITStore:
    store = PITStore(cfg["run"]["data_dir"])
    tables = synthetic.generate(
        n_symbols=cfg["universe"]["fixture_size"], seed=cfg["run"]["seed"])
    lag = cfg["signals"]["fundamental_lag_days"]

    clean, quarantined = gate_prices(tables["prices"], corp_actions=None)
    n = store.append("prices", synthetic.stamp(
        clean, _et_evening(clean["date"]), _et_evening(clean["date"])))
    if not quarantined.empty:
        store.append("quarantine", synthetic.stamp(
            quarantined, _et_evening(quarantined["date"]), _et_evening(quarantined["date"])))

    m = tables["minute_1030"]
    store.append("minute_1030", synthetic.stamp(
        m, pd.to_datetime(m["date"]) + pd.Timedelta(hours=10, minutes=30),
        pd.to_datetime(m["date"]) + pd.Timedelta(hours=10, minutes=31)))

    mac = tables["macro"]
    store.append("macro", synthetic.stamp(mac, _et_evening(mac["date"]), _et_evening(mac["date"])))

    f = tables["fundamentals"]
    avail = pd.to_datetime(f["fiscal_end"]) + pd.Timedelta(days=lag)
    store.append("fundamentals", synthetic.stamp(f, pd.to_datetime(f["fiscal_end"]), avail))

    e = tables["earnings_calendar"]
    store.append("earnings_calendar", synthetic.stamp(
        e, pd.to_datetime(e["earnings_date"]),
        pd.to_datetime(e["earnings_date"]) - pd.Timedelta(days=30)))

    sm = tables["security_master"]
    store.append("security_master", synthetic.stamp(
        sm, pd.Timestamp("2000-01-01"), pd.Timestamp("2000-01-01")))

    build_universe(store, cfg, fixture=True)

    q = store.read("quarantine")
    print(f"[ingest] fixture complete: {n} price rows, "
          f"{len(q)} quarantined rows (review: quarantine table), "
          f"config_hash={cfg['_config_hash']}")
    return store


def ingest_live(cfg: dict) -> PITStore:
    from .sources.alpaca_src import ingest_alpaca_prices
    from .sources.french import ingest_french_rf
    from .sources.yfin import ingest_yf_fundamentals, ingest_yf_macro

    store = PITStore(cfg["run"]["data_dir"])
    ingest_alpaca_prices(store, cfg)
    ingest_yf_macro(store, cfg)
    ingest_yf_fundamentals(store, cfg)   # flagged NOT point-in-time
    ingest_french_rf(store, cfg)
    build_universe(store, cfg, fixture=False)
    print(f"[ingest] live ingest complete, config_hash={cfg['_config_hash']}")
    return store


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", action="store_true",
                    help="generate the deterministic synthetic dataset")
    ap.add_argument("--config", default=None)
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    if args.fixture:
        ingest_fixture(cfg)
    else:
        ingest_live(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
