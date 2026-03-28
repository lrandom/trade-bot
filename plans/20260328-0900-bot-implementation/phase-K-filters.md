# Phase K — Volatility & Correlation Filters

## Context
- Parent plan: [plan.md](plan.md)
- Design spec: `plans/20260327-1200-gold-trading-bot/phase-12-volatility-filter.md`
- Depends on: [phase-B-data-layer.md](phase-B-data-layer.md), [phase-C-llm-engine.md](phase-C-llm-engine.md), [phase-G-orchestration.md](phase-G-orchestration.md)
- Runs as pre-gate before LLM chain — saves LLM tokens on obvious blocks

## Overview
- **Date:** 2026-03-28
- **Priority:** P0 (required before live)
- **Status:** pending
- Two-gate filter chain: (1) ATR spike guard — detects abnormal volatility (news events), (2) Correlation check — DXY proxy (EURUSDT) + Silver (XAGUSD) alignment. Plus seasonal/weekday cycle context injected into LLM macro prompt.

## Key Insights
- Filters run BEFORE LLM chain — early block saves ~$0.05/cycle in token costs
- DXY proxy: use EURUSDT 4H (Binance has it) — inverse correlation ~0.95 with DXY
- Silver (XAGUSD): Binance Futures may not have it — use Binance Spot `XAGUSDT` or skip gracefully
- Correlation check adjusts signal confidence (not hard block) unless 2+ conflicts AND confidence < 40
- ATR spike: scalp most sensitive (2x threshold), swing least sensitive (3x)
- Cycle context (seasonal + weekday) is data enrichment — injected into macro prompt text
- `FilterChain` runs both gates sequentially; returns `FilterResult` to orchestrator

## Requirements

**Functional:**
- `VolatilityFilter.check_atr_spike(mode, tf='1h') -> FilterResult`
- `CorrelationFilter.check_correlation(signal) -> FilterResult`
- `CycleContext.get_cycle_context() -> dict`
- `FilterChain.run(mode, signal=None) -> FilterResult` — combines both gates
- ATR spike: pause auto_trade + Telegram alert; signal mode still works (with warning)
- Correlation: adjust `signal.confidence` up/down; block if ≥2 conflicts + confidence < 40
- `/filter status` — current filter state
- `/filter log` — last 10 filter blocks + reasons
- `/filter override` — skip filters for next signal (1-time flag)
- Log all filter decisions to `filter_log` table

**Non-functional:**
- Each filter has 5s timeout; on timeout, pass (fail-open for correlation, fail-closed for ATR)
- Silver/EURUSD fetch failure: skip that check, continue with remaining

## Architecture

```
bot/filters/
├── __init__.py            # FilterChain class
├── volatility_filter.py   # ATR spike guard
├── correlation_filter.py  # DXY proxy + Silver alignment
└── cycle_context.py       # Seasonal + weekday context

bot/telegram/handlers/
└── filter_handler.py      # /filter subcommands
```

### FilterResult
```python
@dataclass
class FilterResult:
    passed: bool
    reason: str = ""
    action: str = ""         # PAUSE_AUTO_TRADE | WARN | BLOCK
    spike_ratio: float = 0.0
    dxy_status: str = ""     # DXY_WEAK | DXY_STRONG | DXY_NEUTRAL
    silver_aligned: bool = True
    confidence_adj: int = 0  # adjustment to signal confidence
    adjusted_confidence: int = 0
    original_confidence: int = 0
    conflicts: list[str] = field(default_factory=list)
```

## Related Code Files

| Action | File | Description |
|--------|------|-------------|
| create | `bot/filters/__init__.py` | `FilterChain` — runs both gates |
| create | `bot/filters/volatility_filter.py` | ATR spike check |
| create | `bot/filters/correlation_filter.py` | DXY proxy + Silver check |
| create | `bot/filters/cycle_context.py` | Seasonal + weekday context |
| create | `bot/telegram/handlers/filter_handler.py` | `/filter` subcommands |
| modify | `bot/orchestrator.py` | Call FilterChain in `run_analysis_cycle()` |
| modify | `bot/llm/prompts.py` | Inject correlation + cycle into macro prompt |
| modify | `bot/telegram/bot.py` | Register `/filter` |
| modify | `schema.sql` | Add `filter_log` table |

