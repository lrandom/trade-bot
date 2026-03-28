# Phase 05 — Trading Modes

## Context
- Parent plan: [plan.md](plan.md)
- Depends on: [phase-02-data-layer.md](phase-02-data-layer.md), [phase-04-risk-management.md](phase-04-risk-management.md)
- Blocks: phase-07, phase-08

## Overview
- **Date:** 2026-03-27
- **Priority:** P1
- **Status:** pending
- Three trading modes (scalp / intraday / swing) with different timeframes, SL multipliers, leverage, models, and analysis frequencies.

## Key Insights
- Mode is a runtime config value stored in DB `config` table — changeable via `/mode` Telegram command without restart
- Each mode defines: active TFs for snapshot, primary TF for S/R + ATR, analysis interval, Claude model, leverage default
- Scalp uses WebSocket-driven analysis (candle close event); intraday/swing use APScheduler intervals
- Switching mode while a position is open: allow mode switch but keep existing trade at original parameters

## Requirements

**Functional:**
- `get_current_mode() -> str` from DB
- `set_mode(mode) -> None` updates DB
- `ModeConfig` per mode: TFs, primary TF, analysis interval, model, leverage, risk_pct, atr_multiplier
- Mode-aware snapshot builder: only fetch TFs relevant to mode

**Non-functional:**
- Mode switch is instant (next analysis cycle picks it up)
- No restart required on mode change

## Architecture

```
bot/modes/
├── __init__.py
├── config.py      # MODES dict — single source of truth
└── manager.py     # get_current_mode(), set_mode(), get_mode_config()
```

### Mode Definitions

```python
MODES = {
    "scalp": {
        "timeframes":       ["1m", "5m"],
        "primary_tf":       "5m",
        "analysis_trigger": "candle_close",   # WebSocket-driven
        "interval_minutes": None,
        "claude_model":     "claude-haiku-4-5",
        "leverage":         10,
        "max_leverage":     20,
        "risk_pct":         0.5,
        "atr_sl_mult":      1.0,
        "min_confidence":   65,
    },
    "intraday": {
        "timeframes":       ["15m", "1h"],
        "primary_tf":       "1h",
        "analysis_trigger": "interval",
        "interval_minutes": 15,
        "claude_model":     "claude-sonnet-4-6",
        "leverage":         5,
        "max_leverage":     10,
        "risk_pct":         1.0,
        "atr_sl_mult":      1.5,
        "min_confidence":   60,
    },
    "swing": {
        "timeframes":       ["4h", "1d"],
        "primary_tf":       "4h",
        "analysis_trigger": "interval",
        "interval_minutes": 240,              # every 4h
        "claude_model":     "claude-sonnet-4-6",
        "leverage":         3,
        "max_leverage":     5,
        "risk_pct":         1.5,
        "atr_sl_mult":      2.0,
        "min_confidence":   60,
    }
}
```

### Analysis Trigger Flow
```
scalp mode:
  WebSocket 1m candle close
      └─► on_candle_close() → if 5m candle also closed → run_analysis_cycle()

intraday mode:
  APScheduler every 15min
      └─► run_analysis_cycle()

swing mode:
  APScheduler every 4h
      └─► run_analysis_cycle()
```

### run_analysis_cycle (shared logic)
```python
async def run_analysis_cycle(mode: str):
    snapshot = await build_snapshot(mode)
    signal = await llm_engine.generate_signal(snapshot)
    if signal.action == 'HOLD':
        return
    approved, reason = await risk_engine.pre_trade_check(signal, mode, balance, db)
    if not approved:
        logger.info(f"Signal rejected: {reason}")
        return
    await save_signal_to_db(signal)
    if auto_trade:
        await execution_engine.place_order(signal, mode)
    else:
        await telegram_bot.send_signal_for_approval(signal)
```

## Related Code Files

| Action | File | Description |
|--------|------|-------------|
| create | `bot/modes/config.py` | MODES dict |
| create | `bot/modes/manager.py` | DB-backed mode get/set |

## Implementation Steps

1. **config.py** — `MODES` dict as defined above; `get_mode_config(mode: str) -> dict` helper with `KeyError` guard

2. **manager.py**:
   ```python
   async def get_current_mode(db) -> str:
       row = await db.execute_fetchone(
           "SELECT value FROM config WHERE key='mode'"
       )
       return row['value']  # default 'intraday' set in schema.sql

   async def set_mode(db, mode: str):
       if mode not in MODES:
           raise ValueError(f"Unknown mode: {mode}")
       await db.execute(
           "UPDATE config SET value=?, updated_at=CURRENT_TIMESTAMP WHERE key='mode'",
           (mode,)
       )
       await db.commit()
   ```

3. **Trigger registration** (in orchestrator, Phase 08):
   - On startup: read current mode from DB
   - If scalp: register WebSocket 5m candle close callback → `run_analysis_cycle`
   - If intraday: `scheduler.add_job(run_analysis_cycle, 'interval', minutes=15)`
   - If swing: `scheduler.add_job(run_analysis_cycle, 'interval', minutes=240)`
   - On `/mode` command: remove existing job, re-register for new mode

4. **build_snapshot adaptation**: pass `mode` to `build_snapshot()` → it reads `MODES[mode]['timeframes']` to determine which TFs to fetch

## Todo

- [ ] `config.py` MODES dict with all 3 modes
- [ ] `manager.py` get/set with DB
- [ ] Mode validation (reject unknown mode names)
- [ ] Document trigger flow in orchestrator (phase-08 task)

## Success Criteria
- `/mode scalp` updates DB and next analysis cycle uses scalp TFs
- `get_mode_config('intraday')['atr_sl_mult']` returns `1.5`
- `set_mode(db, 'unknown')` raises `ValueError`
- Scalp mode build_snapshot fetches only 1m and 5m candles

## Risk Assessment
| Risk | Impact | Mitigation |
|------|--------|------------|
| Mode switch during open trade | Medium | Allow switch, keep trade params immutable after entry |
| Scalp WebSocket lag > 1m candle duration | High | Monitor ws latency; fallback to 1m REST polling if ws delay > 10s |
| 4h swing job fires during low-liquidity hours | Low | Log but proceed; LLM will return HOLD if setup unclear |

## Security Considerations
- Mode switch via Telegram only allowed from authorized `TELEGRAM_CHAT_ID`
- No external API exposes mode change endpoint

## Next Steps
- Phase 07: execution engine reads `mode` from DB for leverage/SL params at order time
- Phase 08: orchestrator reads mode on startup to register correct scheduler triggers
