"""FastAPI 应用入口。

职责：创建应用、注册路由（统一 /api/v1 前缀）、CORS、统一异常信封、静态资源（SPA）挂载。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import APP_NAME, APP_VERSION, BASE_DIR, configure_logging, get_settings
from .providers import get_active_data_source, get_provider, reset_provider
from .routers import backtest, health, market, pool, rules, signals, stocks
from .routers import settings as settings_router
from .utils import EnvelopeJSONResponse, api_err

logger = logging.getLogger(__name__)

# 前端构建产物目录（不存在时不挂载，仅返回提示，绝不报错）
FRONTEND_DIST = BASE_DIR.parent / "frontend" / "dist"

API_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动/关闭钩子。"""
    configure_logging()
    settings = get_settings()
    settings.ensure_dirs()
    logger.info(
        "%s v%s 启动中：数据源模式=%s，数据目录=%s",
        APP_NAME,
        APP_VERSION,
        settings.data_source_mode,
        settings.data_path,
    )
    try:
        await get_provider()
        logger.info("当前生效数据源：%s", get_active_data_source())
    except Exception as exc:  # noqa: BLE001 - 启动阶段数据源异常不应阻断服务
        logger.warning("数据源初始化失败（将在首次请求时重试）：%s", exc)
    yield
    await reset_provider()
    logger.info("服务已关闭")


app = FastAPI(
    title=APP_NAME,
    description="中国 A 股「涨停回调低吸战法」量化选股后端服务",
    version=APP_VERSION,
    lifespan=lifespan,
    default_response_class=EnvelopeJSONResponse,
)

_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------------------------------------------------------- 路由
app.include_router(health.router, prefix=API_PREFIX)
app.include_router(market.router, prefix=API_PREFIX)
app.include_router(stocks.router, prefix=API_PREFIX)
app.include_router(signals.router, prefix=API_PREFIX)
app.include_router(backtest.router, prefix=API_PREFIX)
app.include_router(rules.router, prefix=API_PREFIX)
app.include_router(pool.router, prefix=API_PREFIX)
app.include_router(settings_router.router, prefix=API_PREFIX)


# --------------------------------------------------------------------- 异常
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """HTTP 异常统一为信封格式。"""
    detail = exc.detail if isinstance(exc.detail, str) else "请求处理失败"
    return api_err(detail, status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Pydantic 校验失败统一为 422 + 中文提示。"""
    messages: list[str] = []
    for error in exc.errors():
        location = ".".join(str(item) for item in error.get("loc", ()) if item != "body")
        messages.append(f"{location or '参数'}：{error.get('msg', '校验失败')}")
    return api_err("参数校验失败：" + "；".join(messages[:3]), status_code=422)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """兜底异常处理：返回统一信封，绝不向前端泄漏堆栈。"""
    logger.exception("未捕获异常：%s %s", request.method, request.url.path)
    return api_err("服务器内部错误，请稍后重试", status_code=500)


# --------------------------------------------------------------------- 静态
def _mount_frontend() -> None:
    """挂载前端 SPA（存在构建产物时）；否则提供中文提示页。"""
    if FRONTEND_DIST.is_dir() and (FRONTEND_DIST / "index.html").is_file():
        assets = FRONTEND_DIST / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str):  # noqa: ANN202
            if full_path.startswith("api"):
                return api_err("接口不存在", status_code=404)
            candidate = (FRONTEND_DIST / full_path).resolve()
            try:
                candidate.relative_to(FRONTEND_DIST.resolve())
            except ValueError:
                return api_err("非法的资源路径", status_code=400)
            if full_path and candidate.is_file():
                return FileResponse(str(candidate))
            return FileResponse(str(FRONTEND_DIST / "index.html"))

        logger.info("已挂载前端静态资源：%s", FRONTEND_DIST)
        return

    @app.get("/", include_in_schema=False)
    async def root_hint() -> HTMLResponse:
        return HTMLResponse(
            "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
            f"<title>{APP_NAME} 后端服务</title></head><body style='font-family:sans-serif;padding:2rem'>"
            f"<h2>{APP_NAME} 后端服务已启动（v{APP_VERSION}）</h2>"
            f"<p>未检测到前端构建产物，请先构建前端，或直接访问接口：</p>"
            f"<ul><li><a href='{API_PREFIX}/health'>{API_PREFIX}/health</a></li>"
            f"<li><a href='{API_PREFIX}/market/overview'>{API_PREFIX}/market/overview</a></li>"
            f"<li><a href='{API_PREFIX}/signals'>{API_PREFIX}/signals</a></li>"
            f"<li><a href='{API_PREFIX}/rules'>{API_PREFIX}/rules</a></li>"
            f"<li><a href='/docs'>/docs（OpenAPI 文档）</a></li></ul>"
            "<p>接口清单见 docs/API.md。</p></body></html>"
        )


_mount_frontend()


def main() -> None:
    """命令行入口：python -m app.main。"""
    import uvicorn

    settings = get_settings()
    configure_logging()
    uvicorn.run(app, host=settings.app_host, port=settings.app_port, log_level=settings.log_level.lower())


if __name__ == "__main__":
    main()
