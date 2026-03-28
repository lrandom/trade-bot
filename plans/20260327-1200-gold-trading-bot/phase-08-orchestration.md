# Phase 08 — Orchestration

## Context
- Parent plan: [plan.md](plan.md)
- Depends on: all phases (01–07)
- Research: [researcher-01-binance-indicators.md](research/researcher-01-binance-indicators.md), [researcher-02-llm-telegram.md](research/researcher-02-llm-telegram.md)

## Overview
- **Date:** 2026-03-27
- **Priority:** P0
- **Status:** pending
- Main entry point, asyncio event loop architecture, APScheduler AsyncIOScheduler, WebSocket management, error recovery, graceful shutdown.

## Key Insights
- `AsyncIOScheduler` runs inside the existing asyncio event loop — no separate thread needed
- Telegram `run_polling()` blocks; use `updater.start_polling()` + `asyncio.gather()` instead for co-routine sharing
- WebSocket reconnect must happen in a background task, not in the scheduler
- Graceful shutdown: cancel all pending tasks, close DB connection, stop Telegram polling
- One global `asyncio.Event` — `shutdown_event` — signals all loops to exit cleanly
- Wrap entire main in try/finally to ensure cleanup on crash

## Requirements

**Functional:**
- `main()` coroutine: init DB, init clients, register scheduler jobs, start WebSocket (if scalp), start position monitor, start Telegram polling
- `run_analysis_cycle(mode)` — shared function called by scheduler or WebSocket callback
- APScheduler jobs: analysis cycle (mode-dependent interval) + daily PnL reset (00:01 UTC)
- Graceful shutdown on `SIGINT`/`SIGTERM`
- Error recovery: catch exceptions in analysis cycle, log, continue (don't crash main loop)

**Non-functional:**
- All coroutines share the same asyncio event loop
- Startup sequence documented and deterministic
- Log startup banner with mode, version, testnet/live status

## Architecture

### asyncio Event Loop Layout
```
asyncio.run(main())
  │
  ├── init_db()
  ├── init_binance_client()
  ├── init_llm_engine()
  ├── init_risk_engine()
  │
  ├── APScheduler (AsyncIOScheduler)
  │     ├── analysis_job  (mode-driven interval)
  │     └── daily_reset   (cron: 00:01 UTC)
  │
  ├── asyncio.create_task(position_monitor_loop())
  │
  ├── asyncio.create_task(websocket_task())   # scalp mode only
  │
  └── Telegram Application
        └── app.run_polling()  # runs until shutdown_event set
```

### Startup Sequence
```
1. Load Settings (.env)
2. Setup logger
3. init_db() — create tables if not exist
4. Connect Binance AsyncClient
5. Read current mode from DB
6. Build LLMEngine, RiskEngine, TradeExecutor
7. Build Telegram Application
8. Start APScheduler
9. Register analysis_job for current mode
10. Register daily_reset cron job
11. Start position_monitor_loop task
12. If mode == scalp: start websocket_task
13. Start Telegram polling
14. Await shutdown_event
15. Graceful shutdown
```

## Related Code Files

| Action | File | Description |
|--------|------|-------------|
| create | `bot/orchestrator.py` | Main orchestration logic |
| create | `main.py` | Entry point: `asyncio.run(main())` |

## Implementation Steps

1. **main.py** — single line entry point:
   ```python
   import asyncio
   from bot.orchestrator import main

   if __name__ == "__main__":
       asyncio.run(main())
   ```

2. **orchestrator.py** — `main()` coroutine:
   ```python
   import asyncio, signal
   from apscheduler.schedulers.asyncio import AsyncIOScheduler

   shutdown_event = asyncio.Event()

   def handle_signal(sig):
       logger.info(f"Received {sig}, initiating shutdown...")
       shutdown_event.set()

   async def main():
       # Signal handlers
       loop = asyncio.get_event_loop()
       for sig in (signal.SIGINT, signal.SIGTERM):
           loop.add_signal_handler(sig, handle_signal, sig)

       # Init
       setup_logger()
       settings = Settings()
       logger.info(f"Starting gold bot | mode=? | testnet={settings.BINANCE_TESTNET}")

       async with aiosqlite.connect(settings.DB_PATH) as db:
           await init_db(db)
           mode = await get_current_mode(db)
           logger.info(f"Active mode: {mode}")

           # Build components
           binance = await get_client()
           llm = LLMEngine()
           risk = RiskEngine()
           executor = TradeExecutor(binance, risk)
           tg_app = build_application(settings.TELEGRAM_BOT_TOKEN)

           # APScheduler
           scheduler = AsyncIOScheduler()
           mode_cfg = get_mode_config(mode)

           async def analysis_job():
               try:
                   await run_analysis_cycle(mode, db, llm, risk, executor, tg_app.bot)
               except Exception as e:
                   logger.exception(f"Analysis cycle error: {e}")

           if mode_cfg['analysis_trigger'] == 'interval':
               scheduler.add_job(
                   analysis_job, 'interval',
                   minutes=mode_cfg['interval_minutes'],
                   id='analysis'
               )
           # Always add daily reset
           scheduler.add_job(
               lambda: asyncio.create_task(reset_daily_pnl(db)),
               'cron', hour=0, minute=1, id='daily_reset'
           )
           scheduler.start()

           # Background tasks
           tasks = [
               asyncio.create_task(position_monitor_loop(db, binance, executor, tg_app.bot)),
           ]
           if mode == 'scalp':
               tasks.append(asyncio.create_task(
                   start_kline_stream('XAUUSDT',
                       lambda df: asyncio.create_task(
                           analysis_job() if is_5m_close(df) else asyncio.sleep(0)
                       ))
               ))

           # Start Telegram (non-blocking)
           await tg_app.initialize()
           await tg_app.start()
           await tg_app.updater.start_polling()

           logger.info("Bot running. Press Ctrl+C to stop.")
           await shutdown_event.wait()

           # Graceful shutdown
           logger.info("Shutting down...")
           scheduler.shutdown(wait=False)
           for task in tasks:
               task.cancel()
           await asyncio.gather(*tasks, return_exceptions=True)
           await tg_app.updater.stop()
           await tg_app.stop()
           await tg_app.shutdown()
           await binance.close_connection()
           logger.info("Shutdown complete.")
   ```

3. **run_analysis_cycle** function:
   ```python
   async def run_analysis_cycle(mode, db, llm, risk, executor, bot):
       snapshot = await build_snapshot(mode)
       signal = await llm.generate_signal(snapshot)
       if signal.action == 'HOLD':
           logger.debug(f"Signal: HOLD (confidence={signal.confidence})")
           return
       approved, reason = await risk.pre_trade_check(signal, mode,
                                                       await get_balance(), db)
       if not approved:
           logger.info(f"Signal rejected by risk: {reason}")
           return
       signal_id = await save_signal_to_db(db, signal)
       auto = await get_auto_trade(db)
       if auto:
           await executor.execute_signal(signal_id)
           await send_notification(bot, settings.TELEGRAM_CHAT_ID,
                                   f"Auto-executed {signal.action} @ {signal.entry_price}")
       else:
           msg_id = await send_signal_for_approval(bot, settings.TELEGRAM_CHAT_ID, signal)
           await db.execute("UPDATE signals SET telegram_msg_id=? WHERE id=?",
                            (msg_id, signal_id))
   ```

4. **is_5m_close(df)** — returns `True` if latest candle open_time is a 5-minute boundary (minute % 5 == 0)

5. **Error recovery policy**:
   - Analysis cycle exceptions: log + continue (never crash main loop)
   - WebSocket disconnect: reconnect logic in `websocket_feed.py` handles it
   - Telegram polling error: python-telegram-bot v20 handles reconnect internally
   - DB write failure: log + alert via Telegram; trade not placed if DB write fails

6. **Startup banner** log line:
   ```
   ===== GOLD BOT STARTED =====
   Mode: intraday | Auto: false | Testnet: true
   Analysis interval: 15min | Model: claude-sonnet-4-6
   ============================
   ```

7. **Production deployment notes** (document in README):
   - Run with: `python main.py`
   - Use `systemd` service or `screen`/`tmux` session for persistence
   - Rotate logs: loguru handles 10MB rotation automatically
   - Set `BINANCE_TESTNET=false` only after full testnet validation

## Todo

- [ ] `main.py` entry point
- [ ] `orchestrator.py` full main() coroutine
- [ ] SIGINT/SIGTERM signal handlers
- [ ] APScheduler setup with mode-aware job registration
- [ ] `run_analysis_cycle()` function
- [ ] Graceful shutdown sequence (tasks cancel + await)
- [ ] Telegram non-blocking start (updater.start_polling)
- [ ] Startup banner log
- [ ] `is_5m_close()` helper for scalp WebSocket trigger
- [ ] End-to-end test on testnet (full cycle: snapshot → signal → approve → execute)

## Success Criteria
- `python main.py` starts cleanly, logs startup banner
- Analysis job fires on schedule (verify with log timestamps)
- `Ctrl+C` triggers graceful shutdown within 5 seconds
- Full cycle works on testnet: signal generated → Telegram message → approve button → order placed on Binance
- Position monitor updates DB after TP hit

## Risk Assessment
| Risk | Impact | Mitigation |
|------|--------|------------|
| Main loop crash on unhandled exception | Critical | try/except in analysis_job; top-level try/finally in main() |
| Task cancellation on shutdown leaves open orders | High | close_position() called in shutdown if open trade exists |
| APScheduler fires analysis while previous still running | Medium | `max_instances=1` on analysis_job |
| Mode switch doesn't update scheduler interval | Medium | On mode change: remove old job, re-add with new interval |

## Security Considerations
- SIGTERM handler is clean — no open orders left on server restart
- Production server: `BINANCE_TESTNET=false` must be explicitly set; default is `true`
- No web server exposed — bot communicates only via Telegram polling (outbound only)
- Logs should not contain API keys — verified in Settings class (phase-01)

## Unresolved Questions
1. Mode switch while job is mid-execution: need lock (`asyncio.Lock`) to prevent race condition during job re-registration
2. Paper trading mode: should a mock executor be implemented first before live? Research recommends yes — add flag `PAPER_TRADE=true` that skips actual Binance orders but logs as if executed
3. Telegram updater non-blocking integration with custom asyncio loop: verify `updater.start_polling()` vs `app.run_polling()` behavior in python-telegram-bot v21
