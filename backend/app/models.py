"""数据模型（严格对齐 docs/API.md 第 2 节，字段名保持 camelCase 契约）。

为避免 alias 序列化歧义，模型字段直接使用契约中的 camelCase 命名。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

LimitUpType = Literal[
    "QUALITY",
    "ONE_WORD",
    "TAIL_SNEAK",
    "WEAK_SEAL",
    "HIGH_POSITION",
    "CONSECUTIVE",
    "ST_LIMIT",
    "NONE",
]

Verdict = Literal["BUY", "WATCH", "REJECT"]

PoolStatus = Literal["watching", "triggered", "stopped", "target"]

SIGNAL_KEYS: tuple[str, ...] = (
    "volume_shrink",
    "support_hold",
    "intraday_stabilize",
    "kline_bottom",
    "sector_resonance",
)

SIGNAL_NAMES: dict[str, str] = {
    "volume_shrink": "回调持续缩量，量能逐日递减",
    "support_hold": "守住关键支撑，不破安全区间",
    "intraday_stabilize": "分时止跌企稳，低点逐步抬高",
    "kline_bottom": "K线筑底止跌，小阳十字星收尾",
    "sector_resonance": "板块情绪同步回暖，题材有持续性",
}


class StockMeta(BaseModel):
    """股票基础信息。"""

    model_config = ConfigDict(extra="ignore")

    code: str
    name: str
    market: Literal["SH", "SZ", "BJ"]
    board: str
    industry: str
    isSt: bool = False
    limitPct: float = 0.10


class Candle(BaseModel):
    """日线K线。"""

    date: str
    open: float
    high: float
    low: float
    close: float
    preClose: float
    pctChg: float
    volume: float
    amount: float
    turnover: float
    volRatio: float | None = None
    ma5: float | None = None
    ma10: float | None = None
    ma20: float | None = None
    ma60: float | None = None
    isLimitUp: bool = False


class SignalDetail(BaseModel):
    """单个共振信号的判定结果。"""

    key: str
    name: str
    passed: bool
    score: float
    weight: float
    contribution: float
    detail: str
    metrics: dict[str, Any] = Field(default_factory=dict)


class SupportInfo(BaseModel):
    """支撑位快照。"""

    limitOpen: float | None = None
    strongHalf: float | None = None
    ma5: float | None = None
    ma10: float | None = None
    ma20: float | None = None
    activeSupport: float | None = None
    activeSupportName: str | None = None
    distanceToSupportPct: float | None = None


class TradePlan(BaseModel):
    """买卖与风控计划。"""

    buyLow: float
    buyHigh: float
    stopLoss: float
    takeProfit1: float
    takeProfit2: float
    riskReward: float
    positionPct: int
    batchCount: int = 3


class RiskInfo(BaseModel):
    """风险提示。"""

    riskLevel: Literal["低", "中", "高"]
    riskPoints: list[str] = Field(default_factory=list)


class StockSignal(BaseModel):
    """核心筛选结果。"""

    meta: StockMeta
    lastClose: float
    lastDate: str
    limitUpDate: str | None = None
    limitUpType: LimitUpType = "NONE"
    limitUpRejectReason: str | None = None
    limitUpClose: float | None = None
    pullbackDays: int = 0
    pullbackPct: float = 0.0
    retraceRatio: float = 0.0
    score: float = 0.0
    verdict: Verdict = "REJECT"
    signals: list[SignalDetail] = Field(default_factory=list)
    signalSummary: str = ""
    support: SupportInfo = Field(default_factory=SupportInfo)
    plan: TradePlan
    risk: RiskInfo
    sparkline: list[float] = Field(default_factory=list)
    sparklineDates: list[str] = Field(default_factory=list)


class PoolItem(BaseModel):
    """自选低吸池条目。"""

    id: str
    meta: StockMeta
    limitUpDate: str | None = None
    limitUpType: str = "NONE"
    addedAt: str
    addedPrice: float | None = None
    buyLow: float | None = None
    buyHigh: float | None = None
    stopLoss: float | None = None
    takeProfit1: float | None = None
    takeProfit2: float | None = None
    note: str | None = None
    lastClose: float | None = None
    lastDate: str | None = None
    pnlPct: float | None = None
    status: PoolStatus = "watching"
    statusText: str = "观察中"


class Weights(BaseModel):
    """五信号权重。"""

    volume_shrink: float = 0.25
    support_hold: float = 0.25
    intraday_stabilize: float = 0.15
    kline_bottom: float = 0.20
    sector_resonance: float = 0.15


class RuleSet(BaseModel):
    """策略参数。"""

    version: str = "1.0.0"
    weights: Weights = Field(default_factory=Weights)
    buyScore: float = 75.0
    watchScore: float = 55.0
    maxHighPositionRatio: float = 0.80
    minVolRatio: float = 1.2
    minTurnover: float = 3.0
    maxTurnover: float = 25.0
    minPullbackDays: int = 3
    maxPullbackDays: int = 15


class RuleSetUpdate(BaseModel):
    """规则参数局部更新请求体。"""

    model_config = ConfigDict(extra="ignore")

    version: str | None = None
    weights: dict[str, float] | None = None
    buyScore: float | None = None
    watchScore: float | None = None
    maxHighPositionRatio: float | None = None
    minVolRatio: float | None = None
    minTurnover: float | None = None
    maxTurnover: float | None = None
    minPullbackDays: int | None = None
    maxPullbackDays: int | None = None


class ScanRequest(BaseModel):
    """扫描任务请求体。"""

    model_config = ConfigDict(extra="ignore")

    refresh: bool = True
    limitUpType: str = "QUALITY"


class ScanTaskInfo(BaseModel):
    """扫描任务状态。"""

    taskId: str
    status: Literal["pending", "running", "finished", "failed"] = "pending"
    progress: int = 0
    total: int = 0
    startedAt: str | None = None
    finishedAt: str | None = None
    matched: int = 0
    message: str = ""


class PoolCreateRequest(BaseModel):
    """加入低吸池请求体（价格字段省略时由服务端按战法计算）。"""

    model_config = ConfigDict(extra="ignore")

    code: str
    buyLow: float | None = None
    buyHigh: float | None = None
    stopLoss: float | None = None
    takeProfit1: float | None = None
    takeProfit2: float | None = None
    note: str | None = None


# ------------------------------------------------------------------ 规则文案
def build_criteria(rule_set: RuleSet) -> dict[str, list[dict[str, Any]]]:
    """构造 /rules 的 criteria（limitUp / entry / discipline / avoid 四组）。"""
    n = rule_set
    limit_up = [
        {
            "key": "low_position_first_board",
            "name": "只做低位首板，不做连板妖股",
            "enabled": True,
            "desc": (
                "涨停前一日、前两日不得有涨停；涨停日收盘价在近 120 日区间中的位置"
                f"比例必须 ≤ {n.maxHighPositionRatio:.2f}，高位板一律放弃。"
            ),
        },
        {
            "key": "volume_entity_limit_up",
            "name": "只做放量实体涨停，拒绝缩量一字板",
            "enabled": True,
            "desc": (
                f"必须放量换手：量比 ≥ {n.minVolRatio:.2f}、换手率在 "
                f"{n.minTurnover:.0f}%~{n.maxTurnover:.0f}% 之间，"
                "且不得是 open == high == close 的一字板。"
            ),
        },
        {
            "key": "solid_intraday_limit_up",
            "name": "只做日内扎实涨停，拒绝尾盘偷袭板",
            "enabled": True,
            "desc": (
                "排除 close == high 且 (close-open)/pre_close ≥ 6% 且换手 < 3% 的"
                "疑似尾盘偷袭形态，只做日内扎实封板的实体阳线。"
            ),
        },
        {
            "key": "veto_four_types",
            "name": "一票否决烂板/偷袭板/缩量板/高位板",
            "enabled": True,
            "desc": (
                "判定优先级 ST_LIMIT → NONE → CONSECUTIVE → HIGH_POSITION → "
                "ONE_WORD → TAIL_SNEAK → WEAK_SEAL → QUALITY，命中即否决。"
            ),
        },
        {
            "key": "st_limit_separate",
            "name": "ST 股 5% 涨停制度单独识别",
            "enabled": True,
            "desc": "名称含 ST 且涨幅落在 4.8%~5.2% 区间时单独标记为 ST_LIMIT，不作为战法标的。",
        },
    ]

    entry = [
        {
            "key": "volume_shrink",
            "name": "回调持续缩量，量能逐日递减",
            "enabled": True,
            "desc": (
                "回调每一天成交量都小于涨停日成交量且逐日递减；缩量回调是洗盘，放量回调是出货。"
                "回调中出现量能 ≥ 涨停日量能即该信号失败。"
            ),
        },
        {
            "key": "support_hold",
            "name": "守住关键支撑，不破安全区间",
            "enabled": True,
            "desc": (
                "两道硬支撑：① 涨停实体K线二分之一位置 (limitOpen+limitClose)/2；"
                "② 涨停当天开盘价（主力成本底线），有效跌破视为形态破坏。另参考 MA5/MA10/MA20。"
            ),
        },
        {
            "key": "intraday_stabilize",
            "name": "分时止跌企稳，低点逐步抬高",
            "enabled": True,
            "desc": "回调后段（最后 3 日）最低价不再创新低、低点逐步抬高，下跌斜率趋缓，收盘位置走强。",
        },
        {
            "key": "kline_bottom",
            "name": "K线筑底止跌，小阳十字星收尾",
            "enabled": True,
            "desc": "回调末端不得出现单日跌幅 ≤ -3% 的破位大阴线；标准企稳形态为连续小阳、十字星或小阴小阳交替。",
        },
        {
            "key": "sector_resonance",
            "name": "板块情绪同步回暖，题材有持续性",
            "enabled": True,
            "desc": "同行业成分股近 5 日平均涨幅、涨停家数占比、上涨家数占比合成板块情绪分（0~100），个股强板块弱则独木难支。",
        },
    ]

    discipline = [
        {
            "key": "no_early_dip_buy",
            "name": "绝不提前抄底",
            "enabled": True,
            "desc": "回调没有缩量、没有企稳、没有筑底，坚决不提前潜伏，等信号共振再动手。",
        },
        {
            "key": "only_low_first_board",
            "name": "只做低位首板回调",
            "enabled": True,
            "desc": "不碰高位票、高位连板、暴涨过后的标的，只赚首板后回调二波的确定性利润。",
        },
        {
            "key": "no_signal_no_position",
            "name": "无信号坚决空仓",
            "enabled": True,
            "desc": "没有共振信号就耐心空仓等待；炒股赚钱靠等待，不靠频繁操作，少做杂毛、只做精品。",
        },
        {
            "key": "batch_entry",
            "name": "分批入场，不重仓梭哈",
            "enabled": True,
            "desc": "分三批低吸、滚动操作，规避突发风险，拿到完整二波行情。",
        },
        {
            "key": "stop_loss_on_break",
            "name": "跌破支撑果断止损",
            "enabled": True,
            "desc": "有效跌破涨停开盘价底线支撑，说明洗盘变出货，果断离场，不幻想不扛单。",
        },
        {
            "key": "no_emotion_trade",
            "name": "拒绝情绪化交易",
            "enabled": True,
            "desc": "重复做高胜率机会，减少无效操作，靠纪律与复利实现长期稳定收益。",
        },
    ]

    avoid = [
        {
            "key": "broken_volume",
            "name": "回调放量超过涨停日",
            "enabled": True,
            "desc": "主力出逃、筹码崩坏，后续大概率持续阴跌，硬性否决。",
        },
        {
            "key": "heavy_bearish_kline",
            "name": "回调末端大阴线砸盘",
            "enabled": True,
            "desc": "单日跌幅 ≤ -3% 的破位阴线意味着形态彻底破坏，直接放弃。",
        },
        {
            "key": "bad_limit_up_types",
            "name": "烂板 / 偷袭板 / 缩量板 / 高位板",
            "enabled": True,
            "desc": "这四类涨停之后的回调大概率是下跌中继，坚决不碰。",
        },
        {
            "key": "weak_sector",
            "name": "个股强板块弱",
            "enabled": True,
            "desc": "独木难支，容易冲高回落；板块情绪未回暖时不参与。",
        },
        {
            "key": "chase_high",
            "name": "追高接盘",
            "enabled": True,
            "desc": "涨停是主力出货舞台，追高是接盘博弈；只做回调低吸，不追涨停。",
        },
    ]

    return {"limitUp": limit_up, "entry": entry, "discipline": discipline, "avoid": avoid}
