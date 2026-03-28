# Phase J — Health Monitor

## Context
- Parent plan: [plan.md](plan.md)
- Design spec: `plans/20260327-1200-gold-trading-bot/phase-11-health-monitor.md`
- Depends on: [phase-F-telegram.md](phase-F-telegram.md), [phase-G-orchestration.md](phase-G-orchestration.md)
- Required before live trading

## Overview
- **Date:** 2026-03-28
- **Priority:** P0 (required before live)
- **Status:** pending
- Heartbeat ping (5min), component watchdog (Binance/LLM/DB/Scheduler), alert on miss, `/health` on-demand report, systemd service file for auto-restart.

## Key Insights
- Silent mode by default (no Telegram spam) — alert only on problems
- `HEALTH_VERBOSE=false` default: only send Telegram if something is wrong
- LLM health check is optional (`LLM_HEALTH_CHECK=false` default) — costs tokens
- `StartLimitBurst=3` in systemd — stops restart loop after 3 rapid failures
- Heartbeat miss = 15min without DB update → Telegram CRITICAL alert
- Bot restart with open position: check on startup, alert user before resuming
- Component check timeout: use `asyncio.wait_for(..., timeout=5.0)` per check

## Requirements

**Functional:**
- `HealthMonitor.heartbeat()` — runs every 5min via APScheduler
- Component checks: Binance API ping, LLM (optional), DB `SELECT 1`, scheduler job count
- Alert on component down: pause `auto_trade`, send Telegram WARNING
- Alert on 15min heartbeat miss: send Telegram CRITICAL
- `/health` command: immediate full report
- Startup event + shutdown event logged to `bot_events` table
- systemd service file for production

**Non-functional:**
- Each component check has 5s timeout
- Health check never crashes main loop
- DB update is the "proof of life" for heartbeat monitoring

## Architecture

```
bot/health/
├── __init__.py
├── models.py      # HealthStatus, ComponentStatus dataclasses
└── monitor.py     # HealthMonitor class

/etc/systemd/system/gold-bot.service  (deployment artifact)
```

### HealthStatus Model
```python
@dataclass
class ComponentStatus:
    name: str
    ok: bool
    latency_ms: float = 0.0
    error: str = ""

@dataclass
class HealthStatus:
    timestamp: datetime
    all_ok: bool
    components: list[ComponentStatus]
    uptime_seconds: int
    details: str  # JSON string of component statuses
```

## Related Code Files

| Action | File | Description |
|--------|------|-------------|
| create | `bot/health/models.py` | `HealthStatus`, `ComponentStatus` |
| create | `bot/health/monitor.py` | `HealthMonitor` class |
| create | `gold-bot.service` | systemd unit file (save in project root) |
| modify | `bot/orchestrator.py` | Register heartbeat job in APScheduler |
| modify | `bot/telegram/bot.py` | Register `/health` CommandHandler |
| modify | `schema.sql` | Add `health_log` + `bot_events` tables |

## Implementation Steps

1. **Schema additions** to `schema.sql`:
   ```sql
   CREATE TABLE IF NOT EXISTS health_log (
       timestamp DATETIME PRIMARY KEY,
       all_ok BOOLEAN,
       details TEXT,       -- JSON: {"binance": true, "llm": true, ...}
       uptime_seconds INTEGER
   );
   CREATE TABLE IF NOT EXISTS bot_events (
       id TEXT PRIMARY KEY,
       timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
       event_type TEXT,    -- startup | shutdown | crash | restart | api_error
       component TEXT,     -- binance | llm | db | scheduler
       message TEXT,
       severity TEXT       -- info | warning | critical
   );
   ```

2. **`health/models.py`** — dataclasses as above

