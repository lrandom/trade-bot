# Phase A — Foundation

## Context
- Parent plan: [plan.md](plan.md)
- Design spec: `plans/20260327-1200-gold-trading-bot/phase-01-project-setup.md`
- Dependencies: none (entry point)
- Blocks: all other phases

## Overview
- **Date:** 2026-03-28
- **Priority:** P0 (critical path)
- **Status:** pending
- Scaffold project directory, config management, SQLite schema + async DB, structured logging, timezone utilities.

## Key Insights
- `python-dotenv` for `.env` — never hardcode secrets
- `aiosqlite` for SQLite — single-bot workload, no concurrency pressure
- `loguru` over stdlib `logging` — simpler rotating file setup for async bots
- Runtime mutable config (mode, auto_trade, circuit_breaker) stored in DB `config` table, not `.env` — survives restarts
- Timezone rule: UTC everywhere internal; ICT (UTC+7) only at display time (Telegram + logs)

## Requirements

**Functional:**
- Python 3.11+ project with venv
- All secrets in `.env`, gitignored
- SQLite DB with `signals`, `trades`, `config` tables; schema run on every startup
- Rotating file logger + colored console output
- `utc_now()`, `to_ict()`, `fmt_ict()`, `session_label()` timezone helpers

**Non-functional:**
- Zero secrets in version control
- `IF NOT EXISTS` in all schema DDL — idempotent migrations
- Logs include timestamp, level, module:line, message

## Architecture

```
trade-gold/
├── .env                    # gitignored — actual secrets
├── .env.example            # committed — empty template
├── .gitignore
├── requirements.txt        # pinned versions
├── schema.sql              # all CREATE TABLE statements
├── main.py                 # entry point: asyncio.run(main())
├── bot/
│   ├── __init__.py
│   ├── config.py           # Settings dataclass from .env
│   ├── database.py         # init_db(), get_db() context manager
│   ├── logger.py           # loguru setup
│   └── utils/
│       ├── __init__.py
│       └── timezone.py     # utc_now, to_ict, fmt_ict, session_label
├── tests/
│   └── __init__.py
└── data/                   # runtime: bot.db lives here (gitignored)
```

## Related Code Files

| Action | File | Description |
|--------|------|-------------|
| create | `trade-gold/.gitignore` | Exclude `.env`, `*.db`, `__pycache__`, `.venv`, `logs/` |
| create | `trade-gold/.env.example` | All keys with empty values + inline comments |
| create | `trade-gold/requirements.txt` | Pinned deps |
| create | `trade-gold/schema.sql` | `signals`, `trades`, `config` + default INSERT rows |
| create | `trade-gold/bot/config.py` | `Settings` dataclass via `python-dotenv` |
| create | `trade-gold/bot/database.py` | `init_db()`, `get_db()` async context manager |
| create | `trade-gold/bot/logger.py` | loguru rotating file + stderr sinks |
| create | `trade-gold/bot/utils/timezone.py` | `ICT`, `UTC`, 4 helper functions |
| create | `trade-gold/main.py` | `asyncio.run(main())` stub |

## Implementation Steps

1. **Directory scaffold**
   ```bash
   mkdir -p trade-gold/bot/utils trade-gold/bot/data trade-gold/bot/llm \
     trade-gold/bot/risk trade-gold/bot/modes trade-gold/bot/telegram \
     trade-gold/bot/trader trade-gold/bot/cost trade-gold/bot/health \
     trade-gold/bot/filters trade-gold/tests trade-gold/data trade-gold/logs
   touch trade-gold/bot/__init__.py trade-gold/bot/utils/__init__.py \
     trade-gold/tests/__init__.py
   ```

2. **`.gitignore`** — include: `.env`, `*.db`, `data/`, `logs/`, `__pycache__/`, `.venv/`, `*.pyc`, `*.egg-info/`

