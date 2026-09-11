"""合成数据源测试：可复现性、形态植入数量、涨停与量能特征。"""

from __future__ import annotations

import asyncio
from collections import Counter

import numpy as np
import pandas as pd
import pytest

from app.models import RuleSet
from app.providers.synthetic import SEED, SyntheticProvider, generate_dataset
from app.strategy import arrays_from_df, classify_limit_up, limit_up_flags, scan_universe


@pytest.fixture(scope="module")
def dataset():
    """固定种子生成的数据集（模块级复用）。"""
    return generate_dataset(300, SEED)


# --------------------------------------------------------------------- 可复现
def test_generation_is_reproducible():
    """同种子两次生成结果必须完全一致。"""
    metas_a, frames_a, info_a = generate_dataset(60, SEED)
    metas_b, frames_b, info_b = generate_dataset(60, SEED)
    assert [m.model_dump() for m in metas_a] == [m.model_dump() for m in metas_b]
    assert info_a["qualityCodes"] == info_b["qualityCodes"]
    assert set(frames_a) == set(frames_b)
    for code in list(frames_a)[:10]:
        pd.testing.assert_frame_equal(frames_a[code], frames_b[code])


def test_different_seed_differs():
    """不同种子应产生不同数据（避免退化为静态数据）。"""
    _, frames_a, _ = generate_dataset(20, SEED)
    _, frames_b, _ = generate_dataset(20, SEED + 1)
    code = next(iter(frames_a))
    assert not np.allclose(frames_a[code]["close"].to_numpy(), frames_b[code]["close"].to_numpy())


# --------------------------------------------------------------------- 结构
def test_universe_structure(dataset):
    """虚构 A 股的代码、板块、行业分布与涨跌停制度。"""
    metas, frames, info = dataset
    assert len(metas) == 300
    assert len(frames) == 300
    assert len(set(m.code for m in metas)) == 300
    assert len(set(m.name for m in metas)) == 300
    boards = Counter(m.board for m in metas)
    assert {"主板", "创业板", "科创板"} <= set(boards)
    assert len(set(m.industry for m in metas)) >= 15
    for meta in metas:
        assert len(meta.code) == 6 and meta.code.isdigit()
        assert meta.market in {"SH", "SZ", "BJ"}
        expected = 0.05 if meta.isSt else (0.20 if meta.board in {"创业板", "科创板"} else 0.10)
        assert meta.limitPct == pytest.approx(expected)
        df = frames[meta.code]
        assert list(df.columns) == [
            "date",
            "open",
            "high",
            "low",
            "close",
            "pre_close",
            "pct_chg",
            "volume",
            "amount",
            "turnover",
        ]
        assert len(df) == 250
        assert (df["high"] >= df["low"]).all()
        assert (df["volume"] > 0).all()
        assert (df["turnover"] >= 0).all()
        # 日涨跌幅受涨跌停制度约束
        assert df["pct_chg"].abs().max() <= meta.limitPct + 0.002


def test_quality_plants_count(provider):
    """植入的优质样本数量必须 >= 30。"""
    assert provider.quality_plant_count >= 30
    patterns = Counter(provider.info["patterns"])
    assert patterns["quality"] == provider.quality_plant_count
    # 各类反面样本齐备
    for key in ("consecutive", "one_word", "high_position", "weak_seal", "tail_sneak", "volume_growth", "st_limit"):
        assert patterns.get(key, 0) >= 3, f"缺少反面样本：{key}"