3. **`health/monitor.py`** — `HealthMonitor` class:
   ```python
   class HealthMonitor:
       def __init__(self, db, binance_client, llm_provider, scheduler, settings):
           self._start_time = utc_now()
           self._db = db
           self._binance = binance_client
           self._llm = llm_provider
           self._scheduler = scheduler
           self._settings = settings

       async def heartbeat(self):
           """Called every 5min by APScheduler."""
           status = await self._check_all()
           uptime = int((utc_now() - self._start_time).total_seconds())

           await self._db.execute(
               "INSERT OR REPLACE INTO health_log VALUES (?,?,?,?)",
               (utc_now().isoformat(), status.all_ok,
                json.dumps({c.name: c.ok for c in status.components}), uptime)
           )
           await self._db.commit()

           if not status.all_ok:
               # Pause auto_trade
               await self._db.execute(
                   "UPDATE config SET value='false' WHERE key='auto_trade'"
               )
               await self._db.commit()
               msg = self._format_alert(status)
               await send_notification(self._bot, self._settings.TELEGRAM_CHAT_ID, msg)
           elif self._settings.HEALTH_VERBOSE:
               await send_notification(self._bot, self._settings.TELEGRAM_CHAT_ID,
                                       self._format_verbose(status))

       async def _check_all(self) -> HealthStatus:
           checks = await asyncio.gather(
               self._check_binance(),
               self._check_db(),
               self._check_scheduler(),
               self._check_llm() if self._settings.LLM_HEALTH_CHECK else asyncio.coroutine(lambda: ComponentStatus("llm", True))(),
               return_exceptions=True,
           )
           components = [c if isinstance(c, ComponentStatus)
                         else ComponentStatus("unknown", False, error=str(c))
                         for c in checks]
           return HealthStatus(
               timestamp=utc_now(),
               all_ok=all(c.ok for c in components),
               components=components,
               uptime_seconds=int((utc_now() - self._start_time).total_seconds()),
               details=json.dumps({c.name: c.ok for c in components})
           )

       async def _check_binance(self) -> ComponentStatus:
           t0 = utc_now()
           try:
               await asyncio.wait_for(
                   self._binance.get_symbol_ticker(symbol="XAUUSDT"), timeout=5.0
               )
               lat = (utc_now() - t0).total_seconds() * 1000
               return ComponentStatus("binance", True, latency_ms=lat)
           except Exception as e:
               return ComponentStatus("binance", False, error=str(e)[:100])

       async def _check_db(self) -> ComponentStatus:
           try:
               await asyncio.wait_for(self._db.execute("SELECT 1"), timeout=5.0)
               return ComponentStatus("db", True)
           except Exception as e:
               return ComponentStatus("db", False, error=str(e)[:100])

       async def _check_scheduler(self) -> ComponentStatus:
           jobs = self._scheduler.get_jobs()
           ok = len(jobs) > 0 and all(j.next_run_time is not None for j in jobs)
           return ComponentStatus("scheduler", ok,
                                  error="" if ok else f"Jobs: {len(jobs)}")

       async def _check_llm(self) -> ComponentStatus:
           try:
               resp = await asyncio.wait_for(
                   self._llm.provider.complete("system", "reply: ok"), timeout=10.0
               )
               return ComponentStatus("llm", True)
           except Exception as e:
               return ComponentStatus("llm", False, error=str(e)[:100])

       async def get_health_report(self) -> str:
           """Used by /health command."""
           status = await self._check_all()
           # Format full report (see design spec format)
           ...
   ```

4. **Register heartbeat** in `orchestrator.py`:
   ```python
   health_monitor = HealthMonitor(db, client, llm, scheduler, settings)
   scheduler.add_job(
       health_monitor.heartbeat, 'interval',
       minutes=settings.HEALTH_INTERVAL_MIN, id='heartbeat'
   )
   ```

5. **Startup + shutdown events** in `orchestrator.py`:
   ```python
   # After init:
   await db.execute(
       "INSERT INTO bot_events VALUES (?,?,?,?,?,?)",
       (uuid4().hex, utc_now().isoformat(), 'startup', 'system',
        f'Mode:{mode} Paper:{settings.PAPER_TRADE}', 'info')
   )
   # In shutdown:
   await db.execute(
       "INSERT INTO bot_events VALUES (?,?,?,?,?,?)",
       (uuid4().hex, utc_now().isoformat(), 'shutdown', 'system', 'Graceful', 'info')
   )
   ```

6. **`/health` command** in `handlers/commands.py`:
   ```python
   @authorized_only
   async def cmd_health(update, context):
       report = await health_monitor.get_health_report()
       await update.message.reply_text(report, parse_mode="Markdown")
   ```
   Register: `app.add_handler(CommandHandler("health", cmd_health))`

7. **`gold-bot.service`** systemd unit:
   ```ini
   [Unit]
   Description=Gold Trading Bot
   After=network.target
   StartLimitIntervalSec=60
   StartLimitBurst=3

   [Service]
   Type=simple
   User=ubuntu
   WorkingDirectory=/home/ubuntu/trade-gold
   ExecStart=/home/ubuntu/trade-gold/.venv/bin/python main.py
   Restart=always
   RestartSec=10s
   StandardOutput=journal
   StandardError=journal
   Environment=PYTHONUNBUFFERED=1

   [Install]
   WantedBy=multi-user.target
   ```
   Deploy: `sudo systemctl enable gold-bot && sudo systemctl start gold-bot`

## Todo

- [ ] Schema: `health_log` + `bot_events` tables
- [ ] `health/models.py` dataclasses
- [ ] `health/monitor.py` HealthMonitor — 4 component checks
- [ ] Heartbeat scheduler job (5min)
- [ ] Alert logic: component down → pause auto_trade + Telegram WARNING
- [ ] Startup + shutdown events logged to `bot_events`
- [ ] `/health` command + handler
- [ ] Register `/health` in `bot.py`
- [ ] `gold-bot.service` file
- [ ] Test: kill main process → verify Telegram alert within 15min

## Success Criteria
- Heartbeat updates `health_log` every 5 min
- Binance API error → `auto_trade` paused, Telegram WARNING sent
- `/health` always responds within 3s
- `gold-bot.service` auto-restarts bot within 10s on crash
- Startup message: "Bot started — 14:30 VN | Mode: Swing | Paper: ON"
- Shutdown message: "Bot stopped gracefully"

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| LLM health check wastes tokens | Low | `LLM_HEALTH_CHECK=false` default |
| Heartbeat spam Telegram | Low | `HEALTH_VERBOSE=false` default |
| systemd restart loop on config error | Medium | `StartLimitBurst=3`; alert user after 3 restarts |
| Bot restart with open position | High | Startup check: query open trades → alert user |

## Security Considerations
- systemd runs as `ubuntu` user (not root)
- `journalctl` for log access — no file permission issues
- API keys in `.env` not accessible to systemd directly — use `EnvironmentFile=/home/ubuntu/trade-gold/.env` if needed

## Next Steps
- Post-live: add external uptime monitor (UptimeRobot free tier) as second layer
