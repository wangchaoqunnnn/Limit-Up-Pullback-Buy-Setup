"""腾讯财经行情数据源适配器（qt.gtimg.cn / web.ifzq.gtimg.cn / proxy.finance.qq.com）。

== 使用的三个公开接口 ==

1. **实时批量行情**：``GET https://qt.gtimg.cn/q=sh600519,sz000001``
   返回 **GB18030** 编码文本（必须用 ``get_text(url, encoding="gb18030")`` 解码），
   每只股票一行::

       v_sh600519="1~贵州茅台~600519~1275.16~...~";

   以 ``~`` 分隔，实测共 **88** 个字段，下表索引均由本机实测确认
   （见下方 ``F_*`` 常量）。解析失败的单只直接跳过，不影响整批。

2. **日线**：``GET https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh600519,day,,,250,qfq``
   （同一份数据还有多个等价入口，见 ``KLINE_BASES``：主入口被反爬挑战时会自动切换）
   ``data.<symbol>.qfqday``（前复权）或 ``data.<symbol>.day``（不复权）为 bar 数组::

       [["2026-09-11", "1285.15", "1275.16", "1286.15", "1263.01", "34801"], ...]

   **顺序是「日期、开、收、高、低、成交量(手)」，不是 OHLC**，这是本接口最容易写错的地方，
   已由实测数据交叉验证：``开=1285.15 收=1275.16 高=1286.15 低=1263.01`` 与实时接口的
   今开/最新/最高/最低完全一致（见测试 ``test_parse_kline_bars_open_close_high_low``）。
   优先 ``qfqday``，回退 ``day``（指数只有 ``day``，没有 ``qfqday``）；
   两者都没有视为失败。返回根数少于请求天数**不算错误**（新股/次新股正常），≥1 根即可用。
   数组偶尔多出第 7 个元素（附加信息），忽略。

3. **全市场排行榜（股票列表）**：
   ``GET https://proxy.finance.qq.com/cgi/cgi-bin/rank/hs/getBoardRankList``
   ``?board_code=aStock&sort_type=turnover&direct=down&offset=0&count=100``
   返回 ``{"code":0,"data":{"rank_list":[{"code":"sz300308","name":"中际旭创",
   "turnover":"2997407","zdf":"4.03","zxj":"926.00","stock_type":"GP-A-CYB",...}],
   "offset":0,"total":4602}}``。
   实测 ``sort_type=turnover&direct=down`` 就是**成交额降序**（单位万元），
   分页用 ``offset``（``count`` 实测 200 可用、500 返回空数组，故单页取 100），
   无需 Referer。详见 ``list_stocks_ranked()``。

== 单位与内部约定对齐（``base.KLINE_COLUMNS`` / 既有 provider）==

======================  ==================  ==================================
字段                    接口原始单位        转换后
======================  ==================  ==================================
``volume``（字段 6/36）  手                  手（原样）
``amount``（字段 37）    万元                **元（×10000）**
``pctChg``（字段 32）    百分数              **小数（÷100）**
``turnover``（字段 38）  %                   %（原样）
======================  ==================  ==================================

腾讯**提供**全市场股票列表：走排行榜接口 ``getBoardRankList``（成交额降序 + offset 分页），
``supports_stock_list = True``；``list_stocks()`` 等价于 ``list_stocks_ranked(universe_size)``，
``list_stocks_ranked(limit)`` 只取成交额前 N 只，因此股票池天然是流动性最好的活跃股
（战法要的「涨停 + 放量」标的几乎都在其中），也避免了翻遍全市场。

**日线注意**：bar 数组只有 6 列，不含成交额与换手率，交给 ``frame_from_records`` 后
``amount`` 会按 ``volume * 100 * close`` 估算、``turnover`` 填 0；``pre_close``/``pct_chg``
由前一日收盘自动推导。指数同样走该接口（只有 ``day`` 键，成交量单位是手）。
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from typing import Any, Iterable, Sequence

import pandas as pd

from ..models import StockMeta
from .base import frame_from_records
from .source_base import (
    MarketSource,
    MarketSourceError,
    chunked,
    make_meta,
    normalize_code,
    prefixed,
)

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------- URL
QT_URL = "https://qt.gtimg.cn/q="
#: 日线入口（多个等价 host）。实测同一份数据在下列入口都能取到；
#: 主入口 ``web.ifzq.gtimg.cn/appstock/app/fqkline/get`` 在被高频访问后会触发腾讯反爬，
#: 返回 **HTTP 501 + JS 校验页**（此时实时接口 qt.gtimg.cn 仍正常），
#: 所以这里做入口级故障转移：某个入口失败就换下一个，并把成功的入口记为优先。
KLINE_BASES: tuple[str, ...] = (
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
    "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/fqkline/get",
    "https://ifzq.gtimg.cn/appstock/app/fqkline/get",
    "https://web.ifzq.gtimg.cn/appstock/app/newfqkline/get",
)
#: 主入口（保持与接口文档一致）
KLINE_URL = KLINE_BASES[0]

#: 排行榜（股票列表）：成交额降序 + offset 分页
RANK_URL = "https://proxy.finance.qq.com/cgi/cgi-bin/rank/hs/getBoardRankList"
#: 全量覆盖尝试的板块并集：
#:   aStock → 4602 只，仅含沪深**主板**与**创业板**（实测 data.total=4602）
#:   ksh    → 617 只**科创板**、cyb → 1407 只创业板（**间歇可用**，见下方说明）
#:
#: 实测注意（2026-09）：``board_code`` 取 ``ksh``/``cyb`` 时，
#: **首个请求可用，随后该接口对本机会持续返回空数组**（疑似按 IP 的会话级限流），
#: 恢复时间不确定。因此这里把它们作为「尽力而为」的补充来源：
#: 拿到就合并，拿不到也不影响 aStock 的结果，更不会报错。
#: 科创板的**可靠**来源仍是新浪 ``sh_a`` 节点（见 SinaSource.list_stocks_all），
#: 由 ResilientProvider 做板块完整性校验后择优采用。
#: 北交所在腾讯侧无任何可用板块号，只能由新浪 ``hs_a`` 提供。
RANK_BOARDS: tuple[str, ...] = ("aStock", "ksh", "cyb")
#: 兼容既有引用
RANK_BOARD = "aStock"
#: 单页条数：实测 count=200 也可用，但 count=500 会返回空数组，故保守取 100
RANK_PAGE_SIZE = 100
#: 排行榜最多请求页数（安全上限：40×100=4000 只）
RANK_MAX_PAGES = 40
#: 全量列表模式的最大页数。``data.total`` 实测约 4602，
#: 按每页 100 只需 47 页；留到 60 页作为安全上限。
TEN_MAX_ALL_PAGES = 60
#: 拿不到配置时的默认列表长度
DEFAULT_LIST_LIMIT = 1000

#: 单次实时批量请求最多携带的代码数（URL 不宜过长，也不宜触发限流）
BATCH_SIZE = 60

#: 单次日线请求最多返回的根数
MAX_KLINE_BARS = 800

#: 指数：symbol、内部代码、兜底名称（名称优先取实时接口字段 1）
INDEX_LIST: tuple[tuple[str, str, str], ...] = (
    ("sh000001", "000001", "上证指数"),
    ("sz399001", "399001", "深证成指"),
    ("sz399006", "399006", "创业板指"),
)

# --------------------------------------------------- qt 批量行情字段索引（实测）
F_MARKET = 0  # 市场标识：1=上海，51=深圳
F_NAME = 1  # 股票名称（可能带空格，需 strip）
F_CODE = 2  # 6 位代码
F_LAST = 3  # 最新价
F_PRE_CLOSE = 4  # 昨收
F_OPEN = 5  # 今开
F_VOLUME = 6  # 成交量（手）
F_TIME = 30  # 时间戳 YYYYMMDDHHMMSS
F_CHANGE = 31  # 涨跌额
F_PCT = 32  # 涨跌幅（百分数）
F_HIGH = 33  # 最高
F_LOW = 34  # 最低
F_VOLUME2 = 36  # 成交量（手，与 6 相同）
F_AMOUNT = 37  # 成交额（万元）
F_TURNOVER = 38  # 换手率（%）
F_AMPLITUDE = 43  # 振幅（%）
F_FLOAT_CAP = 44  # 流通市值（亿元）
F_TOTAL_CAP = 45  # 总市值（亿元）
F_PB = 46  # 市净率
F_LIMIT_UP = 47  # 涨停价
F_LIMIT_DOWN = 48  # 跌停价

#: 解析所需的最小字段数（至少要能取到跌停价 48）
QT_MIN_FIELDS = 49

#: 实时快照对外返回的键（与 EastMoneyProvider.get_realtime 保持同一集合）
REALTIME_FIELDS: tuple[str, ...] = (
    "code",
    "name",
    "lastClose",
    "preClose",
    "pctChg",
    "turnover",
    "amount",
    "volume",
    "date",
)

#: 形如 ``v_sh600519`` 的行头
_LINE_HEAD_RE = re.compile(r"^[a-zA-Z]{2}\d{6}$")
_DATE_RE = re.compile(r"^\d{8}$")


# --------------------------------------------------------------------- 工具
def _to_float(value: Any) -> float | None:
    """宽松转 float：缺失（空串/``-``/``--``）或非有限值统一返回 None。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"-", "--", "null", "None", "nan"}:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clamp_days(days: Any, upper: int) -> int:
    """把请求天数限制在 [1, upper]。"""
    try:
        value = int(days)
    except (TypeError, ValueError):
        value = 250
    return max(1, min(value, upper))


