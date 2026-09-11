"""真实行情数据源的公共基础设施。

包含三部分：
1. **股票代码规范化**：把内部 6 位代码转换为各数据源要求的带前缀格式；
2. **数据源健康度追踪**：统计每个源的连续失败次数并做指数退避，
   让「故障源」短期内不再被优先尝试，同时保留自动恢复能力；
3. **规范数据源接口** ``MarketSource``：所有真实源适配器统一实现，
   由 ``ResilientProvider`` 按操作做故障转移。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import httpx

from ..models import StockMeta

logger = logging.getLogger(__name__)

# 统一的浏览器 UA：多个国内行情接口对非浏览器 UA 会直接断连
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


# --------------------------------------------------------------------- 代码
def normalize_code(raw: str) -> str:
    """把各种写法的股票代码统一为内部 6 位数字形式。

    支持：``600519`` / ``sh600519`` / ``SH600519`` / ``600519.SH`` / ``600519.SS``。
    """
    text = str(raw or "").strip().upper()
    if not text:
        return ""
    # 600519.SH / 600519.SS 形式
    if "." in text:
        text = text.split(".", 1)[0]
    # sh600519 形式
    for prefix in ("SH", "SZ", "BJ"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits.zfill(6) if digits else ""


def market_of_code(code: str) -> str:
    """按代码段推断交易所：SH / SZ / BJ。

    代码段规则（实测各板块确认）：
    - 北交所 ``920xxx``（新段，如 920000 安徽凤凰）以及存量 ``43x/83x/87x/88x``；
    - 上交所 ``60x``（主板）、``688``（科创板）、``9xx``（B 股）、``5xx``（基金）；
    - 深交所 ``000/001/002/003``（主板）、``300/301``（创业板）、``2xx``（B 股）、``1xx``（基金/债）。

    **注意**：``920`` 必须归北交所而非深交所 —— 否则北交所股票会被错标为深市，
    涨跌停幅度也会按深市主板算（北交所为 30%），直接影响涨停判定。
    """
    code = normalize_code(code)
    if code.startswith("920"):
        return "BJ"
    if code.startswith(("43", "83", "87", "88")):
        return "BJ"
    if code.startswith(("688", "689")):
        return "SH"
    if code.startswith(("60", "90", "50", "51", "52", "56", "58")):
        return "SH"
    if code.startswith(("000", "001", "002", "003")):
        return "SZ"
    if code.startswith(("300", "301", "302")):
        return "SZ"
    if code.startswith(("200", "159", "15", "16", "18")):
        return "SZ"
    # 兜底：6/9 开头归上海，其余归深圳
    return "SH" if code.startswith(("6", "9")) else "SZ"


def board_of_code(code: str) -> str:
    """按代码段推断板块（用于涨跌停幅度判定）。

    实测确认的代码段（腾讯 ``stock_type`` 标记）：
    - 主板：沪 ``600/601/603/605``；深 ``000/001/002/003``（标记 ``GP-A``）
    - 创业板：深 ``300/301/302``（标记 ``GP-A-CYB``）
    - 科创板：沪 ``688/689``（标记 ``GP-A-KCB``）
    - 北交所：``920``（新段）及存量 ``43x/83x/87x/88x``

    幅度对照：主板 10%、创业板/科创板 20%、北交所 30%、ST 5%。
    """
    code = normalize_code(code)
    if code.startswith(("920", "43", "83", "87", "88")):
        return "北交所"
    if code.startswith(("688", "689")):
        return "科创板"
    if code.startswith(("300", "301", "302")):
        return "创业板"
    return "主板"


def prefixed(code: str, style: str = "lower") -> str:
    """返回带交易所前缀的代码，如 ``sh600519`` / ``SH600519``。

    ``style`` 取 ``lower``（新浪/腾讯）、``upper``（东方财富 secid 用不到，保留备用）。
    """
    code = normalize_code(code)
    prefix = market_of_code(code)
    return f"{prefix.lower()}{code}" if style == "lower" else f"{prefix}{code}"


def eastmoney_secid(code: str) -> str:
    """东方财富 secid：``1.600519``（上海） / ``0.000001``（深圳）。"""
    code = normalize_code(code)
    return f"{1 if market_of_code(code) == 'SH' else 0}.{code}"


def limit_pct(board: str, is_st: bool = False) -> float:
    """按板块与 ST 状态给出涨停幅度。"""
    if is_st:
        return 0.05
    if board in {"创业板", "科创板"}:
        return 0.20
    if board == "北交所":
        return 0.30
    return 0.10


def make_meta(code: str, name: str, is_st: bool | None = None) -> StockMeta:
    """由代码与名称构造 StockMeta（板块、涨跌停幅度自动推导）。"""
    code = normalize_code(code)
    board = board_of_code(code)
    upper = (name or "").upper()
    if is_st is None:
        is_st = "ST" in upper
    return StockMeta(
        code=code,
        name=(name or "").strip() or f"股票{code}",
        market=market_of_code(code),  # type: ignore[arg-type]
        board=board,
        industry="未分类",
        isSt=bool(is_st),
        limitPct=limit_pct(board, bool(is_st)),
    )


def chunked(items: Sequence[str], size: int) -> Iterable[list[str]]:
    """把序列按固定长度切块（批量接口用）。"""
    size = max(1, int(size))
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


# --------------------------------------------------------------------- 健康度
#: 某数据源「从未成功过」时，连续失败多少次后判定为本环境不可用并长期跳过。
#: 取 1 的理由：本项目的真实源之间是**等价可替代**的（都能提供日线），
#: 因此一个从未成功过的源再试一次的收益极低，而每次都要付出一个连接超时的
#: 代价（实测约 1 秒，且出现在股票列表/日线/实时行情等所有请求路径上）。
#: 一旦它以任意方式成功过一次（``ever_succeeded`` 置位），就恢复为
#: 「按指数退避冷却重试」的正常源策略，不会永久剔除一个只是临时抽风的源。
NEVER_SUCCEEDED_THRESHOLD = 1


@dataclass
class SourceHealth:
    """单个数据源的健康度。

    失败采用指数退避：连续失败 n 次后，冷却 ``base * 2^(n-1)`` 秒（上限 ``max``）。
    冷却期内该源被跳过，但冷却结束后会重新参与，避免永久剔除一个只是临时抽风的源。
    """

    name: str
    consecutive_failures: int = 0
    total_success: int = 0
    total_failure: int = 0
    last_error: str | None = None
    last_failure_at: float = 0.0
    last_success_at: float = 0.0
    cooldown_base: float = 3.0
    cooldown_max: float = 120.0

    def __post_init__(self) -> None:
        # 是否曾经成功过。用于区分两种「不可用」：
        #   - 曾成功、现在连续失败 → 临时故障，退避后应重试（可能恢复）
        #   - 从未成功过           → 本环境根本不可达（如域名被网络阻断），
        #                            反复重试只会每次都白等一个超时
        self.ever_succeeded: bool = False

    @property
    def cooldown_seconds(self) -> float:
        if self.consecutive_failures <= 0:
            return 0.0
        return min(self.cooldown_max, self.cooldown_base * (2 ** (self.consecutive_failures - 1)))

    def available(self, now: float | None = None) -> bool:
        """当前是否可尝试。

        从未成功过且已连续失败达到 ``NEVER_SUCCEEDED_THRESHOLD`` 时，直接判定为
        不可用并长期跳过 —— 否则一个在本环境被网络阻断的源会每隔一段退避时间
        就重新参与，每次都让用户白等一个连接超时（实测约 1 秒，且出现在
        股票列表、日线、实时行情等每个请求路径上）。
        """
        if self.consecutive_failures <= 0:
            return True
        if not self.ever_succeeded and self.consecutive_failures >= NEVER_SUCCEEDED_THRESHOLD:
            return False
        now = time.time() if now is None else now
        return (now - self.last_failure_at) >= self.cooldown_seconds

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.total_success += 1
        self.last_success_at = time.time()
        self.last_error = None
        self.ever_succeeded = True

    def record_failure(self, error: str) -> None:
        self.consecutive_failures += 1
        self.total_failure += 1
        self.last_error = str(error)[:200]
        self.last_failure_at = time.time()

    def reset(self) -> None:
        """手动重置健康度（供「重新探测」使用），使被跳过的源重新参与。"""
        self.consecutive_failures = 0
        self.ever_succeeded = False
        self.last_error = None

    def snapshot(self) -> dict[str, Any]:
        total = self.total_success + self.total_failure
        return {
            "name": self.name,
            "consecutiveFailures": self.consecutive_failures,
            "totalSuccess": self.total_success,
            "totalFailure": self.total_failure,
            "successRate": round(self.total_success / total, 4) if total else None,
            "lastError": self.last_error,
            "cooldownSeconds": round(self.cooldown_seconds, 1),
            "available": self.available(),
            "everSucceeded": self.ever_succeeded,
            "skipped": (
                not self.ever_succeeded and self.consecutive_failures >= NEVER_SUCCEEDED_THRESHOLD
            ),
            "lastSuccessAt": (
                time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self.last_success_at))
                if self.last_success_at
                else None
            ),
        }


# --------------------------------------------------------------------- 接口
class MarketSourceError(RuntimeError):
    """真实数据源调用失败（由适配器抛出，交给 ResilientProvider 切换）。"""


class MarketSource:
    """真实行情数据源统一接口。

    所有方法失败时 **必须** 抛出 ``MarketSourceError``（或 ``asyncio.TimeoutError``），
    由 ``ResilientProvider`` 负责切换到下一个源。
    """

    #: 数据源标识（同时作为 API 响应中的 dataSource 取值）
    name: str = "unknown"

    #: 该源是否支持「全市场股票列表」；不支持时由 ResilientProvider 用其他源补齐
    supports_stock_list: bool = True

    #: 该源是否支持「实时批量行情」
    supports_realtime: bool = True

    #: 该源是否支持「主要指数快照」。
    #: 置 False 的源会被**排除**在指数取数之外，而不是参与后再抛错 ——
    #: 否则一个只是不提供指数、但日线完全正常的源（如同花顺）
    #: 会在每次指数请求时被记为失败并进入冷却，属于错误归因。
    supports_index: bool = True

    def __init__(self, timeout: float = 10.0, concurrency: int = 12) -> None:
        self.timeout = float(timeout)
        self.concurrency = int(concurrency)
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------ 连接
    async def client(self) -> httpx.AsyncClient:
        """惰性创建共享连接池。"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=min(6.0, self.timeout)),
                headers={
                    "User-Agent": DEFAULT_UA,
                    "Accept": "*/*",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                    "Connection": "keep-alive",
                },
                follow_redirects=True,
                limits=httpx.Limits(max_connections=self.concurrency * 2, max_keepalive_connections=self.concurrency),
                trust_env=False,  # 忽略环境代理，避免容器内误用代理导致接口不可达
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def get_json(self, url: str, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        """GET 并解析 JSON，失败统一抛出 MarketSourceError。"""
        client = await self.client()
        try:
            resp = await client.get(url, params=params, **kwargs)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            raise MarketSourceError(f"{self.name} HTTP {exc.response.status_code}") from exc
        except (httpx.HTTPError, ValueError, OSError) as exc:
            raise MarketSourceError(f"{self.name} 请求失败：{type(exc).__name__}: {exc}") from exc

    async def get_text(self, url: str, params: dict[str, Any] | None = None, encoding: str = "utf-8", **kwargs: Any) -> str:
        """GET 并解码文本（部分接口返回 GBK）。"""
        client = await self.client()
        try:
            resp = await client.get(url, params=params, **kwargs)
            resp.raise_for_status()
            return resp.content.decode(encoding, errors="ignore")
        except httpx.HTTPStatusError as exc:
            raise MarketSourceError(f"{self.name} HTTP {exc.response.status_code}") from exc
        except (httpx.HTTPError, OSError) as exc:
            raise MarketSourceError(f"{self.name} 请求失败：{type(exc).__name__}: {exc}") from exc

    async def ping(self) -> bool:
        """轻量探测该源是否可用（失败返回 False，不抛异常）。"""
        try:
            await self.probe()
            return True
        except Exception:  # noqa: BLE001 - 探测失败一律视为不可用
            return False

    async def probe(self) -> None:
        """具体探测逻辑，由子类实现；失败抛 MarketSourceError。"""
        raise NotImplementedError

    # ------------------------------------------------------------------ 能力
    async def list_stocks(self) -> list[StockMeta]:
        """返回全市场股票列表（不支持时抛 MarketSourceError）。"""
        raise MarketSourceError(f"{self.name} 不支持获取股票列表")

    async def daily_kline(self, code: str, days: int = 250) -> Any:
        """返回单只股票日线 DataFrame（列见 base.KLINE_COLUMNS）。"""
        raise MarketSourceError(f"{self.name} 不支持获取日线")

    async def daily_kline_many(self, codes: Sequence[str], days: int = 250) -> dict[str, Any]:
        """并发获取多只日线；单只失败不抛异常，仅在结果中缺失。

        默认实现基于 ``daily_kline`` + 信号量限流。
        """
        sem = asyncio.Semaphore(self.concurrency)
        out: dict[str, Any] = {}

        async def one(code: str) -> None:
            async with sem:
                try:
                    out[code] = await self.daily_kline(code, days)
                except Exception as exc:  # noqa: BLE001 - 单只失败不影响整体
                    logger.debug("%s 获取 %s 日线失败：%s", self.name, code, exc)

        await asyncio.gather(*(one(c) for c in codes))
        return out

    async def realtime(self, codes: Sequence[str]) -> dict[str, dict[str, Any]]:
        """返回 代码 -> 最新行情快照。"""
        raise MarketSourceError(f"{self.name} 不支持实时行情")

    async def index_snapshot(self) -> list[dict[str, Any]]:
        """返回主要指数快照。"""
        return []
