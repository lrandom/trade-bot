# Phase 12 — Volatility & Correlation Filter

## Context
- Parent plan: [plan.md](plan.md)
- Depends on: phase-02 (Data Layer), phase-03 (LLM Engine), phase-05 (Trading Modes)
- Chạy như một pre-gate trước LLM chain — nếu filter block thì không tốn LLM token

## Overview
- **Date:** 2026-03-28
- **Priority:** P0 — bắt buộc trước live
- **Status:** pending
- 2 lớp bảo vệ: (1) ATR spike guard — phát hiện volatility bất thường, (2) Correlation check — DXY + Silver xác nhận hướng trước khi vào lệnh.

---

## Architecture

```
Candle close / Signal trigger
         │
         ▼
┌─────────────────────────────────────────────────────┐
│           Volatility & Correlation Filter            │
│                                                      │
│  ┌─────────────────────────────────────────────┐    │
│  │  Gate 1: ATR Spike Guard                    │    │
│  │                                             │    │
│  │  current_atr > avg_atr_20 × SPIKE_MULT?     │    │
│  │    YES → PAUSE (news event likely)          │    │
│  │    NO  → pass ✅                            │    │
│  └──────────────────┬──────────────────────────┘    │
│                     │ pass                          │
│                     ▼                               │
│  ┌─────────────────────────────────────────────┐    │
│  │  Gate 2: Correlation Check                  │    │
│  │                                             │    │
│  │  DXY trend vs Gold signal aligned?          │    │
│  │  Silver (XAGUSD) same direction?            │    │
│  │    CONFLICT → reduce confidence or HOLD     │    │
│  │    ALIGNED  → boost confidence + pass ✅   │    │
│  └──────────────────┬──────────────────────────┘    │
│                     │ pass                          │
│                     ▼                               │
│           → LLM Chain (phase-03)                    │
└─────────────────────────────────────────────────────┘
```

---

## Gate 1: ATR Spike Guard

```python
# bot/filters/volatility_filter.py

ATR_SPIKE_MULTIPLIERS = {
    "scalp":    2.0,   # ATR tăng 2x bình thường → dừng scalp
    "intraday": 2.5,   # Intraday chịu được nhiều hơn
    "swing":    3.0,   # Swing ít nhạy cảm với spike ngắn
}

class VolatilityFilter:

    async def check_atr_spike(self, mode: str, tf: str = "1h") -> FilterResult:
        df = await fetcher.get_ohlcv("XAUUSDT", tf, limit=25)
        df["atr"] = ta.atr(df.high, df.low, df.close, length=14)

        current_atr = df["atr"].iloc[-1]
        avg_atr_20  = df["atr"].iloc[-21:-1].mean()   # 20-bar avg, không tính bar hiện tại
        spike_ratio = current_atr / avg_atr_20

        threshold = ATR_SPIKE_MULTIPLIERS[mode]

        if spike_ratio >= threshold:
            return FilterResult(
                passed=False,
                reason=f"ATR spike {spike_ratio:.1f}x (threshold {threshold}x) — likely news event",
                action="PAUSE_AUTO_TRADE",
                spike_ratio=spike_ratio,
            )

        return FilterResult(passed=True, spike_ratio=spike_ratio)
```

**Telegram alert khi bị block:**
```
⚡ Volatility Alert — 19:32 VN
ATR spike detected: 2.8x bình thường
Action: Auto trade TẠM DỪNG
Chờ ATR về mức bình thường (~30 phút)
→ Signal mode vẫn hoạt động bình thường
```

**Hành vi khi ATR spike:**

| Mode | Hành động |
|------|-----------|
| Auto trade | Tạm dừng, chuyển signal mode |
| Signal mode | Vẫn gửi signal nhưng thêm cảnh báo ⚡ |
| Scalp | Block hoàn toàn (spike 2x đã nguy hiểm) |
| Swing | Chỉ cảnh báo, không block (swing chịu volatility tốt hơn) |

---

## Gate 2: Correlation Check

### DXY Proxy (không có free API trực tiếp)

```python
# Dùng EURUSD làm DXY proxy (tương quan nghịch ~0.95 với DXY)
# EURUSD là cặp lớn nhất trong rổ DXY (57.6% weight)

async def get_dxy_proxy_trend(self) -> str:
    """
    EURUSD tăng → DXY yếu → Gold BULLISH
    EURUSD giảm → DXY mạnh → Gold BEARISH
    """
    df = await fetcher.get_ohlcv("EURUSDT", "4h", limit=50)
    ema20 = ta.ema(df.close, 20).iloc[-1]
    ema50 = ta.ema(df.close, 50).iloc[-1]
    price = df.close.iloc[-1]

    if price > ema20 > ema50:
        return "DXY_WEAK"      # EURUSD uptrend → DXY yếu → Gold bullish
    elif price < ema20 < ema50:
        return "DXY_STRONG"    # EURUSD downtrend → DXY mạnh → Gold bearish
    else:
        return "DXY_NEUTRAL"
```

