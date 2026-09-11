"""数据源工厂。

按 ``DATA_SOURCE_MODE`` 决定生效的数据源：

| 模式 | 行为 |
|---|---|
| ``auto``      | 依次尝试 ``DATA_SOURCE_ORDER`` 中的真实源；**全部不可用**才降级为合成演示数据 |
| ``real``      | 同上，但绝不降级为合成数据（全部真实源失败则接口报 503），用于生产 |
| ``eastmoney`` / ``tencent`` / ``ths`` / ``sina`` | 强制只使用该单一真实源 |
| ``synthetic`` | 强制使用合成演示数据（完全离线，用于演示与自动化测试） |

与旧实现的区别（重要）：
旧版 ``AutoFallbackProvider`` 是**全有或全无**降级 —— 东方财富任一环节失败就整体
退回合成数据。现在改为 ``ResilientProvider`` 的**按操作故障转移**：
取股票列表、取某只日线、取实时行情各自独立切换数据源，
单个源抽风不会让整个应用失去真实行情。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..config import get_settings
from .base import BaseProvider, DataSourceError, empty_kline
from .cache import FileCache
from .synthetic import SyntheticProvider

logger = logging.getLogger(__name__)

__all__ = [
    "BaseProvider",
    "DataSourceError",
    "FileCache",
    "SyntheticProvider",
    "get_provider",
    "get_active_data_source",
    "get_source_status",
    "reset_provider",
    "empty_kline",
]


_provider: BaseProvider | None = None
_lock = asyncio.Lock()

REAL_MODES = {"auto", "real", "eastmoney", "tencent", "ths", "sina"}


def _build_synthetic() -> SyntheticProvider:
    """构造合成演示数据源（规模独立于真实股票池配置）。"""
    return SyntheticProvider()


async def _build_provider() -> BaseProvider:
    """按配置构造数据源实例。"""
    settings = get_settings()
    mode = settings.data_source_mode

    if mode == "synthetic":
        provider = _build_synthetic()
        logger.info("数据源模式 synthetic：使用内置合成演示数据")
        return provider

    # 真实源：延迟导入，避免适配器缺失导致整个应用无法启动
    try:
        from .resilient import ResilientProvider
    except ImportError as exc:  # pragma: no cover - 仅在文件缺失时触发
        if mode == "real":
            raise
        logger.error("多源故障转移模块加载失败（%s），回退为合成演示数据", exc)
        return _build_synthetic()

    provider = ResilientProvider()

    # 单一源模式：只保留指定的那个源
    if mode in {"eastmoney", "tencent", "ths", "sina"}:
        provider.sources = [s for s in provider.sources if s.name == mode]
        provider.health = {s.name: provider.health[s.name] for s in provider.sources}
        if not provider.sources:
            logger.error("指定的数据源 %s 不可用（适配器未加载）", mode)
            if mode == "real":
                raise RuntimeError(f"数据源 {mode} 不可用")
            return _build_synthetic()

    if not provider.has_real_sources():
        logger.warning("没有任何真实数据源可用，使用合成演示数据")
        return _build_synthetic()

    # 启动时探测：仅用于日志告知，不决定整体是否降级
    try:
        reachable = [s.name for s in provider.sources if await s.ping()]
    except Exception as exc:  # noqa: BLE001 - 探测异常不应阻断启动
        logger.warning("数据源启动探测异常：%s", exc)
        reachable = []

    if reachable:
        # 把可达的源调整到优先级前面，减少首轮无谓的超时等待
        order = provider._settings.source_order_list
        ranked = sorted(provider.sources, key=lambda s: (s.name not in reachable, order.index(s.name) if s.name in order else 99))
        provider.sources = ranked
        logger.info(
            "数据源就绪：可用=%s，优先级=%s",
            ",".join(reachable),
            ",".join(s.name for s in provider.sources),
        )
    else:
        logger.warning("启动探测：全部真实数据源暂不可达，将在请求时继续尝试并自动切换")
        if mode == "real":
            logger.error("DATA_SOURCE_MODE=real 但所有真实源不可达，接口将返回 503")

    return provider


async def get_provider(force_refresh: bool = False) -> BaseProvider:
    """获取当前生效的数据源（进程内单例）。"""
    global _provider
    if _provider is not None and not force_refresh:
        return _provider
    async with _lock:
        if _provider is not None and not force_refresh:
            return _provider
        if _provider is not None:
            await _provider.close()
        _provider = await _build_provider()
        logger.info("当前数据源：%s（模式 %s）", _provider.source_label(), get_settings().data_source_mode)
        return _provider


def get_active_data_source() -> str:
    """返回当前生效的数据源标识字符串。"""
    if _provider is None:
        mode = get_settings().data_source_mode
        return "synthetic" if mode == "synthetic" else "unknown"
    return _provider.source_label()


def get_source_status() -> dict[str, Any]:
    """返回多源运行状态（供 /api/v1/settings 与 /api/v1/sources 展示）。"""
    if _provider is None:
        return {"mode": get_settings().data_source_mode, "active": "unknown", "sources": []}
    status = getattr(_provider, "status", None)
    if callable(status):
        return status()
    return {
        "mode": get_settings().data_source_mode,
        "active": _provider.source_label(),
        "usingFallback": _provider.source_label() == "synthetic",
        "sources": [],
    }


async def reset_provider() -> None:
    """关闭并清空数据源单例（测试与切换配置时使用）。"""
    global _provider
    if _provider is not None:
        await _provider.close()
    _provider = None
