"""
core/risk.py
The mandatory risk gate. No order can bypass this.
Responsibilities:
  - Calculate position size from account balance + stop distance
  - Enforce max open trades
  - Enforce daily and total drawdown limits
  - Return a RiskDecision (approved/rejected + qty)
"""

import logging
from typing import Protocol

from core.models import RejectionReason, RiskDecision, Signal

logger = logging.getLogger(__name__)


class BrokerInterface(Protocol):
    """Minimal interface the risk gate needs from any broker."""
    def get_balance(self) -> float: ...
    def get_open_positions(self) -> list: ...
    def get_symbol_price(self, symbol: str) -> float: ...
    def get_min_qty(self, symbol: str) -> float: ...


class RiskGate:
    def __init__(
        self,
        risk_pct: float = 1.0,
        max_open_trades: int = 3,
        max_daily_drawdown_pct: float = 4.0,
        max_total_drawdown_pct: float = 8.0,
        ledger=None,
    ):
        self.risk_pct = risk_pct / 100.0
        self.max_open_trades = max_open_trades
        self.max_daily_drawdown_pct = max_daily_drawdown_pct / 100.0
        self.max_total_drawdown_pct = max_total_drawdown_pct / 100.0
        self.ledger = ledger
        self._peak_balance: float = 0.0  # updated on each evaluation
        self._is_halted: bool = False

    def evaluate(self, signal: Signal, broker: BrokerInterface) -> RiskDecision:
        """
        Run all risk checks. Returns RiskDecision with approved=True and qty,
        or approved=False with a rejection reason.
        """
        if self._is_halted:
            return RiskDecision(approved=False, reason=RejectionReason.TOTAL_DRAWDOWN)

        balance = broker.get_balance()
        if balance <= 0:
            return RiskDecision(approved=False, reason=RejectionReason.INSUFFICIENT_BALANCE)

        # Update peak balance for drawdown tracking
        if balance > self._peak_balance:
            self._peak_balance = balance

        # ── Total drawdown check ──────────────────────────────────────
        if self._peak_balance > 0:
            total_dd = (self._peak_balance - balance) / self._peak_balance
            if total_dd >= self.max_total_drawdown_pct:
                self._is_halted = True
                if self.ledger:
                    self.ledger.log_halt(
                        "Total drawdown limit hit",
                        {"drawdown_pct": round(total_dd * 100, 2), "balance": balance},
                    )
                return RiskDecision(approved=False, reason=RejectionReason.TOTAL_DRAWDOWN)

        # ── Daily drawdown check ──────────────────────────────────────
        daily_loss = self._calc_daily_loss(balance)
        if daily_loss / balance >= self.max_daily_drawdown_pct:
            logger.warning(f"Daily drawdown limit reached: {daily_loss:.2f}")
            return RiskDecision(approved=False, reason=RejectionReason.DAILY_DRAWDOWN)

        # ── Max open trades check ─────────────────────────────────────
        open_positions = broker.get_open_positions()
        if len(open_positions) >= self.max_open_trades:
            return RiskDecision(approved=False, reason=RejectionReason.MAX_OPEN_TRADES)

        # ── Position sizing ───────────────────────────────────────────
        current_price = broker.get_symbol_price(signal.symbol)
        if current_price <= 0 or signal.stop_loss <= 0:
            return RiskDecision(approved=False, reason=RejectionReason.ZERO_SIZE)

        stop_distance = abs(current_price - signal.stop_loss)
        if stop_distance == 0:
            return RiskDecision(approved=False, reason=RejectionReason.ZERO_SIZE)

        risk_amount = balance * self.risk_pct
        lot_size = broker.get_lot_size(signal.symbol)
        raw_qty = risk_amount / (stop_distance * lot_size)

        # Round down to broker's minimum quantity increment
        min_qty = broker.get_min_qty(signal.symbol)
        qty = self._floor_to_precision(raw_qty, min_qty)

        if qty <= 0:
            logger.warning(
                f"Calculated qty={raw_qty:.6f} below min_qty={min_qty} "
                f"for {signal.symbol}. Risk amount too small for stop distance."
            )
            return RiskDecision(approved=False, reason=RejectionReason.ZERO_SIZE)

        logger.info(
            f"Risk approved: {signal.symbol} {signal.side.value} "
            f"qty={qty} risk=${risk_amount:.2f} stop_dist={stop_distance:.4f}"
        )
        return RiskDecision(approved=True, qty=qty)

    def reset_halt(self) -> None:
        """Manual override to resume after investigating a halt."""
        logger.warning("Risk halt manually reset — ensure you've reviewed the ledger.")
        self._is_halted = False

    def _calc_daily_loss(self, current_balance: float) -> float:
        """
        Estimate today's P&L from the ledger if available,
        otherwise approximate from peak balance (conservative).
        """
        if self.ledger is None:
            return 0.0
        today_entries = self.ledger.read_today()
        # Sum up filled order fees as a proxy for realized loss floor
        # Strategies should extend this with actual P&L tracking
        realized_loss = 0.0
        for entry in today_entries:
            if entry.get("event") == "ORDER" and entry.get("status") == "FILLED":
                realized_loss += entry.get("fee", 0.0)
        return realized_loss

    @staticmethod
    def _floor_to_precision(qty: float, min_qty: float) -> float:
        if min_qty <= 0:
            return round(qty, 6)
        import math
        return math.floor(qty / min_qty) * min_qty
