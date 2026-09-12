"""多源故障转移与增量缓存的集成测试（``ResilientProvider``）。

覆盖真实踩到的坑：
1. **数据量过少的静默失败**：腾讯日线对北交所（920xxx）只返回 1 根当日 K 线，
   既不报错也不为空。若按「非空即成功」处理，这些股票会带着 1 根数据进入策略引擎，
   所有均线/位置判据全部失真 —— 等于「进了股票池却无法分析」。
   必须判定为取数失败并切换到其他源。
2. **板块完整性校验**：腾讯排行榜缺科创板与北交所，
   若「先到先得」就会静默漏板块，必须择优采用覆盖最全的源。
3. **不完整列表不落盘快照**：否则一次限流导致的残缺列表会被缓存 24 小时。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pandas as pd
import pytest

from app.providers.base import KLINE_COLUMNS
from app.providers.resilient import MIN_BARS, ResilientProvider
from app.providers.source_base import MarketSourceError, make_meta


def frame(n: int, start: str = "2026-01-01") -> pd.DataFrame:
    """构造 n 根规范日线。"""
    dates = pd.date_range(start, periods=n, freq="D")
    rows = [
        {
            "date": d,
            "open": 10.0 + i * 0.1,
            "high": 10.5 + i * 0.1,
            "low": 9.5 + i * 0.1,
            "close": 10.2 + i * 0.1,
            "pre_close": 10.1 + i * 0.1,
            "pct_chg": 0.01,
            "volume": 1000.0,
            "amount": 1_000_000.0,
            "turnover": 1.0,
        }
        for i, d in enumerate(dates)
    ]
    return pd.DataFrame(rows)[KLINE_COLUMNS]


class StubSource:
    """可编程的假数据源（只实现故障转移需要的接口）。"""

    def __init__(self, name: str, *, bars: dict[str, int] | None = None, fail: bool = False,
                 stocks: list | None = None) -> None:
        self.name = name
        self.supports_stock_list = True
        self.supports_realtime = False
        self.supports_index = False
        self._bars = bars or {}
        self._fail = fail
        self._stocks = stocks or []
        self.kline_calls = 0

    async def probe(self) -> None:
        if self._fail:
            raise MarketSourceError(f"{self.name} 探测失败")

    async def ping(self) -> bool:
        return not self._fail

    async def close(self) -> None:
        return None

    async def list_stocks_all(self):
        if self._fail:
            raise MarketSourceError(f"{self.name} 列表失败")
        return list(self._stocks)

    async def list_stocks(self):
        return await self.list_stocks_all()

    async def daily_kline_many(self, codes, days=250):
        self.kline_calls += 1
        if self._fail:
            raise MarketSourceError(f"{self.name} 日线失败")
        out = {}
        for code in codes:
            n = self._bars.get(str(code), 0)
            if n > 0:
                out[str(code)] = frame(n)
        return out

    async def realtime(self, codes):
        raise MarketSourceError(f"{self.name} 无实时行情")

    async def index_snapshot(self):
        return []


@pytest.fixture()
def provider(tmp_path: Path) -> ResilientProvider:
    """构造一个注入假数据源的 provider（不依赖网络，也不碰真实数据目录）。"""
    p = ResilientProvider()
    p.cache.close()
    from app.providers.klines_cache import KlineCache

    p.cache = KlineCache(tmp_path / "klines.db")

    # 关键：把股票列表快照指向临时目录。
    # 否则会读到工作区里已存在的真实快照（5561 只、板块完整），
    # 导致「残缺列表」相关用例永远走不到真实逻辑，测试失去意义。
    class _TempSettings:
        def __init__(self, real, base: Path) -> None:
            self._real = real
            self._base = base

        def __getattr__(self, item):
            return getattr(self._real, item)

        @property
        def stock_list_file(self) -> Path:
            return self._base / "stock_list.json"

        @property
        def industry_map_file(self) -> Path:
            return self._base / "industry_map.json"

    p._settings = _TempSettings(p._settings, tmp_path)
    yield p
    p.cache.close()


def test_min_bars_constant_is_sane():
    """MIN_BARS 至少要能满足 MA20 的计算需求。"""
    assert MIN_BARS >= 20


@pytest.mark.asyncio
async def test_thin_data_triggers_failover(provider: ResilientProvider):
    """核心回归：A 源只给 1 根（数据量不足）时必须切换到 B 源。"""
    thin = StubSource("thin", bars={"920000": 1, "600519": 250})
    good = StubSource("good", bars={"920000": 250})
    provider.sources = [thin, good]
    provider.health = {s.name: provider.health.get(s.name) or __import__(
        "app.providers.source_base", fromlist=["SourceHealth"]
    ).SourceHealth(name=s.name) for s in provider.sources}

    frames = await provider.get_daily_kline_batch(["920000"], 250)

    assert len(frames["920000"]) == 250, "数据量不足应触发切换到完整源"
    assert thin.kline_calls >= 1 and good.kline_calls >= 1
    # 数据量不足的源应被记为失败
    assert provider.health["thin"].total_failure >= 1
    assert provider.health["good"].total_success >= 1


@pytest.mark.asyncio
async def test_thin_data_raises_when_no_source_has_history(provider: ResilientProvider):
    """所有源都只有 1 根时，不应把它当作可用数据写进缓存。"""
    a = StubSource("a", bars={"920000": 1})
    b = StubSource("b", bars={"920000": 3})
    provider.sources = [a, b]
    from app.providers.source_base import SourceHealth

    provider.health = {s.name: SourceHealth(name=s.name) for s in provider.sources}

    frames = await provider.get_daily_kline_batch(["920000"], 250)
    # 不应返回残缺数据（会被策略误用）
    assert "920000" not in frames or len(frames["920000"]) == 0
    # 所有源一致判「不足」= 股票自身历史太短，任何源都不应被记为故障
    assert provider.health["a"].total_failure == 0
    assert provider.health["b"].total_failure == 0

@pytest.mark.asyncio
async def test_healthy_source_is_not_penalized(provider: ResilientProvider):
    """数据充足的源应正常记录成功，不因 MIN_BARS 校验被误伤。"""
    good = StubSource("good", bars={"600519": 250, "688981": 250})
    provider.sources = [good]
    from app.providers.source_base import SourceHealth

    provider.health = {s.name: SourceHealth(name=s.name) for s in provider.sources}

    frames = await provider.get_daily_kline_batch(["600519", "688981"], 250)
    assert len(frames["600519"]) == 250
    assert len(frames["688981"]) == 250
    assert provider.health["good"].total_success >= 1
    assert provider.health["good"].total_failure == 0


@pytest.mark.asyncio
async def test_universe_prefers_source_with_all_four_boards(provider: ResilientProvider):
    """板块完整性：缺板块的源不应被优先采用，即使它排在前面。"""
    incomplete = StubSource(
        "incomplete",
        stocks=[make_meta("600519", "贵州茅台"), make_meta("300750", "宁德时代")],
    )
    complete = StubSource(
        "complete",
        stocks=[
            make_meta("600519", "贵州茅台"),
            make_meta("300750", "宁德时代"),
            make_meta("688981", "中芯国际"),
            make_meta("920000", "安徽凤凰"),
        ],
    )
    provider.sources = [incomplete, complete]
    from app.providers.source_base import SourceHealth

    provider.health = {s.name: SourceHealth(name=s.name) for s in provider.sources}

    metas = await provider.get_stock_list()
    boards = {m.board for m in metas}
    assert boards == {"主板", "创业板", "科创板", "北交所"}
    assert provider._universe_source == "complete"
    assert provider._universe_complete is True


@pytest.mark.asyncio
async def test_incomplete_universe_is_not_snapshotted(provider: ResilientProvider):
    """残缺股票池绝不能落盘快照，否则会被缓存 24 小时造成持续遗漏。"""
    only_main = StubSource("only_main", stocks=[make_meta("600519", "贵州茅台")])
    provider.sources = [only_main]
    from app.providers.source_base import SourceHealth

    provider.health = {s.name: SourceHealth(name=s.name) for s in provider.sources}

    await provider.get_stock_list()
    assert provider._universe_complete is False
    assert not provider._settings.stock_list_file.exists(), "残缺列表不应写入快照"


# --------------------------------------------------------------------- 归因
# 以下用例锁定一个**已实测复现的严重 Bug**：
# 次新股（上市不足 MIN_BARS 个交易日）的历史本来就短，任何数据源都给不出
# 足够根数。此前的实现把「数据量不足」一律记为**数据源失败**，
# 于是一次批量里只要有 1 只次新股，四个源就全被记为失败；
# 而「从未成功过」的源会被永久跳过（NEVER_SUCCEEDED_THRESHOLD=1），
# 因此新进程的第一次请求就可能把**全部数据源一起判死**，
# 之后连贵州茅台这样的正常股票都取不到行情 —— 真实数据整体静默不可用。
# 正确行为：数据量不足必须继续向后续源尝试，但只有在**后续源能补齐**时
# 才证明是该源自身的能力缺陷，此时才记失败。


@pytest.mark.asyncio
async def test_new_listing_thin_on_every_source_does_not_kill_health(provider: ResilientProvider):
    """次新股在所有源上都数据不足时，绝不能牵连任何数据源的健康度。"""
    from app.providers.source_base import SourceHealth

    # 920071（金钛股份）实测上市仅 7 个交易日：腾讯/同花顺/新浪一致给不出 20 根
    a = StubSource("a", bars={"920071": 7})
    b = StubSource("b", bars={"920071": 16})
    c = StubSource("c", bars={"920071": 18})
    provider.sources = [a, b, c]
    provider.health = {s.name: SourceHealth(name=s.name) for s in provider.sources}

    frames = await provider.get_daily_kline_batch(["920071"], 250)

    assert "920071" not in frames, "不足 MIN_BARS 的数据不得进入策略引擎"
    for name in ("a", "b", "c"):
        health = provider.health[name]
        assert health.total_failure == 0, f"{name} 不应因次新股被判故障"
        assert health.available() is True, f"{name} 不应被标记为不可用"
        assert health.snapshot()["skipped"] is False, f"{name} 不应被长期跳过"
    # 三个源都应被真正尝试过（数据不足仍要按顺序取，只是不归咎于源）
    assert a.kline_calls == 1 and b.kline_calls == 1 and c.kline_calls == 1


@pytest.mark.asyncio
async def test_thin_data_alone_must_not_disable_the_whole_chain(provider: ResilientProvider):
    """核心回归：一次次新股请求之后，正常股票仍必须能取到真实行情。"""
    from app.providers.source_base import SourceHealth

    # 源 a 只有次新股数据（会判不足），源 b 同时有次新股与正常股票
    a = StubSource("a", bars={"920071": 7})
    b = StubSource("b", bars={"920071": 7, "600519": 250})
    provider.sources = [a, b]
    provider.health = {s.name: SourceHealth(name=s.name) for s in provider.sources}

    # 第一步：只请求次新股（模拟股票池里恰好只有它需要补数据）
    await provider.get_daily_kline_batch(["920071"], 250)
    assert provider.health["a"].available() is True
    assert provider.health["b"].available() is True

    # 第二步：同一个 provider 继续请求正常股票，必须仍然拿得到数据
    frames = await provider.get_daily_kline_batch(["600519"], 250)
    assert "600519" in frames
    assert len(frames["600519"]) == 250


@pytest.mark.asyncio
async def test_thin_source_is_penalized_only_when_another_source_fixes_it(
    provider: ResilientProvider,
):
    """某源判不足、但后续源能补齐时，说明是该源自身缺陷，此时才记失败。"""
    from app.providers.source_base import SourceHealth

    thin = StubSource("thin", bars={"920000": 1})
    good = StubSource("good", bars={"920000": 250})
    provider.sources = [thin, good]
    provider.health = {s.name: SourceHealth(name=s.name) for s in provider.sources}

    frames = await provider.get_daily_kline_batch(["920000"], 250)

    assert len(frames["920000"]) == 250
    assert provider.health["thin"].total_failure >= 1, "腾讯式「只给 1 根」应被记为缺陷"
    assert provider.health["good"].total_failure == 0
    assert provider.health["good"].total_success >= 1


@pytest.mark.asyncio
async def test_empty_result_is_still_recorded_as_failure(provider: ResilientProvider):
    """源返回完全空结果（如被网络阻断）仍必须记为失败 —— 归因改动不能放宽这条。"""
    from app.providers.source_base import SourceHealth

    empty = StubSource("empty", bars={})
    provider.sources = [empty]
    provider.health = {s.name: SourceHealth(name=s.name) for s in provider.sources}

    await provider.get_daily_kline_batch(["600519"], 250)

    assert provider.health["empty"].total_failure >= 1


# ------------------------------------------------------- 缓存 I/O 不得阻塞事件循环
# 实测：全市场 5561 只、137 万行时，KlineCache.get_many 同步执行要 **43.94 秒**，
# upsert_many 写回也要数十秒。它们此前直接写在 async 函数里，于是这几十秒内
# 事件循环被完全占死 —— 所有并发请求（含 /health、/market/clock）都是
# 「TCP 已连接但无任何响应」，浏览器 15 秒后报超时，前置代理报 502。
# 用户看到的是「部署后整个页面全部加载失败」，而应用其实没崩。
# 以下用例确保缓存读写始终在 worker 线程里执行，事件循环始终保持可响应。


@pytest.mark.asyncio
async def test_cache_io_runs_off_the_event_loop(provider: ResilientProvider):
    """缓存读/写必须在别的线程执行：调用期间事件循环仍能推进心跳。"""
    import threading
    import time as _time

    from app.providers.source_base import SourceHealth

    loop_thread = threading.get_ident()
    seen_threads: list[int] = []
    ticks: list[float] = []

    real_get_many = provider.cache.get_many

    def slow_get_many(codes, days, *a, **kw):
        seen_threads.append(threading.get_ident())
        _time.sleep(0.25)  # 模拟真实的全市场读取耗时
        return real_get_many(codes, days, *a, **kw)

    provider.cache.get_many = slow_get_many  # type: ignore[method-assign]
    good = StubSource("good", bars={"600519": 250})
    provider.sources = [good]
    provider.health = {s.name: SourceHealth(name=s.name) for s in provider.sources}

    async def heartbeat() -> None:
        for _ in range(5):
            ticks.append(_time.perf_counter())
            await asyncio.sleep(0.05)

    hb = asyncio.create_task(heartbeat())
    started = _time.perf_counter()
    frames = await provider.get_daily_kline_batch(["600519"], 250)
    elapsed = _time.perf_counter() - started
    await hb

    assert "600519" in frames
    assert seen_threads and all(t != loop_thread for t in seen_threads), (
        "缓存读取必须在线程池中执行，否则会阻塞事件循环数十秒"
    )
    # 关键断言：0.25 秒的缓存读取期间，事件循环仍推进了心跳
    assert len(ticks) >= 4, f"缓存读取期间事件循环被阻塞（仅 {len(ticks)} 次心跳）"
    assert elapsed >= 0.25


@pytest.mark.asyncio
async def test_memory_cache_serves_repeat_requests_without_db_read(provider: ResilientProvider):
    """第二轮请求应命中进程内缓存，不再读数据库。"""
    from app.providers.source_base import SourceHealth

    provider._settings.kline_memory_cache_seconds = 900  # type: ignore[attr-defined]
    good = StubSource("good", bars={"600519": 250, "300750": 250})
    provider.sources = [good]
    provider.health = {s.name: SourceHealth(name=s.name) for s in provider.sources}

    calls = {"n": 0}
    real_get_many = provider.cache.get_many

    def counting_get_many(codes, days, *a, **kw):
        calls["n"] += 1
        return real_get_many(codes, days, *a, **kw)

    provider.cache.get_many = counting_get_many  # type: ignore[method-assign]

    first = await provider.get_daily_kline_batch(["600519", "300750"], 250)
    after_first = calls["n"]
    second = await provider.get_daily_kline_batch(["600519", "300750"], 250)

    assert len(first["600519"]) == len(second["600519"]) == 250
    assert after_first >= 1, "首轮必须读一次数据库"
    assert calls["n"] == after_first, "第二轮不应再读数据库（应命中进程内缓存）"
    assert provider.status()["memoryCache"]["codes"] == 2


@pytest.mark.asyncio
async def test_memory_cache_can_be_disabled_and_invalidated(provider: ResilientProvider):
    """ttl=0 时彻底关闭；显式失效后必须重新读库。"""
    from app.providers.source_base import SourceHealth

    good = StubSource("good", bars={"600519": 250})
    provider.sources = [good]
    provider.health = {s.name: SourceHealth(name=s.name) for s in provider.sources}

    # 关闭
    provider._settings.kline_memory_cache_seconds = 0  # type: ignore[attr-defined]
    await provider.get_daily_kline_batch(["600519"], 250)
    assert provider.status()["memoryCache"]["codes"] == 0

    # 开启并失效
    provider._settings.kline_memory_cache_seconds = 900  # type: ignore[attr-defined]
    await provider.get_daily_kline_batch(["600519"], 250)
    assert provider.status()["memoryCache"]["codes"] == 1
    provider.invalidate_memory_cache()
    assert provider.status()["memoryCache"]["codes"] == 0


# ------------------------------------------------------- 缓存写入必须单事务
def test_upsert_many_uses_single_transaction(tmp_path):
    """批量写入必须合并事务：逐只 commit 在全市场下会产生 5000+ 次 fsync。"""
    from app.providers.klines_cache import KlineCache

    cache = KlineCache(tmp_path / "single-tx.db")
    frames = {f"60000{i}": frame(30) for i in range(5)}

    class CountingConn:
        """统计 commit 次数（sqlite3.Connection.commit 是只读属性，无法直接打桩）。"""

        def __init__(self, conn) -> None:
            self._conn = conn
            self.commits = 0

        def commit(self) -> None:
            self.commits += 1
            self._conn.commit()

        def __getattr__(self, name):  # noqa: ANN001 - 其余方法原样转发
            return getattr(self._conn, name)

    proxy = CountingConn(cache.conn)
    cache._conn = proxy  # type: ignore[assignment]

    codes, rows = cache.upsert_many(frames)
    assert codes == 5
    assert rows == 5 * 30
    assert proxy.commits == 1, f"应只提交一次事务，实际 {proxy.commits} 次"
    # 数据确实落库
    assert len(cache.get("600000", 250)) == 30
    cache.close()
