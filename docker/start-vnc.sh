#!/bin/bash
# =============================================================================
# start-vnc.sh — Mở desktop Wine qua trình duyệt để cài MT5 terminal
# =============================================================================
# Chạy trong container:
#   docker exec -it mt5_webhook_bot /start-vnc.sh
#
# Sau đó mở trình duyệt:
#   http://localhost:6080
#
# Trong desktop, mở terminal và gõ:
#   wine /tmp/mt5setup.exe
#
# Sau khi cài xong, đóng cửa sổ terminal này (Ctrl+C).
# Restart container để MT5 được nhận ra:
#   docker compose restart
# =============================================================================

set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }

VNC_PORT=5900
NOVNC_PORT=6080
DISPLAY_NUM=":10"
VNC_PASSWD="${VNC_PASSWORD:-mt5vnc}"

log_info "======================================================"
log_info "  MT5 Setup — VNC Desktop"
log_info "======================================================"
log_info ""
log_info "Khởi động virtual desktop..."

# Dọn dẹp lock cũ nếu có
rm -f /tmp/.X10-lock /tmp/.X11-unix/X10 2>/dev/null || true

# Start Xvfb
Xvfb "${DISPLAY_NUM}" -screen 0 1280x800x24 -nolisten tcp &
XVFB_PID=$!
export DISPLAY="${DISPLAY_NUM}"
sleep 2

# Start fluxbox (window manager nhẹ)
fluxbox &>/dev/null &
sleep 1

# Setup VNC password
mkdir -p /root/.vnc
x11vnc -storepasswd "${VNC_PASSWD}" /root/.vnc/passwd 2>/dev/null

# Start x11vnc
x11vnc \
    -display "${DISPLAY_NUM}" \
    -rfbport "${VNC_PORT}" \
    -rfbauth /root/.vnc/passwd \
    -forever \
    -noxdamage \
    -quiet &
VNC_PID=$!
sleep 1

# Start noVNC (browser-based VNC client)
websockify --web /usr/share/novnc/ \
    "${NOVNC_PORT}" \
    "localhost:${VNC_PORT}" &>/dev/null &
NOVNC_PID=$!
sleep 1

log_info ""
log_info "✓ Desktop VNC đang chạy!"
log_info ""
log_warn ">>> Mở trình duyệt tại: http://localhost:${NOVNC_PORT}/vnc.html"
log_warn ">>> Password VNC: ${VNC_PASSWD}"
log_info ""
log_info "Trong desktop, mở terminal (right-click → Terminal) và gõ:"
log_info ""
log_info "   WINEPREFIX=/root/.wine wine /tmp/mt5setup.exe"
log_info ""
log_info "Hoàn thành cài MT5, sau đó nhấn Ctrl+C ở đây để tắt VNC."
log_info "Rồi restart container:"
log_info "   docker compose restart"
log_info ""
log_info "Đang chờ... (Ctrl+C để tắt VNC)"

# Cleanup khi tắt
cleanup() {
    echo ""
    log_info "Đang tắt VNC..."
    kill "${NOVNC_PID}" 2>/dev/null || true
    kill "${VNC_PID}" 2>/dev/null || true
    kill "${XVFB_PID}" 2>/dev/null || true
    rm -f /tmp/.X10-lock 2>/dev/null || true
    log_info "VNC đã tắt."
}
trap cleanup EXIT SIGTERM SIGINT

# Giữ script chạy
wait "${XVFB_PID}"
