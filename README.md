# daybreak

A daily 10:30am ET systematic long-only US equity trading model, built to the
spec in [`trading-system-spec.md`](trading-system-spec.md) with the gap
analysis in [`PLAN.md`](PLAN.md). Python 3.11+.

**v1 is paper-only.** No live money until ≥6 months of paper history exists and
validation stays green (spec non-goal). No shorting, no options, no ML-fitted
weights, and the 10:30 decision time is hardcoded — never optimized.

## Quickstart (no API keys needed)

```bash
pip install -r requirements.txt
make ingest-fixture   # deterministic synthetic dataset + quarantine log
make backtest         # net-only metrics + SPY benchmarks -> reports/
make validate         # 6 checks, red/green -> reports/validation.html
make paper-dry-run    # today's decision + orders, simulated fills, decision log
make test             # unit tests incl. the leakage tripwire
```

Live data instead of the fixture: set `ALPACA_API_KEY` / `ALPACA_SECRET_KEY`
and run `make ingest`. Prices come from Alpaca, fundamentals from yfinance
(**flagged NOT point-in-time** — structured for a PIT vendor swap), RF/factors
from the Ken French library, VIX + HYG/LQD from yfinance.

## Core rules (from the spec — enforced in code)

1. **Point-in-time everything.** Append-only store (`PITStore` has no
   update/delete API); every row carries `ingested_at` + `source_ts`; models
   only see rows with `ingested_at < decision_time`.
2. **No trade is the default.** Output is BUY/SELL/HOLD/CASH; CASH needs no
   justification, a trade does.
3. **One decision time, 10:30:00 ET,** hardcoded in `daybreak/mcal.py`.
4. **All performance numbers are net.** Costs baked into every backtest;
   gross is never displayed.
5. **Backtest Sharpe > 1.5 prints a bug-warning banner** until walk-forward
   and lag tests pass.
6. **Config over code.** All thresholds in `config.yaml`; every run logs the
   config hash.

## Architecture

```
daybreak/
├── config.py, mcal.py    # config + hash; NYSE calendar; hardcoded 10:30 ET
├── data/                 # PITStore (DuckDB), quality gate, universe, sources/, synthetic fixture
├── signals/              # 6 signals -> winsorized z-scores -> equal-weight composite
├── regime/               # ON / REDUCED / OFF from SPY 200dma, HYG/LQD, VIX
├── portfolio/            # sizing, caps, blackout, conviction, turnover brake, circuit breaker
├── backtest/             # event-driven simulator, cost model, net metrics, benchmarks
├── validate/             # walk-forward, lag, shuffle, sub-period, sensitivity, cost sanity
├── execute/              # Alpaca paper / dry-run broker, 10:25 runner, reconciliation
└── report/               # hash-chained immutable decision log
tests/                    # unit tests + leakage tripwire + bit-reproducibility
```

## Config reference (`config.yaml`)

Every parameter, per the definition of done:

### run
| key | meaning |
|---|---|
| `seed` | RNG seed for the synthetic fixture (bit-reproducibility) |
| `data_dir` | location of the DuckDB point-in-time store + decision log |

### universe
| key | meaning |
|---|---|
| `size` | top-N stocks by trailing dollar volume (Russell 1000 proxy) |
| `adv_lookback_days` | trailing window for the dollar-volume ranking |
| `fixture_size` | universe size used by the synthetic fixture/CI |

### signals
| key | meaning |
|---|---|
| `winsor_z` | cross-sectional z-scores clipped at ± this value |
| `fundamental_lag_days` | fundamentals usable only this many days after fiscal period end |
| `momentum.lookback_days` / `skip_days` | 12-1 momentum: ~12-month return skipping the most recent month |
| `reversal.lookback_days` | trailing 1-month return, inverted |
| `lowvol.lookback_days` | realized-vol window, inverted |
| `pead.decay_days` | SUE signal decays linearly to zero over this many trading days |
| `pead.sue_lookback_quarters` | trailing quarters for the SUE denominator std |

