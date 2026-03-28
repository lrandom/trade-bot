# Phase 07 — Execution Engine

## Context
- Parent plan: [plan.md](plan.md)
- Depends on: [phase-03-claude-llm-engine.md](phase-03-claude-llm-engine.md), [phase-04-risk-management.md](phase-04-risk-management.md), [phase-05-trading-modes.md](phase-05-trading-modes.md)
- Blocks: phase-08

## Overview
- **Date:** 2026-03-27
- **Priority:** P0
- **Status:** pending
- Binance Futures order placement, auto vs signal mode, partial TP closes (33/33/34%), position monitoring loop, order state machine.

## Key Insights
- Market orders for entry (avoid slippage on limit misses during fast gold moves)
- Three TP orders placed simultaneously at entry: reduce_only market orders triggered by position monitor (Binance Futures does not natively support 3-way partial close via single OCO; use separate TP orders or position monitor)
- Stop loss as a stop-market order placed immediately after entry
- Position monitor polls every 30s; checks mark price vs TP/SL levels; closes partial positions
- `reduce_only=True` on all TP/SL orders to prevent opening new positions accidentally
- State machine prevents double-execution of the same signal

## Requirements

**Functional:**
- `execute_signal(signal_id)` — fetch signal from DB, place entry + SL orders
- `place_tp_orders(trade_id)` — place 3 TP reduce_only orders
- `close_position(symbol)` — close entire position at market
- `cancel_all_orders(symbol)` — cancel all open orders (used by /stop)
- `position_monitor_loop()` — async loop, polls every 30s, checks TP hits
- State machine for trade: `pending → entry_placed → open → tp1_hit → tp2_hit → closed / stopped`

**Non-functional:**
- All Binance calls wrapped with retry (3x, exponential backoff) on `BinanceAPIException`
- `BINANCE_TESTNET` flag switches between testnet and live endpoints
- Order IDs stored in DB for audit trail

## Trader Pattern (liên kết Phase 09)

```python
# bot/trader/base.py
class BaseTrader(ABC):
    @abstractmethod
    async def place_order(self, signal, size) -> Order: ...
    @abstractmethod
    async def close_position(self, order_id, price) -> float: ...
    @abstractmethod
    async def get_open_positions(self) -> list[Order]: ...

# factory.py — engine không biết đang paper hay live
def get_trader(settings) -> BaseTrader:
    if settings.paper_trade:
        return MockTrader(db)       # phase-09
    return RealTrader(binance_client)  # phase-07
```
- `RealTrader` implement ở phase này
- `MockTrader` implement ở phase-09
- Engine chỉ gọi `BaseTrader` interface → switch bằng env flag

## Architecture

```
bot/trader/
├── __init__.py
├── order_manager.py    # place_order(), cancel_order(), set_leverage()
├── trade_executor.py   # execute_signal(), place_tp_orders(), close_position()
├── position_monitor.py # position_monitor_loop(), check_tp_sl()
└── state_machine.py    # TradeState enum, transitions
```

### Order State Machine
```
PENDING
  │ signal approved (manual or auto)
  ▼
ENTRY_PLACED (entry market order sent)
  │ fill confirmed
  ▼
OPEN (SL + TP1/2/3 orders active)
  │ mark price hits TP1
  ▼
TP1_HIT (33% closed, SL moved to break-even)
  │ mark price hits TP2
  ▼
TP2_HIT (66% closed)
  │ mark price hits TP3 or SL
  ▼
CLOSED or STOPPED
```

### TP Position Split
```
Entry quantity = Q
TP1 order: close Q * 0.33
TP2 order: close Q * 0.33
TP3 order: close Q * 0.34  (remainder)
```

### Partial TP Implementation Strategy
- Binance Futures supports multiple reduce_only orders simultaneously
- Place 3 separate `TAKE_PROFIT_MARKET` orders with `closePosition=false` and explicit `quantity`
- After TP1 fill: cancel TP2/TP3, resubmit with correct remaining quantity if needed (position monitor handles)
- After TP1 hit: move SL to break-even (cancel original SL, place new SL at entry price)

## Related Code Files

| Action | File | Description |
|--------|------|-------------|
| create | `bot/execution/order_manager.py` | Low-level order placement |
| create | `bot/execution/trade_executor.py` | High-level trade lifecycle |
| create | `bot/execution/position_monitor.py` | 30s polling loop |
| create | `bot/execution/state_machine.py` | TradeState + transitions |

## Implementation Steps

1. **state_machine.py**:
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

2. **order_manager.py** — `OrderManager` class:
   ```python
   async def set_leverage(self, symbol: str, leverage: int):
       await self.client.futures_change_leverage(symbol=symbol, leverage=leverage)

   async def place_market_order(self, symbol, side, quantity, reduce_only=False) -> dict:
       # side: 'BUY' or 'SELL'
       return await self.client.futures_create_order(
           symbol=symbol, side=side,
           type='MARKET', quantity=quantity,
           reduceOnly=reduce_only
       )

   async def place_stop_market(self, symbol, side, stop_price, quantity) -> dict:
       return await self.client.futures_create_order(
           symbol=symbol, side=side,
           type='STOP_MARKET', stopPrice=stop_price,
           quantity=quantity, reduceOnly=True
       )

   async def place_take_profit_market(self, symbol, side, stop_price, quantity) -> dict:
       return await self.client.futures_create_order(
           symbol=symbol, side=side,
           type='TAKE_PROFIT_MARKET', stopPrice=stop_price,
           quantity=quantity, reduceOnly=True, closePosition=False
       )

   async def cancel_order(self, symbol: str, order_id: int):
       await self.client.futures_cancel_order(symbol=symbol, orderId=order_id)

   async def cancel_all_orders(self, symbol: str):
       await self.client.futures_cancel_all_open_orders(symbol=symbol)
   ```
   Wrap each call with:
   ```python
   async def _with_retry(self, coro, retries=3, base_delay=1.0):
       for attempt in range(retries):
           try:
               return await coro
           except BinanceAPIException as e:
               if attempt == retries - 1: raise
               await asyncio.sleep(base_delay * (2 ** attempt))
   ```

