# Phase 01 — Project Setup

## Context
- Parent plan: [plan.md](plan.md)
- Dependencies: none (entry point)
- Blocks: all other phases

## Overview
- **Date:** 2026-03-27
- **Priority:** P0 (critical path)
- **Status:** pending
- Scaffold the Python project: directory structure, virtual env, config management, SQLite schema, logging.

## Key Insights
- Use `python-dotenv` for `.env` config — never hardcode API keys
- SQLite with `aiosqlite` fits single-bot workload (no concurrency pressure)
- Structured logging with `loguru` is simpler than stdlib `logging` for async bots
- Store mutable runtime config (mode, auto_trade flag) in DB `config` table, not `.env`
- **Timezone**: DB + internal logic luôn dùng UTC. Chỉ convert sang ICT (UTC+7) khi hiển thị ra Telegram/log

## Timezone Strategy

```
RULE: Store UTC everywhere, display ICT (Vietnam UTC+7) to user

Internal (DB, scheduler, API):  datetime.utcnow() → lưu UTC
Display (Telegram, log, report): convert → ICT = UTC + 7h
```

```python
# bot/utils/timezone.py
from datetime import datetime, timezone, timedelta

ICT = timezone(timedelta(hours=7))  # Indochina Time (Vietnam)
UTC = timezone.utc

def utc_now() -> datetime:
    """Dùng trong toàn bộ internal logic."""
    return datetime.now(UTC)

def to_ict(dt: datetime) -> datetime:
    """Convert UTC datetime → ICT. Dùng khi format cho user."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)   # assume UTC nếu naive
    return dt.astimezone(ICT)

def fmt_ict(dt: datetime, fmt: str = "%Y-%m-%d %H:%M ICT") -> str:
    """Format datetime thành chuỗi ICT để hiển thị."""
    return to_ict(dt).strftime(fmt)

def session_label(utc_hour: int) -> str:
    """Trả về tên phiên + giờ VN tương ứng."""
    ict_hour = (utc_hour + 7) % 24
    if 1 <= utc_hour < 7:
        return f"Phiên Á/SGE ({ict_hour:02d}:00 VN)"
    elif 7 <= utc_hour < 12:
        return f"Phiên London ({ict_hour:02d}:00 VN)"
    elif 12 <= utc_hour < 20:
        return f"Phiên New York ({ict_hour:02d}:00 VN)"
    else:
        return f"Phiên chờ ({ict_hour:02d}:00 VN)"
```

### Bảng quy đổi phiên giao dịch UTC → VN

| Phiên | Giờ UTC | Giờ Việt Nam | Scalp? |
|-------|---------|--------------|--------|
| Dead zone | 20:00-00:00 | 03:00-07:00 | ❌ |
| Á / SGE | 01:00-07:00 | 08:00-14:00 | ✅ range |
| London open | 07:00-10:00 | 14:00-17:00 | ✅✅ |
| London/NY overlap | 12:00-16:00 | 19:00-23:00 | ✅✅ |
| NY afternoon | 16:00-20:00 | 23:00-03:00 | ✅ |

---

## Checklist Token / API Keys

Cần chuẩn bị đủ trước khi chạy bot. Thực hiện theo thứ tự:

### 1. Telegram Bot Token
```
1. Mở Telegram → tìm @BotFather
2. Gõ /newbot → đặt tên (vd: Gold Trader) → đặt username (vd: my_gold_trader_bot)
3. BotFather trả về token dạng: 7123456789:AAFxxx...
4. Copy vào TELEGRAM_BOT_TOKEN=
```

### 2. Telegram Chat ID
```
1. Mở bot vừa tạo → gõ /start
2. Truy cập URL: https://api.telegram.org/bot<TOKEN>/getUpdates
3. Tìm "chat" → "id" trong JSON (số nguyên, có thể âm nếu là group)
4. Copy vào TELEGRAM_CHAT_ID=
```

