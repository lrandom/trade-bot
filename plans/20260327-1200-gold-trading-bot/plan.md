# Gold Trading Bot — XAUUSDT Binance Futures
**Date:** 2026-03-28 | **Status:** ✅ Implemented

## Overview
Python bot trading XAUUSDT on Binance Futures. Claude LLM as decision engine. Telegram for human-in-the-loop approval + monitoring. Three trading modes: scalp, intraday, swing.

## Stack
- **Exchange:** python-binance (REST + WebSocket)
- **Indicators:** pandas-ta
- **LLM:** anthropic SDK (claude-sonnet-4-6 / claude-haiku-4-5)
- **Telegram:** python-telegram-bot v20 (async)
- **Scheduler:** APScheduler AsyncIOScheduler
- **DB:** SQLite (aiosqlite)
- **Macro:** FRED API (pandas-datareader) + NewsAPI

## Testing Ladder (trước khi live)

```
[1] --dry-run       Prompt Validation   In signal ra terminal, không DB, không Telegram
                                        Mục đích: kiểm tra LLM prompt + JSON output
                                        Chi phí: LLM calls bình thường

[2] PAPER_TRADE=true  Paper Trade       Full pipeline, mock order vào SQLite
                                        Telegram bắn signal + kết quả lệnh ảo
                                        Theo dõi PnL ảo theo giá thực Binance
                                        KPI để lên live: win rate ≥50%, profit factor ≥1.3

[3] Live micro size   Live              Trade thật, MIN_POSITION_USD=10
                                        Xác nhận execution khớp paper trade
```

## Phases

| # | Phase | Status | File |
|---|-------|--------|------|
| 01 | Project Setup | ✅ completed | [phase-01-project-setup.md](phase-01-project-setup.md) |
| 02 | Data Layer | ✅ completed | [phase-02-data-layer.md](phase-02-data-layer.md) |
| 03 | LLM Engine (Provider Pattern) | ✅ completed | [phase-03-claude-llm-engine.md](phase-03-claude-llm-engine.md) |
| 04 | Risk Management | ✅ completed | [phase-04-risk-management.md](phase-04-risk-management.md) |
| 05 | Trading Modes | ✅ completed | [phase-05-trading-modes.md](phase-05-trading-modes.md) |
| 06 | Telegram Bot | ✅ completed | [phase-06-telegram-bot.md](phase-06-telegram-bot.md) |
| 07 | Execution Engine + Paper Trade | ✅ completed | [phase-07-execution-engine.md](phase-07-execution-engine.md) |
| 08 | Orchestration | ✅ completed | [phase-08-orchestration.md](phase-08-orchestration.md) |
| 09 | Paper Trade Dashboard | ✅ completed | [phase-09-paper-trade.md](phase-09-paper-trade.md) |
| 10 | Cost Tracking | ✅ completed | [phase-10-cost-tracking.md](phase-10-cost-tracking.md) |
| 11 | Health Monitor | ✅ completed | [phase-11-health-monitor.md](phase-11-health-monitor.md) |
| 12 | Volatility & Correlation Filter | ✅ completed | [phase-12-volatility-filter.md](phase-12-volatility-filter.md) |

