"""
strategies/ema_crossover.py

Example strategy: EMA crossover with ATR-based stop loss.
This is a TEMPLATE to demonstrate the plugin pattern — not a recommendation.
Replace or extend with whatever logic you want to test.

Logic:
  - BUY  when fast EMA crosses above slow EMA
  - SELL when fast EMA crosses below slow EMA
  - Stop loss = entry ± (ATR * atr_multiplier)
  - Take profit = entry ± (stop_distance * rr_ratio)
"""

import logging
from typing import Optional

from core.models import Signal, Side
from strategies.base import BaseStrategy, Candle

logger = logging.getLogger(__name__)


class EMACrossoverStrategy(BaseStrategy):

    def __init__(
        self,
        broker_name: str,
        symbol: str,
        timeframe: str,
        fast_period: int = 9,
        slow_period: int = 21,
        atr_period: int = 14,
        atr_multiplier: float = 1.5,  # stop = ATR * this
        rr_ratio: float = 2.0,        # take profit = stop_distance * this
    ):
        super().__init__(broker_name, symbol, timeframe)
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier
        self.rr_ratio = rr_ratio

    @property
    def name(self) -> str:
        return f"EMA_{self.fast_period}_{self.slow_period}"

    def evaluate(self, candles: list[Candle]) -> Optional[Signal]:
        min_candles = max(self.slow_period, self.atr_period) + 2
        if len(candles) < min_candles:
            logger.debug(f"{self.name}: not enough candles ({len(candles)}/{min_candles})")
            return None

        closes = [c.close for c in candles]
        fast_ema = self._ema(closes, self.fast_period)
        slow_ema = self._ema(closes, self.slow_period)

        if len(fast_ema) < 2 or len(slow_ema) < 2:
            return None

        # Check for crossover on the last two completed candles
        # (index -1 is the most recent, -2 is the one before)
        fast_now, fast_prev = fast_ema[-1], fast_ema[-2]
        slow_now, slow_prev = slow_ema[-1], slow_ema[-2]

        bullish_cross = fast_prev <= slow_prev and fast_now > slow_now
        bearish_cross = fast_prev >= slow_prev and fast_now < slow_now

        if not bullish_cross and not bearish_cross:
            return None

        # ATR-based stop distance
        atr = self._atr(candles, self.atr_period)
        if atr <= 0:
            return None

        entry_price = candles[-1].close
        stop_distance = atr * self.atr_multiplier

        if bullish_cross:
            side = Side.BUY
            stop_loss = entry_price - stop_distance
            take_profit = entry_price + (stop_distance * self.rr_ratio)
        else:
            side = Side.SELL
            stop_loss = entry_price + stop_distance
            take_profit = entry_price - (stop_distance * self.rr_ratio)

        logger.info(
            f"{self.name} signal: {side.value} {self.symbol} "
            f"entry~{entry_price:.4f} sl={stop_loss:.4f} tp={take_profit:.4f}"
        )

        return Signal(
            strategy=self.name,
            broker=self.broker_name,
            symbol=self.symbol,
            side=side,
            stop_loss=stop_loss,
            take_profit=take_profit,
            notes=f"ATR={atr:.4f} fast={fast_now:.4f} slow={slow_now:.4f}",
        )
