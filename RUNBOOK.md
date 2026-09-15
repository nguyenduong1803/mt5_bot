# Hướng dẫn triển khai MT5 Webhook Bot (Runbook)

Tài liệu này hướng dẫn cách deploy hệ thống MT5 Webhook Bot (chạy qua Docker + Wine) lên một Server/VPS Linux (Ubuntu) mới.

> [!IMPORTANT]
> **Vấn đề cốt lõi:** Các bộ cài đặt (`.exe`) gần đây của MetaQuotes chặn hoàn toàn việc cài đặt qua Wine trên Linux để chống Bot. Do đó, quy trình dưới đây sẽ **bỏ qua bước cài đặt** và sử dụng phương pháp **"Copy thư mục cài sẵn từ Windows"** (Portable mode).

---

## Bước 1: Chuẩn bị Server
Yêu cầu hệ thống:
- Ubuntu 20.04 hoặc 22.04.
- Đã cài đặt **Docker** và **Docker Compose**.
- RAM tối thiểu 2GB (Khuyến nghị 4GB vì Wine và MT5 khá ngốn RAM).

## Bước 2: Chuẩn bị mã nguồn và Cấu hình
1. Clone mã nguồn bot về server.
2. Copy file `.env.example` thành `.env` và điền Telegram (không còn `MT5_PASSWORD_*`):
   ```env
   # Ví dụ:
   TELEGRAM_BOT_TOKEN=123456789:AA...
   TELEGRAM_CHAT_ID=-1001234567890
   ```
3. Cấu hình file `config.json` (xem `config.example.json`):
   - Mỗi strategy có `accounts.<key>` với `magic` và `mt5` (`l` = login, `p` = password, `server`).
   - Mật khẩu MT5 nằm trong `mt5.p` của từng account — **không** đặt trong `.env`.
   - Thiết lập `dryRun`: `true` (nếu chỉ muốn test) hoặc `false` (nếu muốn trade thật).
   - Đảm bảo **KHÔNG** set hardcode `terminal_path` trong config.json (để bot tự động tìm đường dẫn MT5 trong volume).

## Bước 3: Đưa dữ liệu MT5 từ Windows lên Server
Đây là bước quan trọng nhất để bypass Anti-Tamper của MetaQuotes.

1. **Trên máy Windows cá nhân:**
   - Cài đặt sẵn MetaTrader 5 (từ sàn Exness, XM,...).
   - Đăng nhập thử vào tài khoản để chắc chắn MT5 hoạt động.
   - Nén toàn bộ thư mục cài đặt (Thường ở `C:\Program Files\MetaTrader 5\`) thành file `mt5.zip`.

2. **Trên Ubuntu Server:**
   - Upload file `mt5.zip` lên server (bằng `scp`, Google Drive, wget...).
   - Khởi động Docker container lần đầu để tạo các volume cần thiết:
     ```bash
     docker compose up -d
     ```
   - Giải nén và copy dữ liệu MT5 vào thẳng trong container:
     ```bash
     # Giải nén
     sudo apt install -y unzip
     unzip mt5.zip -d /tmp/mt5_extracted/
     
     # Tạo thư mục đích trong container
     docker exec mt5_webhook_bot mkdir -p "/root/.wine/drive_c/Program Files/MetaTrader 5/"
     
     # Copy toàn bộ file sang container
     docker cp /tmp/mt5_extracted/. mt5_webhook_bot:"/root/.wine/drive_c/Program Files/MetaTrader 5/"
     ```
   - Khởi động lại container để script tự động nhận diện MT5:
     ```bash
     docker compose restart
     ```

---

## Bước 4: Đăng nhập & Cấu hình MT5 qua VNC
Vì đây là container Docker headless, bạn cần dùng VNC trên trình duyệt để thao tác với giao diện của MT5.

1. Mở trình duyệt web và truy cập: **`http://<IP_CỦA_SERVER>:6080/vnc.html`**
2. Nhập mật khẩu mặc định: `mt5vnc`
3. Trong giao diện MT5 hiện ra:
   - Đăng nhập vào tài khoản Broker của bạn.
   - Tích chọn **"Keep personal settings and data at startup"**.
   - > [!WARNING]
     > Nhìn lên thanh công cụ trên cùng, bạn **bắt buộc phải click vào nút "Algo Trading"** để nó chuyển sang màu **xanh lá**. Nếu không bật, API sẽ trả về lỗi `AutoTrading disabled by client`.
   - Tắt tất cả các biểu đồ và popup quảng cáo không cần thiết để tiết kiệm RAM.

---

## Bước 5: Kiểm thử (Verification)
Sau khi nút Algo Trading đã xanh lá, hãy đứng ở màn hình terminal của Ubuntu và chạy lệnh Test Webhook:

```bash
curl -X POST http://localhost:8000/api/order \
  -H "Content-Type: application/json" \
  -d '{
    "strategy": "eth_strategy",
    "symbol": "ETHUSDm",
    "order_id": "openLong",
    "price": 2500.0,
    "order_ratio": 1.0,
    "timenow": "2024-01-01T00:00:00Z"
  }'
```

- Response thành công luôn **HTTP 200** với body dạng `{ "strategy": "...", "results": [ { "account": "...", ... } ] }`.
  Thêm `"account": "exness_main"` để chỉ chạy một account; bỏ field đó = fan-out mọi account `enabled`.
- Lỗi MT5 trên một/nhiều account vẫn trả **200** (`results[].success=false`) — theo dõi qua Telegram/logs, không qua HTTP status.
- Nếu `dryRun=false` trong `config.json`, hệ thống sẽ lập tức bắn lệnh thật lên MT5.
- Bạn có thể mở VNC để xem lệnh hiển thị trong tab **Trade** ở dưới cùng.

## TroubleShooting
- **Lỗi `IPC initialize failed`**: Container chưa tìm thấy `terminal64.exe` trong đường dẫn, hãy kiểm tra lại Bước 3 xem bạn copy file vào đúng chưa.
- **Lỗi `list object has no attribute visible`**: Xảy ra nếu proxy parse lỗi JSON, đảm bảo source code của `mt5_proxy/server.py` là bản mới nhất.
- **VNC báo `Failed to connect`**: X11Vnc bị treo. Khởi động lại bằng lệnh `docker compose restart`.
