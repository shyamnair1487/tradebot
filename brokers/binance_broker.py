"""
brokers/binance_broker.py
Binance adapter using the python-binance SDK.
Supports spot trading. For futures, swap client to AsyncClient with futures endpoints.

Install: pip install python-binance
"""

import logging
from datetime import datetime, timezone
from functools import lru_cache

from brokers.base import BaseBroker
from core.models import Candle, Order, OrderStatus, OrderType

logger = logging.getLogger(__name__)


class BinanceBroker(BaseBroker):

    def __init__(self, api_key: str, api_secret: str, testnet: bool = True):
        from binance.client import Client
        self._client = Client(api_key, api_secret, testnet=testnet)
        self._testnet = testnet
        if testnet:
            self._client.API_URL = "https://testnet.binance.vision/api"
        logger.info(f"BinanceBroker initialized (testnet={testnet})")

    @property
    def name(self) -> str:
        return "binance"

    # ── Account ──────────────────────────────────────────────────────

    def get_balance(self) -> float:
        """Return free USDT balance."""
        try:
            account = self._client.get_account()
            for asset in account["balances"]:
                if asset["asset"] == "USDT":
                    return float(asset["free"])
        except Exception as e:
            logger.error(f"get_balance failed: {e}")
        return 0.0

    def get_open_positions(self) -> list:
        """Return list of open orders across all symbols."""
        try:
            return self._client.get_open_orders()
        except Exception as e:
            logger.error(f"get_open_positions failed: {e}")
            return []

    # ── Market data ──────────────────────────────────────────────────

    def get_symbol_price(self, symbol: str) -> float:
        try:
            ticker = self._client.get_symbol_ticker(symbol=symbol)
            return float(ticker["price"])
        except Exception as e:
            logger.error(f"get_symbol_price({symbol}) failed: {e}")
            return 0.0

    @lru_cache(maxsize=64)
    def _get_symbol_info(self, symbol: str) -> dict:
        """Cache symbol info to avoid hammering the API."""
        return self._client.get_symbol_info(symbol) or {}

    def get_min_qty(self, symbol: str) -> float:
        try:
            info = self._get_symbol_info(symbol)
            for f in info.get("filters", []):
                if f["filterType"] == "LOT_SIZE":
                    return float(f["stepSize"])
        except Exception as e:
            logger.error(f"get_min_qty({symbol}) failed: {e}")
        return 0.001  # fallback

    def get_candles(self, symbol: str, timeframe: str, limit: int = 200) -> list[Candle]:
        """
        timeframe: Binance interval string — "1m","5m","15m","1h","4h","1d"
        """
        try:
            raw = self._client.get_klines(symbol=symbol, interval=timeframe, limit=limit)
            candles = []
            for k in raw:
                candles.append(Candle(
                    symbol=symbol,
                    timestamp=datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc),
                    open=float(k[1]),
                    high=float(k[2]),
                    low=float(k[3]),
                    close=float(k[4]),
                    volume=float(k[5]),
                    timeframe=timeframe,
                ))
            return candles
        except Exception as e:
            logger.error(f"get_candles({symbol},{timeframe}) failed: {e}")
            return []

    # ── Execution ────────────────────────────────────────────────────

    def place_order(self, order: Order) -> Order:
        try:
            params = dict(
                symbol=order.symbol,
                side=order.side.value,
                type=order.order_type.value,
                quantity=round(order.qty, 6),
            )
            # For MARKET orders, no price needed
            if order.order_type == OrderType.LIMIT:
                params["price"] = order.fill_price
                params["timeInForce"] = "GTC"

            response = self._client.create_order(**params)
            order.broker_order_id = response.get("orderId", "")
            order.status = OrderStatus.FILLED if response.get("status") == "FILLED" else OrderStatus.PENDING

            # Extract fill price and fee from fills if available
            fills = response.get("fills", [])
            if fills:
                total_qty = sum(float(f["qty"]) for f in fills)
                order.fill_price = sum(float(f["price"]) * float(f["qty"]) for f in fills) / total_qty
                order.fee = sum(float(f["commission"]) for f in fills)

            # Place stop loss as a separate stop-market order
            if order.stop_loss > 0 and order.status == OrderStatus.FILLED:
                self._place_stop_loss(order)

            # Place take profit as a separate limit order
            if order.take_profit > 0 and order.status == OrderStatus.FILLED:
                self._place_take_profit(order)

            logger.info(f"Order placed: {order.broker_order_id} fill={order.fill_price}")
            return order

        except Exception as e:
            logger.error(f"place_order failed: {e}")
            order.status = OrderStatus.REJECTED
            return order

    def cancel_order(self, broker_order_id: str, symbol: str) -> bool:
        try:
            self._client.cancel_order(symbol=symbol, orderId=broker_order_id)
            return True
        except Exception as e:
            logger.error(f"cancel_order({broker_order_id}) failed: {e}")
            return False

    # ── Private helpers ──────────────────────────────────────────────

    def _place_stop_loss(self, original_order: Order) -> None:
        from core.models import Side
        sl_side = Side.SELL if original_order.side == Side.BUY else Side.BUY
        try:
            self._client.create_order(
                symbol=original_order.symbol,
                side=sl_side.value,
                type="STOP_LOSS_LIMIT",
                quantity=round(original_order.qty, 6),
                price=round(original_order.stop_loss * 0.999, 2),  # small buffer
                stopPrice=round(original_order.stop_loss, 2),
                timeInForce="GTC",
            )
            logger.info(f"Stop loss placed at {original_order.stop_loss}")
        except Exception as e:
            logger.error(f"_place_stop_loss failed: {e}")

    def _place_take_profit(self, original_order: Order) -> None:
        from core.models import Side
        tp_side = Side.SELL if original_order.side == Side.BUY else Side.BUY
        try:
            self._client.create_order(
                symbol=original_order.symbol,
                side=tp_side.value,
                type="TAKE_PROFIT_LIMIT",
                quantity=round(original_order.qty, 6),
                price=round(original_order.take_profit * 1.001, 2),
                stopPrice=round(original_order.take_profit, 2),
                timeInForce="GTC",
            )
            logger.info(f"Take profit placed at {original_order.take_profit}")
        except Exception as e:
            logger.error(f"_place_take_profit failed: {e}")
