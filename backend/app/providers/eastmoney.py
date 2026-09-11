"""东方财富公开行情接口数据源。

仅使用公开的 JSON 接口（push2 / push2his），不依赖 akshare。
所有网络异常统一转换为 ``DataSourceError``，绝不因网络问题抛 500。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Iterable

import httpx
import pandas as pd

from ..config import get_settings
from ..models import StockMeta
from ..utils import limit_pct_of
from .base import BaseProvider, DataSourceError, empty_kline, frame_from_records
from .cache import FileCache

logger = logging.getLogger(__name__)

PUSH2_BASE = "https://push2.eastmoney.com"
PUSH2HIS_BASE = "https://push2his.eastmoney.com"

# 沪深 A 股（含主板/创业板/科创板）筛选串
FS_ALL_A = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
LIST_FIELDS = "f12,f13,f14,f100,f2,f3"
KLINE_FIELDS2 = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

# 指数：上证指数 / 深证成指 / 创业板指
INDEX_LIST = [
    ("1.000001", "000001", "上证指数"),
    ("0.399001", "399001", "深证成指"),
    ("0.399006", "399006", "创业板指"),
]


def _board_of(code: str) -> str:
    """按代码段判断上市板块。"""
    if code.startswith("688"):
        return "科创板"
    if code.startswith("300") or code.startswith("301"):
        return "创业板"
    if code.startswith(("8", "4")):
        return "北交所"
    return "主板"


def _secid(code: str) -> str:
    """构造东财 secid：沪市前缀 1，深市/北交所前缀 0。"""
    return f"{'1' if code.startswith(('6', '9')) else '0'}.{code}"


class EastMoneyProvider(BaseProvider):
    """东方财富数据源。"""

    name = "eastmoney"

    def __init__(self, cache: FileCache | None = None) -> None:
        settings = get_settings()
        self.timeout = float(settings.http_timeout)
        self.cache = cache or FileCache(settings.cache_path, settings.cache_ttl_seconds)
        self.ttl = int(settings.cache_ttl_seconds)
        self._client: httpx.AsyncClient | None = None
        # 最近一次数据请求是否直接命中本地文件缓存
        self._last_from_cache = False

    def source_label(self) -> str:
        """命中本地文件缓存时上报 cache（内容仍为真实行情）。"""
        return "cache" if self._last_from_cache else "eastmoney"

    # ------------------------------------------------------------------ 基础设施
    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=min(5.0, self.timeout)),
                headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*"},
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def _fetch_json(self, url: str, params: dict[str, Any], retries: int = 1) -> dict[str, Any]:
        """请求 JSON，异常统一抛 DataSourceError。"""
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                client = await self._get_client()
                resp = await client.get(url, params=params)
                if resp.status_code != 200:
                    raise DataSourceError(f"东方财富接口返回 HTTP {resp.status_code}")
                payload = resp.json()
                if not isinstance(payload, dict):
                    raise DataSourceError("东方财富接口返回格式异常")
                return payload
            except (httpx.HTTPError, ValueError, DataSourceError) as exc:
                last_error = exc
                if attempt < retries:
                    await asyncio.sleep(0.3 * (attempt + 1))
                    continue
        raise DataSourceError(f"东方财富接口请求失败：{last_error}")

    async def ping(self) -> bool:
        """轻量探测：能否取到股票列表（结果写入缓存）。"""
        try:
            data = await self._fetch_json(
                f"{PUSH2_BASE}/api/qt/clist/get",
                {
                    "pn": 1,
                    "pz": 5,
                    "po": 1,
                    "np": 1,
                    "fltt": 2,
                    "invt": 2,
                    "fid": "f3",
                    "fs": FS_ALL_A,
                    "fields": LIST_FIELDS,
                },
            )
        except DataSourceError as exc:
            logger.warning("东方财富连通性探测失败：%s", exc)
            return False
        diff = (data or {}).get("data") or {}
        return bool(diff.get("diff"))

    # ------------------------------------------------------------------ 股票列表
    async def get_stock_list(self) -> list[StockMeta]:
        cache_key = "eastmoney:stock_list"
        cached = self.cache.get(cache_key)
        if cached:
            self._last_from_cache = True
            return [StockMeta(**item) for item in cached]
        self._last_from_cache = False
        metas: list[StockMeta] = []
        page = 1
        page_size = 200
        max_pages = 40
        while page <= max_pages:
            payload = await self._fetch_json(
                f"{PUSH2_BASE}/api/qt/clist/get",
                {
                    "pn": page,
                    "pz": page_size,
                    "po": 0,
                    "np": 1,
                    "fltt": 2,
                    "invt": 2,
                    "fid": "f12",
                    "fs": FS_ALL_A,
                    "fields": LIST_FIELDS,
                },
            )
            diff = ((payload or {}).get("data") or {}).get("diff") or []
            if not diff:
                break
            for row in diff:
                code = str(row.get("f12") or "").strip()
                name = str(row.get("f14") or "").strip()
                if len(code) != 6 or not code.isdigit() or not name:
                    continue
                board = _board_of(code)
                is_st = "ST" in name.upper()
                metas.append(
                    StockMeta(
                        code=code,
                        name=name,
                        market="SH" if code.startswith(("6", "9")) else ("BJ" if code.startswith(("4", "8")) else "SZ"),
                        board=board,
                        industry=str(row.get("f100") or "其他").strip() or "其他",
                        isSt=is_st,
                        limitPct=limit_pct_of(board, is_st),
                    )
                )
            if len(diff) < page_size:
                break
            page += 1
        if not metas:
            raise DataSourceError("东方财富未返回任何股票列表")
        self.cache.set(cache_key, [m.model_dump() for m in metas])
        logger.info("东方财富股票列表加载完成，共 %d 只", len(metas))
        return metas

    # ------------------------------------------------------------------ 日线
    async def get_daily_kline(self, code: str, days: int = 250) -> pd.DataFrame:
        if not code:
            raise DataSourceError("股票代码不能为空")
        cache_key = f"eastmoney:kline:{code}:{days}"
        cached = self.cache.get(cache_key)
        if cached:
            self._last_from_cache = True
            return _records_to_frame(cached)
        self._last_from_cache = False
        payload = await self._fetch_json(
            f"{PUSH2HIS_BASE}/api/qt/stock/kline/get",
            {
                "secid": _secid(code),
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": KLINE_FIELDS2,
                "klt": 101,
                "fqt": 1,
                "beg": 0,
                "end": 20500101,
                "lmt": int(days),
            },
        )
        data = (payload or {}).get("data") or {}
        klines = data.get("klines") or []
        if not klines:
            raise DataSourceError(f"未获取到 {code} 的日线数据")
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
                        "volume": float(parts[5]),
                        "amount": float(parts[6]),
                        "pct_chg": float(parts[8]) / 100.0,
                        "turnover": float(parts[10]),
                    }
                )
            except (TypeError, ValueError):
                continue
        if not records:
            raise DataSourceError(f"{code} 日线数据解析失败")
        self.cache.set(cache_key, records)
        return _records_to_frame(records)

    async def get_daily_kline_batch(self, codes: Iterable[str], days: int = 250) -> dict[str, pd.DataFrame]:
        """串行 + 限速获取，避免被限流。"""
        result: dict[str, pd.DataFrame] = {}
        for code in codes:
            try:
                result[code] = await self.get_daily_kline(code, days)
            except DataSourceError as exc:
                logger.warning("获取 %s 日线失败：%s", code, exc)
                result[code] = empty_kline()
            await asyncio.sleep(0.02)
        return result

    # ------------------------------------------------------------------ 快照
    async def get_realtime(self, codes: Iterable[str]) -> dict[str, dict[str, Any]]:
        code_list = [c for c in codes if c]
        if not code_list:
            return {}
        fields = "f12,f14,f2,f3,f8,f6,f18"
        out: dict[str, dict[str, Any]] = {}
        try:
            for i in range(0, len(code_list), 50):
                chunk = code_list[i : i + 50]
                payload = await self._fetch_json(
                    f"{PUSH2_BASE}/api/qt/ulist.np/get",
                    {"secids": ",".join(_secid(c) for c in chunk), "fields": fields, "fltt": 2, "invt": 2},
                )
                diff = ((payload or {}).get("data") or {}).get("diff") or []
                if isinstance(diff, dict):
                    diff = list(diff.values())
                for row in diff:
                    code = str(row.get("f12") or "").strip()
                    if not code:
                        continue
                    out[code] = {
                        "code": code,
                        "name": str(row.get("f14") or ""),
                        "lastClose": _num(row.get("f2")),
                        "pctChg": _num(row.get("f3")) / 100.0,
                        "turnover": _num(row.get("f8")),
                        "amount": _num(row.get("f6")),
                        "preClose": _num(row.get("f18")),
                        "date": "",
                    }
        except DataSourceError as exc:
            logger.warning("实时快照获取失败，回退日线推导：%s", exc)
            return await super().get_realtime(code_list)
        missing = [c for c in code_list if c not in out]
        if missing:
            out.update(await super().get_realtime(missing))
        return out

    async def get_index_snapshot(self) -> list[dict[str, Any]]:
        cache_key = "eastmoney:index_snapshot"
        cached = self.cache.get(cache_key, ttl=min(self.ttl, 120))
        if cached:
            return cached
        result: list[dict[str, Any]] = []
        for secid, code, name in INDEX_LIST:
            try:
                payload = await self._fetch_json(
                    f"{PUSH2_BASE}/api/qt/stock/get",
                    {"secid": secid, "fields": "f43,f170,f58,f169", "fltt": 2, "invt": 2},
                )
                data = (payload or {}).get("data") or {}
                close = _num(data.get("f43"))
                pct = _num(data.get("f170")) / 100.0
                kline = await self.get_daily_kline(code, days=25)
                spark = [round(float(x), 2) for x in kline["close"].tail(20).tolist()] if not kline.empty else []
                result.append({"code": code, "name": name, "close": close, "pctChg": pct, "sparkline": spark})
            except DataSourceError as exc:
                logger.warning("指数 %s 快照获取失败：%s", name, exc)
        if result:
            self.cache.set(cache_key, result)
        return result


def _num(value: Any) -> float:
    """东财接口常以 '-' 表示缺失。"""
    try:
        if value in (None, "-", ""):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _records_to_frame(records: list[dict[str, Any]]) -> pd.DataFrame:
    if not records:
        return empty_kline()
    return frame_from_records(records)
