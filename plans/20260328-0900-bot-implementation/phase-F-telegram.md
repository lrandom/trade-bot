# Phase F — Telegram Bot

## Context
- Parent plan: [plan.md](plan.md)
- Design spec: `plans/20260327-1200-gold-trading-bot/phase-06-telegram-bot.md`
- Depends on: [phase-A-foundation.md](phase-A-foundation.md)
- Blocks: Phase G (Orchestration)

## Overview
- **Date:** 2026-03-28
- **Priority:** P1
- **Status:** pending
- python-telegram-bot v20+ async, 9 commands + 1 callback, inline keyboard approve/reject flow, signal formatter, notification system. All commands restricted to `TELEGRAM_CHAT_ID`.

## Key Insights
- v20 fully async — all handlers must be `async def`
- Run with `updater.start_polling()` (non-blocking) inside asyncio loop, NOT `app.run_polling()` (blocking)
- Authorization: check `update.effective_user.id == TELEGRAM_CHAT_ID` — silently ignore others
- `callback_data` format: `"{action}_{signal_id}"` — UUID is 36 chars, well within 64 byte limit
- Store `telegram_msg_id` in `signals` table — needed to edit message after approve/reject
- Telegram 4096 char limit — truncate `reasoning` to 300 chars in formatter
- Duplicate approve button press: check `signal.status` before executing — idempotent

## Requirements

**Functional:**
- Commands: `/signal /status /balance /mode /auto /close /stop /history`
- `/paper` commands (Phase H): `/paper on|off|status|history|stats|reset`
- `/cost` commands (Phase I): `/cost today|mtd|from...`
- `/health` command (Phase J)
- `/filter` commands (Phase K)
- Inline keyboard on signal messages: Approve / Reject
- Edit signal message after approve/reject
- Notifications: trade open, TP hit, SL hit, circuit breaker, ATR spike

**Non-functional:**
- All unauthorized requests silently ignored
- Telegram errors logged but never crash main loop
- Message send wrapped in try/except

## Architecture

```
bot/telegram/
├── __init__.py
├── bot.py           # Application builder, handler registration
├── handlers/
│   ├── __init__.py
│   ├── commands.py  # All /command handlers
│   ├── callback.py  # Inline keyboard callback handler
│   └── cost_handler.py  # /cost command parser (Phase I)
├── formatters.py    # Signal, trade, status message formatters
└── notifier.py      # send_notification(), send_signal_for_approval()
```

### Command Map
```
/signal   → trigger immediate analysis, send result
/status   → open positions + live PnL
/balance  → Binance USDT balance + unrealized PnL
/mode     → show current mode | /mode scalp|intraday|swing
/auto     → /auto on | /auto off
/close    → close open position at market
/stop     → emergency: circuit_breaker=true, cancel all orders
/history  → last 10 closed trades
/paper    → paper trade commands (Phase H)
/cost     → cost tracking (Phase I)
/health   → health report (Phase J)
/filter   → filter status (Phase K)
```

### Signal Message Format
```
XAUUSD Signal — INTRADAY

Action:     BUY
Entry:      $3,287.50
SL:         $3,268.00  (-0.59%)
TP1:        $3,305.00  (+0.53%)
TP2:        $3,325.00  (+1.14%)
TP3:        $3,350.00  (+1.90%)
Confidence: 74%
Bias:       BULLISH

Reasoning:
HTF BUY-ONLY bias confirmed. MTF pullback to EMA50 support.
LTF bullish engulfing at 3,285 level.

[✅ Approve]  [❌ Reject]
```

## Related Code Files

| Action | File | Description |
|--------|------|-------------|
| create | `bot/telegram/bot.py` | Application builder + handler registration |
| create | `bot/telegram/handlers/commands.py` | All command handlers |
| create | `bot/telegram/handlers/callback.py` | Inline keyboard handler |
| create | `bot/telegram/formatters.py` | Message formatters |
| create | `bot/telegram/notifier.py` | Notification helpers |

## Implementation Steps

1. **`bot/telegram/bot.py`**:
   ```python
   from telegram.ext import Application, CommandHandler, CallbackQueryHandler

   def build_application(token: str) -> Application:
       app = Application.builder().token(token).build()
       app.add_handler(CommandHandler("signal",  cmd_signal))
       app.add_handler(CommandHandler("status",  cmd_status))
       app.add_handler(CommandHandler("balance", cmd_balance))
       app.add_handler(CommandHandler("mode",    cmd_mode))
       app.add_handler(CommandHandler("auto",    cmd_auto))
       app.add_handler(CommandHandler("close",   cmd_close))
       app.add_handler(CommandHandler("stop",    cmd_stop))
       app.add_handler(CommandHandler("history", cmd_history))
       app.add_handler(CallbackQueryHandler(btn_callback))
       return app
   ```
   Note: Phase H/I/J/K handlers added here when those phases are implemented.

2. **Authorization decorator** — applied to every handler:
   ```python
   from functools import wraps

   def authorized_only(func):
       @wraps(func)
       async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
           if str(update.effective_user.id) != str(settings.TELEGRAM_CHAT_ID):
               return  # silently ignore
           return await func(update, context)
       return wrapper
   ```

