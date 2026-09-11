"""健康检查路由（唯一不走统一信封的接口）。"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter

from ..config import APP_VERSION, get_settings
from ..providers import DataSourceError, get_active_data_source, get_provider
from ..strategy import scan_cache_state
from ..utils import now_iso

logger = logging.getLogger(__name__)

router = APIRouter(tags=["健康检查"])

_STARTED_AT = time.time()


@router.get("/health", summary="健康检查（探针专用，不使用统一信封）")
async def health() -> dict:
    """返回服务状态、数据源与 universe 规模。"""
    settings = get_settings()
    provider = await get_provider()
    state = scan_cache_state()
    universe_size = int(state.get("universeSize") or 0)
    if universe_size <= 0:
        try:
            metas = await provider.get_stock_list()
            universe_size = len(metas)
        except DataSourceError as exc:
            logger.warning("健康检查获取股票列表失败：%s", exc)
            universe_size = int(settings.universe_size)
    return {
        "status": "ok",
        "version": APP_VERSION,
        "uptimeSeconds": round(time.time() - _STARTED_AT, 1),
        "dataSource": get_active_data_source(),
        "lastSyncAt": state.get("lastSyncAt") or now_iso(),
        "universeSize": universe_size,
    }
