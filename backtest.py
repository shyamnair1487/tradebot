"""
backtest.py - Walk-forward backtester
Usage:
  python3 backtest.py
  python3 backtest.py --symbol EURUSD --strategy ema --years 2
  python3 backtest.py --symbol BTCUSDT --strategy rsi --years 2
  python3 backtest.py --symbol EURUSD --strategy bb --timeframe 4h
"""

import argparse
import csv
import os
import sys
import time
import logging
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logging.basicConfig(level=logging.WARNING)

FOREX_PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "NZDUSD", "USDCHF", "GBPJPY", "EURJPY"]

def is_forex(symbol):
    return symbol.upper() in FOREX_PAIRS

def fetch_candles(symbol, timeframe, years=2):
    if is_forex(symbol):
        return fetch_candles_yahoo(symbol, timeframe, years)
    else:
        return fetch_candles_binance(symbol, timeframe, years)

def fetch_candles_yahoo(symbol, timeframe, years=2):
    import urllib.request
    import json
    from core.models import Candle
    tf_map = {"1h": "1h", "4h": "1h", "1d": "1d", "15m": "15m"}
    yf_interval = tf_map.get(timeframe, "1h")
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           + symbol + "=X?interval=" + yf_interval + "&range=" + str(years) + "y")
    print("Fetching " + symbol + " from Yahoo Finance...")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    resp = urllib.request.urlopen(req, timeout=15)
    data = json.loads(resp.read())
    result = data["chart"]["result"][0]
    timestamps = result["timestamp"]
    quote = result["indicators"]["quote"][0]
    volumes = quote.get("volume") or [0] * len(timestamps)
    candles = []
    for i, ts in enumerate(timestamps):
        try:
            c = quote["close"][i]
            if c is None:
                continue
            candles.append(Candle(
                symbol=symbol,
                timestamp=datetime.fromtimestamp(ts, tz=timezone.utc),
                open=float(quote["open"][i] or c),
                high=float(quote["high"][i] or c),
                low=float(quote["low"][i] or c),
                close=float(c),
                volume=float(volumes[i] or 0),
                timeframe=timeframe,
            ))
        except (TypeError, ValueError):
            continue
    if timeframe == "4h":
        candles = downsample_candles(candles, 4)
    print("Fetched " + str(len(candles)) + " candles ("
          + str(candles[0].timestamp.date()) + " to "
          + str(candles[-1].timestamp.date()) + ")")
    return candles

def downsample_candles(candles, factor):
    from core.models import Candle
    result = []
    for i in range(0, len(candles) - factor + 1, factor):
        group = candles[i:i + factor]
        result.append(Candle(
            symbol=group[0].symbol,
            timestamp=group[0].timestamp,
            open=group[0].open,
            high=max(c.high for c in group),
            low=min(c.low for c in group),
            close=group[-1].close,
            volume=sum(c.volume for c in group),
            timeframe=str(factor) + "h",
        ))
    return result

def fetch_candles_binance(symbol, timeframe, years=2):
    from binance.client import Client
    from core.models import Candle
    from dotenv import load_dotenv
    load_dotenv()
    candles_per_year = {"1h": 8760, "4h": 2190, "1d": 365, "15m": 35040}
    target = candles_per_year.get(timeframe, 8760) * years
    print("Fetching " + symbol + " " + timeframe + " candles (~" + str(target) + " needed)...")
    api_key = os.getenv("BINANCE_API_KEY", "")
    api_secret = os.getenv("BINANCE_API_SECRET", "")
    client = Client(api_key, api_secret)
    all_klines = []
    end_time = None
    while len(all_klines) < target:
        kwargs = dict(symbol=symbol, interval=timeframe, limit=1000)
        if end_time:
            kwargs["endTime"] = end_time
        klines = client.get_klines(**kwargs)
        if not klines:
            break
        all_klines = klines + all_klines
        end_time = klines[0][0] - 1
        print("  Got " + str(len(all_klines)) + " candles so far...")
        if len(klines) < 1000:
            break
        time.sleep(0.1)
    all_klines = all_klines[-target:]
    candles = [
        Candle(
            symbol=symbol,
            timestamp=datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc),
            open=float(k[1]), high=float(k[2]),
            low=float(k[3]), close=float(k[4]),
            volume=float(k[5]), timeframe=timeframe,
        )
        for k in all_klines
    ]
    print("Fetched " + str(len(candles)) + " candles ("
          + str(candles[0].timestamp.date()) + " to "
          + str(candles[-1].timestamp.date()) + ")")
    return candles


@dataclass
class BacktestTrade:
    entry_time: datetime
    exit_time: Optional[datetime]
    side: str
    entry_price: float
    exit_price: float
    stop_loss: float
    take_profit: float
    qty: float
    pnl: float
    pnl_pct: float
    exit_reason: str


