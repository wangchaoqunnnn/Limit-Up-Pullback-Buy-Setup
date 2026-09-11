"""多源故障转移数据源（``ResilientProvider``）。

设计目标（对应需求「行情数据要有多个备用源，当某个源获取不到数据时，果断切换到别的源」）：
1. **按操作故障转移**，而不是整体降级。取股票列表、取某只日线、取实时行情
   各自独立地在多个源之间切换；A 源挂了不会让整个应用退回演示数据。
2. **数据源健康度 + 指数退避**：连续失败的源会进入冷却期被跳过，
   冷却结束后自动恢复参与，避免每次请求都在一个死源上白等超时。
3. **增量缓存**：日线首轮全量落 SQLite，之后只补最近若干根，
   使开盘期间的 30 秒刷新在真实全市场数据上可行。
4. **合成数据仅作最后兜底**：只有全部真实源都不可用时才启用，
   并在 ``dataSource`` 中明确标注，前端会显著提示「演示数据」。

数据新鲜度：
- 由 ``market_calendar.effective_ttl`` 按交易时段决定 TTL
  （开盘 30 秒 / 集合竞价与午休 60 秒 / 盘后 300 秒）；
- 缓存未过期直接命中；已过期则增量补齐，**只有最后一根 K 线会被更新**。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Iterable, Sequence

import pandas as pd

from ..config import get_settings
from ..market_calendar import effective_ttl, is_market_open, market_clock, now_cn, reference_bar_date
from ..models import StockMeta
from .base import BaseProvider, DataSourceError, empty_kline
from .industry import IndustryEnricher
from .klines_cache import KlineCache
from .synthetic import SyntheticProvider
from .source_base import MarketSource, MarketSourceError, SourceHealth

logger = logging.getLogger(__name__)

#: 判定「日线数据是否可用」的最小根数。
#: 取 20 的理由：策略要算 MA20（需要 20 根），也是「位置比例」等判据的最低要求。
#: 低于此值说明该源没有真正的历史数据（例如腾讯对北交所只返回 1 根），
#: 应判定为取数失败并切换到其他源，而不是把残缺数据喂给策略引擎。
#: 注：次新股上市不足 20 个交易日本就无足够历史，属正常情况，
#: 策略会因数据不足而跳过，不会误判。
MIN_BARS = 20

#: 某只股票在所有源上都拿不到足够历史时，多久内不再重试（秒）。
#: 取值不大不小：太短会每次刷新都白跑一轮上游请求，太长则新上市股票
#: 补到足够历史后无法及时纳入。20 分钟足以覆盖开盘期间的多轮刷新。
UNAVAILABLE_RETRY_SECONDS = 1200


def build_sources(order: Sequence[str], timeout: float, concurrency: int, kline_concurrency: int = 16) -> list[MarketSource]:
    """按优先级实例化真实数据源适配器（延迟导入，任一源缺失不影响其他源）。

    ``concurrency`` 用于列表/实时等轻量请求，``kline_concurrency`` 单独用于
    日线批量抓取（数量级更大，需要更高并发）。
    """
    built: list[MarketSource] = []
    for name in order:
        try:
            if name == "eastmoney":
                from .eastmoney_source import EastMoneySource

                built.append(EastMoneySource(timeout=timeout, concurrency=concurrency))
            elif name == "tencent":
                from .tencent import TencentSource

                built.append(TencentSource(timeout=timeout, concurrency=kline_concurrency))
            elif name == "sina":
                from .sina import SinaSource

                built.append(SinaSource(timeout=timeout, concurrency=kline_concurrency))
            else:
                logger.warning("未知的数据源名称：%s（已跳过）", name)
        except ImportError as exc:  # 适配器文件缺失时不应导致启动失败
            logger.warning("数据源 %s 加载失败：%s", name, exc)
    return built


class ResilientProvider(BaseProvider):
    """多源故障转移 + 增量缓存 + 合成数据兜底的真实行情数据源。"""

    def __init__(self) -> None:
        settings = get_settings()
        self._settings = settings
        self.sources = build_sources(
            settings.source_order_list,
            timeout=settings.http_timeout,
            concurrency=settings.source_concurrency,
            kline_concurrency=settings.kline_concurrency,
        )
        self.health: dict[str, SourceHealth] = {s.name: SourceHealth(name=s.name) for s in self.sources}
        self.fallback = SyntheticProvider()
        self.cache = KlineCache(settings.kline_db_file, max_days=settings.kline_cache_max_days)
        # 行业分类补充（腾讯排行榜不返回行业，会导致「信号五：板块情绪」失真）
        self.industry = IndustryEnricher(
            settings.industry_map_file, timeout=max(20.0, settings.http_timeout * 2)
        )

        # 运行期状态
        self._using_fallback = not self.sources
        self._active_source: str | None = None
        self._last_pick: dict[str, str | None] = {}
        self._universe: list[StockMeta] = []
        self._universe_at: float = 0.0
        self._universe_source: str | None = None
        self._universe_boards: dict[str, int] = {}
        self._universe_complete = False
        self._industry_ready = False
        self._refresh_lock = asyncio.Lock()
        # 负缓存：无法取到足够历史的股票（如部分北交所标的，腾讯只给 1 根、
        # 新浪也没有足够历史）。记录重试时间，冷却期内不再反复向上游请求，
        # 否则这些「注定拿不到」的股票会在每次刷新时都触发一轮重试。
        self._unavailable_until: dict[str, float] = {}

    # ------------------------------------------------------------------ 标识
    @property
    def name(self) -> str:  # type: ignore[override]
        if self._using_fallback:
            return "synthetic"
        return self._active_source or (self.sources[0].name if self.sources else "unknown")

    def source_label(self) -> str:
        return self.name

    def has_real_sources(self) -> bool:
        return bool(self.sources)

    # ------------------------------------------------------------------ 生命周期
    async def close(self) -> None:
        for source in self.sources:
            await source.close()
        await self.fallback.close()
        self.cache.close()

    def reset_health(self) -> None:
        """重置全部数据源的健康度，使被跳过的源重新参与尝试。

        供 ``GET /api/v1/sources?probe=true``（设置页「重新探测」）使用：
        网络策略变化后无需重启服务即可重新接纳此前不可达的源。
        """
        for health in self.health.values():
            health.reset()
        logger.info("已重置全部数据源健康度，将重新尝试所有源")

    async def ping(self) -> bool:
        """探测是否至少有一个真实源可用。"""
        if not self.sources:
            return False
        for source in self.sources:
            if not self.health[source.name].available():
                continue
            if await source.ping():
                self.health[source.name].record_success()
                self._active_source = source.name
                return True
            self.health[source.name].record_failure("ping 失败")
        return False

    # ------------------------------------------------------------------ 选源
    def _ordered_sources(self, capability: str | None = None) -> list[MarketSource]:
        """按「健康度 + 声明优先级」返回可用源，冷却中的源排在最后。"""
        candidates = [s for s in self.sources if self._supports(s, capability)]
        if not candidates:
            return []
        now = time.time()
        ready = [s for s in candidates if self.health[s.name].available(now)]
        cooling = [s for s in candidates if not self.health[s.name].available(now)]
        # ready 保持声明顺序；cooling 追加在后面（全部 ready 失败后仍会尝试，
        # 避免所有源同时进入冷却导致完全无源可用）
        return ready + cooling

    @staticmethod
    def _supports(source: MarketSource, capability: str | None) -> bool:
        if capability is None:
            return True
        return {
            "stock_list": source.supports_stock_list,
            "realtime": source.supports_realtime,
        }.get(capability, True)

    async def _try_sources(
        self,
        operation: str,
        capability: str | None,
        call,
    ) -> tuple[Any, str]:
        """依次尝试各数据源，返回 ``(结果, 生效源名)``；全部失败抛 DataSourceError。

        ``call`` 为 ``async (source) -> result``；返回 ``None`` 或空结果视为失败，
        继续尝试下一个源 —— 这实现了「某个源获取不到数据就果断切换」。
        """
        sources = self._ordered_sources(capability)
        errors: list[str] = []
        for source in sources:
            health = self.health[source.name]
            try:
                result = await call(source)
            except (MarketSourceError, asyncio.TimeoutError, OSError, ValueError) as exc:
                health.record_failure(str(exc))
                errors.append(f"{source.name}: {exc}")
                logger.warning("数据源 %s 在 %s 失败，切换到下一个源（%s）", source.name, operation, exc)
                continue
            except Exception as exc:  # noqa: BLE001 - 适配器未预期异常也不能中断整体
                health.record_failure(f"{type(exc).__name__}: {exc}")
                errors.append(f"{source.name}: {exc}")
                logger.warning("数据源 %s 在 %s 抛出未预期异常，切换到下一个源（%s）", source.name, operation, exc)
                continue
            if result is None or (hasattr(result, "__len__") and len(result) == 0):  # type: ignore[arg-type]
                health.record_failure("返回空结果")
                errors.append(f"{source.name}: 返回空结果")
                continue
            health.record_success()
            self._active_source = source.name
            self._last_pick[operation] = source.name
            return result, source.name
        detail = "；".join(errors[:4]) if errors else "无可用数据源"
        raise DataSourceError(f"全部数据源在「{operation}」上均失败：{detail}")

    # ------------------------------------------------------------------ 股票列表
    async def get_stock_list(self) -> list[StockMeta]:
        """获取扫描用的股票池。

        真实源可能不支持全市场列表（如腾讯），因此按能力筛选源；
        拿到全量列表后按成交额降序截取 ``UNIVERSE_SIZE`` 只，
        兼顾「覆盖全部强势股」与「刷新耗时可控」。
        """
        if self._using_fallback:
            return await self.fallback.get_stock_list()

        ttl = max(300, int(self._settings.cache_ttl_seconds))
        if self._universe and (time.time() - self._universe_at) < ttl:
            return self._universe

        async with self._refresh_lock:
            if self._universe and (time.time() - self._universe_at) < ttl:
                return self._universe

            # ① 尝试磁盘快照（避免每次重启都全市场翻页）
            cached = self._load_universe_snapshot()
            if cached:
                self._universe = cached
                self._universe_at = time.time()
                self._universe_boards = self._board_counts(cached)
                self._universe_complete = True
                logger.info(
                    "股票池命中本地快照：%d 只，板块分布 %s", len(cached), self._universe_boards
                )
                await self._ensure_industry()
                return self._universe

            # ② 从支持列表能力的真实源获取。
            #    关键：不同源的覆盖范围不同（腾讯排行榜实测缺科创板与北交所），
            #    因此这里逐个源尝试并做**板块完整性校验**，优先采用覆盖最全的源，
            #    绝不允许「静默漏掉某个板块」。
            metas, source_name, _coverage = await self._fetch_best_universe()
            if metas:
                metas = self._rank_and_limit(list(metas))
                self._universe = metas
                self._universe_at = time.time()
                self._universe_source = source_name
                self._universe_boards = self._board_counts(metas)
                missing = [b for b in self.REQUIRED_BOARDS if not self._universe_boards.get(b)]
                # 关键：**只有板块覆盖完整时才落盘快照**。
                # 否则一次被上游限流导致的残缺列表会被缓存 24 小时，
                # 变成「静默遗漏科创板/北交所」——正是用户明确要求避免的问题。
                if not missing:
                    self._universe_complete = True
                    await self._ensure_industry()
                    self._save_universe_snapshot(metas, source_name)
                else:
                    self._universe_complete = False
                    logger.warning(
                        "股票池板块不完整（缺 %s），本次**不写入快照**，且下次请求会重新尝试获取完整列表",
                        "/".join(missing),
                    )
                    # 缩短内存有效期为 1 分钟：让后续请求较快重试完整列表，
                    # 而不是把残缺结果缓存满 cache_ttl
                    self._universe_at = time.time() - max(0, ttl - 60)
                    await self._ensure_industry()
                logger.info(
                    "股票池来自 %s：%d 只，板块分布 %s（完整=%s）",
                    source_name,
                    len(metas),
                    self._universe_boards,
                    not missing,
                )
                return self._universe

            logger.warning("全部真实源均无法获取股票列表")
            if cached is None and self._settings.data_source_mode == "real":
                raise DataSourceError("全部真实源均无法获取股票列表")
            await self._enable_fallback("获取股票列表失败")
            return await self.fallback.get_stock_list()

    #: 必须覆盖的板块（用户明确要求「所有交易所的股票都不能遗漏」）
    REQUIRED_BOARDS: tuple[str, ...] = ("主板", "创业板", "科创板", "北交所")

    @staticmethod
    def _board_counts(metas: Sequence[StockMeta]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for meta in metas:
            counts[meta.board] = counts.get(meta.board, 0) + 1
        return counts

    async def _fetch_best_universe(self) -> tuple[list[StockMeta], str, dict[str, int]]:
        """依次尝试各源获取股票列表，返回**板块覆盖最全**的那一份。

        为什么要这样：腾讯排行榜实测只有沪深主板+创业板（无科创板、无北交所），
        新浪三 node 并集才覆盖全部四个板块。若简单「先到先得」，
        用腾讯就会静默漏掉科创板与北交所 —— 这正是用户明确要求避免的问题。
        """
        best: list[StockMeta] = []
        best_source = ""
        best_coverage: dict[str, int] = {}
        best_score = -1
        errors: list[str] = []

        for source in self._ordered_sources("stock_list"):
            health = self.health[source.name]
            try:
                metas = list(await self._list_stocks_from(source, int(self._settings.universe_size)))
            except (MarketSourceError, asyncio.TimeoutError, OSError, ValueError) as exc:
                health.record_failure(str(exc))
                errors.append(f"{source.name}: {exc}")
                logger.warning("数据源 %s 获取股票列表失败，尝试下一个源（%s）", source.name, exc)
                continue
            except Exception as exc:  # noqa: BLE001
                health.record_failure(f"{type(exc).__name__}: {exc}")
                errors.append(f"{source.name}: {exc}")
                logger.warning("数据源 %s 获取股票列表异常，尝试下一个源（%s）", source.name, exc)
                continue

            if not metas:
                health.record_failure("返回空结果")
                errors.append(f"{source.name}: 返回空结果")
                continue

            coverage = self._board_counts(metas)
            missing = [b for b in self.REQUIRED_BOARDS if not coverage.get(b)]
            score = len(self.REQUIRED_BOARDS) - len(missing)

            if missing:
                logger.warning(
                    "数据源 %s 的股票列表缺少板块 %s（共 %d 只，分布 %s）",
                    source.name,
                    "/".join(missing),
                    len(metas),
                    coverage,
                )
            else:
                logger.info("数据源 %s 的股票列表板块覆盖完整：%d 只", source.name, len(metas))

            if score > best_score:
                best, best_source, best_coverage, best_score = metas, source.name, coverage, score

            # 已经拿到完整覆盖就不再尝试后续源，省掉一次全市场翻页
            if not missing:
                break

        if best:
            self.health[best_source].record_success()
            self._active_source = best_source
            self._last_pick["获取股票列表"] = best_source
            missing = [b for b in self.REQUIRED_BOARDS if not best_coverage.get(b)]
            if missing:
                logger.warning(
                    "所有可用数据源都无法覆盖板块 %s，股票池将有遗漏（共 %d 只，分布 %s）",
                    "/".join(missing),
                    len(best),
                    best_coverage,
                )
            return best, best_source, best_coverage

        raise DataSourceError(f"全部数据源获取股票列表均失败：{'；'.join(errors[:4]) or '无可用数据源'}")

    def _list_stocks_from(self, source: MarketSource, limit: int) -> Any:
        """向单个源请求股票列表。

        优先使用源的「全市场覆盖」能力（``list_stocks_all``）：
        沪深主板、创业板、**科创板**、**北交所** 一个都不能漏。
        例如腾讯排行榜只覆盖沪深主板+创业板（实测无科创板与北交所），
        新浪则需要把 ``hs_a``（北交所+沪深）与 ``kcb``（科创板）等多个 node 求并集。
        """
        complete = getattr(source, "list_stocks_all", None)
        if callable(complete):
            return complete()
        # 退化路径：只有成交额榜（覆盖不全，仅在源没有全量能力时使用）
        ranked = getattr(source, "list_stocks_ranked", None)
        if callable(ranked):
            logger.warning("%s 无全市场列表能力，仅使用成交额榜（可能缺少科创板/北交所）", source.name)
            return ranked(limit)
        return source.list_stocks()

    def _rank_and_limit(self, metas: list[StockMeta]) -> list[StockMeta]:
        """按配置截取股票池。

        ``UNIVERSE_SIZE <= 0`` 表示**不限量、覆盖全部 A 股**（默认行为，
        对应用户要求「所有交易所的股票都不能遗漏」）。
        限量时按「先保证每个板块都有代表、再按来源顺序补齐」的策略截取，
        避免截取后某个板块被整体裁掉。
        """
        limit = int(self._settings.universe_size)
        if limit <= 0 or len(metas) <= limit:
            return metas
        # 按板块分组，轮转取用，保证截取结果里四个板块都还有股票
        order: list[str] = ["主板", "创业板", "科创板", "北交所"]
        buckets: dict[str, list[StockMeta]] = {name: [] for name in order}
        for meta in metas:
            buckets.setdefault(meta.board, []).append(meta)
        picked: list[StockMeta] = []
        cursor = 0
        while len(picked) < limit:
            progressed = False
            for name in order:
                bucket = buckets.get(name) or []
                if cursor < len(bucket):
                    picked.append(bucket[cursor])
                    progressed = True
                    if len(picked) >= limit:
                        break
            if not progressed:
                break
            cursor += 1
        logger.info(
            "股票池限量 %d 只（覆盖策略：按板块轮转）：%s",
            limit,
            {name: sum(1 for m in picked if m.board == name) for name in order},
        )
        return picked

    # ------------------------------------------------------------------ 股票列表快照
    def _load_universe_snapshot(self) -> list[StockMeta] | None:
        path = self._settings.stock_list_file
        if not path.exists():
            return None
        try:
            import json

            raw = json.loads(path.read_text(encoding="utf-8"))
            saved_at = float(raw.get("savedAt", 0))
            # 快照有效期：1 天（成员变动不频繁，且失败时会重新拉取）
            if (time.time() - saved_at) > 86400:
                return None
            # 快照必须与当前配置的股票池规模一致，否则改了 UNIVERSE_SIZE 却不生效
            saved_limit = int(raw.get("limit") or 0)
            if saved_limit != int(self._settings.universe_size):
                logger.info(
                    "股票列表快照规模（%d）与当前配置（%d）不一致，将重新获取",
                    saved_limit,
                    int(self._settings.universe_size),
                )
                return None
            items = raw.get("items") or []
            metas = [StockMeta(**item) for item in items]
            self._universe_source = raw.get("source")
            return metas or None
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("股票列表快照读取失败：%s", exc)
            return None

    def _save_universe_snapshot(self, metas: list[StockMeta], source_name: str) -> None:
        import json
        import os
        import tempfile

        path = self._settings.stock_list_file
        payload = {
            "savedAt": time.time(),
            "source": source_name,
            "count": len(metas),
            # 记录生成快照时的股票池规模，用于判断配置是否已变更
            "limit": int(self._settings.universe_size),
            "items": [m.model_dump() for m in metas],
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            os.replace(tmp, path)
        except (OSError, TypeError) as exc:
            logger.warning("股票列表快照写入失败：%s", exc)

    def invalidate_universe(self) -> None:
        """强制下次重新获取股票列表。"""
        self._universe_at = 0.0

    async def _ensure_industry(self) -> None:
        """用真实行业分类补齐股票池的 ``industry`` 字段（纯增强，失败不影响主流程）。

        腾讯排行榜不返回行业，若不补齐，全部股票都会是「未分类」，
        ``build_sector_context`` 会把整个股票池当成一个板块，
        导致板块情绪分失真、行业热度榜无意义、信号五失去区分度。
        """
        if self._industry_ready or not self._universe:
            return
        try:
            mapping = await self.industry.get_mapping()
        except Exception as exc:  # noqa: BLE001 - 行业补充失败必须静默降级
            logger.warning("行业分类补充失败（将保持「未分类」）：%s", exc)
            return
        if not mapping:
            logger.warning("未获取到行业分类，板块情绪将基于「未分类」计算")
            self._industry_ready = True
            return
        hit = 0
        for meta in self._universe:
            name = mapping.get(meta.code)
            if name:
                meta.industry = name
                hit += 1
        self._industry_ready = True
        logger.info("行业分类已补齐：%d/%d 只股票获得真实行业", hit, len(self._universe))

    # ------------------------------------------------------------------ 日线
    async def get_daily_kline(self, code: str, days: int = 250) -> pd.DataFrame:
        """获取单只日线：缓存优先 + 增量补齐 + 多源故障转移。"""
        if self._using_fallback:
            return await self.fallback.get_daily_kline(code, days)
        frames = await self._ensure_klines([code], days)
        df = frames.get(code)
        if df is None or df.empty:
            raise DataSourceError(f"无法获取 {code} 的日线数据")
        return df

    async def get_daily_kline_batch(self, codes: Iterable[str], days: int = 250) -> dict[str, pd.DataFrame]:
        """批量获取日线：只向数据源请求「缓存缺失或已过期」的部分。"""
        if self._using_fallback:
            return await self.fallback.get_daily_kline_batch(codes, days)
        wanted = [str(c) for c in codes]
        return await self._ensure_klines(wanted, days)

    async def _ensure_klines(self, codes: Sequence[str], days: int) -> dict[str, pd.DataFrame]:
        """核心：按需增量补齐日线并返回完整结果。"""
        settings = self._settings
        ttl = effective_ttl(settings.cache_ttl_seconds, settings.refresh_interval_seconds)
        # 已将当日最后一根写进缓存时，只要距上次写入不超过 ttl 就算新鲜。
        # 若最后一根早于今日（休市 / 尚未开盘 / 未收盘），行情不会变化，
        # 放宽到 CACHE_TTL_SECONDS，避免无意义地反复补拉。
        idle_ttl = max(ttl, int(settings.cache_ttl_seconds))
        today = now_cn().strftime("%Y-%m-%d")
        # 「当前应已存在的最新日线日期」。收盘后它等于当日（或最近交易日），
        # 因此缓存里若已有这个日期，就说明本轮行情已经取全，
        # 没有任何理由再向上游请求 —— 这既省配额也避免触发反爬限流。
        ref_date = reference_bar_date()
        session_live = is_market_open()

        out: dict[str, pd.DataFrame] = {}
        stale: list[str] = []
        thin_cached: list[str] = []
        # 一次批量读回全部缓存（避免逐只查询产生 1000 次往返）
        cached_frames = self.cache.get_many(codes, days)
        for code in codes:
            df = cached_frames.get(code)
            if df is None or df.empty:
                stale.append(code)
                continue
            last_date = pd.Timestamp(df["date"].iloc[-1]).strftime("%Y-%m-%d")
            # 缓存里的数据量本身就不足（例如历史上被腾讯的 1 根数据写脏过）：
            # 无论 TTL 是否到期都要重取，否则这些股票会永远停留在「无法分析」状态。
            if len(df) < MIN_BARS:
                # 已被判定为「上游拿不到足够历史」的股票，在冷却期内直接跳过重试
                if self._unavailable_until.get(code, 0.0) > time.time():
                    continue
                thin_cached.append(code)
                stale.append(code)
                continue
            # 核心判定：缓存的最后一根已达到「应有的最新日期」。
            #   - 盘中：当日 K 线仍在变动，交给 TTL 控制（refresh_interval）；
            #   - 非盘中：当日 K 线已定型，直接视为新鲜，不再取数。
            if last_date >= ref_date and not session_live:
                out[code] = df
                continue
            age = self.cache.last_write_age(code)
            # age 为 None 表示无法判断写入时间（数据库文件不可读等），
            # 此时保守地视为过期并走增量补齐，但绝不能因为 None 而抛异常。
            if age is None:
                stale.append(code)
                continue
            if age < ttl:
                out[code] = df
                continue
            if last_date >= ref_date:
                # 日期已到位，只是写入时间稍旧（例如进程重启）：
                # 盘中才需要重新取当日那根，非盘中无需处理。
                if not session_live:
                    out[code] = df
                    continue
            stale.append(code)

        if thin_cached:
            logger.info(
                "缓存中有 %d 只股票的日线不足 %d 根，将忽略新鲜度并重新获取（示例：%s）",
                len(thin_cached),
                MIN_BARS,
                thin_cached[:5],
            )

        if not stale:
            return out

        # 增量：已缓存的只需补最近若干根；未缓存的取完整历史。
        # 注意：必须给「已有缓存」的股票留出足够的回看窗口（至少 30 根），
        # 因为补齐结果会作为最终行情返回给策略引擎使用，而策略要算 MA20、
        # 近 120 日位置等判据。曾经这里取 gap+3（通常只有 5 根），
        # 导致返回给上层的日线只剩 5 根、技术判据全部失真。
        MIN_TAIL = 30
        fetch_days: dict[str, int] = {}
        for code in stale:
            cached = cached_frames.get(code)
            if cached is None or cached.empty:
                # 从未缓存过：取完整历史
                fetch_days[code] = max(int(days), 120)
            elif len(cached) < MIN_BARS:
                # 缓存里只有极少数几根（典型情况：被某个源用 1 根数据写脏）：
                # 必须按完整历史重取，否则永远无法补齐到可用长度。
                fetch_days[code] = max(int(days), 120)
            else:
                last_date = pd.Timestamp(cached["date"].iloc[-1])
                gap = max(0, (now_cn().date() - last_date.date()).days)
                have = int(len(cached))
                if gap <= 0:
                    # 盘中刷新：缓存最后一根就是今天，只需重取最近几根来更新当日那根。
                    # 这样 30 秒一轮的请求量从「每股 250 根」降到「每股 3 根」，
                    # 是开盘期间能真正跑起来的关键。
                    # 历史由 upsert 的 (code,date) 主键保证不被覆盖丢失。
                    fetch_days[code] = 3
                else:
                    # 有跨日缺口：补齐缺口 + 保留足够回看窗口
                    needed = max(MIN_TAIL, gap + 10)
                    fetch_days[code] = int(min(max(int(days), 120), max(needed, min(have, int(days)))))

        fetched = await self._fetch_klines(stale, fetch_days, days)
        out.update(fetched)

        # 补齐失败但缓存里有可用旧数据的，仍然返回旧数据（比空好）。
        # 但**不返回不足 MIN_BARS 的残缺数据** —— 宁可让策略因数据不足跳过，
        # 也不能用 1 根 K 线算出来的均线与位置判据误导用户。
        for code in stale:
            if code in out:
                continue
            backup = cached_frames.get(code)
            if backup is not None and len(backup) >= MIN_BARS:
                out[code] = backup

        # 统一裁剪到请求的根数，保证调用方拿到的日线长度一致：
        # 直接命中缓存的股票是 days 根，而增量补齐的股票可能只有几十根，
        # 若不统一，跨股票的均线/位置判据会因窗口不同而失真。
        limit = int(days)
        if limit > 0:
            for code, df in list(out.items()):
                if df is not None and len(df) > limit:
                    out[code] = df.tail(limit).reset_index(drop=True)
        return out

    async def _fetch_klines(
        self, codes: Sequence[str], fetch_days: dict[str, int], want_days: int
    ) -> dict[str, pd.DataFrame]:
        """从数据源分批并发抓取并写入缓存；同一批内多源故障转移。"""
        if not codes:
            return {}

        # 按请求天数分组（多数情况下相同），减少批次拆分
        groups: dict[int, list[str]] = {}
        for code in codes:
            groups.setdefault(int(fetch_days.get(code, want_days)), []).append(code)

        result: dict[str, pd.DataFrame] = {}
        for days_wanted, group in groups.items():
            remaining = list(group)
            for source in self._ordered_sources():
                if not remaining:
                    break
                health = self.health[source.name]
                try:
                    frames = await source.daily_kline_many(remaining, days=days_wanted)
                except (MarketSourceError, asyncio.TimeoutError, OSError) as exc:
                    health.record_failure(str(exc))
                    logger.warning("数据源 %s 批量取日线失败，切换下一个源（%s）", source.name, exc)
                    continue
                except Exception as exc:  # noqa: BLE001
                    health.record_failure(f"{type(exc).__name__}: {exc}")
                    logger.warning("数据源 %s 批量取日线异常，切换下一个源（%s）", source.name, exc)
                    continue

                ok_frames: dict[str, pd.DataFrame] = {}
                thin: list[str] = []
                for code, df in (frames or {}).items():
                    if df is None or df.empty:
                        continue
                    # 关键：**数据量过少也算取数失败**，同样要切换下一个源。
                    # 实测腾讯日线对北交所（920xxx）只返回 1 根当日 K 线，
                    # 既不报错也不为空；若按「非空即成功」处理，这些股票会带着
                    # 1 根数据进入策略引擎，所有均线/位置判据全部失真
                    # —— 等于「进了股票池却无法分析」，是隐蔽的静默数据缺失。
                    # 新浪对同一批标的有完整历史，切换过去即可修复。
                    if len(df) < MIN_BARS:
                        thin.append(str(code))
                        continue
                    ok_frames[str(code)] = df

                if thin:
                    logger.warning(
                        "数据源 %s 对 %d 只股票返回的日线过少（< %d 根），判定为取数失败并切换下一个源"
                        "（示例：%s）",
                        source.name,
                        len(thin),
                        MIN_BARS,
                        thin[:5],
                    )
                if not ok_frames:
                    health.record_failure(f"返回空结果或数据量不足（{len(thin)} 只过少）")
                    continue

                health.record_success()
                self._active_source = source.name
                self._last_pick["日线"] = source.name
                self.cache.upsert_many(ok_frames)
                result.update(ok_frames)
                # 仍缺失或数据量不足的交给下一个源补
                remaining = [c for c in remaining if c not in result]
            if remaining:
                # 这些股票在所有源上都拿不到足够历史：写入负缓存，
                # 冷却期内不再重试（默认 30 分钟，避免每次刷新都白跑一轮上游请求）。
                retry_after = time.time() + UNAVAILABLE_RETRY_SECONDS
                for code in remaining:
                    self._unavailable_until[code] = retry_after
                logger.warning(
                    "有 %d 只股票在所有源上均未取到足够日线，%.0f 分钟内不再重试（示例：%s）",
                    len(remaining),
                    UNAVAILABLE_RETRY_SECONDS / 60,
                    remaining[:5],
                )

        # 统一裁剪到请求的天数并保证列顺序
        final: dict[str, pd.DataFrame] = {}
        for code, df in result.items():
            final[code] = df.tail(int(want_days)).reset_index(drop=True)
        return final

    # ------------------------------------------------------------------ 实时
    async def get_realtime(self, codes: Iterable[str]) -> dict[str, dict[str, Any]]:
        """实时行情：优先走专用批量实时接口（比日线更快更准）。"""
        wanted = [str(c) for c in codes]
        if not wanted:
            return {}
        if self._using_fallback:
            return await self.fallback.get_realtime(wanted)
        try:
            data, _ = await self._try_sources("获取实时行情", "realtime", lambda s: s.realtime(wanted))
            return dict(data)
        except DataSourceError as exc:
            logger.warning("实时行情全部源失败（%s），回退为日线末行推导", exc)
            return await super().get_realtime(wanted)

    # ------------------------------------------------------------------ 指数
    async def get_index_snapshot(self) -> list[dict[str, Any]]:
        if self._using_fallback:
            return await self.fallback.get_index_snapshot()
        try:
            data, _ = await self._try_sources("获取指数快照", None, lambda s: s.index_snapshot())
            return list(data)
        except DataSourceError as exc:
            logger.warning("指数快照全部源失败：%s", exc)
            return []

    async def get_trade_date(self) -> str:
        latest = self.cache.latest_date()
        if latest:
            return latest
        metas = await self.get_stock_list()
        if not metas:
            return ""
        df = await self.get_daily_kline(metas[0].code, days=5)
        if df is None or df.empty:
            return ""
        return pd.Timestamp(df["date"].iloc[-1]).strftime("%Y-%m-%d")

    # ------------------------------------------------------------------ 兜底
    async def _enable_fallback(self, reason: str) -> None:
        if self._using_fallback:
            return
        self._using_fallback = True
        logger.warning("已切换为合成演示数据（原因：%s）", reason)

    # ------------------------------------------------------------------ 状态
    def status(self) -> dict[str, Any]:
        """数据源运行状态（供 /api/v1/settings 与 /api/v1/sources 展示）。"""
        settings = self._settings
        return {
            "mode": settings.data_source_mode,
            "active": self.source_label(),
            "usingFallback": self._using_fallback,
            "order": settings.source_order_list,
            "sources": [self.health[s.name].snapshot() for s in self.sources],
            "lastPickByOperation": dict(self._last_pick),
            "universeSize": len(self._universe) or int(settings.universe_size),
            "universeSource": self._universe_source,
            "universeBoards": self._universe_boards or self._board_counts(self._universe),
            "universeFullMarket": int(settings.universe_size) <= 0,
            "cache": self.cache.stats(),
            "ttlSeconds": effective_ttl(settings.cache_ttl_seconds, settings.refresh_interval_seconds),
            "refreshIntervalSeconds": int(settings.refresh_interval_seconds),
            "clock": market_clock(interval_seconds=settings.refresh_interval_seconds),
        }

    async def refresh_all(self, codes: Sequence[str] | None = None, days: int = 250) -> dict[str, Any]:
        """强制刷新（供 ``POST /api/v1/scan`` 的 refresh=true 使用）。

        返回刷新摘要，用于向前端反馈「实际向哪个源请求了多少只」。
        """
        target = list(codes) if codes else [m.code for m in await self.get_stock_list()]
        started = time.perf_counter()
        frames = await self._ensure_klines(target, days)
        elapsed = time.perf_counter() - started
        return {
            "requested": len(target),
            "fetched": len(frames),
            "elapsedSeconds": round(elapsed, 2),
            "source": self._last_pick.get("日线") or self.source_label(),
        }
