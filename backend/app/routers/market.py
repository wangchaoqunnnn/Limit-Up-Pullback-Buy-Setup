"""市场情绪总览路由。"""

from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np
import pandas as pd
from fastapi import APIRouter, Query

from ..config import get_settings
from ..providers import DataSourceError, get_active_data_source, get_provider
from ..store import rule_store
from ..strategy import arrays_from_df, scan_cache_state, scan_universe
from ..utils import api_err, api_ok, clamp, now_iso

logger = logging.getLogger(__name__)

router = APIRouter(tags=["市场总览"])

LEVEL_BANDS: tuple[tuple[float, str], ...] = (
    (80.0, "过热"),
    (62.0, "偏暖"),
    (45.0, "中性"),
    (28.0, "偏冷"),
    (0.0, "冰点"),
)


def _level_of(score: float) -> str:
    for threshold, label in LEVEL_BANDS:
        if score >= threshold:
            return label
    return "冰点"


#: 情绪聚合结果缓存：{key: (股票数, 扫描时间戳, 交易日), value: 统计字典}
#: 键里带上扫描批次，保证只要底层快照更新就会自动重算。
_sentiment_cache: dict[str, Any] = {}


def _compute_sentiment(metas: list, frames: dict) -> dict[str, Any]:
    """聚合全市场最后一根 K 线的涨跌统计与市场情绪分。

    全市场（约 5500 只）逐个转数组是这一接口的主要开销，
    因此结果按扫描批次缓存，避免 30 秒刷新时重复计算。
    """
    limit_up_count = 0
    limit_down_count = 0
    broken_count = 0
    up_count = 0
    down_count = 0
    flat_count = 0
    total_amount = 0.0
    pct_list: list[float] = []

    for meta in metas:
        arr = arrays_from_df(frames.get(meta.code))
        if arr is None or len(arr) < 2:
            continue
        pct = float(arr.pct[-1])
        close = float(arr.close[-1])
        high = float(arr.high[-1])
        pre_close = float(arr.pre_close[-1])
        total_amount += float(arr.amount[-1])
        pct_list.append(pct)
        limit_price = round(pre_close * (1.0 + meta.limitPct), 2)
        if pct >= meta.limitPct - 0.002:
            limit_up_count += 1
        elif pct <= -(meta.limitPct - 0.002):
            limit_down_count += 1
        elif high >= limit_price - 0.001 and close < limit_price - 0.001:
            broken_count += 1
        if pct > 0.0005:
            up_count += 1
        elif pct < -0.0005:
            down_count += 1
        else:
            flat_count += 1

    total = max(1, len(pct_list))
    avg_pct = float(np.mean(pct_list)) if pct_list else 0.0
    up_ratio = up_count / total
    score = (
        50.0
        + clamp(avg_pct * 900.0, -30.0, 30.0)
        + clamp(limit_up_count / total * 300.0, 0.0, 20.0)
        + clamp((up_ratio - 0.5) * 80.0, -15.0, 15.0)
        - clamp(limit_down_count / total * 200.0, 0.0, 10.0)
    )
    return {
        "limitUpCount": limit_up_count,
        "limitDownCount": limit_down_count,
        "brokenBoardCount": broken_count,
        "upCount": up_count,
        "downCount": down_count,
        "flatCount": flat_count,
        "totalAmount": total_amount,
        "avgPctChg": avg_pct,
        "sampleSize": len(pct_list),
        "score": float(clamp(score, 0.0, 100.0)),
    }


def _fallback_index(frames: dict[str, pd.DataFrame]) -> list[dict]:
    """数据源未提供指数时，用全市场等权走势兜底。"""
    series: list[np.ndarray] = []
    for df in frames.values():
        if df is None or len(df) < 21:
            continue
        closes = df["close"].to_numpy(dtype="float64")[-20:]
        if closes[0] > 0:
            series.append(closes / closes[0] * 1000.0)
    if not series:
        return []
    matrix = np.vstack(series)
    mean_series = matrix.mean(axis=0)
    return [
        {
            "code": "EQW",
            "name": "全市场等权",
            "close": round(float(mean_series[-1]), 2),
            "pctChg": round(float(mean_series[-1] / mean_series[-2] - 1.0), 4),
            "sparkline": [round(float(v), 2) for v in mean_series],
        }
    ]


