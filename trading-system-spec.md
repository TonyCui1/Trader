# Build Spec: Daily 10:30am Systematic Trading Model

Instructions for Claude Code. Build in the order given. Do not skip acceptance criteria.
Language: Python 3.11+. Repo name: `daybreak`.

---

## Core rules (apply to ALL code)

1. **Point-in-time everything.** Append-only storage. Never overwrite a record. Every row carries `ingested_at` (when WE first saw it) and `source_ts` (vendor claim). Models may only use data where `ingested_at < decision_time`.
2. **No trade is the default.** The system outputs BUY / SELL / HOLD / CASH. CASH requires no justification; a trade does.
3. **One decision time: 10:30:00 ET daily.** Hardcode it. Never parameterize or optimize it.
4. **All performance numbers are net.** Slippage + spread + commissions baked into every backtest result. Gross numbers must never be displayed anywhere.
5. **Any backtest Sharpe > 1.5 is treated as a bug** until walk-forward and lag tests pass. Print a warning banner when it happens.
6. **Config over code.** All thresholds in one `config.yaml`. Log the full config hash with every run so results are reproducible.

---

## Architecture

```
daybreak/
├── config.yaml
├── data/           # ingestion + point-in-time store
├── signals/        # feature computation
├── regime/         # market state → risk-on/off
├── portfolio/      # sizing, caps, circuit breaker
├── backtest/       # simulator + cost model
├── validate/       # walk-forward, lag test, leakage checks
├── execute/        # Alpaca paper trading
├── report/         # daily decision log + explanations
└── tests/
```

Storage: DuckDB + Parquet (local, free, fast). No cloud DB needed.

---

## Phase 1 — Data layer

**Sources (all free/cheap tiers):**
- Prices: Alpaca market data API (free w/ account) — daily OHLCV + one 10:25–10:30am minute bar per symbol.
- Fundamentals: start with yfinance for prototyping; flag it as NOT point-in-time. Structure the loader so a PIT vendor can swap in later.
- Factor benchmarks: Ken French library CSVs (auto-download).
- Earnings calendar: Alpaca or yfinance earnings dates.
- VIX + credit spreads (HYG/LQD ratio as free proxy): daily closes.

