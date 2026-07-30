"""Point-in-time store: DuckDB-backed, append-only, idempotent.

Spec core rule 1:
- Append-only. No update or delete API exists on this class.
- Every row carries `ingested_at` (when WE first saw it) and `source_ts`
  (the vendor's claim).
- Reads are as-of: only rows with ingested_at < the requested decision time
  are visible to models.

Idempotency: each row gets a `row_hash` over its business columns
(everything except ingested_at). Re-appending the same data is a no-op,
so a re-run produces byte-identical tables (Phase 1 acceptance).
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import duckdb
import pandas as pd


class PITStore:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "daybreak.duckdb"
        self.con = duckdb.connect(str(self.db_path))

    # -- write ---------------------------------------------------------------

    def append(self, table: str, df: pd.DataFrame) -> int:
        """Append rows; dedupe on row_hash. Returns number of NEW rows stored.

        df must contain `source_ts` and `ingested_at` columns (UTC-naive or
        tz-aware timestamps). Backfills pass historical ingested_at values that
        honestly reflect when the data could first have been known.
        """
        if df.empty:
            return 0
        for col in ("source_ts", "ingested_at"):
            if col not in df.columns:
                raise ValueError(f"append({table}): missing required column {col!r}")
        df = df.copy()
        df["row_hash"] = _row_hashes(df.drop(columns=["ingested_at"]))

        self.con.register("_incoming", df)
        if not self._table_exists(table):
            self.con.execute(f"CREATE TABLE {table} AS SELECT * FROM _incoming")
            new = len(df)
        else:
            before = self.con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            self.con.execute(
                f"INSERT INTO {table} SELECT * FROM _incoming i "
                f"WHERE NOT EXISTS (SELECT 1 FROM {table} t WHERE t.row_hash = i.row_hash)"
            )
            new = self.con.execute(f"SELECT count(*) FROM {table}").fetchone()[0] - before
        self.con.unregister("_incoming")
        return new

    # -- read ----------------------------------------------------------------

    def read(self, table: str, as_of: pd.Timestamp | None = None,
             where: str = "") -> pd.DataFrame:
        """Read a table. If as_of is given, only rows ingested BEFORE it are
        returned — this is the point-in-time discipline; there is no way to
        read future rows through this API."""
        if not self._table_exists(table):
            return pd.DataFrame()
        q = f"SELECT * FROM {table}"
        clauses = []
        params: list = []
        if as_of is not None:
            clauses.append("ingested_at < ?")
            params.append(pd.Timestamp(as_of).tz_localize(None)
                          if pd.Timestamp(as_of).tzinfo is None
                          else pd.Timestamp(as_of).tz_convert("UTC").tz_localize(None))
        if where:
            clauses.append(where)
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        df = self.con.execute(q, params).df()
        return df.drop(columns=["row_hash"], errors="ignore")

    def read_latest(self, table: str, keys: list[str],
                    as_of: pd.Timestamp | None = None) -> pd.DataFrame:
        """Read with 'latest version wins' semantics: when re-ingestion stored
        multiple versions of the same business row (e.g. restated adjusted
        prices), keep only the newest ingested_at per key. The full history
        stays in the table — this is a view, not a mutation."""
        df = self.read(table, as_of=as_of)
        if df.empty:
            return df
        return (df.sort_values("ingested_at")
                .drop_duplicates(subset=keys, keep="last")
                .reset_index(drop=True))

    def table_hash(self, table: str) -> str:
        """Deterministic content hash of a table's business columns, used to
        verify byte-identical re-runs."""
        if not self._table_exists(table):
            return "empty"
        hashes = self.con.execute(
            f"SELECT row_hash FROM {table} ORDER BY row_hash"
        ).df()["row_hash"]
        return hashlib.sha256("".join(hashes).encode()).hexdigest()[:16]

    def tables(self) -> list[str]:
        return [r[0] for r in self.con.execute("SHOW TABLES").fetchall()]

    def export_parquet(self, out_dir: str | Path | None = None) -> None:
        out = Path(out_dir) if out_dir else self.data_dir / "parquet"
        out.mkdir(parents=True, exist_ok=True)
        for t in self.tables():
            self.con.execute(
                f"COPY (SELECT * FROM {t} ORDER BY row_hash) TO "
                f"'{out / (t + '.parquet')}' (FORMAT PARQUET)"
            )

    def _table_exists(self, table: str) -> bool:
        return table in self.tables()

    def close(self) -> None:
        self.con.close()


def _row_hashes(df: pd.DataFrame) -> pd.Series:
    cols = sorted(df.columns)
    blob = df[cols].apply(lambda r: "|".join(str(v) for v in r), axis=1)
    return blob.map(lambda s: hashlib.sha1(s.encode()).hexdigest())
