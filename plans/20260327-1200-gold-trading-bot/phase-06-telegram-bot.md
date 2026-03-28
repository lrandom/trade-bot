# Phase 06 — Telegram Bot

## Context
- Parent plan: [plan.md](plan.md)
- Depends on: [phase-01-project-setup.md](phase-01-project-setup.md)
- Research: [researcher-02-llm-telegram.md](research/researcher-02-llm-telegram.md)
- Blocks: phase-08

## Overview
- **Date:** 2026-03-27
- **Priority:** P1
- **Status:** pending
- python-telegram-bot v20 async implementation. All 9 commands, inline keyboard approve/reject, signal formatting, notification system.

## Key Insights
- v20 is fully async — all handlers must be `async def`
- Run as `application.run_polling()` on VPS — no SSL cert needed, simpler than webhook
- Authorization: all handlers check `update.effective_user.id == TELEGRAM_CHAT_ID` — reject others silently
- Signal message ID stored in DB `signals.telegram_msg_id` — needed to edit message after approve/reject
- Inline keyboard `callback_data` format: `"{action}_{signal_id}"` — max 64 bytes (UUID is 36 chars → fits)
- Rate limit: 30 messages/sec per bot — queue signals if many fire simultaneously (rare for gold bot)

## Requirements

**Functional:**
- 9 commands: `/signal /approve /reject /status /balance /mode /auto /close /stop /history`
- Inline keyboard on signal messages: Approve / Reject buttons
- After approve: edit message to show "Approved — Executing..."
- After reject: edit message to show "Rejected"
- Notifications: send message on trade open, TP hit, SL hit, circuit breaker trigger
- `/stop` command: sets emergency stop flag in DB, cancels all open orders

**Non-functional:**
- All unauthorized requests silently ignored (no error replies to strangers)
- All handlers `async def` (v20 requirement)
- Telegram errors logged but don't crash main loop

## Architecture

```
bot/telegram/
├── __init__.py
├── bot.py           # Application builder, register handlers, start_polling()
├── handlers.py      # All 9 command handlers + callback_query handler
├── formatters.py    # Signal/trade/status message formatters
└── notifier.py      # send_notification(), send_signal_for_approval()
```

### Command → Handler Map
```
/signal   → cmd_signal    → trigger immediate analysis cycle, send result
/approve  → cmd_approve   → approve pending signal by ID (fallback if button fails)
/reject   → cmd_reject    → reject pending signal by ID
/status   → cmd_status    → show open positions with PnL
/balance  → cmd_balance   → fetch Binance balance, show equity + margin
/mode     → cmd_mode      → show current mode or change: /mode scalp
/auto     → cmd_auto      → toggle auto_trade: /auto on | /auto off
/close    → cmd_close     → close current open position at market
/stop     → cmd_stop      → emergency: set circuit_breaker=true, cancel all orders
/history  → cmd_history   → show last 10 closed trades with PnL
```

### Signal Message Format
```
XAUUSD Signal — {MODE}

Action:    {BUY/SELL}
Entry:     ${entry}
SL:        ${sl}  (-{sl_pct}%)
TP1:       ${tp1} (+{tp1_pct}%)
TP2:       ${tp2} (+{tp2_pct}%)
TP3:       ${tp3} (+{tp3_pct}%)
Confidence: {confidence}%
Bias:       {trend_bias}

Reasoning:
{reasoning}

[Approve] [Reject]
```

## Related Code Files

| Action | File | Description |
|--------|------|-------------|
| create | `bot/telegram/bot.py` | Application build + handler registration |
| create | `bot/telegram/handlers.py` | All command handlers |
| create | `bot/telegram/formatters.py` | Message string builders |
| create | `bot/telegram/notifier.py` | Notification helpers |

## Implementation Steps

1. **bot.py**:
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

2. **Authorization decorator** (apply to all handlers):
   ```python
   def authorized_only(func):
       async def wrapper(update, context):
           if str(update.effective_user.id) != settings.TELEGRAM_CHAT_ID:
               return  # silently ignore
           return await func(update, context)
       return wrapper
   ```