## Implementation Steps

1. **Schema addition** to `schema.sql`:
   ```sql
   CREATE TABLE IF NOT EXISTS filter_log (
       id TEXT PRIMARY KEY,
       timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
       signal_id TEXT,
       filter_type TEXT,    -- atr_spike | correlation | cycle
       passed BOOLEAN,
       reason TEXT,
       spike_ratio REAL,
       dxy_status TEXT,
       silver_aligned BOOLEAN,
       confidence_adj INTEGER,
       original_conf INTEGER,
       adjusted_conf INTEGER
   );
   ```

2. **`volatility_filter.py`** — `VolatilityFilter`:
   ```python
   ATR_SPIKE_MULTIPLIERS = {"scalp": 2.0, "intraday": 2.5, "swing": 3.0}

   async def check_atr_spike(self, mode: str, tf: str = "1h") -> FilterResult:
       df = await fetch_ohlcv("XAUUSDT", tf, limit=25)
       df = compute_indicators(df)
       current_atr = df["atr_14"].iloc[-1]
       avg_atr_20  = df["atr_14"].iloc[-21:-1].mean()
       spike_ratio = current_atr / avg_atr_20 if avg_atr_20 > 0 else 1.0
       threshold   = ATR_SPIKE_MULTIPLIERS[mode]

       if spike_ratio >= threshold:
           return FilterResult(
               passed=False,
               reason=f"ATR spike {spike_ratio:.1f}x (threshold {threshold}x)",
               action="PAUSE_AUTO_TRADE",
               spike_ratio=spike_ratio,
           )
       return FilterResult(passed=True, spike_ratio=spike_ratio)
   ```
   Note: if `volume < 50% avg_volume` (thin market), skip ATR check (return passed=True) — prevents false positive on weekends.

3. **`correlation_filter.py`** — `CorrelationFilter`:
   - `get_dxy_proxy_trend()`: fetch EURUSDT 4H (50 bars); EMA20 + EMA50; if price > EMA20 > EMA50 → `DXY_WEAK`; if price < EMA20 < EMA50 → `DXY_STRONG`; else `DXY_NEUTRAL`
   - `get_silver_alignment(signal_action)`: fetch XAGUSDT 1H (20 bars) from Binance Spot; EMA20; compare trend vs signal direction; return `CorrelationResult` with `confidence_adj=+10` if aligned, `-15` if not
   - `check_correlation(signal)`: combine both; compute `new_confidence`; block if 2+ conflicts AND `new_confidence < 40`; otherwise return passed with adjusted confidence

4. **`cycle_context.py`** — `get_cycle_context() -> dict`:
   ```python
   SEASONAL = {
       1: ("STRONG",  "China NY + India wedding demand"),
       3: ("NEUTRAL", "Post-CNY normalization"),
       6: ("WEAK",    "Summer doldrums"),
       9: ("STRONG",  "India festive season (Navratri)"),
       10: ("STRONG", "Diwali + year-end positioning"),
       # ... all 12 months
   }
   DOW_NOTES = {
       0: "Monday — continuation from last week",
       2: "Wednesday — FOMC/CPI risk, high volatility",
       4: "Friday — position squaring, beware false breakouts",
       # ...
   }
   ```
   Returns: `{month, seasonal_bias, seasonal_note, day_of_week, session, is_high_vol_day}`
   Uses `to_ict(utc_now())` for local time context.

5. **`FilterChain`** (`__init__.py`):
   ```python
   class FilterChain:
       def __init__(self, db, settings):
           self.vol_filter  = VolatilityFilter()
           self.corr_filter = CorrelationFilter()
           self._db = db
           self._settings = settings
           self._override_next = False  # set by /filter override

       async def run(self, mode: str, signal=None) -> FilterResult:
           # Check /filter override flag
           if self._override_next:
               self._override_next = False
               return FilterResult(passed=True, reason="Manual override")

           # Gate 1: ATR spike
           atr_result = await self.vol_filter.check_atr_spike(mode)
           await self._log_filter(signal, 'atr_spike', atr_result)

           if not atr_result.passed:
               return atr_result

           # Gate 2: Correlation (only if signal provided)
           if signal is not None:
               corr_result = await self.corr_filter.check_correlation(signal)
               await self._log_filter(signal, 'correlation', corr_result)
               if not corr_result.passed:
                   return corr_result
               # Apply confidence adjustment to signal
               signal.confidence = corr_result.adjusted_confidence

           return FilterResult(passed=True)
   ```

