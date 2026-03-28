# Phase 04 — Risk Management

## Context
- Parent plan: [plan.md](plan.md)
- Depends on: [phase-01-project-setup.md](phase-01-project-setup.md), [phase-02-data-layer.md](phase-02-data-layer.md)
- Research: [researcher-02-llm-telegram.md](research/researcher-02-llm-telegram.md)
- Blocks: phase-07 (execution)

## Overview
- **Date:** 2026-03-27
- **Priority:** P0
- **Status:** pending
- Fixed fractional position sizing, ATR-based SL validation, daily drawdown circuit breaker, leverage limits, max concurrent position guard.

## Key Insights
- Fixed fractional (1-2% risk/trade) is safer than Kelly Criterion for live algorithmic bots
- ATR-based SL aligns risk with current volatility — prevents wide SL in calm markets
- Circuit breaker (5% daily loss) must persist in DB `config` table so it survives restarts
- Max 1 concurrent position per mode to avoid correlation risk on gold
- LLM-generated SL must be **validated** by risk engine before order placement — LLM can hallucinate levels

## Requirements

**Functional:**
- `calc_position_size(balance, risk_pct, entry, stop_loss, leverage) -> float` (quantity in contracts)
- `validate_signal(signal, atr, mark_price) -> bool` — reject if SL is unreasonably far/close
- `check_circuit_breaker() -> bool` — reads DB `daily_pnl`, blocks trading if loss > 5%
- `update_daily_pnl(pnl_delta)` — updates DB after each trade close
- `reset_daily_pnl()` — called at 00:00 UTC daily
- `get_leverage_for_mode(mode) -> int` — returns default leverage per mode
- `get_risk_pct_for_mode(mode) -> float` — returns risk% per mode

**Non-functional:**
- All DB operations async (aiosqlite)
- Circuit breaker state survives restarts (DB-backed)
- Position size capped by max leverage position size

## Architecture

```
bot/risk/
├── __init__.py
├── calculator.py      # Position sizing, SL validation
├── circuit_breaker.py # Daily PnL tracking, breaker logic
└── limits.py          # Mode-specific leverage, risk%, max positions
```

### Mode Risk Parameters
```python
MODE_CONFIG = {
    "scalp": {
        "leverage": 10,          # default (range 10-20x)
        "risk_pct": 0.5,         # 0.5% per trade
        "atr_sl_multiplier": 1.0,
        "max_positions": 1
    },
    "intraday": {
        "leverage": 5,           # default (range 5-10x)
        "risk_pct": 1.0,         # 1% per trade
        "atr_sl_multiplier": 1.5,
        "max_positions": 1
    },
    "swing": {
        "leverage": 3,           # default (range 3-5x)
        "risk_pct": 1.5,         # 1.5% per trade
        "atr_sl_multiplier": 2.0,
        "max_positions": 1
    }
}
```

## Related Code Files

| Action | File | Description |
|--------|------|-------------|
| create | `bot/risk/calculator.py` | Sizing + SL validation |
| create | `bot/risk/circuit_breaker.py` | Daily PnL + breaker |
| create | `bot/risk/limits.py` | MODE_CONFIG + getters |

## Implementation Steps

1. **limits.py** — `MODE_CONFIG` dict as above; `get_mode_config(mode: str) -> dict` accessor

2. **calculator.py**:

   ```python
   def calc_position_size(
       balance: float,
       risk_pct: float,
       entry: float,
       stop_loss: float,
       leverage: int
   ) -> float:
       """Returns quantity (contracts/lots) to buy."""
       risk_amount = balance * (risk_pct / 100)
       sl_distance = abs(entry - stop_loss)
       if sl_distance == 0:
           return 0.0
       # Risk amount divided by SL distance = position value
       position_value = risk_amount / (sl_distance / entry)
       # Cap by max leveraged position
       max_position_value = balance * leverage
       position_value = min(position_value, max_position_value)
       # Convert to contracts (XAUUSDT: 1 contract = 1 oz equivalent on Binance)
       quantity = position_value / entry
       return round(quantity, 3)

   def validate_signal_sl(
       entry: float,
       stop_loss: float,
       atr: float,
       atr_multiplier: float,
       tolerance: float = 0.5
   ) -> bool:
       """
       Reject if LLM SL is > 2x expected ATR distance or < 0.3x.
       Prevents hallucinated SL levels from reaching the exchange.
       """
       expected_sl_dist = atr * atr_multiplier
       actual_sl_dist = abs(entry - stop_loss)
       low_bound = expected_sl_dist * (1 - tolerance)
       high_bound = expected_sl_dist * (1 + tolerance)
       return low_bound <= actual_sl_dist <= high_bound
   ```

