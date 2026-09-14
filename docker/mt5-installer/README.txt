# Thư mục này chứa file installer MT5 của broker bạn.
#
# CÁCH SỬ DỤNG:
#   1. Download file installer MT5 từ website broker của bạn:
#      - Exness:  https://www.exness.com/download-mt5/
#      - XM:      https://www.xm.com/mt5
#      - FTMO:    https://trading.ftmo.com/
#      - Hoặc bất kỳ broker nào khác hỗ trợ MT5
#
#   2. Đặt file installer vào thư mục này và đổi tên thành: mt5setup.exe
#      Ví dụ:
#        mv ~/Downloads/ExnessSetup.exe docker/mt5-installer/mt5setup.exe
#        mv ~/Downloads/XMSetup.exe     docker/mt5-installer/mt5setup.exe
#
#   3. Rebuild Docker image:
#        docker compose build
#
# LƯU Ý:
#   - File .exe này KHÔNG được commit lên Git (đã thêm vào .gitignore)
#   - Mỗi broker có MT5 installer riêng với server list riêng
#   - Nếu bạn dùng nhiều broker, bạn cần build nhiều image khác nhau
#
# Nếu chỉ muốn test webhook (dry_run=true) mà không cần MT5 thật,
# bạn có thể bỏ qua bước này — bot vẫn sẽ start và nhận webhook.
