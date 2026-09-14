#!/bin/bash
# =============================================================================
# install-mt5.sh — Cài MT5 terminal vào Docker volume (chạy 1 lần)
# =============================================================================
# Chạy lệnh này SAU KHI docker compose up -d lần đầu:
#
#   docker exec -it mt5_webhook_bot /install-mt5.sh /path/to/mt5setup.exe
#
# MT5 sẽ được cài vào volume mt5_wine_data và persist qua container restarts.
# =============================================================================

set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }

INSTALLER="${1}"

# Nếu không truyền arg, tìm trong /tmp
if [ -z "${INSTALLER}" ]; then
    INSTALLER=$(find /tmp -name "mt5setup.exe" -o -name "*.exe" 2>/dev/null | head -1)
fi

if [ -z "${INSTALLER}" ] || [ ! -f "${INSTALLER}" ]; then
    log_error "Không tìm thấy file installer MT5!"
    echo ""
    echo "Cách dùng:"
    echo "  1. Copy installer vào container:"
    echo "     docker cp ./mt5setup.exe mt5_webhook_bot:/tmp/mt5setup.exe"
    echo ""
    echo "  2. Chạy script này:"
    echo "     docker exec -it mt5_webhook_bot /install-mt5.sh /tmp/mt5setup.exe"
    exit 1
fi

log_info "Installer: ${INSTALLER}"

# Kiểm tra MT5 đã cài chưa
MT5_VOLUME_PREFIX="/root/.wine"
if find "${MT5_VOLUME_PREFIX}" -name "terminal64.exe" 2>/dev/null | grep -q .; then
    log_warn "MT5 terminal đã được cài trong volume:"
    find "${MT5_VOLUME_PREFIX}" -name "terminal64.exe" 2>/dev/null
    echo ""
    read -p "Cài đè? (y/N): " confirm
    if [ "${confirm}" != "y" ] && [ "${confirm}" != "Y" ]; then
        log_info "Hủy."
        exit 0
    fi
fi

log_info "Đang cài MT5 terminal (có thể mất 2-5 phút)..."
log_info "Wine prefix: ${MT5_VOLUME_PREFIX}"

# Dùng WINEPREFIX riêng trỏ vào volume để MT5 nằm trong volume
WINEPREFIX="${MT5_VOLUME_PREFIX}" \
WINEARCH=win64 \
WINEDEBUG=-all \
WINEDLLOVERRIDES="mscoree,mshtml=" \
xvfb-run --auto-servernum --server-args="-screen 0 1024x768x24" \
    wine "${INSTALLER}" /auto

WINEPREFIX="${MT5_VOLUME_PREFIX}" wineserver --wait

log_info "Kiểm tra kết quả..."
MT5_EXE=$(find "${MT5_VOLUME_PREFIX}" -name "terminal64.exe" 2>/dev/null | head -1)

if [ -n "${MT5_EXE}" ]; then
    log_info "✓ MT5 cài thành công: ${MT5_EXE}"
    log_info "Restart container để kích hoạt:"
    log_info "  docker compose restart"
else
    log_warn "Không tìm thấy terminal64.exe sau khi cài."
    log_warn "MT5 installer có thể cần thêm thời gian hoặc cần tương tác GUI."
    log_warn "Thử xem trong: ${MT5_VOLUME_PREFIX}/drive_c/Program Files/"
    ls "${MT5_VOLUME_PREFIX}/drive_c/Program Files/" 2>/dev/null || true
fi
