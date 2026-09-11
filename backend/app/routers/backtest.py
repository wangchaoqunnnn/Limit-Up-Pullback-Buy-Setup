"""历史回测路由。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query

from ..providers import DataSourceError, get_active_data_source, get_provider
from ..store import rule_store
from ..strategy import backtest
from ..utils import api_err, api_ok

logger = logging.getLogger(__name__)

router = APIRouter(tags=["回测分析"])


@router.get("/backtest", summary="信号历史回溯统计")
async def run_backtest(
    lookbackDays: int = Query(120, ge=30, le=500, description="回放窗口"),
    minScore: float = Query(75.0, ge=0.0, le=100.0, description="仅统计评分达标的样本"),
    horizon: int = Query(5, ge=1, le=10, description="持有天数"),
):
    """用历史K线回放到「信号日」，统计 T+1..T+horizon 的表现。"""
    provider = await get_provider()
    rule_set = rule_store().get()
    try:
        result = await backtest(
            provider,
            rule_set,
            lookback_days=lookbackDays,
            min_score=minScore,
            horizon=horizon,
        )
    except DataSourceError as exc:
        return api_err(f"数据源不可用：{exc}", status_code=503)
    data = {"dataSource": get_active_data_source(), **result}
    return api_ok(data)
