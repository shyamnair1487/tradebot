"""
strategies/funding_rate_backtest.py

Backtestable version of the funding rate strategy.
Fetches historical funding rates from Binance and uses them
in the backtest instead of live API calls.

Funding rates are published every 8 hours on Binance:
  00:00, 08:00, 16:00 UTC
"""

import logging
import time
from datetime import datetime, timezone
from typing import Optional

import requests

from core.models import Signal, Side
from strategies.base import BaseStrategy

logger = logging.getLogger(__name__)


def fetch_historical_funding_rates(symbol: str, years: int = 2) -> dict:
    """
    Fetch historical funding rates from Binance futures API.
    Returns dict of {timestamp_ms: funding_rate}
    Funding rates are every 8 hours so ~2190 records per year.

    Note: Binance returns results oldest-first when using startTime.
    We page forward in time using startTime.
    """
    from datetime import timedelta
    print("Fetching historical funding rates for " + symbol + "...")

    # Calculate start time
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = now_ms - (years * 365 * 24 * 3600 * 1000)

    all_rates = []
    start_time = start_ms

    while True:
        params = {"symbol": symbol, "limit": 1000, "startTime": start_time}

        try:
            resp = requests.get(
                "https://fapi.binance.com/fapi/v1/fundingRate",
                params=params,
                timeout=10,
            )
            resp.raise_for_status()
            rates = resp.json()
        except Exception as e:
            print("Warning: Could not fetch funding rates: " + str(e))
            return {}

        if not rates:
            break

        all_rates.extend(rates)
        print("  Got " + str(len(all_rates)) + " funding rate records...")

        if len(rates) < 1000:
            break

        # Move start time forward past last record
        start_time = rates[-1]["fundingTime"] + 1
        time.sleep(0.1)

    # Convert to dict for fast lookup: {timestamp_ms: rate}
    rate_dict = {}
    for r in all_rates:
        rate_dict[r["fundingTime"]] = float(r["fundingRate"])

    print("Fetched " + str(len(rate_dict)) + " funding rate records")
    return rate_dict


def get_funding_rate_at(rate_dict: dict, timestamp_ms: int) -> Optional[float]:
    """
    Get the most recent funding rate at or before a given timestamp.
    Funding rates apply from their publish time until the next one.
    """
    if not rate_dict:
        return None

    # Find the most recent funding rate before this timestamp
    applicable_times = [t for t in rate_dict.keys() if t <= timestamp_ms]
    if not applicable_times:
        return None

    latest_time = max(applicable_times)
    return rate_dict[latest_time]


class FundingRateBacktestStrategy(BaseStrategy):
    """
    Funding rate strategy with pre-loaded historical rates for backtesting.
    Set rate_dict before running backtest.
    """

    def __init__(
        self,
        broker_name: str,
        symbol: str,
        timeframe: str,
        rate_dict: dict = None,
        funding_threshold: float = 0.0005,
        ema_period: int = 50,
        atr_period: int = 14,
        atr_multiplier: float = 1.5,
        rr_ratio: float = 2.5,
    ):
        super().__init__(broker_name, symbol, timeframe)
        self.rate_dict = rate_dict or {}
        self.funding_threshold = funding_threshold
        self.ema_period = ema_period
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier
        self.rr_ratio = rr_ratio

    @property
    def name(self) -> str:
        return "FUNDING_" + str(int(self.funding_threshold * 10000)) + "bps_EMA" + str(self.ema_period)

    def evaluate(self, candles: list) -> Optional[Signal]:
        min_candles = self.ema_period + self.atr_period + 2
        if len(candles) < min_candles:
            return None

        current_candle = candles[-1]
        timestamp_ms = int(current_candle.timestamp.timestamp() * 1000)

        funding_rate = get_funding_rate_at(self.rate_dict, timestamp_ms)
        if funding_rate is None:
            return None

        long_bias  = funding_rate < -self.funding_threshold
        short_bias = funding_rate > self.funding_threshold

        if not long_bias and not short_bias:
            return None

        closes = [c.close for c in candles]
        ema = self._ema(closes, self.ema_period)
        if len(ema) < 2:
            return None

        price_above_ema = closes[-1] > ema[-1]
        price_below_ema = closes[-1] < ema[-1]

        buy_signal  = long_bias and price_above_ema
        sell_signal = short_bias and price_below_ema

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

        return Signal(
            strategy=self.name,
            broker=self.broker_name,
            symbol=self.symbol,
            side=side,
            stop_loss=stop_loss,
            take_profit=take_profit,
            notes="funding=" + str(round(funding_rate * 100, 4)) + "%",
        )
