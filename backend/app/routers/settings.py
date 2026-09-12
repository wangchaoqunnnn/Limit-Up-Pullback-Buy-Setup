"""系统设置路由：运行信息、数据源状态、市场时钟。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query

from ..config import APP_NAME, APP_VERSION, TIMEZONE, get_settings
from ..market_calendar import effective_ttl, market_clock
from ..providers import DataSourceError, get_active_data_source, get_provider, get_source_status
from ..utils import api_err, api_ok, now_iso

logger = logging.getLogger(__name__)

router = APIRouter(tags=["系统设置"])


@router.get("/settings", summary="系统设置与运行信息")
async def get_app_settings(deep: bool = Query(False, description="是否拉取股票池以获取准确规模（较慢）")):
    """返回应用信息、生效数据源、多源健康度、市场时钟与刷新策略。

    ``deep=false``（默认）时不访问数据源，仅读取已缓存的规模，
    因此该接口始终快速返回，适合前端每次轮询调用。
    """
    settings = get_settings()
    status = get_source_status()
    clock = market_clock(interval_seconds=settings.refresh_interval_seconds)

    universe_size = int(status.get("universeSize") or settings.universe_size)
    if deep:
        try:
            provider = await get_provider()
            metas = await provider.get_stock_list()
            if metas:
                universe_size = len(metas)
        except DataSourceError as exc:
            if settings.data_source_mode == "real":
                return api_err(f"数据源不可用：{exc}", status_code=503)
            logger.warning("获取股票池失败（不影响设置接口）：%s", exc)

    return api_ok(
        {
            "appName": APP_NAME,
            "version": APP_VERSION,
            "dataSourceMode": settings.data_source_mode,
            "dataSourceActive": get_active_data_source(),
            "dataSourceOrder": settings.source_order_list,
            "usingFallback": bool(status.get("usingFallback")),
            "sources": status.get("sources", []),
            "universeSize": universe_size,
            "universeSource": status.get("universeSource"),
            "cacheTtlSeconds": int(settings.cache_ttl_seconds),
            "effectiveTtlSeconds": effective_ttl(
                settings.cache_ttl_seconds, settings.refresh_interval_seconds
            ),
            "refreshIntervalSeconds": int(settings.refresh_interval_seconds),
            "klineCache": status.get("cache", {}),
            "memoryCache": status.get("memoryCache", {}),
            "clock": clock,
            "syntheticEnabled": True,
            "serverTime": now_iso(),
            "timezone": settings.timezone or TIMEZONE,
        }
    )


@router.get("/market/clock", summary="市场时钟（前端据此决定是否轮询）")
async def get_market_clock():
    """返回当前交易时段与下次开盘时间。

    前端在 ``shouldPoll`` 为 true 时按 ``intervalSeconds`` 间隔自动刷新，
    收盘后自动停轮询，避免无意义的请求。
    """
    settings = get_settings()
    return api_ok(market_clock(interval_seconds=settings.refresh_interval_seconds))


@router.get("/sources", summary="数据源状态与健康度")
async def get_sources(probe: bool = Query(False, description="是否现场探测各源可用性（较慢）")):
    """返回各真实数据源的连续失败次数、成功率、冷却状态与当前生效源。

    ``probe=true`` 会**先重置健康度再重新探测**，使因「从未成功」而被长期跳过的
    数据源重新参与一次尝试（对应前端设置页的「重新探测」按钮）。
    这样当网络策略变化后，无需重启服务即可重新接纳此前不可达的源。
    """
    provider = await get_provider()
    if probe:
        reset = getattr(provider, "reset_health", None)
        if callable(reset):
            reset()
        ping = getattr(provider, "ping", None)
        if callable(ping):
            await ping()
    status = get_source_status()
    status["active"] = get_active_data_source()
    return api_ok(status)
