# =============================================================================
# MT5 TradingView Webhook Bot — Dockerfile
# =============================================================================
# Image này chỉ chứa: Wine + Python for Windows + Python packages
# MT5 terminal được cài RIÊNG vào Docker volume qua script docker/install-mt5.sh
# (chỉ cần chạy 1 lần, persist qua container restarts)
# =============================================================================

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Ho_Chi_Minh

# -----------------------------------------------------------------------------
# 1. System packages + Wine prerequisites
# -----------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget curl ca-certificates gnupg \
    xvfb \
    procps netcat-openbsd findutils \
    # Linux Python 3.11 — chạy uvicorn native (Wine socket binding có vấn đề)
    python3.11 python3.11-venv python3-pip \
    # VNC — dùng để cài MT5 interactively (chỉ cần 1 lần)
    x11vnc fluxbox websockify novnc \
    && rm -rf /var/lib/apt/lists/*

# -----------------------------------------------------------------------------
# 2. Cài Wine (winehq-stable)
# -----------------------------------------------------------------------------
RUN dpkg --add-architecture i386

RUN wget -qO /usr/share/keyrings/winehq-archive.key \
        https://dl.winehq.org/wine-builds/winehq.key && \
    echo "deb [arch=amd64,i386 signed-by=/usr/share/keyrings/winehq-archive.key] \
        https://dl.winehq.org/wine-builds/ubuntu/ jammy main" \
        > /etc/apt/sources.list.d/winehq.list

RUN apt-get update && apt-get install -y --install-recommends \
    winehq-stable \
    && rm -rf /var/lib/apt/lists/*

# -----------------------------------------------------------------------------
# 3. Setup Wine prefix 64-bit
# -----------------------------------------------------------------------------
ENV WINEPREFIX=/opt/wineprefix
ENV WINEARCH=win64
ENV WINEDEBUG=-all
# Tắt gecko/mono để wineboot không treo khi không có internet
ENV WINEDLLOVERRIDES="mscoree,mshtml="

RUN xvfb-run --auto-servernum --server-args="-screen 0 1024x768x24" \
    wineboot --init && \
    wineserver --wait

# -----------------------------------------------------------------------------
# 4. Cài Python 3.11 for Windows vào Wine prefix
# -----------------------------------------------------------------------------
ARG PYTHON_WIN_URL=https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe

RUN wget -q "${PYTHON_WIN_URL}" -O /tmp/python-win.exe

RUN xvfb-run --auto-servernum --server-args="-screen 0 1024x768x24" \
    wine /tmp/python-win.exe \
        /quiet \
        InstallAllUsers=0 \
        PrependPath=1 \
        Include_pip=1 \
        Include_test=0 \
        TargetDir="C:\\Python311" && \
    wineserver --wait && \
    rm /tmp/python-win.exe

# Verify Python cài thành công — build fail ngay nếu hỏng
RUN test -f "${WINEPREFIX}/drive_c/Python311/python.exe" || \
    ( echo "=== FAIL: python.exe không tồn tại ===" && \
      find "${WINEPREFIX}/drive_c" -name "python*.exe" 2>/dev/null && \
      exit 1 )

# -----------------------------------------------------------------------------
# 5. Cài Python packages vào Wine Python
# -----------------------------------------------------------------------------
WORKDIR /app
COPY requirements.txt .

RUN xvfb-run --auto-servernum --server-args="-screen 0 1024x768x24" \
    wine "${WINEPREFIX}/drive_c/Python311/python.exe" \
        -m pip install --upgrade pip --quiet && \
    wineserver --wait

RUN xvfb-run --auto-servernum --server-args="-screen 0 1024x768x24" \
    wine "${WINEPREFIX}/drive_c/Python311/python.exe" \
        -m pip install -r requirements.txt --quiet && \
    wineserver --wait

# Verify packages (Wine Python)
RUN xvfb-run --auto-servernum --server-args="-screen 0 1024x768x24" \
    wine "${WINEPREFIX}/drive_c/Python311/python.exe" \
        -c "import MetaTrader5; print('=== Wine MetaTrader5 OK ===')"

# -----------------------------------------------------------------------------
# 5b. Cài packages vào Linux Python (cho uvicorn native)
# -----------------------------------------------------------------------------
# Loại MetaTrader5 khỏi requirements cho Linux Python (Windows-only package)
RUN grep -v "MetaTrader5" /app/requirements.txt > /tmp/requirements-linux.txt && \
    python3.11 -m pip install --no-cache-dir -r /tmp/requirements-linux.txt && \
    rm /tmp/requirements-linux.txt

# -----------------------------------------------------------------------------
# 6. Copy app code + scripts
# -----------------------------------------------------------------------------
COPY app/ ./app/
COPY docker/mt5_proxy/ ./mt5_proxy/
COPY docker/install-mt5.sh /install-mt5.sh
COPY docker/start-vnc.sh /start-vnc.sh
RUN chmod +x /install-mt5.sh /start-vnc.sh && mkdir -p /app/logs

# -----------------------------------------------------------------------------
# 7. Entrypoint
# -----------------------------------------------------------------------------
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000
# Port 6080 dùng cho noVNC (chỉ khi chạy /start-vnc.sh để setup MT5)
EXPOSE 6080

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