## System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      MAIN LOOP (asyncio)                     │
│                                                              │
│  ┌─────────────┐   ┌──────────────┐   ┌──────────────────┐ │
│  │ APScheduler │   │  WebSocket   │   │   Telegram Bot   │ │
│  │ (4h/15m/1m)│   │ (scalp feed) │   │    (commands)    │ │
│  └──────┬──────┘   └──────┬───────┘   └──────┬───────────┘ │
│         └─────────────────┼───────────────────┘             │
│                           ▼                                 │
│                   ┌───────────────┐                         │
│                   │  Pre-Filter   │ ← pandas-ta indicators  │
│                   │ (EMA/RSI/ST)  │   (avoids LLM cost)     │
│                   └───────┬───────┘                         │
│               setup detected?│                              │
│                           ▼                                 │
│   ┌──────────────── LLM Chain (Provider Pattern) ──────┐  │
│   │                                                      │  │
│   │  [1] Macro    FRED + NewsAPI ──► bias + risks        │  │
│   │       │                                              │  │
│   │  [2] HTF      W1/D1/H4 ──► htf_bias                 │  │
│   │       │       BUY-ONLY / SELL-ONLY / NEUTRAL         │  │
│   │       │ NEUTRAL? ──────────────────────► HOLD        │  │
│   │       │                                              │  │
│   │  [3] MTF      H4/H1 ──► pullback/impulse?            │  │
│   │       │       confirms HTF bias?                     │  │
│   │       │ NO? ───────────────────────────► HOLD        │  │
│   │       │                                              │  │
│   │  [4] LTF      M15/H1 ──► entry trigger?              │  │
│   │       │       candle pattern + RSI confirm           │  │
│   │       │ NO? ───────────────────────────► HOLD        │  │
│   │       │                                              │  │
│   │  [5] SIGNAL   3 TF aligned ──► tool_use JSON         │  │
│   │               {action, entry, sl, tp1/2/3,           │  │
│   │                htf_bias, confidence, reasoning}      │  │
│   └──────────────────────┬───────────────────────────────┘  │
│                          ▼                                  │
│             ┌────────────────────────┐                      │
│             │    Risk Management     │                      │
│             │ ATR SL validate · Size │                      │
│             │ Circuit breaker 5%     │                      │
│             └────────────┬───────────┘                      │
│                          ▼                                  │
│        ┌─────────────────┴──────────────────┐              │
│        │ AUTO MODE              SIGNAL MODE  │              │
│        ▼                                    ▼              │
│  ┌───────────┐                 ┌──────────────────────┐    │
│  │  Binance  │                 │   Telegram Signal    │    │
│  │  Futures  │                 │  [✅ Approve]        │    │
│  │ Order API │                 │  [❌ Reject]         │    │
│  └───────────┘                 └──────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

## LLM Cost Estimate

### Tokens per analysis cycle (5 calls: Macro + HTF + MTF + LTF + Signal)
| Call | Input | Output |
|------|-------|--------|
| Macro Analysis | ~1,500 | ~300 |
| HTF (W1/D1/H4) | ~3,000 | ~400 |
| MTF (H4/H1) | ~2,000 | ~300 |
| LTF (M15/H1) | ~1,500 | ~300 |
| Signal Generation | ~1,000 | ~400 |
| **Total/cycle** | **~9,000** | **~1,700** |

> Early gates: if HTF=NEUTRAL → stop after 2 calls (~4,500 in + 700 out). Average ~6,000/1,200 with gate filtering.

### Monthly cost by provider & mode (with indicator pre-filter)

**Swing (6 cycles/day)**
| Provider | Model | Input $/M | Output $/M | Cost/month |
|----------|-------|-----------|------------|------------|
| Claude | sonnet-4-6 | $3.00 | $15.00 | ~$10 |
| OpenAI | gpt-4o | $2.50 | $10.00 | ~$8 |
| Gemini | 2.0 Flash | $0.10 | $0.40 | ~$0.40 |
| Deepseek | V3 | $0.27 | $1.10 | ~$0.90 |

**Intraday (~50 filtered cycles/day)**
| Provider | Model | Cost/month |
|----------|-------|------------|
| Claude | sonnet-4-6 | ~$85 |
| OpenAI | gpt-4o | ~$70 |
| OpenAI | gpt-4o-mini | ~$4 |
| Gemini | 2.0 Flash | ~$3 |
| Deepseek | V3 | ~$8 |

**Scalp (~80 filtered cycles/day)**
| Provider | Model | Cost/month |
|----------|-------|------------|
| Claude | haiku-4-5 | ~$20 |
| OpenAI | gpt-4o-mini | ~$6 |
| Gemini | 2.0 Flash | ~$5 |
| Deepseek | V3 | ~$12 |

**Key rule: indicator pre-filter runs first (EMA cross, RSI threshold), LLM only called when a setup is detected. Raw 1m polling without filter = ~$500+/month — avoid.**

### LLM Provider Comparison

