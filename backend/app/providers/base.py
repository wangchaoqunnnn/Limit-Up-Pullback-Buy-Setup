"""数据源抽象基类。

统一约定：所有 provider 均为异步接口，日线返回 pandas DataFrame，列包括
``date``(datetime64) / ``open`` / ``high`` / ``low`` / ``close`` / ``pre_close`` /
``pct_chg``(小数) / ``volume``(手) / ``amount``(元) / ``turnover``(%)。
"""

from __future__ import annotations

import abc
import logging
from typing import Any, Iterable

import numpy as np
import pandas as pd

from ..models import StockMeta

logger = logging.getLogger(__name__)

KLINE_COLUMNS = [
    "date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "pct_chg",
    "volume",
    "amount",
    "turnover",
]


class DataSourceError(RuntimeError):
    """数据源不可用或返回非法数据。"""


def empty_kline() -> pd.DataFrame:
    """返回结构正确的空日线表。"""
    df = pd.DataFrame({c: pd.Series(dtype="float64") for c in KLINE_COLUMNS})
    df["date"] = pd.Series(dtype="datetime64[ns]")
    return df


def frame_from_records(records: Iterable[dict[str, Any]]) -> pd.DataFrame:
    """由记录列表构造规范日线表（自动补 pre_close / pct_chg 并裁剪非法值）。"""
    rows = list(records)
    if not rows:
        return empty_kline()
    df = pd.DataFrame(rows)
    for col in KLINE_COLUMNS:
        if col not in df.columns:
            df[col] = np.nan
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for col in ("open", "high", "low", "close", "pre_close", "volume", "amount", "turnover", "pct_chg"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
    df = df.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)
    if df.empty:
        return empty_kline()
    # 缺失的 pre_close 用前一日收盘补齐（首日回退为当日开盘）
    df["pre_close"] = df["pre_close"].fillna(df["close"].shift(1)).fillna(df["open"])
    df["pct_chg"] = df["pct_chg"].fillna(df["close"] / df["pre_close"] - 1.0)
    df["volume"] = df["volume"].fillna(0.0)
    df["amount"] = df["amount"].fillna(df["volume"] * 100.0 * df["close"])
    df["turnover"] = df["turnover"].fillna(0.0)
    return df[KLINE_COLUMNS].reset_index(drop=True)


class BaseProvider(abc.ABC):
    """行情数据源基类。"""

    #: 数据源标识（eastmoney / synthetic / cache）
    name: str = "base"

    def source_label(self) -> str:
        """响应中 dataSource 字段的取值（子类可覆写，例如命中本地缓存时上报 cache）。"""
        return self.name

    # ------------------------------------------------------------------ 基础
    async def ping(self) -> bool:
        """探测数据源是否可用，默认认为可用。"""
        return True

    async def close(self) -> None:
        """释放资源（如 HTTP 连接池）。"""
        return None

    # ------------------------------------------------------------------ 接口
    @abc.abstractmethod
    async def get_stock_list(self) -> list[StockMeta]:
        """返回全市场（或演示universe）股票列表。"""
        raise NotImplementedError

    @abc.abstractmethod
    async def get_daily_kline(self, code: str, days: int = 250) -> pd.DataFrame:
        """返回指定股票最近 days 个交易日的日线。"""
        raise NotImplementedError

    async def get_daily_kline_batch(self, codes: Iterable[str], days: int = 250) -> dict[str, pd.DataFrame]:
        """批量获取日线，默认逐只串行调用（子类可覆写为并发实现）。"""
        result: dict[str, pd.DataFrame] = {}
        for code in codes:
            try:
                result[code] = await self.get_daily_kline(code, days)
            except DataSourceError as exc:  # 单只失败不影响整体
                logger.warning("获取 %s 日线失败：%s", code, exc)
                result[code] = empty_kline()
        return result

    async def get_realtime(self, codes: Iterable[str]) -> dict[str, dict[str, Any]]:
        """返回代码 -> 最新一句话行情（默认用日线末行推导）。"""
        out: dict[str, dict[str, Any]] = {}
        for code in codes:
            df = await self.get_daily_kline(code, days=30)
            if df is None or df.empty:
                continue
            row = df.iloc[-1]
            out[code] = {
                "code": code,
                "lastClose": float(row["close"]),
                "preClose": float(row["pre_close"]),
                "pctChg": float(row["pct_chg"]),
                "turnover": float(row["turnover"]),
                "amount": float(row["amount"]),
                "volume": float(row["volume"]),
                "date": pd.Timestamp(row["date"]).strftime("%Y-%m-%d"),
            }
        return out

    async def get_index_snapshot(self) -> list[dict[str, Any]]:
        """返回主要指数快照（默认空列表，由子类实现）。"""
        return []

    async def get_trade_date(self) -> str:
        """返回最新交易日（默认取全市场日线中最新的日期）。"""
        metas = await self.get_stock_list()
        if not metas:
            return ""
        df = await self.get_daily_kline(metas[0].code, days=5)
        if df is None or df.empty:
            return ""
        return pd.Timestamp(df["date"].iloc[-1]).strftime("%Y-%m-%d")
