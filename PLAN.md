# Daybreak — Execution Plan (reconciled with `trading-system-spec.md`)

The spec defines a daily 10:30am ET systematic long-only US equity model
("daybreak"). This plan confirms the spec against a research pass on common
investing/backtesting failure modes, lists the gaps that still need decisions or
mitigations, and lays out the build order.

**Overall verdict:** the spec is unusually disciplined and already covers most of
the classic killers — point-in-time storage, T-1 cutoffs, net-only performance,
lag/shuffle/walk-forward validation, regime gating, circuit breakers, cost
modeling, and "CASH is the default." The gaps below are mostly about *data
sourcing reality* and *live operations*, not strategy design.

---

## 1. Pitfall coverage check

Mapping the research-derived checklist onto the spec:

| Known pitfall | Spec coverage |
|---|---|
| Look-ahead bias | ✅ Core rule 1 (PIT store), T-1 cutoffs, 90-day fundamental lag, lag test (Phase 6.2) |
| Overfitting / multiple testing | ✅ Equal-weight composite (no fitted weights), Sharpe >1.5 = bug, parameter sensitivity test, signal cap of 10 |
| Ignoring costs | ✅ Core rule 4 (net-only), sqrt-impact cost model, random-portfolio cost sanity check |
| Small-sample false confidence | ✅ Sub-period report, walk-forward, 5-year backtest + 6-month paper gate |
| Emotional overrides | ✅ Hardcoded decision time, config-over-code, manual-restart-only circuit breaker |
| No predefined exits / risk limits | ✅ Vol targeting, caps, turnover brake, 12% drawdown breaker |
| Regime blindness | ✅ Phase 3, plus SPY-with-regime-filter benchmark |
| Taxes | ✅ Short-term-gains estimate in backtest metrics |
| Survivorship bias | ⚠️ Partially — see Gap 1 |
| Operational failure modes | ⚠️ Partially — see Gaps 6–10 |

## 2. Gap analysis — what the spec misses or leaves undecided

Ordered by how much they can silently corrupt results:

1. **Historical survivorship bias is unsolved.** The spec stores the universe
   point-in-time *going forward*, but the 5-year historical backtest is built
   from Alpaca/yfinance data, which only has *currently listed* names. The
   backtest universe therefore excludes stocks that delisted in 2021–2026 —
   this inflates long-only momentum/quality results specifically.
   *Mitigation:* (a) state the bias in every backtest report banner alongside
   the Sharpe warning; (b) treat historical results as upper bounds and weight
   walk-forward + paper evidence more; (c) budget line-item for Sharadar/Norgate
   (~$40–90/mo) as the designated "PIT vendor swap" the spec already provides
   a seam for.

2. **Alpaca free data is IEX-only (~2–3% of consolidated volume).** The
   10:25–10:30 minute-bar VWAP that drives fills can be thin or missing for
   smaller names in a 1000-stock universe, biasing the cost model in *either*
   direction. *Mitigation:* validate IEX minute bars against daily consolidated
   OHLCV; consider Alpaca's $9/mo SIP feed before Phase 7 paper trading;
   quarantine symbols whose 10:30 bar has zero prints.

3. **PEAD needs earnings *estimates* — the hardest data to get free and PIT.**
   yfinance consensus estimates are current-snapshot, not point-in-time, and
   silently restated. *Mitigation:* define SUE against a seasonal random walk
   (YoY EPS change ÷ trailing std of changes) which requires only *actuals*,
   is standard in the PEAD literature, and stays PIT-safe. Keep the
   estimates-based version behind the vendor-swap seam.

4. **Sector data source is unspecified**, but two components depend on it
   (value sector-neutralization, 25% sector caps). GICS is licensed.
   *Mitigation:* use yfinance sector/industry, snapshot it monthly into the PIT
   store (sector membership drifts), and treat it as another
   flagged-not-really-PIT source like fundamentals.

5. **Cash rate for live use:** Ken French RF publishes with weeks of lag —
   fine for backtests, useless live. *Mitigation:* ^IRX (13-week T-bill) or
   BIL for the live/paper cash rate; French RF for the historical simulator.

6. **No alerting/monitoring for the 10:25 job.** A cron that silently fails at
   10:24 means no decision, no log, and nobody notices. *Mitigation:* the
   scheduler must emit a heartbeat (healthchecks.io or similar free ping);
   missed heartbeat or any exception → alert + the day's decision defaults to
   HOLD (never retry into stale data). Add "job did not run" to the decision
   log as an explicit state.

7. **Order idempotency is missing.** Spec makes *ingest* idempotent but not
   *order submission* — a crashed-and-rerun 10:30 job could double-submit.
   *Mitigation:* deterministic `client_order_id` = hash(date, symbol, side,
   qty); Alpaca rejects duplicates. Reconciliation job (already specced)
   verifies.