| Provider | Model | Reasoning | JSON tool_use | Latency | Cost | Best for |
|----------|-------|-----------|---------------|---------|------|----------|
| **Anthropic** | claude-sonnet-4-6 | ⭐⭐⭐⭐⭐ | Native tool_use ✅ | ~3-5s | $$$ | Swing / Intraday — best analysis quality |
| **Anthropic** | claude-haiku-4-5 | ⭐⭐⭐ | Native tool_use ✅ | ~1-2s | $ | Scalp — fast + cheap |
| **OpenAI** | gpt-4o | ⭐⭐⭐⭐ | function_calling ✅ | ~2-4s | $$$ | Good alternative, stable API |
| **OpenAI** | gpt-4o-mini | ⭐⭐⭐ | function_calling ✅ | ~1-2s | $ | Budget scalp mode |
| **Google** | gemini-2.0-flash | ⭐⭐⭐ | function_calling ✅ | ~1-2s | $ | Cheapest, good for high-freq |
| **Google** | gemini-1.5-pro | ⭐⭐⭐⭐ | function_calling ✅ | ~3-5s | $$ | Long context (full OHLCV) |
| **Deepseek** | deepseek-v3 | ⭐⭐⭐⭐ | Partial ⚠️ | ~2-4s | $ | Best cost/quality ratio |
| **Deepseek** | deepseek-r1 | ⭐⭐⭐⭐⭐ | Partial ⚠️ | ~5-10s | $$ | Complex swing analysis, slow |

**Pros/Cons Summary (updated với benchmark data thực tế):**

| Model | Trading Strength | Trading Weakness | Hallucination Risk |
|-------|-----------------|------------------|--------------------|
| Claude sonnet-4-6 | Best structured output, deep doc analysis, reliable JSON | Chưa có benchmark trading riêng | Thấp (~2-3%) |
| GPT-4o | Tốt cho data processing, stable API | **Numeric hallucination** trên price levels | ~5-8% |
| GPT-4o-mini | Rẻ, JSON ổn | Yếu financial reasoning | ~8-12% |
| Gemini 2.5/Flash | Real-time search, rẻ | **Higher trading losses** trong benchmark | ~6-10% |
| Deepseek V3 | 14%+ crypto returns (50 ngày), rẻ | China-hosted (data privacy), tool_use partial | ~4-6% |
| Deepseek R1 | Math reasoning mạnh | **Không có lợi thế** trong trading so với instruct models, chậm (5-10s) | ~4-6% |
| Qwen3-235B | Top LiveTradeBench 2025 | Khó self-host, ít phổ biến | ~5% |

**Kết quả benchmark thực tế (LiveTradeBench 2025 — 21 models, 50 ngày live):**
- Best performer: Kimi-K2, Qwen3-235B → ~2-3% return, ~11-14% drawdown
- GPT-5: double-digit losses
- **Kết luận quan trọng: LLM capability score ≠ trading profit**
- Reasoning models (o1, DeepSeek-R1) **KHÔNG có lợi thế** so với instruct models trong trading

**Cảnh báo hallucination cho leveraged trading:**
- Financial data hallucination: 2.1% (tốt nhất) đến 13.8% (trung bình)
- Price/numeric accuracy: >15% error khi phân tích statements
- **Với đòn bẩy 10-20x, 5-10% sai lầm về price level = rủi ro rất cao**
- → **Bắt buộc validate SL/TP từ LLM bằng ATR bounds trước khi đặt lệnh**

**Recommended defaults (dựa trên benchmark + cost):**
```
Swing     → claude-sonnet-4-6  (reliable JSON + best analysis quality, thấp hallucination)
Intraday  → deepseek-v3        (benchmark 14% crypto, rẻ 10x, OpenAI-compat)
Scalp     → gpt-4o-mini        (stable, JSON tốt, rẻ — Gemini Flash rủi ro hallucination cao hơn)
```

> **Insight từ research**: LLM nên đóng vai trò ANALYST (phân tích context, ra bias) chứ không phải TRADER (predict exact price). Kết hợp LLM + indicator pre-filter + ATR validation mới an toàn.

## Key Dependencies
- Phase 01 blocks all others
- Phase 02 blocks 03, 04, 05
- Phase 03 + 04 + 05 block 07
- Phase 06 + 07 block 08

## Research
- [researcher-01-binance-indicators.md](research/researcher-01-binance-indicators.md)
- [researcher-02-llm-telegram.md](research/researcher-02-llm-telegram.md)
