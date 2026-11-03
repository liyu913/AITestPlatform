# Build context: 仓库根目录（docker-compose build.context: .）
# 保留 backend/ 与 ui-runner/ 同级，供 Node 运行器路径解析。
#
# 国内加速要点：
# - apt / PyPI / npm / Playwright 均走国内镜像
# - 不用 `uv sync --frozen` 直接装依赖（lock 里写死 files.pythonhosted.org，国内极慢）
# - 改为 export 版本列表 + uv pip 从清华源安装，并挂 BuildKit cache
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple \
    UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    TZ=Asia/Shanghai
# 注意：不要设 PLAYWRIGHT_DOWNLOAD_HOST=npmmirror。
# 该镜像常缺 linux-arm64（Apple Silicon 上 Docker 默认 arm64）的 Chrome for Testing，会 404。

# Debian 源换成清华（bookworm 使用 DEB822 debian.sources；阿里云偶发大包超时）
RUN sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list.d/debian.sources \
    && sed -i 's/security.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
      curl \
      ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.8.15 /uv /usr/local/bin/uv
COPY --from=node:22-bookworm-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:22-bookworm-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

WORKDIR /workspace/backend

COPY backend/pyproject.toml backend/uv.lock ./

# 先装第三方依赖（走清华 PyPI，避开 lock 内 pythonhosted 直链）
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv /workspace/backend/.venv \
    && uv export --frozen --no-dev --no-hashes --no-emit-project -o /tmp/requirements.txt \
    && uv pip install --python /workspace/backend/.venv/bin/python \
      --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
      -r /tmp/requirements.txt

COPY backend/ ./

# 再以 editable 安装本项目（无额外下载）
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /workspace/backend/.venv/bin/python --no-deps .

ENV PATH="/workspace/backend/.venv/bin:$PATH" \
    UPLOAD_DIR=/data/uploads

# Playwright：系统依赖（apt）与浏览器分开装
RUN apt-get update \
    && for i in 1 2 3; do playwright install-deps chromium && break || sleep 5; done \
    && rm -rf /var/lib/apt/lists/*

# 浏览器走官方 CDN（含 linux-arm64）；失败重试，仍失败则跳过以免阻断一键启动（UI 自动化需后续自行安装）
RUN for i in 1 2 3; do \
      playwright install chromium && break; \
      echo "[playwright] chromium download failed (attempt $i), retrying..."; \
      sleep 8; \
    done \
    || echo "[playwright] WARN: chromium not installed; API 仍可用，UI 自动化暂不可用"

# UI 场景 Agent / 探索用的 Node 运行器（与仓库根同级）
WORKDIR /workspace/ui-runner
COPY ui-runner/package.json ui-runner/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --registry=https://registry.npmmirror.com
COPY ui-runner/ ./
RUN npx playwright install chromium \
    || echo "[playwright/node] WARN: chromium not installed"

WORKDIR /workspace/backend

# Windows 检出常为 CRLF；shebang 末尾的 \r 会让内核找 /bin/sh\r，报 no such file or directory
RUN mkdir -p /data/uploads \
    && sed -i 's/\r$//' /workspace/backend/docker-entrypoint.sh \
    && chmod +x /workspace/backend/docker-entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/workspace/backend/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