@router.get("/market/overview", summary="市场情绪总览")
async def market_overview(refresh: bool = Query(False, description="是否强制刷新数据源")):
    """顶部仪表盘数据：情绪、指数、评分分布、热门板块。

    性能说明：全市场（约 5500 只）的情绪聚合需要把每只股票转成数组算涨跌幅，
    单次开销较大。因此这里把聚合结果按「扫描批次」缓存 —— 只要底层日线快照没变
    （``scan_cache_state().at`` 未变），就直接复用上一次的统计结果，
    使开盘期间 30 秒一次的刷新不会被重复聚合拖慢。
    """
    settings = get_settings()
    provider = await get_provider()
    rule_set = rule_store().get()
    try:
        metas = await provider.get_stock_list()
        frames = await provider.get_daily_kline_batch([m.code for m in metas], days=250)
    except DataSourceError as exc:
        return api_err(f"数据源不可用：{exc}", status_code=503)
    if not metas:
        return api_err("数据源未返回任何股票数据", status_code=503)

    state = scan_cache_state()
    fresh = bool(state["at"]) and (time.time() - float(state["at"])) < settings.cache_ttl_seconds
    if refresh or not fresh:
        await scan_universe(provider, rule_set, metas=metas, frames=frames)
        state = scan_cache_state()

    # ---- 情绪聚合（按批次缓存，避免每次请求重算 5500 只） ----
    global _sentiment_cache
    batch_key = (len(metas), round(float(state.get("at") or 0.0), 3), str(state.get("tradeDate") or ""))
    if _sentiment_cache.get("key") == batch_key:
        sentiment = _sentiment_cache["value"]
    else:
        sentiment = _compute_sentiment(metas, frames)
        _sentiment_cache = {"key": batch_key, "value": sentiment}

    limit_up_count = sentiment["limitUpCount"]
    limit_down_count = sentiment["limitDownCount"]
    broken_count = sentiment["brokenBoardCount"]
    up_count = sentiment["upCount"]
    down_count = sentiment["downCount"]
    flat_count = sentiment["flatCount"]
    total_amount = sentiment["totalAmount"]
    score = sentiment["score"]

    indexes = await provider.get_index_snapshot()
    if not indexes:
        indexes = _fallback_index(frames)

    signals = state.get("signals") or []
    buy_count = sum(1 for s in signals if s.verdict == "BUY")
    watch_count = sum(1 for s in signals if s.verdict == "WATCH")
    sector = state.get("sector")
    top_industries: list[dict] = []
    if sector is not None:
        industries = sorted(sector.industries.values(), key=lambda item: item.get("sentimentScore", 0.0), reverse=True)
        for info in industries[:5]:
            top_industries.append(
                {
                    "name": info.get("name"),
                    "pctChg": info.get("pctChg", 0.0),
                    "limitUpCount": info.get("limitUpCount", 0),
                    "sentimentScore": info.get("sentimentScore", 0.0),
                    "leader": info.get("leader", ""),
                }
            )

    data = {
        "dataSource": get_active_data_source(),
        "updatedAt": state.get("lastSyncAt") or now_iso(),
        "tradeDate": state.get("tradeDate") or "",
        "sentiment": {
            "score": round(score, 1),
            "level": _level_of(score),
            "limitUpCount": limit_up_count,
            "limitDownCount": limit_down_count,
            "brokenBoardCount": broken_count,
            "brokenRate": round(broken_count / max(1, broken_count + limit_up_count), 4),
            "upCount": up_count,
            "downCount": down_count,
            "flatCount": flat_count,
            "avgPctChg": round(sentiment["avgPctChg"], 4),
            "totalAmount": round(total_amount, 2),
        },
        "indexes": indexes,
        "scoreDistribution": {
            "buy": buy_count,
            "watch": watch_count,
            "reject": max(0, len(metas) - buy_count - watch_count),
        },
        "topIndustries": top_industries,
    }
    return api_ok(data)
