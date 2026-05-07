"""
backtest.py
Walk-forward backtester. Tests any strategy against historical data.

Usage:
  python3 backtest.py                        # test EMA 9/21 on BTCUSDT 1h
  python3 backtest.py --symbol ETHUSDT       # different symbol
  python3 backtest.py --timeframe 4h         # different timeframe
  python3 backtest.py --years 3              # more history

Output:
  - Trade log
  - Win rate, profit factor, Sharpe, max drawdown
  - Equity curve saved to logs/equity_curve.csv
"""

import argparse
import csv
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logging.basicConfig(level=logging.WARNING)

# ── Fetch historical data from Binance (no API key needed for public data) ───

def fetch_candles(symbol: str, timeframe: str, years: int = 2) -> list:
    from binance.client import Client
    from core.models import Candle
    import time

    # Calculate target number of candles
    candles_per_year = {
        "1h": 365 * 24,
        "4h": 365 * 6,
        "1d": 365,
        "15m": 365 * 96,
    }
    target = candles_per_year.get(timeframe, 365 * 24) * years
    print(f"Fetching {symbol} {timeframe} candles from Binance (~{target} candles)...")

    # Use real API key if available, else anonymous
    from dotenv import load_dotenv
    import os
    load_dotenv()
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

        # Prepend to build oldest-first list
        all_klines = klines + all_klines
        end_time = klines[0][0] - 1  # go further back in time

        print(f"  Fetched {len(all_klines)} candles so far...", end="
")

        if len(klines) < 1000:
            break  # no more history available

        time.sleep(0.1)  # be polite to the API

    # Trim to target
    all_klines = all_klines[-target:]

    candles = [
        Candle(
            symbol=symbol,
            timestamp=datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc),
            open=float(k[1]),
            high=float(k[2]),
            low=float(k[3]),
            close=float(k[4]),
            volume=float(k[5]),
            timeframe=timeframe,
        )
        for k in all_klines
    ]

    print(f"
Fetched {len(candles)} candles "
          f"({candles[0].timestamp.date()} → {candles[-1].timestamp.date()})")
    return candles


# ── Backtest engine ───────────────────────────────────────────────────────────

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
    exit_reason: str  # "TP", "SL", "END"


def run_backtest(
    candles: list,
    strategy,
    initial_balance: float = 10000.0,
    risk_pct: float = 1.0,
    fee_pct: float = 0.1,       # Binance taker fee 0.1%
    train_pct: float = 0.6,     # 60% train, 40% test
) -> dict:
    """
    Walk-forward backtest:
    - Train period: strategy is not evaluated (avoids in-sample bias)
    - Test period: strategy signals are taken and evaluated
    """
    split = int(len(candles) * train_pct)
    train_candles = candles[:split]
    test_candles = candles[split:]

    print(f"\nTrain period: {train_candles[0].timestamp.date()} → "
          f"{train_candles[-1].timestamp.date()} ({len(train_candles)} candles)")
    print(f"Test period:  {test_candles[0].timestamp.date()} → "
          f"{test_candles[-1].timestamp.date()} ({len(test_candles)} candles)")

    balance = initial_balance
    peak_balance = initial_balance
    trades = []
    equity_curve = [(test_candles[0].timestamp, balance)]
    open_trade: Optional[BacktestTrade] = None

    for i in range(1, len(test_candles)):
        candle = test_candles[i]
        prev_candle = test_candles[i - 1]

        # ── Check if open trade hit SL or TP on this candle ──────────────
        if open_trade:
            hit_sl = hit_tp = False

            if open_trade.side == "BUY":
                hit_sl = candle.low <= open_trade.stop_loss
                hit_tp = candle.high >= open_trade.take_profit
            else:
                hit_sl = candle.high >= open_trade.stop_loss
                hit_tp = candle.low <= open_trade.take_profit

            if hit_sl or hit_tp:
                exit_price = open_trade.take_profit if hit_tp else open_trade.stop_loss
                exit_reason = "TP" if hit_tp else "SL"

                # Calculate P&L including fees
                fee = open_trade.qty * exit_price * (fee_pct / 100)
                if open_trade.side == "BUY":
                    raw_pnl = (exit_price - open_trade.entry_price) * open_trade.qty
                else:
                    raw_pnl = (open_trade.entry_price - exit_price) * open_trade.qty
                pnl = raw_pnl - fee
                pnl_pct = (pnl / balance) * 100

                balance += pnl
                peak_balance = max(peak_balance, balance)

                open_trade.exit_time = candle.timestamp
                open_trade.exit_price = exit_price
                open_trade.pnl = pnl
                open_trade.pnl_pct = pnl_pct
                open_trade.exit_reason = exit_reason
                trades.append(open_trade)
                open_trade = None
                equity_curve.append((candle.timestamp, balance))

        # ── Ask strategy for a signal ─────────────────────────────────────
        if open_trade is None:
            # Feed all train candles + test candles up to now
            window = train_candles + test_candles[:i + 1]
            signal = strategy.evaluate(window)

            if signal and signal.stop_loss > 0:
                entry_price = candle.close
                stop_distance = abs(entry_price - signal.stop_loss)
                if stop_distance > 0:
                    risk_amount = balance * (risk_pct / 100)
                    fee = risk_amount * (fee_pct / 100)
                    qty = (risk_amount - fee) / stop_distance

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

    # Close any open trade at end of test period
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


