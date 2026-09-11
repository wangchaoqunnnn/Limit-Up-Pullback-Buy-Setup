"""新浪财经行情数据源适配器（hq.sinajs.cn / money.finance.sina.com.cn / vip.stock.finance.sina.com.cn）。

== 使用的三个公开接口 ==

1. **实时批量行情**：``GET https://hq.sinajs.cn/list=sh600519,sz000001``
   必须 **GB18030** 解码，且必须带 ``Referer: https://finance.sina.com.cn``（否则 403）。
   每只股票一行::

       var hq_str_sh600519="贵州茅台,1285.150,1285.130,1275.160,...,2026-09-11,15:34:59,00,...";

   以 ``,`` 分隔，实测 34 个字段；索引同样由本机实测确认（见 ``S_*`` 常量）。
   本接口**不提供换手率**，``turnover`` 统一填 0（不伪造）。
   涨跌幅需按 ``(最新 - 昨收) / 昨收`` 自行计算。

2. **日线**：``GET .../CN_MarketData.getKLineData?symbol=sh600519&scale=240&ma=no&datalen=250``
   （需 ``Referer: https://finance.sina.com.cn``），返回 JSON 数组::

       [{"day":"2026-09-07","open":"8.150","high":"8.200","low":"7.950",
         "close":"7.970","volume":"341549255"}, ...]

   - ``scale=240`` 表示日线；
   - ``volume`` 单位是**股**，需 ÷100 转为**手**（与腾讯/东方财富口径一致）；
   - **复权差异**：本接口是**不复权**数据，而腾讯接口用的是**前复权（qfq）**。
     两者在除权日附近会有跳空差异。战法计算依赖的是形态与量能，不复权数据可用，
     但若同一批次混用两个源，建议在故障转移层保持「同一次扫描只用同一个源」。
     另外 ``day`` 数组不含成交额/换手率，``frame_from_records`` 会按
     ``volume * 100 * close`` 估算成交额、换手率填 0。

3. **全市场股票列表**（沪深 A 股，含创业板/科创板）::

       GET .../Market_Center.getHQNodeData?page=1&num=100&sort=symbol&asc=1&node=sh_a&symbol=&_s_r_a=page

   （需 ``Referer: https://vip.stock.finance.sina.com.cn``）返回 JSON 数组，字段含
   ``symbol`` / ``code`` / ``name``（``\\uXXXX`` 转义，``resp.json()`` 会自动还原中文）/
   ``trade`` / ``changepercent`` / ``volume`` / ``amount`` / ``turnoverratio`` 等。
   ``node`` 取值：``sh_a``（上海A股）、``sz_a``（深圳A股）、``cyb``（创业板）、``kcb``（科创板）。
   名称与代码交给 ``make_meta`` 推导 market/board/limitPct；**接口不提供行业，industry 统一 "未分类"**。

   除了全量 ``list_stocks()``，本适配器还提供 ``list_stocks_ranked(limit)``：
   用 ``sort=amount&asc=0`` 直接取**成交额降序**的前 N 只（实测该接口接受并正确执行这两个参数），
   只需少量页就能拿到「活跃股」，既快又不容易触发反爬。

   实测补充（2026-09）：``sh_a`` 已包含科创板（与 ``kcb`` 重叠 616 只），
   ``sz_a`` 已包含创业板（与 ``cyb`` 重叠 1407 只）；按 node 全量翻页后
   四个 node 去重合计约 5200 只。本适配器仍按需求遍历四个 node，并按代码去重。
   新浪不提供北交所列表（``node=bj_a`` 返回空）。

   **反爬限制**：该列表接口对高频翻页很敏感，实测快速连续翻页（约 4~5 次/秒）
   数十页后会返回 **HTTP 456** 并被短暂封禁（此后连正常单页请求也会 456，实测持续约 10 分钟）。
   因此本适配器在 node 之间、页与页之间都加了延时，并对 456 做「等待 + 重试」，
   仍在失败时只放弃当前 node（有数据就返回，全空才报错）。
   由于 ``sh_a``/``sz_a`` 已分别覆盖科创板/创业板且排在最前，
   即使中途被封禁，已取到的部分也是完整的沪深 A 股列表。
"""

from __future__ import annotations

import asyncio
import logging
import math
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
HQ_URL = "https://hq.sinajs.cn/list="
KLINE_URL = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
LIST_URL = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"

