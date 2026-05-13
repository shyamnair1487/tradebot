# Trading Bot

A clean, extensible trading bot architecture for Binance (live) and FundedTraderMarkets (stub).

## Architecture

```
main.py                   ← entry point, wires everything
core/
  models.py               ← shared data structures (Signal, Order, Candle...)
  ledger.py               ← append-only audit log (written before orders sent)
  risk.py                 ← mandatory risk gate (position sizing, drawdown limits)
  engine.py               ← decision pipeline: strategy → risk → execution → ledger
brokers/
  base.py                 ← interface every broker must implement
  binance_broker.py       ← Binance spot implementation
  ftm_broker.py           ← FundedTraderMarkets stub (implement when API confirmed)
strategies/
  base.py                 ← base class with EMA/ATR helpers
  ema_crossover.py        ← example strategy (plug in your own)
logs/
  ledger.jsonl            ← every signal, decision, and fill (auto-created)
  bot.log                 ← human-readable log (auto-created)
```

## Setup

```bash
git clone <repo>
cd tradebot
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your API keys
```

## Running

```bash
# Dry run (no real orders) — start here
python main.py --dry-run

# Live on Binance testnet (BINANCE_TESTNET=true in .env)
python main.py

# Live trading — set BINANCE_TESTNET=false in .env first
python main.py --interval 300   # poll every 5 minutes
```

## Adding a Strategy

1. Create `strategies/my_strategy.py`
2. Subclass `BaseStrategy`, implement `evaluate(candles) -> Optional[Signal]`
3. Signal MUST have a `stop_loss` price set
4. Register it in `main.py` → `build_strategies()`

```python
# strategies/my_strategy.py
from strategies.base import BaseStrategy
from core.models import Signal, Side

class MyStrategy(BaseStrategy):
    @property
    def name(self): return "MyStrategy"

    def evaluate(self, candles):
        # your logic here
        # return Signal(...) or None
        pass
```

## Risk defaults (edit in .env)

| Setting | Default | Purpose |
|---|---|---|
| RISK_PER_TRADE_PCT | 1.0 | % of balance per trade |
| MAX_OPEN_TRADES | 3 | concurrent positions limit |
| MAX_DAILY_DRAWDOWN_PCT | 4.0 | pause trading if daily loss hits this |
| MAX_TOTAL_DRAWDOWN_PCT | 8.0 | halt bot permanently until manual reset |

## FundedTraderMarkets integration

1. Log into your FTM dashboard → find API docs
2. If MT4/MT5: uncomment MetaApi in `requirements.txt`, implement `brokers/ftm_broker.py`
3. Set `ACTIVE_BROKER=ftm` or `ACTIVE_BROKER=both` in `.env`
4. Add FTM strategies in `main.py` → `build_strategies()`

## Ledger format

Every event is a JSON line in `logs/ledger.jsonl`:
- `SIGNAL` — raw strategy output before risk check
- `RISK_DECISION` — approved/rejected + qty + reason
- `ORDER` — order state at creation and after fill/rejection
- `HALT` — trading halted due to drawdown limit
- `ERROR` — caught exceptions with context

Use this to audit every decision the bot made.
# Live trading started 2026-05-13