3. **circuit_breaker.py**:

   ```python
   DAILY_LOSS_LIMIT_PCT = 5.0

   async def is_circuit_breaker_active(db) -> bool:
       """Read 'circuit_breaker' from config table."""
       row = await db.execute_fetchone(
           "SELECT value FROM config WHERE key='circuit_breaker'"
       )
       return row['value'] == 'true'

   async def check_and_trip(db, balance: float) -> bool:
       """
       Reads daily_pnl. If loss > 5%, sets circuit_breaker=true.
       Returns True if breaker was tripped or already active.
       """
       if await is_circuit_breaker_active(db):
           return True
       row = await db.execute_fetchone(
           "SELECT value FROM config WHERE key='daily_pnl'"
       )
       daily_pnl = float(row['value'])
       if daily_pnl < -(balance * DAILY_LOSS_LIMIT_PCT / 100):
           await db.execute(
               "UPDATE config SET value='true', updated_at=CURRENT_TIMESTAMP "
               "WHERE key='circuit_breaker'"
           )
           await db.commit()
           return True
       return False

   async def update_daily_pnl(db, pnl_delta: float):
       await db.execute(
           "UPDATE config SET value=CAST(CAST(value AS REAL) + ? AS TEXT), "
           "updated_at=CURRENT_TIMESTAMP WHERE key='daily_pnl'",
           (pnl_delta,)
       )
       await db.commit()

   async def reset_daily_pnl(db):
       """Call at UTC midnight."""
       await db.execute(
           "UPDATE config SET value='0', updated_at=CURRENT_TIMESTAMP "
           "WHERE key IN ('daily_pnl', 'circuit_breaker')"
       )
       await db.commit()
   ```

4. **RiskEngine integration** — create `RiskEngine` class in `calculator.py` combining all checks:
   ```python
   class RiskEngine:
       async def pre_trade_check(self, signal, mode, balance, db) -> Tuple[bool, str]:
           """Returns (approved, reason)."""
           config = get_mode_config(mode)
           # 1. Circuit breaker
           if await check_and_trip(db, balance):
               return False, "Circuit breaker active"
           # 2. Max concurrent positions
           open_count = await get_open_position_count(db)
           if open_count >= config['max_positions']:
               return False, f"Max positions ({config['max_positions']}) reached"
           # 3. SL validation
           atr = signal.atr  # passed in from snapshot
           if not validate_signal_sl(signal.entry_price, signal.stop_loss,
                                     atr, config['atr_sl_multiplier']):
               return False, "SL distance out of ATR range"
           # 4. Minimum confidence threshold
           if signal.confidence < 60:
               return False, f"Confidence {signal.confidence} below threshold 60"
           return True, "OK"
   ```

5. **Drawdown reset scheduler** — add APScheduler cron job at `00:01 UTC` daily calling `reset_daily_pnl()`

## Todo

- [ ] `limits.py` MODE_CONFIG + getters
- [ ] `calculator.py` calc_position_size
- [ ] `calculator.py` validate_signal_sl
- [ ] `circuit_breaker.py` check_and_trip
- [ ] `circuit_breaker.py` update_daily_pnl + reset_daily_pnl
- [ ] `RiskEngine.pre_trade_check` integration method
- [ ] Unit tests for position sizing math
- [ ] Unit test circuit breaker trips at 5%

## Success Criteria
- `calc_position_size(10000, 1.0, 3300, 3280, 5)` returns sane quantity (verify math)
- Circuit breaker trips when daily PnL < -500 on $10K balance
- Circuit breaker survives restart (DB-backed)
- Signal with SL 3x ATR away is rejected by `validate_signal_sl`

## Risk Assessment
| Risk | Impact | Mitigation |
|------|--------|------------|
| LLM hallucinates SL at 0 or at entry | Critical | validate_signal_sl rejects it |
| Circuit breaker not persisting on crash | High | DB-backed, not in-memory |
| Incorrect contract size for XAUUSDT | High | Verify Binance XAUUSDT contract spec; add assert in tests |
| Over-leveraged position on high volatility | High | Max leverage hard cap per mode in MODE_CONFIG |

## Security Considerations
- `balance` fetched live from Binance API before each pre_trade_check — never use stale cached balance for sizing
- Circuit breaker reset requires manual DB update or scheduled job — no Telegram command to reset it (safety)

## Next Steps
- Phase 07: `RiskEngine.pre_trade_check()` called before any order placement
- Phase 08: `reset_daily_pnl` registered as APScheduler cron job
