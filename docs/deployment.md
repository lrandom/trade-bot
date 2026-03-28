# Deployment Guide

## Chạy trên máy cá nhân

Hoàn toàn có thể chạy bot ở máy cá nhân, đặc biệt trong giai đoạn test và paper trade.

### Ưu điểm
- Không tốn chi phí VPS
- Dễ debug, xem log trực tiếp

### Hạn chế

| Vấn đề | Ảnh hưởng |
|--------|-----------|
| Máy tắt / ngủ | Bot dừng, bỏ lỡ signal và không monitor vị thế đang mở |
| Mất điện / mạng | Vị thế mở không có ai quản lý |
| Scalp mode | Cần chạy 24/7, không phù hợp máy cá nhân |

### Khuyến nghị theo mode

| Mode | Máy cá nhân | Ghi chú |
|------|-------------|---------|
| `swing` | ✅ OK | 4h/lần — miss 1-2 signal không ảnh hưởng nhiều |
| `intraday` | ⚠️ Rủi ro | Cần máy chạy liên tục trong giờ giao dịch |
| `scalp` | ❌ Không nên | WebSocket cần 24/7, mất kết nối là mất tiền |

### Chạy ổn định trên Mac

```bash
# Ngăn Mac ngủ khi chạy bot
caffeinate -i python main.py

# Chạy background + log ra file
nohup python main.py > logs/bot.log 2>&1 &

# Xem log realtime
tail -f logs/bot.log

# Dừng bot đang chạy background
kill $(cat bot.pid)   # nếu có lưu PID
# hoặc
pkill -f "python main.py"
```

---

## Lên production (VPS)

Khi muốn chạy 24/7 ổn định, dùng VPS giá rẻ là đủ cho bot này.

### Yêu cầu tối thiểu
- CPU: 1 vCPU
- RAM: 512 MB (1 GB khuyến nghị)
- Disk: 5 GB
- OS: Ubuntu 22.04

### Nhà cung cấp gợi ý

| Provider | Giá | Ghi chú |
|----------|-----|---------|
| Oracle Cloud Free Tier | **Miễn phí** | Always Free VM.Standard.A1 (4 vCPU / 24 GB RAM) |
| DigitalOcean Droplet | ~$5/tháng | Đơn giản, dễ dùng |
| Vultr | ~$5/tháng | Tương tự DigitalOcean |
| Hetzner | ~$4/tháng | Rẻ hơn, server EU |

### Chạy với systemd (khuyến nghị)

Tạo file `/etc/systemd/system/trade-bot.service`:

```ini
[Unit]
Description=Gold Trading Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/trade-gold
ExecStart=/home/ubuntu/trade-gold/.venv/bin/python main.py
Restart=on-failure
RestartSec=10
EnvironmentFile=/home/ubuntu/trade-gold/.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable trade-bot
sudo systemctl start trade-bot

# Xem log
sudo journalctl -u trade-bot -f
```

### Chạy với screen (đơn giản hơn)

```bash
screen -S bot
python main.py

# Detach: Ctrl+A, D
# Reattach:
screen -r bot
```
