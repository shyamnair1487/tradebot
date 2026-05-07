"""
brokers/ftm_broker.py
FundedTraderMarkets adapter using the Match Trader Platform REST API.

The FTM server drops connections after ~5 minutes idle.
We re-login before each request to ensure a fresh session.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import requests

from brokers.base import BaseBroker
from core.models import Candle, Order, OrderStatus

logger = logging.getLogger(__name__)

TIMEFRAME_MAP = {
    "1m":  "M1",
    "5m":  "M5",
    "15m": "M15",
    "30m": "M30",
    "1h":  "H1",
    "4h":  "H4",
    "1d":  "D1",
}


class FTMBroker(BaseBroker):

    def __init__(
        self,
        server_url: str,
        email: str,
        api_key: str,
        system_uuid: str,
        partner_id: str = "1",
        account_index: int = 0,
    ):
        self._base = server_url.rstrip("/")
        self._email = email
        self._api_key = api_key
        self._system_uuid = system_uuid
        self._partner_id = partner_id
        self._account_index = account_index
        self._trading_account_id: Optional[str] = None
        self._authenticated = False
        self._login()

    @property
    def name(self) -> str:
        return "ftm"

    def _base_headers(self) -> dict:
        return {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36",
            "Origin": "https://platform.fundedtradermarkets.com",
        }

    def _login(self) -> tuple:
        """
        Login and return (session, token).
        Creates a fresh session each time.
        """
        session = requests.Session()
        url = self._base + "/mtr-core-edge/login"
        payload = {
            "email": self._email,
            "password": self._api_key,
            "partnerId": self._partner_id,
        }
        headers = self._base_headers()
        headers["Referer"] = "https://platform.fundedtradermarkets.com/login"
        try:
            resp = session.post(url, json=payload, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            accounts = data.get("tradingAccounts", [])
            if not accounts:
                raise ValueError("No trading accounts returned")
            account = accounts[self._account_index]
            token = account.get("tradingApiToken")
            self._trading_account_id = account.get("tradingAccountId")
            self._authenticated = True
            if not hasattr(self, '_logged_once'):
                self._logged_once = True
                logger.info(
                    "FTMBroker authenticated. Using account " + str(self._trading_account_id) +
                    " (index " + str(self._account_index) + " of " + str(len(accounts)) + " accounts)"
                )
                for i, acc in enumerate(accounts):
                    logger.info("  [" + str(i) + "] accountId=" + str(acc.get("tradingAccountId")) +
                               " created=" + str(acc.get("created", "")[:10]))
            return session, token
        except Exception as e:
            logger.error("FTM login failed: " + str(e))
            self._authenticated = False
            return None, None

    def _get_session_and_token(self):
        """Get a fresh session with login for each API call batch."""
        session, token = self._login()
        return session, token

    def _api_headers(self, token: str) -> dict:
        headers = self._base_headers()
        headers["Auth-trading-api"] = token or ""
        headers["Referer"] = "https://platform.fundedtradermarkets.com/trade"
        return headers

    def _api_url(self, path: str) -> str:
        return self._base + "/mtr-api/" + self._system_uuid + "/" + path.lstrip("/")

    def _get(self, path: str) -> dict:
        session, token = self._get_session_and_token()
        if not session:
            raise Exception("Could not authenticate with FTM")
        url = self._api_url(path)
        resp = session.get(url, headers=self._api_headers(token), timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, payload: dict) -> dict:
        session, token = self._get_session_and_token()
        if not session:
            raise Exception("Could not authenticate with FTM")
        url = self._api_url(path)
        resp = session.post(url, json=payload, headers=self._api_headers(token), timeout=30)
        resp.raise_for_status()
        return resp.json()

    # ── Account ──────────────────────────────────────────────────────────────

    def get_balance(self) -> float:
        try:
            return float(self._get("balance").get("balance", 0))
        except Exception as e:
            logger.error("FTM get_balance failed: " + str(e))
            return 0.0

    def get_open_positions(self) -> list:
        try:
            return self._get("open-positions").get("positions", [])
        except Exception as e:
            logger.error("FTM get_open_positions failed: " + str(e))
            return []

    # ── Market data ──────────────────────────────────────────────────────────

    def get_symbol_price(self, symbol: str) -> float:
        try:
            data = self._get("quotations?symbols=" + symbol)
            body = data if isinstance(data, list) else data.get("body", [])
            if body:
                bid = float(body[0].get("bid", 0))
                ask = float(body[0].get("ask", 0))
                return (bid + ask) / 2
        except Exception as e:
            logger.error("FTM get_symbol_price(" + symbol + ") failed: " + str(e))
        return 0.0

    def get_min_qty(self, symbol: str) -> float:
        return 0.01

    def get_candles(self, symbol: str, timeframe: str, limit: int = 200) -> list:
        mt_interval = TIMEFRAME_MAP.get(timeframe, "H1")
        # Use a single session for login + candles fetch
        session, token = self._get_session_and_token()
        if not session:
            logger.error("FTM get_candles: could not authenticate")
            return []
        try:
            url = self._api_url(
                "candles?symbol=" + symbol + "&interval=" + mt_interval + "&count=" + str(limit)
            )
            resp = session.get(url, headers=self._api_headers(token), timeout=30)
            resp.raise_for_status()
            data = resp.json()
            candles = []
            for c in data.get("candles", []):
                candles.append(Candle(
                    symbol=symbol,
                    timestamp=datetime.fromtimestamp(c["time"] / 1000, tz=timezone.utc),
                    open=float(c["open"]),
                    high=float(c["high"]),
                    low=float(c["low"]),
                    close=float(c["close"]),
                    volume=float(c.get("volume", 0)),
                    timeframe=timeframe,
                ))
            return candles
        except Exception as e:
            logger.error("FTM get_candles(" + symbol + "," + timeframe + ") failed: " + str(e))
            return []

    # ── Execution ────────────────────────────────────────────────────────────

    def place_order(self, order: Order) -> Order:
        try:
            payload = {
                "instrument": order.symbol,
                "orderSide": order.side.value,
                "volume": round(order.qty, 2),
                "slPrice": round(order.stop_loss, 5) if order.stop_loss else 0,
                "tpPrice": round(order.take_profit, 5) if order.take_profit else 0,
                "isMobile": False,
            }
            data = self._post("position/open", payload)
            if data.get("status") == "OK":
                order.status = OrderStatus.FILLED
                order.broker_order_id = str(data.get("id", ""))
                order.fill_price = self.get_symbol_price(order.symbol)
                logger.info("FTM order placed: " + order.symbol + " " +
                           order.side.value + " vol=" + str(order.qty))
            else:
                order.status = OrderStatus.REJECTED
                logger.error("FTM order rejected: " + str(data.get("errorMessage", "unknown")))
        except Exception as e:
            logger.error("FTM place_order failed: " + str(e))
            order.status = OrderStatus.REJECTED
        return order

    def cancel_order(self, broker_order_id: str, symbol: str) -> bool:
        try:
            data = self._post(
                "pending-order/cancel",
                {"instrument": symbol, "id": broker_order_id, "isMobile": False},
            )
            return data.get("status") == "OK"
        except Exception as e:
            logger.error("FTM cancel_order failed: " + str(e))
            return False


