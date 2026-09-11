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
