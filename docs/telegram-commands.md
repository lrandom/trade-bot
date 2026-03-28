# Telegram Bot — Tài liệu lệnh

Bot chỉ phản hồi đúng `TELEGRAM_CHAT_ID` được cấu hình trong `.env`. Người khác nhắn tin sẽ bị bỏ qua hoàn toàn.

---

## Tổng quan nhanh

| Lệnh | Chức năng | Dùng khi |
|------|-----------|---------|
| `/signal` | Phân tích ngay | Muốn xem signal thủ công |
| `/status` | Vị thế + trạng thái bot | Kiểm tra bot đang làm gì |
| `/balance` | Số dư Binance | Live mode only |
| `/mode` | Xem / đổi trading mode | Muốn chuyển swing ↔ intraday |
| `/auto` | Bật/tắt auto trade | Quyết định bot tự đặt lệnh hay không |
| `/close` | Đóng tất cả vị thế | Cần thoát lệnh thủ công |
| `/stop` | Dừng khẩn cấp | Tình huống khẩn cấp |
| `/history` | 10 lệnh gần nhất | Xem kết quả giao dịch |
| `/health` | Trạng thái hệ thống | Kiểm tra bot có hoạt động không |
| `/cost` | Chi phí LLM + phí giao dịch | Theo dõi ngân sách |
| `/filter status` | ATR spike + DXY filter | Biết filter đang block không |

---

## Chi tiết từng lệnh

---

### `/start`

Hiển thị danh sách lệnh. Gõ sau khi khởi động bot lần đầu.

```
🤖 Gold Trading Bot active!

Commands:
/signal — trigger analysis
/status — current positions
...
```

---

### `/signal`

Kích hoạt 1 chu kỳ phân tích ngay lập tức (không chờ lịch).

**Luồng:**
1. Pre-filter ATR spike → nếu bị block, dừng sớm
2. Chạy LLM chain: Macro → HTF → MTF → LTF → Signal
3. Nếu signal ≠ HOLD: gửi tin nhắn kèm nút Approve/Reject (signal mode) hoặc tự đặt lệnh (auto mode)

**Lưu ý:** Tốn LLM API token (khoảng $0.01–$0.05 mỗi lần tùy provider)

---

### `/status`

Hiển thị trạng thái bot và các vị thế đang mở.

**Output mẫu:**
```
🤖 Bot Status — 14:32 VN

Mode:       SWING
Auto Trade: OFF ⛔
Trading:    PAPER 📄

Positions:
  BUY @ $3,245.00 | PnL: +$12.50
```

---

### `/balance`

Hiển thị số dư tài khoản Binance Futures.

- **Paper mode:** Trả về thông báo "no real balance", dùng `/status` thay thế
- **Live mode:** Hiển thị tổng số dư, lãi/lỗ chưa chốt, số tiền khả dụng

**Output mẫu (live):**
```
💰 Account Balance

Total:      $2,500.00
Unrealized: +$12.50
Available:  $2,200.00
```

---

### `/mode`

Xem hoặc đổi trading mode. **Áp dụng ngay lập tức** — scheduler và WebSocket được cập nhật mà không cần restart bot.

**Cú pháp:**
```
/mode                  → Xem mode hiện tại
/mode swing            → Đổi sang swing
/mode intraday         → Đổi sang intraday
/mode scalp            → Đổi sang scalp
```

**Output mẫu khi đổi mode:**
```
✅ Mode → intraday
Leverage: 5x | Risk: 1.0%/lệnh
Lịch: Phân tích mỗi 15 phút
```
```
✅ Mode → scalp
Leverage: 10x | Risk: 0.5%/lệnh
Lịch: Real-time (WebSocket 1m candle)
```

**So sánh các mode:**

| Mode | Phân tích | Leverage | Risk/lệnh | Phù hợp |
|------|----------|---------|-----------|---------|
| `swing` | Mỗi 240 phút | 3x | 1.5% | Người bận, ít theo dõi |
| `intraday` | Mỗi 15 phút | 5x | 1.0% | Theo dõi được trong ngày |
| `scalp` | Real-time (WebSocket 1m) | 10x | 0.5% | Kinh nghiệm, chú ý liên tục |

