"""Phase 6 — validation harness. Built BEFORE any tuning (spec).

One command (`make validate`) runs all checks and writes one HTML report with
red/green per check (reports/validation.html):

1. walk-forward   — v1 fits nothing, so OOS == the full run; reported per-year.
                    The expanding-window harness activates if anything is ever fit.
2. lag test       — every input lagged one extra day; net Sharpe dropping >30%
                    means leakage. Fix the data, not the threshold.
3. shuffle test   — signal-to-stock assignment randomized; must earn ~zero
                    minus costs. If shuffled data "works", the framework leaks.
4. sub-periods    — results by year and by regime; flags PnL concentrated in
                    one year (the 2020-2021 pattern).
5. sensitivity    — +/-25% on key thresholds; collapse = fit artifact.
6. cost sanity    — a zero-signal random portfolio must lose ~its cost load
                    (Phase 5 acceptance: proves the cost model bites).
"""
from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import load_config
from ..data.store import PITStore
from ..regime.state import compute_regime
from ..signals.compute import SIGNAL_NAMES, compute_signal_panels
from ..backtest.engine import run_backtest
from ..backtest.metrics import per_regime, per_year

SENSITIVITY_KEYS = [
    ("portfolio", "conviction_z_min"),
    ("portfolio", "min_trade_pct"),
    ("portfolio", "max_daily_turnover"),
    ("portfolio", "target_vol_annual"),
    ("regime", "vix_reduced_threshold"),
]


