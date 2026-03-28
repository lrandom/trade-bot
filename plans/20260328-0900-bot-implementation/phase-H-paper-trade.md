# Phase H — Paper Trade Dashboard

## Context
- Parent plan: [plan.md](plan.md)
- Design spec: `plans/20260327-1200-gold-trading-bot/phase-09-paper-trade.md`
- Depends on: [phase-E-execution.md](phase-E-execution.md), [phase-F-telegram.md](phase-F-telegram.md), [phase-G-orchestration.md](phase-G-orchestration.md)
- Must be completed before live trading

## Overview
- **Date:** 2026-03-28
- **Priority:** P0 (required before live)
- **Status:** pending
- Paper trade dashboard: `/paper` Telegram commands, daily stats aggregator, KPI tracking, daily summary message.

## Key Insights
- `MockTrader` (Phase E) is the paper trade core — this phase adds monitoring + reporting on top
- `paper_orders` + `paper_stats` tables already added in Phase E schema
- Daily stats scheduled at 23:59 UTC → aggregates into `paper_stats`
- KPI gate: win rate >= 50%, profit factor >= 1.3 before going live
- `DRY_RUN=true` mode: no DB write, no Telegram, just logs — useful for prompt quality testing
- Position monitor loop (Phase E) handles TP/SL simulation for paper orders

## Requirements

**Functional:**
- `/paper status` — open paper orders with mark price PnL
- `/paper history` — last 20 closed paper orders
- `/paper stats` — win rate, profit factor, total PnL, max drawdown, avg RR
- `/paper reset` — delete all paper_orders + paper_stats (confirmation required)
- `/paper on` / `/paper off` — toggle `PAPER_TRADE` in DB config (requires bot restart for clean state)
- Daily summary at 23:59 UTC: sent to Telegram automatically
- `--dry-run` CLI flag (or `DRY_RUN=true` in `.env`): no DB, no Telegram, just stdout

**Non-functional:**
- Paper stats displayed in ICT timezone
- All paper orders clearly labeled `[PAPER]` in Telegram messages

## Architecture

The `MockTrader` and `paper_orders` schema are already in Phase E.
This phase adds:

```
bot/telegram/handlers/
└── paper_handler.py     # /paper command parser + subcommands

bot/orchestrator.py      # add daily_stats cron job (23:59 UTC)
```

### Daily Stats Aggregation (23:59 UTC)
```python
async def aggregate_paper_stats(db, date_str: str, mode: str):
    orders = await db.fetchall(
        "SELECT * FROM paper_orders WHERE DATE(close_time)=? AND status!='open'", date_str
    )
    if not orders:
        return

    wins   = [o for o in orders if o['pnl_usd'] > 0]
    losses = [o for o in orders if o['pnl_usd'] <= 0]
    win_rate = len(wins) / len(orders) if orders else 0

    gross_profit = sum(o['pnl_usd'] for o in wins)
    gross_loss   = abs(sum(o['pnl_usd'] for o in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

    total_pnl = sum(o['pnl_usd'] for o in orders)
    # Max drawdown: peak-to-trough on running PnL curve
    running = []; cumsum = 0
    for o in sorted(orders, key=lambda x: x['close_time']):
        cumsum += o['pnl_usd']
        running.append(cumsum)
    peak = running[0]; max_dd = 0
    for val in running:
        if val > peak: peak = val
        dd = (peak - val) / abs(peak) if peak != 0 else 0
        max_dd = max(max_dd, dd)

    await db.execute("""
        INSERT OR REPLACE INTO paper_stats
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (date_str, mode, len(orders), len(wins), len(losses),
          win_rate, profit_factor, total_pnl, max_dd))
    await db.commit()
```

### Daily Summary Telegram Message
```
[PAPER] Daily Summary — 28/03/2026 (ICT)
Mode: Swing | Provider: claude-sonnet-4-6

Trades: 3 | Wins: 2 | Losses: 1
Win Rate: 66.7% | Profit Factor: 2.1
Daily PnL: +$47.30 (simulated $10,000)
Max Drawdown today: -1.2%

Running total: +$142.80 (+1.43%)
Signals: 8 | Gate filtered: 5 | Executed: 3
```