**Hành vi khi đổi mode:**
- `swing` / `intraday` → APScheduler job được reschedule ngay với interval mới; WebSocket dừng nếu đang chạy
- `scalp` → Interval job bị xóa khỏi scheduler; WebSocket 1m candle được khởi động ngay
- Vị thế đang mở **không bị ảnh hưởng**, chỉ signal tiếp theo mới áp dụng mode mới

---

### `/auto`

Bật/tắt chế độ tự động đặt lệnh.

**Cú pháp:**
```
/auto          → Xem trạng thái hiện tại
/auto on       → Bật auto trade
/auto off      → Tắt auto trade
```

**Hai chế độ hoạt động:**

| Chế độ | Hành vi |
|--------|---------|
| `AUTO OFF` (mặc định) | Bot phân tích → gửi signal kèm nút **[✅ Approve] [❌ Reject]** → chờ bạn quyết định |
| `AUTO ON` | Bot phân tích → tự đặt lệnh ngay, không hỏi → gửi thông báo sau khi đặt |

**Bảo vệ Live mode:** Nếu đang ở live (không phải paper), `/auto on` sẽ yêu cầu xác nhận:
```
⚠️ This enables LIVE auto trading with real money!
Send /auto on confirm to proceed.
```
Phải gõ đúng `/auto on confirm` mới kích hoạt.

---

### `/close`

Đóng tất cả vị thế đang mở (market order).

- **Paper mode:** Đóng tất cả paper_orders, tính PnL theo giá Binance hiện tại
- **Live mode:** Gửi lệnh market close_position lên Binance Futures

**Dùng khi:** Cần thoát khẩn cấp, cuối ngày muốn không giữ qua đêm.

---

### `/stop`

Dừng khẩn cấp — kích hoạt circuit breaker.

**Hành động:**
1. Set `circuit_breaker = true` trong DB
2. Set `auto_trade = false`
3. Bot dừng đặt lệnh mới

**Lệnh này KHÔNG:**
- Đóng vị thế đang mở (dùng `/close` để đóng)
- Tắt Telegram bot (bot vẫn chạy và nhận lệnh)
- Tắt phân tích market (vẫn phân tích, chỉ không đặt lệnh)

**Để resume:** Gõ `/auto on`

**Output:**
```
🚨 EMERGENCY STOP ACTIVATED

Circuit breaker set. Auto trade disabled.
Use /auto on to resume.
```

---

### `/history`

Hiển thị 10 lệnh gần nhất đã đóng, kèm PnL.

**Output mẫu:**
```
📈 Trade History (last 10)

✅ BUY $3,245→$3,268 | +$23.00
❌ SELL $3,290→$3,305 | -$15.00
✅ BUY $3,210→$3,245 | +$35.00
...
```

---

### `/health`

Báo cáo trạng thái chi tiết: hệ thống, components, trading info.

**Output mẫu:**
```
🏥 Health Report — 14:30 VN

System:
  Uptime: 3h 42m 15s
  Memory: 124 MB
  CPU:    2.3%

Components:
  Binance:   ✅  45ms
  Db:        ✅  0.3ms
  Scheduler: ✅

Trading:
  Mode:        swing
  Paper:       ON
  Auto trade:  OFF
  Last signal: BUY (conf: 72%)

Overall: ✅ All OK
```

**Heartbeat tự động:** Bot gửi cảnh báo qua Telegram nếu component nào bị lỗi (không cần gõ lệnh). Cấu hình trong `.env`:
```bash
HEALTH_VERBOSE=false       # true = gửi tin mỗi 5 phút dù OK
HEALTH_INTERVAL_MIN=5      # kiểm tra mỗi 5 phút
HEALTH_ALERT_AFTER_MIN=15  # cảnh báo sau 15 phút mất heartbeat
```

---

### `/cost`

Theo dõi chi phí LLM API, phí giao dịch, infra.

**Cú pháp:**

```
/cost                              → Chi phí hôm nay
/cost today                        → Chi phí hôm nay
/cost mtd                          → Chi phí từ đầu tháng
/cost from 2026-03-01              → Từ ngày đó đến hôm nay
/cost from 2026-03-01 to 2026-03-15  → Khoảng thời gian cụ thể
/cost llm                          → Chi tiết theo từng LLM model (MTD)
/cost set vps 10                   → Ghi nhận VPS cost $10/tháng
/cost set domain 1.5               → Ghi nhận domain cost $1.5/tháng
/cost export                       → Tải xuống file CSV
```

