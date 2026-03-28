# Phase 11 — Health Monitor & Auto-restart

## Context
- Parent plan: [plan.md](plan.md)
- Depends on: phase-06 (Telegram), phase-08 (Orchestration)
- Phải hoàn thành trước khi live — bot crash ban đêm không ai biết là nguy hiểm

## Overview
- **Date:** 2026-03-28
- **Priority:** P0 — bắt buộc trước live
- **Status:** pending
- Đảm bảo bot luôn chạy 24/7: heartbeat ping, auto-restart, alert khi có vấn đề.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                  Health Monitor                      │
│                                                      │
│  ┌──────────────────┐    ┌───────────────────────┐  │
│  │ Heartbeat (5min) │    │  Component Watchdog   │  │
│  │                  │    │                       │  │
│  │ • Ping Telegram  │    │ • Binance API ok?     │  │
│  │ • Log alive      │    │ • LLM API ok?         │  │
│  │ • Update DB      │    │ • DB accessible?      │  │
│  └────────┬─────────┘    │ • Scheduler running?  │  │
│           │              └──────────┬────────────┘  │
│           └──────────────┬──────────┘               │
│                          ▼                          │
│              ┌───────────────────────┐              │
│              │   Alert & Recovery    │              │
│              │                       │              │
│              │ heartbeat miss 15min  │              │
│              │  → Telegram CRITICAL  │              │
│              │  → systemd restart    │              │
│              │                       │              │
│              │ component down        │              │
│              │  → Telegram WARNING   │              │
│              │  → pause auto trade   │              │
│              └───────────────────────┘              │
└─────────────────────────────────────────────────────┘
```

---

## Components

### 1. Heartbeat (mỗi 5 phút)

```python
# bot/health/monitor.py

class HealthMonitor:

    async def heartbeat(self):
        """Chạy mỗi 5 phút via APScheduler."""
        status = await self._check_all()
        await db.execute(
            "INSERT OR REPLACE INTO health_log VALUES (?,?,?,?)",
            (utc_now(), status.ok, status.details, status.uptime_seconds)
        )
        # Chỉ ping Telegram nếu silent_mode=False (tránh spam)
        if not self.silent_mode:
            await telegram.send_status(status)

    async def _check_all(self) -> HealthStatus:
        checks = await asyncio.gather(
            self._check_binance(),
            self._check_llm(),
            self._check_db(),
            self._check_scheduler(),
            return_exceptions=True,
        )
        return HealthStatus(checks)
```

### 2. Component Checks

```python
async def _check_binance(self) -> ComponentStatus:
    try:
        price = await binance.get_symbol_ticker("XAUUSDT")
        return ComponentStatus("binance", ok=True, latency_ms=...)
    except Exception as e:
        return ComponentStatus("binance", ok=False, error=str(e))

async def _check_llm(self) -> ComponentStatus:
    # Ping với prompt ngắn nhất có thể (tiết kiệm token)
    try:
        resp = await llm_provider.complete("test", "reply ok")
        return ComponentStatus("llm", ok=True, tokens_used=resp.input_tokens)
    except Exception as e:
        return ComponentStatus("llm", ok=False, error=str(e))

async def _check_db(self) -> ComponentStatus:
    try:
        await db.scalar("SELECT 1")
        return ComponentStatus("db", ok=True)
    except Exception as e:
        return ComponentStatus("db", ok=False, error=str(e))

async def _check_scheduler(self) -> ComponentStatus:
    jobs = scheduler.get_jobs()
    ok = len(jobs) > 0 and all(j.next_run_time is not None for j in jobs)
    return ComponentStatus("scheduler", ok=ok)
```

### 3. Heartbeat Telegram Messages

**Silent mode** (default — tránh spam):
```
# Không gửi Telegram khi mọi thứ OK
# Chỉ gửi khi có vấn đề
```

**Verbose mode** (`HEALTH_VERBOSE=true` — dùng khi debug):
```
✅ Bot Alive — 14:30 VN (28/03/2026)
Uptime: 3h 42m
Binance: ✅ 45ms | LLM: ✅ | DB: ✅
Mode: Swing | Paper: ON | Auto: OFF
Last signal: 12:15 VN (HOLD)
Next analysis: 16:00 VN
```

**Alert khi có vấn đề:**
```
⚠️ WARNING — 15:42 VN
Binance API: ❌ Connection timeout
Action: Auto trade PAUSED
→ Đang retry... (attempt 2/3)
```

```
🚨 CRITICAL — 15:45 VN
Bot heartbeat MISSING 15 minutes
Last seen: 15:30 VN
→ Auto-restart triggered via systemd
```

### 4. /health Command (on-demand)

```
/health → báo cáo tức thì

