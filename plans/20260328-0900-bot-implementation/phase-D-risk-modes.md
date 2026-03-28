# Phase D — Risk Engine + Trading Modes

## Context
- Parent plan: [plan.md](plan.md)
- Design specs: `plans/20260327-1200-gold-trading-bot/phase-04-risk-management.md`, `phase-05-trading-modes.md`
- Depends on: [phase-A-foundation.md](phase-A-foundation.md), [phase-B-data-layer.md](phase-B-data-layer.md)
- Blocks: Phase E (Execution)

## Overview
- **Date:** 2026-03-28
- **Priority:** P0
- **Status:** pending
- Fixed fractional position sizing, ATR-based SL validation, daily drawdown circuit breaker. Three trading modes (scalp/intraday/swing) with DB-backed runtime config.

## Key Insights
- Fixed fractional (1-2% risk/trade) safer than Kelly Criterion for algorithmic live bots
- Circuit breaker stored in DB `config` table — survives crashes/restarts
- LLM-generated SL must be validated before any order — hallucination risk is real
- Mode is a runtime config in DB — changeable via Telegram without restart
- Max 1 concurrent position per mode (gold is correlated with itself)
- `balance` fetched live from Binance before each pre-trade check — never stale

## Requirements

**Functional (Risk):**
- `calc_position_size(balance, risk_pct, entry, stop_loss, leverage) -> float`
- `validate_signal_sl(entry, stop_loss, atr, atr_multiplier, tolerance=0.5) -> bool`
- `check_circuit_breaker(db) -> bool`
- `check_and_trip(db, balance) -> bool`
- `update_daily_pnl(db, pnl_delta)`
- `reset_daily_pnl(db)` — called at 00:01 UTC daily
- `RiskEngine.pre_trade_check(signal, mode, balance, db) -> (bool, str)`

**Functional (Modes):**
- `MODES` dict: single source of truth for all mode configs
- `get_current_mode(db) -> str`
- `set_mode(db, mode)`
- `get_mode_config(mode) -> dict`

**Non-functional:**
- All DB ops async
- Circuit breaker reset: scheduled job only (no Telegram command — safety)
- Mode switch: instant, no restart

## Architecture

```
bot/risk/
├── __init__.py
├── calculator.py      # calc_position_size, validate_signal_sl, RiskEngine
├── circuit_breaker.py # check_circuit_breaker, check_and_trip, update/reset daily_pnl
└── limits.py          # MODE_CONFIG dict, getters

bot/modes/
├── __init__.py
├── config.py          # MODES dict (single source of truth)
└── manager.py         # get_current_mode, set_mode (DB-backed)
```

### Mode Config
```python
MODES = {
    "scalp": {
        "timeframes":       ["1m", "5m"],
        "primary_tf":       "5m",
        "analysis_trigger": "candle_close",   # WebSocket-driven
        "interval_minutes": None,
        "llm_provider":     "openai",         # default, overridden by env
        "llm_model":        "gpt-4o-mini",
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
        "llm_provider":     "deepseek",
        "llm_model":        "deepseek-chat",
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
        "interval_minutes": 240,
        "llm_provider":     "anthropic",
        "llm_model":        "claude-sonnet-4-6",
        "leverage":         3,
        "max_leverage":     5,
        "risk_pct":         1.5,
        "atr_sl_mult":      2.0,
        "min_confidence":   60,
    }
}
```

### Risk Parameters (also in MODES for reference)
```python
MODE_RISK = {  # kept in risk/limits.py
    "scalp":    {"leverage": 10, "risk_pct": 0.5,  "atr_sl_multiplier": 1.0, "max_positions": 1},
    "intraday": {"leverage": 5,  "risk_pct": 1.0,  "atr_sl_multiplier": 1.5, "max_positions": 1},
    "swing":    {"leverage": 3,  "risk_pct": 1.5,  "atr_sl_multiplier": 2.0, "max_positions": 1},
}
DAILY_LOSS_LIMIT_PCT = 5.0
MIN_CONFIDENCE = 60
```

## Related Code Files

| Action | File | Description |
|--------|------|-------------|
| create | `bot/risk/calculator.py` | Position sizing + SL validation + RiskEngine |
| create | `bot/risk/circuit_breaker.py` | DB-backed daily PnL + circuit breaker |
| create | `bot/risk/limits.py` | MODE_RISK dict + getter |
| create | `bot/modes/config.py` | MODES dict — single source of truth |
| create | `bot/modes/manager.py` | `get_current_mode`, `set_mode` |

## Implementation Steps

1. **`bot/modes/config.py`** — `MODES` dict as above. `get_mode_config(mode: str) -> dict` with `KeyError` guard raising `ValueError(f"Unknown mode: {mode}")`.

2. **`bot/modes/manager.py`**:
   ```python
   async def get_current_mode(db) -> str:
       row = await db.execute_fetchone("SELECT value FROM config WHERE key='mode'")
       return row['value']  # default 'intraday' set in schema.sql

   async def set_mode(db, mode: str):
       if mode not in MODES:
           raise ValueError(f"Unknown mode: {mode}")
       await db.execute(
           "UPDATE config SET value=?, updated_at=CURRENT_TIMESTAMP WHERE key='mode'", (mode,)
       )
       await db.commit()
   ```

