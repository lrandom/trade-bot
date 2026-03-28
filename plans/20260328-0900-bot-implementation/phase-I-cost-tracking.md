# Phase I — Cost Tracking

## Context
- Parent plan: [plan.md](plan.md)
- Design spec: `plans/20260327-1200-gold-trading-bot/phase-10-cost-tracking.md`
- Depends on: [phase-C-llm-engine.md](phase-C-llm-engine.md), [phase-E-execution.md](phase-E-execution.md), [phase-G-orchestration.md](phase-G-orchestration.md)

## Overview
- **Date:** 2026-03-28
- **Priority:** P1
- **Status:** pending
- Track LLM API cost, trading fees, infra cost. `/cost` Telegram commands with today/MTD/custom date range. Net profit = PnL - all costs.

## Key Insights
- LLM pricing hardcoded in `pricing.py` — update manually when providers change prices
- Token counts already logged by LLM engine (Phase C) — just need to pass to CostTracker
- Trading fees from Binance fill response (`fills[0].commission`) — paper trade skips fees
- Infra cost entered manually via `/cost set vps 10` — stored in DB
- All cost logging async and non-blocking — must NOT slow trading pipeline
- Inject `CostTracker` into `LLMEngine` and `RealTrader` — pass by reference in orchestrator

## Requirements

**Functional:**
- `log_llm_call(provider, model, call_type, mode, in_tokens, out_tokens, signal_id)`
- `log_trading_fee(trade_id, order_type, fee_amount, fee_asset)`
- `set_infra_cost(category, amount_per_month)`
- `get_daily_summary(date) -> dict`
- `get_mtd_summary() -> dict`
- `get_range_summary(date_from, date_to) -> dict`
- `export_csv(date_from, date_to) -> path`
- `/cost today|mtd|from D1 [to D2]|llm|set CAT AMT|export`

**Non-functional:**
- Log LLM cost after every API call (non-blocking)
- Alert when daily LLM cost exceeds `LLM_DAILY_BUDGET_USD`

## Architecture

```
bot/cost/
├── __init__.py
├── pricing.py     # LLM_PRICING table + calc_llm_cost()
└── tracker.py     # CostTracker class

bot/telegram/handlers/
└── cost_handler.py  # /cost command parser
```

### LLM Pricing Table
```python
LLM_PRICING = {
    "anthropic": {
        "claude-sonnet-4-6":  {"input": 3.00,  "output": 15.00},
        "claude-haiku-4-5":   {"input": 0.80,  "output": 4.00},
    },
    "openai": {
        "gpt-4o":       {"input": 2.50, "output": 10.00},
        "gpt-4o-mini":  {"input": 0.15, "output": 0.60},
    },
    "gemini": {
        "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
    },
    "deepseek": {
        "deepseek-chat":     {"input": 0.27, "output": 1.10},
        "deepseek-reasoner": {"input": 0.55, "output": 2.19},
    },
}

def calc_llm_cost(provider, model, input_tokens, output_tokens) -> float:
    pricing = LLM_PRICING.get(provider, {}).get(model)
    if not pricing:
        return 0.0
    return (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000
```

## Related Code Files

| Action | File | Description |
|--------|------|-------------|
| create | `bot/cost/pricing.py` | LLM price table + `calc_llm_cost()` |
| create | `bot/cost/tracker.py` | `CostTracker` class |
| create | `bot/telegram/handlers/cost_handler.py` | `/cost` command parser |
| modify | `bot/llm/engine.py` | Inject CostTracker, call `log_llm_call` after each provider call |
| modify | `bot/trader/real_trader.py` | Call `log_trading_fee` after each order fill |
| modify | `bot/telegram/bot.py` | Register `/cost` command |
| modify | `schema.sql` | Add 4 new tables |

## Implementation Steps

1. **Schema additions** to `schema.sql`:
   ```sql
   CREATE TABLE IF NOT EXISTS llm_usage (
       id TEXT PRIMARY KEY,
       timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
       provider TEXT, model TEXT, call_type TEXT, mode TEXT,
       input_tokens INTEGER, output_tokens INTEGER,
       cost_usd REAL, signal_id TEXT
   );
   CREATE TABLE IF NOT EXISTS trading_fees (
       id TEXT PRIMARY KEY,
       timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
       trade_id TEXT, order_type TEXT,
       fee_asset TEXT, fee_amount REAL, fee_usd REAL
   );
   CREATE TABLE IF NOT EXISTS infra_costs (
       id TEXT PRIMARY KEY,
       month TEXT,          -- YYYY-MM
       category TEXT,       -- vps | domain | other
       description TEXT, cost_usd REAL
   );
   CREATE TABLE IF NOT EXISTS cost_summary (
       date DATE PRIMARY KEY,
       llm_cost_usd REAL DEFAULT 0,
       trading_fee_usd REAL DEFAULT 0,
       infra_cost_usd REAL DEFAULT 0,
       total_cost_usd REAL DEFAULT 0,
       trading_pnl_usd REAL DEFAULT 0,
       net_profit_usd REAL DEFAULT 0
   );
   ```

