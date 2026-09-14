# 🐳 Docker & Ubuntu Deployment Guide

Hướng dẫn này giải thích cách chạy **MT5 TradingView Webhook Bot** trong Docker trên Ubuntu/VPS — thay thế hoàn toàn cho `run.bat` trên Windows.

---

## 📋 Yêu cầu

| Phần mềm | Phiên bản | Lệnh cài đặt |
|----------|-----------|--------------|
| Ubuntu | 22.04 LTS (Jammy) | — |
| Docker | ≥ 24.0 | Xem bên dưới |
| Docker Compose | v2 (plugin) | `sudo apt install docker-compose-plugin` |
| RAM | ≥ 2 GB | MT5 + Wine tốn ~800 MB |
| Disk | ≥ 5 GB | Wine prefix + MT5 install |

### Cài Docker trên Ubuntu

```bash
# Cài Docker Engine chính thức
curl -fsSL https://get.docker.com | sudo sh

# Thêm user hiện tại vào group docker (tránh phải dùng sudo mỗi lần)
sudo usermod -aG docker $USER

# Áp dụng ngay (hoặc logout/login lại)
newgrp docker

# Kiểm tra
docker --version
docker compose version
```

---

## 🏗️ Kiến trúc

```
Docker Container (Ubuntu 22.04)
│
├── Xvfb :99          ← Virtual display (MT5 cần GUI)
│
├── Wine (64-bit)
│   ├── MT5 terminal64.exe   ← Kết nối broker
│   └── Python 3.11 (Windows build)
│       └── MetaTrader5 package  ← Windows-only, chạy được qua Wine
│
└── FastAPI / uvicorn  → port 8000
    └── /health
    └── /api/order     ← TradingView webhook
```

> **Tại sao cần Wine?** Package `MetaTrader5` Python chỉ có wheel cho Windows (`win_amd64`). Không có Linux build. Wine cho phép chạy `terminal64.exe` và Python for Windows cùng nhau bên trong Linux container.

---

## 🚀 Hướng dẫn deploy từng bước

### Bước 1: Clone project

```bash
git clone <repo-url> mt5_bot
cd mt5_bot
```

### Bước 2: Chuẩn bị config

```bash
# Copy và điền thông tin config
cp config.example.json config.json
nano config.json   # Điền login, server, magic, v.v.

# Copy và điền .env
cp .env.example .env
nano .env          # Điền MT5_PASSWORD_<STRATEGY>, TELEGRAM_BOT_TOKEN
```

### Bước 3: Lấy MT5 installer từ broker

> ⚠️ **Bắt buộc nếu muốn trade thật.** Có thể bỏ qua nếu chỉ test webhook với `dry_run=true`.

1. Vào website broker, download file installer MT5:
   - **Exness**: https://www.exness.com/download-mt5/ → `ExnessSetup.exe`
   - **XM**: https://www.xm.com/mt5 → `XMSetup.exe`  
   - **FTMO**: Tìm trong trading platform của FTMO
   - **Broker khác**: Tìm mục "Download MT5" trên website broker

2. Copy vào project và đổi tên:
   ```bash
   cp ~/Downloads/ExnessSetup.exe docker/mt5-installer/mt5setup.exe
   # HOẶC
   cp ~/Downloads/XMSetup.exe docker/mt5-installer/mt5setup.exe
   ```

### Bước 4: Build và chạy

```bash
# Cách nhanh — dùng script có sẵn
chmod +x run.sh
./run.sh

# HOẶC thủ công
docker compose build        # Lần đầu: ~15-20 phút (phải download Wine, Python)
docker compose up -d        # Start nền
docker compose logs -f      # Xem logs
```

### Bước 5: Kiểm tra hoạt động

```bash
# Health check
curl http://localhost:8000/health
# → {"status": "ok"}

# Test webhook (dry_run)
curl -X POST http://localhost:8000/api/order \
  -H "Content-Type: application/json" \
  -d '{
    "strategy": "eth_strategy_exness",
    "symbol": "ETHUSD",
    "order_id": "buy",
    "timenow": "2024-01-01T00:00:00Z"
  }'
```