### regime
| key | meaning |
|---|---|
| `spy_ma_days` | SPY below this MA → REDUCED |
| `credit_ma_days` | HYG/LQD below its own MA (while SPY < MA) → OFF |
| `vix_reduced_threshold` | VIX above this forces at least REDUCED |

### portfolio
| key | meaning |
|---|---|
| `max_positions` | max concurrent names (20) |
| `top_fraction` | candidate pool = top decile of composite (0.10) |
| `target_vol_annual` | portfolio vol target for inverse-vol sizing |
| `vol_lookback_days` | window for per-name realized vol |
| `max_weight_per_name` | per-name cap (8%) |
| `max_weight_per_sector` | per-sector cap (25%) |
| `earnings_blackout_days` | no new/added position if earnings within N trading days |
| `conviction_z_min` | increases require composite z above this |
| `min_trade_pct` | any trade must move the position by more than this fraction of portfolio |
| `max_daily_turnover` | turnover brake: max fraction of portfolio traded per day |
| `circuit_breaker_dd` | drawdown from high-water mark that flattens + halts |
| `restart_after_halt` | manual flag a human must set to resume after a halt |

### costs
| key | meaning |
|---|---|
| `slippage_k` | slippage = k·√(order$/ADV$); recalibrated from the paper slippage log |
| `sec_fee_per_dollar_sold` / `taf_fee_per_share_sold` | regulatory fees on sells |
| `commission_per_trade` | $0 at Alpaca, kept configurable |
| `tax_rate_short_term` | rate for the short-term-gains tax estimate |

### backtest
| key | meaning |
|---|---|
| `start` / `end` | simulation window (end null = latest) |
| `initial_equity` | starting cash |
| `sharpe_warning_level` | net Sharpe above this prints the treat-as-a-bug banner |

### validate
| key | meaning |
|---|---|
| `lag_sharpe_drop_max` | lag test fails (leakage) if Sharpe drops more than this fraction |
| `sensitivity_perturbation` | ±fraction applied to each threshold in the sensitivity check |
| `shuffle_seed` | seed for the shuffle test's randomization |

### execute
| key | meaning |
|---|---|
| `paper` | must stay `true` in v1 — the Alpaca adapter refuses otherwise |
| `limit_offset_pct` | limit price = last trade ± this (0.3%) |
| `cancel_after_minutes` | unfilled orders cancelled after this window |
| `kill_switch_file` | if this file exists, no order is ever submitted |

## Paper trading (Phase 7)

Schedule (cron, times in **America/New_York**):

```
25 10 * * 1-5  cd /path/to/repo && make ingest && python3 -m daybreak.execute.runner
0  11 * * 1-5  cd /path/to/repo && python3 -m daybreak.execute.reconcile
```

- Orders carry deterministic `client_order_id`s — a crashed-and-rerun job
  cannot double-submit.
- Any error → the decision is HOLD and the failure is logged; never trade on
  broken inputs. Wire the cron to a heartbeat monitor (e.g. healthchecks.io)
  so a silently-dead job pages you (PLAN.md gap 6).
- Kill switch: `touch KILL_SWITCH` stops all order submission immediately.
- The decision log (`pit_data/decisions.jsonl`) is append-only and
  hash-chained; `DecisionLog.verify_chain()` proves it untampered.

## Known data caveats (from PLAN.md — read before trusting results)

- **Historical survivorship bias:** free sources only cover currently-listed
  names; treat historical backtests as upper bounds (gap 1).
- **IEX-only free feed:** thin 10:30 bars for small names; consider Alpaca's
  SIP subscription before paper trading (gap 2).
- **yfinance fundamentals are not point-in-time** (spec flags this); the
  loader is a seam for a PIT vendor (Sharadar/Norgate/CRSP).

## Phase 8 (news layer)

Deliberately NOT built. The spec gates it on Phases 1–7 passing on real data
first (regime context, veto list, explainability only — never a model input).

## Definition of done status

`make ingest-fixture && make backtest && make validate && make paper-dry-run`
all pass; validation report green; this README documents every config
parameter; the backtest daily log contains CASH days (the fixture's engineered
crash forces regime OFF), and a kill-switch HALT record is demonstrated in the
decision log.