def _normalize_codes(codes: Iterable[str] | None) -> list[str]:
    """批量代码归一化：转 6 位、去重、保序、丢弃非法值。"""
    out: list[str] = []
    seen: set[str] = set()
    for raw in codes or []:
        code = normalize_code(raw)
        if len(code) == 6 and code.isdigit() and code not in seen:
            seen.add(code)
            out.append(code)
    return out


def _default_list_limit() -> int:
    """``list_stocks()`` 的默认长度：优先用配置里的 ``universe_size``，取不到则用 1000。"""
    try:
        from ..config import get_settings  # 局部导入，避免适配器被配置强耦合

        value = int(get_settings().universe_size)
        return value if value > 0 else DEFAULT_LIST_LIMIT
    except Exception:  # noqa: BLE001 - 配置不可用时退回默认值，不影响抓取
        return DEFAULT_LIST_LIMIT


# --------------------------------------------------------------------- 解析
def _parse_qt_line(line: str) -> dict[str, Any] | None:
    """解析腾讯实时接口的一行（``v_sh600519="1~贵州茅台~..."``）。

    任何一个关键字段缺失/类型非法都返回 ``None``（由调用方跳过该只），
    **绝不抛 ValueError / IndexError**。
    """
    text = str(line or "").strip().strip(";").strip()
    if not text or "=" not in text:
        return None
    head, _, body = text.partition("=")
    body = body.strip()
    if body.startswith('"'):
        body = body[1:]
    if body.endswith('"'):
        body = body[:-1]
    if not body:
        # 无效代码时腾讯返回 v_pv_none_match="1" 之类的占位内容
        return None

    symbol = ""
    head_key = head.strip().lower()
    if head_key.startswith("v_"):
        head_key = head_key[2:]
    if _LINE_HEAD_RE.match(head_key):
        symbol = head_key

    parts = body.split("~")
    if len(parts) < QT_MIN_FIELDS:
        # 字段不足（停牌/退市/占位行）直接跳过
        return None

    code = normalize_code(parts[F_CODE]) or normalize_code(symbol)
    if len(code) != 6 or not code.isdigit():
        return None

    last = _to_float(parts[F_LAST])
    if last is None:
        return None
    pre_close = _to_float(parts[F_PRE_CLOSE])
    if pre_close is None or pre_close <= 0:
        pre_close = last
    pct = _to_float(parts[F_PCT])
    if pct is None:
        # 字段缺失时按 (最新 - 昨收) / 昨收 自行计算
        pct = (last - pre_close) / pre_close if pre_close > 0 else 0.0
    else:
        pct = pct / 100.0  # 接口是百分数，内部约定是小数

    raw_time = str(parts[F_TIME]).strip()
    date = ""
    if _DATE_RE.match(raw_time[:8]):
        date = f"{raw_time[0:4]}-{raw_time[4:6]}-{raw_time[6:8]}"

    amount_wan = _to_float(parts[F_AMOUNT])
    volume = _to_float(parts[F_VOLUME])
    if volume is None:
        volume = _to_float(parts[F_VOLUME2])

    return {
        "code": code,
        "symbol": symbol,
        "market": str(parts[F_MARKET]).strip(),
        "name": str(parts[F_NAME]).strip(),
        "lastClose": last,
        "preClose": pre_close,
        "open": _to_float(parts[F_OPEN]),
        "high": _to_float(parts[F_HIGH]),
        "low": _to_float(parts[F_LOW]),
        "pctChg": pct,
        "change": _to_float(parts[F_CHANGE]),
        "volume": volume if volume is not None else 0.0,
        "amount": (amount_wan * 10000.0) if amount_wan is not None else 0.0,  # 万元 -> 元
        "turnover": _to_float(parts[F_TURNOVER]) or 0.0,
        "amplitude": _to_float(parts[F_AMPLITUDE]),
        "floatCap": _to_float(parts[F_FLOAT_CAP]),
        "totalCap": _to_float(parts[F_TOTAL_CAP]),
        "pb": _to_float(parts[F_PB]),
        "limitUp": _to_float(parts[F_LIMIT_UP]),
        "limitDown": _to_float(parts[F_LIMIT_DOWN]),
        "timestamp": raw_time,
        "date": date,
    }