6. **Integration in `run_analysis_cycle()`** (Phase G orchestrator):
   ```python
   async def run_analysis_cycle(mode, ...):
       snapshot = await build_snapshot(mode)

       # Run filters before LLM
       pre_filter = await filter_chain.run(mode)
       if not pre_filter.passed:
           logger.info(f"Pre-filter block: {pre_filter.reason}")
           if pre_filter.action == "PAUSE_AUTO_TRADE":
               await send_notification(bot, chat_id, f"ATR spike — auto trade paused")
               await db.execute("UPDATE config SET value='false' WHERE key='auto_trade'")
           return

       # Inject cycle context into snapshot for LLM prompts
       cycle = get_cycle_context()
       snapshot.cycle_context = cycle

       signal = await llm.generate_signal(snapshot)
       ...

       # Post-signal correlation check (with signal direction known)
       post_filter = await filter_chain.run(mode, signal=signal)
       if not post_filter.passed:
           return
   ```

7. **Correlation + cycle injected into macro prompt** (`prompts.py`):
   ```
   ## Cycle Context
   Month: {month} → Seasonal: {seasonal_bias} ({seasonal_note})
   Day: {day_of_week}
   Session: {session}

   ## Market Correlations
   DXY Proxy (EURUSD 4H): {dxy_status}
   Silver (1H): {silver_trend} — aligned: {aligned}
   Conflicts: {conflicts or "None"}
   ```

8. **`filter_handler.py`** — `/filter SUBCOMMAND`:
   - `status`: run both filters, format current state
   - `log`: query last 10 `filter_log` blocks
   - `override`: set `filter_chain._override_next = True`; reply "Filter override set for next signal"

## Todo

- [ ] Schema: `filter_log` table
- [ ] `filters/volatility_filter.py` — ATR spike check + volume guard
- [ ] `filters/correlation_filter.py` — DXY proxy + Silver
- [ ] `filters/cycle_context.py` — seasonal + DOW table
- [ ] `filters/__init__.py` FilterChain — 2-gate chain + override flag
- [ ] Inject FilterChain into `run_analysis_cycle()` (Phase G)
- [ ] Inject cycle context into MarketSnapshot + macro prompt
- [ ] `filter_handler.py` — status/log/override subcommands
- [ ] Register `/filter` in `bot.py`
- [ ] Unit test: spike detection with mock ATR data
- [ ] Handle EURUSDT/XAGUSDT API failure gracefully (skip check)

## Success Criteria
- ATR 2.5x normal in intraday → auto_trade paused within 1 cycle (15min)
- Correlation conflict → `signal.confidence` adjusted correctly
- 2+ conflicts + confidence < 40 → signal blocked
- All blocks logged to `filter_log`
- `/filter status` responds in 2s
- EURUSD fetch failure → skip DXY check, continue normally

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| EURUSDT not on Binance Futures | Medium | Use Binance Spot endpoint; log warning if unavailable |
| XAGUSDT not on Binance Spot | Medium | Skip Silver check gracefully; log |
| Filter too sensitive → miss good signals | Medium | Log all blocks → review after 1 week; tune threshold |
| ATR spike on thin market (weekend) | Low | Volume check: skip ATR if volume < 50% avg |

## Security Considerations
- `/filter override` is a safety bypass — authorized user only
- Override is one-time flag (reset after use) — not persistent

## Next Steps
- After 2 weeks: review `filter_log` → calculate % of blocked signals that would have been losses
- If filter accuracy > 70%: keep threshold; if < 50%: loosen
- Consider adding economic calendar integration for known news event blocks
