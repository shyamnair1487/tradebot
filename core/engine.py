"""
core/engine.py
The main decision pipeline. Wires strategy → risk → execution → ledger.
Nothing trades without going through here.

Positions are persisted to logs/positions.json so they survive restarts
and work correctly with GitHub Actions ephemeral environments.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from brokers.base import BaseBroker
from core.ledger import Ledger
from core.models import Order, OrderStatus, OrderType, RejectionReason
from core.risk import RiskGate
from strategies.base import BaseStrategy
from core.notifier import notify_position_closed, notify_position_opened

logger = logging.getLogger(__name__)

POSITIONS_FILE = "logs/positions.json"


class TradingEngine:

    def __init__(
        self,
        broker: BaseBroker,
        risk_gate: RiskGate,
        ledger: Ledger,
        strategies: list,
        dry_run: bool = False,
    ):
        self.broker = broker
        self.risk = risk_gate
        self.ledger = ledger
        self.strategies = strategies
        self.dry_run = dry_run
        self._open_positions = {}  # position_key -> order dict
        self._load_positions()

        if dry_run:
            logger.warning("DRY RUN MODE — orders will be logged but NOT sent to broker")

    # ── Position persistence ──────────────────────────────────────────────────

    def _position_key(self, strategy_name, symbol):
        return strategy_name + ":" + symbol

    def _save_positions(self):
        """Save open positions to file so they survive restarts."""
        Path("logs").mkdir(exist_ok=True)
        data = {
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "positions": {}
        }
        for key, order in self._open_positions.items():
            data["positions"][key] = {
                "order_id": order.get("order_id", ""),
                "symbol": order.get("symbol", ""),
                "side": order.get("side", ""),
                "entry_price": order.get("entry_price", 0),
                "stop_loss": order.get("stop_loss", 0),
                "take_profit": order.get("take_profit", 0),
                "qty": order.get("qty", 0),
                "opened_at": order.get("opened_at", ""),
                "strategy": order.get("strategy", ""),
            }
        with open(POSITIONS_FILE, "w") as f:
            json.dump(data, f, indent=2)
        logger.debug("Positions saved: " + str(len(self._open_positions)) + " open")

    def _load_positions(self):
        """Load open positions from file on startup."""
        if not os.path.exists(POSITIONS_FILE):
            logger.info("No positions file found, starting fresh")
            return
        try:
            with open(POSITIONS_FILE, "r") as f:
                data = json.load(f)
            positions = data.get("positions", {})
            self._open_positions = positions
            if positions:
                logger.info("Loaded " + str(len(positions)) + " open positions from file:")
                for key, pos in positions.items():
                    logger.info("  " + key + " | entry=" + str(pos.get("entry_price")) +
                               " sl=" + str(pos.get("stop_loss")) +
                               " tp=" + str(pos.get("take_profit")))
        except Exception as e:
            logger.error("Failed to load positions: " + str(e))
            self._open_positions = {}

    def _check_position_closed(self, key, position, current_price):
        """
        Check if an open position has hit its SL or TP.
        Returns True if position should be closed.
        """
        sl = position.get("stop_loss", 0)
        tp = position.get("take_profit", 0)
        side = position.get("side", "BUY")

        if side == "BUY":
            if tp > 0 and current_price >= tp:
                return "TP"
            if sl > 0 and current_price <= sl:
                return "SL"
        else:
            if tp > 0 and current_price <= tp:
                return "TP"
            if sl > 0 and current_price >= sl:
                return "SL"
        return None

    def _check_open_positions(self):
        """Check all open positions for SL/TP hits and close them."""
        closed = []
        for key, position in self._open_positions.items():
            symbol = position.get("symbol", "")
            if not symbol:
                continue
            try:
                current_price = self.broker.get_symbol_price(symbol)
                if current_price <= 0:
                    continue
                result = self._check_position_closed(key, position, current_price)
                if result:
                    entry = position.get("entry_price", 0)
                    side = position.get("side", "BUY")
                    if side == "BUY":
                        pips = round((current_price - entry) * 10000, 1)
                    else:
                        pips = round((entry - current_price) * 10000, 1)
                    logger.info(
                        "Position CLOSED (" + result + "): " + key +
                        " | exit=" + str(round(current_price, 5)) +
                        " | pips=" + str(pips)
                    )
                    self.ledger._write({
                        "event": "POSITION_CLOSED",
                        "key": key,
                        "symbol": symbol,
                        "side": side,
                        "entry_price": entry,
                        "exit_price": current_price,
                        "exit_reason": result,
                        "pips": pips,
                    })
                    notify_position_closed(symbol, side, entry, current_price, result, pips)
                    closed.append(key)
            except Exception as e:
                logger.error("Error checking position " + key + ": " + str(e))

        for key in closed:
            del self._open_positions[key]

        if closed:
            self._save_positions()

    # ── Main loop ────────────────────────────────────────────────────────────

    def run_once(self) -> None:
        """Single evaluation pass. Safe to call from GitHub Actions."""
        # First check if any open positions have closed
        if self._open_positions:
            self._check_open_positions()

        for strategy in self.strategies:
            try:
                self._evaluate_strategy(strategy)
                time.sleep(2)  # Small pause between strategies
            except Exception as e:
                self.ledger.log_error(
                    context="engine.run_once:" + strategy.name,
                    error=e,
                )

    def run_loop(self, interval_seconds: int = 300) -> None:
        """Blocking loop for running on Mac/VPS."""
        logger.info("Bot started. Polling every " + str(interval_seconds) + "s.")
        while True:
            try:
                self.run_once()
            except KeyboardInterrupt:
                logger.info("Bot stopped by user.")
                break
            except Exception as e:
                self.ledger.log_error("engine.run_loop", e)
            time.sleep(interval_seconds)

    # ── Strategy evaluation ──────────────────────────────────────────────────

    def _evaluate_strategy(self, strategy) -> None:
        position_key = self._position_key(strategy.name, strategy.symbol)

        # Skip if already have open position for this strategy+symbol
        if position_key in self._open_positions:
            logger.debug(strategy.name + ": skipping, position already open for " + strategy.symbol)
            return

        # Fetch candles
        candles = self.broker.get_candles(
            symbol=strategy.symbol,
            timeframe=strategy.timeframe,
            limit=200,
        )
        if not candles:
            logger.warning(strategy.name + ": no candles returned")
            return

        market_snapshot = {
            "symbol": strategy.symbol,
            "timeframe": strategy.timeframe,
            "last_close": candles[-1].close,
            "last_ts": candles[-1].timestamp.isoformat(),
            "candle_count": len(candles),
        }

        # Get signal
        signal = strategy.evaluate(candles)
        if signal is None:
            logger.debug(strategy.name + ": no signal")
            return

        self.ledger.log_signal(signal, raw_market_data=market_snapshot)

        # Risk gate
        decision = self.risk.evaluate(signal, self.broker)
        self.ledger.log_risk_decision(signal.id, decision)

        if not decision.approved:
            logger.info(strategy.name + ": signal rejected — " + str(decision.reason))
            return

        # Build order
        order = Order(
            signal_id=signal.id,
            broker=signal.broker,
            symbol=signal.symbol,
            side=signal.side,
            order_type=OrderType.MARKET,
            qty=decision.qty,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
        )
        self.ledger.log_order(order)

        # Execute
        if self.dry_run:
            order.status = OrderStatus.FILLED
            order.fill_price = candles[-1].close
            order.broker_order_id = "DRY_" + order.id
            logger.info("DRY RUN — would have placed: " + str(order.symbol) +
                       " " + str(order.side.value) + " @ " + str(order.fill_price))
        else:
            order = self.broker.place_order(order)

        self.ledger.log_order(order)

        if order.status == OrderStatus.FILLED:
            # Track open position and persist to file
            self._open_positions[position_key] = {
                "order_id": order.id,
                "symbol": order.symbol,
                "side": order.side.value,
                "entry_price": order.fill_price,
                "stop_loss": order.stop_loss,
                "take_profit": order.take_profit,
                "qty": order.qty,
                "opened_at": datetime.now(timezone.utc).isoformat(),
                "strategy": strategy.name,
            }
            self._save_positions()
            notify_position_opened(strategy.name, strategy.symbol, order.side.value, order.fill_price, order.stop_loss, order.take_profit, order.qty)
            logger.info(strategy.name + ": position opened for " + strategy.symbol)
        elif order.status == OrderStatus.REJECTED:
            logger.error("Order rejected by broker: " + order.id)