### KPI Gate (before going live)
```
Min threshold | Target
Win rate      ≥50%  | ≥55%
Profit factor ≥1.3  | ≥1.5
Max drawdown  ≤8%   | ≤5%
Avg RR        ≥1.5  | ≥2.0
Uptime        ≥95%  | ≥99%
Period        2 wk  | 4 wk
```

## Related Code Files

| Action | File | Description |
|--------|------|-------------|
| create | `bot/telegram/handlers/paper_handler.py` | `/paper` subcommand parser |
| modify | `bot/orchestrator.py` | Add `aggregate_paper_stats` cron at 23:59 UTC |
| modify | `bot/telegram/bot.py` | Register `/paper` CommandHandler |
| modify | `main.py` | Add `--dry-run` argparse flag |

## Implementation Steps

1. **`main.py`** — add argparse:
   ```python
   import argparse
   parser = argparse.ArgumentParser()
   parser.add_argument('--dry-run', action='store_true')
   args = parser.parse_args()
   if args.dry_run:
       os.environ['DRY_RUN'] = 'true'
   asyncio.run(main())
   ```

2. **`paper_handler.py`** — parse `/paper SUBCOMMAND`:
   - `status`: query `paper_orders WHERE status='open'`; fetch mark price; format live PnL per order
   - `history N=20`: query last N closed paper orders; format table
   - `stats`: aggregate from `paper_stats` (MTD); format KPI table
   - `reset`: reply "Are you sure? Reply /paper confirm-reset"; on confirm: DELETE paper_orders + paper_stats
   - `on` / `off`: update DB `config` key `paper_mode`; reply "Paper mode ON/OFF — restart for full effect"

3. **Register handler** in `bot.py`: `app.add_handler(CommandHandler("paper", cmd_paper))`

4. **Orchestrator cron** — add to APScheduler:
   ```python
   scheduler.add_job(
       lambda: asyncio.create_task(aggregate_paper_stats(db, today_utc(), mode)),
       'cron', hour=23, minute=59, id='paper_daily_stats'
   )
   ```

5. **Dry-run guard** in `run_analysis_cycle()` (Phase G):
   ```python
   if settings.DRY_RUN:
       logger.info(f"[DRY RUN] Signal: action={signal.action} entry={signal.entry_price:.2f} conf={signal.confidence}%")
       return
   ```

6. **Position monitor additions** (Phase E `position_monitor.py`) — send Telegram on paper TP/SL:
   - After simulated TP hit: `await send_notification(bot, chat_id, f"[PAPER] TP{n} hit: {order.id}")`
   - After simulated SL hit: `await send_notification(bot, chat_id, f"[PAPER] SL hit: {order.id} | PnL: {pnl:.2f}")`

## Todo

- [ ] Add argparse `--dry-run` to `main.py`
- [ ] Dry-run guard in `run_analysis_cycle()`
- [ ] `paper_handler.py` — 5 subcommands
- [ ] Register `/paper` in `bot.py`
- [ ] `aggregate_paper_stats()` function
- [ ] Daily stats cron in orchestrator (23:59 UTC)
- [ ] Daily summary Telegram message formatter
- [ ] Telegram notifications on paper TP/SL hits
- [ ] `/paper reset` with confirmation step
- [ ] KPI summary in `/paper stats`

## Success Criteria
- Paper trade runs for 24h without crash
- `/paper stats` shows correct win rate, profit factor
- Daily summary sent at 23:59 UTC in ICT format
- `--dry-run` flag: signal printed to stdout, no DB writes, no Telegram
- `[PAPER]` label visible in all paper notifications
- SL hit on paper → `paper_orders.status='stopped'`, correct negative PnL

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| Paper PnL diverges from live (slippage) | Medium | 0.05% simulated slippage in MockTrader (Phase E) |
| Forget to turn off PAPER_TRADE before live | High | Startup banner prominently shows PAPER/LIVE status |
| Paper stats lost on reset | Medium | Warn user + require `/paper confirm-reset` |
| KPI looks good on paper but bad live | Medium | Layer 3 (micro live $10) required before full size |

## Security Considerations
- `/paper off` changes config in DB — requires authorized user
- Double-confirmation on `/paper reset` (destructive operation)
- Startup banner shows PAPER mode prominently

## Next Steps
- Run paper trade ≥2 weeks per mode
- Hit KPI thresholds (win rate ≥50%, profit factor ≥1.3) before Phase "Live Micro"
- Export data: use `/paper history` manually or add `/paper export` later