3. **`handlers/commands.py`** — implement each handler decorated with `@authorized_only`:

   - `cmd_signal`: trigger `run_analysis_cycle()` on demand; reply with signal or "No setup found (HOLD)"
   - `cmd_status`: query `trades WHERE status='open'`; fetch mark price; format live PnL
   - `cmd_balance`: `client.futures_account_balance()`; show USDT balance + unrealized PnL
   - `cmd_mode`: no args → reply current mode; with arg (`/mode swing`) → call `set_mode()`; confirm
   - `cmd_auto`: `/auto on` → `config.auto_trade='true'`; `/auto off` → false; confirm reply
   - `cmd_close`: call `trade_executor.close_position()`; reply with close price + PnL
   - `cmd_stop`: set `circuit_breaker='true'` in DB; call `cancel_all_orders('XAUUSDT')`; reply "Emergency stop activated"
   - `cmd_history`: query last 10 closed `trades`; format as list

4. **`handlers/callback.py`** — `btn_callback`:
   ```python
   @authorized_only
   async def btn_callback(update: Update, context):
       query = update.callback_query
       await query.answer()
       action, signal_id = query.data.split("_", 1)

       # Idempotency check
       signal = await db_get_signal(signal_id)
       if signal.status != 'pending':
           await query.edit_message_text(query.message.text + f"\n\nAlready {signal.status}.")
           return

       if action == "approve":
           await db_update_signal_status(signal_id, 'approved')
           await trade_executor.execute_signal(signal_id)
           await query.edit_message_text(query.message.text + "\n\nApproved — Executing...")
       elif action == "reject":
           await db_update_signal_status(signal_id, 'rejected')
           await query.edit_message_text(query.message.text + "\n\nRejected.")
   ```

5. **`formatters.py`**:
   - `format_signal(signal) -> str`: compute `sl_pct`, `tp1_pct`, `tp2_pct`, `tp3_pct`; truncate reasoning to 300 chars; use backticks for numbers (safer Markdown escaping)
   - `format_trade(trade, mark_price) -> str`: side, entry, current price, live PnL
   - `format_history(trades) -> str`: table of last N trades with entry/exit/pnl
   - `format_balance(balance_data) -> str`: total equity + free margin + unrealized PnL

6. **`notifier.py`**:
   ```python
   async def send_notification(bot, chat_id: str, text: str):
       try:
           await bot.send_message(chat_id=int(chat_id), text=text, parse_mode="Markdown")
       except Exception as e:
           logger.error(f"Telegram send error: {e}")

   async def send_signal_for_approval(bot, chat_id: str, signal) -> int:
       text = format_signal(signal)
       keyboard = InlineKeyboardMarkup([[
           InlineKeyboardButton("Approve", callback_data=f"approve_{signal.id}"),
           InlineKeyboardButton("Reject",  callback_data=f"reject_{signal.id}")
       ]])
       msg = await bot.send_message(int(chat_id), text,
                                    parse_mode="Markdown", reply_markup=keyboard)
       return msg.message_id
   ```

7. **Integration**: `bot.py` exposes `build_application(token)` returning the `Application`; orchestrator calls `await app.initialize(); await app.start(); await app.updater.start_polling()` then `await shutdown_event.wait()`

## Todo

- [ ] `bot.py` Application builder + handler registration
- [ ] Authorization decorator
- [ ] `handlers/commands.py` — all 9 command handlers
- [ ] `handlers/callback.py` — approve/reject + idempotency check
- [ ] `formatters.py` signal + trade + history formatters
- [ ] `notifier.py` send_notification + send_signal_for_approval
- [ ] Verify callback_data split handles signal UUIDs correctly
- [ ] Test /stop sets circuit_breaker in DB

## Success Criteria
- `/signal` triggers analysis and sends formatted signal with inline buttons
- Approve button executes signal (or queues it); message updated to "Approved"
- Reject button updates message; `signals.status='rejected'` in DB
- Double-press approve: second press shows "Already approved"
- `/stop` sets `circuit_breaker=true`; unauthorized user gets no reply
- `/history` returns last 10 trades formatted

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| Telegram network error on notification | Medium | try/except in `send_notification`, log error, continue |
| Duplicate approve button press | Medium | Idempotency check on `signal.status` |
| Message too long (4096 limit) | Low | Truncate reasoning to 300 chars |
| Unauthorized access attempt | High | Authorization decorator silently ignores |

## Security Considerations
- `TELEGRAM_CHAT_ID` in `.env` — only that user ID can interact
- `cmd_stop` is the nuclear option — no confirmation dialog needed (it's an emergency command)
- Never echo raw user input back in command responses
- Validate `signal_id` exists in DB before processing callback

## Next Steps
- Phase G: `build_application()` called in `main()`; `app.updater.start_polling()` in asyncio loop
- Phase H: Add `/paper` command handlers
- Phase I: Add `/cost` command handlers
- Phase J: Add `/health` command handler
- Phase K: Add `/filter` command handlers
