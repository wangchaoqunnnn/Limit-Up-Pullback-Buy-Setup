"""健康检查路由（唯一不走统一信封的接口）。"""

from __future__ import annotations

import time

from fastapi import APIRouter

from ..config import APP_VERSION, get_settings
from ..providers import get_active_data_source, get_provider, get_source_status
from ..strategy import scan_cache_state
from ..utils import now_iso

router = APIRouter(tags=["健康检查"])

_STARTED_AT = time.time()


def _rss_mb() -> float | None:
    """当前进程常驻内存（MB）。读 /proc 失败时返回 None，绝不抛异常。"""
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as fp:
            for line in fp:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024.0, 1)
    except (OSError, ValueError, IndexError):
        return None
    return None


def _source_summary() -> dict:
    """数据源概览：可用/被跳过数量。只读内存中的健康度，不发请求。"""
    try:
        status = get_source_status()
        sources = status.get("sources") or []
        usable = [s.get("name") for s in sources if s.get("available")]
        skipped = [s.get("name") for s in sources if s.get("skipped")]
        return {
            "total": len(sources),
            "usable": usable,
            "skipped": skipped,
            # 所有源都被跳过 = 拿不到任何真实行情，界面会表现为「数据缺字段」
            "allSkipped": bool(sources) and not usable,
        }
    except Exception:  # noqa: BLE001 - 健康检查绝不能因统计失败而报错
        return {"total": 0, "usable": [], "skipped": [], "allSkipped": False}


@router.get("/health", summary="健康检查（探针专用，不使用统一信封）")
async def health() -> dict:
    """返回服务状态、数据源与 universe 规模。

    **性能约束（重要）**：本接口会被 Docker HEALTHCHECK 与外部监控每 30 秒调用，
    因此绝不允许在此处触发任何上游请求。
    曾经这里在「尚未扫描过」时直接调用 ``provider.get_stock_list()``，
    导致容器刚启动时健康检查要等一次全市场列表拉取（实测数十秒甚至数分钟），
    进而使 HEALTHCHECK 超时、编排工具误判容器不健康。现在只读已缓存的状态。

    额外暴露内存占用与数据源可用性，便于在**看不到容器日志**时也能判断
    「是后端挂了、还是只是首轮行情没预热完」。
    """
    settings = get_settings()
    provider = await get_provider()
    state = scan_cache_state()

    # 优先读「最近一次扫描」的规模；没有则读数据源已缓存的规模；
    # 都没有才回退到配置值 —— 全程不发起网络请求。
    universe_size = int(state.get("universeSize") or 0)
    if universe_size <= 0:
        status = getattr(provider, "status", None)
        if callable(status):
            try:
                universe_size = int((status() or {}).get("universeSize") or 0)
            except Exception:  # noqa: BLE001 - 健康检查绝不能因统计失败而报错
                universe_size = 0
    if universe_size <= 0:
        universe_size = int(settings.universe_size)

    sources = _source_summary()
    return {
        "status": "ok",
        "version": APP_VERSION,
        "uptimeSeconds": round(time.time() - _STARTED_AT, 1),
        "dataSource": get_active_data_source(),
        "lastSyncAt": state.get("lastSyncAt") or now_iso(),
        "universeSize": universe_size,
        # 首轮预热是否完成：未完成时重接口慢属正常现象，而非故障
        "scanReady": bool(state.get("signals") is not None),
        "memoryMB": _rss_mb(),
        "sources": sources,
        "dataSourceMode": settings.data_source_mode,
    }