### Silver Correlation Check

```python
async def get_silver_alignment(self, gold_signal_action: str) -> CorrelationResult:
    """
    Silver thường dẫn trước Gold hoặc xác nhận cùng chiều.
    Silver ngược chiều → cảnh báo, giảm confidence.
    """
    df = await fetcher.get_ohlcv("XAGUSDT", "1h", limit=20)
    silver_rsi  = ta.rsi(df.close, 14).iloc[-1]
    silver_ema20 = ta.ema(df.close, 20).iloc[-1]
    silver_price = df.close.iloc[-1]
    silver_trend = "UP" if silver_price > silver_ema20 else "DOWN"

    gold_direction = "UP" if gold_signal_action == "BUY" else "DOWN"
    aligned = (silver_trend == gold_direction)

    return CorrelationResult(
        asset="XAGUSD",
        trend=silver_trend,
        aligned=aligned,
        rsi=silver_rsi,
        confidence_adj=+10 if aligned else -15,   # điều chỉnh confidence signal
    )
```

### Tổng hợp Correlation

```python
async def check_correlation(self, signal: TradingSignal) -> FilterResult:
    dxy   = await self.get_dxy_proxy_trend()
    silver = await self.get_silver_alignment(signal.action)

    conflicts = []
    confidence_adj = 0

    # DXY conflict check
    if signal.action == "BUY" and dxy == "DXY_STRONG":
        conflicts.append("DXY strong — đi ngược Gold BUY")
        confidence_adj -= 20
    elif signal.action == "SELL" and dxy == "DXY_WEAK":
        conflicts.append("DXY weak — đi ngược Gold SELL")
        confidence_adj -= 20

    # Silver alignment
    confidence_adj += silver.confidence_adj
    if not silver.aligned:
        conflicts.append(f"Silver diverging ({silver.trend})")

    # Adjusted confidence
    new_confidence = max(0, signal.confidence + confidence_adj)

    # Block nếu quá nhiều conflict và confidence quá thấp
    if len(conflicts) >= 2 and new_confidence < 40:
        return FilterResult(
            passed=False,
            reason=f"Correlation conflicts: {', '.join(conflicts)}",
            adjusted_confidence=new_confidence,
        )

    return FilterResult(
        passed=True,
        conflicts=conflicts,
        adjusted_confidence=new_confidence,
        dxy_status=dxy,
        silver_aligned=silver.aligned,
    )
```

---

## Thêm vào Macro Prompt (Phase 03)

Correlation data được inject vào LLM context:

```
## Market Correlations
DXY Proxy (via EURUSD 4H): {dxy_status}
  → {dxy_interpretation}

Silver (XAGUSD 1H):
  Trend: {silver_trend} | RSI: {silver_rsi}
  Alignment với Gold signal: {aligned}

⚠️ Conflicts: {conflicts_list or "None"}
```

---

## Cycle Context (Seasonal + Weekly)

Tự động inject vào Macro prompt:

```python
# bot/filters/cycle_context.py

def get_cycle_context() -> dict:
    now_ict = to_ict(utc_now())

    # Seasonal bias theo tháng
    SEASONAL = {
        1:  ("STRONG",  "Chinese New Year + India wedding demand"),
        2:  ("STRONG",  "Chinese New Year peak"),
        3:  ("NEUTRAL", "Post-CNY normalization"),
        4:  ("NEUTRAL", "Quiet period"),
        5:  ("NEUTRAL", "Pre-summer"),
        6:  ("WEAK",    "Summer doldrums — low volume"),
        7:  ("WEAK",    "Summer doldrums"),
        8:  ("WEAK",    "Summer doldrums — watch for reversal"),
        9:  ("STRONG",  "India festive season begins (Navratri, Dussehra)"),
        10: ("STRONG",  "Diwali demand + year-end positioning"),
        11: ("STRONG",  "Year-end buying, geopolitical hedge"),
        12: ("STRONG",  "Holiday buying, portfolio rebalancing"),
    }

    # Day of week
    DOW_NOTES = {
        0: "Monday — continuation from last week's trend",
        1: "Tuesday — usually trend day",
        2: "Wednesday — FOMC/CPI often released, high volatility risk",
        3: "Thursday — post-news follow-through or reversal",
        4: "Friday — position squaring, beware false breakouts",
        5: "Saturday — market closed",
        6: "Sunday — gap risk on open",
    }

    month = now_ict.month
    dow   = now_ict.weekday()
    seasonal_bias, seasonal_note = SEASONAL[month]

    return {
        "month":          now_ict.strftime("%B %Y"),
        "seasonal_bias":  seasonal_bias,
        "seasonal_note":  seasonal_note,
        "day_of_week":    DOW_NOTES[dow],
        "session":        session_label(utc_now().hour),
        "is_high_vol_day": dow == 2,   # Wednesday
    }
```

