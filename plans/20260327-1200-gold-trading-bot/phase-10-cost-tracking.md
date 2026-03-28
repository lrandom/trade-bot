# Phase 10 — Cost Tracking Dashboard

## Context
- Parent plan: [plan.md](plan.md)
- Depends on: phase-03 (LLM engine — token logging), phase-07 (execution — trading fees)
- Integrates vào: phase-06 (Telegram commands), phase-09 (paper trade stats)

## Overview
- **Date:** 2026-03-28
- **Priority:** P1
- **Status:** pending
- Track toàn bộ chi phí vận hành bot: LLM API, trading fees, infrastructure. So sánh với PnL để tính **net profit thực sự**.

---

## Cost Categories

```
┌─────────────────────────────────────────────────────┐
│              TOTAL COST BREAKDOWN                   │
│                                                     │
│  1. LLM API Cost          (tự động — theo token)    │
│     ├── Anthropic (Claude)                          │
│     ├── OpenAI (GPT)                                │
│     ├── Google (Gemini)                             │
│     └── Deepseek                                    │
│                                                     │
│  2. Trading Fees          (tự động — theo trade)    │
│     ├── Binance maker fee (0.02%)                   │
│     └── Binance taker fee (0.04%)                   │
│                                                     │
│  3. Infrastructure        (thủ công — config)       │
│     ├── VPS monthly cost                            │
│     ├── Domain (optional)                           │
│     └── Other (backup, monitoring...)               │
│                                                     │
│  NET PROFIT = Trading PnL - LLM Cost                │
│               - Trading Fees - Infra Cost           │
└─────────────────────────────────────────────────────┘
```

---

## LLM Pricing Table (hardcoded, update khi provider thay đổi giá)

```python
# bot/cost/pricing.py

LLM_PRICING = {
    "anthropic": {
        "claude-sonnet-4-6":  {"input": 3.00,  "output": 15.00},  # per 1M tokens
        "claude-haiku-4-5":   {"input": 0.80,  "output": 4.00},
        "claude-opus-4-6":    {"input": 15.00, "output": 75.00},
    },
    "openai": {
        "gpt-4o":             {"input": 2.50,  "output": 10.00},
        "gpt-4o-mini":        {"input": 0.15,  "output": 0.60},
        "o1-mini":            {"input": 1.10,  "output": 4.40},
    },
    "gemini": {
        "gemini-2.0-flash":   {"input": 0.10,  "output": 0.40},
        "gemini-1.5-pro":     {"input": 1.25,  "output": 5.00},
    },
    "deepseek": {
        "deepseek-chat":      {"input": 0.27,  "output": 1.10},   # deepseek-v3
        "deepseek-reasoner":  {"input": 0.55,  "output": 2.19},   # deepseek-r1
    },
}

BINANCE_FEES = {
    "maker": 0.0002,   # 0.02%
    "taker": 0.0004,   # 0.04%
}

def calc_llm_cost(provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = LLM_PRICING[provider][model]
    return (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000
```

---

## SQLite Schema

```sql
-- LLM usage log (ghi sau mỗi API call)
CREATE TABLE IF NOT EXISTS llm_usage (
    id          TEXT PRIMARY KEY,
    timestamp   DATETIME DEFAULT CURRENT_TIMESTAMP,
    provider    TEXT,           -- anthropic | openai | gemini | deepseek
    model       TEXT,
    call_type   TEXT,           -- macro | htf | mtf | ltf | signal | management
    mode        TEXT,           -- swing | intraday | scalp
    input_tokens  INTEGER,
    output_tokens INTEGER,
    cost_usd    REAL,           -- pre-calculated
    signal_id   TEXT            -- link to signal (nullable)
);

-- Trading fee log (ghi sau mỗi order fill)
CREATE TABLE IF NOT EXISTS trading_fees (
    id          TEXT PRIMARY KEY,
    timestamp   DATETIME DEFAULT CURRENT_TIMESTAMP,
    trade_id    TEXT,
    order_type  TEXT,           -- entry | tp1 | tp2 | tp3 | sl | manual_close
    fee_asset   TEXT,           -- USDT | BNB
    fee_amount  REAL,
    fee_usd     REAL
);

-- Infrastructure cost (nhập thủ công hoặc từ config)
CREATE TABLE IF NOT EXISTS infra_costs (
    id          TEXT PRIMARY KEY,
    month       TEXT,           -- YYYY-MM
    category    TEXT,           -- vps | domain | other
    description TEXT,
    cost_usd    REAL
);

-- Daily cost summary (aggregated mỗi ngày)
CREATE TABLE IF NOT EXISTS cost_summary (
    date            DATE PRIMARY KEY,
    llm_cost_usd    REAL DEFAULT 0,
    trading_fee_usd REAL DEFAULT 0,
    infra_cost_usd  REAL DEFAULT 0,   -- prorated daily
    total_cost_usd  REAL DEFAULT 0,
    trading_pnl_usd REAL DEFAULT 0,   -- from trades / paper_orders
    net_profit_usd  REAL DEFAULT 0    -- pnl - total_cost
);
```