2. **`pricing.py`** — `LLM_PRICING` dict + `calc_llm_cost()` as above

3. **`tracker.py`** — `CostTracker` class:
   - `async def log_llm_call(...)`: compute cost via `calc_llm_cost`, INSERT into `llm_usage`; fire-and-forget with `asyncio.create_task`
   - `async def log_trading_fee(...)`: convert BNB fee to USD if needed; INSERT into `trading_fees`
   - `async def set_infra_cost(category, amount)`: INSERT OR REPLACE into `infra_costs` for current month
   - `async def get_range_summary(date_from, date_to) -> dict`: aggregate `llm_usage` + `trading_fees` + prorated infra + PnL from `trades`; return structured dict
   - `async def get_mtd_summary()`: calls `get_range_summary(first_of_month, today)`
   - `async def get_daily_summary(date)`: calls `get_range_summary(date, date)`
   - `async def export_csv(date_from, date_to) -> str`: write `llm_usage` rows to temp CSV; return path

4. **LLM engine integration** (`engine.py`):
   ```python
   # After each provider call in _call_macro, _call_htf, etc.:
   asyncio.create_task(cost_tracker.log_llm_call(
       provider=self.provider.__class__.__name__.lower().replace('provider',''),
       model=self.provider.model_name,
       call_type=call_type,  # 'macro' | 'htf' | 'mtf' | 'ltf' | 'signal'
       mode=self.mode,
       input_tokens=resp.input_tokens,
       output_tokens=resp.output_tokens,
       signal_id=signal_id,
   ))
   ```

5. **RealTrader integration** (`real_trader.py`):
   ```python
   # After entry order fill:
   for fill in entry_order.get('fills', []):
       asyncio.create_task(cost_tracker.log_trading_fee(
           trade_id=trade_id,
           order_type='entry',
           fee_amount=float(fill['commission']),
           fee_asset=fill['commissionAsset'],
       ))
   ```

6. **`cost_handler.py`** — parse `/cost ARGS`:
   ```python
   match args:
       case [] | ["today"]:    summary = await cost_tracker.get_daily_summary(today())
       case ["mtd"]:           summary = await cost_tracker.get_mtd_summary()
       case ["from", d1, "to", d2]: summary = await cost_tracker.get_range_summary(d1, d2)
       case ["from", d1]:      summary = await cost_tracker.get_range_summary(d1, today())
       case ["llm"]:           # show LLM breakdown only
       case ["set", cat, amt]: await cost_tracker.set_infra_cost(cat, float(amt))
       case ["export"]:        path = await cost_tracker.export_csv(); # send as document
       case _:                 await update.message.reply_text(COST_HELP_TEXT)
   ```
   Validate dates with `datetime.strptime(d, "%Y-%m-%d")`.

7. **Daily cost aggregator** — add to orchestrator scheduler:
   ```python
   scheduler.add_job(
       lambda: asyncio.create_task(_aggregate_daily_costs(db, today_utc())),
       'cron', hour=23, minute=58, id='cost_daily_agg'
   )
   ```

8. **Budget alert**: after each `log_llm_call`, check daily total; if > `settings.LLM_DAILY_BUDGET_USD`, send Telegram warning.

## Todo

- [ ] Schema: 4 new tables in `schema.sql`
- [ ] `pricing.py` LLM pricing table + calc function
- [ ] `tracker.py`:
  - [ ] `log_llm_call()` (fire-and-forget)
  - [ ] `log_trading_fee()`
  - [ ] `set_infra_cost()`
  - [ ] `get_range_summary()` (used by all query methods)
  - [ ] `get_mtd_summary()` + `get_daily_summary()`
  - [ ] `export_csv()`
- [ ] Inject CostTracker into LLMEngine
- [ ] Inject CostTracker into RealTrader
- [ ] `cost_handler.py` — all subcommands
- [ ] Register `/cost` in `bot.py`
- [ ] Daily aggregator cron in orchestrator
- [ ] Budget alert when daily cost > threshold

## Success Criteria
- After each LLM call: `llm_usage` has record with correct cost
- `/cost today` returns correct total cost + net profit
- `net_profit = pnl - llm_cost - trading_fees - infra_prorated`
- Cost logging does not slow trading pipeline (fire-and-forget)
- Paper trade: LLM costs tracked, trading fees = 0

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| LLM pricing changes | Low | `pricing.py` isolated — update + restart |
| BNB fee conversion fails | Low | Fallback: estimate `taker_fee * notional` |
| CSV export large file | Low | Limit export to 90 days max; document limit |

## Security Considerations
- Cost data is operational — not secret, but keep in DB (not exposed externally)
- `/cost export` sends document to Telegram — authorized user only

## Next Steps
- After 1 month: export CSV via `/cost export` for trend analysis
- If LLM cost > 30% of PnL: switch to cheaper provider (Deepseek/Gemini)
