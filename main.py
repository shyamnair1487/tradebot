"""
main.py - Entry point. Wires up broker + strategies + single engine, starts the loop.
"""

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/bot.log"),
    ],
)
logger = logging.getLogger(__name__)


def build_broker(active: str):
    if active == "ftm":
        from brokers.ftm_broker import FTMBroker
        return FTMBroker(
            server_url=os.environ["FTM_SERVER_URL"],
            email=os.environ["FTM_EMAIL"],
            api_key=os.environ["FTM_API_KEY"],
            system_uuid=os.environ["FTM_SYSTEM_UUID"],
            partner_id=os.getenv("FTM_PARTNER_ID", "1"),
            account_index=int(os.getenv("FTM_ACCOUNT_INDEX", "0")),
        )
    elif active == "binance":
        from brokers.binance_broker import BinanceBroker
        return BinanceBroker(
            api_key=os.environ["BINANCE_API_KEY"],
            api_secret=os.environ["BINANCE_API_SECRET"],
            testnet=os.getenv("BINANCE_TESTNET", "true").lower() == "true",
        )
    else:
        raise ValueError("Unknown broker: " + active)


def build_strategies(broker_name: str) -> list:
    from strategies.ema_crossover import EMACrossoverStrategy
    from strategies.rsi_mean_reversion import RSIMeanReversionStrategy

    if broker_name == "ftm":
        return [
            EMACrossoverStrategy("ftm", "USDCAD", "4h", fast_period=9, slow_period=21, atr_multiplier=1.5, rr_ratio=3.0),
            EMACrossoverStrategy("ftm", "USDCHF", "4h", fast_period=9, slow_period=21, atr_multiplier=1.5, rr_ratio=3.0),
            EMACrossoverStrategy("ftm", "EURUSD", "4h", fast_period=9, slow_period=21, atr_multiplier=1.5, rr_ratio=3.0),
            RSIMeanReversionStrategy("ftm", "USDCAD", "4h", rsi_period=14, oversold=30.0, overbought=70.0, atr_multiplier=1.5, rr_ratio=3.0),
            RSIMeanReversionStrategy("ftm", "USDCHF", "4h", rsi_period=14, oversold=30.0, overbought=70.0, atr_multiplier=1.5, rr_ratio=3.0),
            RSIMeanReversionStrategy("ftm", "EURUSD", "4h", rsi_period=14, oversold=30.0, overbought=70.0, atr_multiplier=1.5, rr_ratio=3.0),
        ]
    elif broker_name == "binance":
        return [
            EMACrossoverStrategy("binance", "BTCUSDT", "1h", fast_period=9, slow_period=21, atr_multiplier=1.5, rr_ratio=2.0),
        ]
    else:
        raise ValueError("Unknown broker: " + broker_name)


def main():
    parser = argparse.ArgumentParser(description="Trading Bot")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--broker", default=None)
    parser.add_argument("--interval", type=int, default=300)
    parser.add_argument("--single-pass", action="store_true", dest="single_pass")
    args = parser.parse_args()

    Path("logs").mkdir(exist_ok=True)

    from core.ledger import Ledger
    from core.risk import RiskGate
    from core.engine import TradingEngine

    ledger = Ledger(path=os.getenv("LEDGER_PATH", "logs/ledger.jsonl"))
    risk = RiskGate(
        risk_pct=float(os.getenv("RISK_PER_TRADE_PCT", "1.0")),
        max_open_trades=int(os.getenv("MAX_OPEN_TRADES", "3")),
        max_daily_drawdown_pct=float(os.getenv("MAX_DAILY_DRAWDOWN_PCT", "4.0")),
        max_total_drawdown_pct=float(os.getenv("MAX_TOTAL_DRAWDOWN_PCT", "8.0")),
        ledger=ledger,
    )

    active_broker = args.broker if args.broker else os.getenv("ACTIVE_BROKER", "binance")
    broker = build_broker(active_broker)
    strategies = build_strategies(active_broker)

    if not strategies:
        logger.error("No strategies configured.")
        sys.exit(1)

    # Single engine with all strategies — fixes duplicate position close bug
    engine = TradingEngine(
        broker=broker,
        risk_gate=risk,
        ledger=ledger,
        strategies=strategies,
        dry_run=args.dry_run,
    )

    logger.info(
        "Starting bot | broker=" + active_broker +
        " | strategies=" + str([s.name for s in strategies]) +
        " | dry_run=" + str(args.dry_run) +
        " | interval=" + str(args.interval) + "s"
    )

    import time
    if args.single_pass:
        logger.info("Single pass mode — running once and exiting")
        try:
            engine.run_once()
        except Exception as e:
            ledger.log_error("main.single_pass", e)
        logger.info("Single pass complete")
    else:
        while True:
            try:
                engine.run_once()
            except Exception as e:
                ledger.log_error("main.loop", e)
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