#: 实时/日线接口要求的 Referer（缺失会 403）
REFERER = "https://finance.sina.com.cn"
#: 股票列表接口要求的 Referer
LIST_REFERER = "https://vip.stock.finance.sina.com.cn"

#: 单次实时批量请求最多携带的代码数
BATCH_SIZE = 60

#: 日线接口 datalen 上限（实测可支持千根级别）
MAX_KLINE_BARS = 1000

#: 股票列表分页
LIST_PAGE_SIZE = 100
#: 单个 node 的最大翻页数（安全上限，防接口异常导致死循环）
MAX_LIST_PAGES = 80
#: 两个 node 之间的极短延时，避免被限流（秒）
LIST_NODE_DELAY = 0.15
#: 同一 node 内两次翻页之间的延时（实测连续快速翻页会被反爬限流）
LIST_PAGE_DELAY = 0.45
#: 被反爬限流（HTTP 456）时的重试等待（秒，按次数线性递增）
BLOCKED_RETRY_DELAY = 3.0
#: 被限流后的最大重试次数。
#: 全市场列表（三个 node、约 90 页）一天只抓一次并落盘快照，
#: 因此这里愿意多等一会儿换取完整覆盖 —— 宁可慢一次，也不要漏掉科创板/北交所。
MAX_BLOCKED_RETRY = 3
#: 依次遍历的 node（全市场覆盖：沪主板+科创 / 深主板+创业 / 沪深京含北交所）
LIST_NODES: tuple[str, ...] = ("sh_a", "sz_a", "cyb", "kcb")
#: 「全部 A 股」所需的 node 并集（实测结论）：
#:   sh_a → 沪市主板 600/601/603/605 + 科创板 688
#:   sz_a → 深市主板 000/001/002/003 + 创业板 300/301
#:   hs_a → 沪深京综合，含北交所 920（但**不含科创板**）
#: 三者并集才能同时覆盖主板、创业板、科创板、北交所，缺任意一个都会漏板块。
ALL_MARKET_NODES: tuple[str, ...] = ("hs_a", "sh_a", "sz_a")
#: 按成交额排名时遍历的 node：实测 ``sh_a`` 已含科创板、``sz_a`` 已含创业板，
#: 所以「沪 + 深」两个 node 即可覆盖全市场，且交替翻页可避免结果全被沪市占据。
RANKED_NODES: tuple[str, ...] = ("sh_a", "sz_a")
#: 排名模式下单个 node 的最大翻页数（安全上限）
MAX_RANKED_PAGES = 60

#: 指数：symbol、内部代码、兜底名称
INDEX_LIST: tuple[tuple[str, str, str], ...] = (
    ("sh000001", "000001", "上证指数"),
    ("sz399001", "399001", "深证成指"),
    ("sz399006", "399006", "创业板指"),
)

# ------------------------------------------------------- 实时行情字段索引（实测）
S_NAME = 0  # 名称
S_OPEN = 1  # 今开
S_PRE_CLOSE = 2  # 昨收
S_LAST = 3  # 最新价
S_HIGH = 4  # 最高
S_LOW = 5  # 最低
S_VOLUME = 8  # 成交量（**股**，需 ÷100 转手）
S_AMOUNT = 9  # 成交额（元）
S_DATE = 30  # 日期 YYYY-MM-DD
S_TIME = 31  # 时间 HH:MM:SS

#: 解析所需的最小字段数（至少要能取到时间 31）
SINA_MIN_FIELDS = 32

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


def _shares_to_hands(value: Any) -> float:
    """新浪的成交量单位是「股」，内部统一用「手」（1 手 = 100 股）。"""
    shares = _to_float(value)
    return (shares / 100.0) if shares is not None else 0.0


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


