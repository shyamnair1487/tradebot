"""
strategies/funding_rate_strategy.py

Funding Rate + EMA Strategy

Logic:
- Funding rate > +threshold = longs are overcrowded = SELL signal only
- Funding rate < -threshold = shorts are overcrowded = BUY signal only
- Funding rate near zero = no strong bias = skip trade
- EMA trend confirms direction before entry

Funding rates on Binance perpetuals are settled every 8 hours.
Positive funding = longs pay shorts (market is bullish/overcrowded long)
Negative funding = shorts pay longs (market is bearish/overcrowded short)

Extreme funding rates historically precede reversals because:
- Very positive: too many longs, any dip triggers cascading liquidations
- Very negative: too many shorts, any pump triggers short squeeze
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import requests

from core.models import Signal, Side
from strategies.base import BaseStrategy, Candle

logger = logging.getLogger(__name__)


class FundingRateStrategy(BaseStrategy):

    def __init__(
        self,
        broker_name: str,
        symbol: str,
        timeframe: str,
        funding_threshold: float = 0.0005,  # 0.05% per 8h = overcrowded
        ema_period: int = 50,
        atr_period: int = 14,
        atr_multiplier: float = 1.5,
        rr_ratio: float = 2.5,
        futures_symbol: str = None,  # e.g. "BTCUSDT" for BTC perps
    ):
        super().__init__(broker_name, symbol, timeframe)
        self.funding_threshold = funding_threshold
        self.ema_period = ema_period
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier
        self.rr_ratio = rr_ratio
        self.futures_symbol = futures_symbol or symbol
        self._last_funding_rate = None
        self._last_funding_fetch = None

    @property
    def name(self) -> str:
        return "FUNDING_EMA_" + str(self.ema_period)

    def _get_funding_rate(self) -> Optional[float]:
        """
        Fetch current funding rate from Binance futures API.
        Returns funding rate as a float (e.g. 0.0001 = 0.01%)
        Caches for 30 minutes to avoid hammering the API.
        """
        now = datetime.now(timezone.utc)

        # Return cached value if fresh enough
        if (self._last_funding_rate is not None and
                self._last_funding_fetch is not None):
            elapsed = (now - self._last_funding_fetch).seconds
            if elapsed < 1800:  # 30 minutes
                return self._last_funding_rate

        try:
            resp = requests.get(
                "https://fapi.binance.com/fapi/v1/premiumIndex",
                params={"symbol": self.futures_symbol},
                timeout=5,
            )
            resp.raise_for_status()
            data = resp.json()
            rate = float(data.get("lastFundingRate", 0))
            self._last_funding_rate = rate
            self._last_funding_fetch = now
            logger.info("Funding rate for " + self.futures_symbol + ": " + str(round(rate * 100, 4)) + "%")
            return rate
        except Exception as e:
            logger.warning("Could not fetch funding rate: " + str(e))
            return None

    def evaluate(self, candles: list) -> Optional[Signal]:
        min_candles = self.ema_period + self.atr_period + 2
        if len(candles) < min_candles:
            return None

        # Get funding rate
        funding_rate = self._get_funding_rate()
        if funding_rate is None:
            logger.debug("No funding rate available, skipping")
            return None

        # Determine bias from funding rate
        long_bias  = funding_rate < -self.funding_threshold   # shorts overcrowded = buy
        short_bias = funding_rate > self.funding_threshold    # longs overcrowded = sell
        neutral    = not long_bias and not short_bias

        if neutral:
            logger.debug("Funding rate neutral (" + str(round(funding_rate * 100, 4)) + "%), skipping")
            return None

        # Confirm with EMA trend
        closes = [c.close for c in candles]
        ema = self._ema(closes, self.ema_period)
        if len(ema) < 2:
            return None

        price_above_ema = closes[-1] > ema[-1]
        price_below_ema = closes[-1] < ema[-1]

        # Only trade when funding AND trend agree
        buy_signal  = long_bias and price_above_ema   # funding says buy + uptrend
        sell_signal = short_bias and price_below_ema  # funding says sell + downtrend

        if not buy_signal and not sell_signal:
            return None

        atr = self._atr(candles, self.atr_period)
        if atr <= 0:
            return None

        entry_price = closes[-1]
        stop_distance = atr * self.atr_multiplier

        if buy_signal:
            side = Side.BUY
            stop_loss = entry_price - stop_distance
            take_profit = entry_price + stop_distance * self.rr_ratio
        else:
            side = Side.SELL
            stop_loss = entry_price + stop_distance
            take_profit = entry_price - stop_distance * self.rr_ratio

        logger.info(
            self.name + " signal: " + side.value +
            " funding=" + str(round(funding_rate * 100, 4)) + "%" +
            " entry=" + str(round(entry_price, 2)) +
            " sl=" + str(round(stop_loss, 2)) +
            " tp=" + str(round(take_profit, 2))
        )

        return Signal(
            strategy=self.name,
            broker=self.broker_name,
            symbol=self.symbol,
            side=side,
            stop_loss=stop_loss,
            take_profit=take_profit,
            notes="funding=" + str(round(funding_rate * 100, 4)) + "% bias=" + ("long" if long_bias else "short"),
        )