**Output mẫu (`/cost mtd`):**
```
💰 Cost Report
Period: 2026-03-01 → 2026-03-28

LLM API:    $8.4200
Trade Fees: $1.2300
Total:      $9.6500
LLM Calls:  340

By Model:
  claude-sonnet-4-6: 120 calls → $6.8400
  deepseek-chat: 180 calls → $1.2400
  gpt-4o-mini: 40 calls → $0.3400
```

**Output mẫu (`/cost llm`):**
```
📡 LLM Breakdown — MTD
Period: 2026-03-01 → 2026-03-28

  claude-sonnet-4-6: 120 calls | 1,200,000 in / 240,000 out | $6.8400
  deepseek-chat: 180 calls | 900,000 in / 180,000 out | $1.2400

Total LLM: $8.0800
```

---

### `/filter status`

Kiểm tra trạng thái các bộ lọc đang hoạt động.

**Output mẫu:**
```
🔍 Filter Status — 14:32 VN

ATR Spike Guard:
  Ratio: 1.52x  ✅

DXY (EURUSD proxy): DXY_NEUTRAL

Cycle: March 2026 — NEUTRAL seasonal
Session: Phiên London (14:00 VN)

Status: ✅ All filters PASS
```

**Khi bị block:**
```
🔍 Filter Status — 19:45 VN

ATR Spike Guard:
  Ratio: 2.87x  ❌  (threshold 2.5x)

Status: ⚠️ ATR blocked (2.9x)
```

---

## Inline Keyboard — Approve / Reject Signal

Khi bot gửi signal (signal mode: `/auto off`), tin nhắn có 2 nút:

```
📊 XAUUSD Signal — SWING

Action:     BUY
Entry:      $3,245.50
SL:         $3,228.00  (-0.54%)
TP1:        $3,265.00  (+0.60%)
TP2:        $3,285.00  (+1.22%)
TP3:        $3,315.00  (+2.14%)
Confidence: 72%
Bias:       BUY-ONLY

Reasoning:
HTF bullish structure intact, MTF pullback complete...

[✅ Approve]  [❌ Reject]
```

| Nút | Hành động |
|-----|----------|
| **✅ Approve** | Đặt lệnh ngay (paper hoặc live tùy mode), tin nhắn cập nhật "Approved — executing..." |
| **❌ Reject** | Bỏ qua signal, tin nhắn cập nhật "Rejected" |

**Lưu ý:** Nút chỉ hoạt động 1 lần. Nếu signal hết hạn (giá đã đi quá xa), nên Reject.

---

## Thông báo tự động (không cần gõ lệnh)

Bot tự gửi các thông báo sau:

| Sự kiện | Thông báo |
|---------|----------|
| Bot khởi động | `🟢 Bot Started — 14:30 VN \| Mode: SWING \| Paper: ON` |
| Bot dừng | `🔴 Bot Stopped — 14:35 VN` |
| Signal mới | Tin nhắn signal kèm nút Approve/Reject |
| Auto trade thực hiện | `🤖 Auto Trade Executed: BUY @ $3,245.00` |
| Paper SL hit | `🔴 SL Hit — Paper Trade \| PnL: -$15.00` |
| Paper TP hit | `✅ TP3 Hit — Paper Trade \| PnL: +$45.00` |
| ATR spike | `⚡ Volatility Alert — ATR spike 2.9x \| Auto trade paused` |
| Component lỗi | `⚠️ WARNING — Binance API: ❌ Connection timeout` |
| Circuit breaker | `🚨 Circuit breaker triggered — daily loss limit hit` |

---

## Cấu hình BotFather (recommended)

Đăng ký danh sách lệnh với BotFather để Telegram hiển thị autocomplete:

1. Nhắn `/setcommands` với **@BotFather**
2. Chọn bot của bạn
3. Paste nội dung sau:

```
signal - Trigger phân tích ngay lập tức
status - Xem vị thế + trạng thái bot
balance - Số dư tài khoản Binance
mode - Xem hoặc đổi trading mode
auto - Bật tắt auto trade
close - Đóng tất cả vị thế
stop - Dừng khẩn cấp
history - 10 lệnh gần nhất
health - Kiểm tra sức khỏe hệ thống
cost - Chi phí LLM và phí giao dịch
filter - Trạng thái ATR và correlation filter
```