# --------------------------------------------------------------------- 解析
def _parse_sina_line(line: str) -> dict[str, Any] | None:
    """解析新浪实时接口的一行（``var hq_str_sh600519="贵州茅台,...";``）。

    字段缺失/长度不足/价格非法一律返回 ``None``（调用方跳过该只），
    **绝不抛 ValueError / IndexError**。
    """
    text = str(line or "").strip()
    if not text or "=" not in text:
        return None
    head, _, body = text.partition("=")
    symbol = head.strip().rstrip(";").split("hq_str_")[-1].strip().lower()
    body = body.strip().rstrip(";").strip()
    if body.startswith('"'):
        body = body[1:]
    if body.endswith('"'):
        body = body[:-1]
    if not body:
        # 代码非法时新浪返回 var hq_str_xxx="";
        return None

    parts = body.split(",")
    if len(parts) < SINA_MIN_FIELDS:
        return None

    code = normalize_code(symbol)
    if len(code) != 6 or not code.isdigit():
        return None

    last = _to_float(parts[S_LAST])
    pre_close = _to_float(parts[S_PRE_CLOSE])
    if last is None or last <= 0:
        # 停牌/未开盘时最新价为 0，视为无效快照
        return None
    if pre_close is None or pre_close <= 0:
        pre_close = last

    name = str(parts[S_NAME]).strip()
    date = str(parts[S_DATE]).strip()
    return {
        "code": code,
        "symbol": symbol,
        "name": name,
        "lastClose": last,
        "preClose": pre_close,
        "open": _to_float(parts[S_OPEN]),
        "high": _to_float(parts[S_HIGH]),
        "low": _to_float(parts[S_LOW]),
        # 接口不提供涨跌幅，按 (最新 - 昨收) / 昨收 计算
        "pctChg": (last - pre_close) / pre_close if pre_close > 0 else 0.0,
        "volume": _shares_to_hands(parts[S_VOLUME]),  # 股 -> 手
        "amount": _to_float(parts[S_AMOUNT]) or 0.0,  # 元
        # 该接口不提供换手率，填 0（不伪造）
        "turnover": 0.0,
        "date": date,
        "time": str(parts[S_TIME]).strip(),
        "timestamp": f"{date} {str(parts[S_TIME]).strip()}".strip(),
    }


def _parse_sina_records(text: str) -> list[dict[str, Any]]:
    """把整段响应文本解析成记录列表（坏行跳过）。"""
    records: list[dict[str, Any]] = []
    if not text:
        return records
    for chunk in text.replace("\r", "").split("\n"):
        record = _parse_sina_line(chunk)
        if record is not None:
            records.append(record)
    return records


def _parse_sina_payload(text: str) -> dict[str, dict[str, Any]]:
    """把整段响应文本解析成 ``6 位代码 -> 记录`` 字典（坏行跳过）。"""
    return {record["code"]: record for record in _parse_sina_records(text)}


def _parse_sina_kline(payload: Any) -> list[dict[str, Any]]:
    """把新浪日线 JSON 数组转成 ``frame_from_records`` 需要的记录。

    ``volume`` 从「股」换算为「手」；坏行跳过；整体格式非法抛 ``MarketSourceError``。
    """
    if not isinstance(payload, list):
        raise MarketSourceError("sina 日线接口返回格式异常（非 JSON 数组）")
    records: list[dict[str, Any]] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        date = str(row.get("day") or "").strip()
        close = _to_float(row.get("close"))
        if not date or close is None:
            continue
        open_ = _to_float(row.get("open"))
        if open_ is None:
            open_ = close
        high = _to_float(row.get("high"))
        low = _to_float(row.get("low"))
        records.append(
            {
                "date": date,
                "open": open_,
                "close": close,
                "high": high if high is not None else max(open_, close),
                "low": low if low is not None else min(open_, close),
                "volume": _shares_to_hands(row.get("volume")),  # 股 -> 手
            }
        )
    if not records:
        raise MarketSourceError("sina 日线接口未返回有效数据")
    return records


def _meta_from_list_row(row: Any) -> StockMeta | None:
    """股票列表接口单行 -> StockMeta（代码/名称非法则返回 None）。"""
    if not isinstance(row, dict):
        return None
    code = normalize_code(row.get("code") or row.get("symbol") or "")
    name = str(row.get("name") or "").strip()
    if len(code) != 6 or not code.isdigit() or not name:
        return None
    # make_meta 自动推导 market/board/limitPct/isSt，industry 固定为「未分类」
    return make_meta(code, name)


def _amount_of_list_row(row: Any) -> float:
    """取列表接口单行的成交额（元）；缺失/非法返回 0.0（排序时排到最后）。"""
    if not isinstance(row, dict):
        return 0.0
    value = _to_float(row.get("amount"))
    return value if value is not None else 0.0


