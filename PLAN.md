# Stock Trading Model — Execution Plan

> **Status note:** This plan was drafted before the original spec file
> (`trading-system-spec.md`) was available in this repository — the file lives on a
> local machine and was not uploaded to the session. The plan below covers the full
> lifecycle of a trading system using industry best practices. When the spec is added
> to the repo, reconcile it against the **Open Questions** section at the bottom and
> adjust the roadmap accordingly.

---

## 1. Goals and Guiding Principles

Build a systematic stock trading model that goes from idea → validated strategy →
paper trading → (optionally) live execution, with risk management and honest
evaluation built in from day one.

Principles that everything below follows:

1. **Validation before capital.** No strategy touches real money until it survives
   out-of-sample testing and a paper-trading period.
2. **Risk management is part of the strategy, not an afterthought.** Position sizing,
   drawdown limits, and kill switches are first-class components.
3. **Assume the backtest is lying until proven otherwise.** Most backtests overstate
   performance; the process is designed to catch that.
4. **Costs are real.** Commissions, slippage, spread, and borrow costs are modeled in
   every simulation.
5. **Simple and understood beats complex and opaque.** A common investor failure is
   putting money into something not fully understood — that applies doubly to your
   own model.

## 2. Phased Roadmap

| Phase | Deliverable | Gate to next phase |
|-------|-------------|--------------------|
| 0. Scope & spec | Written requirements (universe, horizon, capital, broker) | Spec agreed |
| 1. Data layer | Clean, point-in-time historical data pipeline | Data validated |
| 2. Research framework | Backtesting engine + cost model | Engine verified on known cases |
| 3. Strategy development | Candidate strategies with in-sample results | Passes out-of-sample test |
| 4. Robustness testing | Walk-forward, sensitivity, regime analysis | Metrics stable across tests |
| 5. Paper trading | Live-data simulation vs. backtest expectations | Live results track backtest |
| 6. Live deployment (optional) | Small-capital live trading with monitoring | Sustained tracking + risk limits hold |
| 7. Operations | Monitoring dashboard, alerting, periodic review | Ongoing |

Do not skip gates. Deploying without real-market paper testing is one of the most
frequently cited critical mistakes in algorithmic trading.

## 3. Phase 0 — Scope Decisions (must be settled first)

These decisions shape everything downstream. The spec should answer each:

- **Universe:** Which stocks? (e.g., S&P 500, Russell 3000, specific sectors.) Broad
  enough to diversify — concentration in a handful of names is a classic error.
- **Horizon / style:** Intraday, swing (days–weeks), or position (months)? This
  determines data granularity (tick/minute/daily) and infrastructure cost.
- **Strategy family:** Momentum, mean reversion, fundamental factor, ML-driven, or a
  blend.
- **Capital and constraints:** Account size, margin use (recommend none initially),
  long-only vs. long/short, tax considerations (short-term gains are taxed as income).
- **Broker/API:** Alpaca (simple, free paper trading), Interactive Brokers (robust,
  more complex), or others. Pick one with a good paper-trading API.
- **Regulatory note:** Personal-account trading is fine; pattern day trader rules
  (>3 day trades/5 days requires $25k equity) may constrain intraday styles.

## 4. Phase 1 — Data Layer

**The #1 source of inflated backtests is bad data.** Requirements:

- **Point-in-time, survivorship-bias-free data.** Testing only on stocks that exist
  today inflates results dramatically — one demonstration showed survivor-only
  average returns of 12.0% vs. 0.8% when delisted stocks were included. Use a
  dataset that includes delisted securities (e.g., Norgate, Sharadar, CRSP) or at
  minimum acknowledge and bound this bias.
- **Corporate-action adjustment:** splits and dividends handled consistently
  (adjusted for signals, unadjusted + explicit dividends for P&L).
- **Fundamental data must be as-reported with correct availability dates** (a Q4
  report isn't known on Dec 31 — it's known when filed weeks later). Using restated
  or prematurely-known data is look-ahead bias.
- **Validation suite:** automated checks for gaps, outliers, zero-volume days, and
  cross-source spot checks before any data enters research.

Suggested stack: Python, pandas/polars, Parquet or DuckDB storage, a thin ingestion
layer per data vendor so vendors are swappable.

## 5. Phase 2 — Research & Backtesting Framework

Build or adopt (e.g., vectorbt, backtrader, Zipline-reloaded, or a purpose-built
engine) with these non-negotiables:

- **No look-ahead:** signals computed at time *t* execute at *t+1* open (or later).
  Enforce structurally in the engine, not by convention.