### 3. Binance API Key (Testnet — dùng khi PAPER_TRADE=true)
```
1. Truy cập: testnet.binancefuture.com
2. Đăng ký / đăng nhập
3. Vào API Management → Generate
4. Copy API Key + Secret Key
   → BINANCE_API_KEY=   (testnet key)
   → BINANCE_SECRET_KEY=
   → BINANCE_TESTNET=true
```

### 4. Binance API Key (Live — chỉ cần khi lên live)
```
1. Đăng nhập binance.com → Profile → API Management
2. Create API → "System generated"
3. Bật quyền:
   ✅ Enable Reading
   ✅ Enable Futures
   ✅ Enable Spot & Margin Trading (nếu dùng auto trade)
4. Whitelist IP của VPS (bắt buộc cho production)
   → Đổi BINANCE_TESTNET=false khi live
```

### 5. LLM API Key (chọn ít nhất 1)

| Provider | Trang lấy key | Env var |
|----------|--------------|---------|
| Anthropic (Claude) | console.anthropic.com → API Keys | `ANTHROPIC_API_KEY` |
| OpenAI (GPT) | platform.openai.com → API Keys | `OPENAI_API_KEY` |
| Google (Gemini) | aistudio.google.com → Get API Key | `GEMINI_API_KEY` |
| Deepseek | platform.deepseek.com → API Keys | `DEEPSEEK_API_KEY` |

### 6. Macro Data Keys (optional, có thể skip cho MVP)

| Service | Trang đăng ký | Ghi chú |
|---------|--------------|---------|
| FRED API | fred.stlouisfed.org/docs/api/api_key | Free, không giới hạn |
| NewsAPI | newsapi.org/register | Free 100 req/day |

---

## File `.env` đầy đủ
```bash
# === TELEGRAM ===
TELEGRAM_BOT_TOKEN=7123456789:AAFxxx...
TELEGRAM_CHAT_ID=123456789

# === BINANCE ===
BINANCE_API_KEY=xxx
BINANCE_SECRET_KEY=xxx
BINANCE_TESTNET=true          # true = testnet, false = live

# === LLM — chọn provider ===
LLM_PROVIDER=anthropic        # anthropic | openai | gemini | deepseek
LLM_MODEL=claude-sonnet-4-6
ANTHROPIC_API_KEY=sk-ant-xxx
OPENAI_API_KEY=sk-xxx         # nếu dùng openai / gpt-4o-mini cho scalp
GEMINI_API_KEY=xxx
DEEPSEEK_API_KEY=xxx

# Per-mode override (optional)
LLM_PROVIDER_SWING=anthropic
LLM_MODEL_SWING=claude-sonnet-4-6
LLM_PROVIDER_INTRADAY=deepseek
LLM_MODEL_INTRADAY=deepseek-chat
LLM_PROVIDER_SCALP=openai
LLM_MODEL_SCALP=gpt-4o-mini

# === MACRO DATA ===
FRED_API_KEY=xxx
NEWS_API_KEY=xxx

# === BOT SETTINGS ===
PAPER_TRADE=true              # LUÔN bắt đầu bằng true
TRADING_MODE=swing            # swing | intraday | scalp
AUTO_TRADE=false              # false = chỉ bắn signal Telegram
DRY_RUN=false                 # true = chỉ in signal, không ghi DB

# === DB & LOG ===
DB_PATH=data/bot.db
LOG_LEVEL=INFO
```

## Requirements

**Functional:**
- Python 3.11+ project with isolated venv
- All secrets in `.env`, never committed
- SQLite DB with signals / trades / config tables
- Rotating file logger + console output
- `requirements.txt` pinned versions

**Non-functional:**
- Zero secrets in version control
- DB migrations handled by explicit `schema.sql` run on startup
- Logging must include timestamp, level, module name

## Architecture