# --------------------------------------------------------------------- 数据源
class SinaSource(MarketSource):
    """新浪财经数据源（实时批量 + 日线 + 全市场股票列表）。"""

    name = "sina"
    supports_stock_list = True
    supports_realtime = True

    def __init__(self, timeout: float = 10.0, concurrency: int = 12) -> None:
        super().__init__(timeout=timeout, concurrency=concurrency)
        #: 两个 node 之间的延时（测试可置 0）
        self.node_delay = LIST_NODE_DELAY
        #: 同一 node 内两次翻页之间的延时（测试可置 0）
        self.page_delay = LIST_PAGE_DELAY
        #: 被反爬限流（HTTP 456）时的重试等待（测试可置 0）
        self.blocked_retry_delay = BLOCKED_RETRY_DELAY

    # ------------------------------------------------------------------ 探测
    async def probe(self) -> None:
        """取 1 只股票实时行情做最轻量探测，失败抛 MarketSourceError。"""
        text = await self.get_text(
            f"{HQ_URL}{prefixed('600519')}", encoding="gb18030", headers={"Referer": REFERER}
        )
        if not _parse_sina_payload(text):
            raise MarketSourceError("sina 探测失败：实时接口未返回有效行情")

    # ------------------------------------------------------------------ 列表
    async def _fetch_list_page(
        self,
        node: str,
        page: int,
        headers: dict[str, str],
        sort: str = "symbol",
        ascending: int = 1,
    ) -> Any:
        """取股票列表的一页；遇到反爬限流（HTTP 456）等待后重试。

        ``sort``/``ascending`` 直接透传给接口：``sort=symbol&asc=1`` 是代码升序，
        ``sort=amount&asc=0`` 是**成交额降序**（实测有效）。
        """
        params = {
            "page": page,
            "num": LIST_PAGE_SIZE,
            "sort": sort,
            "asc": ascending,
            "node": node,
            "symbol": "",
            "_s_r_a": "page",
        }
        last_error: MarketSourceError | None = None
        for attempt in range(MAX_BLOCKED_RETRY + 1):
            try:
                return await self.get_json(LIST_URL, params=params, headers=headers)
            except MarketSourceError as exc:
                last_error = exc
                if attempt < MAX_BLOCKED_RETRY and "456" in str(exc):
                    # 456 是新浪的反爬限流码（连续快速翻页会触发），等一会儿再试
                    delay = self.blocked_retry_delay * (attempt + 1)
                    logger.warning("sina 股票列表被限流（%s 第 %d 页），%.1fs 后重试", node, page, delay)
                    await asyncio.sleep(delay)
                    continue
                raise
        raise last_error  # pragma: no cover - 循环内必然 return 或 raise

    async def list_stocks_all(self) -> list[StockMeta]:
        """拉取**全部 A 股**列表，覆盖所有交易所与板块（不遗漏任何一类）。

        为什么需要三个 node 求并集（以下均为实测结论）：
        - ``sh_a``：沪市 —— ``600/601/603/605``（主板）+ ``688``（**科创板**）；
        - ``sz_a``：深市 —— ``000/001/002/003``（主板）+ ``300/301``（**创业板**）；
        - ``hs_a``：沪深京综合 —— 含 ``920``（**北交所**），但**不含科创板**；
        - 单独的 ``kcb`` 节点虽可翻页，但实测容易被反爬限流，故不依赖它。

        任取其一都会漏板块：只用 ``sh_a+sz_a`` 会**丢掉北交所**，
        只用 ``hs_a`` 会**丢掉科创板**。因此这里对三者求并集。

        容错：某个 node 被限流时，已拿到的并集照常返回（只记警告），
        只有三个 node 全部失败才抛 ``MarketSourceError``——保证「尽力覆盖」优先于「要么全有要么全无」。
        """
        union: dict[str, StockMeta] = {}
        headers = {"Referer": LIST_REFERER}
        failed: list[str] = []
        for index, node in enumerate(ALL_MARKET_NODES):
            if index:
                await asyncio.sleep(self.node_delay)
            before = len(union)
            try:
                await self._collect_node(node, headers, union)
            except MarketSourceError as exc:
                failed.append(f"{node}({exc})")
                logger.warning("sina %s 节点拉取失败（已保留其余节点结果）：%s", node, exc)
                continue
            logger.info("sina %s 节点新增 %d 只（累计 %d 只）", node, len(union) - before, len(union))

        if not union:
            raise MarketSourceError(f"sina 全市场列表获取失败：{'；'.join(failed) or '无数据'}")

        # 板块覆盖自检：缺板块必须显式告警，避免「静默遗漏」这类最危险的问题
        boards: dict[str, int] = {}
        for meta in union.values():
            boards[meta.board] = boards.get(meta.board, 0) + 1
        missing = [name for name in ("主板", "创业板", "科创板", "北交所") if not boards.get(name)]
        if missing:
            logger.warning(
                "sina 全市场列表缺少板块 %s（已获取 %d 只，分布 %s）——可能被上游限流，建议稍后重试",
                "/".join(missing),
                len(union),
                boards,
            )
        else:
            logger.info("sina 全市场列表覆盖完整：%d 只，分布 %s", len(union), boards)
        return list(union.values())

    async def _collect_node(self, node: str, headers: dict[str, str], union: dict[str, StockMeta]) -> None:
        """翻页拉取单个 node 并写入 ``union``（按代码去重，先到者为准）。"""
        page = 1
        while page <= MAX_LIST_PAGES:
            if page > 1:
                # 同一 node 内翻页留出间隔，避免触发反爬（456）
                await asyncio.sleep(self.page_delay)
            payload = await self._fetch_list_page(node, page, headers)
            rows = payload if isinstance(payload, list) else []
            if not rows:
                break
            for row in rows:
                meta = _meta_from_list_row(row)
                if meta is not None:
                    union.setdefault(meta.code, meta)
            if len(rows) < LIST_PAGE_SIZE:
                break
            page += 1

    async def list_stocks(self) -> list[StockMeta]:
        """拉取全市场 A 股列表（等价于 :meth:`list_stocks_all`）。

        终止条件：返回条数 < ``num``、返回空数组或超过 ``MAX_LIST_PAGES`` 页；
        单个 node/页面失败只记日志并跳过该 node，全部失败才抛 ``MarketSourceError``。
        """
        return await self.list_stocks_all()

    async def list_stocks_ranked(self, limit: int) -> list[StockMeta]:
        """按**成交额降序**返回前 ``limit`` 只股票（服务端排序，避免翻遍全市场）。

        为什么需要它：``list_stocks()`` 用 ``sort=symbol&asc=1`` 从代码最小的开始翻页，
        取前 N 只 = 代码最小的 N 只，与流动性/强势程度无关，且要翻很多页（慢、易被 456 限流）。
        战法要的是「近期涨停 + 放量」的活跃股，几乎都在成交额榜前列，所以这里改成
        ``sort=amount&asc=0``（实测接口确实按成交额降序返回），只需少量页即可凑够。

        实现要点：
        1. ``sh_a``/``sz_a`` **交替翻页**，各翻 ``ceil(limit / 100)`` 页 ——
           这样才能保证「全局成交额前 N」一定落在取回的并集内（只翻一个市场会漏掉另一个市场的活跃股）；
        2. 取回的并集在本地再按 ``amount`` 降序排一次并截断到 ``limit``，
           保证返回的确实是跨两市的前 N 只（服务端已排序，这里只是归并两个有序列表）；
        3. 某个 node 的某一页被限流（HTTP 456）时，已拿到的部分照常返回，全部拿不到才抛错；
        4. 返回的 ``StockMeta`` 用 ``make_meta`` 构造，industry 为「未分类」。
        """
        try:
            wanted = int(limit)
        except (TypeError, ValueError):
            wanted = 0
        if wanted <= 0:
            return []

        headers = {"Referer": LIST_REFERER}
        #: 代码 -> (成交额, StockMeta)，同一只以成交额较大的一条为准
        collected: dict[str, tuple[float, StockMeta]] = {}
        pages_per_node = min(MAX_RANKED_PAGES, -(-wanted // LIST_PAGE_SIZE))  # ceil
        exhausted: set[str] = set()  # 已翻完（返回不足一页）的 node
        failed: set[str] = set()  # 已失败（限流等）的 node

        async def fetch_page(node: str, page: int) -> None:
            """取一页并累积结果；失败只记日志，不中断整体。"""
            try:
                payload = await self._fetch_list_page(node, page, headers, sort="amount", ascending=0)
            except MarketSourceError as exc:
                failed.add(node)
                logger.warning("sina 成交额榜 %s 第 %d 页获取失败：%s", node, page, exc)
                return
            rows = payload if isinstance(payload, list) else []
            if len(rows) < LIST_PAGE_SIZE:
                exhausted.add(node)
            for row in rows:
                meta = _meta_from_list_row(row)
                if meta is None:
                    continue
                amount = _amount_of_list_row(row)
                previous = collected.get(meta.code)
                if previous is None or amount > previous[0]:
                    collected[meta.code] = (amount, meta)

        # 第一轮：两个市场交替翻页，各翻 ceil(limit / num) 页
        for page in range(1, pages_per_node + 1):
            for node in RANKED_NODES:
                if node in exhausted or node in failed:
                    continue
                if page > 1 or node != RANKED_NODES[0]:
                    await asyncio.sleep(self.page_delay)  # 翻页/换 node 之间留间隔，避免被限流
                await fetch_page(node, page)
        # 容错：条数还不够（有 node 提前翻完或被限流）时再多补一页
        if len(collected) < wanted:
            for node in RANKED_NODES:
                if node in exhausted or node in failed:
                    continue
                await asyncio.sleep(self.page_delay)
                await fetch_page(node, pages_per_node + 1)

        if not collected:
            raise MarketSourceError("sina 成交额榜未返回任何股票")
        # 服务端已按成交额降序，这里归并两个市场并按成交额取全局前 limit 只
        ordered = sorted(collected.values(), key=lambda item: item[0], reverse=True)
        logger.info("sina 成交额榜加载完成，请求 %d 只、实得 %d 只（本地归并截断）",
                    wanted, len(ordered))
        return [meta for _, meta in ordered[:wanted]]

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
                url = f"{HQ_URL}{','.join(prefixed(code) for code in batch)}"
                try:
                    text = await self.get_text(url, encoding="gb18030", headers={"Referer": REFERER})
                except MarketSourceError as exc:
                    errors.append(str(exc))
                    logger.warning("sina 批量实时行情失败（%d 只）：%s", len(batch), exc)
                    return
                for code, record in _parse_sina_payload(text).items():
                    if code in wanted_set:
                        out[code] = {key: record[key] for key in REALTIME_FIELDS}

        await asyncio.gather(*(one(batch) for batch in batches))
        if not out and errors:
            raise MarketSourceError(f"sina 实时行情全部失败：{errors[0]}")
        if not out:
            raise MarketSourceError("sina 实时行情未返回任何有效数据")
        return out

    # ------------------------------------------------------------------ 日线
    async def _kline_records(self, symbol: str, days: int) -> list[dict[str, Any]]:
        payload = await self.get_json(
            KLINE_URL,
            params={
                "symbol": symbol,
                "scale": 240,  # 240 分钟 = 日线
                "ma": "no",
                "datalen": _clamp_days(days, MAX_KLINE_BARS),
            },
            headers={"Referer": REFERER},
        )
        return _parse_sina_kline(payload)

    async def daily_kline(self, code: str, days: int = 250) -> pd.DataFrame:
        """单只日线（**不复权**，注意与腾讯前复权的差异）。"""
        normalized = normalize_code(code)
        if not normalized:
            raise MarketSourceError(f"sina 日线：非法的股票代码 {code!r}")
        records = await self._kline_records(prefixed(normalized), days)
        frame = frame_from_records(records)
        if frame.empty:
            raise MarketSourceError(f"sina 未返回 {normalized} 的有效日线数据")
        return frame

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
                    logger.debug("sina 获取 %s 日线失败：%s", code, exc)

        await asyncio.gather(*(one(code) for code in wanted))
        return out

    # ------------------------------------------------------------------ 指数
    async def index_snapshot(self) -> list[dict[str, Any]]:
        """上证指数 / 深证成指 / 创业板指快照，sparkline 取最近 20 个收盘价。"""
        url = f"{HQ_URL}{','.join(symbol for symbol, _, _ in INDEX_LIST)}"
        try:
            text = await self.get_text(url, encoding="gb18030", headers={"Referer": REFERER})
        except MarketSourceError as exc:
            logger.warning("sina 指数快照失败：%s", exc)
            return []
        quotes = {record["symbol"]: record for record in _parse_sina_records(text)}
        sem = asyncio.Semaphore(self.concurrency)

        async def one(symbol: str, code: str, fallback_name: str) -> dict[str, Any]:
            async with sem:
                quote = quotes.get(symbol) or {}
                sparkline: list[float] = []
                try:
                    frame = frame_from_records(await self._kline_records(symbol, 20))
                    if not frame.empty:
                        sparkline = [round(float(x), 2) for x in frame["close"].tail(20).tolist()]
                except MarketSourceError as exc:
                    logger.warning("sina 指数 %s 日线失败：%s", symbol, exc)
                return {
                    "code": code,
                    "name": str(quote.get("name") or "").strip() or fallback_name,
                    "close": float(quote.get("lastClose") or 0.0),
                    "pctChg": float(quote.get("pctChg") or 0.0),
                    "sparkline": sparkline,
                }

        result = await asyncio.gather(*(one(s, c, n) for s, c, n in INDEX_LIST))
        return [item for item in result if item]