🏥 Health Report — 14:30 VN

System:
  Uptime:      3h 42m 15s
  Memory:      124 MB / 512 MB
  CPU:         2.3%

Components:
  Binance API: ✅  45ms
  Claude API:  ✅  1.2s
  Database:    ✅  0.3ms
  Scheduler:   ✅  4 jobs active

Trading:
  Mode:        Swing
  Paper:       ON
  Auto trade:  OFF
  Open pos:    0
  Last signal: 12:15 VN → HOLD (conf: 45%)
  Next run:    16:00 VN

Today:
  Signals:     8 | Executed: 2 | Filtered: 6
  LLM calls:  40 | Cost: $0.42
```

---

## Auto-restart via systemd

```ini
# /etc/systemd/system/gold-bot.service
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

[Install]
WantedBy=multi-user.target
```

```bash
# Deploy commands
sudo systemctl enable gold-bot    # auto-start on reboot
sudo systemctl start gold-bot
sudo journalctl -u gold-bot -f    # xem log realtime
```

---

## SQLite Schema

```sql
CREATE TABLE IF NOT EXISTS health_log (
    timestamp       DATETIME PRIMARY KEY,
    all_ok          BOOLEAN,
    details         TEXT,    -- JSON string: {"binance": true, "llm": true, ...}
    uptime_seconds  INTEGER
);

CREATE TABLE IF NOT EXISTS bot_events (
    id          TEXT PRIMARY KEY,
    timestamp   DATETIME DEFAULT CURRENT_TIMESTAMP,
    event_type  TEXT,   -- startup | shutdown | crash | restart | api_error
    component   TEXT,   -- binance | llm | db | scheduler
    message     TEXT,
    severity    TEXT    -- info | warning | critical
);
```

---

## .env additions

```bash
# HEALTH MONITOR
HEALTH_VERBOSE=false          # true = ping Telegram mỗi 5 phút dù ok
HEALTH_INTERVAL_MIN=5         # heartbeat interval
HEALTH_ALERT_AFTER_MIN=15     # alert sau bao nhiêu phút mất heartbeat
LLM_HEALTH_CHECK=false        # false = bỏ qua LLM ping (tiết kiệm token)
```

---

## Related Code Files

| Action | File | Description |
|--------|------|-------------|
| create | `bot/health/monitor.py` | `HealthMonitor` class |
| create | `bot/health/models.py` | `HealthStatus`, `ComponentStatus` dataclasses |
| modify | `bot/orchestrator.py` | Register heartbeat job vào APScheduler |
| modify | `bot/telegram/bot.py` | Thêm `/health` command |
| modify | `schema.sql` | Thêm `health_log` + `bot_events` tables |
| create | `/etc/systemd/system/gold-bot.service` | systemd unit file |

---

## Todo

- [ ] `health/models.py` — `HealthStatus`, `ComponentStatus`
- [ ] `health/monitor.py` — `HealthMonitor` với 4 component checks
- [ ] Heartbeat scheduler job (5 phút)
- [ ] Alert logic: miss 15 phút → Telegram CRITICAL
- [ ] Pause auto_trade khi Binance/LLM down
- [ ] `/health` Telegram command
- [ ] `health_log` + `bot_events` DB tables
- [ ] systemd service file
- [ ] Startup message: "🟢 Bot started — 14:30 VN | Mode: Swing | Paper: ON"
- [ ] Shutdown message: "🔴 Bot stopped gracefully — 14:35 VN"

## Success Criteria
- Bot crash → Telegram alert trong vòng 15 phút
- systemd tự restart trong 10s
- `/health` luôn trả về response trong 3s
- Binance API lỗi → auto_trade tạm dừng, không đặt lệnh sai

## Risk Assessment
| Risk | Mitigation |
|------|------------|
| LLM health check tốn token | `LLM_HEALTH_CHECK=false` default, ping chỉ khi thực sự cần |
| Heartbeat spam Telegram | `HEALTH_VERBOSE=false` default, chỉ alert khi có vấn đề |
| systemd restart loop | `StartLimitBurst=3` — dừng sau 3 lần restart liên tiếp, alert user |
| Bot restart giữa lúc có open position | Startup check: nếu có open position → alert user trước khi resume |

## Next Steps
- Sau live: thêm external uptime monitor (UptimeRobot free) ping `/health` endpoint
