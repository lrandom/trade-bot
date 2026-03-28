# Phase 09 — Paper Trade & Testing Ladder

## Context
- Parent plan: [plan.md](plan.md)
- Depends on: phase-07 (execution engine), phase-06 (telegram), phase-03 (LLM engine)
- Must be completed before any live trading

## Overview
- **Date:** 2026-03-27
- **Priority:** P0 — bắt buộc trước khi live
- **Status:** pending
- **Goal:** Validate toàn bộ pipeline với 3 lớp test tăng dần rủi ro

---

## Testing Ladder

```
┌─────────────────────────────────────────────────────────────┐
│  Layer 1: Dry Run          python main.py --dry-run          │
│  ─────────────────────────────────────────────────────────  │
│  • Chạy LLM chain thực (tốn API token)                       │
│  • In signal ra stdout/log                                   │
│  • KHÔNG ghi DB, KHÔNG Telegram, KHÔNG execute              │
│  • Dùng để: kiểm tra prompt quality, JSON format đúng không  │
│  • KPI: signal JSON hợp lệ 100%, không crash                 │
│                                                              │
│  Layer 2: Paper Trade      PAPER_TRADE=true                  │
│  ─────────────────────────────────────────────────────────  │
│  • Full pipeline như live                                    │
│  • Order → MockTrader (ghi SQLite, không gọi Binance API)    │
│  • PnL tính theo mark price thực từ Binance                  │
│  • Telegram: bắn signal + kết quả lệnh ảo + daily summary   │
│  • Thời gian recommend: 2-4 tuần mỗi mode                    │
│  • KPI để lên live: win rate ≥50%, profit factor ≥1.3        │
│                                                              │
│  Layer 3: Live Micro       PAPER_TRADE=false + MIN_SIZE=10   │
│  ─────────────────────────────────────────────────────────  │
│  • Trade thật, position size tối đa $10/lệnh                 │
│  • Xác nhận: order fill, SL/TP placement, Telegram đúng      │
│  • Sau 1 tuần không lỗi → tăng size dần                     │
└─────────────────────────────────────────────────────────────┘
```

---

## Architecture

### MockTrader (Paper Trade core)

```python
# bot/trader/mock_trader.py

class MockTrader:
    """Replaces RealTrader when PAPER_TRADE=true.
    Same interface — engine.py không cần biết đang paper hay live.
    """

    async def place_order(self, signal: TradingSignal, size: float) -> PaperOrder:
        order = PaperOrder(
            id=uuid4().hex,
            symbol="XAUUSDT",
            side=signal.action,
            entry=signal.entry_price,
            stop_loss=signal.stop_loss,
            tp1=signal.tp1, tp2=signal.tp2, tp3=signal.tp3,
            size=size,
            open_time=datetime.utcnow(),
            status="open",
            mode=settings.trading_mode,
        )
        await db.save_paper_order(order)
        return order

    async def get_open_positions(self) -> list[PaperOrder]:
        return await db.get_open_paper_orders()

    async def close_position(self, order_id: str, close_price: float):
        order = await db.get_paper_order(order_id)
        pnl = self._calc_pnl(order, close_price)
        await db.close_paper_order(order_id, close_price, pnl)
        return pnl
```

### Factory pattern (Provider pattern tương tự LLM)

```python
# bot/trader/factory.py
def get_trader(settings) -> BaseTrader:
    if settings.paper_trade:
        return MockTrader(db)
    return RealTrader(binance_client)
```

### PnL Monitor (chạy mỗi 30s)

```python
# Kiểm tra các paper order đang mở:
# - Nếu mark_price chạm SL → close với SL price, ghi loss
# - Nếu chạm TP1 → close 33%, ghi partial win
# - Nếu chạm TP2 → close thêm 33%
# - Nếu chạm TP3 → close nốt 34%, ghi full win
async def monitor_paper_positions(mark_price: float):
    for order in await db.get_open_paper_orders():
        if hits_stop_loss(order, mark_price):
            await mock_trader.close_position(order.id, order.stop_loss)
            await telegram.send(f"❌ SL hit: {order.id} | PnL: {pnl:.2f}$")
        elif hits_tp(order, mark_price):
            await mock_trader.partial_close(order.id, mark_price)
```

---

## SQLite Schema (paper trade tables)

```sql
CREATE TABLE paper_orders (
    id          TEXT PRIMARY KEY,
    symbol      TEXT DEFAULT 'XAUUSDT',
    side        TEXT,           -- BUY | SELL
    mode        TEXT,           -- swing | intraday | scalp
    entry       REAL,
    stop_loss   REAL,
    tp1 REAL, tp2 REAL, tp3 REAL,
    size        REAL,           -- notional USD
    open_time   DATETIME,
    close_time  DATETIME,
    close_price REAL,
    pnl_usd     REAL,
    pnl_pct     REAL,
    status      TEXT,           -- open | tp1_hit | tp2_hit | closed | stopped
    signal_id   TEXT,
    llm_provider TEXT,
    confidence  INTEGER
);

CREATE TABLE paper_stats (
    date        DATE PRIMARY KEY,
    mode        TEXT,
    total_trades INTEGER,
    wins        INTEGER,
    losses      INTEGER,
    win_rate    REAL,
    profit_factor REAL,
    total_pnl_usd REAL,
    max_drawdown REAL
);
```