def _parse_qt_records(text: str) -> list[dict[str, Any]]:
    """把整段响应文本解析成记录列表（坏行跳过）。"""
    records: list[dict[str, Any]] = []
    if not text:
        return records
    for chunk in text.replace("\r", "").replace("\n", ";").split(";"):
        record = _parse_qt_line(chunk)
        if record is not None:
            records.append(record)
    return records


def _parse_qt_payload(text: str) -> dict[str, dict[str, Any]]:
    """把整段响应文本解析成 ``6 位代码 -> 记录`` 字典（坏行跳过）。"""
    return {record["code"]: record for record in _parse_qt_records(text)}


def _parse_kline_bars(bars: Any) -> list[dict[str, Any]]:
    """把腾讯日线 bar 数组转成 ``frame_from_records`` 需要的记录。

    数组顺序为 ``[日期, 开, 收, 高, 低, 成交量(手)]``（**开收高低**，非 OHLC）。
    长度不足或非法的 bar 直接跳过；多出的第 7 个元素忽略。
    """
    records: list[dict[str, Any]] = []
    if not isinstance(bars, (list, tuple)):
        return records
    for bar in bars:
        if not isinstance(bar, (list, tuple)) or len(bar) < 3:
            continue
        date = str(bar[0]).strip()
        close = _to_float(bar[2])
        open_ = _to_float(bar[1])
        if not date or close is None:
            continue
        if open_ is None:
            open_ = close
        high = _to_float(bar[3]) if len(bar) > 3 else None
        low = _to_float(bar[4]) if len(bar) > 4 else None
        volume = _to_float(bar[5]) if len(bar) > 5 else None
        records.append(
            {
                "date": date,
                "open": open_,
                "close": close,
                # 高/低缺失时退化，保证 high >= max(open, close) >= min(open, close) >= low
                "high": high if high is not None else max(open_, close),
                "low": low if low is not None else min(open_, close),
                "volume": volume if volume is not None else 0.0,
            }
        )
    return records