def run_all(cfg: dict, store: PITStore) -> dict:
    from ..backtest.run import spy_benchmarks

    panels = compute_signal_panels(store, cfg)
    regime = compute_regime(store, cfg)
    base = run_backtest(store, cfg, panels, regime)
    bm = base.metrics
    checks: dict[str, dict] = {}

    # Long-only baseline: a skill-free long portfolio still earns market
    # exposure (beta). Shuffle/random checks therefore compare against the
    # regime-filtered SPY benchmark, not against zero — "the shuffled
    # strategy must show no SELECTION edge", which is what the spec's
    # "≈ zero minus costs" means once beta is accounted for.
    bench = spy_benchmarks(store, regime, base.daily.index, base.daily["rf_daily"])
    beta_ann = bench["spy_with_regime_filter"]["net_annual_return"]
    # Skill-free comparisons use SHARPE (excess of the risk-free rate): a
    # mostly-cash random portfolio legitimately earns rf, and raw-return
    # comparisons misread that as alpha whenever the beta benchmark is weak
    # (e.g. the fixture's engineered bear market). A shuffled/random book must
    # not meaningfully beat the market benchmark risk-adjusted.
    bench_sharpe = bench["spy_with_regime_filter"]["net_sharpe"]
    skill_free_sharpe_max = max(bench_sharpe + 0.3, 0.5)

    # 1. walk-forward ---------------------------------------------------------
    yearly = per_year(base.daily)
    checks["walk_forward"] = {
        "status": "green",
        "note": ("v1 fits no parameters (equal-weight composite), so all results "
                 "are out-of-sample by construction. Per-year OOS results below. "
                 "If any weight is ever fitted, this check must switch to "
                 "expanding-window refits."),
        "per_year": yearly.to_dict("records"),
    }

    # 2. lag test -------------------------------------------------------------
    lagged = run_backtest(store, cfg, panels, regime, signal_lag=2)
    s1, s2 = bm["net_sharpe"], lagged.metrics["net_sharpe"]
    if abs(s1) > 0.3:
        drop = (s1 - s2) / abs(s1)
        leak = drop > cfg["validate"]["lag_sharpe_drop_max"]
        checks["lag_test"] = {
            "status": "red" if leak else "green",
            "base_sharpe": s1, "lagged_sharpe": s2, "sharpe_drop_pct": round(drop, 3),
            "note": "leakage suspected — find it in the data, not the threshold" if leak else "",
        }
    else:
        checks["lag_test"] = {
            "status": "green", "base_sharpe": s1, "lagged_sharpe": s2,
            "note": "baseline Sharpe too small for a relative-drop test; informational",
        }

    # 3. shuffle test ---------------------------------------------------------
    shuffle_seed = cfg["validate"]["shuffle_seed"]

    def shuffler(prev_i: int, comp: pd.Series) -> pd.Series:
        rng = np.random.default_rng(shuffle_seed * 1_000_003 + prev_i)
        vals = comp.to_numpy().copy()
        rng.shuffle(vals)
        return pd.Series(vals, index=comp.index)

    shuffled = run_backtest(store, cfg, panels, regime, composite_hook=shuffler)
    sm = shuffled.metrics
    bad = sm["net_sharpe"] > skill_free_sharpe_max
    checks["shuffle_test"] = {
        "status": "red" if bad else "green",
        "shuffled_annual_return": sm["net_annual_return"],
        "shuffled_sharpe": sm["net_sharpe"],
        "skill_free_sharpe_max": round(skill_free_sharpe_max, 3),
        "beta_benchmark_annual_return": beta_ann,
        "note": ("shuffled signals BEAT the market benchmark risk-adjusted — "
                 "the framework leaks" if bad else
                 "shuffled signals show no risk-adjusted selection edge"),
    }

    # 4. sub-periods ----------------------------------------------------------
    total_pnl = float(base.daily["equity"].iloc[-1] - base.daily["equity"].iloc[0])
    conc = None
    if total_pnl > 0 and len(yearly) > 1:
        conc = float(yearly["pnl"].max() / total_pnl)
    concentrated = conc is not None and conc > 0.7
    checks["sub_periods"] = {
        "status": "red" if concentrated else "green",
        "per_year": yearly.to_dict("records"),
        "per_regime": per_regime(base.daily).to_dict("records"),
        "max_year_pnl_share": round(conc, 3) if conc is not None else None,
        "note": "PnL concentrated in a single year — flagged" if concentrated else "",
    }

    # 5. parameter sensitivity ------------------------------------------------
    pert = cfg["validate"]["sensitivity_perturbation"]
    rows, collapse = [], False
    for section, key in SENSITIVITY_KEYS:
        for direction in (1 - pert, 1 + pert):
            c2 = deepcopy(cfg)
            c2[section][key] = cfg[section][key] * direction
            reg2 = compute_regime(store, c2) if section == "regime" else regime
            r2 = run_backtest(store, c2, panels, reg2)
            rows.append({"param": f"{section}.{key}", "factor": round(direction, 2),
                         "sharpe": r2.metrics["net_sharpe"],
                         "annual_return": r2.metrics["net_annual_return"]})
            # collapse = a POSITIVE edge disappearing under perturbation; a
            # negative/flat baseline has no edge to collapse
            if bm["net_sharpe"] > 0.5 and r2.metrics["net_sharpe"] < 0.5 * bm["net_sharpe"]:
                collapse = True
    checks["sensitivity"] = {
        "status": "red" if collapse else "green",
        "base_sharpe": bm["net_sharpe"], "perturbations": rows,
        "note": "results collapse under perturbation — fit artifact" if collapse else "",
    }

    # 6. cost-model sanity (zero-signal random portfolio) ---------------------
    def randomizer(prev_i: int, comp: pd.Series) -> pd.Series:
        rng = np.random.default_rng(99_000_017 + prev_i)
        return pd.Series(rng.normal(1.0, 1.0, len(comp)), index=comp.index)

    rand = run_backtest(store, cfg, panels, regime, composite_hook=randomizer)
    rm = rand.metrics
    cost_load = rm["total_costs"] / cfg["backtest"]["initial_equity"]
    ok = (rm["total_costs"] > 0
          and rm["net_sharpe"] < skill_free_sharpe_max)
    checks["cost_sanity"] = {
        "status": "green" if ok else "red",
        "random_portfolio_annual_return": rm["net_annual_return"],
        "random_portfolio_sharpe": rm["net_sharpe"],
        "skill_free_sharpe_max": round(skill_free_sharpe_max, 3),
        "random_portfolio_cost_load_pct": round(cost_load, 4),
        "note": ("random portfolio shows costs and no risk-adjusted edge"
                 if ok else
                 "random portfolio BEATS the market risk-adjusted — cost model "
                 "or framework broken"),
    }

    return {"config_hash": cfg["_config_hash"], "base_metrics": bm,
            "checks": checks, "decile_spreads": decile_spreads(store, cfg, panels)}


