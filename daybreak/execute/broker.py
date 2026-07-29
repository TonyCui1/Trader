"""Broker adapters (Phase 7). Paper only in v1 — `execute.paper` must be true.

Order idempotency (PLAN.md gap 7): every order carries a deterministic
client_order_id = sha1(date|symbol|side|qty). A crashed-and-rerun job
re-submits the same IDs and Alpaca rejects the duplicates.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pandas as pd


def client_order_id(date: str, symbol: str, side: str, qty: int) -> str:
    return "dbk-" + hashlib.sha1(f"{date}|{symbol}|{side}|{qty}".encode()).hexdigest()[:20]


class DryRunBroker:
    """Simulates paper execution locally; positions persist across runs so
    consecutive dry-run days behave like a real paper account."""

    def __init__(self, data_dir: str | Path):
        self.state_path = Path(data_dir) / "paper_state.json"
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text())
        else:
            self.state = {"cash": 100000.0, "positions": {}, "orders": []}

    def get_positions(self) -> dict[str, int]:
        return {k: int(v) for k, v in self.state["positions"].items()}

    def get_equity(self, last_prices: dict[str, float]) -> float:
        pos_val = sum(sh * last_prices.get(sym, 0.0)
                      for sym, sh in self.state["positions"].items())
        return self.state["cash"] + pos_val

    def submit_order(self, symbol: str, side: str, qty: int, limit_price: float,
                     coid: str) -> dict:
        if any(o["client_order_id"] == coid for o in self.state["orders"]):
            return {"status": "duplicate_rejected", "client_order_id": coid}
        # dry-run assumption: limit fills at the limit price
        fill = {"symbol": symbol, "side": side, "qty": qty,
                "limit_price": limit_price, "fill_price": limit_price,
                "client_order_id": coid, "status": "filled"}
        self.state["orders"].append(fill)
        pos = self.state["positions"]
        if side == "buy":
            self.state["cash"] -= qty * limit_price
            pos[symbol] = pos.get(symbol, 0) + qty
        else:
            self.state["cash"] += qty * limit_price
            pos[symbol] = pos.get(symbol, 0) - qty
            if pos[symbol] <= 0:
                pos.pop(symbol, None)
        return fill

    def get_orders(self, date: str) -> list[dict]:
        return [o for o in self.state["orders"]]

    def save(self) -> None:
        self.state_path.write_text(json.dumps(self.state, indent=2))


class AlpacaBroker:
    """Live paper adapter. Requires alpaca-py + ALPACA_API_KEY/SECRET_KEY.
    Only ever talks to the paper endpoint in v1."""

    def __init__(self, cfg: dict):
        if not cfg["execute"]["paper"]:
            raise RuntimeError("v1 is paper-only (spec non-goal: no live money). "
                               "execute.paper must remain true.")
        from alpaca.trading.client import TradingClient
        self.client = TradingClient(os.environ["ALPACA_API_KEY"],
                                    os.environ["ALPACA_SECRET_KEY"], paper=True)

    def get_positions(self) -> dict[str, int]:
        return {p.symbol: int(float(p.qty)) for p in self.client.get_all_positions()}

    def submit_order(self, symbol: str, side: str, qty: int, limit_price: float,
                     coid: str) -> dict:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest
        try:
            order = self.client.submit_order(LimitOrderRequest(
                symbol=symbol, qty=qty,
                side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
                limit_price=round(limit_price, 2),
                client_order_id=coid))
            return {"status": str(order.status), "client_order_id": coid,
                    "id": str(order.id)}
        except Exception as e:
            if "client_order_id must be unique" in str(e):
                return {"status": "duplicate_rejected", "client_order_id": coid}
            raise

    def cancel_open_orders(self) -> None:
        self.client.cancel_orders()

    def get_orders(self, date: str) -> list[dict]:
        from alpaca.trading.requests import GetOrdersRequest
        after = pd.Timestamp(date).tz_localize("America/New_York")
        orders = self.client.get_orders(GetOrdersRequest(after=after, status="all"))
        return [{"symbol": o.symbol, "side": str(o.side), "qty": int(float(o.qty)),
                 "fill_price": float(o.filled_avg_price) if o.filled_avg_price else None,
                 "status": str(o.status), "client_order_id": o.client_order_id}
                for o in orders]
