# Implementation Plan — Gold Trading Bot
**Date:** 2026-03-28 | **Status:** Ready to implement

## Context
Design specs: `plans/20260327-1200-gold-trading-bot/`
Output dir: `/Users/luan_prep_vn/Desktop/v-matrix/trade-gold/`

## Stack
python-binance · pandas-ta · anthropic/openai/google-generativeai · python-telegram-bot v20 · APScheduler · aiosqlite · loguru · python-dotenv

## Safety Rule
`PAPER_TRADE=true` is default. Never set false without 2-week paper validation.

## Phase Table

| Phase | Name | Group | Priority | Depends On | File |
|-------|------|-------|----------|------------|------|
| A | Foundation | MVP | P0 | — | [phase-A-foundation.md](phase-A-foundation.md) |
| B | Data Layer | MVP | P0 | A | [phase-B-data-layer.md](phase-B-data-layer.md) |
| C | LLM Engine | MVP | P0 | A, B | [phase-C-llm-engine.md](phase-C-llm-engine.md) |
| D | Risk + Modes | MVP | P0 | A, B | [phase-D-risk-modes.md](phase-D-risk-modes.md) |
| E | Execution | MVP | P0 | C, D | [phase-E-execution.md](phase-E-execution.md) |
| F | Telegram | MVP | P1 | A | [phase-F-telegram.md](phase-F-telegram.md) |
| G | Orchestration | MVP | P0 | E, F | [phase-G-orchestration.md](phase-G-orchestration.md) |
| H | Paper Trade | Enhancement | P0 | E | [phase-H-paper-trade.md](phase-H-paper-trade.md) |
| I | Cost Tracking | Enhancement | P1 | C, E | [phase-I-cost-tracking.md](phase-I-cost-tracking.md) |
| J | Health Monitor | Enhancement | P0 | F, G | [phase-J-health-monitor.md](phase-J-health-monitor.md) |
| K | Filters | Enhancement | P0 | B, C | [phase-K-filters.md](phase-K-filters.md) |

## MVP Sequence
A → B → C+D (parallel) → E → F → G

## Key Dependencies
- Phase A: blocks everything
- Phase B: blocks C, D
- Phase C + D: block E
- Phase E + F: block G
- Phase G: blocks H, I, J, K