---

## Telegram Commands (paper trade)

| Command | Mô tả |
|---------|-------|
| `/paper on` | Bật paper trade mode |
| `/paper off` | Tắt paper trade (chuyển live) |
| `/paper status` | Danh sách lệnh ảo đang mở |
| `/paper history` | 20 lệnh ảo gần nhất |
| `/paper stats` | Win rate, profit factor, PnL tổng |
| `/paper reset` | Xóa toàn bộ paper history |

### Daily Summary (tự động 23:59 UTC)

```
📊 Paper Trade Daily Summary — 2026-03-27
Mode: Swing | Provider: claude-sonnet-4-6

Trades: 3 | Wins: 2 | Losses: 1
Win Rate: 66.7% | Profit Factor: 2.1
Daily PnL: +$47.30 (simulated $10,000 account)
Max Drawdown today: -1.2%

🟢 Running total: +$142.80 (+1.43%)
Signals generated: 8 | Filtered by gate: 5 | Executed: 3
```

---

## KPI để chuyển sang Live

| Metric | Min threshold | Target |
|--------|--------------|--------|
| Thời gian paper trade | 2 tuần/mode | 4 tuần |
| Win rate | ≥ 50% | ≥ 55% |
| Profit factor | ≥ 1.3 | ≥ 1.5 |
| Max drawdown | ≤ 8% | ≤ 5% |
| Avg RR (reward:risk) | ≥ 1.5:1 | ≥ 2:1 |
| Signal valid JSON | 100% | 100% |
| Bot uptime | ≥ 95% | ≥ 99% |

---

## Related Code Files

| Action | File | Description |
|--------|------|-------------|
| create | `bot/trader/base.py` | `BaseTrader` abstract interface |
| create | `bot/trader/real_trader.py` | Live Binance execution |
| create | `bot/trader/mock_trader.py` | Paper trade mock |
| create | `bot/trader/factory.py` | `get_trader(settings)` |
| modify | `bot/telegram_bot.py` | Thêm /paper commands |
| modify | `config.py` | `PAPER_TRADE: bool`, `DRY_RUN: bool` |
| modify | `main.py` | `--dry-run` CLI flag |
| modify | `data/schema.sql` | Thêm paper_orders + paper_stats tables |

---

## Implementation Steps

1. **config.py** — thêm `PAPER_TRADE: bool = True` (default true — safe), `DRY_RUN: bool = False`

2. **base.py** — `BaseTrader` với `place_order()`, `close_position()`, `get_open_positions()`

3. **mock_trader.py** — implement `BaseTrader`, ghi vào SQLite `paper_orders`

4. **factory.py** — `get_trader()` trả `MockTrader` nếu `PAPER_TRADE=true`

5. **PnL monitor** — thêm vào APScheduler, check mỗi 30s, update paper_orders status

6. **Daily stats aggregator** — chạy 23:59 UTC, tính win_rate/profit_factor, ghi `paper_stats`, gửi Telegram

7. **Telegram /paper commands** — `status`, `history`, `stats`, `reset`, `on/off`

8. **Dry-run flag** — `if settings.dry_run: logger.info(signal); return` trước bước ghi DB

---

## Todo

- [ ] `PAPER_TRADE=true` default trong .env template
- [ ] `--dry-run` CLI arg trong `main.py`
- [ ] `BaseTrader` abstract interface
- [ ] `MockTrader` — place/close/monitor paper orders
- [ ] `RealTrader` — live Binance execution (phase 07)
- [ ] `TraderFactory.get_trader(settings)`
- [ ] SQLite: `paper_orders` + `paper_stats` tables
- [ ] PnL monitor loop (30s) — SL/TP hit detection
- [ ] Daily stats aggregator + Telegram summary
- [ ] `/paper` Telegram commands
- [ ] KPI dashboard command `/paper stats`

---

## Risk Assessment

| Risk | Mitigation |
|------|------------|
| Paper PnL tốt nhưng live kém (slippage) | Thêm simulated slippage 0.05% vào paper fills |
| Paper không test được Binance API lỗi | Layer 3 (micro live) bắt buộc trước full live |
| Quên tắt PAPER_TRADE khi lên live | Telegram cảnh báo rõ mode mỗi lần start bot |
| Paper stats bị reset mất data | Export CSV định kỳ qua `/paper export` command |

## Security Considerations
- `PAPER_TRADE=true` là default — phải chủ động đổi sang `false` để live
- Khi `PAPER_TRADE=false`, bot hỏi xác nhận lần 2 qua Telegram trước khi start
- Log rõ mode (PAPER/LIVE) ở mỗi dòng log và mỗi Telegram message

## Next Steps
- Hoàn thành paper trade ≥ 2 tuần đạt KPI → Phase "Live Micro"
- Sau micro live 1 tuần ổn định → tăng size theo Kelly criterion
