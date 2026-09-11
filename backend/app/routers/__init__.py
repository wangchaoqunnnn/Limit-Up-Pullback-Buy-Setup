"""路由聚合包。"""

from __future__ import annotations

from . import backtest, health, market, pool, rules, signals, stocks
from . import settings as settings_router

__all__ = [
    "backtest",
    "health",
    "market",
    "pool",
    "rules",
    "settings_router",
    "signals",
    "stocks",
]
