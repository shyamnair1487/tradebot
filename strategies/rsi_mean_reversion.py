"""
strategies/rsi_mean_reversion.py

RSI Mean Reversion Strategy
- BUY  when RSI drops below oversold level (default 30) and then crosses back above it
- SELL when RSI rises above overbought level (default 70) and then crosses back below it
- Stop loss: ATR-based
- Take profit: fixed risk/reward ratio

This strategy works best in ranging/choppy markets — the opposite of trend following.
"""

import logging
from typing import Optional

from core.models import Signal, Side
from strategies.base import BaseStrategy, Candle

logger = logging.getLogger(__name__)


class RSIMeanReversionStrategy(BaseStrategy):

    def __init__(
        self,
        broker_name: str,
        symbol: str,
        timeframe: str,
        rsi_period: int = 14,
        oversold: float = 30.0,
        overbought: float = 70.0,
        atr_period: int = 14,
        atr_multiplier: float = 1.5,
        rr_ratio: float = 2.0,
    ):
        super().__init__(broker_name, symbol, timeframe)
        self.rsi_period = rsi_period
        self.oversold = oversold
        self.overbought = overbought
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier
        self.rr_ratio = rr_ratio

    @property
    def name(self) -> str:
        return "RSI_" + str(self.rsi_period) + "_" + str(int(self.oversold)) + "_" + str(int(self.overbought))

    def _rsi(self, closes: list, period: int) -> list:
        if len(closes) < period + 1:
            return []
        gains = []
        losses = []
        for i in range(1, len(closes)):
            change = closes[i] - closes[i - 1]
            gains.append(max(change, 0))
            losses.append(max(-change, 0))

        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        rsi_values = []

        for i in range(period, len(closes)):
            if avg_loss == 0:
                rsi_values.append(100.0)
            else:
                rs = avg_gain / avg_loss
                rsi_values.append(100 - (100 / (1 + rs)))
            if i < len(closes) - 1:
                idx = i - period + 1
                avg_gain = (avg_gain * (period - 1) + gains[idx]) / period
                avg_loss = (avg_loss * (period - 1) + losses[idx]) / period

        return rsi_values

    def evaluate(self, candles: list) -> Optional[Signal]:
        min_candles = self.rsi_period + self.atr_period + 2
        if len(candles) < min_candles:
            return None

        closes = [c.close for c in candles]
        rsi = self._rsi(closes, self.rsi_period)

        if len(rsi) < 2:
            return None

        rsi_now = rsi[-1]
        rsi_prev = rsi[-2]

        # BUY: RSI was below oversold and just crossed back above it
        bullish = rsi_prev < self.oversold and rsi_now >= self.oversold

        # SELL: RSI was above overbought and just crossed back below it
        bearish = rsi_prev > self.overbought and rsi_now <= self.overbought

        if not bullish and not bearish:
            return None

        atr = self._atr(candles, self.atr_period)
        if atr <= 0:
            return None

        entry_price = candles[-1].close
        stop_distance = atr * self.atr_multiplier

        if bullish:
            side = Side.BUY
            stop_loss = entry_price - stop_distance
            take_profit = entry_price + (stop_distance * self.rr_ratio)
        else:
            side = Side.SELL
            stop_loss = entry_price + stop_distance
            take_profit = entry_price - (stop_distance * self.rr_ratio)

        logger.info(
            self.name + " signal: " + side.value + " " + self.symbol +
            " entry~" + str(round(entry_price, 4)) +
            " sl=" + str(round(stop_loss, 4)) +
            " tp=" + str(round(take_profit, 4)) +
            " RSI=" + str(round(rsi_now, 1))
        )

        return Signal(
            strategy=self.name,
            broker=self.broker_name,
            symbol=self.symbol,
            side=side,
            stop_loss=stop_loss,
            take_profit=take_profit,
            notes="RSI=" + str(round(rsi_now, 1)) + " prev=" + str(round(rsi_prev, 1)),
        )