---

## 📁 Cấu trúc file mới

```
mt5_bot/
├── Dockerfile              ← Build image với Wine + MT5
├── docker-compose.yml      ← Orchestration
├── run.sh                  ← Script khởi động (Linux, thay run.bat)
├── docker/
│   ├── entrypoint.sh       ← Startup sequence trong container
│   └── mt5-installer/
│       ├── README.txt      ← Hướng dẫn
│       └── mt5setup.exe    ← ⚠️ Bạn phải tự cung cấp (không có trong git)
├── app/                    ← Code (không đổi)
├── config.json             ← Bạn tạo từ config.example.json
└── .env                    ← Bạn tạo từ .env.example
```

---

## 🔧 Quản lý container

```bash
# Xem trạng thái
docker compose ps

# Xem logs realtime
docker compose logs -f

# Xem logs của ngày hôm nay
docker compose logs --since 24h

# Restart bot (không rebuild)
docker compose restart

# Dừng bot
docker compose down

# Dừng và xóa hết data Wine (phải login lại MT5)
docker compose down -v

# Rebuild sau khi sửa code
docker compose build && docker compose up -d
```

---

## 🌐 Expose ra internet (TradingView webhook)

TradingView cần gửi webhook từ internet vào bot. Có 2 cách:

### Cách 1: Dùng IP public của VPS (khuyến nghị)

```bash
# Mở port 8000 trên firewall
sudo ufw allow 8000/tcp

# Webhook URL trong TradingView:
# http://<VPS-IP>:8000/api/order
```

### Cách 2: Dùng Nginx reverse proxy + domain (bảo mật hơn)

```nginx
# /etc/nginx/sites-available/mt5bot
server {
    listen 80;
    server_name yourdomain.com;

    location /api/order {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /health {
        proxy_pass http://localhost:8000;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/mt5bot /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# Thêm HTTPS với Let's Encrypt (khuyến nghị)
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d yourdomain.com
```

---

## 🐛 Troubleshooting

### MT5 không kết nối được

```bash
# Xem log chi tiết
docker compose logs mt5bot | grep -i "mt5\|error\|wine"

# Vào trong container kiểm tra
docker exec -it mt5_webhook_bot bash

# Kiểm tra MT5 process trong Wine
wine tasklist | grep terminal
```

**Nguyên nhân thường gặp:**
- `terminal64.exe` không tìm thấy → rebuild sau khi thêm installer
- MT5 cần GUI để login lần đầu → volume `mt5_wine_data` bị xóa

### Wine lỗi khi build

```bash
# Build với output chi tiết hơn
docker compose build --progress=plain 2>&1 | tee build.log
```

### Port 8000 bị chiếm

```bash
# Đổi port trong docker-compose.yml
ports:
  - "8001:8000"   # Dùng port 8001 ngoài host
```

### Container bị OOM (out of memory)

```bash
# Kiểm tra memory usage
docker stats mt5_webhook_bot

# Tăng RAM trên VPS, hoặc thêm swap:
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
```

---

## 🔄 Update code

```bash
git pull
docker compose build
docker compose up -d
```

> ⚠️ **Lưu ý:** Mỗi lần rebuild, volume `mt5_wine_data` vẫn được giữ nguyên — **không cần login lại MT5**.

---

## 🔒 Bảo mật

- Không commit `.env` và `config.json` lên git (đã có trong `.gitignore`)
- Dùng firewall (ufw) chỉ cho phép IP TradingView nếu cần
- TradingView IP ranges: https://www.tradingview.com/support/solutions/43000529348

```bash
# Chỉ cho phép TradingView (ví dụ)
sudo ufw allow from 52.89.214.238 to any port 8000
sudo ufw allow from 34.212.75.30 to any port 8000
# ... thêm các IP khác từ tài liệu TradingView
```