# --------------------------------------------------------------------- 形态
def test_quality_plant_shapes(provider):
    """优质样本：低位首板 + 涨停放量 + 回调缩量且逐日递减。"""
    metas = {m.code: m for m in provider._metas}
    checked = 0
    for code in provider.info["qualityCodes"]:
        meta = metas[code]
        df = provider._frames[code]
        arr = arrays_from_df(df)
        flags = np.flatnonzero(limit_up_flags(meta, arr))
        assert len(flags) >= 1
        lu = int(flags[-1])
        # 涨停涨幅与制度匹配
        assert arr.pct[lu] >= meta.limitPct - 0.002
        # 低位：近 120 日区间位置 <= 0.80
        window = arr.close[max(0, lu - 120) : lu]
        hi, lo = float(window.max()), float(window.min())
        position = (float(arr.close[lu]) - lo) / (hi - lo) if hi > lo else 1.0
        assert position <= 0.80, f"{code} 位置比例 {position:.3f} 过高"
        # 涨停日放量
        assert arr.vol_ratio[lu] >= 1.2
        assert 3.0 <= arr.turnover[lu] <= 25.0
        # 回调缩量且逐日递减
        vols = arr.volume[lu + 1 :]
        assert len(vols) >= 3
        assert np.all(vols < arr.volume[lu])
        assert np.all(np.diff(vols) < 0)
        # 守住涨停实体半分位
        half = (arr.open[lu] + arr.close[lu]) / 2.0
        assert arr.low[lu + 1 :].min() >= arr.open[lu] * 0.995
        assert arr.close[-1] >= half
        checked += 1
    assert checked >= 30


def test_negative_plant_types(provider, rule_set):
    """反面样本能被识别为对应涨停类型。"""
    metas = {m.code: m for m in provider._metas}
    seen: dict[str, int] = {}
    for code, pattern in provider.info["plans"].items():
        if pattern == "normal" or pattern in {"quality", "quality_cold"}:
            continue
        meta = metas[code]
        arr = arrays_from_df(provider._frames[code])
        flags = np.flatnonzero(limit_up_flags(meta, arr))
        if len(flags) == 0:
            continue
        limit_type, _ = classify_limit_up(meta, arr, int(flags[-1]), rule_set)
        seen.setdefault(limit_type, 0)
        seen[limit_type] += 1
    # 至少覆盖连板/一字/高位/烂板/偷袭/ST 六类中的五类
    assert len([k for k in seen if k != "QUALITY"]) >= 4


def test_quality_plants_reach_buy(provider, rule_set):
    """植入的优质样本经扫描后应有 >= 30 只达到 BUY。"""
    signals = asyncio.run(scan_universe(provider, rule_set))
    buys = [s for s in signals if s.verdict == "BUY"]
    assert len(buys) >= 30
    quality_plants = set(provider.info["qualityCodes"])
    planted_buys = [s for s in buys if s.meta.code in quality_plants]
    assert len(planted_buys) >= 30
    for signal in planted_buys[:5]:
        assert signal.limitUpType == "QUALITY"
        assert all(item.passed for item in signal.signals)
        assert signal.score >= rule_set.buyScore
        assert 3 <= signal.pullbackDays <= 15


# --------------------------------------------------------------------- 计算
def test_vol_ratio_matches_manual_calculation(provider):
    """volRatio = 当日成交量 / 前 5 日均量。"""
    code = provider.info["qualityCodes"][0]
    df = provider._frames[code]
    arr = arrays_from_df(df)
    for i in (60, 150, 240):
        manual = float(df["volume"].iloc[i]) / float(df["volume"].iloc[i - 5 : i].mean())
        assert arr.vol_ratio[i] == pytest.approx(manual, rel=1e-9)


def test_batch_and_single_kline_consistency(provider):
    """批量与单只接口返回一致，且均为副本（互不污染）。"""
    code = provider.info["qualityCodes"][0]

    async def run():
        single = await provider.get_daily_kline(code, days=60)
        batch = await provider.get_daily_kline_batch([code], days=60)
        return single, batch[code]

    single, batch = asyncio.run(run())
    pd.testing.assert_frame_equal(single, batch)
    single.loc[0, "close"] = -1.0
    again = asyncio.run(provider.get_daily_kline(code, days=60))
    assert float(again["close"].iloc[0]) > 0
