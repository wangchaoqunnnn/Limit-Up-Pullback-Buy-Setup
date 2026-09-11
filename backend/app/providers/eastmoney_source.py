"""东方财富公开行情接口适配器（多源体系中的一员）。

定位：
- 东方财富的全市场股票列表接口（``push2.eastmoney.com``）字段最全，
  是**首选的股票池来源**（一次请求可拿到代码/名称/成交额），
  因此在 ``DATA_SOURCE_ORDER`` 中默认排第一。
- 但该域名在部分网络环境（企业出口、部分云机房、本机安全软件）会被
  在 TLS 层直接阻断（表现为 ``Server disconnected without sending a response``）。
  因此本适配器**必须快速失败**：连接超时压到 4 秒以内，
  让 ``ResilientProvider`` 能立刻切换到腾讯/新浪，而不是让用户干等。

与 ``eastmoney.py`` 的区别：
``eastmoney.py`` 是旧版 ``BaseProvider`` 实现（绑定旧的全有或全无降级逻辑），
本文件是面向新多源体系（``MarketSource``）的适配器，二者共用同一套接口地址。
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

from .base import frame_from_records
from .source_base import (
    MarketSource,
    MarketSourceError,
    chunked,
    eastmoney_secid,
    make_meta,
)

logger = logging.getLogger(__name__)

# 行情快照与全市场列表
QUOTE_BASE = "https://push2.eastmoney.com/api/qt"
# 历史 K 线
KLINE_BASE = "https://push2his.eastmoney.com/api/qt/stock/kline/get"

# 沪深京全部 A 股（剔除退市与 B 股）
MARKET_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"

# 全市场列表需要的字段：代码/名称/最新价/涨跌幅/成交额/换手率/总市值
LIST_FIELDS = "f12,f14,f2,f3,f6,f8,f20,f13"

INDEX_CODES = [
    ("000001", "上证指数"),
    ("399001", "深证成指"),
    ("399006", "创业板指"),
]


class EastMoneySource(MarketSource):
    """东方财富公开行情接口。"""

    name = "eastmoney"
    supports_stock_list = True
    supports_realtime = True

    async def probe(self) -> None:
        """用一个极小的请求探测可用性（快速失败）。"""
        await self.get_json(
            f"{QUOTE_BASE}/ulist.np/get",
            params={"fltt": 2, "secids": "1.000001", "fields": "f12,f14,f2", "ut": "fa5fd1943c7b386f172d6893dbfba10b"},
            timeout=min(4.0, self.timeout),
        )

    # ------------------------------------------------------------------ 列表
    async def list_stocks(self) -> list[Any]:
        """拉取沪深京全部 A 股列表，按成交额降序返回（流动性优先）。"""
        metas: list[Any] = []
        page = 1
        page_size = 2000
        while page <= 10:
            data = await self.get_json(
                f"{QUOTE_BASE}/clist/get",
                params={
                    "pn": page,
                    "pz": page_size,
                    "po": 1,  # 按 f6（成交额）降序
                    "np": 1,
                    "fltt": 2,
                    "invt": 2,
                    "fid": "f6",
                    "fs": MARKET_FS,
                    "fields": LIST_FIELDS,
                    "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                },
            )
            diff = ((data or {}).get("data") or {}).get("diff") or []
            if not diff:
                break
            for item in diff:
                code = str(item.get("f12") or "").strip()
                name = str(item.get("f14") or "").strip()
                if not code or not name:
                    continue
                # 过滤退市/未上市标记
                if "退" in name:
                    continue
                metas.append(make_meta(code, name))
            total = int(((data or {}).get("data") or {}).get("total") or 0)
            if len(diff) < page_size or len(metas) >= total:
                break
            page += 1
        if not metas:
            raise MarketSourceError("eastmoney 未返回任何股票")
        logger.info("eastmoney 股票列表：%d 只", len(metas))
        return metas

    # ------------------------------------------------------------------ 日线
    async def daily_kline(self, code: str, days: int = 250) -> Any:
        data = await self.get_json(
            KLINE_BASE,
            params={
                "secid": eastmoney_secid(code),
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                "klt": 101,  # 日线
                "fqt": 1,  # 前复权
                "end": "20500101",
                "lmt": int(days),
            },
        )
        klines = ((data or {}).get("data") or {}).get("klines") or []
        records: list[dict[str, Any]] = []
        for line in klines:
            parts = str(line).split(",")
            if len(parts) < 11:
                continue
            try:
                records.append(
                    {
                        "date": parts[0],
                        "open": float(parts[1]),
                        "close": float(parts[2]),
                        "high": float(parts[3]),
                        "low": float(parts[4]),
                        "volume": float(parts[5]),  # 手
                        "amount": float(parts[6]),  # 元
                        "turnover": float(parts[10]),  # %
                    }
                )
            except (TypeError, ValueError):
                continue
        if not records:
            raise MarketSourceError(f"eastmoney 未返回 {code} 的日线")
        return frame_from_records(records)

    # ------------------------------------------------------------------ 实时
    async def realtime(self, codes: Sequence[str]) -> dict[str, dict[str, Any]]:
        """批量实时快照（东方财富单次可带较多 secid）。"""
        out: dict[str, dict[str, Any]] = {}
        for group in chunked([str(c) for c in codes], 80):
            secids = ",".join(eastmoney_secid(c) for c in group)
            data = await self.get_json(
                f"{QUOTE_BASE}/ulist.np/get",
                params={
                    "fltt": 2,
                    "invt": 2,
                    "secids": secids,
                    "fields": "f12,f14,f2,f3,f4,f5,f6,f8,f15,f16,f17,f18",
                    "ut": "fa5fd1943c7b386f172d6893dbfba10b",
                },
            )
            diff = ((data or {}).get("data") or {}).get("diff") or []
            for item in diff:
                code = str(item.get("f12") or "")
                if not code:
                    continue
                out[code] = {
                    "code": code,
                    "name": str(item.get("f14") or "").strip(),
                    "lastClose": _num(item.get("f2")),
                    "pctChg": _pct(item.get("f3")),
                    "preClose": _num(item.get("f18")),
                    "open": _num(item.get("f17")),
                    "high": _num(item.get("f15")),
                    "low": _num(item.get("f16")),
                    "volume": _num(item.get("f5")),
                    "amount": _num(item.get("f6")),
                    "turnover": _num(item.get("f8")),
                }
        if not out:
            raise MarketSourceError("eastmoney 未返回实时行情")
        return out

    # ------------------------------------------------------------------ 指数
    async def index_snapshot(self) -> list[dict[str, Any]]:
        secids = ",".join(eastmoney_secid(c) for c, _ in INDEX_CODES)
        data = await self.get_json(
            f"{QUOTE_BASE}/ulist.np/get",
            params={"fltt": 2, "secids": secids, "fields": "f12,f14,f2,f3", "ut": "fa5fd1943c7b386f172d6893dbfba10b"},
        )
        diff = ((data or {}).get("data") or {}).get("diff") or []
        name_map = {c: n for c, n in INDEX_CODES}
        out: list[dict[str, Any]] = []
        for item in diff:
            code = str(item.get("f12") or "")
            out.append(
                {
                    "code": code,
                    "name": name_map.get(code, str(item.get("f14") or code)),
                    "close": _num(item.get("f2")),
                    "pctChg": _pct(item.get("f3")),
                    "sparkline": [],
                }
            )
        if not out:
            raise MarketSourceError("eastmoney 未返回指数快照")
        return out


def _num(value: Any) -> float:
    """东方财富用 '-' 表示缺失。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _pct(value: Any) -> float:
    """东方财富 f3 已是以 % 为单位的数值，转为小数。"""
    try:
        return float(value) / 100.0
    except (TypeError, ValueError):
        return 0.0