3. **`.env.example`** — all keys from the template below (empty values):
   ```bash
   # TELEGRAM
   TELEGRAM_BOT_TOKEN=
   TELEGRAM_CHAT_ID=

   # BINANCE
   BINANCE_API_KEY=
   BINANCE_SECRET_KEY=
   BINANCE_TESTNET=true

   # LLM
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
   TRADING_MODE=intraday
   AUTO_TRADE=false
   DRY_RUN=false

   # HEALTH MONITOR
   HEALTH_VERBOSE=false
   HEALTH_INTERVAL_MIN=5
   HEALTH_ALERT_AFTER_MIN=15
   LLM_HEALTH_CHECK=false

   # SYSTEM
   DB_PATH=data/bot.db
   LOG_LEVEL=INFO
   LLM_DAILY_BUDGET_USD=5.0
   ```

4. **`requirements.txt`** — pinned versions:
   ```
   python-binance==1.0.19
   pandas==2.2.2
   pandas-ta==0.3.14b
   anthropic==0.28.0
   openai==1.35.0
   google-generativeai==0.7.2
   python-telegram-bot==21.3
   apscheduler==3.10.4
   aiosqlite==0.20.0
   pandas-datareader==0.10.0
   newsapi-python==0.2.7
   python-dotenv==1.0.1
   loguru==0.7.2
   httpx==0.27.0
   ```

