"""Deterministic synthetic market fixture.

Purpose: lets `make backtest`, `make validate`, `make paper-dry-run`, and CI run
end-to-end with zero API keys, and gives the leakage tripwire a controlled
dataset. Everything is seeded — two runs produce byte-identical tables
(spec testing requirement: bit-reproducible CI fixture).

The fixture has NO embedded alpha: signals on it should earn ~0 minus costs,
which is exactly what the shuffle/cost sanity checks expect.

A small number of deliberately bad bars (zero volume, unexplained >50% moves)
are injected so the quality gate's quarantine log is non-empty (Phase 1
acceptance criterion).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..mcal import trading_days

FIXTURE_START = "2023-01-02"
FIXTURE_END = "2024-12-31"
SECTORS = ["Tech", "Health", "Financials", "Energy", "Industrials",
           "Staples", "Discretionary", "Utilities", "Materials", "RealEstate"]


def generate(n_symbols: int = 100, start: str = FIXTURE_START,
             end: str = FIXTURE_END, seed: int = 42) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    days = trading_days(start, end)
    n_days = len(days)
    symbols = [f"SYN{i:03d}" for i in range(n_symbols)]

    # --- market factor with an engineered mid-sample drawdown (exercises
    # regime REDUCED/OFF and the circuit breaker) -----------------------------
    mkt = rng.normal(0.0004, 0.009, n_days)
    crash_start = n_days // 2
    crash_len = 40
    mkt[crash_start:crash_start + crash_len] = rng.normal(-0.006, 0.022, crash_len)

    sector_of = {s: SECTORS[i % len(SECTORS)] for i, s in enumerate(symbols)}
    sector_factors = {sec: rng.normal(0, 0.004, n_days) for sec in SECTORS}

    # --- per-symbol daily paths ---------------------------------------------
    price_rows, minute_rows = [], []
    betas = rng.uniform(0.6, 1.5, n_symbols)
    idio_vol = rng.uniform(0.008, 0.025, n_symbols)
    p0 = rng.uniform(20, 400, n_symbols)
    base_volume = rng.lognormal(13, 1.0, n_symbols)

    for i, sym in enumerate(symbols):
        rets = (betas[i] * mkt + sector_factors[sector_of[sym]]
                + rng.normal(0, idio_vol[i], n_days))
        close = p0[i] * np.cumprod(1 + rets)
        overnight = rng.normal(0, 0.004, n_days)
        opens = np.empty(n_days)
        opens[0] = p0[i]
        opens[1:] = close[:-1] * (1 + overnight[1:])
        intraday = close / opens - 1
        high = np.maximum(opens, close) * (1 + np.abs(rng.normal(0, 0.004, n_days)))
        low = np.minimum(opens, close) * (1 - np.abs(rng.normal(0, 0.004, n_days)))
        vol = (base_volume[i] * np.exp(rng.normal(0, 0.3, n_days))).astype(np.int64)
        vwap_1030 = opens * (1 + 0.4 * intraday + rng.normal(0, 0.0015, n_days))

        price_rows.append(pd.DataFrame({
            "date": days, "symbol": sym, "open": opens.round(4),
            "high": high.round(4), "low": low.round(4), "close": close.round(4),
            "adj_close": close.round(4), "volume": vol,
        }))
        minute_rows.append(pd.DataFrame({
            "date": days, "symbol": sym, "vwap_1030": vwap_1030.round(4),
            "volume_1030": (vol * 0.03).astype(np.int64),
        }))

    prices = pd.concat(price_rows, ignore_index=True)
    minute = pd.concat(minute_rows, ignore_index=True)

    # --- SPY (market proxy) --------------------------------------------------
    spy_close = 400 * np.cumprod(1 + mkt)
    spy = pd.DataFrame({
        "date": days, "symbol": "SPY", "open": (spy_close * (1 + rng.normal(0, 0.001, n_days))).round(4),
        "high": (spy_close * 1.004).round(4), "low": (spy_close * 0.996).round(4),
        "close": spy_close.round(4), "adj_close": spy_close.round(4),
        "volume": np.full(n_days, 80_000_000, dtype=np.int64),
    })
    prices = pd.concat([prices, spy], ignore_index=True)
    minute = pd.concat([minute, pd.DataFrame({
        "date": days, "symbol": "SPY",
        "vwap_1030": (spy_close * (1 + rng.normal(0, 0.0008, n_days))).round(4),
        "volume_1030": np.full(n_days, 2_400_000, dtype=np.int64),
    })], ignore_index=True)

    # --- inject bad bars for the quarantine log (Phase 1 acceptance) --------
    bad_idx = rng.choice(len(price_rows[0]) - 10, 3, replace=False) + 5
    prices.loc[prices.index[bad_idx[0]], "volume"] = 0
    prices.loc[prices.index[bad_idx[1]], "close"] *= 1.9   # >50% move, no corp action
    prices.loc[prices.index[bad_idx[2]], "low"] = np.nan

    # --- macro: VIX + HYG/LQD + risk-free -----------------------------------
    roll_vol = pd.Series(mkt).rolling(10, min_periods=1).std().fillna(0.009).to_numpy()
    vix = np.clip(11 + 900 * roll_vol + rng.normal(0, 1.5, n_days), 9, 85)
    credit_stress = pd.Series(mkt).rolling(20, min_periods=1).mean().fillna(0).to_numpy()
    hyg = 78 * np.cumprod(1 + 0.3 * mkt + rng.normal(0, 0.002, n_days))
    lqd = 108 * np.cumprod(1 + 0.1 * mkt - 0.5 * np.minimum(credit_stress, 0) + rng.normal(0, 0.0015, n_days))
    macro = pd.DataFrame({
        "date": days, "vix": vix.round(2), "hyg_close": hyg.round(4),
        "lqd_close": lqd.round(4), "rf_daily": np.full(n_days, 0.045 / 252),
    })

    # --- quarterly fundamentals + earnings calendar --------------------------
    fund_rows, earn_rows = [], []
    q_ends = pd.date_range(pd.Timestamp(start) - pd.DateOffset(months=15),
                           end, freq="QE")
    for i, sym in enumerate(symbols):
        assets = rng.uniform(1e9, 5e10)
        margin = rng.uniform(0.15, 0.55)
        eps = rng.uniform(1.0, 8.0)
        shares = rng.uniform(5e7, 2e9)
        for q in q_ends:
            assets *= 1 + rng.normal(0.01, 0.01)
            eps = eps * (1 + rng.normal(0.01, 0.08))
            net_income = eps * shares
            gross_profit = margin * assets * (1 + rng.normal(0, 0.05))
            fcf = net_income * (0.8 + rng.normal(0, 0.15))
            fund_rows.append({
                "symbol": sym, "fiscal_end": q, "eps": round(eps, 4),
                "net_income": round(net_income, 2), "gross_profit": round(gross_profit, 2),
                "total_assets": round(assets, 2), "fcf": round(fcf, 2),
                "shares": round(shares, 0),
            })
            report_date = q + pd.Timedelta(days=int(rng.integers(25, 45)))
            earn_rows.append({"symbol": sym, "earnings_date": report_date})

    fundamentals = pd.DataFrame(fund_rows)
    earnings = pd.DataFrame(earn_rows)

    master = pd.DataFrame({
        "symbol": symbols, "sector": [sector_of[s] for s in symbols],
    })

    return {"prices": prices, "minute_1030": minute, "macro": macro,
            "fundamentals": fundamentals, "earnings_calendar": earnings,
            "security_master": master}


def stamp(df: pd.DataFrame, source_ts, ingested_at) -> pd.DataFrame:
    """Attach PIT columns. Accepts scalars or per-row series."""
    out = df.copy()
    out["source_ts"] = source_ts
    out["ingested_at"] = ingested_at
    return out
