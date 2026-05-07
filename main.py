"""
main.py
Entry point. Reads .env, wires up brokers + strategies + engine, starts the loop.

Usage:
  cp .env.example .env        # fill in your keys
  pip install -r requirements.txt
  python main.py --broker ftm --dry-run    # simulate without sending orders
  python main.py --broker ftm              # live trading
  python main.py --broker binance --dry-run
"""

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── Logging setup ─────────────────────────────────────────────────────────────
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


def build_brokers(active: str) -> dict:
    brokers = {}

    if active in ("binance", "both"):
        from brokers.binance_broker import BinanceBroker
        brokers["binance"] = BinanceBroker(
            api_key=os.environ["BINANCE_API_KEY"],
            api_secret=os.environ["BINANCE_API_SECRET"],
            testnet=os.getenv("BINANCE_TESTNET", "true").lower() == "true",
        )

    if active in ("ftm", "both"):
        from brokers.ftm_broker import FTMBroker
        brokers["ftm"] = FTMBroker(
            server_url=os.environ["FTM_SERVER_URL"],
            email=os.environ["FTM_EMAIL"],
            api_key=os.environ["FTM_API_KEY"],
            system_uuid=os.environ["FTM_SYSTEM_UUID"],
            partner_id=os.getenv("FTM_PARTNER_ID", "1"),
            account_index=int(os.getenv("FTM_ACCOUNT_INDEX", "0")),
        )

    if not brokers:
        raise ValueError("Unknown ACTIVE_BROKER value: " + active)

    return brokers


def build_strategies(brokers: dict) -> list:
    from strategies.ema_crossover import EMACrossoverStrategy
    from strategies.rsi_mean_reversion import RSIMeanReversionStrategy
    strategies = []

    if "binance" in brokers:
        strategies += [
            EMACrossoverStrategy(
                broker_name="binance",
                symbol="BTCUSDT",
                timeframe="1h",
                fast_period=9,
                slow_period=21,
                atr_multiplier=1.5,
                rr_ratio=2.0,
            ),
        ]

    if "ftm" in brokers:
        strategies += [
            # USDCAD and USDCHF first — EURUSD candles endpoint slow on FTM
            EMACrossoverStrategy("ftm", "USDCAD", "4h", fast_period=9, slow_period=21, atr_multiplier=1.5, rr_ratio=3.0),
            EMACrossoverStrategy("ftm", "USDCHF", "4h", fast_period=9, slow_period=21, atr_multiplier=1.5, rr_ratio=3.0),
            RSIMeanReversionStrategy("ftm", "USDCAD", "4h", rsi_period=14, oversold=30.0, overbought=70.0, atr_multiplier=1.5, rr_ratio=3.0),
            RSIMeanReversionStrategy("ftm", "USDCHF", "4h", rsi_period=14, oversold=30.0, overbought=70.0, atr_multiplier=1.5, rr_ratio=3.0),
            # EURUSD last since candles endpoint is slow
            EMACrossoverStrategy("ftm", "EURUSD", "4h", fast_period=9, slow_period=21, atr_multiplier=1.5, rr_ratio=3.0),
            RSIMeanReversionStrategy("ftm", "EURUSD", "4h", rsi_period=14, oversold=30.0, overbought=70.0, atr_multiplier=1.5, rr_ratio=3.0),
        ]

    return strategies


def main():
    parser = argparse.ArgumentParser(description="Trading Bot")
    parser.add_argument("--dry-run", action="store_true", help="Log orders without sending to broker")
    parser.add_argument("--broker", default=None, help="binance | ftm | both (overrides .env ACTIVE_BROKER)")
    parser.add_argument("--interval", type=int, default=300, help="Poll interval in seconds (default 300)")
    parser.add_argument("--single-pass", action="store_true", help="Run once and exit (for GitHub Actions)", dest="single_pass")
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
    brokers = build_brokers(active_broker)
    strategies = build_strategies(brokers)

    if not strategies:
        logger.error("No strategies configured.")
        sys.exit(1)

    engines = []
    for strategy in strategies:
        broker = brokers[strategy.broker_name]
        engine = TradingEngine(
            broker=broker,
            risk_gate=risk,
            ledger=ledger,
            strategies=[strategy],
            dry_run=args.dry_run,
        )
        engines.append(engine)

    logger.info(
        "Starting bot | broker=" + active_broker + " | "
        "strategies=" + str([s.name for s in strategies]) + " | "
        "dry_run=" + str(args.dry_run) + " | interval=" + str(args.interval) + "s"
    )

    import time
    if args.single_pass:
        logger.info("Single pass mode — running once and exiting")
        for engine in engines:
            try:
                engine.run_once()
            except Exception as e:
                ledger.log_error("main.loop", e)
        logger.info("Single pass complete")
    else:
        while True:
            for engine in engines:
                try:
                    engine.run_once()
                except Exception as e:
                    ledger.log_error("main.loop", e)
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