3. **trade_executor.py** — `TradeExecutor`:
   ```python
   async def execute_signal(self, signal_id: str):
       signal = await db_get_signal(signal_id)
       if signal.status != 'approved':
           return
       mode_cfg = get_mode_config(signal.mode)
       balance = await self._get_usdt_balance()
       quantity = calc_position_size(balance, mode_cfg['risk_pct'],
                                     signal.entry, signal.stop_loss,
                                     mode_cfg['leverage'])
       # Set leverage
       await order_mgr.set_leverage('XAUUSDT', mode_cfg['leverage'])
       # Entry
       side = 'BUY' if signal.action == 'BUY' else 'SELL'
       entry_order = await order_mgr.place_market_order('XAUUSDT', side, quantity)
       fill_price = float(entry_order['avgPrice'])
       # SL order
       sl_side = 'SELL' if side == 'BUY' else 'BUY'
       sl_order = await order_mgr.place_stop_market('XAUUSDT', sl_side,
                                                     signal.stop_loss, quantity)
       # TP orders (3-way split)
       tp_orders = []
       for tp_price, tp_qty in [
           (signal.tp1, round(quantity * 0.33, 3)),
           (signal.tp2, round(quantity * 0.33, 3)),
           (signal.tp3, round(quantity * 0.34, 3)),
       ]:
           tp_orders.append(await order_mgr.place_take_profit_market(
               'XAUUSDT', sl_side, tp_price, tp_qty))
       # Persist trade to DB
       trade = Trade(signal_id=signal_id, entry=fill_price,
                     quantity=quantity, status='open', ...)
       await db_save_trade(trade)
       await db_update_signal_status(signal_id, 'executed')

   async def close_position(self, symbol='XAUUSDT'):
       pos = await self.client.futures_position_information(symbol=symbol)
       qty = abs(float(pos[0]['positionAmt']))
       if qty == 0: return
       side = 'SELL' if float(pos[0]['positionAmt']) > 0 else 'BUY'
       await order_mgr.cancel_all_orders(symbol)
       await order_mgr.place_market_order(symbol, side, qty, reduce_only=True)
   ```

4. **position_monitor.py** — `position_monitor_loop()`:
   ```python
   async def position_monitor_loop():
       while True:
           await asyncio.sleep(30)
           open_trades = await db_get_open_trades()
           for trade in open_trades:
               mark_price = await get_mark_price('XAUUSDT')
               await check_tp_progression(trade, mark_price)

   async def check_tp_progression(trade, mark_price):
       # Detect which TPs have been hit based on mark price vs tp1/tp2/tp3
       # After TP1 hit: move SL to break-even
       # After TP2 hit: trail SL
       # Update DB trade.tp_hit count and state
   ```
   Note: Primary TP execution is done by exchange orders. Monitor is a safety net to update DB state and move SL.

5. **SL trail after TP1**: cancel original SL order, place new SL at `entry_price` (break-even)

6. **Auto vs signal mode**:
   - Auto mode (`auto_trade=true`): `trade_executor.execute_signal()` called directly from analysis cycle
   - Signal mode: signal saved to DB with status `pending`, Telegram message sent with approve/reject buttons

## Todo

- [ ] `state_machine.py` TradeState enum
- [ ] `order_manager.py` all order types + retry wrapper
- [ ] `trade_executor.py` execute_signal() full flow
- [ ] `trade_executor.py` close_position()
- [ ] TP split 33/33/34 quantity calculation
- [ ] SL move to break-even after TP1
- [ ] `position_monitor.py` 30s loop
- [ ] Test on Binance testnet before live

## Success Criteria
- Entry + SL + 3 TP orders placed on testnet for a BUY signal
- TP1 hit → DB `tp_hit=1` updated, SL moved to entry
- `/close` command closes full position at market
- Retry wrapper retries on `BinanceAPIException` up to 3x

## Risk Assessment
| Risk | Impact | Mitigation |
|------|--------|------------|
| Double execution of same signal | Critical | Check `signal.status != 'approved'` guard; use DB transaction |
| TP order partially filled, remaining qty wrong | High | Position monitor reconciles actual position size before resubmitting |
| Leverage not set before entry order | Critical | `set_leverage()` always called first in `execute_signal()` |
| Binance testnet different behavior than live | Medium | Test core logic on testnet; review Binance XAUUSDT contract spec |
| SL order rejected (price too close to mark) | Medium | Min distance validation before placement |

## Security Considerations
- `reduce_only=True` on all TP/SL orders — prevents accidentally opening new position on wrong side
- Never hardcode `XAUUSDT` symbol in all methods — pass as parameter for testability
- Trade quantity precision: XAUUSDT has 3 decimal precision on Binance — always `round(qty, 3)`
- Testnet flag must be verified before any live order

## Next Steps
- Phase 08: `trade_executor.execute_signal()` called from analysis cycle in auto mode
- Phase 08: `position_monitor_loop()` started as background asyncio task
