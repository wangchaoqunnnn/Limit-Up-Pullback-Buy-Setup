"""内置合成行情数据源（离线兜底 / 演示 / 自动化测试的基石）。

设计要点：
1. 固定随机种子（20240101），同种子两次生成结果完全一致（可复现测试）。
2. 约 250 个交易日的日线：价格随机游走 + 市场因子 + 行业联动因子 + 个股噪声，
   日涨跌幅受板块涨跌停制度约束，并含 volume / amount / turnover。
3. **刻意植入**足量「优质首板 + 缩量回调企稳」形态（默认 40 只，远高于 30 只下限），
   同时植入连板、一字板、高位板、烂板、尾盘偷袭板、放量回调、跌破支撑等反面样本。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np
import pandas as pd

from ..config import get_settings
from ..models import StockMeta
from ..utils import limit_pct_of, trade_dates
from .base import BaseProvider, empty_kline

logger = logging.getLogger(__name__)

# 固定随机种子与日历终点，保证完全可复现
SEED = 20240101
DAYS = 250
END_DATE = "2026-02-13"
POSITION_WINDOW = 120

# --------------------------------------------------------------------- 行业词库
INDUSTRY_NAMES: dict[str, tuple[list[str], list[str]]] = {
    "半导体": (
        ["华芯", "中微", "晶合", "兆易", "通富", "士兰", "长电", "北方", "韦尔", "芯源", "捷捷", "富满", "明微", "力芯", "国芯", "华虹"],
        ["微电", "半导", "科技", "电子"],
    ),
    "白酒": (
        ["贵州", "泸州", "洋河", "古井", "今世", "舍得", "酒鬼", "迎驾", "金徽", "伊力", "老白", "衡水", "皇台", "金种", "青稞", "董酒"],
        ["酒业", "酿造", "股份", "酒庄"],
    ),
    "锂电池": (
        ["宁德", "亿纬", "国轩", "欣旺", "孚能", "鹏辉", "珠海", "德赛", "科达", "杉杉", "当升", "容百", "璞泰", "星源", "中科", "天赐"],
        ["锂能", "电池", "新能", "材料"],
    ),
    "光伏设备": (
        ["隆基", "通威", "阳光", "晶澳", "天合", "晶科", "东方", "大全", "上机", "捷佳", "迈为", "金辰", "奥特", "帝尔", "高测", "福斯"],
        ["光伏", "新能", "装备", "科技"],
    ),
    "医疗器械": (
        ["迈瑞", "联影", "乐普", "鱼跃", "万东", "开立", "理邦", "三鑫", "普门", "翔宇", "维力", "戴维", "康泰", "宝莱", "正海", "昊海"],
        ["医疗", "医械", "生物", "健康"],
    ),
    "软件开发": (
        ["用友", "金蝶", "东软", "浪潮", "中软", "太极", "神州", "广联", "恒生", "同花", "顶点", "汉得", "石基", "远光", "超图", "南威"],
        ["软件", "信息", "科技", "数据"],
    ),
    "证券": (
        ["中信", "华泰", "国泰", "招商", "广发", "海通", "申万", "银河", "东方", "光大", "兴业", "方正", "长江", "国金", "财通", "东吴"],
        ["证券", "投行", "资本", "金融"],
    ),
    "银行": (
        ["工商", "建设", "农业", "中国", "交通", "招商", "兴业", "浦发", "民生", "光大", "华夏", "平安", "北京", "南京", "宁波", "杭州"],
        ["银行", "农商", "村镇", "金控"],
    ),
    "房地产开发": (
        ["万科", "保利", "招商", "金地", "新城", "华发", "滨江", "首开", "城建", "光明", "中华", "天健", "深振", "格力", "苏宁", "世茂"],
        ["地产", "置业", "发展", "控股"],
    ),
    "汽车零部件": (
        ["华域", "均胜", "拓普", "旭升", "三花", "银轮", "万里", "凌云", "中鼎", "爱柯", "岱美", "常熟", "双林", "模塑", "宁波", "继峰"],
        ["汽零", "车配", "精密", "工业"],
    ),
    "化学制药": (
        ["恒瑞", "复星", "人福", "华东", "丽珠", "京新", "海正", "现代", "华海", "普洛", "恩华", "科伦", "信立", "天晴", "双鹭", "北陆"],
        ["制药", "药业", "医药", "生物"],
    ),
    "电力": (
        ["华能", "大唐", "华电", "国电", "长江", "川投", "浙能", "皖能", "京能", "内蒙", "粤电", "上海", "深圳", "湖北", "甘肃", "吉电"],
        ["电力", "能源", "水电", "发电"],
    ),
    "食品饮料": (
        ["伊利", "蒙牛", "海天", "双汇", "安井", "三全", "洽洽", "桃李", "绝味", "天味", "千禾", "中炬", "恒顺", "加加", "克明", "盐津"],
        ["食品", "饮品", "味业", "乳业"],
    ),
    "有色金属": (
        ["紫金", "山东", "洛阳", "云铝", "神火", "中国", "云南", "江西", "西部", "宝钛", "贵研", "金钼", "厦门", "锡业", "株冶", "中钨"],
        ["矿业", "有色", "金属", "资源"],
    ),
    "通信设备": (
        ["中兴", "烽火", "亨通", "中天", "光迅", "新易", "太辰", "天孚", "剑桥", "长飞", "通鼎", "富通", "特发", "星网", "三维", "共进"],
        ["通信", "光通", "网络", "科技"],
    ),
    "军工电子": (
        ["中航", "航天", "雷电", "宏达", "振华", "火炬", "国睿", "四创", "天奥", "成都", "武汉", "南京", "桂林", "长盈", "高德", "晨曦"],
        ["军工", "电子", "导航", "装备"],
    ),
    "工程机械": (
        ["三一", "徐工", "中联", "柳工", "山推", "厦工", "雷沃", "恒立", "艾迪", "川润", "巨力", "建设", "河北", "安徽", "山东", "浙江"],
        ["重工", "机械", "装备", "液压"],
    ),
    "物流": (
        ["顺丰", "圆通", "申通", "韵达", "德邦", "中通", "华贸", "嘉友", "密尔", "音飞", "今天", "飞力", "新宁", "恒基", "长久", "天顺"],
        ["物流", "供应链", "仓储", "速运"],
    ),
    "家用电器": (
        ["美的", "格力", "海尔", "海信", "王牌", "长虹", "九阳", "苏泊", "老板", "万和", "华帝", "奥马", "澳柯", "日出", "爱仕", "莱克"],
        ["电器", "家电", "智能", "厨电"],
    ),
    "传媒": (
        ["分众", "芒果", "光线", "华策", "华谊", "慈文", "唐德", "捷成", "蓝色", "省广", "思美", "引力", "天龙", "中视", "电广", "出版"],
        ["传媒", "文化", "影视", "广告"],
    ),
}

INDUSTRIES: list[str] = list(INDUSTRY_NAMES.keys())
# 情绪偏暖的行业（植入优质样本的板块）与偏冷的行业
HOT_INDUSTRIES: list[str] = INDUSTRIES[:8]
COLD_INDUSTRIES: list[str] = INDUSTRIES[8:11]

QUALITY_PLANT_PER_HOT = 8
COLD_PLANT_PER_INDUSTRY = 2

# 演示数据股票名称后缀：名称由真实公司名片段组合而成，
# 但代码为虚构，加此后缀避免用户误认为代码有误。
DEMO_NAME_SUFFIX = "（演示）"

# 各反面样本植入数量
NEGATIVE_PLANT_COUNTS: dict[str, int] = {
    "consecutive": 6,
    "one_word": 6,
    "high_position": 6,
    "weak_seal": 6,
    "tail_sneak": 5,
    "volume_growth": 8,
    "break_support": 5,
    "st_limit": 3,
    # 最新交易日涨停（用于市场情绪面板与「回调天数不足」否决样本）
    "today_limit": 10,
    # 最新交易日炸板（用于 brokenBoardCount 统计）
    "today_broken": 4,
}

INDEX_DEFS = [
    ("000001", "上证指数", 3218.0),
    ("399001", "深证成指", 10233.0),
    ("399006", "创业板指", 2044.0),
]


# --------------------------------------------------------------------- 计划
@dataclass
class StockPlan:
    """单只合成股票的生成计划。"""

    meta: StockMeta
    pattern: str = "normal"
    pullback_days: int = 0
    # 涨停日相对序列末尾再向前推移的根数：
    #   0  -> 形态落在最近（「今日可低吸」信号来源）
    #   >0 -> 形态落在历史区间，为回测提供充足的后续 K 线
    history_offset: int = 0
    base_vol: float = 1.0e5
    avg_turnover: float = 2.5
    beta: float = 1.0
    drift: float = 0.0
    start_price: float = 20.0
    industry_bias: float = 0.0
    index_in_industry: int = 0
    extras: dict[str, Any] = field(default_factory=dict)


def _build_metas(size: int, rng: np.random.Generator) -> list[StockMeta]:
    """构造虚构 A 股列表（主板 / 创业板 / 科创板混合）。"""
    n_main_sh = int(size * 0.33)
    n_main_sz = int(size * 0.27)
    n_gem = int(size * 0.25)
    n_star = max(0, size - n_main_sh - n_main_sz - n_gem)
    codes: list[tuple[str, str]] = []
    for i in range(n_main_sh):
        codes.append((f"60{1000 + i:04d}", "主板"))
    for i in range(n_main_sz):
        codes.append((f"00{1000 + i:04d}", "主板"))
    for i in range(n_gem):
        codes.append((f"30{1000 + i:04d}", "创业板"))
    for i in range(n_star):
        codes.append((f"68{8000 + i:04d}", "科创板"))
    codes = codes[:size]
    order = rng.permutation(len(codes))
    codes = [codes[i] for i in order]

    used_names: set[str] = set()
    metas: list[StockMeta] = []
    for idx, (code, board) in enumerate(codes):
        industry = INDUSTRIES[idx % len(INDUSTRIES)]
        prefixes, suffixes = INDUSTRY_NAMES[industry]
        slot = idx // len(INDUSTRIES)
        base_name = ""
        for k in range(len(suffixes) * 2):
            prefix = prefixes[(slot + k) % len(prefixes)]
            suffix = suffixes[(slot + k) % len(suffixes)]
            candidate = f"{prefix}{suffix}"
            if candidate not in used_names:
                base_name = candidate
                break
        if not base_name:
            base_name = f"{prefixes[slot % len(prefixes)]}{suffixes[slot % len(suffixes)]}{slot}"
        # 关键：名称由真实公司名片段组合而成，若与随机生成的代码直接配对，
        # A 股用户会误以为「代码写错了」。统一追加演示后缀，明确标注虚构属性。
        name = f"{base_name}{DEMO_NAME_SUFFIX}"
        used_names.add(name)
        market = "SH" if code.startswith(("6", "9")) else "SZ"
        metas.append(
            StockMeta(
                code=code,
                name=name,
                market=market,
                board=board,
                industry=industry,
                isSt=False,
                limitPct=limit_pct_of(board, False),
            )
        )
    return metas


def build_plans(size: int, rng: np.random.Generator) -> list[StockPlan]:
    """按行业分布与形态配额，生成每只股票的生成计划。"""
    metas = _build_metas(size, rng)
    by_industry: dict[str, list[StockMeta]] = {name: [] for name in INDUSTRIES}
    for meta in metas:
        by_industry[meta.industry].append(meta)

    plans: dict[str, StockPlan] = {}
    for meta in metas:
        plans[meta.code] = StockPlan(
            meta=meta,
            base_vol=float(rng.uniform(3.0e4, 6.0e5)),
            avg_turnover=float(rng.uniform(1.6, 3.6)),
            beta=float(rng.uniform(0.7, 1.4)),
            drift=float(rng.normal(0.0, 0.0004)),
            start_price=float(rng.uniform(4.5, 90.0)),
        )

    # 1) 优质样本植入到情绪偏暖行业
    for industry in HOT_INDUSTRIES:
        members = by_industry[industry]
        for meta in members[:QUALITY_PLANT_PER_HOT]:
            plan = plans[meta.code]
            plan.pattern = "quality"
            plan.pullback_days = int(rng.integers(3, 9))
            plan.industry_bias = float(rng.uniform(0.006, 0.012))
            # 保证涨停日换手落在 3%~25%
            plan.avg_turnover = float(rng.uniform(2.2, 3.4))

    # 2) 「个股强板块弱」样本：形态优质但板块偏冷
    for industry in COLD_INDUSTRIES:
        members = by_industry[industry]
        for meta in members[QUALITY_PLANT_PER_HOT : QUALITY_PLANT_PER_HOT + COLD_PLANT_PER_INDUSTRY]:
            plan = plans[meta.code]
            plan.pattern = "quality_cold"
            # 回调天数取 6~9 日，使涨停日落在近 5 日窗口之外，板块情绪不被自身涨停抬高
            plan.pullback_days = int(rng.integers(6, 10))
            plan.industry_bias = float(rng.uniform(-0.018, -0.012))

    # 3) 行业近 5 日情绪偏置（对未植入形态的成员统一施加）
    #
    #    注意：必须让「情绪偏暖」的行业之间也有明显梯度，否则所有热门行业的
    #    板块情绪分会一起顶到 100，热度榜上每根条长度完全相同、完全看不出差异。
    hot_sorted = list(HOT_INDUSTRIES)
    rng.shuffle(hot_sorted)
    for rank, industry in enumerate(hot_sorted):
        # 排名越靠前，行业动能越强：0.020 → 0.002（20 个交易日累计约 +49% ~ +4%）。
        # 梯度必须拉得足够开，否则热门行业的情绪分会一起顶到上限，热度榜条长相同。
        bias = 0.020 - rank * (0.018 / max(1, len(hot_sorted) - 1))
        for meta in by_industry[industry]:
            if plans[meta.code].pattern == "normal":
                plans[meta.code].industry_bias = bias
    for industry in COLD_INDUSTRIES:
        # 冷门行业：区间下沿，使情绪分明显偏低但不至于崩盘
        bias = float(rng.uniform(-0.020, -0.014))
        for meta in by_industry[industry]:
            if plans[meta.code].pattern == "normal":
                plans[meta.code].industry_bias = bias
    neutral = [name for name in INDUSTRIES if name not in HOT_INDUSTRIES and name not in COLD_INDUSTRIES]
    for industry in neutral:
        bias = float(rng.uniform(-0.006, 0.003))
        for meta in by_industry[industry]:
            if plans[meta.code].pattern == "normal":
                plans[meta.code].industry_bias = bias

    # 4) 反面样本配额
    candidates = [m for m in metas if plans[m.code].pattern == "normal"]
    cursor = 0
    for pattern, count in NEGATIVE_PLANT_COUNTS.items():
        for _ in range(count):
            if cursor >= len(candidates):
                break
            meta = candidates[cursor]
            cursor += 1
            plan = plans[meta.code]
            plan.pattern = pattern
            plan.pullback_days = int(rng.integers(3, 9))
    # 5) 历史样本间隔：为部分植入样本追加一个「更早的同形态区间」，
    #    使回测（把历史每一天当作「今天」回放）获得跨越多日的足够样本。
    #    当前区间始终保留在序列末端，因此「今日可低吸」榜单不受影响；
    #    历史区间与当前区间至少间隔 12 根 K 线，避免量能与支撑判定互相干扰。
    history_units = (14, 34, 22, 48, 30, 62, 40, 26, 70, 36, 54, 44)
    planted = [
        plan
        for plan in plans.values()
        if plan.pattern not in {"normal", "today_limit", "today_broken"}
    ]
    for index, plan in enumerate(planted):
        if index % 2 == 1:
            plan.history_offset = history_units[(index // 2) % len(history_units)]

    # ST 样本需要改写名称与涨跌幅制度
    for code, plan in plans.items():
        if plan.pattern == "st_limit":
            meta = plan.meta
            plan.meta = meta.model_copy(
                update={
                    "name": f"ST{meta.name[:2]}",
                    "isSt": True,
                    "limitPct": limit_pct_of(meta.board, True),
                }
            )
            plan.avg_turnover = float(rng.uniform(1.8, 3.0))
        elif plan.pattern == "tail_sneak":
            # 尾盘偷袭板的识别条件之一是换手 < 3%，此处刻意压低换手
            plan.avg_turnover = float(rng.uniform(1.0, 1.4))
        elif plan.pattern == "quality_cold":
            plan.avg_turnover = float(rng.uniform(2.2, 3.4))

    return [plans[m.code] for m in metas]


# --------------------------------------------------------------------- 形态
def _smooth_noise(rng: np.random.Generator, length: int, sigma: float) -> np.ndarray:
    """平滑噪声（用于构造不呆板的趋势路径）。"""
    raw = rng.normal(0.0, sigma, length)
    if length >= 3:
        kernel = np.array([0.25, 0.5, 0.25])
        raw = np.convolve(raw, kernel, mode="same")
    return raw


def _override_window(close: np.ndarray, lu_idx: int, pc: float, span: float, rng: np.random.Generator, rising: bool) -> None:
    """覆盖涨停日之前的 POSITION_WINDOW 根收盘价，构造低位/高位形态。

    窗口之前的历史价格被压平到走势起点附近，避免出现价格跳空（保证日涨跌幅合法）。
    """
    start = max(0, lu_idx - POSITION_WINDOW)
    length = lu_idx - start
    if length <= 0:
        return
    ratio = np.linspace(0.0, 1.0, length, endpoint=False)
    if rising:
        levels = (1.0 - span) + span * ratio  # 由低走高 → 高位
    else:
        levels = (1.0 + span) - span * ratio  # 由高走低 → 低位
    noise = _smooth_noise(rng, length, 0.006)
    close[start:lu_idx] = pc * (levels + noise)
    if start > 0:
        base_level = (1.0 - span) if rising else (1.0 + span)
        early = pc * (base_level + _smooth_noise(rng, start, 0.006))
        close[:start] = early
    if length >= 2:
        close[lu_idx - 2] = 0.5 * (close[lu_idx - 2] + pc)


def _rebuild_ohlc(
    open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray, start: int, end: int, rng: np.random.Generator
) -> None:
    """依据收盘价重建窗口内的 open/high/low，保证 K 线自洽。"""
    if end <= start:
        return
    length = end - start
    gaps = rng.normal(0.0, 0.004, length)
    upper = np.abs(rng.normal(0.0, 0.007, length))
    lower = np.abs(rng.normal(0.0, 0.007, length))
    prev = np.concatenate(([close[start - 1] if start > 0 else close[start]], close[start:end - 1]))
    open_[start:end] = prev * (1.0 + gaps)
    high[start:end] = np.maximum(open_[start:end], close[start:end]) * (1.0 + upper)
    low[start:end] = np.minimum(open_[start:end], close[start:end]) * (1.0 - lower)


def _plant_limit_up(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    lu_idx: int,
    meta: StockMeta,
    kind: str,
    base_vol: float,
    rng: np.random.Generator,
) -> float:
    """植入涨停日，返回涨停日成交量。"""
    lim = meta.limitPct
    pc = float(close[lu_idx - 1])
    limit_close = round(pc * (1.0 + lim), 2)

    if kind == "one_word":
        # 一字板：open == high == close，且缩量
        limit_open = limit_close
        limit_low = limit_close
        limit_high = limit_close
        limit_vol = base_vol * float(rng.uniform(0.5, 0.9))
    elif kind == "weak_seal":
        # 炸板：收盘远低于最高价
        limit_open = round(pc * (1.0 + rng.uniform(0.01, 0.04)), 2)
        limit_high = round(limit_close * (1.0 + rng.uniform(0.035, 0.06)), 2)
        limit_low = round(pc * (1.0 + rng.uniform(0.004, 0.02)), 2)
        limit_vol = base_vol * float(rng.uniform(2.0, 3.2))
    elif kind == "tail_sneak":
        # 尾盘偷袭：几乎平开、直拉涨停、换手极低
        limit_open = round(pc * (1.0 + rng.uniform(0.0, 0.02)), 2)
        limit_high = limit_close
        limit_low = round(pc * (1.0 - rng.uniform(0.0, 0.01)), 2)
        limit_vol = base_vol * float(rng.uniform(1.5, 2.1))
    else:
        # 普通实体放量涨停（优质 / 反面样本共用）
        gap = float(rng.uniform(0.015, 0.045))
        limit_open = round(pc * (1.0 + gap), 2)
        limit_high = limit_close
        limit_low = round(pc * (1.0 + rng.uniform(0.002, 0.012)), 2)
        limit_vol = base_vol * float(rng.uniform(1.9, 3.0))
    limit_low = min(limit_low, limit_open, limit_close)
    limit_high = max(limit_high, limit_open, limit_close)

    open_[lu_idx] = limit_open
    high[lu_idx] = limit_high
    low[lu_idx] = limit_low
    close[lu_idx] = limit_close
    volume[lu_idx] = limit_vol
    return limit_vol


def _plant_pullback(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    lu_idx: int,
    days: int,
    limit_vol: float,
    meta: StockMeta,
    mode: str,
    base_vol: float,
    rng: np.random.Generator,
) -> None:
    """植入回调段（缩量企稳 / 放量出货 / 破位砸盘）。"""
    n = len(close)
    # 注意：days 允许为 0（如 today_limit / today_broken 形态，涨停日就在最后一根），
    # 此时不存在回调段，直接返回。务必不要把下限夹到 1，否则会越界写 n。
    days = int(min(max(days, 0), n - lu_idx - 1))
    if days <= 0:
        return
    lim = meta.limitPct
    limit_close = float(close[lu_idx])
    limit_open = float(open_[lu_idx])
    half = (limit_open + limit_close) / 2.0

    if mode == "shrink":
        # 缩量回调：底部落在涨停实体半分位上方；末端 3 日为横盘抬高的小阳线
        floor_by_time = limit_close * (1.0 - 0.024 * max(1, days - 3))
        bottom = max(half * 1.01, floor_by_time)
        decline_days = max(0, days - 3)
        if decline_days > 0:
            closes = np.linspace(limit_close * 0.995, bottom * 0.999, decline_days, endpoint=False)
        else:
            closes = np.array([], dtype="float64")
        tail = bottom * (1.0 + 0.002 * np.arange(1, days - decline_days + 1))
        path = np.concatenate([closes, tail])
        vol_start = float(rng.uniform(0.40, 0.55))
        vols = limit_vol * vol_start * (0.87 ** np.arange(days))
        lows_scale = np.full(days, 0.982)
        for j in range(max(0, days - 3), days):
            lows_scale[j] = 0.985 + 0.006 * (j - (days - 3))
    elif mode == "growth":
        # 放量回调：回调途中量能超过涨停日
        bottom = half * float(rng.uniform(0.97, 1.02))
        path = np.linspace(limit_close * 0.995, bottom, days)
        vols = limit_vol * np.linspace(0.7, 1.35, days)
        lows_scale = np.full(days, 0.982)
    else:  # break
        # 破位砸盘：有效跌破涨停开盘价，并出现大阴线
        target = limit_open * float(rng.uniform(0.93, 0.965))
        path = np.linspace(limit_close * 0.99, target, days)
        if days >= 2:
            path[-1] = path[-2] * 0.955
        vols = limit_vol * np.linspace(0.75, 1.1, days)
        lows_scale = np.full(days, 0.975)

    for k in range(days):
        idx = lu_idx + 1 + k
        prev_close = float(close[idx - 1])
        c = float(path[k])
        # 单日涨跌幅不得超过 -2.9%（避免意外触发大阴线否决，break 模式除外）
        if mode != "break":
            c = max(c, prev_close * (1.0 - 0.028))
        o = prev_close * (1.0 + float(rng.normal(0.0, 0.004)))
        if k == days - 1:
            o = c * (1.0 - 0.003)  # 收尾小阳线
        o = min(max(o, c * 0.99), c * 1.01)
        lo = min(o, c) * lows_scale[k]
        hi = max(o, c) * (1.0 + abs(float(rng.normal(0.0, 0.005))))
        close[idx] = round(c, 2)
        open_[idx] = round(o, 2)
        low[idx] = round(lo, 2)
        high[idx] = round(hi, 2)
        volume[idx] = float(max(1000.0, vols[k]))


# --------------------------------------------------------------------- 主生成
def _plant_previous_limit_up(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    lu_idx: int,
    meta: StockMeta,
    base_vol: float,
    rng: np.random.Generator,
) -> None:
    """在涨停日的前一日再植入一个涨停，构造「连板 / 妖股」形态。"""
    lim = meta.limitPct
    prev_idx = lu_idx - 1
    if prev_idx < 1:
        return
    pc2 = float(close[prev_idx - 1])
    close[prev_idx] = round(pc2 * (1.0 + lim), 2)
    open_[prev_idx] = round(pc2 * (1.0 + rng.uniform(0.015, 0.04)), 2)
    high[prev_idx] = close[prev_idx]
    low[prev_idx] = round(pc2 * (1.0 + rng.uniform(0.002, 0.01)), 2)
    volume[prev_idx] = base_vol * float(rng.uniform(1.8, 2.6))


def _plant_history_continuation(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    start_idx: int,
    end_idx: int,
    end_close: float,
    peak_close: float,
    rise_days: int,
    meta: StockMeta,
    base_vol: float,
    rng: np.random.Generator,
) -> None:
    """历史回调之后的「二波拉升 + 回落衔接」。

    战法赚的正是回调企稳后的第二波主升，因此历史样本必须真的走出二波：
      1. 回调结束后先在 ``rise_days`` 内拉升到 ``peak_close``（二波主升）；
      2. 若距当前区间起点仍有剩余交易日，则平滑回落到 ``end_close``，
         保证末尾与当前区间无缝衔接（不产生非法跳空）。
    """
    days = int(end_idx - start_idx)
    if days <= 0:
        return
    lim = meta.limitPct
    cap_step = lim * 0.6
    rise_days = int(min(max(rise_days, 1), days))
    start_prev = float(close[start_idx - 1])
    peak = min(float(peak_close), start_prev * (1.0 + cap_step) ** rise_days)
    peak = max(peak, start_prev)
    if days > rise_days:
        bottom = max(float(end_close), peak * (1.0 - cap_step) ** (days - rise_days))
    else:
        bottom = peak
    vec = np.concatenate(
        [
            np.linspace(start_prev, peak, rise_days + 1)[1:],
            np.linspace(peak, bottom, days - rise_days + 1)[1:],
        ]
    )
    if len(vec) != days:  # 数值兜底
        vec = np.resize(vec, days)
    for k in range(days):
        idx = start_idx + k
        prev_close = float(close[idx - 1])
        c = min(max(float(vec[k]), prev_close * (1.0 - cap_step)), prev_close * (1.0 + cap_step))
        o = prev_close * (1.0 + float(rng.normal(0.0, 0.003)))
        o = min(max(o, c * 0.992), c * 1.008)
        close[idx] = round(c, 2)
        open_[idx] = round(o, 2)
        low[idx] = round(min(o, c) * 0.99, 2)
        high[idx] = round(max(o, c) * 1.006, 2)
        volume[idx] = float(max(1000.0, base_vol * float(rng.uniform(0.85, 1.4))))


def _plant_historical_episode(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    plan: StockPlan,
    meta: StockMeta,
    current_lu: int,
    current_pullback: int,
    base_vol: float,
    rng: np.random.Generator,
) -> None:
    """在历史区间植入一个与当前区间同形态的完整「涨停 → 回调 → 二波」样本。

    位置约束（n=250、默认回测窗口 120 日）：
      * 涨停日与回调窗口都必须落在近 120 个交易日内，回测才能统计到；
      * 与当前区间保留 >= 4 根间隔，避免量能与支撑判定互相干扰。
    """
    n = len(close)
    hist_pullback = int(max(3, min(plan.pullback_days or 4, 9)))
    current_start = current_lu - current_pullback
    max_lu = min(current_start - hist_pullback - 4, n - 1 - hist_pullback - 5)
    desired = (n - 100) + int(np.clip(plan.history_offset, 0, 52))
    hist_lu = int(min(max(desired, POSITION_WINDOW + 15), max_lu))
    if hist_lu < POSITION_WINDOW + 5 or hist_lu >= max_lu:
        return
    hist_pc = float(close[hist_lu - 1])
    span_hist = 1.25 * meta.limitPct + float(rng.uniform(0.08, 0.20))
    _override_window(close, hist_lu, hist_pc, span_hist, rng, rising=False)
    _rebuild_ohlc(open_, high, low, close, max(0, hist_lu - POSITION_WINDOW), hist_lu, rng)
    hist_limit_vol = _plant_limit_up(open_, high, low, close, volume, hist_lu, meta, "quality", base_vol, rng)
    _plant_pullback(
        open_, high, low, close, volume, hist_lu, hist_pullback, hist_limit_vol, meta, "shrink", base_vol, rng
    )
    cont_start = hist_lu + hist_pullback + 1
    target = float(close[current_start])
    if current_start - 1 > cont_start + 1:
        # 二波主升：以「涨停收盘价的 1.18 倍」为拉升目标，拉升天数控制在
        # 回测默认持有窗口（5 日）之后，保证 T+1~T+5 落在上升段内。
        rise_days = int(min(10, max(4, (current_start - cont_start) // 2)))
        limit_close = float(close[hist_lu])
        peak = limit_close * 1.18
        if peak <= float(close[cont_start - 1]):
            peak = float(close[cont_start - 1]) * 1.12
        _plant_history_continuation(
            open_,
            high,
            low,
            close,
            volume,
            cont_start,
            current_start,
            target,
            peak,
            rise_days,
            meta,
            base_vol,
            rng,
        )


def _simulate(plan: StockPlan, market_factor: np.ndarray, industry_factor: np.ndarray, rng: np.random.Generator) -> pd.DataFrame:
    """生成单只股票的规范日线表。"""
    n = len(market_factor)
    meta = plan.meta
    lim = meta.limitPct
    idio = rng.normal(0.0, 0.013, n)
    ret = plan.drift + plan.beta * market_factor + industry_factor + idio
    ret[:5] *= 0.4
    ret[-5:] += plan.industry_bias
    cap = lim * 0.9
    ret = np.clip(ret, -cap, cap)
    close = plan.start_price * np.cumprod(1.0 + ret)
    close = np.round(close, 2)
    open_ = np.round(close * (1.0 + rng.normal(0.0, 0.004, n)), 2)
    high = np.round(np.maximum(open_, close) * (1.0 + np.abs(rng.normal(0.0, 0.007, n))), 2)
    low = np.round(np.minimum(open_, close) * (1.0 - np.abs(rng.normal(0.0, 0.007, n))), 2)
    volume = np.maximum(1000.0, plan.base_vol * np.exp(rng.normal(0.0, 0.3, n)))

    pattern = plan.pattern
    if pattern != "normal":
        if pattern in {"today_limit", "today_broken"}:
            # 最新交易日涨停 / 炸板：回调天数为 0
            lu_idx = n - 1
            pullback = 0
        else:
            pullback = int(max(3, min(plan.pullback_days or 4, 15, n - POSITION_WINDOW // 2)))
            lu_idx = n - 1 - pullback

        kind = pattern
        if pattern in {"quality_cold", "volume_growth", "break_support", "consecutive", "high_position"}:
            kind = "quality"
        if pattern == "volume_growth":
            pullback_mode = "growth"
        elif pattern == "break_support":
            pullback_mode = "break"
        else:
            pullback_mode = "shrink"

        # (a) 历史同形态区间在「当前区间」重建完成之后再植入（见本段末尾），
        #     否则当前区间的 120 根位置窗口会把历史区间的收盘价整段覆盖掉。

        # (b) 当前区间：始终位于序列末端，供「今日可低吸」信号使用
        pc = float(close[lu_idx - 1])
        if pattern == "high_position":
            span = float(rng.uniform(0.25, 0.40))
            _override_window(close, lu_idx, pc, span, rng, rising=True)
        else:
            # 其余植入样本统一构造「低位首板」形态，保证涨停类型判定不受位置干扰
            span = 1.25 * lim + float(rng.uniform(0.08, 0.20))
            _override_window(close, lu_idx, pc, span, rng, rising=False)
        _rebuild_ohlc(open_, high, low, close, 0, lu_idx, rng)

        if pattern == "consecutive":
            _plant_previous_limit_up(open_, high, low, close, volume, lu_idx, meta, plan.base_vol, rng)
        limit_vol = _plant_limit_up(open_, high, low, close, volume, lu_idx, meta, kind, plan.base_vol, rng)
        _plant_pullback(
            open_, high, low, close, volume, lu_idx, pullback, limit_vol, meta, pullback_mode, plan.base_vol, rng
        )

        # (c) 历史同形态区间：必须放在当前区间重建之后，
        #     否则当前区间的 120 根位置窗口会把历史区间的收盘价整段覆盖。
        if plan.history_offset > 0 and pattern in {"quality", "quality_cold"}:
            _plant_historical_episode(
                open_,
                high,
                low,
                close,
                volume,
                plan,
                meta,
                lu_idx,
                pullback,
                plan.base_vol,
                rng,
            )

    volume = np.round(volume, 0)
    vol_scale = plan.avg_turnover / plan.base_vol
    turnover = np.round(volume * vol_scale, 2)
    pre_close = np.concatenate(([close[0]], close[:-1]))
    pct_chg = close / pre_close - 1.0
    amount = volume * 100.0 * close
    dates = _DATES_CACHE if _DATES_CACHE is not None else trade_dates(n, END_DATE)
    return pd.DataFrame(
        {
            "date": pd.to_datetime(dates),
            "open": open_.astype("float64"),
            "high": high.astype("float64"),
            "low": low.astype("float64"),
            "close": close.astype("float64"),
            "pre_close": pre_close.astype("float64"),
            "pct_chg": pct_chg.astype("float64"),
            "volume": volume.astype("float64"),
            "amount": amount.astype("float64"),
            "turnover": turnover.astype("float64"),
        }
    )


_DATES_CACHE: np.ndarray | None = None


def _clamp_daily_moves(df: pd.DataFrame, meta: StockMeta) -> pd.DataFrame:
    """最终合法性收口：强制任何一天的涨跌幅都不突破该板块的涨跌停制度。

    形态构造过程涉及多段价格改写与拼接，这里做一次统一的兜底校验，
    确保合成数据在交易制度上始终自洽（含 open / high / low 的一致性）。
    """
    out = df.copy()
    limit = float(meta.limitPct)
    close = out["close"].to_numpy(dtype="float64").copy()
    open_ = out["open"].to_numpy(dtype="float64").copy()
    high = out["high"].to_numpy(dtype="float64").copy()
    low = out["low"].to_numpy(dtype="float64").copy()
    for i in range(1, len(close)):
        prev = float(close[i - 1])
        if prev <= 0:
            continue
        upper, lower = prev * (1.0 + limit), prev * (1.0 - limit)
        if close[i] < lower or close[i] > upper:
            close[i] = round(min(max(close[i], lower), upper), 2)
        if open_[i] < lower or open_[i] > upper:
            open_[i] = round(min(max(open_[i], lower), upper), 2)
    high = np.maximum.reduce([high, open_, close])
    low = np.minimum.reduce([low, open_, close])
    out["close"] = close
    out["open"] = open_
    out["high"] = high
    out["low"] = low
    pre_close = np.concatenate(([close[0]], close[:-1]))
    out["pre_close"] = pre_close
    out["pct_chg"] = close / pre_close - 1.0
    out["amount"] = out["volume"].to_numpy(dtype="float64") * 100.0 * close
    return out


def generate_dataset(size: int = 300, seed: int = SEED) -> tuple[list[StockMeta], dict[str, pd.DataFrame], dict[str, Any]]:
    """生成完整合成数据集（可复现）。"""
    global _DATES_CACHE
    _DATES_CACHE = trade_dates(DAYS, END_DATE)
    rng = np.random.default_rng(seed)
    plans = build_plans(size, rng)

    market_factor = rng.normal(0.0002, 0.0045, DAYS)
    industry_factor = {name: rng.normal(0.0, 0.006, DAYS) for name in INDUSTRIES}

    metas: list[StockMeta] = []
    frames: dict[str, pd.DataFrame] = {}
    plans_info: dict[str, str] = {}
    quality_codes: list[str] = []
    patterns_count: dict[str, int] = {}
    for plan in plans:
        meta = plan.meta
        df = _simulate(plan, market_factor, industry_factor[meta.industry], rng)
        df = _clamp_daily_moves(df, meta)
        metas.append(meta)
        frames[meta.code] = df
        plans_info[meta.code] = plan.pattern
        patterns_count[plan.pattern] = patterns_count.get(plan.pattern, 0) + 1
        if plan.pattern == "quality":
            quality_codes.append(meta.code)

    # 指数序列（由市场因子驱动，保证与个股同源）
    indexes: list[dict[str, Any]] = []
    for code, name, base in INDEX_DEFS:
        series = np.round(base * np.cumprod(1.0 + rng.normal(0.0001, 0.006, DAYS)), 2)
        indexes.append(
            {
                "code": code,
                "name": name,
                "close": float(series[-1]),
                "pctChg": float(series[-1] / series[-2] - 1.0),
                "sparkline": [float(x) for x in series[-20:]],
            }
        )

    info = {
        "qualityCodes": quality_codes,
        "qualityCount": len(quality_codes),
        "plans": plans_info,
        "patterns": patterns_count,
        "indexes": indexes,
        "dates": [pd.Timestamp(d).strftime("%Y-%m-%d") for d in _DATES_CACHE],
    }
    return metas, frames, info


class SyntheticProvider(BaseProvider):
    """合成行情数据源（离线可用、结果可复现）。"""

    name = "synthetic"

    def __init__(self, universe_size: int | None = None, seed: int = SEED) -> None:
        settings = get_settings()
        # 演示规模独立于真实股票池规模（UNIVERSE_SIZE 用于真实模式），
        # 否则把真实股票池调到 1000 会顺带把演示数据撑成 1000 只、拖慢离线启动。
        self.universe_size = int(universe_size or settings.synthetic_universe_size)
        self.seed = seed
        self._metas: list[StockMeta] = []
        self._frames: dict[str, pd.DataFrame] = {}
        self.info: dict[str, Any] = {}
        self._built = False

    # ------------------------------------------------------------------ 构建
    def _ensure(self) -> None:
        if self._built:
            return
        metas, frames, info = generate_dataset(self.universe_size, self.seed)
        self._metas, self._frames, self.info = metas, frames, info
        self._built = True
        logger.info(
            "合成数据生成完成：%d 只股票，%d 个交易日，优质样本 %d 只",
            len(metas),
            DAYS,
            info["qualityCount"],
        )

    @property
    def quality_plant_count(self) -> int:
        """植入的「优质首板 + 缩量回调」样本数量。"""
        self._ensure()
        return int(self.info.get("qualityCount", 0))

    def pattern_of(self, code: str) -> str:
        self._ensure()
        return str(self.info.get("plans", {}).get(code, "normal"))

    # ------------------------------------------------------------------ 接口
    async def get_stock_list(self) -> list[StockMeta]:
        self._ensure()
        return list(self._metas)

    async def get_daily_kline(self, code: str, days: int = DAYS) -> pd.DataFrame:
        self._ensure()
        df = self._frames.get(code)
        if df is None or df.empty:
            return empty_kline()
        days = int(max(1, days))
        return df.tail(days).reset_index(drop=True).copy()

    async def get_daily_kline_batch(self, codes: Iterable[str], days: int = DAYS) -> dict[str, pd.DataFrame]:
        self._ensure()
        out: dict[str, pd.DataFrame] = {}
        for code in codes:
            df = self._frames.get(code)
            out[code] = empty_kline() if df is None or df.empty else df.tail(int(days)).reset_index(drop=True).copy()
        return out

    async def get_index_snapshot(self) -> list[dict[str, Any]]:
        self._ensure()
        return [dict(item) for item in self.info.get("indexes", [])]
