"""
core/ledger.py
Append-only JSONL ledger. Written BEFORE orders are sent to brokers.
This is the system of record — broker state is truth, but this is your audit trail.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)


class Ledger:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def _write(self, record: dict) -> None:
        record.setdefault("logged_at", datetime.now(timezone.utc).isoformat())
        with self._lock:
            with self.path.open("a") as f:
                f.write(json.dumps(record) + "\n")

    def log_signal(self, signal, raw_market_data: dict = None) -> None:
        """Log a raw strategy signal before risk evaluation."""
        from core.models import Signal
        record = {
            "event": "SIGNAL",
            "signal_id": signal.id,
            "strategy": signal.strategy,
            "broker": signal.broker,
            "symbol": signal.symbol,
            "side": signal.side.value,
            "stop_loss": signal.stop_loss,
            "take_profit": signal.take_profit,
            "notes": signal.notes,
            "market_snapshot": raw_market_data or {},
        }
        self._write(record)
        logger.debug(f"SIGNAL logged: {signal.id} {signal.symbol} {signal.side.value}")

    def log_risk_decision(self, signal_id: str, decision) -> None:
        """Log the risk gate outcome."""
        record = {
            "event": "RISK_DECISION",
            "signal_id": signal_id,
            "approved": decision.approved,
            "qty": decision.qty,
            "reason": decision.reason.value if decision.reason else None,
        }
        self._write(record)

    def log_order(self, order) -> None:
        """Log an order at every status change."""
        record = {"event": "ORDER", **order.to_dict()}
        self._write(record)
        logger.info(
            f"ORDER {order.status.value}: {order.symbol} {order.side.value} "
            f"qty={order.qty} fill={order.fill_price} id={order.id}"
        )

    def log_error(self, context: str, error: Exception, extra: dict = None) -> None:
        record = {
            "event": "ERROR",
            "context": context,
            "error": str(error),
            "extra": extra or {},
        }
        self._write(record)
        logger.error(f"ERROR in {context}: {error}")

    def log_halt(self, reason: str, extra: dict = None) -> None:
        record = {
            "event": "HALT",
            "reason": reason,
            "extra": extra or {},
        }
        self._write(record)
        logger.critical(f"TRADING HALTED: {reason}")

    def read_today(self) -> list[dict]:
        """Return all ledger entries from today (UTC). Useful for drawdown calc."""
        today = datetime.now(timezone.utc).date().isoformat()
        entries = []
        if not self.path.exists():
            return entries
        with self.path.open() as f:
            for line in f:
                try:
                    record = json.loads(line)
                    if record.get("logged_at", "").startswith(today):
                        entries.append(record)
                except json.JSONDecodeError:
                    continue
        return entries
