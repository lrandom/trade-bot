# Research Report: Claude LLM + Telegram Bot + Risk Management

## 1. Claude LLM for Trading Decisions

### Model Selection
- **claude-sonnet-4-6**: Best balance cost/quality for trading signals (~$3/$15 per M tokens in/out)
- **claude-haiku-4-5**: 10x cheaper, suitable for quick scalp checks, less reasoning depth
- Recommendation: sonnet-4-6 for swing/intraday, haiku-4-5 for scalp mode

### Structured JSON Output via Tool Use
```python
import anthropic

client = anthropic.Anthropic()

tools = [{
    "name": "generate_trading_signal",
    "description": "Generate a trading signal for XAUUSDT",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["BUY", "SELL", "HOLD"]},
            "entry_price": {"type": "number"},
            "stop_loss": {"type": "number"},
            "tp1": {"type": "number"},
            "tp2": {"type": "number"},
            "tp3": {"type": "number"},
            "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
            "reasoning": {"type": "string"},
            "trend_bias": {"type": "string", "enum": ["BULLISH", "BEARISH", "NEUTRAL"]}
        },
        "required": ["action", "entry_price", "stop_loss", "tp1", "confidence", "reasoning"]
    }
}]

response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    tools=tools,
    tool_choice={"type": "auto"},
    messages=[{"role": "user", "content": prompt}]
)
# Extract tool_use block
signal = next(b for b in response.content if b.type == "tool_use").input
```

### Context Window Management
- 1D candle (200 bars) ≈ 1,600 tokens
- 4H candle (200 bars) ≈ 1,600 tokens
- Full multi-TF context (1D+4H+1H+15m) ≈ 6,000-8,000 tokens
- News + macro ≈ 1,000-2,000 tokens
- Total per request: ~10,000 tokens → well within 200K context
- **Tip**: Send only last 100 candles per TF, use aggregated summaries not raw OHLCV

---

## 2. python-telegram-bot v20+ (Async)

### Key Patterns
```python
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler

# Signal message with inline buttons
async def send_signal(bot, chat_id, signal):
    text = f"""
🥇 *XAUUSDT Signal*
Action: `{signal.action}`
Entry: `{signal.entry}`
SL: `{signal.stop_loss}`
TP1: `{signal.tp1}` | TP2: `{signal.tp2}`
Confidence: `{signal.confidence}%`
"""
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Approve", callback_data=f"approve_{signal.id}"),
         InlineKeyboardButton("❌ Reject", callback_data=f"reject_{signal.id}")]
    ])
    await bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=keyboard)

# Callback handler
async def button_callback(update: Update, context):
    query = update.callback_query
    await query.answer()
    action, signal_id = query.data.split("_", 1)
    if action == "approve":
        # Place order
        ...
```

### Architecture: Polling vs Webhook
- **Polling** (recommended for dev/VPS): `application.run_polling()`
- **Webhook** (recommended for production with domain): `application.run_webhook()`
- For a VPS bot running 24/7: polling is simpler, no SSL cert needed

### Commands List
| Command | Description |
|---------|-------------|
| /signal | Request latest signal analysis |
| /status | Show open positions |
| /balance | Account balance + PnL |
| /mode [swing\|scalp\|intraday] | Change trading mode |
| /auto [on\|off] | Toggle auto trading |
| /close | Close current position |
| /stop | Emergency stop all |
| /history | Last 10 trades |

---

## 3. Bot Architecture Patterns

### Event-Driven Async Architecture
```
Main Loop (asyncio)
├── Scheduler (APScheduler AsyncIOScheduler)
│   ├── Every 1m  → scalp analysis
│   ├── Every 15m → intraday analysis
│   ├── Every 4h  → swing analysis
│   └── Every 24h → macro refresh
├── Telegram Bot (polling loop)
└── Position Monitor (every 30s)
```

### SQLite Schema
```sql
-- signals table
CREATE TABLE signals (
    id TEXT PRIMARY KEY,
    timestamp DATETIME,
    mode TEXT,  -- swing/scalp/intraday
    action TEXT,  -- BUY/SELL/HOLD
    entry REAL, sl REAL, tp1 REAL, tp2 REAL, tp3 REAL,
    confidence INTEGER,
    reasoning TEXT,
    status TEXT  -- pending/approved/rejected/executed
);

-- trades table
CREATE TABLE trades (
    id TEXT PRIMARY KEY,
    signal_id TEXT,
    open_time DATETIME, close_time DATETIME,
    entry REAL, close_price REAL,
    pnl REAL, pnl_pct REAL,
    status TEXT  -- open/closed/stopped
);

-- config table
CREATE TABLE config (
    key TEXT PRIMARY KEY,
    value TEXT
);
```

---

## 4. Risk Management Formulas

### ATR-Based Stop Loss
```python
# ATR Stop Loss (gold)
atr = df['atr_14'].iloc[-1]

# Scalp: 1.0x ATR, Intraday: 1.5x ATR, Swing: 2.0x ATR
atr_multipliers = {"scalp": 1.0, "intraday": 1.5, "swing": 2.0}
sl_distance = atr * atr_multipliers[mode]

stop_loss_buy  = entry - sl_distance
stop_loss_sell = entry + sl_distance
```

### Position Sizing (Fixed Fractional — safer than Kelly)
```python
def calc_position_size(balance, risk_pct, entry, stop_loss, leverage):
    risk_amount = balance * (risk_pct / 100)  # e.g. 1% of capital
    sl_distance = abs(entry - stop_loss)
    sl_pct = sl_distance / entry
    position_size = (risk_amount / sl_pct) / entry  # in contracts
    max_position = (balance * leverage) / entry
    return min(position_size, max_position)
```

### Daily Drawdown Circuit Breaker
```python
# In risk.py
def check_circuit_breaker(daily_pnl, max_daily_loss_pct=5.0, balance=10000):
    if daily_pnl < -(balance * max_daily_loss_pct / 100):
        raise CircuitBreakerTriggered("Daily loss limit reached")
```

### Leverage by Mode
| Mode | Leverage | Max Risk/Trade |
|------|----------|----------------|
| Scalp | 10-20x | 0.5% |
| Intraday | 5-10x | 1% |
| Swing | 3-5x | 1.5-2% |

---

## Unresolved Questions
1. Should scalp mode use WebSocket real-time or 1m polling? (WebSocket is better but more complex)
2. Partial TP closes — does Binance Futures allow multiple TP orders simultaneously? (Yes via OCO)
3. Telegram rate limits: if many signals fire at once, need queue
4. How to handle Claude API timeout during fast market moves?
5. Paper trading mode needed before live? Recommend yes — simulate with mock trader
