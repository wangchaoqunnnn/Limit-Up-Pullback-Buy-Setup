"""雅虎财经（Yahoo Finance chart API）备用行情源。

**定位：最后的跨网络冗余**。国内免费源（东方财富/腾讯/同花顺/新浪）同源风险较高，
可能因同一网络策略或同一反爬批次一起失效；雅虎是**完全独立**的境外公开接口，
因此把它放在 ``DATA_SOURCE_ORDER`` 末位作为兜底：平时不参与，一旦国内源全线不可用，
主板/创业板/科创板仍能取到完整日线。

实测结论（2026-09）：

- 日线 ``query1.finance.yahoo.com/v8/finance/chart/<symbol>?range=1y&interval=1d``
  对 ``600519.SS`` / ``300750.SZ`` / ``688981.SS`` 均返回约 243 根日线；
  与同花顺逐字段比对**完全一致**（O/H/L/C/V 全同）：

  ===========  ========  ========  ========  ========  ==========
  日期          开        高        低        收        成交量
  ===========  ========  ========  ========  ========  ==========
  2026-09-11   1285.15   1286.15   1263.01   1275.16   3480142
  ===========  ========  ========  ========  ========  ==========

- **成交量单位是「股」**，与同花顺口径一致，需 ÷100 转「手」。
- **不提供北交所**（``920000.BJ`` 返回 404 "No data found"），
  因此 ``daily_kline`` 对北交所标的直接抛错并交给其他源，不做无意义的请求。
- 不提供**成交额/换手率**：成交额由框架按 ``量(手) × 100 × 收盘价`` 估算
  （与「缺失成交额」时的统一口径一致），换手率填 0，绝不用猜测值冒充。

代码格式：``600519.SS``（上海） / ``300750.SZ``（深圳）。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Sequence

from .base import frame_from_records
from .source_base import (
    MarketSource,
    MarketSourceError,
    market_of_code,
    normalize_code,
)

logger = logging.getLogger(__name__)

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

#: 指数快照：(雅虎符号, 本项目代码, 兜底名称)。
#: 注意实测雅虎对深市指数覆盖很差（``399006.SZ`` 只有 1 根），
#: 因此这里只作为「腾讯/新浪都不可用」时的最后兜底，根数不足的会被过滤掉。
INDEX_LIST = (
    ("000001.SS", "000001", "上证指数"),
    ("399001.SZ", "399001", "深证成指"),
    ("399006.SZ", "399006", "创业板指"),
)

#: sparkline 与最少可用根数
SPARKLINE_BARS = 20
MIN_INDEX_BARS = 2


def yahoo_symbol(code: str) -> str:
    """把内部 6 位代码转成雅虎符号；北交所不被支持，直接抛错。"""
    code = normalize_code(code)
    if not code:
        raise MarketSourceError("雅虎财经请求了非法股票代码")
    market = market_of_code(code)
    if market == "BJ":
        raise MarketSourceError(f"雅虎财经不提供北交所标的（{code}）")
    return f"{code}.{'SS' if market == 'SH' else 'SZ'}"


def _parse_chart(payload: Any) -> list[dict[str, Any]]:
    """解析 chart 响应 → 规范化日线记录（拆成纯函数便于离线单测）。

    对齐 ``timestamp`` 与 ``indicators.quote[0]`` 的等长数组，
    丢弃任一字段为 null 的行（停牌日雅虎会给 null），失败抛 ``MarketSourceError``。
    """
    if not isinstance(payload, dict):
        raise MarketSourceError("雅虎财经返回非对象")
    chart = payload.get("chart") or {}
    if chart.get("error"):
        raise MarketSourceError(f"雅虎财经返回错误：{chart.get('error')}")
    results = chart.get("result") or []
    if not results:
        raise MarketSourceError("雅虎财经返回空 result")
    node = results[0] or {}
    stamps = node.get("timestamp") or []
    quotes = ((node.get("indicators") or {}).get("quote") or [{}])[0] or {}
    opens = quotes.get("open") or []
    highs = quotes.get("high") or []
    lows = quotes.get("low") or []
    closes = quotes.get("close") or []
    volumes = quotes.get("volume") or []

    records: list[dict[str, Any]] = []
    for i, stamp in enumerate(stamps):
        try:
            close = closes[i]
            open_ = opens[i]
            high = highs[i]
            low = lows[i]
        except IndexError:
            break
        if None in (close, open_, high, low):
            # 停牌/无成交：雅虎给 null，跳过而不是拿 0 冒充
            continue
        try:
            # A 股交易时段落在同一 UTC 日内（09:30 CST = 01:30 UTC），
            # 故按 UTC 日期归档即可得到正确的交易日。
            day = datetime.fromtimestamp(int(stamp), tz=timezone.utc).strftime("%Y-%m-%d")
            volume_shares = float(volumes[i] or 0.0)
            records.append(
                {
                    "date": day,
                    "open": float(open_),
                    "high": float(high),
                    "low": float(low),
                    "close": float(close),
                    # 雅虎给的是「股」，统一转为「手」
                    "volume": volume_shares / 100.0,
                    # 成交额雅虎不提供：留空，由 frame_from_records 统一估算
                }
            )
        except (TypeError, ValueError):
            continue
    if not records:
        raise MarketSourceError("雅虎财经日线无有效记录")
    return records


def _index_from_records(code: str, name: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    """由指数日线记录构造快照项（close/pctChg/sparkline）。"""
    closes = [float(r["close"]) for r in records]
    last = closes[-1]
    prev = closes[-2] if len(closes) >= 2 else last
    return {
        "code": code,
        "name": name,
        "close": round(last, 2),
        "pctChg": (last / prev - 1.0) if prev else 0.0,
        "sparkline": [round(x, 2) for x in closes[-SPARKLINE_BARS:]],
    }


class YahooSource(MarketSource):
    """雅虎财经 chart API：仅日线与指数，作为最后兜底。

    ``supports_stock_list = False``：不提供全市场列表；
    ``supports_realtime = False``：只能逐只取，数量级上不适合盘中批量刷新，
    强行为之会把 30 秒一轮的刷新拖成分钟级。日线则有 243 根且与国内源逐字段一致，
    是真正有冗余价值的能力。
    """

    name = "yahoo"
    supports_stock_list = False
    supports_realtime = False
    supports_index = True

    async def probe(self) -> None:
        """用一只主板股票的日线做轻量探测。"""
        await self._chart("600519.SS")

    async def _chart(self, symbol: str, bars: int = 250) -> list[dict[str, Any]]:
        """请求 chart 接口并解析；失败统一抛 ``MarketSourceError``。"""
        rng = "1y" if int(bars) <= 250 else "2y"
        payload = await self.get_json(
            CHART_URL.format(symbol=symbol),
            params={"range": rng, "interval": "1d"},
        )
        return _parse_chart(payload)

    # ------------------------------------------------------------------ 日线
    async def daily_kline(self, code: str, days: int = 250) -> Any:
        symbol = yahoo_symbol(code)
        records = await self._chart(symbol, bars=days)
        df = frame_from_records(records)
        if df is None or df.empty:
            raise MarketSourceError(f"雅虎财经未返回 {code} 的日线")
        return df

    async def daily_kline_many(self, codes: Sequence[str], days: int = 250) -> dict[str, Any]:
        """并发取多只日线；北交所标的会被立即跳过（不浪费一次请求）。

        ``daily_kline`` 抛出的 ``MarketSourceError`` 由基类逐只捕获，
        因此不支持的标的只会缺席，不影响整批。
        """
        return await super().daily_kline_many(codes, days=days)

    # ------------------------------------------------------------------ 指数
    async def index_snapshot(self) -> list[dict[str, Any]]:
        """主要指数快照（最后兜底；深市指数可能因上游覆盖不足被过滤掉）。"""
        sem = asyncio.Semaphore(self.concurrency)

        async def one(symbol: str, code: str, name: str) -> dict[str, Any] | None:
            async with sem:
                try:
                    records = await self._chart(symbol, bars=SPARKLINE_BARS)
                except (MarketSourceError, asyncio.TimeoutError, OSError) as exc:
                    logger.warning("雅虎财经指数 %s 失败：%s", symbol, exc)
                    return None
                if len(records) < MIN_INDEX_BARS:
                    # 覆盖不足（实测 399006.SZ 只有 1 根）：宁可缺席也不给残缺曲线
                    logger.warning("雅虎财经指数 %s 仅 %d 根，跳过", symbol, len(records))
                    return None
                return _index_from_records(code, name, records)

        items = await asyncio.gather(*(one(s, c, n) for s, c, n in INDEX_LIST))
        result = [item for item in items if item]
        if not result:
            raise MarketSourceError("雅虎财经指数快照全部不可用")
        return result
