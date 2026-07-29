"""Immutable daily decision log (spec Phase 7).

One JSON record per day, append-only JSONL. Each record embeds the SHA-256 of
the previous record (`prev_hash`), making the log tamper-evident: rewriting
history breaks the chain. Records carry the inputs hash, config hash, signals
summary, regime, decision, and a reasoning summary.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


class DecisionLog:
    def __init__(self, data_dir: str | Path):
        self.path = Path(data_dir) / "decisions.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: dict) -> dict:
        record = dict(record)
        record["prev_hash"] = self._last_hash()
        blob = json.dumps(record, sort_keys=True, default=str)
        record["record_hash"] = hashlib.sha256(blob.encode()).hexdigest()[:32]
        with open(self.path, "a") as f:
            f.write(json.dumps(record, sort_keys=True, default=str) + "\n")
        return record

    def read_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        with open(self.path) as f:
            return [json.loads(line) for line in f if line.strip()]

    def verify_chain(self) -> bool:
        prev = "genesis"
        for rec in self.read_all():
            if rec.get("prev_hash") != prev:
                return False
            body = {k: v for k, v in rec.items() if k != "record_hash"}
            blob = json.dumps(body, sort_keys=True, default=str)
            if hashlib.sha256(blob.encode()).hexdigest()[:32] != rec.get("record_hash"):
                return False
            prev = rec["record_hash"]
        return True

    def _last_hash(self) -> str:
        recs = self.read_all()
        return recs[-1]["record_hash"] if recs else "genesis"
