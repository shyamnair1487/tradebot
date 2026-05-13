"""
core/notifier.py
Telegram notifications for trade events.
"""

import logging
import os
import requests

logger = logging.getLogger(__name__)


def send_telegram(message: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        logger.debug("Telegram not configured, skipping notification")
        return False

    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
        }, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error("Telegram notification failed: " + str(e))
        return False


def notify_signal(strategy, symbol, side, entry, sl, tp):
    send_telegram(
        f"📡 <b>SIGNAL</b>\n"
        f"Strategy: {strategy}\n"
        f"Pair: {symbol} {side}\n"
        f"Entry: {entry}\n"
        f"SL: {sl}\n"
        f"TP: {tp}"
    )


def notify_position_opened(strategy, symbol, side, entry, sl, tp, qty):
    send_telegram(
        f"✅ <b>POSITION OPENED</b>\n"
        f"Strategy: {strategy}\n"
        f"Pair: {symbol} {side}\n"
        f"Entry: {round(entry, 5)}\n"
        f"SL: {round(sl, 5)}\n"
        f"TP: {round(tp, 5)}\n"
        f"Qty: {qty} lots"
    )


def notify_position_closed(symbol, side, entry, exit_price, reason, pips):
    emoji = "🎯" if reason == "TP" else "🛑"
    send_telegram(
        f"{emoji} <b>POSITION CLOSED</b>\n"
        f"Pair: {symbol} {side}\n"
        f"Entry: {round(entry, 5)} → Exit: {round(exit_price, 5)}\n"
        f"Reason: {reason}\n"
        f"Pips: {pips:+.1f}"
    )