8. **Fractional vs. whole shares conflicts with the limit-order rule.** Alpaca
   fractional orders can't be limit orders; with 20 positions, 8% caps, and a
   1%-of-portfolio minimum trade, whole-share rounding needs a stated account
   size floor (~$25k+ for clean sizing). *Decision needed:* whole shares +
   limit orders (spec-compliant, needs larger paper account) vs. fractional +
   marketable orders. Recommend whole shares — paper account size is free.

9. **Market calendar edge cases.** 10:30 ET is safe on early-close days (1pm
   close), but the job must use an exchange calendar (e.g.
   `pandas-market-calendars`), run in `America/New_York` explicitly (never
   server-local time), and skip holidays. DST bugs here are classic.

10. **Mid-hold delistings/halts.** A held name that halts or delists can't be
    exited by the normal path. *Mitigation:* reconciliation flags untradeable
    positions; decision log records a forced-HOLD state; delisting proceeds
    handled as cash events in the PIT store.

11. **Benchmark tax parity.** The strategy's returns get a short-term-tax
    haircut; SPY buy-and-hold is mostly unrealized. Reports should show both
    pre-tax and post-tax rows for strategy *and* benchmarks, or the comparison
    quietly flatters the benchmark.

12. **Repo naming:** spec says repo `daybreak`; this repo is `Trader`. Plan:
    use the spec's `daybreak/` directory layout at this repo's root. Rename
    the repo later if it matters.

## 3. Build order

Follow the spec's phases in order; gates are the spec's acceptance criteria.

| Step | Scope | Gap items folded in |
|---|---|---|
| 0. Scaffold | Repo layout, `config.yaml` + config-hash logging, CI skeleton, market calendar util | 9, 12 |
| 1. Data layer | PIT DuckDB/Parquet store, Alpaca + yfinance + French + VIX/HYG/LQD ingestors, quality gate, universe builder | 1, 2, 4, 5 |
| 2. Signals | 6 signals, z-scoring, T-1 cutoff enforcement, decile plots | 3 (SUE definition) |
| 3. Regime | 3-state machine, exactly as specced | — |
| 4. Portfolio | Sizing, caps, calendar filter, conviction threshold, turnover brake, circuit breaker | 8 |
| 5. Backtester | 10:30 VWAP fills, cost model, tax estimate, benchmarks | 11 |
| 6. Validation | `make validate` → HTML red/green report; leakage tripwire in CI | — |
| 7. Paper execution | Scheduler, kill switch, reconciliation, decision log | 6, 7, 10 |
| 8. News layer | Only after 1–7 green | — |

Test-first items the spec already mandates: PIT immutability, T-1 cutoffs, cost
model, calendar filter, circuit breaker, caps, leakage tripwire.

## 4. Decisions to confirm (small, non-blocking for Phases 0–2)

1. Whole shares + limit orders (recommended) or fractional + marketable orders? (Gap 8)
2. Paper account notional size (recommend $100k default — Alpaca's default). 
3. Spend $9/mo on Alpaca SIP data at Phase 7? (Gap 2 — recommend yes)
4. Budget for a PIT fundamentals/universe vendor later? (Gap 1 — defer until
   validation shows the strategy is worth it)

## 5. Definition of done (from spec, unchanged)

`make ingest && make backtest && make validate && make paper-dry-run` all pass;
validation report green; README documents every config parameter; decision log
shows ≥1 day where the correct output was CASH.

---

## Appendix — research sources behind the pitfall checklist

- [QuantStart — Successful backtesting of algorithmic trading strategies](https://www.quantstart.com/articles/Successful-Backtesting-of-Algorithmic-Trading-Strategies-Part-I/)
- [Gainium — Common backtesting mistakes: why strategies fail live](https://gainium.io/blog/common-backtesting-problems)
- [For Traders — How to avoid bias in backtesting](https://www.fortraders.com/blog/how-to-avoid-bias-in-backtesting)
- [Starqube — Critical pitfalls of backtesting trading strategies](https://starqube.com/backtesting-investment-strategies/)
- [Aron Groups — Overfitting in trading models](https://arongroups.co/forex-articles/overfitting-in-trading/)
- [LuxAlgo — Risk management strategies for algo trading](https://www.luxalgo.com/blog/risk-management-strategies-for-algo-trading/)
- [Nurp — 7 risk management strategies for algorithmic trading](https://nurp.com/algorithmic-trading-blog/7-risk-management-strategies-for-algorithmic-trading/)
- [CNBC — 7 biggest investing mistakes according to financial experts](https://www.cnbc.com/select/biggest-investing-mistakes/)
- [Saxo — Common investing mistakes beginners make](https://www.home.saxo/learn/guides/start-investing/the-biggest-mistakes-investors-make)
- [Equifund — 5-step guide to investment due diligence](https://equifund.com/blog/investment-due-diligence/)
