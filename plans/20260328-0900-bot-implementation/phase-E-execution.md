# Phase E — Execution Engine

## Context
- Parent plan: [plan.md](plan.md)
- Design specs: `plans/20260327-1200-gold-trading-bot/phase-07-execution-engine.md`, `phase-09-paper-trade.md`
- Depends on: [phase-C-llm-engine.md](phase-C-llm-engine.md), [phase-D-risk-modes.md](phase-D-risk-modes.md)
- Blocks: Phase G (Orchestration)

## Overview
- **Date:** 2026-03-28
- **Priority:** P0
- **Status:** pending
- `BaseTrader` interface, `RealTrader` (Binance Futures), `MockTrader` (paper trade), factory pattern, order state machine, position monitor loop, 3-way TP partial close.

## Key Insights
- `PAPER_TRADE=true` default — `MockTrader` is the primary path during development
- Factory pattern: engine calls `BaseTrader` only — swapped via env flag, no code change
- Market orders for entry (avoids limit miss during fast gold moves)
- 3 separate `TAKE_PROFIT_MARKET` orders with `reduce_only=True, closePosition=False`
- After TP1 hit: move SL to break-even (cancel original SL, new SL at entry)
- `reduce_only=True` on all SL/TP — prevents accidentally opening new position
- Retry wrapper: 3x exponential backoff on `BinanceAPIException`
- Position monitor polls every 30s — safety net for DB state sync; primary execution by exchange orders
- `MockTrader` simulates SL/TP hits using real mark price from Binance REST

## Requirements

**Functional:**
- `BaseTrader`: `place_order()`, `close_position()`, `get_open_positions()`
- `RealTrader`: full Binance Futures execution — entry + SL + 3 TP orders
- `MockTrader`: paper trade — SQLite-backed, no Binance API calls for orders
- `TraderFactory.get_trader(settings)` — returns MockTrader or RealTrader
- `execute_signal(signal_id)` — fetch signal → size → place orders → persist
- `close_position(symbol)` — close entire open position at market
- `cancel_all_orders(symbol)` — used by `/stop` emergency command
- `position_monitor_loop()` — 30s async polling, checks TP progression + SL hits
- State machine: `PENDING → ENTRY_PLACED → OPEN → TP1_HIT → TP2_HIT → CLOSED/STOPPED`

**Non-functional:**
- All Binance calls wrapped in 3x retry with exponential backoff
- `BINANCE_TESTNET` flag switches endpoints
- Order IDs stored in DB for audit trail
- `XAUUSDT` quantity precision: 3 decimal places

## Architecture

```
bot/trader/
├── __init__.py
├── base.py             # BaseTrader ABC
├── real_trader.py      # Live Binance execution
├── mock_trader.py      # Paper trade (SQLite)
├── factory.py          # get_trader(settings)
├── order_manager.py    # Low-level Binance order API + retry
├── trade_executor.py   # High-level execute_signal(), close_position()
├── position_monitor.py # 30s monitoring loop
└── state_machine.py    # TradeState enum
```

### Trader Interface
```python
class BaseTrader(ABC):
    @abstractmethod
    async def place_order(self, signal: TradingSignal, size: float) -> dict: ...

    @abstractmethod
    async def close_position(self, symbol: str) -> float: ...

    @abstractmethod
    async def get_open_positions(self) -> list[dict]: ...
```

### Order State Machine
```
PENDING → ENTRY_PLACED → OPEN → TP1_HIT → TP2_HIT → CLOSED | STOPPED
```

### TP Split
```
Q = total quantity
TP1: Q * 0.33 (close)
TP2: Q * 0.33 (close)
TP3: Q * 0.34 (close remainder)
```

## Related Code Files

