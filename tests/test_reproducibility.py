"""Bit-reproducibility (spec CI requirement): re-running ingest on the fixture
produces byte-identical tables, and re-running the backtest produces an
identical equity curve."""
import pandas as pd

from daybreak.backtest.engine import run_backtest
from daybreak.config import load_config
from daybreak.data.ingest import ingest_fixture
from daybreak.regime.state import compute_regime
from daybreak.signals.compute import compute_signal_panels


def test_ingest_rerun_is_byte_identical(cfg_store):
    cfg, store = cfg_store
    hashes_before = {t: store.table_hash(t) for t in store.tables()}
    ingest_fixture(cfg)  # full re-run against the same store
    hashes_after = {t: store.table_hash(t) for t in store.tables()}
    assert hashes_before == hashes_after


def test_backtest_is_deterministic(cfg_store):
    cfg, store = cfg_store
    panels = compute_signal_panels(store, cfg)
    regime = compute_regime(store, cfg)
    r1 = run_backtest(store, cfg, panels, regime)
    r2 = run_backtest(store, cfg, panels, regime)
    pd.testing.assert_frame_equal(r1.daily, r2.daily)
    assert r1.metrics == r2.metrics


def test_fresh_store_same_config_same_hashes(tmp_path):
    cfg = load_config()
    cfg["run"]["data_dir"] = str(tmp_path / "pit_a")
    cfg["universe"]["fixture_size"] = 40
    store_a = ingest_fixture(cfg)
    cfg["run"]["data_dir"] = str(tmp_path / "pit_b")
    store_b = ingest_fixture(cfg)
    for t in store_a.tables():
        assert store_a.table_hash(t) == store_b.table_hash(t), t
