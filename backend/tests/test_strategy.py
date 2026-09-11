"""战法规则引擎单元测试：用手工构造的日线数据断言各类判定与评分。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.models import RuleSet, StockMeta
from app.strategy import SectorContext, arrays_from_df, classify_limit_up, evaluate_stock

N_DAYS = 250
END_DATE = "2026-02-13"
BASE_VOL = 1.0e5


def make_meta(industry: str = "半导体", board: str = "主板", is_st: bool = False) -> StockMeta:
    limit_pct = 0.05 if is_st else (0.20 if board in {"创业板", "科创板"} else 0.10)
    return StockMeta(
        code="600001",
        name="测试股份",
        market="SH",
        board=board,
        industry=industry,
        isSt=is_st,
        limitPct=limit_pct,
    )


def make_sector(meta: StockMeta, score: float = 88.0) -> SectorContext:
    """构造板块情绪上下文（分数可调，用于验证信号五）。"""
    return SectorContext(
        industries={
            meta.industry: {
                "name": meta.industry,
                "sentimentScore": score,
                "pctChg": 0.021,
                "avgPct5": 0.032,
                "limitUp5Count": 3,
                "limitUpCount": 3,
                "memberCount": 15,
                "upRatio": 0.8,
                "trend": [60.0, 72.0, 80.0, 85.0, score],
                "leader": "600002",
            }
        }
    )


def build_frame(
    kind: str = "quality",
    pullback_days: int = 4,
    board: str = "主板",
    base: float = 20.0,
) -> tuple[StockMeta, pd.DataFrame]:
    """手工构造一只股票 250 个交易日的日线数据。

    kind: quality / consecutive / one_word / high_position / weak_seal / tail_sneak
          / volume_growth / break_support / no_limit_up
    """
    meta = make_meta(board=board)
    limit_pct = meta.limitPct
    n = N_DAYS
    lu = n - 1 - pullback_days

    open_ = np.zeros(n)
    high = np.zeros(n)
    low = np.zeros(n)
    close = np.zeros(n)
    volume = np.full(n, BASE_VOL, dtype="float64")

    # 涨停日之前的走势：近 120 日由高走低（低位首板）或由低走高（高位板）
    span = 1.25 * limit_pct + 0.12
    win_start = max(0, lu - 120)
    seg = np.arange(win_start, lu, dtype="float64")
    ratio = (seg - (win_start - 1)) / max(1.0, (lu - 1) - (win_start - 1))
    if kind == "high_position":
        close[:lu] = 0.65 * base
        close[win_start:lu] = np.round(base * (0.65 + 0.35 * ratio), 2)
    else:
        close[:lu] = (1.0 + span) * base
        close[win_start:lu] = np.round(base * ((1.0 + span) - span * ratio), 2)
    open_[:lu] = np.round(close[:lu] * 0.997, 2)
    high[:lu] = np.round(np.maximum(open_[:lu], close[:lu]) * 1.004, 2)
    low[:lu] = np.round(np.minimum(open_[:lu], close[:lu]) * 0.996, 2)
    volume[:lu] = BASE_VOL * (1.0 + 0.05 * np.sin(np.arange(lu, dtype="float64")))

    pc = float(close[lu - 1])
    limit_close = round(pc * (1.0 + limit_pct), 2)
    turnover = np.full(n, 2.0, dtype="float64")

    if kind == "no_limit_up":
        close[lu:] = close[lu - 1]
        open_[lu:] = close[lu - 1] * 0.999
        high[lu:] = close[lu - 1] * 1.004
        low[lu:] = close[lu - 1] * 0.996
    elif kind == "one_word":
        open_[lu] = high[lu] = low[lu] = close[lu] = limit_close
        volume[lu] = BASE_VOL * 0.7
        turnover[lu] = 4.0
    elif kind == "weak_seal":
        open_[lu] = round(pc * 1.02, 2)
        close[lu] = limit_close
        high[lu] = round(limit_close * 1.045, 2)
        low[lu] = round(pc * 1.005, 2)
        volume[lu] = BASE_VOL * 2.5
        turnover[lu] = 15.0
    elif kind == "tail_sneak":
        open_[lu] = round(pc * 1.005, 2)
        close[lu] = limit_close
        high[lu] = limit_close
        low[lu] = round(pc * 1.001, 2)
        volume[lu] = BASE_VOL * 1.6
        turnover[lu] = 2.0  # 换手 < 3%
    else:
        open_[lu] = round(pc * 1.03, 2)
        close[lu] = limit_close
        high[lu] = limit_close
        low[lu] = round(pc * 1.008, 2)
        volume[lu] = BASE_VOL * 2.5
        turnover[lu] = 15.0

    if kind == "consecutive":
        prev = lu - 1
        pc2 = float(close[prev - 1])
        open_[prev] = round(pc2 * 1.03, 2)
        close[prev] = round(pc2 * (1.0 + limit_pct), 2)
        high[prev] = close[prev]
        low[prev] = round(pc2 * 1.008, 2)
        volume[prev] = BASE_VOL * 2.4
        turnover[prev] = 14.0
        # 第二个涨停基于新的前收盘价
        pc = float(close[lu - 1])
        limit_close = round(pc * (1.0 + limit_pct), 2)
        open_[lu] = round(pc * 1.03, 2)
        close[lu] = limit_close
        high[lu] = limit_close
        low[lu] = round(pc * 1.008, 2)
        volume[lu] = BASE_VOL * 2.5
        turnover[lu] = 15.0

    if kind != "no_limit_up":
        half = (float(open_[lu]) + float(close[lu])) / 2.0
        bottom = half * 1.01
        decline = max(0, pullback_days - 3)
        pieces = []
        if decline > 0:
            pieces.append(np.linspace(float(close[lu]) * 0.99, bottom * 0.999, decline, endpoint=False))
        pieces.append(bottom * (1.0 + 0.002 * np.arange(1, pullback_days - decline + 1)))
        targets = np.concatenate(pieces)
        limit_vol = float(volume[lu])
        for k in range(pullback_days):
            pos = lu + 1 + k
            prev_close = float(close[pos - 1])
            c = float(targets[k])
            if kind == "break_support":
                c = min(c, float(open_[lu]) * 0.96)
                if k == pullback_days - 1:
                    c = prev_close * 0.955  # 大阴线砸盘
            if kind != "break_support":
                c = max(c, prev_close * 0.975)
            o = prev_close * 0.997
            if k == pullback_days - 1:
                o = c * 0.997  # 收尾小阳线
            close[pos] = round(c, 2)
            open_[pos] = round(min(max(o, c * 0.99), c * 1.01), 2)
            low[pos] = round(min(open_[pos], close[pos]) * (0.985 + 0.006 * max(0, k - (pullback_days - 3))), 2)
            high[pos] = round(max(open_[pos], close[pos]) * 1.005, 2)
            if kind == "volume_growth":
                volume[pos] = limit_vol * (0.8 + 0.25 * k) if k != 1 else limit_vol * 1.3
            else:
                volume[pos] = limit_vol * 0.5 * (0.85**k)
            turnover[pos] = 3.0

    pre_close = np.concatenate(([close[0]], close[:-1]))
    df = pd.DataFrame(
        {
            "date": pd.bdate_range(end=END_DATE, periods=n),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "pre_close": pre_close,
            "pct_chg": close / pre_close - 1.0,
            "volume": volume,
            "amount": volume * 100.0 * close,
            "turnover": turnover,
        }
    )
    return meta, df


# --------------------------------------------------------------------- 涨停分类
def test_none_when_no_limit_up():
    meta, df = build_frame("no_limit_up")
    assert evaluate_stock(meta, df, RuleSet()) is None


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("consecutive", "CONSECUTIVE"),
        ("one_word", "ONE_WORD"),
        ("high_position", "HIGH_POSITION"),
        ("weak_seal", "WEAK_SEAL"),
        ("tail_sneak", "TAIL_SNEAK"),
        ("quality", "QUALITY"),
    ],
)
def test_limit_up_classification(kind: str, expected: str):
    """连板 / 一字板 / 高位板 / 烂板 / 偷袭板 必须被正确识别。"""
    meta, df = build_frame(kind)
    arr = arrays_from_df(df)
    lu = int(np.flatnonzero(df["pct_chg"].to_numpy() >= meta.limitPct - 0.002)[-1])
    limit_type, _ = classify_limit_up(meta, arr, lu, RuleSet())
    assert limit_type == expected


@pytest.mark.parametrize("kind", ["consecutive", "one_word", "high_position", "weak_seal", "tail_sneak"])
def test_bad_limit_up_rejected(kind: str):
    """四类一票否决的涨停必须给出 REJECT。"""
    meta, df = build_frame(kind)
    signal = evaluate_stock(meta, df, RuleSet(), make_sector(meta))
    assert signal is not None
    assert signal.verdict == "REJECT"
    expected = {"consecutive": "CONSECUTIVE"}.get(kind, kind.upper())
    assert signal.limitUpType == expected
    assert signal.limitUpRejectReason


def test_st_limit_up_detected():
    """ST 股 5% 涨停按 ST_LIMIT 单独识别。"""
    meta = make_meta(is_st=True)
    arr_meta, df = build_frame("quality", base=10.0)
    # 把涨幅改成 5% 制度
    close = df["close"].to_numpy().copy()
    lu = len(df) - 5
    close[lu] = round(float(close[lu - 1]) * 1.05, 2)
    df = df.copy()
    df["close"] = close
    df["pct_chg"] = df["close"] / df["pre_close"] - 1.0
    arr = arrays_from_df(df)
    limit_type, reason = classify_limit_up(meta, arr, lu, RuleSet())
    assert limit_type == "ST_LIMIT"
    assert "ST" in reason


# --------------------------------------------------------------------- 优质样本
def test_quality_sample_is_buy():
    """标准「优质首板 + 缩量回调 + 守住半分位 + 小阳十字星 + 板块回暖」应给出 BUY。"""
    rule_set = RuleSet()
    meta, df = build_frame("quality", pullback_days=4)
    signal = evaluate_stock(meta, df, rule_set, make_sector(meta, 88.0))
    assert signal is not None
    assert signal.limitUpType == "QUALITY"
    assert signal.verdict == "BUY"
    assert signal.score >= rule_set.buyScore
    assert all(item.passed for item in signal.signals)
    assert signal.pullbackDays == 4
    assert signal.limitUpRejectReason is None
    # 买卖计划自洽
    assert signal.plan.buyLow <= signal.plan.buyHigh
    assert signal.plan.stopLoss < signal.plan.buyLow
    assert signal.plan.riskReward >= 0
    assert signal.plan.batchCount == 3
    assert signal.plan.positionPct in {10, 20, 30, 40}
    # 支撑信息
    assert signal.support.limitOpen == pytest.approx(round(df["open"].iloc[len(df) - 5], 2))
    assert signal.support.strongHalf is not None
    assert len(signal.sparkline) == 20
    assert len(signal.sparklineDates) == 20
    # 五信号权重与贡献
    weights = {s.key: s.weight for s in signal.signals}
    assert weights == {
        "volume_shrink": 0.25,
        "support_hold": 0.25,
        "intraday_stabilize": 0.15,
        "kline_bottom": 0.20,
        "sector_resonance": 0.15,
    }
    total = sum(s.contribution for s in signal.signals)
    assert signal.score == pytest.approx(round(total, 1), abs=0.2)


def test_quality_but_cold_sector_is_watch():
    """个股形态优质但板块情绪偏冷 → 不得给出 BUY。"""
    meta, df = build_frame("quality", pullback_days=5)
    signal = evaluate_stock(meta, df, RuleSet(), make_sector(meta, 30.0))
    assert signal is not None
    assert signal.verdict == "WATCH"
    sector_signal = next(s for s in signal.signals if s.key == "sector_resonance")
    assert sector_signal.passed is False


def test_volume_growth_pullback_is_vetoed():
    """回调放量超过涨停日量能 → 硬性否决。"""
    meta, df = build_frame("volume_growth", pullback_days=5)
    signal = evaluate_stock(meta, df, RuleSet(), make_sector(meta))
    assert signal is not None
    assert signal.verdict == "REJECT"
    assert "放量" in (signal.limitUpRejectReason or "")
    vol_signal = next(s for s in signal.signals if s.key == "volume_shrink")
    assert vol_signal.passed is False
    assert vol_signal.metrics["maxVolRatioToLimit"] >= 1.0


def test_break_support_is_vetoed():
    """有效跌破涨停日开盘价 / 大阴线砸盘 → 硬性否决。"""
    meta, df = build_frame("break_support", pullback_days=5)
    signal = evaluate_stock(meta, df, RuleSet(), make_sector(meta))
    assert signal is not None
    assert signal.verdict == "REJECT"
    reason = signal.limitUpRejectReason or ""
    assert "跌破" in reason or "阴线" in reason
    support_signal = next(s for s in signal.signals if s.key == "support_hold")
    assert support_signal.passed is False


def test_pullback_days_out_of_range():
    """回调天数不足 3 日 → 硬性否决。"""
    meta, df = build_frame("quality", pullback_days=1)
    signal = evaluate_stock(meta, df, RuleSet(), make_sector(meta))
    if signal is not None:  # 若最近涨停日即为今日则无信号
        assert signal.verdict == "REJECT"
        assert "回调" in (signal.limitUpRejectReason or "")


def test_vol_ratio_calculation():
    """volRatio = 当日量 / 前 5 日均量。"""
    meta, df = build_frame("quality", pullback_days=4)
    arr = arrays_from_df(df)
    i = len(df) - 5
    manual = float(df["volume"].iloc[i]) / float(df["volume"].iloc[i - 5 : i].mean())
    assert arr.vol_ratio[i] == pytest.approx(manual, rel=1e-9)
    assert arr.vol_ratio[i] >= RuleSet().minVolRatio