**Inject vào Macro prompt:**
```
## Cycle Context
Tháng: October 2026 → Seasonal bias: STRONG (Diwali demand)
Hôm nay: Wednesday → FOMC/CPI risk, high volatility possible
Phiên: London Open (14:30 VN) — high volume, trend likely
```

---

## SQLite Schema

```sql
CREATE TABLE IF NOT EXISTS filter_log (
    id              TEXT PRIMARY KEY,
    timestamp       DATETIME DEFAULT CURRENT_TIMESTAMP,
    signal_id       TEXT,
    filter_type     TEXT,   -- atr_spike | correlation | cycle
    passed          BOOLEAN,
    reason          TEXT,
    spike_ratio     REAL,   -- ATR spike ratio (nullable)
    dxy_status      TEXT,
    silver_aligned  BOOLEAN,
    confidence_adj  INTEGER,
    original_conf   INTEGER,
    adjusted_conf   INTEGER
);
```

---

## Telegram Commands

| Command | Output |
|---------|--------|
| `/filter status` | Trạng thái filter hiện tại |
| `/filter override` | Tắt filter 1 lần (cho lệnh tiếp theo) |
| `/filter log` | 10 filter blocks gần nhất + lý do |

### `/filter status` output:
```
🔍 Filter Status — 14:32 VN

ATR Spike Guard:
  Current ATR:  $18.40
  20-bar avg:   $12.10
  Spike ratio:  1.52x  ✅ (threshold: 2.5x)

Correlation:
  DXY (EURUSD): NEUTRAL  ✅
  Silver:       UP — aligned with BUY bias  ✅

Cycle Context:
  Tháng 10: STRONG seasonal (Diwali)
  Thứ 4: ⚠️ High volatility day (FOMC/CPI risk)
  Phiên: London Open — trend likely

Status: ✅ All filters PASS — ready to trade
```

---

## Related Code Files

| Action | File | Description |
|--------|------|-------------|
| create | `bot/filters/volatility_filter.py` | ATR spike check |
| create | `bot/filters/correlation_filter.py` | DXY proxy + Silver check |
| create | `bot/filters/cycle_context.py` | Seasonal + weekday context |
| create | `bot/filters/__init__.py` | `FilterChain` — chạy cả 2 gates |
| modify | `bot/llm/prompts.py` | Inject correlation + cycle vào Macro prompt |
| modify | `bot/orchestrator.py` | Chạy FilterChain trước LLM chain |
| modify | `bot/telegram/bot.py` | Thêm `/filter` commands |
| modify | `schema.sql` | Thêm `filter_log` table |

---

## Todo

- [ ] `volatility_filter.py` — ATR spike check với threshold theo mode
- [ ] `correlation_filter.py`:
  - [ ] `get_dxy_proxy_trend()` — EURUSD 4H làm proxy
  - [ ] `get_silver_alignment()` — XAGUSD 1H
  - [ ] `check_correlation()` — tổng hợp + adjust confidence
- [ ] `cycle_context.py` — seasonal + DOW context
- [ ] `FilterChain` — kết hợp 2 gates, chạy trước LLM
- [ ] Inject correlation + cycle vào Macro prompt (phase-03)
- [ ] Telegram alert khi ATR spike block auto trade
- [ ] `/filter status` + `/filter log` + `/filter override`
- [ ] `filter_log` DB table
- [ ] Unit test: spike detection với mock ATR data

## Success Criteria
- ATR 2x bình thường → auto trade dừng trong 30s
- Correlation conflict → confidence giảm đúng số điểm
- Filter block → ghi `filter_log`, gửi Telegram warning
- `/filter status` phản hồi trong 2s

## Risk Assessment
| Risk | Mitigation |
|------|------------|
| EURUSD không available trên Binance | Dùng EURUSDT (Binance có). Nếu không có → skip DXY check |
| XAGUSD không có trên Binance Futures | Dùng Binance Spot hoặc skip Silver check nếu API lỗi |
| Filter quá nhạy → miss nhiều signal tốt | Log tất cả blocks → review sau 1 tuần, tune threshold |
| ATR spike do thin market (weekend) → false positive | Thêm volume check: nếu volume < 50% avg → skip ATR check |

## Next Steps
- Sau 2 tuần: review `filter_log` → tính % signal bị block có phải loss không
- Nếu filter đúng >70% → giữ threshold; nếu <50% → nới lỏng
