#!/bin/bash
# =============================================================================
# run.sh — Linux/Ubuntu equivalent của run.bat
# =============================================================================
# Dùng để khởi động MT5 Webhook Bot trên Ubuntu/Linux qua Docker.
# Yêu cầu: docker và docker compose đã được cài đặt.
#
# CÁCH DÙNG:
#   chmod +x run.sh
#   ./run.sh
# =============================================================================

set -e

# Màu sắc
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

echo -e "${BLUE}"
echo "  ╔═══════════════════════════════════════════════╗"
echo "  ║     MT5 TradingView Webhook Bot               ║"
echo "  ║     Docker + Wine + Ubuntu                    ║"
echo "  ╚═══════════════════════════════════════════════╝"
echo -e "${NC}"

# --------------------------------------------------------------------------
# Kiểm tra prerequisites
# --------------------------------------------------------------------------
if ! command -v docker &>/dev/null; then
    log_error "Docker chưa được cài đặt."
    log_error "Cài Docker: https://docs.docker.com/engine/install/ubuntu/"
    exit 1
fi

if ! docker compose version &>/dev/null 2>&1; then
    log_error "Docker Compose v2 chưa được cài đặt."
    log_error "Cài: sudo apt install docker-compose-plugin"
    exit 1
fi

if [ ! -f config.json ]; then
    log_error "Không tìm thấy config.json!"
    log_error "Copy và sửa file mẫu:"
    log_error "  cp config.example.json config.json"
    log_error "  nano config.json"
    exit 1
fi

if [ ! -f .env ]; then
    log_warn "Không tìm thấy file .env — mật khẩu MT5 sẽ bị thiếu."
    log_warn "Copy và điền thông tin:"
    log_warn "  cp .env.example .env"
    log_warn "  nano .env"
    echo ""
fi

# --------------------------------------------------------------------------
# Kiểm tra MT5 installer (warning nếu thiếu)
# --------------------------------------------------------------------------
if ! ls docker/mt5-installer/*.exe 2>/dev/null | grep -q .; then
    log_warn "Không tìm thấy MT5 installer trong docker/mt5-installer/"
    log_warn "Bot sẽ chạy nhưng không thể kết nối MT5 terminal."
    log_warn "Xem docker/mt5-installer/README.txt để biết cách setup."
    echo ""
fi

# --------------------------------------------------------------------------
# Build image (nếu chưa có hoặc có thay đổi)
# --------------------------------------------------------------------------
log_info "Build Docker image (lần đầu có thể mất 15-20 phút)..."
docker compose build

# --------------------------------------------------------------------------
# Start container
# --------------------------------------------------------------------------
log_info "Khởi động container..."
docker compose up -d

echo ""
log_info "Bot đang chạy!"
echo ""
echo -e "  ${GREEN}►${NC} Health check:   http://localhost:8000/health"
echo -e "  ${GREEN}►${NC} Webhook URL:    http://localhost:8000/api/order"
echo -e "  ${GREEN}►${NC} API docs:       http://localhost:8000/docs"
echo ""
echo -e "  ${YELLOW}Xem logs:${NC}  docker compose logs -f"
echo -e "  ${YELLOW}Dừng bot:${NC}  docker compose down"
echo ""

# Hiển thị logs realtime
log_info "Đang hiển thị logs (Ctrl+C để thoát, bot vẫn chạy nền)..."
sleep 2
docker compose logs -f