**Universe:** Russell 1000 proxy — top 1000 US stocks by dollar volume, refreshed monthly, **including delisted names going forward** (store the universe as of each date; never apply today's universe to the past).

**Requirements:**
- Idempotent daily ingest job. Re-running never duplicates or mutates rows.
- Data quality gate: reject/quarantine bars with zero volume, >50% single-day moves without corporate-action match, missing splits/dividends. Log everything quarantined.
- Corporate actions: use adjusted prices for signals, UNadjusted for execution simulation.

**Acceptance:** ingest 5 years of daily data for the universe; a re-run produces byte-identical tables; quarantine log is non-empty and reviewed.

## Phase 2 — Signals

Implement exactly these, no more (cap: 10):

| Signal | Definition |
|---|---|
| Momentum | 12-1 month return (skip most recent month) |
| Short-term reversal | trailing 1-month return, inverted |
| Value | earnings yield + FCF yield, sector-neutralized |
| Quality | gross profit / assets; accruals penalty |
| Low vol | 60-day realized vol, inverted |
| PEAD | standardized earnings surprise, decayed over 60 days |

- Each signal → cross-sectional z-score, winsorized at ±3.
- Composite = equal-weight average of z-scores. **Do not fit weights in v1.** Equal weights cannot be overfit.
- Every signal computed with a **T-1 close cutoff**: only data available before yesterday's close feeds today's 10:30 decision. Fundamentals lagged 90 days after fiscal period end.

**Acceptance:** for each signal, decile-spread plot over the backtest period; sign must match the literature; if it doesn't, investigate — don't flip the sign to fix it.

## Phase 3 — Regime layer

Risk state each day ∈ {ON, REDUCED, OFF}:
- SPY below 200-day MA → REDUCED
- SPY below 200-day MA **and** HYG/LQD ratio below its own 100-day MA → OFF
- VIX > 30 → force REDUCED minimum

OFF → 100% cash. REDUCED → half target exposure. Simple, few parameters, all stated in advance. Do not add regime indicators in v1.

## Phase 4 — Portfolio construction

- Long-only v1. Top-decile composite names, max 20 positions.
- **Vol-targeted sizing:** weight ∝ 1/60-day vol, portfolio targeted to 10% annualized vol.
- Caps: 8% per name, 25% per sector.
- **Calendar filter:** no new position if the name reports earnings within the next 2 trading days; existing positions may be held or exited but not added to.
- **Conviction threshold:** trade only if composite z > 0.5 AND the required trade moves the position by >1% of portfolio. Otherwise HOLD/CASH.
- Turnover brake: max 20% of portfolio traded per day.
- **Circuit breaker:** 12% drawdown from high-water mark → flatten to cash, halt, require manual restart flag in config.

## Phase 5 — Backtester

Event-driven daily simulator:
- Fills at the actual 10:30am minute bar VWAP (never the daily close).
- Cost model: half-spread (estimate from bar high-low if no quote data) + slippage = `k * sqrt(order_size / ADV)`, k=0.1 default + $0 commission (Alpaca) but model SEC/TAF fees.
- Cash earns T-bill rate (from French library RF series).
- Track: net equity curve, max DD, Sharpe, Sortino, turnover, % days in cash, per-signal attribution, short-term-gains tax estimate at a configurable rate.
- Benchmarks on every report: SPY buy-and-hold, and SPY with the same regime filter (to show what the signals add beyond timing).

**Acceptance:** costs shown as % of gross return; a zero-signal random portfolio through the same simulator must lose ≈ its cost load (sanity check the cost model works).

## Phase 6 — Validation harness (build BEFORE tuning anything)

1. **Walk-forward:** expanding window; if anything is ever fit, fit on data through year N, test on year N+1 only, roll forward. Report out-of-sample concatenated results only.
2. **Lag test:** rerun the full backtest with every input lagged one extra day. If net Sharpe drops >30%, there is leakage — find it, fixing the data, not the threshold.
3. **Shuffle test:** randomize signal-to-stock assignment; result must be ≈ zero minus costs. If shuffled data "works," the framework leaks.
4. **Sub-period report:** results split by year and by regime state. A strategy that made everything in 2020–2021 gets flagged.
5. **Parameter sensitivity:** ±25% perturbation on every config threshold; if results collapse, the strategy is a fit artifact.

**Acceptance:** all five run from one command (`make validate`) and produce one HTML report. Red/green per check.

## Phase 7 — Paper execution

- Alpaca paper account. Cron/scheduler at 10:25 ET: pull fresh data → compute decision → submit limit orders at 10:30 (limit = last trade ±0.3%) → cancel unfilled after 15 min.
- **Kill switch:** file-based flag checked before any order.
- Reconciliation job at 11:00: compare intended vs. filled; log slippage vs. model estimate daily. This slippage log is the ground truth that recalibrates `k`.
- Decision log: every day, one JSON record — inputs hash, signals, regime, decision, reasoning summary. Immutable.

**Acceptance:** 10 consecutive trading days of automated paper decisions with zero manual intervention and complete logs.

## Phase 8 — News layer (LAST, and only after 1–7 pass)

Scope strictly to the three agreed roles:
1. **Regime context:** weekly macro state summary (Fed stance, credit conditions) stored as slow-moving features, minimum 1-session lag.
2. **Veto list:** names with pending M&A, halts, going-concern, fraud litigation → no new positions. Sourced from filings/headlines, 24h lag.
3. **Explainability:** attach relevant headlines to the daily decision log. Display only, never a model input.

News rules: stamp with our own `ingested_at`, never vendor time; minimum one full session lag before any feature use; every news feature must pass the Phase 6 lag test individually.

---

## Testing requirements

- Unit tests for: PIT store immutability, T-1 cutoffs, cost model, calendar filter, circuit breaker, caps.
- A **leakage tripwire test** in CI: a synthetic dataset with a known future-leak must be caught by the lag test.
- CI runs `make validate` on a fixed 2-year fixture; results must be bit-reproducible.

## Explicit non-goals for v1

No shorting. No options. No intraday reaction to news. No ML-fitted weights. No optimizing the 10:30 time. No live money — paper only until ≥6 months of paper history exists and validation stays green.

## Definition of done

`make ingest && make backtest && make validate && make paper-dry-run` all pass; validation report green; README explains every config parameter; decision log demonstrates ≥1 day where the correct output was CASH.
