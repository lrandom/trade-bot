# Phase G — Orchestration

## Context
- Parent plan: [plan.md](plan.md)
- Design spec: `plans/20260327-1200-gold-trading-bot/phase-08-orchestration.md`
- Depends on: all phases A–F
- Blocks: Phase H, I, J, K (enhancement group)

## Overview
- **Date:** 2026-03-28
- **Priority:** P0
- **Status:** pending
- Main entry point, asyncio event loop, APScheduler `AsyncIOScheduler`, WebSocket management, graceful SIGINT/SIGTERM shutdown, error recovery policy.

## Key Insights
- `AsyncIOScheduler` runs inside existing asyncio loop — no thread
- `app.run_polling()` blocks; use `updater.start_polling()` (non-blocking) + `shutdown_event.wait()`
- One global `asyncio.Event` — `shutdown_event` — signals all loops to exit
- `max_instances=1` on analysis job — prevents overlapping analysis cycles
- Mode switch while job is running: use `asyncio.Lock` before job re-registration
- Analysis exceptions: log + continue (never crash main loop)
- WebSocket reconnect handled in `websocket_feed.py`, not in scheduler
- `PAPER_TRADE=true` logged prominently in startup banner — visibility is safety

## Requirements

**Functional:**
- `main()` coroutine: init DB → init clients → register scheduler jobs → start tasks → start Telegram → await shutdown
- `run_analysis_cycle(mode)` — shared function called by scheduler or WebSocket callback
- APScheduler jobs: analysis (mode-dependent interval) + daily PnL reset (cron: 00:01 UTC)
- SIGINT/SIGTERM handlers → `shutdown_event.set()`
- Graceful shutdown: cancel tasks, stop Telegram, close Binance connection, close DB

**Non-functional:**
- All coroutines share the same event loop
- Startup sequence documented and deterministic
- Log startup banner with mode, testnet/live status, paper/live, model name

## Architecture

### asyncio Event Loop Layout
```
asyncio.run(main())
  │
  ├── init_db()
  ├── get_client()            # Binance AsyncClient
  ├── LLMEngine(mode)
  ├── RiskEngine()
  ├── get_trader(settings, db, client)
  │
  ├── APScheduler (AsyncIOScheduler)
  │     ├── analysis_job (mode-driven interval)  max_instances=1
  │     └── daily_reset  (cron 00:01 UTC)
  │
  ├── asyncio.create_task(position_monitor_loop())
  ├── asyncio.create_task(websocket_task())       # scalp mode only
  │
  └── Telegram Application
        ├── app.initialize()
        ├── app.start()
        └── app.updater.start_polling()
            └── await shutdown_event.wait()
```

### Startup Sequence
```
1.  Load Settings
2.  Setup logger
3.  init_db(settings.DB_PATH)
4.  Connect Binance AsyncClient
5.  Read current mode from DB
6.  Build LLMEngine, RiskEngine, TradeExecutor, Trader
7.  Build Telegram Application
8.  Start APScheduler
9.  Register analysis_job (mode-dependent trigger)
10. Register daily_reset cron
11. Start position_monitor_loop task
12. If mode == scalp: start websocket_task
13. Initialize + start Telegram updater polling
14. Log startup banner
15. Await shutdown_event
16. Graceful shutdown
```

## Related Code Files

| Action | File | Description |
|--------|------|-------------|
| create | `bot/orchestrator.py` | Full `main()` coroutine + `run_analysis_cycle()` |
| modify | `main.py` | Finalize entry point |

## Implementation Steps

1. **`main.py`**:
   ```python
   import asyncio
   from bot.orchestrator import main

   if __name__ == "__main__":
       asyncio.run(main())
   ```

2. **`bot/orchestrator.py`** — `main()`:
   ```python
   import asyncio, signal as signal_module
   from apscheduler.schedulers.asyncio import AsyncIOScheduler

   shutdown_event = asyncio.Event()
   _mode_lock = asyncio.Lock()

   def _handle_signal(sig):
       logger.info(f"Received {sig.name}, initiating shutdown...")
       shutdown_event.set()

   async def main():
       loop = asyncio.get_event_loop()
       for sig in (signal_module.SIGINT, signal_module.SIGTERM):
           loop.add_signal_handler(sig, _handle_signal, sig)

       setup_logger()
       settings = Settings()

       async with aiosqlite.connect(settings.DB_PATH) as db:
           db.row_factory = aiosqlite.Row
           await init_db(settings.DB_PATH)
           mode = await get_current_mode(db)

           client  = await get_client()
           trader  = get_trader(settings, db, client)
           llm     = LLMEngine(mode)
           risk    = RiskEngine()
           executor = TradeExecutor(db, trader, risk)
           tg_app  = build_application(settings.TELEGRAM_BOT_TOKEN)

           # Inject dependencies into handlers via context
           tg_app.bot_data.update({
               'db': db, 'executor': executor, 'llm': llm,
               'risk': risk, 'client': client, 'settings': settings
           })

           scheduler = AsyncIOScheduler()
           mode_cfg  = get_mode_config(mode)

           async def analysis_job():
               try:
                   await run_analysis_cycle(mode, db, llm, risk, executor, tg_app.bot, settings)
               except Exception as e:
                   logger.exception(f"Analysis cycle error: {e}")

           if mode_cfg['analysis_trigger'] == 'interval':
               scheduler.add_job(analysis_job, 'interval',
                                  minutes=mode_cfg['interval_minutes'],
                                  id='analysis', max_instances=1)

           scheduler.add_job(
               lambda: asyncio.create_task(reset_daily_pnl(db)),
               'cron', hour=0, minute=1, id='daily_reset'
           )
           scheduler.start()

           tasks = [asyncio.create_task(
               position_monitor_loop(db, trader, tg_app.bot, settings)
           )]

           if mode == 'scalp':
               tasks.append(asyncio.create_task(
                   start_kline_stream('XAUUSDT',
                       lambda df: asyncio.create_task(
                           analysis_job() if _is_5m_close(df) else asyncio.sleep(0)
                       )
                   )
               ))

           await tg_app.initialize()
           await tg_app.start()
           await tg_app.updater.start_polling()

           _log_startup_banner(settings, mode)

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
           await client.close_connection()
           logger.info("Shutdown complete.")
   ```

