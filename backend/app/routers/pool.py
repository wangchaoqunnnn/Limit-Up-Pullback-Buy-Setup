"""自选低吸池路由。"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from ..models import PoolCreateRequest
from ..providers import DataSourceError, get_provider
from ..store import build_pool_item, compute_status, pool_store, rule_store
from ..strategy import analyze_stock
from ..utils import api_err, api_ok, is_valid_code, round2, safe_div

logger = logging.getLogger(__name__)

router = APIRouter(tags=["自选低吸池"])

PRICE_FIELDS = ("buyLow", "buyHigh", "stopLoss", "takeProfit1", "takeProfit2")


async def _latest_price(provider, code: str) -> tuple[float | None, str]:
    """取最新收盘价与交易日。"""
    try:
        df = await provider.get_daily_kline(code, days=5)
    except DataSourceError as exc:
        logger.warning("获取 %s 最新价失败：%s", code, exc)
        return None, ""
    if df is None or len(df) == 0:
        return None, ""
    return float(df["close"].iloc[-1]), str(df["date"].iloc[-1])[:10]


@router.get("/pool", summary="自选低吸池列表")
async def list_pool():
    """返回股票池条目（实时计算盈亏与状态）与汇总。"""
    store = pool_store()
    provider = await get_provider()
    items: list[dict] = []
    for raw in store.list_raw():
        meta = raw.get("meta") or {}
        code = str(meta.get("code") or "")
        last_close = raw.get("lastClose")
        last_date = raw.get("lastDate") or ""
        if code:
            price, date = await _latest_price(provider, code)
            if price is not None:
                last_close, last_date = price, date
        status, status_text = compute_status(
            last_close, raw.get("buyLow"), raw.get("buyHigh"), raw.get("stopLoss"), raw.get("takeProfit1")
        )
        added_price = raw.get("addedPrice")
        item = dict(raw)
        item["lastClose"] = round2(last_close) if last_close is not None else None
        item["lastDate"] = last_date or None
        item["status"] = status
        item["statusText"] = status_text
        item["pnlPct"] = (
            round(safe_div(last_close - added_price, added_price, 0.0), 4)
            if (last_close is not None and added_price)
            else 0.0
        )
        items.append(item)

    total = len(items)
    avg_pnl = round(sum(float(i.get("pnlPct") or 0.0) for i in items) / total, 4) if total else 0.0
    return api_ok(
        {
            "total": total,
            "items": items,
            "summary": {
                "avgPnlPct": avg_pnl,
                "triggeredCount": sum(1 for i in items if i.get("status") == "triggered"),
                "stoppedCount": sum(1 for i in items if i.get("status") == "stopped"),
                "targetCount": sum(1 for i in items if i.get("status") == "target"),
            },
        }
    )


@router.post("/pool", summary="加入自选低吸池")
async def add_pool(payload: PoolCreateRequest):
    """把战法标的加入股票池；价格字段省略时按战法自动计算。"""
    body = payload.model_dump()
    code = str(body.get("code") or "").strip()
    if not is_valid_code(code):
        return api_err(f"股票代码非法：{code}，应为 6 位数字", status_code=400)
    store = pool_store()
    if store.contains(code):
        return api_err(f"股票 {code} 已在自选低吸池中，请勿重复添加", status_code=409)

    provider = await get_provider()
    rule_set = rule_store().get()
    try:
        result = await analyze_stock(provider, rule_set, code, days=250)
    except DataSourceError as exc:
        return api_err(f"数据源不可用：{exc}", status_code=503)
    if result is None:
        return api_err(f"未找到股票 {code} 的行情数据", status_code=404)

    signal = result["signal"]
    missing = [f for f in PRICE_FIELDS if body.get(f) is None]
    if signal is None and missing:
        return api_err(
            f"股票 {code} 当前没有有效战法信号，无法自动计算买卖计划，请手动填写 {','.join(missing)}",
            status_code=400,
        )
    df = result["df"]
    last_close = float(df["close"].iloc[-1])
    last_date = str(df["date"].iloc[-1])[:10]
    item = build_pool_item(result["meta"], signal, body, last_close, last_date)
    store.add(item.model_dump())
    logger.info("股票 %s 已加入自选低吸池", code)
    return api_ok(item.model_dump())


@router.delete("/pool/{code}", summary="从自选低吸池移除")
async def remove_pool(code: str):
    """移除股票池条目，不存在返回 404。"""
    if not is_valid_code(code):
        return api_err(f"股票代码非法：{code}，应为 6 位数字", status_code=400)
    store = pool_store()
    if not store.contains(code):
        return api_err(f"股票 {code} 不在自选低吸池中", status_code=404)
    store.remove(code)
    return api_ok({"code": code})
