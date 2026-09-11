"""信号选股路由（核心）：五信号筛选、单标的详情、异步扫描任务。"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import OrderedDict
from typing import Any

from fastapi import APIRouter, Body, Query

from ..models import ScanRequest, StockSignal
from ..providers import DataSourceError, get_active_data_source, get_provider
from ..store import rule_store
from ..strategy import df_to_candles, get_market_snapshot, scan_cache_state, scan_universe
from ..utils import api_err, api_ok, is_valid_code, now_iso

logger = logging.getLogger(__name__)

router = APIRouter(tags=["信号选股"])

VALID_VERDICTS = {"BUY", "WATCH", "REJECT"}
VALID_LIMIT_TYPES = {
    "QUALITY",
    "ONE_WORD",
    "TAIL_SNEAK",
    "WEAK_SEAL",
    "HIGH_POSITION",
    "CONSECUTIVE",
    "ST_LIMIT",
    "NONE",
    "ALL",
}
VALID_SORTS = {"score", "pullbackDays", "pctChg", "turnover"}

MAX_TASKS = 20
_TASKS: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
_SCAN_SEM = asyncio.Semaphore(4)


def _parse_verdicts(raw: str) -> set[str]:
    items = {item.strip().upper() for item in (raw or "").split(",") if item.strip()}
    return items or {"BUY", "WATCH"}


def _sort_key(sort: str, metrics: dict[str, dict[str, float]]):
    if sort == "pullbackDays":
        return lambda s: s.pullbackDays
    if sort == "pctChg":
        return lambda s: metrics.get(s.meta.code, {}).get("pctChg", 0.0)
    if sort == "turnover":
        return lambda s: metrics.get(s.meta.code, {}).get("turnover", 0.0)
    return lambda s: s.score


@router.get("/signals", summary="按战法筛选当前可低吸标的")
async def list_signals(
    verdict: str = Query("BUY,WATCH", description="逗号分隔，可选 BUY/WATCH/REJECT"),
    industry: str | None = Query(None, description="行业过滤"),
    minScore: float = Query(0.0, ge=0.0, le=100.0),
    maxScore: float = Query(100.0, ge=0.0, le=100.0),
    limitUpType: str = Query("QUALITY", description="涨停类型过滤，ALL 表示不过滤"),
    sort: str = Query("score"),
    order: str = Query("desc"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(50, ge=1, le=200),
    refresh: bool = Query(False),
):
    """核心接口：返回满足条件的 StockSignal 列表与统计。"""
    verdicts = _parse_verdicts(verdict)
    invalid = verdicts - VALID_VERDICTS
    if invalid:
        return api_err(f"verdict 参数非法：{','.join(sorted(invalid))}", status_code=400)
    limit_type = (limitUpType or "QUALITY").upper()
    if limit_type not in VALID_LIMIT_TYPES:
        return api_err(f"limitUpType 参数非法：{limitUpType}", status_code=400)
    sort_key = sort if sort in VALID_SORTS else "score"
    if sort not in VALID_SORTS:
        return api_err(f"sort 参数非法：{sort}", status_code=400)
    if order not in {"asc", "desc"}:
        return api_err(f"order 参数非法：{order}", status_code=400)
    if minScore > maxScore:
        return api_err("minScore 不能大于 maxScore", status_code=400)

    provider = await get_provider()
    rule_set = rule_store().get()
    try:
        snapshot = await get_market_snapshot(provider, rule_set, refresh=refresh)
    except DataSourceError as exc:
        return api_err(f"数据源不可用：{exc}", status_code=503)
    signals: list[StockSignal] = list(snapshot.get("signals") or [])

    matched = [
        s
        for s in signals
        if s.verdict in verdicts
        and (limit_type == "ALL" or s.limitUpType == limit_type)
        and (not industry or s.meta.industry == industry)
        and minScore <= s.score <= maxScore
    ]
    metrics: dict[str, dict[str, float]] = {}
    if matched and sort_key in {"pctChg", "turnover"}:
        try:
            frames = await provider.get_daily_kline_batch([s.meta.code for s in matched], days=250)
        except DataSourceError as exc:
            logger.warning("排序指标获取失败：%s", exc)
            frames = {}
        for code, df in frames.items():
            if df is not None and len(df):
                metrics[code] = {
                    "pctChg": float(df["pct_chg"].iloc[-1]),
                    "turnover": float(df["turnover"].iloc[-1]),
                }
    matched.sort(key=_sort_key(sort_key, metrics), reverse=(order == "desc"))

    total = len(matched)
    start = (page - 1) * pageSize
    items = matched[start : start + pageSize]

    statistics = {
        "avgScore": round(sum(s.score for s in matched) / total, 1) if total else 0.0,
        "buyCount": sum(1 for s in matched if s.verdict == "BUY"),
        "watchCount": sum(1 for s in matched if s.verdict == "WATCH"),
        "avgPullbackDays": round(sum(s.pullbackDays for s in matched) / total, 1) if total else 0.0,
        "signalPassRate": {
            key: round(
                sum(1 for s in matched if any(d.key == key and d.passed for d in s.signals)) / total, 4
            )
            if total
            else 0.0
            for key in (
                "volume_shrink",
                "support_hold",
                "intraday_stabilize",
                "kline_bottom",
                "sector_resonance",
            )
        },
    }
    cache_state = scan_cache_state()
    data = {
        "dataSource": get_active_data_source(),
        "scannedAt": cache_state.get("lastSyncAt") or now_iso(),
        "tradeDate": snapshot.get("tradeDate") or cache_state.get("tradeDate") or "",
        "universeSize": int(snapshot.get("universeSize") or 0),
        "matched": total,
        "total": total,
        "page": page,
        "pageSize": pageSize,
        "ruleSet": rule_set.model_dump(),
        "items": [s.model_dump() for s in items],
        "statistics": statistics,
    }
    return api_ok(data)


@router.get("/signals/{code}", summary="单只标的信号详情")
async def signal_detail(code: str, refresh: bool = Query(False)):
    """返回 StockSignal 全量字段，并附最近 60 根K线与板块信息。"""
    if not is_valid_code(code):
        return api_err(f"股票代码非法：{code}，应为 6 位数字", status_code=400)
    provider = await get_provider()
    rule_set = rule_store().get()
    try:
        snapshot = await get_market_snapshot(provider, rule_set, refresh=refresh)
    except DataSourceError as exc:
        return api_err(f"数据源不可用：{exc}", status_code=503)
    signal = next((s for s in (snapshot.get("signals") or []) if s.meta.code == code), None)
    if signal is None:
        return api_err(f"未找到股票 {code} 的战法信号（无有效优质首板回调形态）", status_code=404)
    try:
        df = await provider.get_daily_kline(code, days=250)
    except DataSourceError as exc:
        return api_err(f"数据源不可用：{exc}", status_code=503)
    sector = snapshot.get("sector")
    industry_info = sector.info_of(signal.meta.industry) if sector is not None else {}
    data = signal.model_dump()
    data["candles"] = [c.model_dump() for c in df_to_candles(signal.meta, df, limit=60)]
    data["industry"] = {
        "name": signal.meta.industry,
        "sentimentScore": industry_info.get("sentimentScore", 0.0),
        "pctChg": industry_info.get("pctChg", 0.0),
        "limitUpCount": industry_info.get("limitUpCount", 0),
        "memberCount": industry_info.get("memberCount", 0),
        "trend": industry_info.get("trend", []),
    }
    return api_ok(data)


# --------------------------------------------------------------------- 扫描
async def _run_scan(task_id: str, refresh: bool) -> None:
    """后台扫描任务：限并发 + 分批更新进度。"""
    task = _TASKS.get(task_id)
    if task is None:
        return
    task["status"] = "running"
    task["startedAt"] = now_iso()
    task["message"] = "扫描中"

    def progress(done: int, total: int) -> None:
        task["progress"] = int(done)
        task["total"] = int(total)

    provider = await get_provider()
    rule_set = rule_store().get()
    try:
        async with _SCAN_SEM:
            signals = await scan_universe(provider, rule_set, progress_cb=progress)
        matched = sum(1 for s in signals if s.verdict in {"BUY", "WATCH"})
        task["matched"] = matched
        task["progress"] = task.get("total") or len(signals)
        task["status"] = "finished"
        task["finishedAt"] = now_iso()
        task["message"] = f"扫描完成，命中 {matched} 只"
        logger.info("扫描任务 %s 完成，命中 %d 只", task_id, matched)
    except Exception as exc:  # noqa: BLE001 - 任务内部异常不应影响进程
        logger.exception("扫描任务 %s 失败", task_id)
        task["status"] = "failed"
        task["finishedAt"] = now_iso()
        task["message"] = f"扫描失败：{exc}"


@router.post("/scan", summary="触发全市场扫描任务")
async def start_scan(payload: ScanRequest = Body(default=ScanRequest())):
    """异步触发全市场扫描，立即返回任务信息。"""
    provider = await get_provider()
    universe_size = 0
    try:
        metas = await provider.get_stock_list()
        universe_size = len(metas)
    except DataSourceError as exc:
        return api_err(f"数据源不可用：{exc}", status_code=503)

    task_id = uuid.uuid4().hex[:8]
    _TASKS[task_id] = {
        "taskId": task_id,
        "status": "pending",
        "progress": 0,
        "total": universe_size,
        "startedAt": None,
        "finishedAt": None,
        "matched": 0,
        "message": "开始扫描",
    }
    while len(_TASKS) > MAX_TASKS:
        _TASKS.popitem(last=False)
    asyncio.create_task(_run_scan(task_id, bool(payload.refresh)))
    return api_ok(dict(_TASKS[task_id]))


@router.get("/scan/{taskId}", summary="查询扫描任务状态")
async def get_scan(taskId: str):
    """查询扫描任务进度（内存保存，仅保留最近 20 条）。"""
    task = _TASKS.get(taskId)
    if task is None:
        return api_err(f"未找到扫描任务 {taskId}", status_code=404)
    return api_ok(dict(task))
