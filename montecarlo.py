"""
montecarlo.py - Block Bootstrap Monte Carlo stress tester

Runs N simulations by resampling blocks of trades from backtest results.
Block bootstrap preserves the clustering of wins/losses that occurs in real markets.

Usage:
  python3 montecarlo.py --symbol EURUSD --strategy ema --years 2 --timeframe 4h --rr-ratio 3.0
  python3 montecarlo.py --portfolio  # runs all three pairs combined
"""

import argparse
import random
import statistics
import os
import sys


def run_backtest_for_mc(symbol, strategy, years, timeframe, rr_ratio, atr_multiplier):
    """Run backtest and return list of trade PnL percentages."""
    from backtest import fetch_candles, run_backtest

    if strategy == "ema":
        from strategies.ema_crossover import EMACrossoverStrategy
        strat = EMACrossoverStrategy(
            broker_name="backtest", symbol=symbol, timeframe=timeframe,
            fast_period=9, slow_period=21,
            atr_multiplier=atr_multiplier, rr_ratio=rr_ratio,
        )
    elif strategy == "rsi":
        from strategies.rsi_mean_reversion import RSIMeanReversionStrategy
        strat = RSIMeanReversionStrategy(
            broker_name="backtest", symbol=symbol, timeframe=timeframe,
            rsi_period=14, oversold=30.0, overbought=70.0,
            atr_multiplier=atr_multiplier, rr_ratio=rr_ratio,
        )
    else:
        raise ValueError("Unknown strategy: " + strategy)

    candles = fetch_candles(symbol, timeframe, years)
    results = run_backtest(candles, strat, initial_balance=10000.0, risk_pct=1.0)
    trades = results["trades"]
    return [t.pnl_pct for t in trades], results


def block_bootstrap(trade_returns, n_trades, block_size=5):
    """
    Resample trade returns using block bootstrap.
    Blocks of consecutive trades are resampled to preserve win/loss clustering.
    """
    if len(trade_returns) < block_size:
        return random.choices(trade_returns, k=n_trades)

    blocks = []
    for i in range(0, len(trade_returns) - block_size + 1):
        blocks.append(trade_returns[i:i + block_size])

    resampled = []
    while len(resampled) < n_trades:
        block = random.choice(blocks)
        resampled.extend(block)

    return resampled[:n_trades]


def simulate(trade_returns, n_simulations=1000, initial_balance=10000.0, risk_pct=1.0):
    """
    Run N Monte Carlo simulations.
    Returns list of (final_balance, max_drawdown) for each simulation.
    """
    n_trades = len(trade_returns)
    if n_trades == 0:
        return []

    results = []
    for _ in range(n_simulations):
        resampled = block_bootstrap(trade_returns, n_trades)
        balance = initial_balance
        peak = initial_balance
        min_balance = initial_balance

        for pct in resampled:
            # Apply the return as a percentage of current balance
            pnl = balance * (pct / 100)
            balance += pnl
            peak = max(peak, balance)
            min_balance = min(min_balance, balance)

        max_dd = (peak - min_balance) / peak * 100 if peak > 0 else 0
        total_return = (balance - initial_balance) / initial_balance * 100
        results.append((total_return, max_dd, balance))

    return results


