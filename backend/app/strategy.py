"""战法规则引擎（核心）。

实现《涨停回调低吸战法》全流程：
1. 优质首板认定（八类涨停类型按契约优先级判定）；
2. 五大共振入场信号（缩量 / 支撑 / 企稳 / 筑底 / 板块）；
3. 评分与结论（BUY / WATCH / REJECT）+ 硬性否决；
4. 买卖与风控计划（plan / support / risk）；
5. 全市场扫描（异步 + 分批进度）与历史回放回测。

实现以 numpy/pandas 向量化为主，300 只 × 250 日可在数秒内完成。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

import numpy as np
import pandas as pd

from .config import get_settings
from .models import (
    SIGNAL_KEYS,
    SIGNAL_NAMES,
    Candle,
    RiskInfo,
    RuleSet,
    SignalDetail,
    StockMeta,
    StockSignal,
    SupportInfo,
    TradePlan,
)
from .providers.base import BaseProvider
from .utils import clamp, enrich_candles, nan_to_none, round2, safe_div

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------- 常量
POSITION_WINDOW = 120          # 高位判定窗口（涨停日之前的 120 根K线）
LIMIT_TOLERANCE = 0.002        # 涨停判定容差，避免浮点误差
BREAK_TOLERANCE = 0.005        # 有效跌破的容差（0.5%）
BIG_BEARISH_PCT = -0.03        # 破位大阴线阈值
SMALL_BODY_PCT = 0.015         # 小阳/十字星实体上限
TAIL_SNEAK_BODY = 0.06         # 尾盘偷袭板实体幅度阈值
WEAK_SEAL_GAP = 0.03           # 炸板回落幅度阈值
VOL_SHRINK_TARGET = 0.60       # 缩量打分目标区间（量能比越低分越高）
SECTOR_PASS_SCORE = 55.0       # 板块情绪通过线
DEFAULT_SECTOR_SCORE = 60.0    # 缺少板块数据时的中性偏暖分

# 评分 → 仓位映射
POSITION_TIERS: tuple[tuple[float, int], ...] = ((90.0, 40), (85.0, 30), (80.0, 20))


# --------------------------------------------------------------------- 结构
@dataclass
class Arrays:
    """向量化后的日线数组。"""

    dates: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    pre_close: np.ndarray
    pct: np.ndarray
    volume: np.ndarray
    turnover: np.ndarray
    amount: np.ndarray
    vol_ratio: np.ndarray

    def __len__(self) -> int:  # pragma: no cover - 便捷方法
        return int(len(self.close))


@dataclass
class SectorContext:
    """板块（行业）情绪上下文。"""

    industries: dict[str, dict[str, Any]] = field(default_factory=dict)

    def score_of(self, industry: str) -> float:
        info = self.industries.get(industry)
        if not info:
            return DEFAULT_SECTOR_SCORE
        return float(info.get("sentimentScore", DEFAULT_SECTOR_SCORE))

    def info_of(self, industry: str) -> dict[str, Any]:
        return self.industries.get(industry, {})


# --------------------------------------------------------------------- 预处理
def _num_array(df: pd.DataFrame, name: str) -> np.ndarray | None:
    """快速取数值列（已是数值 dtype 时零拷贝）。"""
    if name not in df.columns:
        return None
    series = df[name]
    if series.dtype.kind in "fiub":
        return series.to_numpy(dtype="float64", copy=False)
    return pd.to_numeric(series, errors="coerce").fillna(0.0).to_numpy(dtype="float64")


def arrays_from_df(df: pd.DataFrame) -> Arrays | None:
    """把日线 DataFrame 转换为 numpy 数组；字段缺失时自动补齐。

    性能敏感：全市场扫描会调用数百次，因此避免 DataFrame 复制与重复类型转换。
    """
    if df is None or len(df) == 0:
        return None
    if not all(col in df.columns for col in ("open", "high", "low", "close")):
        return None
    open_ = _num_array(df, "open")
    high = _num_array(df, "high")
    low = _num_array(df, "low")
    close = _num_array(df, "close")
    if open_ is None or high is None or low is None or close is None or len(close) == 0:
        return None
    n = len(close)

    volume = _num_array(df, "volume")
    if volume is None:
        volume = np.zeros(n, dtype="float64")
    turnover = _num_array(df, "turnover")
    if turnover is None:
        turnover = np.zeros(n, dtype="float64")
    amount = _num_array(df, "amount")
    if amount is None:
        amount = volume * 100.0 * close

    pre_close = _num_array(df, "pre_close")
    if pre_close is None or len(pre_close) != n:
        pre_close = np.concatenate(([open_[0]], close[:-1]))
    pct = _num_array(df, "pct_chg")
    if pct is None or len(pct) != n:
        with np.errstate(divide="ignore", invalid="ignore"):
            pct = np.where(pre_close > 0, close / pre_close - 1.0, 0.0)

    dates = df["date"].to_numpy(copy=False) if "date" in df.columns else np.arange(n).astype("datetime64[D]")
    if not np.issubdtype(dates.dtype, np.datetime64):
        dates = pd.to_datetime(df["date"], errors="coerce").to_numpy()

    # volRatio = 当日量 / 前 5 日均量（不含当日），用前缀和实现
    base = np.full(n, np.nan, dtype="float64")
    if n >= 6:
        csum = np.concatenate(([0.0], np.cumsum(volume)))
        base[5:] = (csum[5:n] - csum[0 : n - 5]) / 5.0
    with np.errstate(divide="ignore", invalid="ignore"):
        vol_ratio = np.where(base > 0, volume / base, np.nan)

    return Arrays(
        dates=dates,
        open=open_,
        high=high,
        low=low,
        close=close,
        pre_close=pre_close,
        pct=pct,
        volume=volume,
        turnover=turnover,
        amount=amount,
        vol_ratio=vol_ratio,
    )


def is_limit_up_at(meta: StockMeta, arr: Arrays, idx: int) -> bool:
    """按板块涨跌停制度判定第 idx 根K线是否涨停。"""
    if idx < 0 or idx >= len(arr):
        return False
    pct = float(arr.pct[idx])
    if meta.isSt:
        return 0.048 <= pct <= 0.052
    return pct >= meta.limitPct - LIMIT_TOLERANCE


def limit_up_flags(meta: StockMeta, arr: Arrays) -> np.ndarray:
    """向量化涨停标记。"""
    if meta.isSt:
        return (arr.pct >= 0.048) & (arr.pct <= 0.052)
    return arr.pct >= (meta.limitPct - LIMIT_TOLERANCE)


def classify_limit_up(meta: StockMeta, arr: Arrays, idx: int, rule_set: RuleSet) -> tuple[str, str | None]:
    """按契约优先级判定涨停类型，返回 (类型, 否决原因)。"""
    pct = float(arr.pct[idx])
    pre_close = float(arr.pre_close[idx])
    open_ = float(arr.open[idx])
    high = float(arr.high[idx])
    close = float(arr.close[idx])
    turnover = float(arr.turnover[idx])
    vol_ratio = float(arr.vol_ratio[idx]) if np.isfinite(arr.vol_ratio[idx]) else None

    # ① ST 股 5% 涨停制度
    if meta.isSt and 0.048 <= pct <= 0.052:
        return "ST_LIMIT", f"ST 股按 5% 涨停制度封板（涨幅 {pct * 100:.2f}%），不属于战法标的"
    # ② 非涨停
    if pct < meta.limitPct - LIMIT_TOLERANCE:
        return "NONE", f"当日涨幅 {pct * 100:.2f}% 未达 {meta.limitPct * 100:.0f}% 涨停阈值"
    # ③ 连板 / 妖股
    for back in (1, 2):
        j = idx - back
        if j >= 0 and is_limit_up_at(meta, arr, j):
            return "CONSECUTIVE", f"涨停前 {back} 日已存在涨停，属于连板妖股，不做"
    # ④ 高位板
    start = max(0, idx - POSITION_WINDOW)
    window = arr.close[start:idx]
    if len(window) >= 20:
        hi = float(np.max(window))
        lo = float(np.min(window))
        position = 1.0 if hi <= lo else (close - lo) / (hi - lo)
    else:
        position = 0.0
    if position > rule_set.maxHighPositionRatio:
        return "HIGH_POSITION", (
            f"涨停日收盘价处于近 {POSITION_WINDOW} 日区间的 {position * 100:.1f}% 位置"
            f"（上限 {rule_set.maxHighPositionRatio * 100:.0f}%），属于高位板"
        )
    # ⑤ 缩量一字板
    same_price = abs(open_ - high) < 1e-6 and abs(high - close) < 1e-6
    open_pct = safe_div(open_ - pre_close, pre_close)
    if same_price:
        return "ONE_WORD", "开盘即封死的一字板（open == high == close），无换手无法参与"
    # 主板阈值 9.5% 等价于涨停幅度 95%，此处按板块制度换算
    if open_pct >= meta.limitPct * 0.95:
        return "ONE_WORD", f"开盘即高开 {open_pct * 100:.2f}%，属于一字板/准一字板"
    if vol_ratio is not None and vol_ratio < rule_set.minVolRatio:
        return "ONE_WORD", (
            f"量比仅 {vol_ratio:.2f}（阈值 {rule_set.minVolRatio:.2f}），属于缩量板，缺乏资金承接"
        )
    # ⑥ 尾盘偷袭板
    if abs(close - high) < 1e-6 and safe_div(close - open_, pre_close) >= TAIL_SNEAK_BODY and turnover < rule_set.minTurnover:
        return "TAIL_SNEAK", (
            f"收盘价等于最高价且实体幅度达 {(close - open_) / pre_close * 100:.2f}%，"
            f"换手仅 {turnover:.2f}%（阈值 {rule_set.minTurnover:.0f}%），疑似尾盘偷袭板"
        )
    # ⑦ 烂板 / 炸板
    if close < high and safe_div(high - close, pre_close) >= WEAK_SEAL_GAP:
        return "WEAK_SEAL", (
            f"最高价回落 {(high - close) / pre_close * 100:.2f}%（阈值 {WEAK_SEAL_GAP * 100:.0f}%），属于炸板/烂板"
        )
    # ⑧ 优质首板
    return "QUALITY", None


# --------------------------------------------------------------------- 信号
def _signal(
    key: str, rule_set: RuleSet, passed: bool, score: float, detail: str, metrics: dict[str, Any]
) -> SignalDetail:
    weight = float(getattr(rule_set.weights, key))
    score = float(clamp(round(score, 1), 0.0, 100.0))
    return SignalDetail(
        key=key,
        name=SIGNAL_NAMES[key],
        passed=bool(passed),
        score=score,
        weight=weight,
        contribution=round(score / 100.0 * weight * 100.0, 1),
        detail=detail,
        metrics=metrics,
    )


def signal_volume_shrink(arr: Arrays, lu_idx: int, rule_set: RuleSet) -> SignalDetail:
    """信号一：回调持续缩量，量能逐日递减。"""
    vols = arr.volume[lu_idx + 1 :]
    limit_vol = float(arr.volume[lu_idx])
    pullback_days = int(len(vols))
    if pullback_days == 0 or limit_vol <= 0:
        return _signal(
            "volume_shrink",
            rule_set,
            False,
            0.0,
            "回调尚未展开或涨停日量能缺失，无法确认缩量",
            {"pullbackDays": pullback_days, "maxVolRatioToLimit": None, "isMonotonicShrink": False, "shrinkStreak": 0},
        )
    max_ratio = float(np.max(vols)) / limit_vol
    diffs = np.diff(vols)
    monotonic = bool(np.all(diffs < 0))
    streak = 0
    for value in diffs[::-1]:
        if value < 0:
            streak += 1
        else:
            break
    if max_ratio >= 1.0:
        score = 0.0
    else:
        score = 100.0 * clamp((1.0 - max_ratio) / VOL_SHRINK_TARGET, 0.0, 1.0)
    if not monotonic:
        score *= 0.75
    passed = bool(monotonic and max_ratio < 1.0)
    seq = "→".join(f"{v / 1e4:.0f}万" for v in vols[-5:])
    detail = (
        f"回调 {pullback_days} 日量能依次 {seq}手"
        f"{'，逐日递减' if monotonic else '，未保持逐日递减（量能反复）'}；"
        f"最大量能仅为涨停日的 {max_ratio:.2f} 倍（阈值 1.00）"
    )
    return _signal(
        "volume_shrink",
        rule_set,
        passed,
        score,
        detail,
        {
            "pullbackDays": pullback_days,
            "maxVolRatioToLimit": round(max_ratio, 4),
            "isMonotonicShrink": monotonic,
            "shrinkStreak": streak,
        },
    )


def signal_support_hold(arr: Arrays, lu_idx: int, supports: dict[str, float | None], rule_set: RuleSet) -> SignalDetail:
    """信号二：守住关键支撑，不破安全区间。"""
    limit_open = float(arr.open[lu_idx])
    limit_close = float(arr.close[lu_idx])
    half = (limit_open + limit_close) / 2.0
    pb_low = arr.low[lu_idx + 1 :]
    last_close = float(arr.close[-1])
    break_level = limit_open * (1.0 - BREAK_TOLERANCE)
    min_low = float(np.min(pb_low)) if len(pb_low) else last_close

    broken = bool(min_low < break_level or last_close < break_level)
    half_held = last_close >= half
    if broken:
        score = 15.0
        passed = False
        detail = (
            f"回调最低 {min_low:.2f} 已有效跌破涨停日开盘价 {limit_open:.2f}"
            f"（底线 {break_level:.2f}），主力成本支撑失守"
        )
    else:
        passed = True
        if half_held:
            score = 100.0
            detail = (
                f"守住涨停实体二分之一位 {half:.2f} 与涨停开盘价 {limit_open:.2f}，"
                f"最新收盘 {last_close:.2f} 位于安全区间上方"
            )
        else:
            ratio = safe_div(last_close - limit_open, half - limit_open, 0.0)
            score = float(clamp(70.0 * ratio, 25.0, 70.0))
            detail = (
                f"跌破实体半分位 {half:.2f} 但未破涨停开盘价 {limit_open:.2f}，"
                f"洗盘转弱，最新收盘 {last_close:.2f}"
            )
    ma_bits = []
    for label in ("ma5", "ma10", "ma20"):
        value = supports.get(label)
        if value:
            ma_bits.append(f"{label.upper()} {value:.2f}")
    if ma_bits:
        detail += "；动态支撑：" + "、".join(ma_bits)
    return _signal(
        "support_hold",
        rule_set,
        passed,
        score,
        detail,
        {
            "limitOpen": round(limit_open, 2),
            "strongHalf": round(half, 2),
            "breakLevel": round(break_level, 2),
            "minPullbackLow": round(min_low, 2),
            "lastClose": round(last_close, 2),
            "halfHeld": half_held,
            "effectiveBreak": broken,
        },
    )


def signal_intraday_stabilize(arr: Arrays, lu_idx: int, rule_set: RuleSet) -> SignalDetail:
    """信号三：分时止跌企稳，低点逐步抬高（日线代理）。"""
    lows = arr.low[lu_idx + 1 :]
    closes = arr.close[lu_idx + 1 :]
    highs = arr.high[lu_idx + 1 :]
    days = int(len(closes))
    if days < 3:
        return _signal(
            "intraday_stabilize",
            rule_set,
            False,
            35.0,
            f"回调仅 {days} 日，尚不足以确认低点抬高",
            {"pullbackDays": days, "risingLows": 0, "slopeEasing": False, "closePositionTrend": False},
        )
    comparisons = [lows[-1] >= lows[-2] * 0.998, lows[-2] >= lows[-3] * 0.998]
    rising_lows = int(sum(1 for item in comparisons if item))
    mid = max(1, days // 2)
    slope_first = safe_div(closes[mid - 1] - closes[0], mid, 0.0)
    slope_last = safe_div(closes[-1] - closes[mid], max(1, days - mid), 0.0)
    slope_easing = bool(abs(slope_last) <= abs(slope_first) + 0.002)
    span = np.where((highs - lows) > 1e-9, (closes - lows) / np.maximum(highs - lows, 1e-9), 0.5)
    recent = float(np.mean(span[-3:]))
    earlier = float(np.mean(span[:-3])) if days > 3 else float(np.mean(span))
    position_up = bool(recent >= earlier - 0.02)
    passed = bool(rising_lows >= 2 and (slope_easing or position_up))
    score = 45.0 * rising_lows / 2.0 + (30.0 if slope_easing else 0.0) + (25.0 if position_up else 0.0)
    detail = (
        f"回调后 3 日最低价 {'逐步抬高' if rising_lows >= 2 else '仍有个别创新低'}"
        f"（抬高 {rising_lows}/2 次）；下跌斜率{'趋缓' if slope_easing else '未明显趋缓'}；"
        f"收盘位置 {earlier:.2f}→{recent:.2f}"
    )
    return _signal(
        "intraday_stabilize",
        rule_set,
        passed,
        score,
        detail,
        {
            "pullbackDays": days,
            "risingLows": rising_lows,
            "slopeEasing": slope_easing,
            "closePositionTrend": position_up,
            "recentClosePosition": round(recent, 4),
        },
    )


def signal_kline_bottom(arr: Arrays, lu_idx: int, rule_set: RuleSet) -> SignalDetail:
    """信号四：K线筑底止跌，小阳十字星收尾。"""
    pcts = arr.pct[lu_idx + 1 :]
    closes = arr.close[lu_idx + 1 :]
    days = int(len(closes))
    if days == 0:
        return _signal(
            "kline_bottom",
            rule_set,
            False,
            0.0,
            "回调尚未展开，无法判断筑底形态",
            {"bigBearishCount": 0, "lastBodyPct": None, "centerHolding": False, "tailNonFalling": False},
        )
    bearish = np.where(pcts <= BIG_BEARISH_PCT)[0]
    big_bearish_count = int(len(bearish))
    last_body = abs(float(arr.close[-1]) - float(arr.open[-1])) / max(float(arr.pre_close[-1]), 1e-9)
    small_body = bool(last_body <= SMALL_BODY_PCT)
    tail = closes[-3:] if days >= 3 else closes
    tail_non_falling = bool(np.all(np.diff(tail) >= -0.005 * tail[:-1])) if len(tail) >= 2 else True
    center_holding = bool(float(closes[-1]) >= float(np.min(closes)) * 0.998)
    center_ok = bool(tail_non_falling or center_holding)
    passed = bool(big_bearish_count == 0 and small_body and center_ok)
    score = 45.0 * (1.0 if small_body else 0.0) + 35.0 * (1.0 if big_bearish_count == 0 else 0.0) + 20.0 * (1.0 if center_ok else 0.0)
    body_text = "小阳/十字星收尾" if small_body else "收尾K线实体偏大"
    detail = (
        f"{body_text}（实体 {last_body * 100:.2f}%，阈值 {SMALL_BODY_PCT * 100:.1f}%）；"
        f"回调期间{'无' if big_bearish_count == 0 else f'{big_bearish_count} 根'}破位大阴线；"
        f"股价重心{'横盘或上移' if center_ok else '仍在下移'}"
    )
    return _signal(
        "kline_bottom",
        rule_set,
        passed,
        score,
        detail,
        {
            "bigBearishCount": big_bearish_count,
            "lastBodyPct": round(last_body, 4),
            "centerHolding": center_holding,
            "tailNonFalling": tail_non_falling,
        },
    )


def signal_sector_resonance(meta: StockMeta, sector: SectorContext | None, rule_set: RuleSet) -> SignalDetail:
    """信号五：板块情绪同步回暖，题材有持续性。"""
    info = sector.info_of(meta.industry) if sector else {}
    score = float(info.get("sentimentScore", DEFAULT_SECTOR_SCORE))
    passed = bool(score >= SECTOR_PASS_SCORE)
    avg_pct = float(info.get("avgPct5", 0.0))
    limit_up_count = int(info.get("limitUp5Count", 0))
    member_count = int(info.get("memberCount", 0))
    up_ratio = float(info.get("upRatio", 0.0))
    trend = info.get("trend", [])
    trend_text = ""
    if trend:
        trend_text = f"，近 5 日情绪 {trend[0]:.0f}→{trend[-1]:.0f}"
    detail = (
        f"所属板块「{meta.industry}」情绪分 {score:.1f}"
        f"（成分股 {member_count} 只，近 5 日平均涨幅 {avg_pct * 100:+.2f}%，"
        f"涨停 {limit_up_count} 家，上涨占比 {up_ratio * 100:.0f}%）{trend_text}；"
        f"{'板块同步回暖，具备共振条件' if passed else '板块情绪偏弱，个股独木难支'}"
    )
    return _signal(
        "sector_resonance",
        rule_set,
        passed,
        score,
        detail,
        {
            "industry": meta.industry,
            "sentimentScore": round(score, 1),
            "avgPct5": round(avg_pct, 4),
            "limitUp5Count": limit_up_count,
            "memberCount": member_count,
            "upRatio": round(up_ratio, 4),
            "passScore": SECTOR_PASS_SCORE,
        },
    )


# --------------------------------------------------------------------- 计划
def build_support_info(arr: Arrays, lu_idx: int, ma: dict[str, float | None]) -> tuple[SupportInfo, str, float]:
    """构造支撑快照，返回 (SupportInfo, 活跃支撑名称, 活跃支撑价)。"""
    limit_open = float(arr.open[lu_idx])
    limit_close = float(arr.close[lu_idx])
    half = (limit_open + limit_close) / 2.0
    last_close = float(arr.close[-1])
    candidates: list[tuple[str, float]] = [("涨停开盘价", limit_open)]
    for label, key in (("MA5", "ma5"), ("MA10", "ma10")):
        value = ma.get(key)
        if value:
            candidates.append((label, float(value)))
    below = [(name, value) for name, value in candidates if value <= last_close]
    if below:
        name, active = max(below, key=lambda item: item[1])
    else:
        name, active = min(candidates, key=lambda item: item[1])
    info = SupportInfo(
        limitOpen=round(limit_open, 2),
        strongHalf=round(half, 2),
        ma5=round2(ma.get("ma5")) if ma.get("ma5") else None,
        ma10=round2(ma.get("ma10")) if ma.get("ma10") else None,
        ma20=round2(ma.get("ma20")) if ma.get("ma20") else None,
        activeSupport=round(active, 2),
        activeSupportName=name,
        distanceToSupportPct=round(safe_div(last_close - active, active, 0.0), 4),
    )
    return info, name, active


def build_trade_plan(
    arr: Arrays, lu_idx: int, score: float, active_support: float, strong_half: float
) -> TradePlan:
    """构造买卖与风控计划。"""
    limit_open = float(arr.open[lu_idx])
    limit_high = float(arr.high[lu_idx])
    limit_close = float(arr.close[lu_idx])
    last_close = float(arr.close[-1])

    buy_low = round(active_support * 1.005, 2)
    buy_high = round(min(strong_half, last_close), 2)
    if buy_high < buy_low:
        buy_high = buy_low
    stop_loss = round(limit_open * 0.99, 2)
    take_profit1 = round(max(limit_high, limit_close), 2)
    take_profit2 = round(limit_high * 1.10, 2)
    buy_mid = (buy_low + buy_high) / 2.0
    risk_reward = round(max(0.0, safe_div(take_profit1 - buy_mid, buy_mid - stop_loss, 0.0)), 2)

    position_pct = 10
    for threshold, pct in POSITION_TIERS:
        if score >= threshold:
            position_pct = pct
            break
    return TradePlan(
        buyLow=buy_low,
        buyHigh=buy_high,
        stopLoss=stop_loss,
        takeProfit1=take_profit1,
        takeProfit2=take_profit2,
        riskReward=risk_reward,
        positionPct=position_pct,
        batchCount=3,
    )


def build_risk(
    arr: Arrays,
    lu_idx: int,
    score: float,
    signals: Sequence[SignalDetail],
    support: SupportInfo,
    sector: SectorContext | None,
    meta: StockMeta,
    rule_set: RuleSet,
) -> RiskInfo:
    """综合评分、回调天数、支撑距离与量能形态给出风险等级与风险点。"""
    points: list[str] = []
    pullback_days = int(len(arr.close) - 1 - lu_idx)
    last_close = float(arr.close[-1])
    ma20 = support.ma20
    vol_signal = next((s for s in signals if s.key == "volume_shrink"), None)
    max_ratio = float(vol_signal.metrics.get("maxVolRatioToLimit") or 0.0) if vol_signal else 0.0
    if vol_signal and not vol_signal.metrics.get("isMonotonicShrink", True):
        points.append("回调量能未能逐日递减，洗盘不够干净")
    if 0 < max_ratio:
        if max_ratio >= 1.0:
            points.append(f"回调出现量能放大至涨停日的 {max_ratio:.2f} 倍，主力出逃嫌疑")
        elif max_ratio >= 0.85:
            points.append(f"回调量能接近涨停日（{max_ratio:.2f} 倍），需警惕抛压")
    if pullback_days <= 3:
        points.append(f"回调仅 {pullback_days} 日，洗盘时间偏短，企稳信号待确认")
    if pullback_days >= 12:
        points.append(f"回调已达 {pullback_days} 日，时间成本偏高，注意题材退潮")
    distance = float(support.distanceToSupportPct or 0.0)
    if distance > 0.06:
        points.append(f"现价距活跃支撑 {distance * 100:.1f}%，追高风险偏大")
    if ma20 and last_close < ma20:
        points.append(f"现价 {last_close:.2f} 仍低于 MA20 {ma20:.2f}，中期趋势尚未修复")
    pcts = arr.pct[lu_idx + 1 :]
    if len(pcts) and float(np.min(pcts)) <= BIG_BEARISH_PCT:
        points.append("回调期间出现单日跌幅超过 3% 的破位阴线")
    sector_score = sector.score_of(meta.industry) if sector else DEFAULT_SECTOR_SCORE
    if sector_score < 70:
        points.append(f"板块「{meta.industry}」情绪仅 {sector_score:.0f} 分，题材持续性待观察")
    if not points:
        points.append("形态与量能均健康，按计划分批低吸并严守止损")

    if score < 60 or (max_ratio >= 1.0):
        level = "高"
    elif score < 75 or len(points) >= 2 or distance > 0.08:
        level = "中"
    else:
        level = "低"
    return RiskInfo(riskLevel=level, riskPoints=points)


# --------------------------------------------------------------------- 评估
def evaluate_stock(
    meta: StockMeta,
    df: pd.DataFrame,
    rule_set: RuleSet,
    sector_ctx: SectorContext | None = None,
    lu_idx_override: int | None = None,
) -> StockSignal | None:
    """评估单只股票；无有效涨停记录时返回 None。

    ``lu_idx_override`` 用于回测：把「本次要评估的涨停日」显式指定为某根 K 线，
    而不是默认取序列中最近一次涨停。这样同一只股票的历史多个区间都能被独立评估。
    """
    arr = arrays_from_df(df)
    if arr is None or len(arr) < 30:
        return None
    flags = limit_up_flags(meta, arr)
    candidates = np.flatnonzero(flags)
    if len(candidates) == 0:
        return None
    if lu_idx_override is not None:
        lu_idx = int(lu_idx_override)
        if lu_idx < 0 or lu_idx >= len(arr) or not bool(flags[lu_idx]):
            return None
    else:
        lu_idx = int(candidates[-1])  # 最近一次涨停日
    last_idx = len(arr) - 1
    pullback_days = int(last_idx - lu_idx)

    limit_close = float(arr.close[lu_idx])
    limit_open = float(arr.open[lu_idx])
    last_close = float(arr.close[last_idx])

    ma: dict[str, float | None] = {}
    for window in (5, 10, 20):
        ma[f"ma{window}"] = round(float(np.mean(arr.close[-window:])), 2) if len(arr) >= window else None

    support_info, support_name, active_support = build_support_info(arr, lu_idx, ma)
    limit_type, reason = classify_limit_up(meta, arr, lu_idx, rule_set)
    hard_rejects: list[str] = []
    if limit_type != "QUALITY":
        hard_rejects.append(reason or f"涨停类型为 {limit_type}，不符合优质首板标准")
    if pullback_days < rule_set.minPullbackDays:
        hard_rejects.append(
            f"涨停后仅回调 {pullback_days} 日，不足 {rule_set.minPullbackDays} 日，洗盘尚不充分"
        )
    elif pullback_days > rule_set.maxPullbackDays:
        hard_rejects.append(
            f"涨停后已过 {pullback_days} 日，超出 {rule_set.maxPullbackDays} 日观察窗口，题材或已退潮"
        )

    signals = [
        signal_volume_shrink(arr, lu_idx, rule_set),
        signal_support_hold(arr, lu_idx, ma, rule_set),
        signal_intraday_stabilize(arr, lu_idx, rule_set),
        signal_kline_bottom(arr, lu_idx, rule_set),
        signal_sector_resonance(meta, sector_ctx, rule_set),
    ]
    by_key = {s.key: s for s in signals}

    if not by_key["volume_shrink"].passed and float(by_key["volume_shrink"].metrics.get("maxVolRatioToLimit") or 0) >= 1.0:
        hard_rejects.append("回调放量超过涨停日量能，主力出逃、筹码崩坏")
    if bool(by_key["support_hold"].metrics.get("effectiveBreak")):
        hard_rejects.append(
            f"回调有效跌破涨停日开盘价 {limit_open:.2f}，形态破坏，按纪律止损离场"
        )
    bearish_count = int(by_key["kline_bottom"].metrics.get("bigBearishCount") or 0)
    if bearish_count > 0:
        hard_rejects.append(f"回调末端出现 {bearish_count} 根单日跌幅超 3% 的大阴线砸盘，形态彻底破坏")

    score = round(sum(s.contribution for s in signals), 1)
    all_passed = all(s.passed for s in signals)
    signal45 = by_key["kline_bottom"].passed and by_key["sector_resonance"].passed
    if hard_rejects:
        verdict = "REJECT"
    elif score >= rule_set.buyScore and all_passed:
        verdict = "BUY"
    elif score >= rule_set.watchScore or signal45:
        verdict = "WATCH"
    else:
        verdict = "REJECT"

    if verdict == "BUY":
        summary = "五信号共振，可分批低吸"
    elif verdict == "WATCH":
        summary = "信号接近共振，纳入观察名单等待企稳确认"
    else:
        summary = hard_rejects[0] if hard_rejects else "共振信号不足，放弃本次机会"

    plan = build_trade_plan(arr, lu_idx, score, active_support, support_info.strongHalf or limit_close)
    risk = build_risk(arr, lu_idx, score, signals, support_info, sector_ctx, meta, rule_set)

    tail = slice(max(0, len(arr) - 20), len(arr))
    sparkline = [round(float(v), 2) for v in arr.close[tail]]
    spark_dates = [pd.Timestamp(d).strftime("%Y-%m-%d") for d in arr.dates[tail]]

    return StockSignal(
        meta=meta,
        lastClose=round(last_close, 2),
        lastDate=pd.Timestamp(arr.dates[last_idx]).strftime("%Y-%m-%d"),
        limitUpDate=pd.Timestamp(arr.dates[lu_idx]).strftime("%Y-%m-%d"),
        limitUpType=limit_type,  # type: ignore[arg-type]
        limitUpRejectReason="；".join(hard_rejects) if hard_rejects else None,
        limitUpClose=round(limit_close, 2),
        pullbackDays=pullback_days,
        pullbackPct=round(safe_div(last_close - limit_close, limit_close, 0.0), 4),
        retraceRatio=round(clamp(safe_div(limit_close - last_close, limit_close - limit_open, 0.0), 0.0, 2.0), 4),
        score=score,
        verdict=verdict,  # type: ignore[arg-type]
        signals=signals,
        signalSummary=summary,
        support=support_info,
        plan=plan,
        risk=risk,
        sparkline=sparkline,
        sparklineDates=spark_dates,
    )


# --------------------------------------------------------------------- 输出
def df_to_candles(meta: StockMeta, df: pd.DataFrame, limit: int = 120) -> list[Candle]:
    """把日线 DataFrame 转换为契约要求的 Candle 列表（含 volRatio 与均线）。"""
    if df is None or len(df) == 0:
        return []
    enriched = enrich_candles(df)
    if limit and limit > 0:
        enriched = enriched.tail(int(limit))
    candles: list[Candle] = []
    for row in enriched.itertuples(index=False):
        pct = float(getattr(row, "pct_chg"))
        candles.append(
            Candle(
                date=pd.Timestamp(getattr(row, "date")).strftime("%Y-%m-%d"),
                open=round(float(getattr(row, "open")), 2),
                high=round(float(getattr(row, "high")), 2),
                low=round(float(getattr(row, "low")), 2),
                close=round(float(getattr(row, "close")), 2),
                preClose=round(float(getattr(row, "pre_close")), 2),
                pctChg=round(pct, 4),
                volume=float(getattr(row, "volume")),
                amount=float(getattr(row, "amount")),
                turnover=round(float(getattr(row, "turnover")), 2),
                volRatio=nan_to_none(getattr(row, "volRatio", None)),
                ma5=nan_to_none(getattr(row, "ma5", None)),
                ma10=nan_to_none(getattr(row, "ma10", None)),
                ma20=nan_to_none(getattr(row, "ma20", None)),
                ma60=nan_to_none(getattr(row, "ma60", None)),
                isLimitUp=bool(pct >= meta.limitPct - LIMIT_TOLERANCE),
            )
        )
    return candles


def limit_up_history(meta: StockMeta, df: pd.DataFrame, rule_set: RuleSet, sector: SectorContext | None) -> list[dict[str, Any]]:
    """历史涨停记录（含类型与当时的战法评分）。"""
    arr = arrays_from_df(df)
    if arr is None or len(arr) < 20:
        return []
    flags = np.flatnonzero(limit_up_flags(meta, arr))
    history: list[dict[str, Any]] = []
    for idx in flags[-6:][::-1]:
        limit_type, reason = classify_limit_up(meta, arr, int(idx), rule_set)
        pullback_days = int(len(arr) - 1 - int(idx))
        score = 0.0
        verdict = "REJECT"
        end = min(len(arr) - 1, int(idx) + rule_set.maxPullbackDays)
        window = df.iloc[: end + 1]
        signal = evaluate_stock(meta, window, rule_set, sector)
        if signal is not None:
            score = signal.score
            verdict = signal.verdict
        history.append(
            {
                "date": pd.Timestamp(arr.dates[int(idx)]).strftime("%Y-%m-%d"),
                "type": limit_type,
                "pullbackDays": pullback_days,
                "score": score,
                "verdict": verdict,
                "reason": reason,
            }
        )
    return history


async def analyze_stock(
    provider: BaseProvider, rule_set: RuleSet, code: str, days: int = 250
) -> dict[str, Any] | None:
    """单只股票完整分析：仅拉取同行业成分股即可得到与全市场扫描一致的板块分。"""
    metas = await provider.get_stock_list()
    meta = next((m for m in metas if m.code == code), None)
    if meta is None:
        return None
    df = await provider.get_daily_kline(code, days=days)
    if df is None or len(df) == 0:
        return None
    members = [m for m in metas if m.industry == meta.industry]
    frames = await provider.get_daily_kline_batch([m.code for m in members], days=days)
    frames[code] = df
    sector = await asyncio.to_thread(build_sector_context, members, frames)
    signal = evaluate_stock(meta, df, rule_set, sector)
    history = await asyncio.to_thread(limit_up_history, meta, df, rule_set, sector)
    industry = sector.info_of(meta.industry)
    return {
        "meta": meta,
        "df": df,
        "signal": signal,
        "sector": sector,
        "industry": {
            "name": meta.industry,
            "sentimentScore": industry.get("sentimentScore", 0.0),
            "pctChg": industry.get("pctChg", 0.0),
            "limitUpCount": industry.get("limitUpCount", 0),
            "memberCount": industry.get("memberCount", 0),
            "trend": industry.get("trend", []),
        },
        "limitUpHistory": history,
    }


def explain_signal(meta: StockMeta, signal: StockSignal | None) -> str:
    """生成个股中文解读文案。"""
    if signal is None:
        return f"{meta.name}（{meta.code}）近 250 个交易日内没有有效涨停记录，不符合「涨停回调低吸」战法的观察前提。"
    passed = [s.name for s in signal.signals if s.passed]
    failed = [s.name for s in signal.signals if not s.passed]
    if signal.verdict == "BUY":
        head = (
            f"{meta.name}（{meta.code}）于 {signal.limitUpDate} 以放量实体首板启动，"
            f"此后缩量回调 {signal.pullbackDays} 日，回调幅度 {signal.pullbackPct * 100:.2f}%，"
            f"守住涨停实体半分位与开盘价支撑，五信号共振，综合评分 {signal.score} 分，可分批低吸。"
        )
    elif signal.verdict == "WATCH":
        head = (
            f"{meta.name}（{meta.code}）{signal.limitUpDate} 涨停后回调 {signal.pullbackDays} 日，"
            f"综合评分 {signal.score} 分，信号尚未完全共振，建议纳入观察名单等待企稳。"
        )
    else:
        head = (
            f"{meta.name}（{meta.code}）{signal.limitUpDate} 涨停（类型 {signal.limitUpType}），"
            f"回调 {signal.pullbackDays} 日，综合评分 {signal.score} 分，未通过战法门槛，建议放弃。"
        )
    if failed:
        head += f" 未通过信号：{'、'.join(failed)}。"
    if passed:
        head += f" 已通过信号：{'、'.join(passed)}。"
    return head


# --------------------------------------------------------------------- 板块
def build_sector_context(metas: Sequence[StockMeta], frames: dict[str, pd.DataFrame]) -> SectorContext:
    """由成分股近 5 日表现合成板块情绪（0~100）。"""
    buckets: dict[str, list[tuple[StockMeta, np.ndarray, np.ndarray]]] = {}
    for meta in metas:
        df = frames.get(meta.code)
        if df is None or len(df) < 6:
            continue
        pct = _num_array(df, "pct_chg")
        close = _num_array(df, "close")
        if pct is None or close is None or len(pct) < 6:
            arr = arrays_from_df(df)
            if arr is None or len(arr) < 6:
                continue
            pct, close = arr.pct, arr.close
        buckets.setdefault(meta.industry, []).append((meta, pct[-5:], close[-6:]))

    industries: dict[str, dict[str, Any]] = {}
    for industry, members in buckets.items():
        pct5: list[float] = []
        pct1: list[float] = []
        limit_up_flags_list: list[bool] = []
        up_flags: list[bool] = []
        daily_matrix: list[np.ndarray] = []
        for meta, last5, closes in members:
            pct5.append(float(closes[-1] / closes[0] - 1.0) if closes[0] > 0 else 0.0)
            pct1.append(float(last5[-1]))
            limit_up_flags_list.append(bool(np.any(last5 >= meta.limitPct - LIMIT_TOLERANCE)))
            up_flags.append(bool(last5[-1] > 0))
            daily_matrix.append(last5)
        member_count = len(members)
        avg_pct5 = float(np.mean(pct5))
        avg_pct1 = float(np.mean(pct1))
        up_ratio = float(np.mean(up_flags))
        limit_up_ratio = float(np.mean(limit_up_flags_list))
        score = (
            50.0
            # 敏感度 420：行业近 5 日平均涨幅是情绪的主导因子，系数过大会让所有
            # 强势行业一起顶到上限，热度榜条长相同、排序失去可读性。
            + clamp(avg_pct5 * 420.0, -30.0, 30.0)
            # 涨停家数占比与上涨家数占比作为次要加分，权重刻意压低，
            # 保证强势行业之间仍有可分辨的分差。
            + clamp(limit_up_ratio * 90.0, 0.0, 6.0)
            + clamp((up_ratio - 0.5) * 40.0, -7.0, 7.0)
        )
        # 上限 98：留出余量避免并列满分。
        score = float(clamp(score, 0.0, 98.0))
        matrix = np.vstack(daily_matrix) if daily_matrix else np.zeros((1, 5))
        avg_daily = matrix.mean(axis=0)
        cumulative = np.cumprod(1.0 + avg_daily)
        trend = [float(clamp(50.0 + (value - 1.0) * 700.0, 0.0, 100.0)) for value in cumulative]
        leader = max(members, key=lambda item: float(item[1].sum()))[0].code
        industries[industry] = {
            "name": industry,
            "sentimentScore": round(score, 1),
            "pctChg": round(avg_pct1, 4),
            "avgPct5": round(avg_pct5, 4),
            "limitUp5Count": int(sum(limit_up_flags_list)),
            "limitUpCount": int(sum(limit_up_flags_list)),
            "memberCount": member_count,
            "upRatio": round(up_ratio, 4),
            "trend": [round(v, 1) for v in trend],
            "leader": leader,
        }
    return SectorContext(industries=industries)


# --------------------------------------------------------------------- 扫描
_scan_cache: dict[str, Any] = {"at": 0.0, "signals": None, "tradeDate": "", "sector": None, "universeSize": 0}


def _evaluate_all(
    metas: Sequence[StockMeta],
    frames: dict[str, pd.DataFrame],
    rule_set: RuleSet,
    sector: SectorContext,
    progress_cb: Callable[[int, int], None] | None = None,
    chunk_size: int = 25,
) -> list[StockSignal]:
    """同步批量评估（分批回调进度），由 asyncio.to_thread 调用。"""
    results: list[StockSignal] = []
    total = len(metas)
    done = 0
    for meta in metas:
        signal = evaluate_stock(meta, frames.get(meta.code), rule_set, sector)
        if signal is not None:
            results.append(signal)
        done += 1
        if progress_cb and (done % chunk_size == 0 or done == total):
            progress_cb(done, total)
    return results


async def scan_universe(
    provider: BaseProvider,
    rule_set: RuleSet,
    progress_cb: Callable[[int, int], None] | None = None,
    days: int = 250,
    metas: Sequence[StockMeta] | None = None,
    frames: dict[str, pd.DataFrame] | None = None,
) -> list[StockSignal]:
    """扫描全市场，返回全部被评估标的（含 REJECT，便于统计与对照）。"""
    if metas is None:
        metas = await provider.get_stock_list()
    if frames is None:
        frames = await provider.get_daily_kline_batch([m.code for m in metas], days=days)
    sector = await asyncio.to_thread(build_sector_context, metas, frames)
    signals = await asyncio.to_thread(_evaluate_all, metas, frames, rule_set, sector, progress_cb)
    trade_date = ""
    if frames:
        first = next(iter(frames.values()))
        if first is not None and len(first):
            trade_date = pd.Timestamp(first["date"].iloc[-1]).strftime("%Y-%m-%d")
    _scan_cache.update(
        {
            "at": time.time(),
            "signals": signals,
            "tradeDate": trade_date,
            "sector": sector,
            "universeSize": len(metas),
        }
    )
    return signals


async def get_market_snapshot(
    provider: BaseProvider, rule_set: RuleSet, refresh: bool = False
) -> dict[str, Any]:
    """带 TTL 的全市场快照：信号列表 + 板块情绪 + 交易日 + universe 规模。"""
    ttl = int(get_settings().cache_ttl_seconds)
    if not refresh and _scan_cache.get("signals") is not None and time.time() - float(_scan_cache.get("at", 0.0)) < ttl:
        return _scan_cache
    await scan_universe(provider, rule_set)
    return _scan_cache


async def get_cached_scan(
    provider: BaseProvider, rule_set: RuleSet, refresh: bool = False
) -> tuple[list[StockSignal], str]:
    """兼容用法：返回 (信号列表, 交易日)。"""
    snapshot = await get_market_snapshot(provider, rule_set, refresh=refresh)
    return list(snapshot.get("signals") or []), str(snapshot.get("tradeDate") or "")


def scan_cache_state() -> dict[str, Any]:
    """扫描缓存状态（供 /health 使用）。"""
    at = float(_scan_cache.get("at", 0.0))
    signals = _scan_cache.get("signals")
    return {
        "at": at,
        "lastSyncAt": (
            pd.Timestamp(at, unit="s", tz="Asia/Shanghai").isoformat(timespec="seconds") if at > 0 else None
        ),
        "signals": signals,
        "matched": len(signals) if signals is not None else 0,
        "tradeDate": _scan_cache.get("tradeDate") or "",
        "universeSize": int(_scan_cache.get("universeSize") or 0),
        "sector": _scan_cache.get("sector"),
    }


def clear_scan_cache() -> None:
    _scan_cache.update({"at": 0.0, "signals": None, "tradeDate": "", "sector": None, "universeSize": 0})


# --------------------------------------------------------------------- 回测
_backtest_cache: dict[str, Any] = {}
BACKTEST_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("<-5%", -1.0, -0.05),
    ("-5%~-2%", -0.05, -0.02),
    ("-2%~0%", -0.02, 0.0),
    ("0%~2%", 0.0, 0.02),
    ("2%~5%", 0.02, 0.05),
    (">5%", 0.05, 1.0),
)


def _replay_stock(
    meta: StockMeta,
    df: pd.DataFrame,
    rule_set: RuleSet,
    sector: SectorContext,
    lookback_days: int,
    min_score: float,
    horizon: int,
) -> list[dict[str, Any]]:
    """对单只股票逐日回放：把第 t 日当作今天，用 t 之前的数据判定信号。"""
    arr = arrays_from_df(df)
    if arr is None or len(arr) < 60:
        return []
    flags = limit_up_flags(meta, arr)
    candidates = np.flatnonzero(flags)
    n = len(arr)
    trades: list[dict[str, Any]] = []
    start_idx = max(0, n - lookback_days)
    for lu_idx in candidates:
        lu_idx = int(lu_idx)
        if lu_idx < start_idx - rule_set.maxPullbackDays:
            continue

        # 廉价预筛（性能关键）：真实行情下每只股票平均有 6+ 个涨停候选，
        # 每个候选最多要回放 13 个时间点，全市场会产生 5 万次以上的
        # evaluate_stock 调用（每次都要重建数组、算均线、跑分类），实测超过 2 分钟。
        # 这里先用纯 numpy 做三项必要条件的快速判断，把绝大多数不可能达标的
        # 候选直接排除，只对少数「看起来像优质首板」的候选做完整判定。
        limit_open = float(arr.open[lu_idx])
        limit_close = float(arr.close[lu_idx])
        if limit_close <= 0 or limit_open <= 0:
            continue
        # ① 首板：涨停日之前两天不得有涨停
        prev_flags = flags[max(0, lu_idx - 2) : lu_idx]
        if bool(np.any(prev_flags)):
            continue
        # ② 高度：涨停日收盘处于近 120 日区间的相对低位
        pos_start = max(0, lu_idx - POSITION_WINDOW)
        window = arr.close[pos_start:lu_idx]
        if len(window) >= 20:
            hi = float(np.max(window))
            lo = float(np.min(window))
            if hi > lo and (limit_close - lo) / (hi - lo) > rule_set.maxHighPositionRatio:
                continue
        # ③ 放量：涨停日成交量需明显高于前 5 日均量
        if lu_idx >= 5:
            base_vol = float(np.mean(arr.volume[lu_idx - 5 : lu_idx]))
            if base_vol > 0 and float(arr.volume[lu_idx]) / base_vol < rule_set.minVolRatio:
                continue
        # ④ 回调期不得跌破涨停日开盘价（跌破即形态破坏，硬性否决）
        break_level = limit_open * (1.0 - BREAK_TOLERANCE)
        if lu_idx + 1 < n and float(np.min(arr.low[lu_idx + 1 :])) < break_level:
            continue

        for t in range(lu_idx + rule_set.minPullbackDays, min(lu_idx + rule_set.maxPullbackDays, n - 1 - horizon) + 1):
            if t < start_idx:
                continue
            window = df.iloc[: t + 1]
            signal = evaluate_stock(meta, window, rule_set, sector, lu_idx_override=int(lu_idx))
            if signal is None or signal.score < min_score:
                continue
            entry = float(arr.close[t])
            exit_price = float(arr.close[t + horizon])
            future_high = float(np.max(arr.high[t + 1 : t + 1 + horizon]))
            future_low = float(np.min(arr.low[t + 1 : t + 1 + horizon]))
            trades.append(
                {
                    "code": meta.code,
                    "name": meta.name,
                    "signalDate": pd.Timestamp(arr.dates[t]).strftime("%Y-%m-%d"),
                    "entryPrice": round(entry, 2),
                    "exitPrice": round(exit_price, 2),
                    "returnPct": round(exit_price / entry - 1.0, 4),
                    "maxGainPct": round(future_high / entry - 1.0, 4),
                    "maxLossPct": round(future_low / entry - 1.0, 4),
                    "score": signal.score,
                    "holdDays": horizon,
                    "signalKeys": [s.key for s in signal.signals if s.passed],
                }
            )
            break  # 同一轮涨停只取首次触发
    return trades


def backtest_sync(
    metas: Sequence[StockMeta],
    frames: dict[str, pd.DataFrame],
    rule_set: RuleSet,
    lookback_days: int = 120,
    min_score: float = 75.0,
    horizon: int = 5,
) -> dict[str, Any]:
    """同步回测主流程（供 to_thread 调用）。"""
    sector = build_sector_context(metas, frames)
    trades: list[dict[str, Any]] = []
    for meta in metas:
        df = frames.get(meta.code)
        if df is None or len(df) == 0:
            continue
        trades.extend(_replay_stock(meta, df, rule_set, sector, lookback_days, min_score, horizon))

    sample_size = len(trades)
    wins = [t for t in trades if t["returnPct"] > 0]
    losses = [t for t in trades if t["returnPct"] <= 0]
    win_rate = safe_div(len(wins), sample_size, 0.0)
    avg_return = float(np.mean([t["returnPct"] for t in trades])) if trades else 0.0
    avg_win = float(np.mean([t["returnPct"] for t in wins])) if wins else 0.0
    avg_loss = float(np.mean([t["returnPct"] for t in losses])) if losses else 0.0
    gross_profit = float(sum(t["returnPct"] for t in wins))
    gross_loss = abs(float(sum(t["returnPct"] for t in losses)))
    profit_factor = round(safe_div(gross_profit, gross_loss, 0.0), 2)

    # 等权累乘净值曲线（按信号日聚合后累乘）
    by_date: dict[str, list[float]] = {}
    for trade in trades:
        by_date.setdefault(trade["signalDate"], []).append(trade["returnPct"])
    curve: list[dict[str, Any]] = []
    equity = 100.0
    peak = 100.0
    max_drawdown = 0.0
    for date_key in sorted(by_date.keys()):
        day_return = float(np.mean(by_date[date_key]))
        equity *= 1.0 + day_return
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity / peak - 1.0)
        curve.append({"date": date_key, "index": round(equity, 2), "equity": round(equity, 2)})

    distribution = []
    for label, low, high in BACKTEST_BUCKETS:
        if label == "<-5%":
            count = sum(1 for t in trades if t["returnPct"] < -0.05)
        elif label == ">5%":
            count = sum(1 for t in trades if t["returnPct"] > 0.05)
        else:
            count = sum(1 for t in trades if low <= t["returnPct"] < high)
        distribution.append({"bucket": label, "count": int(count)})

    by_signal = []
    for key in SIGNAL_KEYS:
        subset = [t for t in trades if key in t["signalKeys"]]
        if not subset:
            by_signal.append({"key": key, "name": SIGNAL_NAMES[key], "sampleSize": 0, "winRate": 0.0, "avgReturn": 0.0})
            continue
        subset_wins = sum(1 for t in subset if t["returnPct"] > 0)
        by_signal.append(
            {
                "key": key,
                "name": SIGNAL_NAMES[key],
                "sampleSize": len(subset),
                "winRate": round(safe_div(subset_wins, len(subset), 0.0), 4),
                "avgReturn": round(float(np.mean([t["returnPct"] for t in subset])), 4),
            }
        )

    avg_max_gain = float(np.mean([t["maxGainPct"] for t in trades])) if trades else 0.0
    avg_max_loss = float(np.mean([t["maxLossPct"] for t in trades])) if trades else 0.0
    trades_out = sorted(trades, key=lambda item: (item["signalDate"], item["code"]))[:200]
    for trade in trades_out:
        trade.pop("signalKeys", None)

    return {
        "lookbackDays": lookback_days,
        "minScore": float(min_score),
        "horizon": horizon,
        "sampleSize": sample_size,
        "winRate": round(win_rate, 4),
        "avgReturn": round(avg_return, 4),
        "avgWin": round(avg_win, 4),
        "avgLoss": round(avg_loss, 4),
        "profitFactor": profit_factor,
        "maxDrawdown": round(max_drawdown, 4),
        "avgMaxGain": round(avg_max_gain, 4),
        "avgMaxLoss": round(avg_max_loss, 4),
        "returnCurve": curve,
        "returnDistribution": distribution,
        "bySignal": by_signal,
        "trades": trades_out,
    }


async def backtest(
    provider: BaseProvider,
    rule_set: RuleSet,
    lookback_days: int = 120,
    min_score: float = 75.0,
    horizon: int = 5,
    max_stocks: int = 120,
) -> dict[str, Any]:
    """历史回放回测；为控制耗时对股票数量做确定性抽样（上限 max_stocks）。"""
    metas = await provider.get_stock_list()
    if not metas:
        return {
            "lookbackDays": lookback_days,
            "minScore": float(min_score),
            "horizon": horizon,
            "sampleSize": 0,
            "winRate": 0.0,
            "avgReturn": 0.0,
            "avgWin": 0.0,
            "avgLoss": 0.0,
            "profitFactor": 0.0,
            "maxDrawdown": 0.0,
            "avgMaxGain": 0.0,
            "avgMaxLoss": 0.0,
            "returnCurve": [],
            "returnDistribution": [{"bucket": label, "count": 0} for label, _, _ in BACKTEST_BUCKETS],
            "bySignal": [],
            "trades": [],
        }
    codes = [m.code for m in metas]
    frames = await provider.get_daily_kline_batch(codes, days=250)
    # 确定性抽样：优先保留具备涨停形态的标的，再按等距补足
    with_limit: list[StockMeta] = []
    without: list[StockMeta] = []
    for meta in metas:
        df = frames.get(meta.code)
        arr = arrays_from_df(df) if df is not None and len(df) else None
        if arr is not None and bool(np.any(limit_up_flags(meta, arr))):
            with_limit.append(meta)
        else:
            without.append(meta)
    sample: list[StockMeta] = with_limit[:max_stocks]
    if len(sample) < max_stocks and without:
        step = max(1, len(without) // max(1, max_stocks - len(sample)))
        sample.extend(without[::step][: max_stocks - len(sample)])
    result = await asyncio.to_thread(backtest_sync, sample, frames, rule_set, lookback_days, min_score, horizon)
    result["sampledStocks"] = len(sample)
    result["universeSize"] = len(metas)
    return result