```
trade-gold/
├── .env                    # secrets (gitignored)
├── .env.example            # template committed
├── .gitignore
├── requirements.txt
├── schema.sql              # DB definition, run on startup
├── README.md
├── bot/
│   ├── __init__.py
│   ├── config.py           # loads .env, exposes Settings dataclass
│   ├── database.py         # aiosqlite connection, init_db()
│   ├── logger.py           # loguru setup
│   ├── utils/
│   │   ├── __init__.py
│   │   └── timezone.py     # utc_now(), to_ict(), fmt_ict(), session_label()
│   ├── data/               # Phase 02
│   ├── llm/                # Phase 03
│   ├── risk/               # Phase 04
│   ├── modes/              # Phase 05
│   ├── telegram/           # Phase 06
│   ├── trader/             # Phase 07 + 09
│   ├── cost/               # Phase 10
│   └── orchestrator.py     # Phase 08
└── tests/
    └── __init__.py
```

## Related Code Files

| Action | File | Description |
|--------|------|-------------|
| create | `bot/config.py` | Settings dataclass from env |
| create | `bot/database.py` | DB init, connection factory |
| create | `bot/logger.py` | Loguru config |
| create | `bot/utils/timezone.py` | `utc_now()`, `to_ict()`, `fmt_ict()`, `session_label()` |
| create | `schema.sql` | All table definitions |
| create | `requirements.txt` | Pinned dependencies |
| create | `.env.example` | Key template |
| create | `.gitignore` | Exclude .env, __pycache__, *.db |

## Implementation Steps

1. Create project root at `trade-gold/` with `mkdir -p bot/data bot/llm bot/risk bot/modes bot/telegram bot/execution tests`
2. Create `.gitignore` — include `.env`, `*.db`, `__pycache__/`, `.venv/`, `*.pyc`
3. Create `.env.example` (committed to git, values trống):
   ```bash
   # TELEGRAM — xem hướng dẫn lấy token ở phase-01 checklist
   TELEGRAM_BOT_TOKEN=
   TELEGRAM_CHAT_ID=

   # BINANCE — testnet: testnet.binancefuture.com | live: binance.com
   BINANCE_API_KEY=
   BINANCE_SECRET_KEY=
   BINANCE_TESTNET=true

   # LLM PROVIDER
   LLM_PROVIDER=anthropic
   LLM_MODEL=claude-sonnet-4-6
   ANTHROPIC_API_KEY=
   OPENAI_API_KEY=
   GEMINI_API_KEY=
   DEEPSEEK_API_KEY=

   # PER-MODE OVERRIDE (optional)
   LLM_PROVIDER_SWING=anthropic
   LLM_MODEL_SWING=claude-sonnet-4-6
   LLM_PROVIDER_INTRADAY=deepseek
   LLM_MODEL_INTRADAY=deepseek-chat
   LLM_PROVIDER_SCALP=openai
   LLM_MODEL_SCALP=gpt-4o-mini

   # MACRO DATA
   FRED_API_KEY=
   NEWS_API_KEY=

   # BOT SETTINGS
   PAPER_TRADE=true
   TRADING_MODE=swing
   AUTO_TRADE=false
   DRY_RUN=false

   # SYSTEM
   DB_PATH=data/bot.db
   LOG_LEVEL=INFO
   ```
4. Create `requirements.txt` with pinned versions:
   ```
   python-binance==1.0.19
   pandas==2.2.2
   pandas-ta==0.3.14b
   anthropic==0.28.0
   python-telegram-bot==21.3
   apscheduler==3.10.4
   aiosqlite==0.20.0
   pandas-datareader==0.10.0
   newsapi-python==0.2.7
   python-dotenv==1.0.1
   loguru==0.7.2
   httpx==0.27.0
   ```