def run_backtest(candles, strategy, initial_balance=10000.0, risk_pct=1.0, fee_pct=0.01, train_pct=0.6):
    # Note: fee_pct is 0.01% for forex (spread-based), 0.1% for crypto
    split = int(len(candles) * train_pct)
    train_candles = candles[:split]
    test_candles = candles[split:]
    print("")
    print("Train: " + str(train_candles[0].timestamp.date()) + " to "
          + str(train_candles[-1].timestamp.date()) + " (" + str(len(train_candles)) + " candles)")
    print("Test:  " + str(test_candles[0].timestamp.date()) + " to "
          + str(test_candles[-1].timestamp.date()) + " (" + str(len(test_candles)) + " candles)")
    balance = initial_balance
    peak_balance = initial_balance
    trades = []
    equity_curve = [(test_candles[0].timestamp, balance)]
    open_trade = None
    for i in range(1, len(test_candles)):
        candle = test_candles[i]
        if open_trade:
            if open_trade.side == "BUY":
                hit_sl = candle.low <= open_trade.stop_loss
                hit_tp = candle.high >= open_trade.take_profit
            else:
                hit_sl = candle.high >= open_trade.stop_loss
                hit_tp = candle.low <= open_trade.take_profit
            if hit_sl or hit_tp:
                exit_price = open_trade.take_profit if hit_tp else open_trade.stop_loss
                exit_reason = "TP" if hit_tp else "SL"
                fee = open_trade.qty * exit_price * (fee_pct / 100)
                if open_trade.side == "BUY":
                    raw_pnl = (exit_price - open_trade.entry_price) * open_trade.qty
                else:
                    raw_pnl = (open_trade.entry_price - exit_price) * open_trade.qty
                pnl = raw_pnl - fee
                balance += pnl
                peak_balance = max(peak_balance, balance)
                open_trade.exit_time = candle.timestamp
                open_trade.exit_price = exit_price
                open_trade.pnl = pnl
                open_trade.pnl_pct = (pnl / balance) * 100
                open_trade.exit_reason = exit_reason
                trades.append(open_trade)
                open_trade = None
                equity_curve.append((candle.timestamp, balance))
        if open_trade is None:
            window = train_candles + test_candles[:i + 1]
            signal = strategy.evaluate(window)
            if signal and signal.stop_loss > 0:
                entry_price = candle.close
                stop_distance = abs(entry_price - signal.stop_loss)
                if stop_distance > 0:
                    risk_amount = balance * (risk_pct / 100)
                    qty = (risk_amount - risk_amount * (fee_pct / 100)) / stop_distance
                    open_trade = BacktestTrade(
                        entry_time=candle.timestamp,
                        exit_time=None,
                        side=signal.side.value,
                        entry_price=entry_price,
                        exit_price=0.0,
                        stop_loss=signal.stop_loss,
                        take_profit=signal.take_profit,
                        qty=qty,
                        pnl=0.0,
                        pnl_pct=0.0,
                        exit_reason="",
                    )
    if open_trade:
        exit_price = test_candles[-1].close
        fee = open_trade.qty * exit_price * (fee_pct / 100)
        if open_trade.side == "BUY":
            raw_pnl = (exit_price - open_trade.entry_price) * open_trade.qty
        else:
            raw_pnl = (open_trade.entry_price - exit_price) * open_trade.qty
        pnl = raw_pnl - fee
        open_trade.exit_time = test_candles[-1].timestamp
        open_trade.exit_price = exit_price
        open_trade.pnl = pnl
        open_trade.pnl_pct = (pnl / balance) * 100
        open_trade.exit_reason = "END"
        balance += pnl
        trades.append(open_trade)
        equity_curve.append((test_candles[-1].timestamp, balance))
    return {
        "trades": trades,
        "equity_curve": equity_curve,
        "initial_balance": initial_balance,
        "final_balance": balance,
        "peak_balance": peak_balance,
    }


def print_stats(results):
    trades = results["trades"]
    initial = results["initial_balance"]
    final = results["final_balance"]
    peak = results["peak_balance"]
    if not trades:
        print("No trades generated.")
        return
    winners = [t for t in trades if t.pnl > 0]
    losers  = [t for t in trades if t.pnl <= 0]
    tp_exits = [t for t in trades if t.exit_reason == "TP"]
    sl_exits = [t for t in trades if t.exit_reason == "SL"]
    win_rate      = len(winners) / len(trades) * 100
    gross_profit  = sum(t.pnl for t in winners)
    gross_loss    = abs(sum(t.pnl for t in losers))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    total_return  = (final - initial) / initial * 100
    min_balance   = min(e[1] for e in results["equity_curve"])
    max_drawdown  = (peak - min_balance) / peak * 100
    avg_win  = gross_profit / len(winners) if winners else 0
    avg_loss = gross_loss / len(losers) if losers else 0
    rr_ratio = avg_win / avg_loss if avg_loss > 0 else float("inf")
    returns = [t.pnl_pct for t in trades]
    sharpe = 0.0
    if len(returns) > 1:
        std = statistics.stdev(returns)
        if std > 0:
            sharpe = statistics.mean(returns) / std
    print("")
    print("=" * 50)
    print("BACKTEST RESULTS")
    print("=" * 50)
    print("Total trades:     " + str(len(trades)))
    print("Win rate:         " + str(round(win_rate, 1)) + "%")
    print("TP exits:         " + str(len(tp_exits)) + "  |  SL exits: " + str(len(sl_exits)))
    print("Avg win:          $" + str(round(avg_win, 2)) + "  |  Avg loss: $" + str(round(avg_loss, 2)))
    print("Risk/reward:      " + str(round(rr_ratio, 2)) + ":1")
    print("Profit factor:    " + str(round(profit_factor, 2)) + "  (>1.5 = good edge)")
    print("Sharpe ratio:     " + str(round(sharpe, 2)) + "  (>1.0 = decent)")
    print("Max drawdown:     " + str(round(max_drawdown, 1)) + "%")
    print("Total return:     " + str(round(total_return, 1)) + "%")
    print("Start balance:    $" + str(round(initial, 2)))
    print("End balance:      $" + str(round(final, 2)))
    print("=" * 50)
    scaled = final * (10 / initial)
    print("")
    print("On $10 starting capital: $" + str(round(scaled, 2)))
    print("Profit/loss: $" + str(round(scaled - 10, 2)))
    print("")
    print("Last 10 trades:")
    print("-" * 55)
    for t in trades[-10:]:
        pnl_str = "$" + str(round(t.pnl, 2))
        print(str(t.entry_time.date()) + "  " + t.side + "  "
              + str(round(t.entry_price, 5)) + " -> " + str(round(t.exit_price, 5))
              + "  " + pnl_str + "  " + t.exit_reason)


