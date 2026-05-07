"""
strategies/base.py + strategies/ema_crossover.py

HOW TO ADD YOUR OWN STRATEGY:
  1. Create a new file in strategies/
  2. Subclass BaseStrategy
  3. Implement evaluate() — return a Signal or None
  4. Register it in main.py

The strategy knows NOTHING about risk or order execution.
It only answers: "Given this market data, should I trade?"
"""

from abc import ABC, abstractmethod
from typing import Optional

from core.models import Candle, Signal


# ── Base ─────────────────────────────────────────────────────────────────────

class BaseStrategy(ABC):

    def __init__(self, broker_name: str, symbol: str, timeframe: str):
        self.broker_name = broker_name
        self.symbol = symbol
        self.timeframe = timeframe

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def evaluate(self, candles: list[Candle]) -> Optional[Signal]:
        """
        Given a list of candles (oldest first, newest last),
        return a Signal if there's a trade opportunity, else None.

        The signal MUST include a stop_loss price.
        take_profit is optional (set to 0.0 to skip).
        """
        ...

    def _ema(self, values: list[float], period: int) -> list[float]:
        """Simple EMA calculation without external dependencies."""
        if len(values) < period:
            return []
        k = 2 / (period + 1)
        ema = [sum(values[:period]) / period]
        for price in values[period:]:
            ema.append(price * k + ema[-1] * (1 - k))
        return ema

    def _atr(self, candles: list[Candle], period: int = 14) -> float:
        """Average True Range — used for dynamic stop placement."""
        if len(candles) < period + 1:
            return 0.0
        trs = []
        for i in range(1, len(candles)):
            high = candles[i].high
            low = candles[i].low
            prev_close = candles[i - 1].close
            trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        return sum(trs[-period:]) / period