3. **`run_analysis_cycle(mode, db, llm, risk, executor, bot, settings)`**:
   ```python
   async def run_analysis_cycle(mode, db, llm, risk, executor, bot, settings):
       snapshot = await build_snapshot(mode)
       signal   = await llm.generate_signal(snapshot)

       if signal.action == 'HOLD':
           logger.debug(f"HOLD — {signal.reasoning[:80]}")
           return

       approved, reason = await risk.pre_trade_check(
           signal, mode, await _get_live_balance(executor), db
       )
       if not approved:
           logger.info(f"Signal rejected: {reason}")
           return

       signal_id = await save_signal_to_db(db, signal, mode)
       auto = await _get_auto_trade(db)

       if auto:
           await executor.execute_signal(signal_id)
           await send_notification(bot, settings.TELEGRAM_CHAT_ID,
               f"Auto-executed {signal.action} @ {signal.entry_price:.2f}")
       else:
           msg_id = await send_signal_for_approval(bot, settings.TELEGRAM_CHAT_ID, signal)
           await db.execute("UPDATE signals SET telegram_msg_id=? WHERE id=?",
                            (msg_id, signal_id))
           await db.commit()
   ```

4. **`_is_5m_close(df)`**: returns True if latest candle is a 5m boundary — `df.index[-1].minute % 5 == 0`

5. **`_log_startup_banner(settings, mode)`**:
   ```
   ===== GOLD BOT STARTED =====
   Mode: intraday | Auto: false
   Paper: ON | Testnet: true
   Model: claude-sonnet-4-6
   Analysis: every 15min
   ============================
   ```

6. **`cmd_mode` integration** — when mode changes via Telegram:
   ```python
   async with _mode_lock:
       await set_mode(db, new_mode)
       scheduler.remove_job('analysis')
       new_cfg = get_mode_config(new_mode)
       scheduler.add_job(analysis_job, 'interval',
                         minutes=new_cfg['interval_minutes'],
                         id='analysis', max_instances=1)
   ```

7. **Error recovery policy**:
   - Analysis cycle exceptions: log + continue
   - WebSocket disconnect: handled in `websocket_feed.py` with backoff
   - Telegram polling error: python-telegram-bot v20 handles reconnect internally
   - DB write failure: log CRITICAL + Telegram alert; do NOT place trade if DB write fails

8. **Dry-run mode** (`DRY_RUN=true`):
   ```python
   if settings.DRY_RUN:
       logger.info(f"[DRY RUN] Signal: {signal}")
       return  # skip DB write, skip Telegram, skip execution
   ```

## Todo

- [ ] `main.py` finalize entry point
- [ ] `orchestrator.py` full `main()` coroutine
- [ ] SIGINT/SIGTERM signal handlers
- [ ] APScheduler setup with mode-aware job registration
- [ ] `run_analysis_cycle()` function
- [ ] `_is_5m_close()` helper
- [ ] Graceful shutdown sequence (cancel tasks → await gather → stop Telegram → close Binance)
- [ ] Startup banner log
- [ ] Mode switch with `asyncio.Lock` + scheduler job re-registration
- [ ] Dry-run mode early return
- [ ] End-to-end test on testnet: snapshot → signal → approve → order

## Success Criteria
- `python main.py` starts cleanly, logs startup banner
- Analysis job fires on schedule (verify with log timestamps)
- `Ctrl+C` triggers graceful shutdown within 5 seconds
- Testnet end-to-end: signal generated → Telegram message → approve → order placed
- Position monitor updates DB after simulated TP hit (paper mode)
- Mode switch via `/mode` updates scheduler without restart

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| Main loop crash on unhandled exception | Critical | try/except in `analysis_job`; top-level try/finally in `main()` |
| Task cancellation leaves open orders | High | `close_position()` called in shutdown if open trade exists |
| APScheduler fires analysis while previous still running | Medium | `max_instances=1` on analysis_job |
| Mode switch race condition | Medium | `asyncio.Lock` on mode change + job re-registration |

## Security Considerations
- SIGTERM handler clean — no open orders left on server restart (close position in shutdown)
- `BINANCE_TESTNET=true` default — explicit change required for live
- No web server exposed — bot communicates only via Telegram polling (outbound only)
- Startup banner visible in logs + Telegram — mode/paper status explicit

## Next Steps
- Phase H: daily paper stats aggregator scheduled here (23:59 UTC cron)
- Phase I: cost daily aggregator scheduled here
- Phase J: health heartbeat job added to scheduler
- Phase K: FilterChain called inside `run_analysis_cycle()` before LLM chain