- **Cost model:** commission + spread + slippage (scale slippage with position size
  relative to average daily volume). Fees and slippage routinely turn marginally
  profitable backtests into consistent live losers.
- **Realistic fills:** no filling limit orders at prices never traded through; cap
  participation at a small % of volume.
- **Deterministic and reproducible:** same inputs → same results; versioned configs.
- **Engine verification:** test the engine itself on strategies with known outcomes
  (e.g., buy-and-hold SPY must match actual index returns net of costs).

## 6. Phase 3 — Strategy Development

Process discipline matters more than the specific signal:

1. **Hypothesis first.** Write down *why* an edge should exist (behavioral,
   structural, risk-premium) before touching data. Data-mined patterns with no
   economic rationale are overfitting bait.
2. **Split data before research:** e.g., in-sample (older 60%), validation (20%),
   final out-of-sample holdout (most recent 20%) touched **once**, at the end.
3. **Limit parameters.** Every free parameter is a chance to overfit. Prefer
   strategies with ≤3–4 parameters; distrust anything that only works at one
   precise setting.
4. **Baselines:** every candidate must beat buy-and-hold SPY *risk-adjusted* (Sharpe,
   max drawdown), net of costs and realistic taxes — otherwise indexing wins.
5. **Track every experiment** (a simple experiment log). Running 100 variations and
   reporting the best one is multiple-testing bias — the "best" is likely luck.

If using ML: walk-forward (purged, embargoed) cross-validation only, simple models
before deep ones, and features must have the same economic-rationale bar.

## 7. Phase 4 — Robustness Testing (where most projects should die)

A strategy graduates only if it survives all of:

- **Walk-forward analysis:** re-fit on rolling windows, test on the next window;
  performance must be reasonably consistent across windows.
- **Parameter sensitivity:** performance surface should be a plateau, not a spike.
  ±20% parameter perturbation shouldn't destroy returns.
- **Regime analysis:** check bull (2016–2019), crash (2020, 2022), high-rate
  (2023–2024), and recent regimes separately. Know *when* it loses.
- **Cost stress test:** double the assumed slippage — still profitable?
- **Monte Carlo trade reshuffling:** bootstrap trade sequences to get a drawdown
  distribution, not a single historical number. Small sample sizes create false
  statistical confidence — demand enough trades (hundreds, not dozens) for
  significance.
- **Final holdout:** run once on the untouched out-of-sample set. If it fails, the
  strategy is dead — do not iterate against the holdout.

## 8. Risk Management (built alongside, not after)

- **Position sizing:** risk a fixed 1–2% of equity per trade, scaled inversely to
  volatility (e.g., ATR-based) so dollar risk per position is roughly constant. If
  using Kelly-style sizing, use fractional Kelly (20–30% of full Kelly).
- **Portfolio limits:** max position size per name (e.g., 10%), max sector exposure,
  max gross exposure, max number of concurrent positions.
- **Drawdown circuit breakers:** e.g., halve sizing at −10% from equity peak, halt
  trading and review at −15–20%. Automated, not discretionary.
- **Kill switch:** one command/button that flattens all positions and stops the
  system; plus automatic halt on anomalies (data feed failure, order rejects,
  fill-price deviation beyond threshold).
- **Correlation awareness:** ten positions that all move with the same factor are
  one position. Monitor correlation to market indices and among holdings.
- **Predefined exits:** every position has stop/target/time-based exit rules defined
  at entry — removing in-the-moment emotional decisions is exactly what a
  systematic approach is for.

## 9. Phase 5 — Paper Trading

- Run the *production* code path against live data via the broker's paper API for a
  meaningful period (suggest ≥2–3 months or ≥30–50 trades, whichever is longer).
- **Compare live-sim results to backtest expectations daily:** fill quality, signal
  timing, P&L tracking error. Divergence means bugs or unrealistic assumptions —
  investigate before proceeding.
- Build the monitoring dashboard now: equity curve, drawdown, exposure, open
  positions, rolling Sharpe, VaR, correlation to SPY, system health (data
  freshness, order status), with alerting (email/Slack) on limit breaches.

## 10. Phase 6 — Live Deployment (optional, gated)

- Start with a small fraction of intended capital (e.g., 10–25%); scale up only
  after live results track paper results for a further evaluation period.
- Operational safeguards: idempotent order logic, reconciliation of internal state
  vs. broker state every cycle, full audit log of every signal/order/fill/error,
  graceful behavior on restarts and API outages.
- **Human failure modes to guard against** (these sink live traders more than bad
  models): overriding the system after a losing streak (panic selling), adding
  discretionary trades on hot names (trend chasing), turning the system off/on
  based on recent performance (market timing). Write a personal operating agreement:
  under what conditions you intervene, and log every intervention.

