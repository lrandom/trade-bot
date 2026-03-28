# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A Python-based gold (XAUUSDT) trading bot that uses a multi-LLM analysis chain to generate trade signals, with Telegram for notifications/control, Binance Futures for execution, and SQLite for persistence. Runs in paper-trade mode by default.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the full bot (paper trade by default)
python main.py

# Dry run: one analysis cycle, prints signal to stdout, no DB or Telegram
python main.py --dry-run

# Run tests
pytest tests/

# Run a single test file
pytest tests/test_risk.py -v
```

## Environment Setup

Copy `.env` (not committed) with these keys:

```
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
BINANCE_API_KEY=
BINANCE_SECRET_KEY=
BINANCE_TESTNET=true
ANTHROPIC_API_KEY=          # At least one LLM key required
OPENAI_API_KEY=
GEMINI_API_KEY=
DEEPSEEK_API_KEY=
PAPER_TRADE=true            # default; set false for live trading
TRADING_MODE=swing          # swing | intraday | scalp
AUTO_TRADE=false            # if true, executes signals without Telegram approval
DB_PATH=data/bot.db
LLM_DAILY_BUDGET_USD=5.0
```

Per-mode LLM overrides: `LLM_PROVIDER_SWING`, `LLM_MODEL_SWING`, etc.

`settings.validate()` is not called on import — it must be called explicitly to check for missing keys.

## Architecture

### Signal Generation Pipeline

The core flow per analysis cycle (`bot/orchestrator.py → _run_analysis_cycle`):

1. **Pre-filter** — ATR spike check (`bot/filters/volatility_filter.py`)
2. **Snapshot** — fetch OHLCV, indicators, S/R levels, macro data (`bot/data/snapshot.py → build_snapshot`)
3. **LLM chain** — HTF→MTF→LTF gated analysis (`bot/llm/engine.py → LLMEngine.generate_signal`)
4. **Post-filter** — DXY/Silver correlation check (`bot/filters/correlation_filter.py`)
5. **Risk check** — position sizing and circuit breaker (`bot/risk/`)
6. **Execute or approve** — auto-trade or send to Telegram for manual approval

### LLM Engine Gates (`bot/llm/engine.py`)

The `LLMEngine` runs 5 sequential LLM calls. Each intermediate step (Macro, HTF, MTF, LTF) uses plain text completion parsed with regex. The final signal uses structured tool/function calling (JSON). Any gate that fails returns `TradingSignal.hold()`.

- **Gate 1**: `htf_bias != NEUTRAL`
- **Gate 2**: `mtf.confirms_htf == True`
- **Gate 3**: `ltf.entry_trigger == True`
- All 3 aligned → structured signal generation

### Trading Modes (`bot/modes/config.py`)

| Mode | Timeframes | Interval | Model | Leverage |
|------|-----------|----------|-------|----------|
| `scalp` | 1m, 5m | candle_close | claude-haiku-4-5 | 10× |
| `intraday` | 15m, 1h | 15 min | claude-sonnet-4-6 | 5× |
| `swing` | 4h, 1d | 240 min | claude-sonnet-4-6 | 3× |

Mode is stored in the `config` DB table and can be changed at runtime via Telegram commands.

### LLM Providers (`bot/llm/providers/`)

- `base.py` — `BaseLLMProvider` with `complete()` and `complete_structured()` returning a response object with `text`, `tool_data`, `input_tokens`, `output_tokens`
- Concrete providers: `anthropic_provider.py`, `openai_provider.py`, `gemini_provider.py`, `deepseek_provider.py`
- `factory.py` — selects provider per mode using `settings.get_llm_provider_for_mode(mode)`

### Data Layer (`bot/data/`)

- `snapshot.py` — assembles `MarketSnapshot` (OHLCV DataFrames + indicators dict + S/R levels + macro data + mark price)
- `indicators.py` — computes EMA 20/50/200, RSI, MACD, ATR, SuperTrend, VWAP, Bollinger Bands via pandas-ta
- `ohlcv.py` — fetches klines from Binance
- `macro.py` — FRED (DFF, T10Y2Y) and NewsAPI gold headlines
- `websocket_feed.py` — live 1m candle buffer used in scalp mode only

### Trader Layer (`bot/trader/`)

- `factory.py` — returns `MockTrader` (paper) or `RealTrader` (live) based on `settings.paper_trade`
- `mock_trader.py` — simulates order execution, writes to `paper_orders` table
- `real_trader.py` — calls Binance Futures API
- `trade_executor.py` — fetches signal from DB, calls trader, records trade
- `position_monitor.py` — runs as a background task; polls open positions, calls `LLMEngine.manage_trade()` for management decisions (HOLD / EXIT / ADJUST_SL)

### Database (`schema.sql`, `bot/database.py`)

SQLite via `aiosqlite`. Schema is applied on startup via `init_db()` (all `IF NOT EXISTS`). Key tables:
- `signals` — LLM-generated signals with status lifecycle: `pending → approved/rejected → executed/expired`
- `trades` / `paper_orders` — execution records
- `config` — runtime-mutable settings (mode, auto_trade, paper_trade, circuit_breaker)
- `llm_usage` — per-call token/cost tracking
- `filter_log` — signal filter decisions for post-analysis

### Scheduler (`bot/orchestrator.py`)

APScheduler (`AsyncIOScheduler`) manages:
- `analysis_job` — runs `_run_analysis_cycle` on the mode interval
- `daily_reset` — resets daily PnL and circuit breaker at 00:01 UTC
- `heartbeat` — health monitor snapshot (default every 5 min)

### Telegram Bot (`bot/telegram/`)

- `bot.py` — builds the `python-telegram-bot` Application
- `handlers.py` — command handlers (`/signal`, `/status`, `/mode`, `/approve`, `/reject`, etc.)
- `notifier.py` — `send_message()`, `send_signal_for_approval()` (sends inline keyboard for approve/reject)
- `formatters.py` — Markdown message formatting

### Risk Engine (`bot/risk/`)

- `calculator.py` — `calc_position_size()`, `validate_signal_sl()`, `validate_signal_prices()`
- `circuit_breaker.py` — halts trading if daily PnL drawdown exceeds threshold; `reset_daily_pnl()` called at midnight
- `limits.py` — `RiskEngine.pre_trade_check()` orchestrates all risk validation

### Cost Tracking (`bot/cost/`)

Tracks LLM token costs and trading fees per call/trade; rolls up to `cost_summary` table daily.