---

## CostTracker Class

```python
# bot/cost/tracker.py

class CostTracker:

    async def log_llm_call(
        self, provider, model, call_type, mode,
        input_tokens, output_tokens, signal_id=None
    ):
        cost = calc_llm_cost(provider, model, input_tokens, output_tokens)
        await db.insert("llm_usage", {
            "id": uuid4().hex,
            "provider": provider, "model": model,
            "call_type": call_type, "mode": mode,
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "cost_usd": cost, "signal_id": signal_id,
        })

    async def log_trading_fee(self, trade_id, order_type, fee_amount, fee_asset="USDT"):
        fee_usd = fee_amount if fee_asset == "USDT" else fee_amount * await get_bnb_price()
        await db.insert("trading_fees", {...})

    async def get_daily_summary(self, date: str) -> dict:
        llm   = await db.scalar("SELECT SUM(cost_usd) FROM llm_usage WHERE DATE(timestamp)=?", date)
        fees  = await db.scalar("SELECT SUM(fee_usd) FROM trading_fees WHERE DATE(timestamp)=?", date)
        infra = await self._prorate_infra(date)
        pnl   = await db.scalar("SELECT SUM(pnl_usd) FROM trades WHERE DATE(closed_at)=?", date)
        return {
            "llm_cost": llm or 0,
            "trading_fees": fees or 0,
            "infra_cost": infra or 0,
            "total_cost": (llm or 0) + (fees or 0) + (infra or 0),
            "trading_pnl": pnl or 0,
            "net_profit": (pnl or 0) - (llm or 0) - (fees or 0) - (infra or 0),
        }

    async def get_range_summary(self, date_from: str, date_to: str) -> dict:
        """
        Dùng cho cả MTD và custom range.
        date_from, date_to: "YYYY-MM-DD"
        """
        llm_rows = await db.fetchall("""
            SELECT provider, model, COUNT(*) as calls,
                   SUM(input_tokens) as in_tok, SUM(output_tokens) as out_tok,
                   SUM(cost_usd) as cost
            FROM llm_usage
            WHERE DATE(timestamp) BETWEEN ? AND ?
            GROUP BY provider, model
        """, date_from, date_to)

        fees = await db.scalar("""
            SELECT SUM(fee_usd) FROM trading_fees
            WHERE DATE(timestamp) BETWEEN ? AND ?
        """, date_from, date_to)

        pnl = await db.scalar("""
            SELECT SUM(pnl_usd) FROM trades
            WHERE DATE(closed_at) BETWEEN ? AND ?
        """, date_from, date_to)

        # Infra: prorate theo số ngày trong range
        days_in_range = (date(date_to) - date(date_from)).days + 1
        days_in_month = calendar.monthrange(date_from[:4], date_from[5:7])[1]
        infra_monthly = await self._get_monthly_infra_total(date_from[:7])
        infra_prorated = infra_monthly * (days_in_range / days_in_month)

        total_llm = sum(r["cost"] for r in llm_rows)
        total_cost = total_llm + (fees or 0) + infra_prorated
        net = (pnl or 0) - total_cost

        return {
            "date_from": date_from,
            "date_to": date_to,
            "days": days_in_range,
            "llm": {"total": total_llm, "breakdown": llm_rows},
            "trading_fees": fees or 0,
            "infra": infra_prorated,
            "total_cost": total_cost,
            "trading_pnl": pnl or 0,
            "net_profit": net,
            "cost_per_day": total_cost / days_in_range,
            "net_per_day": net / days_in_range,
            "cost_pnl_ratio": total_cost / (pnl or 1),  # % cost trên PnL
        }

    async def get_mtd_summary(self) -> dict:
        """Month-to-date: từ ngày 1 tháng hiện tại đến hôm nay."""
        today = date.today().isoformat()
        month_start = today[:8] + "01"          # YYYY-MM-01
        return await self.get_range_summary(month_start, today)

    async def get_daily_summary(self, date: str) -> dict:
        return await self.get_range_summary(date, date)
```