5. Create `bot/config.py` — dataclass `Settings` with fields matching `.env.example`; load via `python-dotenv`; validate required fields on import (raise `ValueError` if missing)
6. Create `schema.sql`:
   ```sql
   CREATE TABLE IF NOT EXISTS signals (
       id TEXT PRIMARY KEY,
       created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
       mode TEXT NOT NULL,
       action TEXT NOT NULL,          -- BUY/SELL/HOLD
       entry REAL, sl REAL,
       tp1 REAL, tp2 REAL, tp3 REAL,
       confidence INTEGER,
       trend_bias TEXT,
       reasoning TEXT,
       status TEXT DEFAULT 'pending'  -- pending/approved/rejected/executed/expired
   );

   CREATE TABLE IF NOT EXISTS trades (
       id TEXT PRIMARY KEY,
       signal_id TEXT REFERENCES signals(id),
       opened_at DATETIME DEFAULT CURRENT_TIMESTAMP,
       closed_at DATETIME,
       side TEXT,                     -- LONG/SHORT
       entry REAL, close_price REAL,
       quantity REAL,
       leverage INTEGER,
       pnl REAL, pnl_pct REAL,
       tp_hit INTEGER DEFAULT 0,      -- how many TPs hit (0-3)
       status TEXT DEFAULT 'open'     -- open/closed/stopped/liquidated
   );

   CREATE TABLE IF NOT EXISTS config (
       key TEXT PRIMARY KEY,
       value TEXT NOT NULL,
       updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
   );

   -- Default config rows
   INSERT OR IGNORE INTO config VALUES ('mode', 'intraday', CURRENT_TIMESTAMP);
   INSERT OR IGNORE INTO config VALUES ('auto_trade', 'false', CURRENT_TIMESTAMP);
   INSERT OR IGNORE INTO config VALUES ('daily_pnl', '0', CURRENT_TIMESTAMP);
   INSERT OR IGNORE INTO config VALUES ('circuit_breaker', 'false', CURRENT_TIMESTAMP);
   ```
7. Create `bot/database.py` — `init_db(db_path)` reads `schema.sql` and executes it; `get_db()` async context manager returning `aiosqlite.Connection`
8. Create `bot/logger.py` — loguru with: rotating file sink (`logs/bot.log`, 10MB, 7-day retention), stderr sink with color, format: `{time:YYYY-MM-DD HH:mm:ss} | {level} | {name}:{line} | {message}`
9. Create `bot/utils/timezone.py` với `ICT`, `UTC`, `utc_now()`, `to_ict()`, `fmt_ict()`, `session_label()`
   - Rule: **chỉ import `utc_now()`** trong internal logic; **chỉ dùng `fmt_ict()`** khi format cho Telegram/log
10. Add `__init__.py` to all package dirs
10. Run `python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
11. Smoke test: `python -c "from bot.config import Settings; s = Settings(); print(s)"`

## Todo

- [ ] Create directory structure
- [ ] Write `.gitignore`
- [ ] Write `.env.example`
- [ ] Pin all deps in `requirements.txt`
- [ ] Implement `bot/config.py`
- [ ] Write `schema.sql`
- [ ] Implement `bot/database.py`
- [ ] Implement `bot/logger.py`
- [ ] `bot/utils/timezone.py` — `utc_now()`, `to_ict()`, `fmt_ict()`, `session_label()`
- [ ] Add `__init__.py` files
- [ ] Smoke test imports

## Success Criteria
- `python -c "from bot.config import Settings"` succeeds with valid `.env`
- `init_db()` creates all three tables without error
- Log file appears in `logs/bot.log` on first run
- `.env` not tracked by git

## Risk Assessment
| Risk | Impact | Mitigation |
|------|--------|------------|
| Secrets in git | Critical | `.gitignore` + pre-commit hook |
| DB migration drift | Medium | Explicit `IF NOT EXISTS` in schema, run on every startup |
| Dep version conflicts | Low | Pin all versions, test in clean venv |

## Security Considerations
- `.env` must be in `.gitignore` before first commit
- `Settings` class should never log its own fields (API keys)
- DB file permissions: `chmod 600 data/bot.db` on production server
- Add `pre-commit` hook that blocks commit if `.env` exists in staging area

## Next Steps
- Phase 02: implement data layer using `Settings` from this phase
- Phase 08: `init_db()` called as first step in main entry point