5. **`bot/config.py`** — `Settings` dataclass:
   - Load `.env` with `load_dotenv()` in `__init__`
   - Fields: all keys from `.env.example` with types and defaults
   - `PAPER_TRADE: bool = True` (default safe)
   - `AUTO_TRADE: bool = False`
   - `BINANCE_TESTNET: bool = True`
   - Validate required fields in `__post_init__`: raise `ValueError` if `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `BINANCE_API_KEY` are empty
   - Never log any field value (API keys)
   - Use `@dataclass` (not pydantic — keep deps minimal)

6. **`schema.sql`** — DDL with `IF NOT EXISTS`:
   ```sql
   CREATE TABLE IF NOT EXISTS signals (
       id TEXT PRIMARY KEY,
       created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
       mode TEXT NOT NULL,
       action TEXT NOT NULL,        -- BUY | SELL | HOLD
       entry REAL, sl REAL,
       tp1 REAL, tp2 REAL, tp3 REAL,
       confidence INTEGER,
       trend_bias TEXT,
       reasoning TEXT,
       telegram_msg_id INTEGER,
       status TEXT DEFAULT 'pending' -- pending|approved|rejected|executed|expired
   );

   CREATE TABLE IF NOT EXISTS trades (
       id TEXT PRIMARY KEY,
       signal_id TEXT REFERENCES signals(id),
       opened_at DATETIME DEFAULT CURRENT_TIMESTAMP,
       closed_at DATETIME,
       side TEXT,                   -- LONG | SHORT
       entry REAL, close_price REAL,
       quantity REAL,
       leverage INTEGER,
       pnl REAL, pnl_pct REAL,
       tp_hit INTEGER DEFAULT 0,    -- 0-3
       status TEXT DEFAULT 'open'   -- open|closed|stopped|liquidated
   );

   CREATE TABLE IF NOT EXISTS config (
       key TEXT PRIMARY KEY,
       value TEXT NOT NULL,
       updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
   );

   INSERT OR IGNORE INTO config VALUES ('mode', 'intraday', CURRENT_TIMESTAMP);
   INSERT OR IGNORE INTO config VALUES ('auto_trade', 'false', CURRENT_TIMESTAMP);
   INSERT OR IGNORE INTO config VALUES ('daily_pnl', '0', CURRENT_TIMESTAMP);
   INSERT OR IGNORE INTO config VALUES ('circuit_breaker', 'false', CURRENT_TIMESTAMP);
   ```

7. **`bot/database.py`**:
   - `async def init_db(db_path: str)`: read `schema.sql`, execute via `aiosqlite.connect`, enable WAL mode (`PRAGMA journal_mode=WAL`)
   - `get_db(db_path)`: `@asynccontextmanager` yielding `aiosqlite.Connection` with `row_factory = aiosqlite.Row`

8. **`bot/logger.py`** — loguru setup:
   - Remove default handler
   - Add stderr sink: level=`LOG_LEVEL`, colorize=True, format=`{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {name}:{line} | {message}`
   - Add file sink: `logs/bot.log`, rotation=`10 MB`, retention=`7 days`, same format, no colorize
   - Export `logger` (re-export from loguru)

9. **`bot/utils/timezone.py`**:
   ```python
   from datetime import datetime, timezone, timedelta

   ICT = timezone(timedelta(hours=7))
   UTC = timezone.utc

   def utc_now() -> datetime:
       return datetime.now(UTC)

   def to_ict(dt: datetime) -> datetime:
       if dt.tzinfo is None:
           dt = dt.replace(tzinfo=UTC)
       return dt.astimezone(ICT)

   def fmt_ict(dt: datetime, fmt: str = "%Y-%m-%d %H:%M ICT") -> str:
       return to_ict(dt).strftime(fmt)

   def session_label(utc_hour: int) -> str:
       ict_hour = (utc_hour + 7) % 24
       if 1 <= utc_hour < 7:
           return f"Asian/SGE ({ict_hour:02d}:00 VN)"
       elif 7 <= utc_hour < 12:
           return f"London ({ict_hour:02d}:00 VN)"
       elif 12 <= utc_hour < 20:
           return f"NY ({ict_hour:02d}:00 VN)"
       else:
           return f"Off-session ({ict_hour:02d}:00 VN)"
   ```
   Rule: use `utc_now()` everywhere internal; use `fmt_ict()` only when formatting for Telegram/log display.

10. **`main.py`** stub:
    ```python
    import asyncio
    from bot.orchestrator import main

    if __name__ == "__main__":
        asyncio.run(main())
    ```

11. Smoke test: `python -c "from bot.config import Settings; s = Settings(); print('OK')"`

## Todo

- [ ] Create directory scaffold + `__init__.py` files
- [ ] Write `.gitignore`
- [ ] Write `.env.example`
- [ ] Write `requirements.txt` with pinned versions
- [ ] Implement `bot/config.py` — Settings dataclass
- [ ] Write `schema.sql` — 3 tables + default config rows
- [ ] Implement `bot/database.py` — `init_db()` + `get_db()`
- [ ] Implement `bot/logger.py` — loguru dual sink
- [ ] Implement `bot/utils/timezone.py` — 4 helpers
- [ ] Create `main.py` stub
- [ ] Run `pip install -r requirements.txt` in venv
- [ ] Smoke test imports

## Success Criteria
- `from bot.config import Settings; Settings()` succeeds with valid `.env`
- `init_db()` creates all 3 tables without error on fresh DB
- `logs/bot.log` appears on first run
- `.env` not tracked by `git status`
- `fmt_ict(utc_now())` returns correctly formatted ICT string

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| Secrets committed to git | Critical | `.gitignore` set before first commit; add pre-commit hook |
| DB schema drift on updates | Medium | `IF NOT EXISTS` everywhere; run `init_db()` on every startup |
| Dependency version conflicts | Low | Pin all versions; test in clean venv |
| Wrong timezone display | Medium | Unit test `fmt_ict` with known UTC → expected ICT output |

## Security Considerations
- `.env` in `.gitignore` before first `git add`
- `Settings.__repr__` must not print field values
- DB file permissions: `chmod 600 data/bot.db` on production VPS
- Consider adding pre-commit hook: `git diff --cached --name-only | grep -q '.env' && exit 1`

## Next Steps
- Phase B: `Settings` + `get_db()` used by data layer
- Phase G: `init_db()` called as first step in `main()`