def _parse_kline_payload(payload: Any, symbol: str) -> list[dict[str, Any]]:
    """从日线 JSON 中提取 bar 记录：优先 ``qfqday``（前复权），回退 ``day``。"""
    if not isinstance(payload, dict):
        raise MarketSourceError("腾讯日线接口返回格式异常（非 JSON 对象）")
    code = payload.get("code")
    if code not in (0, "0", None):
        raise MarketSourceError(f"腾讯日线接口返回错误码 {code}（{symbol}）")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise MarketSourceError(f"腾讯日线接口缺少 data 字段（{symbol}）")
    block = data.get(symbol)
    if not isinstance(block, dict):
        # 也兼容大小写/前缀差异（例如返回键写成 SH600519）
        for key, value in data.items():
            if str(key).lower() == str(symbol).lower() and isinstance(value, dict):
                block = value
                break
    if not isinstance(block, dict):
        raise MarketSourceError(f"腾讯日线接口未返回 {symbol} 的数据")

    for key in ("qfqday", "day"):
        bars = block.get(key)
        if isinstance(bars, (list, tuple)) and bars:
            records = _parse_kline_bars(bars)
            if records:
                return records
    raise MarketSourceError(f"腾讯日线接口 {symbol} 缺少 qfqday/day 数据")


