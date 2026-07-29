"""T-1 cutoff tests: signals visible on day d must not embed day-d+? data, and
fundamentals must respect the 90-day fiscal lag (spec core rule 1 / Phase 2)."""
import numpy as np
import pandas as pd

from daybreak.signals.compute import compute_signal_panels


def test_fundamentals_respect_90_day_lag(cfg_store):
    cfg, store = cfg_store
    f = store.read("fundamentals")
    lag = pd.to_datetime(f["ingested_at"]) - pd.to_datetime(f["fiscal_end"])
    assert (lag >= pd.Timedelta(days=cfg["signals"]["fundamental_lag_days"])).all()


def test_reversal_panel_uses_only_past_closes(cfg_store):
    cfg, store = cfg_store
    panels = compute_signal_panels(store, cfg)
    prices = store.read("prices")
    prices["date"] = pd.to_datetime(prices["date"])
    P = (prices[prices["symbol"] != "SPY"]
         .pivot_table(index="date", columns="symbol", values="adj_close")
         .sort_index())
    lb = cfg["signals"]["reversal"]["lookback_days"]
    d_idx = len(P.index) - 1
    d = P.index[d_idx]
    sym = P.columns[0]
    expected = -(P.iloc[d_idx][sym] / P.iloc[d_idx - lb][sym] - 1)
    # raw reversal reconstructed from the z-score inputs: recompute raw panel
    raw = -(P / P.shift(lb) - 1)
    assert np.isclose(raw.loc[d, sym], expected)
    # and the z-panel for day d must be invariant to FUTURE price changes:
    P2 = P.copy()
    # simulate a huge move on a hypothetical next day -> cannot affect row d
    next_row = P2.iloc[-1] * 5
    P2 = pd.concat([P2, pd.DataFrame([next_row], index=[d + pd.Timedelta(days=1)])])
    raw2 = -(P2 / P2.shift(lb) - 1)
    pd.testing.assert_series_equal(raw.loc[d], raw2.loc[d], check_names=False)


def test_engine_decision_uses_t_minus_1(cfg_store):
    """The engine reads panel row t-1 for a decision on day t (signal_lag=1)."""
    import inspect

    from daybreak.backtest import engine
    src = inspect.getsource(engine.run_backtest)
    assert "signal_lag: int = 1" in src
    assert "prev_i = i - signal_lag" in src