---

## Telegram Commands

| Command | Output |
|---------|--------|
| `/cost today` | Chi phí + PnL hôm nay |
| `/cost mtd` | Month-to-date: từ đầu tháng đến hiện tại |
| `/cost from 2026-03-01 to 2026-03-28` | Custom date range |
| `/cost from 2026-03-01` | Từ ngày đến hôm nay |
| `/cost llm` | LLM breakdown theo provider/model (MTD) |
| `/cost set vps 10` | Nhập chi phí VPS $10/tháng |
| `/cost set domain 1.5` | Nhập chi phí domain $1.5/tháng |
| `/cost export` | Export CSV toàn bộ history |

---

### Output: `/cost today`
```
💰 Cost Report — 2026-03-28

📡 LLM API:
  claude-sonnet-4-6   8 calls  $0.42
  gpt-4o-mini        12 calls  $0.08
  Total LLM:                   $0.50

📊 Trading Fees:    $0.18  (3 trades × avg 0.04%)
🖥️  Infra (daily):  $0.33  (VPS $10/mo → $0.33/day)

💸 Total Cost:      $1.01
📈 Trading PnL:    +$23.40
─────────────────────────────
✅ Net Profit:     +$22.39
```

---

### Output: `/cost mtd`
```
📅 Month-to-Date — 2026-03-01 → 2026-03-28 (28 days)

📡 LLM API:         $14.20
  ├─ anthropic      $9.80  (68%)  234 calls
  ├─ openai         $3.60  (25%)  480 calls
  └─ deepseek       $0.80  (7%)   96 calls

📊 Trading Fees:    $5.40   (47 trades)
🖥️  Infra:          $9.33   (VPS $10 + domain $0.33 prorated)

💸 Total Cost:      $28.93
📈 Trading PnL:    +$187.50
─────────────────────────────
✅ Net Profit:     +$158.57

📊 Cost per trade:  $0.62
📊 Cost/PnL ratio:  15.4%  (mỗi $1 lời tốn $0.154 chi phí)
```

---

### Output: `/cost from 2026-03-10 to 2026-03-20`
```
📅 Cost Report — 2026-03-10 → 2026-03-20 (11 days)

📡 LLM API:         $5.60
📊 Trading Fees:    $2.10
🖥️  Infra:          $3.67  (prorated 11/31 days)

💸 Total Cost:      $11.37
📈 Trading PnL:    +$72.30
─────────────────────────────
✅ Net Profit:     +$60.93

Daily avg cost:   $1.03/day
Daily avg net:   +$5.54/day
```

---

## Telegram Command Parser

```python
# bot/telegram/handlers/cost_handler.py

async def cost_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args  # list of words after /cost

    match args:
        case [] | ["today"]:
            summary = await cost_tracker.get_daily_summary(today())
            await send_daily_report(update, summary)

        case ["mtd"]:
            summary = await cost_tracker.get_mtd_summary()
            await send_range_report(update, summary)

        case ["from", d1, "to", d2]:
            # /cost from 2026-03-10 to 2026-03-20
            validate_date(d1); validate_date(d2)
            summary = await cost_tracker.get_range_summary(d1, d2)
            await send_range_report(update, summary)

        case ["from", d1]:
            # /cost from 2026-03-10  →  đến hôm nay
            validate_date(d1)
            summary = await cost_tracker.get_range_summary(d1, today())
            await send_range_report(update, summary)

        case ["llm"]:
            summary = await cost_tracker.get_mtd_summary()
            await send_llm_breakdown(update, summary["llm"])

        case ["set", category, amount]:
            # /cost set vps 10
            await cost_tracker.set_infra_cost(category, float(amount))
            await update.message.reply_text(f"✅ {category}: ${amount}/month saved")

        case ["export"]:
            csv_path = await cost_tracker.export_csv()
            await update.message.reply_document(csv_path)

        case _:
            await update.message.reply_text(COST_HELP_TEXT)


def validate_date(s: str):
    try:
        datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"Invalid date format: {s}. Use YYYY-MM-DD")
```