def _parse_rank_payload(payload: Any) -> list[dict[str, Any]]:
    """从排行榜 JSON 中提取 ``data.rank_list``（格式非法抛 MarketSourceError）。"""
    if not isinstance(payload, dict):
        raise MarketSourceError("腾讯排行榜返回格式异常（非 JSON 对象）")
    code = payload.get("code")
    if code not in (0, "0", None):
        raise MarketSourceError(f"腾讯排行榜返回错误码 {code}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise MarketSourceError("腾讯排行榜缺少 data 字段")
    rows = data.get("rank_list")
    if not isinstance(rows, list):
        raise MarketSourceError("腾讯排行榜缺少 rank_list 字段")
    return [row for row in rows if isinstance(row, dict)]


def _rank_row_to_meta(row: Any) -> tuple[float, StockMeta] | None:
    """排行榜单行 -> (成交额, StockMeta)；非目标/非法行返回 None。

    实测 ``rank_list`` 单行形如::

        {"code":"sz300308","name":"中际旭创","turnover":"2997407","zdf":"4.03",
         "zxj":"926.00","hsl":"2.95","zsz":"10907.44","ltsz":"10278.02",
         "state":"","stock_type":"GP-A-CYB", ...}

    - ``code`` 带 sh/sz 前缀，用 ``normalize_code`` 转 6 位；
    - ``turnover`` 单位是**万元**（仅用于排序，不进入 StockMeta）；
    - ``state`` 实测恒为空串，语义不明，**不做猜测性过滤**；
    - ``stock_type`` 实测取值为 ``GP-A`` / ``GP-A-CYB`` / ``GP-A-KCB``（都是股票），
      只做「非空且不以 GP 开头就跳过」的保守校验；
    - 名称含「退」的退市整理股直接剔除。
    """
    if not isinstance(row, dict):
        return None
    raw_code = str(row.get("code") or "").strip()
    # 严格校验原始代码：去掉 sh/sz 前缀后必须是恰好 6 位数字
    # （不能用 normalize_code 的结果判断，它会把 5 位数字补零成 6 位）
    if len("".join(ch for ch in raw_code if ch.isdigit())) != 6:
        return None
    code = normalize_code(raw_code)
    name = str(row.get("name") or "").strip()
    if len(code) != 6 or not code.isdigit() or not name:
        return None
    if "退" in name:
        return None
    stock_type = str(row.get("stock_type") or "").strip().upper()
    if stock_type and not stock_type.startswith("GP"):
        return None
    turnover = _to_float(row.get("turnover"))
    return (turnover if turnover is not None else 0.0), make_meta(code, name)


def _clamp_limit(limit: Any) -> int:
    """把列表长度限制在 [0, RANK_PAGE_SIZE * RANK_MAX_PAGES]。"""
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return 0
    return max(0, min(value, RANK_PAGE_SIZE * RANK_MAX_PAGES))


# --------------------------------------------------------------------- 数据源
class TencentSource(MarketSource):
    """腾讯财经数据源（实时批量 + 前复权日线 + 指数快照 + 成交额排行榜列表）。"""

    name = "tencent"
    #: 排行榜接口（getBoardRankList）可以提供全市场列表，故为 True
    supports_stock_list = True
    supports_realtime = True

    def __init__(self, timeout: float = 10.0, concurrency: int = 12) -> None:
        super().__init__(timeout=timeout, concurrency=concurrency)
        #: 最近一次成功的日线入口（下次优先使用，避免每次都被反爬入口拖慢）
        self._kline_base = KLINE_URL
        #: ``list_stocks()`` 默认取多少只（来自配置 universe_size，可按需覆写）
        self.universe_limit = _default_list_limit()

    # ------------------------------------------------------------------ 探测
    async def probe(self) -> None:
        """取 1 只股票实时行情做最轻量探测，失败抛 MarketSourceError。"""
        text = await self.get_text(f"{QT_URL}{prefixed('600519')}", encoding="gb18030")
        if not _parse_qt_payload(text):
            raise MarketSourceError("tencent 探测失败：实时接口未返回有效行情")

    # ------------------------------------------------------------------ 列表
    async def list_stocks_ranked(self, limit: int) -> list[StockMeta]:
        """按**成交额降序**返回前 ``limit`` 只 A 股（服务端排序 + 分页，无需翻全市场）。

        接口：``GET {RANK_URL}?board_code=aStock&sort_type=turnover&direct=down&offset&count``
        （实测 ``sort_type=turnover&direct=down`` 确实是成交额降序，
        榜单前列就是中际旭创、新易盛、紫金矿业这类活跃股）。

        实现要点：
        1. ``count`` 单次最多 100（实测 500 会返回空数组），用 ``offset`` 递增分页，
           每页只请求「还差多少只」，最多 ``RANK_MAX_PAGES`` 页；
        2. 代码/名称交给 ``make_meta`` 推导 market/board/limitPct，industry 为「未分类」；
        3. 剔除名称含「退」的退市整理股，以及 6 位代码校验不过、``stock_type`` 明显非股票的行；
        4. 某页失败时已拿到的部分照常返回，一只都没拿到才抛 ``MarketSourceError``。
        """
        wanted = _clamp_limit(limit)
        if wanted <= 0:
            return []
        collected: dict[str, tuple[float, StockMeta]] = {}
        offset = 0
        for _ in range(RANK_MAX_PAGES):
            if len(collected) >= wanted:
                break
            count = min(RANK_PAGE_SIZE, wanted - len(collected))
            try:
                payload = await self.get_json(
                    RANK_URL,
                    params={
                        "board_code": RANK_BOARD,
                        "sort_type": "turnover",
                        "direct": "down",
                        "offset": int(offset),
                        "count": int(count),
                    },
                )
                rows = _parse_rank_payload(payload)
            except MarketSourceError as exc:
                logger.warning("tencent 成交额榜 offset=%d 获取失败：%s", offset, exc)
                break  # 用已拿到的部分返回
            if not rows:
                break
            for row in rows:
                parsed = _rank_row_to_meta(row)
                if parsed is None:
                    continue
                amount, meta = parsed
                previous = collected.get(meta.code)
                if previous is None or amount > previous[0]:
                    collected[meta.code] = (amount, meta)
            offset += len(rows)
            if len(rows) < count:
                break  # 已经翻到榜尾
        if not collected:
            raise MarketSourceError("tencent 成交额榜未返回任何股票")
        # 服务端已是成交额降序，这里按成交额再排一次保证顺序稳定，并截断到 limit
        ordered = sorted(collected.values(), key=lambda item: item[0], reverse=True)
        logger.info("tencent 股票列表加载完成：请求 %d 只、实得 %d 只", wanted, len(ordered))
        return [meta for _, meta in ordered[:wanted]]

    async def list_stocks_all(self) -> list[StockMeta]:
        """拉取腾讯排行榜能覆盖的全部 A 股（分页取完，而非只取前 N 只）。

        **重要局限（实测）**：``board_code=aStock`` 的 ``total`` 约为 4602，
        只包含沪深主板（``GP-A``）与创业板（``GP-A-CYB``），
        **实测覆盖范围**（2026-09 验证）：``aStock`` 只有沪深主板与创业板，
        因此这里额外并上 ``ksh``（**科创板** 617 只）与 ``cyb``（创业板 1407 只），
        否则整个科创板会被漏掉。北交所在腾讯侧无可用板块号，
        仍需新浪的 ``hs_a`` 节点补齐，``ResilientProvider`` 会做完整性校验。
        """
        collected: dict[str, StockMeta] = {}
        per_board: dict[str, int] = {}
        for board_code in RANK_BOARDS:
            before = len(collected)
            offset = 0
            page = 0
            while page < TEN_MAX_ALL_PAGES:
                rows = await self._fetch_rank_page(offset, board_code=board_code)
                if not rows:
                    break
                for row in rows:
                    parsed = _rank_row_to_meta(row)
                    if parsed is None:
                        continue
                    _, meta = parsed
                    collected.setdefault(meta.code, meta)
                if len(rows) < RANK_PAGE_SIZE:
                    break
                offset += len(rows)
                page += 1
            per_board[board_code] = len(collected) - before
            logger.info("tencent %s 板块新增 %d 只（累计 %d）", board_code, per_board[board_code], len(collected))
        if not collected:
            raise MarketSourceError("tencent 未返回任何股票列表")
        boards: dict[str, int] = {}
        for meta in collected.values():
            boards[meta.board] = boards.get(meta.board, 0) + 1
        logger.info("tencent 列表加载完成：%d 只，板块分布 %s（北交所需由新浪补齐）", len(collected), boards)
        return list(collected.values())

    async def _fetch_rank_page(
        self, offset: int, count: int = RANK_PAGE_SIZE, board_code: str = RANK_BOARD
    ) -> list[dict[str, Any]]:
        """取某个板块排行榜的一页（``count`` 实测不得超过 100，传 500 会静默返回空数组）。"""
        data = await self.get_json(
            RANK_URL,
            params={
                "board_code": board_code,
                "sort_type": "turnover",
                "direct": "down",
                "offset": int(offset),
                "count": int(min(count, RANK_PAGE_SIZE)),
            },
        )
        return ((data or {}).get("data") or {}).get("rank_list") or []

    async def list_stocks(self) -> list[StockMeta]:
        """全市场列表（等价于 :meth:`list_stocks_all`）。"""
        return await self.list_stocks_all()

    # ------------------------------------------------------------------ 实时
    async def realtime(self, codes: Sequence[str]) -> dict[str, dict[str, Any]]:
        """批量实时行情：按 60 只一批切分，批间用 ``self.concurrency`` 限流。"""
        wanted = _normalize_codes(codes)
        if not wanted:
            return {}
        wanted_set = set(wanted)
        batches = list(chunked(wanted, BATCH_SIZE))
        sem = asyncio.Semaphore(self.concurrency)
        out: dict[str, dict[str, Any]] = {}
        errors: list[str] = []

        async def one(batch: list[str]) -> None:
            async with sem:
                url = f"{QT_URL}{','.join(prefixed(code) for code in batch)}"
                try:
                    text = await self.get_text(url, encoding="gb18030")
                except MarketSourceError as exc:
                    errors.append(str(exc))
                    logger.warning("tencent 批量实时行情失败（%d 只）：%s", len(batch), exc)
                    return
                for code, record in _parse_qt_payload(text).items():
                    if code in wanted_set:
                        out[code] = {key: record[key] for key in REALTIME_FIELDS}

        await asyncio.gather(*(one(batch) for batch in batches))
        if not out and errors:
            raise MarketSourceError(f"tencent 实时行情全部失败：{errors[0]}")
        if not out:
            raise MarketSourceError("tencent 实时行情未返回任何有效数据")
        return out

    # ------------------------------------------------------------------ 日线
    async def _kline_frame(self, symbol: str, days: int) -> pd.DataFrame:
        """按带前缀的 symbol 取日线（指数与股票共用）。

        依次尝试 ``KLINE_BASES``：上次成功的入口优先，失败（含反爬 501 挑战页）换下一个。
        """
        param = f"{symbol},day,,,{_clamp_days(days, MAX_KLINE_BARS)},qfq"
        order = [self._kline_base] + [base for base in KLINE_BASES if base != self._kline_base]
        errors: list[str] = []
        for base in order:
            try:
                payload = await self.get_json(base, params={"param": param})
                records = _parse_kline_payload(payload, symbol)
            except MarketSourceError as exc:
                errors.append(f"{base.split('//')[-1]}：{exc}")
                continue
            frame = frame_from_records(records)
            if frame.empty:
                errors.append(f"{base.split('//')[-1]}：解析后无有效数据")
                continue
            self._kline_base = base  # 记住可用入口
            return frame
        raise MarketSourceError(f"腾讯日线全部入口失败（{symbol}）：{'；'.join(errors)}")

    async def daily_kline(self, code: str, days: int = 250) -> pd.DataFrame:
        """单只日线（前复权）。"""
        normalized = normalize_code(code)
        if not normalized:
            raise MarketSourceError(f"腾讯日线：非法的股票代码 {code!r}")
        return await self._kline_frame(prefixed(normalized), days)

    async def daily_kline_many(self, codes: Sequence[str], days: int = 250) -> dict[str, pd.DataFrame]:
        """并发取多只日线，用 ``self.concurrency`` 限流；单只失败只跳过该只。"""
        wanted = _normalize_codes(codes)
        sem = asyncio.Semaphore(self.concurrency)
        out: dict[str, pd.DataFrame] = {}

        async def one(code: str) -> None:
            async with sem:
                try:
                    out[code] = await self.daily_kline(code, days)
                except Exception as exc:  # noqa: BLE001 - 单只失败不影响整体
                    logger.debug("tencent 获取 %s 日线失败：%s", code, exc)

        await asyncio.gather(*(one(code) for code in wanted))
        return out

    # ------------------------------------------------------------------ 指数
    async def index_snapshot(self) -> list[dict[str, Any]]:
        """上证指数 / 深证成指 / 创业板指快照，sparkline 取最近 20 个收盘价。"""
        url = f"{QT_URL}{','.join(symbol for symbol, _, _ in INDEX_LIST)}"
        try:
            text = await self.get_text(url, encoding="gb18030")
        except MarketSourceError as exc:
            logger.warning("tencent 指数快照失败：%s", exc)
            return []
        # 指数代码（000001/399001/399006）在本次请求内唯一，可按 symbol 建索引
        quotes = {record["symbol"]: record for record in _parse_qt_records(text)}
        sem = asyncio.Semaphore(self.concurrency)

        async def one(symbol: str, code: str, fallback_name: str) -> dict[str, Any]:
            async with sem:
                quote = quotes.get(symbol) or {}
                sparkline: list[float] = []
                try:
                    frame = await self._kline_frame(symbol, 20)
                    sparkline = [round(float(x), 2) for x in frame["close"].tail(20).tolist()]
                except MarketSourceError as exc:
                    logger.warning("tencent 指数 %s 日线失败：%s", symbol, exc)
                return {
                    "code": code,
                    # 名称优先取实时接口字段 1
                    "name": str(quote.get("name") or "").strip() or fallback_name,
                    "close": float(quote.get("lastClose") or 0.0),
                    "pctChg": float(quote.get("pctChg") or 0.0),
                    "sparkline": sparkline,
                }

        result = await asyncio.gather(*(one(s, c, n) for s, c, n in INDEX_LIST))
        return [item for item in result if item]