def print_mc_results(symbol, trade_returns, mc_results, initial_balance=10000.0):
    returns = [r[0] for r in mc_results]
    drawdowns = [r[1] for r in mc_results]
    final_balances = [r[2] for r in mc_results]

    returns.sort()
    drawdowns.sort()

    profitable = len([r for r in returns if r > 0])
    prob_profitable = profitable / len(returns) * 100

    print("")
    print("=" * 55)
    print("MONTE CARLO RESULTS — " + symbol + " (" + str(len(mc_results)) + " simulations)")
    print("=" * 55)
    print("Based on " + str(len(trade_returns)) + " historical trades")
    print("")
    print("RETURN DISTRIBUTION:")
    print("  5th percentile:  " + str(round(returns[int(len(returns)*0.05)], 1)) + "%  (worst case)")
    print("  25th percentile: " + str(round(returns[int(len(returns)*0.25)], 1)) + "%")
    print("  Median:          " + str(round(returns[int(len(returns)*0.50)], 1)) + "%")
    print("  75th percentile: " + str(round(returns[int(len(returns)*0.75)], 1)) + "%")
    print("  95th percentile: " + str(round(returns[int(len(returns)*0.95)], 1)) + "%  (best case)")
    print("")
    print("DRAWDOWN DISTRIBUTION:")
    print("  Median max DD:   " + str(round(drawdowns[int(len(drawdowns)*0.50)], 1)) + "%")
    print("  Worst 5% max DD: " + str(round(drawdowns[int(len(drawdowns)*0.95)], 1)) + "%")
    print("")
    print("Probability of profit: " + str(round(prob_profitable, 1)) + "%")
    print("")

    # Scale to $5K FTM account at 0.5% risk
    scale = 5000 / initial_balance * 0.5  # 0.5% risk vs 1% in backtest
    med_return = returns[int(len(returns)*0.50)]
    worst_return = returns[int(len(returns)*0.05)]
    best_return = returns[int(len(returns)*0.95)]

    print("ON $5K FTM ACCOUNT (0.5% risk per trade):")
    print("  Median outcome:  $" + str(round(5000 * (1 + med_return/100 * 0.5), 0)))
    print("  Worst 5%:        $" + str(round(5000 * (1 + worst_return/100 * 0.5), 0)))
    print("  Best 5%:         $" + str(round(5000 * (1 + best_return/100 * 0.5), 0)))
    print("=" * 55)


def run_portfolio_mc(pairs_returns, n_simulations=1000, initial_balance=10000.0):
    """
    Portfolio Monte Carlo — combines trades from all pairs.
    Simulates trading all pairs simultaneously.
    """
    # Combine all trade returns
    all_returns = []
    for returns in pairs_returns.values():
        all_returns.extend(returns)

    if not all_returns:
        return []

    return simulate(all_returns, n_simulations, initial_balance)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol",          default="EURUSD")
    parser.add_argument("--strategy",        default="ema")
    parser.add_argument("--years",           type=int,   default=2)
    parser.add_argument("--timeframe",       default="4h")
    parser.add_argument("--rr-ratio",        type=float, default=3.0,  dest="rr_ratio")
    parser.add_argument("--atr-multiplier",  type=float, default=1.5,  dest="atr_multiplier")
    parser.add_argument("--simulations",     type=int,   default=1000)
    parser.add_argument("--portfolio",       action="store_true",
                        help="Run Monte Carlo on EURUSD+USDCAD+USDCHF portfolio")
    args = parser.parse_args()

    if args.portfolio:
        pairs = ["EURUSD", "USDCAD", "USDCHF"]
        pairs_returns = {}
        print("Running backtests for portfolio...")
        for pair in pairs:
            print("\n--- " + pair + " ---")
            returns, results = run_backtest_for_mc(
                pair, args.strategy, args.years, args.timeframe,
                args.rr_ratio, args.atr_multiplier
            )
            pairs_returns[pair] = returns
            mc = simulate(returns, args.simulations)
            print_mc_results(pair, returns, mc)

        print("\n\nRunning PORTFOLIO Monte Carlo...")
        all_returns = []
        for r in pairs_returns.values():
            all_returns.extend(r)
        portfolio_mc = simulate(all_returns, args.simulations)
        print_mc_results("PORTFOLIO (3 pairs combined)", all_returns, portfolio_mc)

    else:
        print("Running backtest for " + args.symbol + "...")
        returns, results = run_backtest_for_mc(
            args.symbol, args.strategy, args.years, args.timeframe,
            args.rr_ratio, args.atr_multiplier
        )
        if not returns:
            print("No trades generated.")
            sys.exit(1)
        mc = simulate(returns, args.simulations)
        print_mc_results(args.symbol, returns, mc)


if __name__ == "__main__":
    main()
