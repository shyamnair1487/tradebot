"""
brokers/base.py
Abstract base class every broker adapter must implement.
The rest of the bot only ever talks to this interface — never to broker SDKs directly.
"""

from abc import ABC, abstractmethod
from core.models import Order, Signal


class BaseBroker(ABC):

    @abstractmethod
    def get_balance(self) -> float:
        """Return available trading balance in quote currency (e.g. USDT)."""
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
        """Return the minimum order quantity increment for a symbol."""
        ...

    @abstractmethod
    def get_candles(self, symbol: str, timeframe: str, limit: int = 200) -> list:
        """Return list of Candle objects, newest last."""
        ...

    @abstractmethod
    def place_order(self, order: Order) -> Order:
        """
        Submit an order. Must return the Order with:
          - broker_order_id set
          - status updated to FILLED / REJECTED
          - fill_price and fee populated if filled
        """
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
