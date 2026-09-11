"""同花顺（10jqka）行情源适配器。

为什么引入它：实测它是本轮**唯一能在东方财富被阻断、新浪被反爬限流时，
仍稳定提供「科创板」与「北交所」历史日线**的免费公开源。

实测结论（2026-09）：
- 日线 ``d.10jqka.com.cn/v6/line/hs_<code>/01/last.js``
  返回最近约 140 个交易日的日线，**四个板块全覆盖**：
  主板 600519 / 创业板 300750 / 科创板 688981 / **北交所 920000** 均有 140 根。
- 实时 ``d.10jqka.com.cn/v6/time/hs_<code>/last.js``
  返回分钟级分时 + **股票中文名称 + 昨收**，可取末条为最新价。
  注意：它**不提供成交量/换手率**等完整快照，只做报价与最新价来源。

日线字段（**实测 11 个字段**，逗号分隔）::

    20260911,1285.15,1286.15,1263.01,1275.16,3480142,4430841400.00,0.278,,1500.00,1912740
    日期      开      高      低      收      成交量  成交额         换手%  ?   ?        ?

- 成交量单位是**股**，需 ÷100 转成「手」以与腾讯/新浪口径一致；
- 换手率已是百分数；
- 第 9/10 字段语义未确认（实测对多只股票均为空或小整数），
  **按「不猜测」原则忽略**，不写入缓存。

代码格式：``hs_600519``（不分交易所前缀，沪深京统一 ``hs_``）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Iterable, Sequence

from .base import frame_from_records
from .source_base import MarketSource, MarketSourceError, make_meta, normalize_code

logger = logging.getLogger(__name__)

#: 日线与分时接口
LINE_URL = "https://d.10jqka.com.cn/v6/line/hs_{code}/01/last.js"
TIME_URL = "https://d.10jqka.com.cn/v6/time/hs_{code}/last.js"
#: 需要带 Referer，否则可能被拒
REFERER = "https://stockpage.10jqka.com.cn/"

#: 从 ``quotebridge_v6_line_hs_600519_01_last({...})`` 中提取 JSON 主体
_JSONP = re.compile(r"^\s*[A-Za-z_][\w.]*\s*\((.*)\)\s*;?\s*$", re.S)
#: K 线 ``data`` 字段：分号分隔的日线记录
_DATA_FIELD = re.compile(r'"data"\s*:\s*"([^"]*)"')
_NUM_FIELD = re.compile(r'"num"\s*:\s*(\d+)')

#: 日线最少字段数（日期/开/高/低/收/量/额/换手）
MIN_FIELDS = 8


def _unwrap_jsonp(text: str) -> Any:
    """把 JSONP 包装拆成 Python 对象；格式非法抛 MarketSourceError。"""
    match = _JSONP.match(text or "")
    payload = match.group(1) if match else (text or "")
    try:
        return json.loads(payload)
    except (ValueError, TypeError) as exc:
        raise MarketSourceError(f"同花顺返回格式异常（非 JSON）: {exc}") from exc


def _parse_line_payload(text: str) -> list[dict[str, Any]]:
    """解析日线 payload → 规范化记录列表（失败抛 MarketSourceError）。

    拆成纯函数便于离线单测（不依赖网络）。
    """
    data_match = _DATA_FIELD.search(text or "")
    if not data_match:
        raise MarketSourceError("同花顺日线缺少 data 字段")
    raw = data_match.group(1)
    if not raw:
        raise MarketSourceError("同花顺日线 data 为空")

    records: list[dict[str, Any]] = []
    for line in raw.split(";"):
        line = line.strip()
        if not line:
            continue
        parts = line.split(",")
        if len(parts) < MIN_FIELDS:
            continue
        try:
            day = str(parts[0]).strip()
            if len(day) != 8 or not day.isdigit():
                continue
            open_ = float(parts[1])
            high = float(parts[2])
            low = float(parts[3])
            close = float(parts[4])
            volume_shares = float(parts[5])
            amount = float(parts[6])
            turnover = float(parts[7]) if parts[7] not in ("", "-") else 0.0
        except (TypeError, ValueError):
            # 单行异常只跳过该行，不影响整批
            continue
        records.append(
            {
                "date": f"{day[0:4]}-{day[4:6]}-{day[6:8]}",
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                # 同花顺给的是「股」，统一转为「手」
                "volume": volume_shares / 100.0,
                "amount": amount,
                "turnover": turnover,
            }
        )
    if not records:
        raise MarketSourceError("同花顺日线无有效记录")
    return records


def _parse_time_payload(text: str) -> dict[str, Any]:
    """解析分时 payload → {name, preClose, date, price, pctChg}。

    分时 ``data`` 形如 ``0930,1285.15,41150503,1285.150,32020;0931,...``，
    每条为「时间,价格,成交额,均价,成交量」。取**最后一条的价格**作为最新价。
    """
    payload = _unwrap_jsonp(text)
    if not isinstance(payload, dict):
        raise MarketSourceError("同花顺分时返回非对象")
    node = next(iter(payload.values()), None)
    if not isinstance(node, dict):
        raise MarketSourceError("同花顺分时缺少数据节点")

    name = str(node.get("name") or "").strip()
    try:
        pre_close = float(node.get("pre") or 0.0)
    except (TypeError, ValueError):
        pre_close = 0.0
    day = str(node.get("date") or "").strip()
    date_text = f"{day[0:4]}-{day[4:6]}-{day[6:8]}" if len(day) == 8 and day.isdigit() else ""

    price = 0.0
    ticks = str(node.get("data") or "")
    if ticks:
        last = ticks.split(";")[-1].strip()
        fields = last.split(",")
        if len(fields) >= 2:
            try:
                price = float(fields[1])
            except (TypeError, ValueError):
                price = 0.0
    if price <= 0:
        # 无分时数据（如停牌/未开盘）时退回昨收
        price = pre_close

    pct = (price / pre_close - 1.0) if pre_close > 0 else 0.0
    return {
        "name": name,
        "preClose": pre_close,
        "date": date_text,
        "price": price,
        "pctChg": pct,
    }


class TongHuaShunSource(MarketSource):
    """同花顺公开行情接口（日线 + 分时报价）。

    定位：**可靠补充源**。它的价值在于覆盖科创板与北交所 ——
    这两个板块在东方财富被阻断、且腾讯不提供时，只能靠它或新浪。
    不提供全市场股票列表能力（``supports_stock_list = False``），
    列表由新浪/东方财富提供，本源专注于日线与报价。
    """

    name = "ths"
    supports_stock_list = False
    supports_realtime = True
    #: 指数体系与个股不同（1A0001 等），本项目不采用 —— 显式声明为「不支持」，
    #: 使其被排除在指数取数之外，而不是参与后被记一次失败（错误归因）。
    supports_index = False

    async def probe(self) -> None:
        """用一只主板股票的日线做轻量探测。"""
        await self.get_text(LINE_URL.format(code="600519"), headers={"Referer": REFERER}, timeout=min(8.0, self.timeout))

    # ------------------------------------------------------------------ 日线
    async def daily_kline(self, code: str, days: int = 250) -> Any:
        code = normalize_code(code)
        if not code:
            raise MarketSourceError("同花顺请求了非法股票代码")
        text = await self.get_text(
            LINE_URL.format(code=code), headers={"Referer": REFERER}
        )
        records = _parse_line_payload(text)
        df = frame_from_records(records)
        if df is None or df.empty:
            raise MarketSourceError(f"同花顺未返回 {code} 的日线")
        # 该接口固定返回最近约 140 根，不做本地截断（由调用方按需取尾部）
        return df

    async def daily_kline_many(self, codes: Sequence[str], days: int = 250) -> dict[str, Any]:
        """并发取多只日线；单只失败只跳过该只（基类已实现限流）。"""
        return await super().daily_kline_many(codes, days=days)

    # ------------------------------------------------------------------ 报价
    async def realtime(self, codes: Sequence[str]) -> dict[str, dict[str, Any]]:
        """分时报价：提供最新价、昨收、涨跌幅与**股票名称**。

        局限（实测）：该接口**不返回成交量/成交额/换手率**，
        因此这些字段填 0，绝不用猜测值冒充。
        """
        wanted = [normalize_code(c) for c in codes]
        wanted = [c for c in wanted if c]
        if not wanted:
            return {}
        sem = asyncio.Semaphore(self.concurrency)
        out: dict[str, dict[str, Any]] = {}
        errors: list[str] = []

        async def one(code: str) -> None:
            async with sem:
                try:
                    text = await self.get_text(
                        TIME_URL.format(code=code), headers={"Referer": REFERER}
                    )
                    parsed = _parse_time_payload(text)
                except (MarketSourceError, asyncio.TimeoutError, OSError) as exc:
                    errors.append(f"{code}: {exc}")
                    return
                out[code] = {
                    "code": code,
                    "name": parsed["name"],
                    "lastClose": parsed["price"],
                    "preClose": parsed["preClose"],
                    "pctChg": parsed["pctChg"],
                    # 该接口不提供以下字段，填 0 而非编造
                    "open": 0.0,
                    "high": 0.0,
                    "low": 0.0,
                    "volume": 0.0,
                    "amount": 0.0,
                    "turnover": 0.0,
                    "date": parsed["date"],
                }

        await asyncio.gather(*(one(c) for c in wanted))
        if not out:
            raise MarketSourceError(f"同花顺报价全部失败：{'；'.join(errors[:3])}")
        if errors:
            logger.debug("同花顺报价部分失败（%d/%d）：%s", len(errors), len(wanted), errors[:3])
        return out

    # ------------------------------------------------------------------ 指数
    async def index_snapshot(self) -> list[dict[str, Any]]:
        """指数快照。同花顺指数代码为 1A0001（上证）等，与个股不同体系。

        实测该体系与其他源不一致、且缺少 sparkline，
        因此**不实现**，由调用方转向腾讯/东方财富（它们的指数接口更可靠）。
        """
        raise MarketSourceError("同花顺不提供本项目所需的指数快照")
