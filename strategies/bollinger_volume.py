"""
strategies/bollinger_volume.py

Bollinger Band + Volume Confirmation Strategy

Logic:
- Calculate Bollinger Bands (20-period SMA, 2 std deviations)
- BUY  when price closes BELOW lower band AND volume > avg_volume * volume_multiplier
- SELL when price closes ABOVE upper band AND volume > avg_volume * volume_multiplier
- The volume filter eliminates low-conviction breakouts
- Stop loss: ATR-based
- Take profit: fixed risk/reward
"""

import logging
from typing import Optional

from core.models import Signal, Side
from strategies.base import BaseStrategy, Candle

logger = logging.getLogger(__name__)


class BollingerVolumeStrategy(BaseStrategy):

    def __init__(
        self,
        broker_name: str,
        symbol: str,
        timeframe: str,
        bb_period: int = 20,
        bb_std: float = 2.0,
        volume_period: int = 20,
        volume_multiplier: float = 1.5,
        atr_period: int = 14,
        atr_multiplier: float = 1.5,
        rr_ratio: float = 2.0,
    ):
        super().__init__(broker_name, symbol, timeframe)
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.volume_period = volume_period
        self.volume_multiplier = volume_multiplier
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier
        self.rr_ratio = rr_ratio

    @property
    def name(self) -> str:
        return "BB_VOL_" + str(self.bb_period) + "_" + str(self.bb_std)

    def _bollinger_bands(self, closes, period, num_std):
        if len(closes) < period:
            return None, None, None
        sma = sum(closes[-period:]) / period
        variance = sum((c - sma) ** 2 for c in closes[-period:]) / period
        std = variance ** 0.5
        upper = sma + num_std * std
        lower = sma - num_std * std
        return upper, sma, lower

    def evaluate(self, candles):
        min_candles = max(self.bb_period, self.volume_period, self.atr_period) + 2
        if len(candles) < min_candles:
            return None

        closes = [c.close for c in candles]
        volumes = [c.volume for c in candles]

        upper, mid, lower = self._bollinger_bands(closes, self.bb_period, self.bb_std)
        if upper is None:
            return None

        current_close = closes[-1]
        current_volume = volumes[-1]
        avg_volume = sum(volumes[-self.volume_period:]) / self.volume_period

        volume_confirmed = current_volume > avg_volume * self.volume_multiplier

        if not volume_confirmed:
            return None

        # Mean reversion: price outside bands = overextended, expect reversion
        oversold = current_close < lower
        overbought = current_close > upper

        if not oversold and not overbought:
            return None

        atr = self._atr(candles, self.atr_period)
        if atr <= 0:
            return None

        entry_price = current_close
        stop_distance = atr * self.atr_multiplier

        if oversold:
            side = Side.BUY
            stop_loss = entry_price - stop_distance
            take_profit = mid  # target the middle band
            # If TP is too close, use fixed RR instead
            if (take_profit - entry_price) < stop_distance * self.rr_ratio:
                take_profit = entry_price + stop_distance * self.rr_ratio
        else:
            side = Side.SELL
            stop_loss = entry_price + stop_distance
            take_profit = mid
            if (entry_price - take_profit) < stop_distance * self.rr_ratio:
                take_profit = entry_price - stop_distance * self.rr_ratio

        logger.info(
            self.name + " signal: " + side.value + " " + self.symbol +
            " entry=" + str(round(entry_price, 2)) +
            " sl=" + str(round(stop_loss, 2)) +
            " tp=" + str(round(take_profit, 2)) +
            " vol_ratio=" + str(round(current_volume / avg_volume, 2))
        )

        return Signal(
            strategy=self.name,
            broker=self.broker_name,
            symbol=self.symbol,
            side=side,
            stop_loss=stop_loss,
            take_profit=take_profit,
            notes="vol_ratio=" + str(round(current_volume / avg_volume, 2)) +
                  " bb_pos=" + ("below_lower" if oversold else "above_upper"),
        )