3. **handlers.py** — implement each command:

   - `cmd_signal`: calls `run_analysis_cycle()` manually, returns signal message or "No setup found"
   - `cmd_status`: queries DB `trades WHERE status='open'`; formats with current mark price for live PnL
   - `cmd_balance`: calls `client.futures_account_balance()`; shows USDT balance + unrealized PnL
   - `cmd_mode`: no args → show current mode; with arg (`/mode swing`) → call `set_mode()`; confirm reply
   - `cmd_auto`: `/auto on` → sets `config.auto_trade=true`; `/auto off` → false; confirm reply
   - `cmd_close`: calls execution engine `close_position()`; replies with close price + PnL
   - `cmd_stop`: sets `circuit_breaker=true` in DB; calls `cancel_all_orders()`; replies "Emergency stop activated"
   - `cmd_history`: queries last 10 closed trades; formats table with entry/exit/pnl

4. **btn_callback** (inline keyboard):
   ```python
   async def btn_callback(update: Update, context):
       query = update.callback_query
       await query.answer()
       action, signal_id = query.data.split("_", 1)
       if action == "approve":
           await update_signal_status(db, signal_id, 'approved')
           await execution_engine.execute_signal(signal_id)
           await query.edit_message_text(
               query.message.text + "\n\nApproved — Executing...",
               parse_mode="Markdown"
           )
       elif action == "reject":
           await update_signal_status(db, signal_id, 'rejected')
           await query.edit_message_text(
               query.message.text + "\n\nRejected.",
               parse_mode="Markdown"
           )
   ```

5. **formatters.py** — `format_signal(signal) -> str` and `format_trade(trade) -> str`:
   - Compute `sl_pct = abs(entry - sl) / entry * 100`
   - Compute `tp_pct` for each TP
   - Return Markdown-formatted string (use backticks for numbers, not bold — safer escaping)

6. **notifier.py** — `send_notification(bot, chat_id, text)` and `send_signal_for_approval(bot, chat_id, signal) -> int (msg_id)`:
   ```python
   async def send_signal_for_approval(bot, chat_id, signal) -> int:
       text = format_signal(signal)
       keyboard = InlineKeyboardMarkup([[
           InlineKeyboardButton("Approve", callback_data=f"approve_{signal.id}"),
           InlineKeyboardButton("Reject",  callback_data=f"reject_{signal.id}")
       ]])
       msg = await bot.send_message(chat_id, text, parse_mode="Markdown",
                                    reply_markup=keyboard)
       return msg.message_id
   ```

7. **Integration with orchestrator**: `bot.py` exposes `get_bot()` returning the built `Application`; orchestrator calls `app.run_polling()` inside the asyncio loop

## Todo

- [ ] `bot.py` Application builder + handler registration
- [ ] Authorization decorator
- [ ] All 9 command handlers in `handlers.py`
- [ ] `btn_callback` approve/reject inline handler
- [ ] `formatters.py` signal + trade formatters
- [ ] `notifier.py` send_notification + send_signal_for_approval
- [ ] Test all commands with bot father in dev Telegram group

## Success Criteria
- `/signal` command triggers analysis and sends formatted signal message
- Approve button places order (or queues it); message updates to "Approved"
- Reject button updates message to "Rejected" and marks DB signal as rejected
- `/stop` sets circuit_breaker=true and unauthorized user gets no reply
- `/history` returns last 10 trades as formatted list

## Risk Assessment
| Risk | Impact | Mitigation |
|------|--------|------------|
| Telegram network error on notification | Medium | Wrap send in try/except, log error, continue |
| Duplicate approve button presses | Medium | Check signal status before executing; idempotent |
| Unauthorized access attempt | High | Authorization decorator on all handlers |
| Message too long (Telegram 4096 limit) | Low | Truncate reasoning to 300 chars in formatter |

## Security Considerations
- `TELEGRAM_CHAT_ID` stored in `.env` — only your user ID can interact
- `cmd_stop` is the nuclear option — confirm with a reply message before executing cancel-all
- Never echo raw user input back into system commands
- Callback data is opaque string — validate signal_id exists in DB before processing

## Next Steps
- Phase 07: `btn_callback` calls `execution_engine.execute_signal()`
- Phase 08: `app.run_polling()` runs in main asyncio loop alongside scheduler
