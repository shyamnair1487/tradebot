"""
core/models.py
Shared data structures. Nothing in here imports from anywhere else in the bot.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
import uuid


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_MARKET = "STOP_MARKET"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class RejectionReason(str, Enum):
    MAX_OPEN_TRADES = "MAX_OPEN_TRADES"
    DAILY_DRAWDOWN = "DAILY_DRAWDOWN"
    TOTAL_DRAWDOWN = "TOTAL_DRAWDOWN"
    INSUFFICIENT_BALANCE = "INSUFFICIENT_BALANCE"
    ZERO_SIZE = "ZERO_SIZE"
    BROKER_ERROR = "BROKER_ERROR"


@dataclass
class Candle:
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    timeframe: str  # e.g. "1h", "4h", "1d"


@dataclass
class Signal:
    """Emitted by a strategy. Not yet risk-checked."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    strategy: str = ""
    broker: str = ""          # "binance" | "ftm"
    symbol: str = ""
    side: Side = Side.BUY
    stop_loss: float = 0.0    # absolute price
    take_profit: float = 0.0  # absolute price, 0 = no TP
    notes: str = ""


@dataclass
class Order:
    """A risk-approved order sent to a broker."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    signal_id: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    broker: str = ""
    symbol: str = ""
    side: Side = Side.BUY
    order_type: OrderType = OrderType.MARKET
    qty: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    status: OrderStatus = OrderStatus.PENDING
    fill_price: float = 0.0
    fee: float = 0.0
    broker_order_id: str = ""
    rejection_reason: Optional[RejectionReason] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d


@dataclass
class RiskDecision:
    approved: bool
    qty: float = 0.0
    reason: Optional[RejectionReason] = None