| Action | File | Description |
|--------|------|-------------|
| create | `bot/trader/base.py` | `BaseTrader` ABC |
| create | `bot/trader/real_trader.py` | Live Binance execution |
| create | `bot/trader/mock_trader.py` | Paper trade mock |
| create | `bot/trader/factory.py` | `get_trader(settings)` |
| create | `bot/trader/order_manager.py` | Low-level order API + retry |
| create | `bot/trader/trade_executor.py` | `execute_signal()`, `close_position()` |
| create | `bot/trader/position_monitor.py` | 30s polling loop |
| create | `bot/trader/state_machine.py` | `TradeState` enum |
| modify | `schema.sql` | Add `paper_orders`, `paper_stats` tables |

## Implementation Steps

1. **`state_machine.py`**:
   ```python
   from enum import Enum
   class TradeState(Enum):
       PENDING      = "pending"
       ENTRY_PLACED = "entry_placed"
       OPEN         = "open"
       TP1_HIT      = "tp1_hit"
       TP2_HIT      = "tp2_hit"
       CLOSED       = "closed"
       STOPPED      = "stopped"
   ```

2. **`base.py`** — `BaseTrader` ABC as above + docstrings

3. **`order_manager.py`** — `OrderManager` class:
   - `set_leverage(symbol, leverage)`: `client.futures_change_leverage(...)`
   - `place_market_order(symbol, side, quantity, reduce_only=False) -> dict`
   - `place_stop_market(symbol, side, stop_price, quantity) -> dict`
   - `place_take_profit_market(symbol, side, stop_price, quantity) -> dict` — `reduceOnly=True, closePosition=False`
   - `cancel_order(symbol, order_id)`
   - `cancel_all_orders(symbol)`: `client.futures_cancel_all_open_orders(symbol=symbol)`
   - `_with_retry(coro, retries=3, base_delay=1.0)`: `asyncio.sleep(base_delay * 2**attempt)` on `BinanceAPIException`

4. **`real_trader.py`** — `RealTrader(BaseTrader)`:
   - Delegates to `OrderManager`
   - `place_order(signal, size)`: `set_leverage` → entry market order → SL stop-market → 3 TP orders → return fill dict
   - `close_position(symbol)`: get current position amt → cancel all → market close

5. **`mock_trader.py`** — `MockTrader(BaseTrader)`:
   - All state in SQLite `paper_orders` table
   - `place_order(signal, size)`: INSERT into `paper_orders` with `status='open'`; return PaperOrder dict
   - `close_position(symbol)`: UPDATE `paper_orders` with `close_time`, `close_price`, calculated `pnl_usd`, `status='closed'`
   - `get_open_positions()`: SELECT from `paper_orders WHERE status='open'`
   - `_calc_pnl(order, close_price)`: `(close - entry) * size` for BUY; reversed for SELL
   - Apply simulated slippage: `entry_fill = entry * (1 + 0.0005)` for BUY, `entry * (1 - 0.0005)` for SELL

6. **`factory.py`**:
   ```python
   def get_trader(settings, db, binance_client) -> BaseTrader:
       if settings.PAPER_TRADE:
           return MockTrader(db)
       return RealTrader(OrderManager(binance_client))
   ```

7. **`trade_executor.py`** — `TradeExecutor`:
   - `execute_signal(signal_id, db, trader, risk_engine)`:
     1. Fetch signal from DB; guard `signal.status != 'approved'` → return
     2. Fetch live balance
     3. `await risk_engine.pre_trade_check(signal, mode, balance, db)`
     4. `calc_position_size(balance, risk_pct, entry, sl, leverage)`
     5. `await trader.place_order(signal, quantity)`
     6. INSERT into `trades` table with `status=OPEN`
     7. UPDATE `signals.status='executed'`
   - `close_position(symbol, db, trader)`:
     1. `await trader.close_position(symbol)`
     2. UPDATE `trades` SET `status='closed'`, `close_price`, `closed_at`, `pnl`
     3. `await update_daily_pnl(db, pnl)`