3. **`bot/risk/limits.py`** — `MODE_RISK` dict + `get_risk_config(mode) -> dict` accessor

4. **`bot/risk/calculator.py`**:
   ```python
   def calc_position_size(balance, risk_pct, entry, stop_loss, leverage) -> float:
       risk_amount = balance * (risk_pct / 100)
       sl_distance = abs(entry - stop_loss)
       if sl_distance == 0:
           return 0.0
       position_value = risk_amount / (sl_distance / entry)
       max_position_value = balance * leverage
       position_value = min(position_value, max_position_value)
       quantity = position_value / entry
       return round(quantity, 3)  # XAUUSDT precision: 3 decimals

   def validate_signal_sl(entry, stop_loss, atr, atr_multiplier, tolerance=0.5) -> bool:
       expected = atr * atr_multiplier
       actual   = abs(entry - stop_loss)
       return expected * (1 - tolerance) <= actual <= expected * (1 + tolerance)
   ```

5. **`bot/risk/circuit_breaker.py`**:
   - `async def is_circuit_breaker_active(db) -> bool`: read `config WHERE key='circuit_breaker'`
   - `async def check_and_trip(db, balance) -> bool`: read `daily_pnl`; if loss > 5%, set `circuit_breaker=true`, return True
   - `async def update_daily_pnl(db, pnl_delta)`: SQL `CAST(CAST(value AS REAL) + delta AS TEXT)` update
   - `async def reset_daily_pnl(db)`: set both `daily_pnl='0'` and `circuit_breaker='false'` at UTC midnight

6. **`bot/risk/calculator.py`** — `RiskEngine` class combining all checks:
   ```python
   class RiskEngine:
       async def pre_trade_check(self, signal, mode, balance, db) -> tuple[bool, str]:
           config = get_risk_config(mode)

           if await check_and_trip(db, balance):
               return False, "Circuit breaker active"

           open_count = await self._get_open_position_count(db)
           if open_count >= config['max_positions']:
               return False, f"Max positions reached ({config['max_positions']})"

           if not validate_signal_sl(signal.entry_price, signal.stop_loss,
                                     signal.atr, config['atr_sl_multiplier']):
               return False, "SL distance out of ATR range"

           if signal.confidence < config.get('min_confidence', MIN_CONFIDENCE):
               return False, f"Confidence {signal.confidence} below threshold"

           return True, "OK"

       async def _get_open_position_count(self, db) -> int:
           row = await db.execute_fetchone("SELECT COUNT(*) as cnt FROM trades WHERE status='open'")
           return row['cnt']
   ```

7. **Unit tests** (`tests/test_risk.py`):
   - `calc_position_size(10000, 1.0, 3300, 3280, 5)` — verify math manually
   - Circuit breaker trips at daily_pnl < -500 on $10K
   - `validate_signal_sl` rejects SL 3x ATR away

## Todo

- [ ] `modes/config.py` MODES dict
- [ ] `modes/manager.py` get/set with DB + validation
- [ ] `risk/limits.py` MODE_RISK + getters
- [ ] `risk/calculator.py` calc_position_size
- [ ] `risk/calculator.py` validate_signal_sl
- [ ] `risk/circuit_breaker.py` check_and_trip
- [ ] `risk/circuit_breaker.py` update_daily_pnl + reset_daily_pnl
- [ ] `risk/calculator.py` RiskEngine.pre_trade_check
- [ ] Unit tests for position sizing math
- [ ] Unit test circuit breaker trips at 5%

## Success Criteria
- `calc_position_size(10000, 1.0, 3300, 3280, 5)` returns sane quantity (verify manually)
- Circuit breaker trips at daily PnL < -500 on $10K balance
- Circuit breaker state persists after `init_db()` (DB-backed)
- Signal with SL at 3x ATR rejected by `validate_signal_sl`
- `set_mode(db, 'unknown')` raises `ValueError`

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| LLM hallucinates SL at 0 or at entry | Critical | `validate_signal_sl` rejects; `sl_distance==0` guard |
| Circuit breaker not persisting on crash | High | DB-backed, not in-memory |
| Wrong contract size for XAUUSDT | High | Verify Binance contract spec; add assertion in tests |
| Stale balance used for sizing | High | Fetch live from Binance API before each `pre_trade_check` |

## Security Considerations
- Circuit breaker reset via scheduler only — no Telegram command to reset (safety)
- `balance` always fetched live, never cached for risk calculations
- Mode switch from Telegram requires `TELEGRAM_CHAT_ID` auth (Phase F)

## Next Steps
- Phase E: `RiskEngine.pre_trade_check()` called before any order
- Phase G: `reset_daily_pnl()` registered as APScheduler cron at 00:01 UTC
- Phase G: orchestrator reads mode on startup to register correct scheduler trigger
