"""
brokers/base.py
Abstract base class every broker adapter must implement.
"""

from abc import ABC, abstractmethod
from core.models import Order, Signal


class BaseBroker(ABC):

    @abstractmethod
    def get_balance(self) -> float:
        """Return available trading balance in quote currency."""
        ...

    @abstractmethod
    def get_open_positions(self) -> list:
        """Return list of currently open positions/orders."""
        ...

    @abstractmethod
    def get_symbol_price(self, symbol: str) -> float:
        """Return current mid/last price for a symbol."""
        ...

    @abstractmethod
    def get_min_qty(self, symbol: str) -> float:
        """Return the minimum order quantity increment for a symbol (in lots)."""
        ...

    def get_lot_size(self, symbol: str) -> float:
        """Return the contract/lot size for a symbol. Default 100,000 for forex."""
        return 100000.0

    @abstractmethod
    def get_candles(self, symbol: str, timeframe: str, limit: int = 200) -> list:
        """Return list of Candle objects, newest last."""
        ...

    @abstractmethod
    def place_order(self, order: Order) -> Order:
        """Submit an order. Must return Order with broker_order_id, status, fill_price."""
        ...

    @abstractmethod
    def cancel_order(self, broker_order_id: str, symbol: str) -> bool:
        """Cancel an open order. Return True if successful."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Identifier string, e.g. 'binance' or 'ftm'."""
        ...