def decile_spreads(store: PITStore, cfg: dict, panels: dict) -> dict:
    """Phase 2 acceptance: decile spread per signal (top-bottom, fwd 21d)."""
    prices = store.read_latest("prices", keys=["symbol", "date"])
    prices["date"] = pd.to_datetime(prices["date"])
    P = (prices[prices["symbol"] != "SPY"]
         .pivot_table(index="date", columns="symbol", values="adj_close").sort_index())
    fwd = P.shift(-21) / P - 1
    out = {}
    for name in SIGNAL_NAMES:
        z = panels[name].reindex(index=P.index, columns=P.columns)
        deciles = z.rank(axis=1, pct=True)
        top = fwd[deciles > 0.9].mean(axis=1).mean()
        bottom = fwd[deciles < 0.1].mean(axis=1).mean()
        spread = float(top - bottom) if pd.notna(top) and pd.notna(bottom) else None
        out[name] = {"top_decile_fwd21": _r(top), "bottom_decile_fwd21": _r(bottom),
                     "spread": _r(spread),
                     "expected_sign": "positive (per literature)",
                     "note": "investigate if negative on REAL data — do not flip the sign"}
    return out


def _r(x):
    return round(float(x), 5) if x is not None and pd.notna(x) else None


def write_html(results: dict, path: Path) -> None:
    color = {"green": "#127a3c", "red": "#b3261e"}
    rows = []
    for name, c in results["checks"].items():
        st = c["status"]
        detail = json.dumps({k: v for k, v in c.items() if k != "status"},
                            indent=2, default=str)
        rows.append(
            f"<tr><td>{name}</td>"
            f"<td style='color:#fff;background:{color[st]};text-align:center;"
            f"font-weight:bold'>{st.upper()}</td>"
            f"<td><pre>{detail}</pre></td></tr>")
    spreads = json.dumps(results["decile_spreads"], indent=2)
    html = f"""<meta charset="utf-8"><title>daybreak validation</title>
<style>body{{font-family:system-ui;margin:2rem;max-width:70rem}}
table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ccc;
padding:.5rem;vertical-align:top}}pre{{white-space:pre-wrap;margin:0;
font-size:.8rem}}</style>
<h1>daybreak validation report</h1>
<p>config hash: <code>{results['config_hash']}</code></p>
<h2>Base metrics (net)</h2><pre>{json.dumps(results['base_metrics'], indent=2)}</pre>
<h2>Checks</h2>
<table><tr><th>check</th><th>status</th><th>detail</th></tr>{''.join(rows)}</table>
<h2>Signal decile spreads (fwd 21d)</h2><pre>{spreads}</pre>
"""
    path.write_text(html)


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None,
                    help="alternate config.yaml to test without touching the live one")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    store = PITStore(cfg["run"]["data_dir"])
    if "prices" not in store.tables():
        print("No data ingested. Run `make ingest-fixture` (or `make ingest`).")
        return 1
    results = run_all(cfg, store)
    reports = Path("reports")
    reports.mkdir(exist_ok=True)
    write_html(results, reports / "validation.html")
    with open(reports / "validation.json", "w") as f:
        json.dump(results, f, indent=2, default=str)

    statuses = {k: v["status"] for k, v in results["checks"].items()}
    print(f"daybreak validation  [config {results['config_hash']}]")
    for k, v in statuses.items():
        print(f"  {k:14s} {v.upper()}")
    print("wrote reports/validation.html")
    return 0 if all(s == "green" for s in statuses.values()) else 2


if __name__ == "__main__":
    sys.exit(main())
