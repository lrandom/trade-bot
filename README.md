# Gold Trading Bot — XAUUSDT Binance Futures

Bot giao dịch vàng tự động, sử dụng Claude LLM để ra quyết định. Hỗ trợ 4 LLM provider, 3 trading mode, paper trade an toàn trước khi live.

---

## Yêu cầu hệ thống

- Python 3.11+
- macOS / Linux (Windows chưa test)
- Kết nối internet ổn định

---

## Bước 1 — Chuẩn bị API keys

Cần chuẩn bị **ít nhất 3 key** trước khi chạy:

### 1.1 Telegram Bot Token

1. Mở Telegram → tìm **@BotFather**
2. Gõ `/newbot` → đặt tên → đặt username
3. BotFather trả về token dạng: `7123456789:AAFxxx...`

### 1.2 Telegram Chat ID

1. Mở bot vừa tạo → gõ `/start`
2. Truy cập URL (thay `<TOKEN>` bằng token thật):
   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
3. Tìm `"chat" → "id"` trong JSON (số nguyên)

### 1.3 Binance Testnet (dùng để test, không mất tiền thật)

1. Truy cập **testnet.binancefuture.com**
2. Đăng ký → vào **API Management** → Generate
3. Lưu lại **API Key** và **Secret Key**

> Chỉ cần Binance Live khi bạn sẵn sàng trade thật (bước cuối cùng)

### 1.4 LLM API Key (chọn 1 trong 4)

| Provider                 | Trang lấy key                     | Env var             |
| ------------------------ | --------------------------------- | ------------------- |
| **Anthropic (Claude)** ⭐ | console.anthropic.com → API Keys  | `ANTHROPIC_API_KEY` |
| OpenAI (GPT)             | platform.openai.com → API Keys    | `OPENAI_API_KEY`    |
| Google (Gemini)          | aistudio.google.com → Get API Key | `GEMINI_API_KEY`    |
| Deepseek                 | platform.deepseek.com → API Keys  | `DEEPSEEK_API_KEY`  |

### 1.5 Macro Data (optional — có thể bỏ qua lúc đầu)

| Service  | Link đăng ký                         | Ghi chú              |
| -------- | ------------------------------------ | -------------------- |
| FRED API | fred.stlouisfed.org/docs/api/api_key | Free, không giới hạn |
| NewsAPI  | newsapi.org/register                 | Free 100 req/day     |

---

## Bước 2 — Cài đặt

```bash
# Clone hoặc copy project về máy
cd trade-gold

# Tạo virtual environment
python3 -m venv .venv
source .venv/bin/activate   # macOS/Linux
# .venv\Scripts\activate    # Windows

# Cài dependencies
pip install -r requirements.txt
```

---

## Bước 3 — Cấu hình .env

```bash
# Copy file mẫu
cp .env.example .env

# Mở file và điền thông tin
nano .env    # hoặc dùng bất kỳ text editor nào
```

Điền các trường **bắt buộc**:

```bash
TELEGRAM_BOT_TOKEN=7123456789:AAFxxx...   # token từ BotFather
TELEGRAM_CHAT_ID=123456789                 # chat ID của bạn
BINANCE_API_KEY=xxx                        # testnet key
BINANCE_SECRET_KEY=xxx
BINANCE_TESTNET=true                       # LUÔN true lúc đầu
ANTHROPIC_API_KEY=sk-ant-xxx               # hoặc key của provider khác
LLM_PROVIDER=anthropic                     # anthropic | openai | gemini | deepseek
```

Giữ nguyên các giá trị an toàn mặc định:

```bash
PAPER_TRADE=true      # KHÔNG đổi cho đến khi bạn sẵn sàng
AUTO_TRADE=false      # bot chỉ gửi signal, không tự đặt lệnh
TRADING_MODE=swing    # bắt đầu với swing (ít lệnh, dễ theo dõi)
```

---

## Bước 4 — Chạy thử

### 4.1 Dry run (không tốn Telegram/DB, chỉ test LLM prompt)

```bash
python main.py --dry-run
```

Kết quả mong đợi:
```
=== DRY RUN | mode=swing ===
Action:     BUY
Entry:      3245.50
SL:         3228.00
TP1/2/3:    3265.00 / 3285.00 / 3315.00
Confidence: 72%
...
=== DRY RUN COMPLETE ===
```

> Nếu có lỗi ở bước này → kiểm tra `ANTHROPIC_API_KEY` và kết nối internet

### 4.2 Paper Trade (recommended — chạy 2-4 tuần trước khi live)

```bash
python main.py
```

Bot sẽ:
- Gửi tin nhắn Telegram: `🟢 Bot Started — 14:30 VN`
- Phân tích thị trường theo lịch (swing: mỗi 4h)
- Gửi signal kèm nút **[✅ Approve] [❌ Reject]**
- Ghi kết quả vào SQLite (file `data/bot.db`)
- Không đặt lệnh thật trên Binance

---

## Telegram Commands

Sau khi bot chạy, gửi lệnh trong Telegram chat:

| Lệnh                          | Chức năng                              |
| ----------------------------- | -------------------------------------- |
| `/signal`                     | Trigger phân tích ngay lập tức         |
| `/status`                     | Xem vị thế đang mở + PnL               |
| `/balance`                    | Số dư tài khoản (live only)            |
| `/mode swing`                 | Đổi mode (swing / intraday / scalp)    |
| `/auto on`                    | Bật auto trade (cần xác nhận nếu live) |
| `/auto off`                   | Tắt auto trade                         |
| `/close`                      | Đóng tất cả vị thế                     |
| `/stop`                       | **Emergency stop** — dừng khẩn cấp     |
| `/history`                    | 10 lệnh gần nhất + PnL                 |
| `/health`                     | Kiểm tra trạng thái bot                |
| `/cost`                       | Chi phí LLM tháng này                  |
| `/cost 2026-03-01 2026-03-31` | Chi phí trong khoảng thời gian         |
| `/filter status`              | Trạng thái ATR + DXY filter            |

---

## Chọn Trading Mode

| Mode       | Phân tích  | Leverage | Cho ai                          |
| ---------- | ---------- | -------- | ------------------------------- |
| `swing`    | 4h/ngày    | 5x       | Người bận, ít rủi ro hơn        |
| `intraday` | 15-30 phút | 10x      | Theo dõi được trong ngày        |
| `scalp`    | Real-time  | 10-20x   | Kinh nghiệm, chú ý thường xuyên |

```bash
# Đổi mode qua Telegram
/mode intraday

# Hoặc đổi trong .env trước khi chạy
TRADING_MODE=intraday
```

---

## Chọn LLM Provider

Khuyến nghị mặc định:

```bash
# .env
LLM_PROVIDER_SWING=anthropic
LLM_MODEL_SWING=claude-sonnet-4-6

LLM_PROVIDER_INTRADAY=deepseek
LLM_MODEL_INTRADAY=deepseek-chat

LLM_PROVIDER_SCALP=openai
LLM_MODEL_SCALP=gpt-4o-mini
```

Chi phí ước tính mỗi tháng:

| Mode                 | Provider      | Chi phí/tháng |
| -------------------- | ------------- | ------------- |
| Swing (6 cycle/ngày) | Claude sonnet | ~$10          |
| Intraday             | Deepseek V3   | ~$1           |
| Scalp                | GPT-4o-mini   | ~$6           |

---

## Vốn tối thiểu

| Vốn        | Leverage | Target/ngày | Rủi ro         |
| ---------- | -------- | ----------- | -------------- |
| $500       | 20x      | $15-25      | ⚠️ Cao          |
| **$1,500** | **10x**  | **$30-50**  | **✅ Hợp lý**   |
| $3,000     | 10x      | $50-100     | ✅ An toàn      |
| $5,000     | 5x       | $50-100     | ✅ Conservative |

> Gold thường dao động $100/ngày. Ngày volatile có thể đạt $400 — filter sẽ tự pause auto trade khi ATR spike.

---

## Lộ trình lên Live

```
[1] Dry run         → Kiểm tra LLM prompt, JSON format đúng
[2] Paper trade     → Chạy 2-4 tuần, đạt: win rate ≥50%, profit factor ≥1.3
[3] Live micro      → PAPER_TRADE=false + MIN_POSITION_USD=10 (lệnh tối đa $10)
[4] Live normal     → Tăng size sau 1 tuần không lỗi
```

**Lên live:**
```bash
# .env
PAPER_TRADE=false
BINANCE_TESTNET=false   # cần Binance Live API key
MIN_POSITION_USD=10     # bắt đầu với lệnh nhỏ
```

---

## Deploy trên VPS (24/7)

```bash
# Cài systemd service
sudo nano /etc/systemd/system/gold-bot.service
```

```ini
[Unit]
Description=Gold Trading Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/trade-gold
ExecStart=/home/ubuntu/trade-gold/.venv/bin/python main.py
Restart=always
RestartSec=10s

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable gold-bot
sudo systemctl start gold-bot
sudo journalctl -u gold-bot -f    # xem log realtime
```

---

## Xem log

```bash
# Log realtime
tail -f logs/bot.log

# Log theo ngày
ls logs/
```

---

## Cấu trúc thư mục

```
trade-gold/
├── main.py              # Entry point
├── .env                 # API keys (KHÔNG commit)
├── .env.example         # Template
├── requirements.txt
├── schema.sql           # SQLite schema
├── bot/
│   ├── config.py        # Settings
│   ├── orchestrator.py  # Main loop
│   ├── data/            # Binance data, indicators, macro
│   ├── llm/             # LLM engine + 4 providers
│   ├── risk/            # Risk management
│   ├── modes/           # Trading mode configs
│   ├── trader/          # Paper + Live execution
│   ├── telegram/        # Bot commands
│   ├── filters/         # Volatility + correlation
│   ├── health/          # Health monitor
│   └── cost/            # Cost tracking
├── data/                # SQLite DB (auto-created)
└── logs/                # Log files (auto-created)
```

---

## Troubleshooting

**Bot không nhận lệnh Telegram:**
- Kiểm tra `TELEGRAM_CHAT_ID` đúng chưa (phải là số nguyên của *bạn*, không phải bot)
- Gõ `/start` trong chat với bot trước

**LLM error / timeout:**
- Kiểm tra API key còn hạn dùng không
- Thử `python main.py --dry-run` để test riêng LLM

**Binance connection error:**
- Kiểm tra `BINANCE_TESTNET=true` nếu đang dùng testnet key
- Testnet thỉnh thoảng bảo trì — thử lại sau vài phút

**ATR filter block liên tục:**
- Gold đang volatile bất thường (tin tức lớn)
- Kiểm tra `/filter status` trong Telegram
- Đây là tính năng bảo vệ — để yên, bot sẽ tự resume sau khi thị trường ổn định