8. **`position_monitor.py`** — `position_monitor_loop(db, trader, tg_bot)`:
   ```python
   async def position_monitor_loop(db, trader, tg_bot):
       while True:
           await asyncio.sleep(30)
           try:
               open_trades = await db_get_open_trades(db)
               for trade in open_trades:
                   mark_price = await get_mark_price('XAUUSDT')
                   await check_tp_progression(trade, mark_price, db, tg_bot)
           except Exception as e:
               logger.exception(f"Position monitor error: {e}")

   async def check_tp_progression(trade, mark_price, db, tg_bot):
       # For LONG: mark >= tp1/tp2/tp3 → hit; mark <= sl → stopped
       # For SHORT: reversed
       # After TP1 hit: move SL to break-even
       # Update DB trade.tp_hit count and state
       # Send Telegram notification on each TP/SL hit
   ```
   Note: For RealTrader — exchange handles actual fills; monitor just syncs DB state.
   For MockTrader — monitor IS the execution (simulates TP/SL fills).

9. **Schema additions** to `schema.sql` (for paper trade):
   ```sql
   CREATE TABLE IF NOT EXISTS paper_orders (
       id TEXT PRIMARY KEY,
       symbol TEXT DEFAULT 'XAUUSDT',
       side TEXT,           -- BUY | SELL
       mode TEXT,
       entry REAL,
       stop_loss REAL,
       tp1 REAL, tp2 REAL, tp3 REAL,
       size REAL,
       open_time DATETIME,
       close_time DATETIME,
       close_price REAL,
       pnl_usd REAL,
       pnl_pct REAL,
       status TEXT,         -- open | tp1_hit | tp2_hit | closed | stopped
       signal_id TEXT,
       llm_provider TEXT,
       confidence INTEGER
   );

   CREATE TABLE IF NOT EXISTS paper_stats (
       date DATE PRIMARY KEY,
       mode TEXT,
       total_trades INTEGER,
       wins INTEGER, losses INTEGER,
       win_rate REAL, profit_factor REAL,
       total_pnl_usd REAL, max_drawdown REAL
   );
   ```

## Todo

- [ ] `state_machine.py` TradeState enum
- [ ] `base.py` BaseTrader ABC
- [ ] `order_manager.py` all order types + retry wrapper
- [ ] `real_trader.py` RealTrader — full execution flow
- [ ] `mock_trader.py` MockTrader — SQLite paper orders + slippage sim
- [ ] `factory.py` get_trader()
- [ ] `trade_executor.py` execute_signal() + close_position()
- [ ] `position_monitor.py` 30s loop + check_tp_progression()
- [ ] SL move to break-even after TP1 (RealTrader)
- [ ] Add `paper_orders` + `paper_stats` to schema.sql
- [ ] Test on Binance testnet: entry + SL + 3 TP placed correctly

## Success Criteria
- `PAPER_TRADE=true`: `place_order()` writes to `paper_orders`, no Binance API calls
- `PAPER_TRADE=false`: entry + SL + 3 TP orders placed on testnet
- TP1 hit → `trade.tp_hit=1` updated, SL moved to entry (RealTrader)
- MockTrader: SL hit → `paper_orders.status='stopped'`, PnL correct
- `/close` command closes full position at market
- Retry wrapper retries `BinanceAPIException` up to 3x

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| Double execution of same signal | Critical | Guard `signal.status != 'approved'` in DB transaction |
| TP order rejected (price too close to mark) | Medium | Min distance check before placement |
| Leverage not set before entry | Critical | `set_leverage()` always first in execute_signal() |
| Wrong position amount on partial fills | High | Position monitor reconciles actual size before resubmit |
| Paper PnL diverges from live (slippage) | Medium | Add 0.05% simulated slippage in MockTrader |

## Security Considerations
- `reduce_only=True` on all TP/SL orders — prevents accidental new position
- Symbol always passed as parameter, never hardcoded
- `XAUUSDT` quantity precision: always `round(qty, 3)` per Binance contract spec
- `BINANCE_TESTNET=true` must be verified before any live order placement

## Next Steps
- Phase G: `trade_executor.execute_signal()` called from analysis cycle in auto mode
- Phase G: `position_monitor_loop()` started as background `asyncio.create_task()`
- Phase H: `/paper` commands use `MockTrader` state via DB queries
