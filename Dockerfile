# =============================================================================
# 涨停回调低吸战法 —— 多阶段构建
#   Stage 1 (node)   : 构建 React + Vite 前端静态资源
#   Stage 2 (python) : 安装后端依赖，内置前端产物，由 FastAPI 统一提供 Web 服务
# 说明：构建上下文为项目根目录，所有路径均为相对路径，不含任何绝对路径。
#
# 构建期可用参数（国内服务器建议显式传入，避免从 Docker Hub / PyPI 拉取超时）：
#   docker build \
#     --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
#     --build-arg NPM_REGISTRY=https://registry.npmmirror.com \
#     -t limit-up-pullback:latest .
# 一键脚本会自动从 .env 读取并传入这些参数。
# =============================================================================

# 显式声明目标平台，避免在 Apple Silicon 上为 amd64 构建、
# 或在 arm64 服务器上被 QEMU 模拟导致构建慢上百倍（实测过）
ARG TARGETPLATFORM

# ---------------------------------------------------------------------------
# Stage 1 —— 前端构建
# ---------------------------------------------------------------------------
FROM --platform=${TARGETPLATFORM} node:20-alpine AS frontend-builder

# npm 镜像源：国内服务器直连 registry.npmjs.org 会极慢甚至超时
ARG NPM_REGISTRY=https://registry.npmmirror.com
ENV NPM_CONFIG_REGISTRY=${NPM_REGISTRY}

WORKDIR /build/frontend

# 先只复制依赖清单，利用 Docker 层缓存加速重复构建
COPY frontend/package.json frontend/package-lock.json* ./

# 优先使用 npm ci（有 lock 文件时），否则回退 npm install
RUN if [ -f package-lock.json ]; then \
      npm ci --no-audit --no-fund; \
    else \
      npm install --no-audit --no-fund; \
    fi

# 复制前端源码并构建
COPY frontend/ ./
RUN npm run build


# ---------------------------------------------------------------------------
# Stage 2 —— 后端运行时
# ---------------------------------------------------------------------------
FROM --platform=${TARGETPLATFORM} python:3.12-slim AS runtime

# pip 源与超时策略（关键）：
#   实测国内服务器直连 files.pythonhosted.org 只有约 18 KB/s，
#   下载 numpy（16.7 MB）耗时 18 分钟、pandas（11 MB）直接读超时，
#   导致构建失败。因此默认走国内镜像，并把 PyPI 作为备用源
#   （extra-index-url 使「国内镜像没有的包」仍能从官方源取到）。
#   海外服务器可传 --build-arg PIP_INDEX_URL=https://pypi.org/simple 覆盖。
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_EXTRA_INDEX_URL=https://pypi.org/simple
ENV PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_EXTRA_INDEX_URL=${PIP_EXTRA_INDEX_URL} \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10

LABEL org.opencontainers.image.title="Limit-Up-Pullback-Buy-Setup" \
      org.opencontainers.image.description="Limit-up pullback buy-setup stock screener" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# 时区与健康检查所需的最小运行时依赖
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata curl ca-certificates \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# 后端依赖（单独一层，便于缓存）
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# 后端源码
COPY backend/ ./backend/

# 前端构建产物（由后端挂载为 SPA）
COPY --from=frontend-builder /build/frontend/dist ./frontend/dist

# 文档与部署资产（便于容器内自查）
COPY docs/ ./docs/

# 运行期数据目录（缓存 / 规则 / 股票池）
RUN mkdir -p /app/backend/data/cache

# 以非 root 用户运行
RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app
USER appuser

WORKDIR /app/backend

# 运行期默认值。全部可由 docker run -e / compose environment / .env 覆盖。
ENV APP_HOST=0.0.0.0 \
    APP_PORT=8000 \
    DATA_SOURCE_MODE=auto \
    DATA_SOURCE_ORDER=eastmoney,tencent,sina \
    DATA_DIR=./data \
    CACHE_TTL_SECONDS=300 \
    UNIVERSE_SIZE=0 \
    SYNTHETIC_UNIVERSE_SIZE=300 \
    REFRESH_INTERVAL_SECONDS=30 \
    KLINE_CACHE_MAX_DAYS=400 \
    SOURCE_CONCURRENCY=12 \
    KLINE_CONCURRENCY=24 \
    HTTP_TIMEOUT=10 \
    LOG_LEVEL=INFO \
    TZ=Asia/Shanghai

EXPOSE 8000

# 健康检查（/api/v1/health 不套统一信封，专门用于探针）。
# start-period 放宽到 60s：全市场首轮行情拉取需要数分钟，期间接口可能尚未就绪。
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=5 \
    CMD curl -fsS "http://127.0.0.1:${APP_PORT}/api/v1/health" || exit 1

CMD ["sh", "-c", "python -m uvicorn app.main:app --host ${APP_HOST} --port ${APP_PORT} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
