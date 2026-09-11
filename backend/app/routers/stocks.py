"""股票池浏览与个股详情路由。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query

from ..providers import DataSourceError, get_active_data_source, get_provider
from ..store import rule_store
from ..strategy import analyze_stock, df_to_candles, evaluate_stock, explain_signal, get_market_snapshot
from ..utils import api_err, api_ok, is_valid_code

logger = logging.getLogger(__name__)

router = APIRouter(tags=["股票池"])


@router.get("/stocks", summary="股票列表（带最新一句话行情）")
async def list_stocks(
    keyword: str | None = Query(None, description="代码或名称模糊匹配"),
    industry: str | None = Query(None, description="行业精确匹配"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=200),
):
    """分页返回股票列表，附最新行情与战法评分。"""
    provider = await get_provider()
    rule_set = rule_store().get()
    try:
        metas = await provider.get_stock_list()
    except DataSourceError as exc:
        return api_err(f"数据源不可用：{exc}", status_code=503)

    key = (keyword or "").strip()
    filtered = [
        m
        for m in metas
        if (not key or key in m.code or key in m.name) and (not industry or m.industry == industry)
    ]
    total = len(filtered)
    start = (page - 1) * pageSize
    page_metas = filtered[start : start + pageSize]

    signal_map: dict[str, object] = {}
    try:
        snapshot = await get_market_snapshot(provider, rule_set, refresh=False)
        signal_map = {s.meta.code: s for s in (snapshot.get("signals") or [])}
    except DataSourceError as exc:
        logger.warning("评分缓存不可用：%s", exc)

    frames: dict = {}
    if page_metas:
        try:
            frames = await provider.get_daily_kline_batch([m.code for m in page_metas], days=30)
        except DataSourceError as exc:
            logger.warning("行情获取失败：%s", exc)

    items = []
    for meta in page_metas:
        df = frames.get(meta.code)
        signal = signal_map.get(meta.code)
        last_close = None
        last_date = ""
        pct_chg = 0.0
        turnover = 0.0
        amount = 0.0
        sparkline: list[float] = []
        if df is not None and len(df):
            last_close = round(float(df["close"].iloc[-1]), 2)
            last_date = str(df["date"].iloc[-1])[:10]
            pct_chg = round(float(df["pct_chg"].iloc[-1]), 4)
            turnover = round(float(df["turnover"].iloc[-1]), 2)
            amount = float(df["amount"].iloc[-1])
            sparkline = [round(float(v), 2) for v in df["close"].tail(20).tolist()]
        items.append(
            {
                "meta": meta.model_dump(),
                "lastClose": last_close,
                "lastDate": last_date,
                "pctChg": pct_chg,
                "turnover": turnover,
                "amount": amount,
                "limitUpType": getattr(signal, "limitUpType", "NONE"),
                "sparkline": sparkline,
                "score": getattr(signal, "score", 0.0),
                "verdict": getattr(signal, "verdict", "REJECT"),
            }
        )
    return api_ok({"total": total, "page": page, "pageSize": pageSize, "items": items})


@router.get("/stocks/{code}", summary="单只股票完整分析")
async def stock_detail(
    code: str,
    klineLimit: int = Query(120, ge=30, le=500, description="返回最近 N 根K线"),
):
    """个股详情页主数据：K线、信号、历史涨停、板块与解读。"""
    if not is_valid_code(code):
        return api_err(f"股票代码非法：{code}，应为 6 位数字", status_code=400)
    provider = await get_provider()
    rule_set = rule_store().get()
    try:
        result = await analyze_stock(provider, rule_set, code, days=250)
    except DataSourceError as exc:
        return api_err(f"数据源不可用：{exc}", status_code=503)
    if result is None:
        return api_err(f"未找到股票 {code} 的行情数据", status_code=404)

    meta = result["meta"]
    df = result["df"]
    signal = result["signal"]
    data = {
        "meta": meta.model_dump(),
        "dataSource": get_active_data_source(),
        "candles": [c.model_dump() for c in df_to_candles(meta, df, limit=klineLimit)],
        "signal": signal.model_dump() if signal is not None else None,
        "signals": [s.model_dump() for s in signal.signals] if signal is not None else [],
        "limitUpHistory": result["limitUpHistory"],
        "industry": result["industry"],
        "explain": explain_signal(meta, signal),
    }
    return api_ok(data)
