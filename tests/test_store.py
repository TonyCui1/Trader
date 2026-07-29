"""PIT store: immutability, idempotency, as-of discipline (spec core rule 1)."""
import pandas as pd
import pytest

from daybreak.data.store import PITStore


def _df(vals, ingested):
    return pd.DataFrame({
        "symbol": ["A"] * len(vals), "value": vals,
        "source_ts": pd.Timestamp("2024-01-01"),
        "ingested_at": ingested,
    })


def test_append_is_idempotent(tmp_path):
    store = PITStore(tmp_path)
    df = _df([1.0, 2.0], pd.Timestamp("2024-01-02"))
    assert store.append("t", df) == 2
    h1 = store.table_hash("t")
    assert store.append("t", df) == 0          # re-run: zero new rows
    assert store.table_hash("t") == h1         # byte-identical table


def test_no_update_or_delete_api(tmp_path):
    store = PITStore(tmp_path)
    for name in ("update", "delete", "overwrite", "truncate"):
        assert not hasattr(store, name)


def test_as_of_read_hides_future_rows(tmp_path):
    store = PITStore(tmp_path)
    early = _df([1.0], pd.Timestamp("2024-01-02"))
    late = _df([99.0], pd.Timestamp("2024-06-01"))
    store.append("t", early)
    store.append("t", late)
    visible = store.read("t", as_of=pd.Timestamp("2024-03-01"))
    assert visible["value"].tolist() == [1.0]
    # exact boundary: ingested_at == as_of is NOT visible (strictly before)
    boundary = store.read("t", as_of=pd.Timestamp("2024-01-02"))
    assert boundary.empty


def test_missing_pit_columns_rejected(tmp_path):
    store = PITStore(tmp_path)
    with pytest.raises(ValueError):
        store.append("t", pd.DataFrame({"x": [1]}))