# ── Statistics ────────────────────────────────────────────────────────────────

def print_stats(results: dict) -> None:
    trades = results["trades"]
    initial = results["initial_balance"]
    final = results["final_balance"]
    peak = results["peak_balance"]

    if not trades:
        print("\nNo trades generated. Strategy may need adjustment.")
        return

    winners = [t for t in trades if t.pnl > 0]
    losers  = [t for t in trades if t.pnl <= 0]
    tp_exits = [t for t in trades if t.exit_reason == "TP"]
    sl_exits = [t for t in trades if t.exit_reason == "SL"]

    win_rate    = len(winners) / len(trades) * 100
    gross_profit = sum(t.pnl for t in winners)
    gross_loss   = abs(sum(t.pnl for t in losers))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    total_return  = (final - initial) / initial * 100
    max_drawdown  = (peak - min(e[1] for e in results["equity_curve"])) / peak * 100

    avg_win  = gross_profit / len(winners) if winners else 0
    avg_loss = gross_loss / len(losers) if losers else 0
    rr_ratio = avg_win / avg_loss if avg_loss > 0 else float("inf")

    # Sharpe (simplified — using trade returns)
    import statistics
    returns = [t.pnl_pct for t in trades]
    sharpe = (statistics.mean(returns) / statistics.stdev(returns)
              if len(returns) > 1 and statistics.stdev(returns) > 0 else 0)

    print("\n" + "="*50)
    print("BACKTEST RESULTS")
    print("="*50)
    print(f"Total trades:     {len(trades)}")
    print(f"Win rate:         {win_rate:.1f}%")
    print(f"TP exits:         {len(tp_exits)}  |  SL exits: {len(sl_exits)}")
    print(f"Avg win:          ${avg_win:.2f}  |  Avg loss: ${avg_loss:.2f}")
    print(f"Risk/reward:      {rr_ratio:.2f}:1")
    print(f"Profit factor:    {profit_factor:.2f}  (>1.5 = good edge)")
    print(f"Sharpe ratio:     {sharpe:.2f}  (>1.0 = decent)")
    print(f"Max drawdown:     {max_drawdown:.1f}%")
    print(f"Total return:     {total_return:.1f}%")
    print(f"Start balance:    ${initial:,.2f}")
    print(f"End balance:      ${final:,.2f}")
    print("="*50)

    # Scaled to $10
    scaled = final * (10 / initial)
    print(f"\nOn $10 starting capital: ${scaled:.2f} final balance")
    print(f"That's ${scaled - 10:.2f} profit/loss over the test period")

    # Print last 10 trades
    print("\nLast 10 trades:")
    print(f"{'Date':<12} {'Side':<5} {'Entry':>8} {'Exit':>8} "
          f"{'P&L':>8} {'Reason':<6}")
    print("-" * 55)
    for t in trades[-10:]:
        print(f"{str(t.entry_time.date()):<12} {t.side:<5} "
              f"{t.entry_price:>8.2f} {t.exit_price:>8.2f} "
              f"{'$'+str(round(t.pnl,2)):>8} {t.exit_reason:<6}")


def save_equity_curve(results: dict, path: str = "logs/equity_curve.csv") -> None:
    os.makedirs("logs", exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "balance"])
        for ts, bal in results["equity_curve"]:
            writer.writerow([ts.isoformat(), round(bal, 2)])
    print(f"\nEquity curve saved to {path}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Strategy Backtester")
    parser.add_argument("--symbol",    default="BTCUSDT")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--years",     type=int, default=2)
    parser.add_argument("--balance",   type=float, default=10000.0)
    parser.add_argument("--risk",      type=float, default=1.0)
    parser.add_argument("--fast",      type=int, default=9)
    parser.add_argument("--slow",      type=int, default=21)
    args = parser.parse_args()

    from strategies.ema_crossover import EMACrossoverStrategy
    strategy = EMACrossoverStrategy(
        broker_name="backtest",
        symbol=args.symbol,
        timeframe=args.timeframe,
        fast_period=args.fast,
        slow_period=args.slow,
    )

    candles = fetch_candles(args.symbol, args.timeframe, args.years)
    if len(candles) < 100:
        print("Not enough candles. Check symbol/timeframe.")
        sys.exit(1)

    results = run_backtest(
        candles=candles,
        strategy=strategy,
        initial_balance=args.balance,
        risk_pct=args.risk,
    )

    print_stats(results)
    save_equity_curve(results)


if __name__ == "__main__":
    main()