## Integration Points

### Phase 03 — LLM Engine
```python
# Sau mỗi API call trong engine.py:
response = await provider.complete_structured(...)
await cost_tracker.log_llm_call(
    provider=settings.llm_provider,
    model=settings.llm_model,
    call_type="htf",  # macro | htf | mtf | ltf | signal
    mode=settings.trading_mode,
    input_tokens=response.input_tokens,
    output_tokens=response.output_tokens,
    signal_id=current_signal_id,
)
```

### Phase 07 — Execution Engine
```python
# Sau mỗi order fill từ Binance:
fill = binance_response["fills"][0]
await cost_tracker.log_trading_fee(
    trade_id=trade.id,
    order_type="entry",
    fee_amount=float(fill["commission"]),
    fee_asset=fill["commissionAsset"],
)
```

### Phase 09 — Paper Trade
- Paper trade không tính trading fees (mock)
- Vẫn tính LLM cost (vì LLM calls là thật)
- Hiển thị trong `/cost` với note `[PAPER]`

---

## Related Code Files

| Action | File | Description |
|--------|------|-------------|
| create | `bot/cost/pricing.py` | LLM price table + `calc_llm_cost()` |
| create | `bot/cost/tracker.py` | `CostTracker` class |
| modify | `bot/llm/engine.py` | Inject `CostTracker`, log after each call |
| modify | `bot/trader/real_trader.py` | Log fee after each fill |
| modify | `bot/telegram/bot.py` | Thêm `/cost` commands |
| modify | `schema.sql` | Thêm 4 tables mới |

---

## Todo

- [ ] `pricing.py` — LLM price table + `calc_llm_cost()`
- [ ] `tracker.py`:
  - [ ] `log_llm_call()` — ghi sau mỗi LLM API call
  - [ ] `log_trading_fee()` — ghi sau mỗi order fill
  - [ ] `get_daily_summary(date)` — báo cáo 1 ngày
  - [ ] `get_mtd_summary()` — từ đầu tháng đến hôm nay
  - [ ] `get_range_summary(date_from, date_to)` — custom range
  - [ ] `set_infra_cost(category, amount)` — lưu infra monthly cost
  - [ ] `export_csv(date_from, date_to)` — export toàn bộ
- [ ] Schema: `llm_usage`, `trading_fees`, `infra_costs`, `cost_summary`
- [ ] Inject `CostTracker` vào `LLMEngine` (phase-03)
- [ ] Inject `CostTracker` vào `RealTrader` (phase-07)
- [ ] `cost_handler.py` — parse `/cost` commands:
  - [ ] `today` / mặc định
  - [ ] `mtd`
  - [ ] `from DATE to DATE`
  - [ ] `from DATE` (đến hôm nay)
  - [ ] `llm` breakdown
  - [ ] `set vps/domain AMOUNT`
  - [ ] `export`
- [ ] Daily aggregator (23:59 UTC) → ghi `cost_summary`
- [ ] Alert khi LLM cost vượt `LLM_DAILY_BUDGET_USD`

## Success Criteria
- Sau mỗi LLM call → `llm_usage` có record với cost đúng
- `/cost today` trả về đúng tổng cost + net profit
- `net_profit = pnl - llm_cost - trading_fees - infra`
- Không làm chậm trading pipeline (log async, non-blocking)

## Risk Assessment
| Risk | Mitigation |
|------|------------|
| LLM pricing thay đổi | `pricing.py` tách riêng, dễ update. Thêm command `/cost update-pricing` |
| BNB fee conversion lỗi | Fallback: dùng taker_fee * notional nếu không lấy được BNB price |
| Cost log làm chậm bot | Tất cả log ghi async, không await trong critical path |

## Next Steps
- Sau 1 tháng: export CSV `/cost export` để phân tích trend
- Alert Telegram nếu LLM cost vượt ngưỡng: `LLM_DAILY_BUDGET_USD=5`