## 11. Ongoing Operations

- Weekly: review performance vs. expectations, check risk-limit headroom.
- Monthly: data-quality audit, cost-model recalibration vs. actual fills.
- Quarterly: regime check — is the edge decaying? Strategies decay as markets adapt;
  a persistent gap between live and backtest performance beyond noise is a
  retirement signal.
- Never stop monitoring: "set and forget" is a documented investor error; markets
  evolve and a strategy that worked can quietly stop working.

## 12. Commonly Missed Items (research-derived checklist)

From published post-mortems and practitioner guides — the things people miss:

- [ ] Survivorship bias in the stock universe (use delisted-inclusive data)
- [ ] Look-ahead bias in fundamentals (as-reported dates, not fiscal dates)
- [ ] Realistic slippage that scales with trade size vs. liquidity
- [ ] Taxes — short-term capital gains materially change net returns
- [ ] Multiple-testing bias — logging *all* experiments, not just winners
- [ ] Beating a risk-adjusted buy-and-hold baseline, not just being profitable
- [ ] Enough trades for statistical significance (small samples lie)
- [ ] Correlation/concentration — diversification in name only is not diversification
- [ ] Emotional overrides of the system (the human is part of the system)
- [ ] Fees eroding returns — broker, data, and infrastructure costs in the P&L
- [ ] An explicit, written exit strategy for every position *and* for the strategy itself
- [ ] Kill switch and anomaly circuit breakers tested before live deployment
- [ ] Reconciliation between system state and broker state
- [ ] A decay/retirement criterion decided in advance, not under stress

## 13. Proposed Repository Structure

```
Trader/
├── PLAN.md                  # this file
├── trading-system-spec.md   # ← add the original spec here
├── data/                    # ingestion, validation, storage (gitignore raw data)
├── research/                # notebooks, experiment log
├── engine/                  # backtesting engine + cost model
├── strategies/              # strategy implementations (one module each)
├── risk/                    # sizing, limits, circuit breakers
├── execution/               # broker adapters, order management, reconciliation
├── monitoring/              # dashboard, alerting
└── tests/                   # engine verification, data validation, unit tests
```

## 14. Open Questions — reconcile with the original spec

1. Target universe, holding period, and strategy family?
2. Capital size, long-only vs. long/short, margin?
3. Which broker/API, and is live trading actually in scope or paper-only?
4. Data budget (free sources like yfinance have survivorship/quality issues; paid
   point-in-time data costs $30–100+/mo)?
5. ML-based prediction or rules-based signals?
6. Latency requirements (daily rebalance is vastly simpler/cheaper than intraday)?
7. Any specific requirements from the spec not covered above?

## Sources

- [CNBC — 7 biggest investing mistakes according to financial experts](https://www.cnbc.com/select/biggest-investing-mistakes/)
- [Citizens Bank — 8 common investing mistakes](https://www.citizensbank.com/learning/8-common-investing-mistakes.aspx)
- [Saxo — Common investing mistakes beginners make](https://www.home.saxo/learn/guides/start-investing/the-biggest-mistakes-investors-make)
- [Nationwide — Common investing errors](https://www.nationwide.com/lc/resources/investing-and-retirement/articles/common-investing-errors)
- [QuantStart — Successful backtesting of algorithmic trading strategies](https://www.quantstart.com/articles/Successful-Backtesting-of-Algorithmic-Trading-Strategies-Part-I/)
- [Gainium — Common backtesting mistakes: why strategies fail live](https://gainium.io/blog/common-backtesting-problems)
- [For Traders — How to avoid bias in backtesting](https://www.fortraders.com/blog/how-to-avoid-bias-in-backtesting)
- [Starqube — Critical pitfalls of backtesting trading strategies](https://starqube.com/backtesting-investment-strategies/)
- [Aron Groups — Overfitting in trading models](https://arongroups.co/forex-articles/overfitting-in-trading/)
- [LuxAlgo — Risk management strategies for algo trading](https://www.luxalgo.com/blog/risk-management-strategies-for-algo-trading/)
- [Yuktix — Risk management in algorithmic trading best practices](https://yuktix.ai/risk-management-in-algorithmic-trading-best-practices-for-traders/)
- [Nurp — 7 risk management strategies for algorithmic trading](https://nurp.com/algorithmic-trading-blog/7-risk-management-strategies-for-algorithmic-trading/)
- [Equifund — 5-step guide to investment due diligence](https://equifund.com/blog/investment-due-diligence/)
- [SignalX — Investment due diligence guide](https://signalx.ai/investment-due-diligence/)