def save_equity_curve(results, path="logs/equity_curve.csv"):
    os.makedirs("logs", exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "balance"])
        for ts, bal in results["equity_curve"]:
            writer.writerow([ts.isoformat(), round(bal, 2)])
    print("Equity curve saved to " + path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol",             default="BTCUSDT")
    parser.add_argument("--timeframe",           default="1h")
    parser.add_argument("--years",               type=int,   default=2)
    parser.add_argument("--balance",             type=float, default=10000.0)
    parser.add_argument("--risk",                type=float, default=1.0)
    parser.add_argument("--fast",                type=int,   default=9)
    parser.add_argument("--slow",                type=int,   default=21)
    parser.add_argument("--strategy",            default="ema", help="ema | rsi | bb | funding")
    parser.add_argument("--rsi-period",          type=int,   default=14,     dest="rsi_period")
    parser.add_argument("--oversold",            type=float, default=30.0)
    parser.add_argument("--overbought",          type=float, default=70.0)
    parser.add_argument("--rr-ratio",            type=float, default=2.0,    dest="rr_ratio")
    parser.add_argument("--vol-mult",            type=float, default=1.5,    dest="vol_mult")
    parser.add_argument("--atr-multiplier",      type=float, default=1.5,    dest="atr_multiplier")
    parser.add_argument("--funding-threshold",   type=float, default=0.0005, dest="funding_threshold")
    args = parser.parse_args()

    if args.strategy == "ema":
        from strategies.ema_crossover import EMACrossoverStrategy
        strategy = EMACrossoverStrategy(
            broker_name="backtest",
            symbol=args.symbol,
            timeframe=args.timeframe,
            fast_period=args.fast,
            slow_period=args.slow,
            atr_multiplier=args.atr_multiplier,
            rr_ratio=args.rr_ratio,
        )
    elif args.strategy == "rsi":
        from strategies.rsi_mean_reversion import RSIMeanReversionStrategy
        strategy = RSIMeanReversionStrategy(
            broker_name="backtest",
            symbol=args.symbol,
            timeframe=args.timeframe,
            rsi_period=args.rsi_period,
            oversold=args.oversold,
            overbought=args.overbought,
            atr_multiplier=args.atr_multiplier,
            rr_ratio=args.rr_ratio,
        )
    elif args.strategy == "bb":
        from strategies.bollinger_volume import BollingerVolumeStrategy
        strategy = BollingerVolumeStrategy(
            broker_name="backtest",
            symbol=args.symbol,
            timeframe=args.timeframe,
            bb_period=20,
            bb_std=2.0,
            volume_multiplier=args.vol_mult,
            atr_multiplier=args.atr_multiplier,
            rr_ratio=args.rr_ratio,
        )
    elif args.strategy == "funding":
        from strategies.funding_rate_backtest import FundingRateBacktestStrategy, fetch_historical_funding_rates
        rate_dict = fetch_historical_funding_rates(args.symbol, args.years)
        if not rate_dict:
            print("Could not fetch funding rates.")
            sys.exit(1)
        strategy = FundingRateBacktestStrategy(
            broker_name="backtest",
            symbol=args.symbol,
            timeframe=args.timeframe,
            rate_dict=rate_dict,
            funding_threshold=args.funding_threshold,
            ema_period=args.fast,
            rr_ratio=args.rr_ratio,
        )
    else:
        print("Unknown strategy: " + args.strategy)
        sys.exit(1)

    candles = fetch_candles(args.symbol, args.timeframe, args.years)
    if len(candles) < 100:
        print("Not enough candles.")
        sys.exit(1)

    results = run_backtest(candles, strategy, args.balance, args.risk)
    print_stats(results)
    save_equity_curve(results)


if __name__ == "__main__":
    main()
