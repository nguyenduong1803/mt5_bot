#!/bin/bash
# =============================================================================
# Entrypoint — MT5 TradingView Webhook Bot
#
# Kiến trúc:
#   WINEPREFIX=/root/.wine (volume) — chứa MT5 terminal + Python (copy lần đầu)
#   Proxy và MT5 dùng CÙNG wine prefix → share wineserver → IPC hoạt động
# =============================================================================

set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log_info()    { echo -e "${GREEN}[INFO]${NC}  $(date '+%H:%M:%S') $*"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC}  $(date '+%H:%M:%S') $*"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $(date '+%H:%M:%S') $*"; }
log_section() { echo -e "\n${BLUE}════════════════════════════════════════${NC}"; \
                echo -e "${BLUE}  $*${NC}"; \
                echo -e "${BLUE}════════════════════════════════════════${NC}"; }

# Wine prefix chung cho cả MT5 terminal lẫn proxy
WINE_VOL=/root/.wine
# Python được bake trong image
WINE_IMG_PYTHON=/opt/wineprefix/drive_c/Python311

export WINEPREFIX="${WINE_VOL}"
export WINEDEBUG=-all
export WINEDLLOVERRIDES="mscoree,mshtml="

log_section "MT5 Webhook Bot — Khởi động"

# --------------------------------------------------------------------------
# Prerequisites
# --------------------------------------------------------------------------
if [ ! -f /app/config.json ]; then
    log_error "Không tìm thấy /app/config.json!"
    exit 1
fi

# --------------------------------------------------------------------------
# 1. Copy Python vào volume prefix (chỉ lần đầu)
#    Proxy và MT5 PHẢI dùng cùng WINEPREFIX để share IPC
# --------------------------------------------------------------------------
WINE_VOL_PYTHON="${WINE_VOL}/drive_c/Python311/python.exe"

if [ ! -f "${WINE_VOL_PYTHON}" ]; then
    if [ -d "${WINE_IMG_PYTHON}" ]; then
        log_info "Lần đầu chạy: copy Python vào volume prefix (~1 phút)..."
        mkdir -p "${WINE_VOL}/drive_c"
        cp -r "${WINE_IMG_PYTHON}" "${WINE_VOL}/drive_c/Python311"
        log_info "Python copy xong: ${WINE_VOL_PYTHON}"
    else
        log_error "Không tìm thấy Python trong image: ${WINE_IMG_PYTHON}"
        log_error "Rebuild: docker compose build --no-cache"
        exit 1
    fi
else
    log_info "Python đã có trong volume: ${WINE_VOL_PYTHON}"
fi

WINE_PYTHON="${WINE_VOL_PYTHON}"

# --------------------------------------------------------------------------
# 2. Xvfb — virtual display
# --------------------------------------------------------------------------
log_info "Khởi động Xvfb..."
rm -f /tmp/.X11-lock /tmp/.X11-unix/X11 2>/dev/null || true
Xvfb :11 -screen 0 1024x768x24 -nolisten tcp &
XVFB_PID=$!
export DISPLAY=:11
sleep 2
log_info "Xvfb OK (PID=${XVFB_PID})"

# Start VNC server for debugging
log_info "Khởi động x11vnc & websockify..."
mkdir -p /root/.vnc
x11vnc -storepasswd mt5vnc /root/.vnc/passwd
x11vnc -display :11 -rfbport 5900 -rfbauth /root/.vnc/passwd -forever -noxdamage >/dev/null 2>&1 &
websockify --web /usr/share/novnc/ 6080 localhost:5900 >/dev/null 2>&1 &
fluxbox -display :11 >/dev/null 2>&1 &

# --------------------------------------------------------------------------
# 3. MT5 Terminal (cùng WINE_VOL)
# --------------------------------------------------------------------------
MT5_EXE=$(find "${WINE_VOL}/drive_c" -name "terminal64.exe" 2>/dev/null | head -1)

if [ -n "${MT5_EXE}" ]; then
    log_info "Khởi động MT5: ${MT5_EXE}"

    # Tìm Windows path (C:\...) để set MT5_TERMINAL_PATH cho app
    MT5_WIN_PATH=$(echo "${MT5_EXE}" \
        | sed "s|${WINE_VOL}/drive_c/|C:\\\\|" \
        | tr '/' '\\')
    export MT5_TERMINAL_PATH="${MT5_WIN_PATH}"
    log_info "MT5_TERMINAL_PATH=${MT5_TERMINAL_PATH}"

    wine "${MT5_EXE}" /portable &
    log_info "MT5 đang khởi động (nền), đợi 15s..."
    sleep 15
else
    log_warn "MT5 terminal chưa được cài vào volume."
    log_warn "Để cài MT5 (1 lần duy nhất):"
    log_warn "  docker exec -it mt5_webhook_bot /start-vnc.sh"
    log_warn "  Mở http://localhost:6080/vnc.html  (pass: mt5vnc)"
    log_warn "  Gõ: WINEPREFIX=/root/.wine wine /tmp/mt5setup.exe"
    log_warn "  docker compose restart"
fi

# --------------------------------------------------------------------------
# 4. MT5 Proxy — Wine Python, CÙNG WINEPREFIX với MT5 terminal
# --------------------------------------------------------------------------
log_info "Khởi động MT5 Proxy (Wine Python, port 8765)..."

wine "${WINE_PYTHON}" /app/mt5_proxy/server.py &
PROXY_PID=$!

# Đợi proxy sẵn sàng (tối đa 30s)
for i in $(seq 1 30); do
    if curl -sf http://127.0.0.1:8765/health >/dev/null 2>&1; then
        log_info "MT5 Proxy OK (PID=${PROXY_PID}, đợi ${i}s)"
        break
    fi
    sleep 1
    if [ "${i}" -eq 30 ]; then
        log_warn "MT5 Proxy chưa sẵn sàng sau 30s — MT5 calls sẽ fail."
    fi
done

# --------------------------------------------------------------------------
# Cleanup
# --------------------------------------------------------------------------
cleanup() {
    log_info "Đang dừng..."
    wine taskkill /F /IM terminal64.exe 2>/dev/null || true
    kill "${PROXY_PID}" 2>/dev/null || true
    kill "${XVFB_PID}" 2>/dev/null || true
    wait 2>/dev/null || true
}
trap cleanup EXIT SIGTERM SIGINT

# --------------------------------------------------------------------------
# 5. FastAPI — Linux Python native
# --------------------------------------------------------------------------
log_section "FastAPI Bot"
log_info "Webhook: http://0.0.0.0:8000/api/order"
log_info "Health:  http://0.0.0.0:8000/health"
log_info "Docs:    http://0.0.0.0:8000/docs"
echo ""

cd /app
exec python3.11 -m uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 1 \
    --log-level info
