FROM python:3.11-slim

# 必要なシステムパッケージインストール
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    ca-certificates \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libatspi2.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 非rootユーザー作成（先に作成してPlaywrightブラウザを共有）
RUN useradd -m appuser

# Python依存関係インストール
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwrightブラウザインストール（共有ディレクトリに配置）
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers
RUN mkdir -p /opt/playwright-browsers && \
    playwright install chromium && \
    playwright install-deps chromium && \
    chmod -R 755 /opt/playwright-browsers

# アプリケーションコピー
COPY . .

# 設定ディレクトリの権限とオーナー設定
RUN chmod -R 755 /app/config && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 5000

# ヘルスチェック
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost:5000/health || exit 1

# gunicornで起動（Playwright用に最適化）
# - workers=2: ヘルスチェック用とスクレイピング用
# - threads削除: asyncio競合を回避（ThreadPoolExecutorで別途処理）
# - timeout=600: 長時間スクレイピング対応（10分）
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--timeout", "600", "--graceful-timeout", "600", "--workers", "2", "app.main:app"]
