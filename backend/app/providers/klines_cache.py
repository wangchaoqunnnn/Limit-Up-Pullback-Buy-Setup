"""日线增量缓存（SQLite）。

为什么需要它：
- 真实全市场扫描（数千只 × 250 交易日）如果每次都向数据源全量拉取，
  仅受限于上游每只 0.1~0.2 秒的响应，首轮就要几十秒，之后每次刷新同样昂贵。
- 采用「首轮全量落库 + 之后只补增量」策略后，**首次**扫描完成后，
  后续每次刷新只需为每只股票补最近若干根 K 线（通常 0~2 根），
  开盘期间的 30 秒轮询才真正可行。

存储设计：
- 单表 ``bars``，主键 ``(code, date)``，date 存 ``YYYY-MM-DD`` 文本（可直接按字典序比较）；
- 数值列存 REAL，volume/amount 存 REAL（成交量单位为「手」，与契约一致）；
- 写入使用事务批量提交，避免逐行 fsync 拖慢；
- 连接以 ``check_same_thread=False`` 创建并配互斥锁，供线程池中的回测/扫描复用。

注意：该模块**不判断数据新鲜度**，只负责存取；新鲜度判断放在 ``ResilientProvider``。
"""

from __future__ import annotations

import logging
import math
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from .base import KLINE_COLUMNS, empty_kline

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
    code       TEXT NOT NULL,
    date       TEXT NOT NULL,
    open       REAL,
    high       REAL,
    low        REAL,
    close      REAL,
    pre_close  REAL,
    pct_chg    REAL,
    volume     REAL,
    amount     REAL,
    turnover   REAL,
    PRIMARY KEY (code, date)
);
CREATE INDEX IF NOT EXISTS idx_bars_code_date ON bars (code, date);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class KlineCache:
    """日线增量缓存（SQLite 实现）。"""

    def __init__(self, path: Path, max_days: int = 400) -> None:
        self.path = Path(path)
        self.max_days = int(max_days)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        # 每只股票最近一次写入时间（内存态，进程重启后丢失；
        # 丢失只会让首轮刷新多做一次增量抓取，不影响正确性）
        self._write_at: dict[str, float] = {}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connect()

    # ------------------------------------------------------------------ 连接
    def _connect(self) -> None:
        """建立连接并初始化表结构（幂等）。"""
        with self._lock:
            if self._conn is not None:
                return
            conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30.0)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(_SCHEMA)
            conn.commit()
            self._conn = conn
            logger.info("日线缓存就绪：%s", self.path.name)

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._connect()
        assert self._conn is not None
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None

    # ------------------------------------------------------------------ 读
    def latest_date(self, codes: Iterable[str] | None = None) -> str | None:
        """缓存中最新的一根 K 线日期。"""
        with self._lock:
            try:
                if codes is None:
                    row = self.conn.execute("SELECT MAX(date) FROM bars").fetchone()
                else:
                    ids = list(codes)
                    if not ids:
                        return None
                    marks = ",".join("?" * len(ids))
                    row = self.conn.execute(
                        f"SELECT MAX(date) FROM bars WHERE code IN ({marks})", ids
                    ).fetchone()
                return str(row[0]) if row and row[0] else None
            except sqlite3.Error as exc:
                logger.warning("读取缓存最新日期失败：%s", exc)
                return None

    def latest_dates(self, codes: Iterable[str]) -> dict[str, str]:
        """批量返回每只股票在缓存中的最新日期。"""
        ids = [str(c) for c in codes]
        if not ids:
            return {}
        out: dict[str, str] = {}
        with self._lock:
            try:
                marks = ",".join("?" * len(ids))
                rows = self.conn.execute(
                    f"SELECT code, MAX(date) FROM bars WHERE code IN ({marks}) GROUP BY code", ids
                ).fetchall()
                out = {str(r[0]): str(r[1]) for r in rows if r[1]}
            except sqlite3.Error as exc:
                logger.warning("批量读取缓存日期失败：%s", exc)
        return out

    def count_codes(self) -> int:
        with self._lock:
            try:
                row = self.conn.execute("SELECT COUNT(DISTINCT code) FROM bars").fetchone()
                return int(row[0]) if row else 0
            except sqlite3.Error:
                return 0

    def all_codes(self) -> list[str]:
        """返回缓存中所有已有日线的股票代码。

        用途：当上游列表源临时不可用（例如新浪被反爬限流）导致拿不到完整股票列表时，
        可用缓存里已有的代码补全股票池，避免「明明有数据却因为列表不全而漏掉整块板块」。
        """
        with self._lock:
            try:
                rows = self.conn.execute("SELECT DISTINCT code FROM bars ORDER BY code").fetchall()
                return [str(r[0]) for r in rows if r[0]]
            except sqlite3.Error as exc:
                logger.warning("读取缓存股票代码失败：%s", exc)
                return []

    def last_write_age(self, code: str) -> float | None:
        """某只股票距离上次写入缓存过去了多少秒。

        用于判断「这份缓存是否已经过期、需要向数据源增量补齐」。

        进程内写入时间优先；进程重启后内存表为空，此时**回退到数据库文件的
        修改时间**作为近似。这一点很关键：如果重启后直接返回 None，
        调用方会认为「从未写过缓存」，从而对所有股票做一次全量重拉
        （1000 只 ≈ 2 分钟且极易触发上游限流），而实际上数据就在库里。
        """
        stamp = self._write_at.get(str(code))
        if stamp is not None:
            return max(0.0, time.time() - stamp)
        try:
            return max(0.0, time.time() - self.path.stat().st_mtime)
        except OSError:
            return None

    def get(self, code: str, days: int) -> pd.DataFrame:
        """读取某只股票最近 days 根日线（不足则返回全部）。"""
        code = str(code)
        with self._lock:
            try:
                rows = self.conn.execute(
                    "SELECT date, open, high, low, close, pre_close, pct_chg, volume, amount, turnover "
                    "FROM bars WHERE code = ? ORDER BY date DESC LIMIT ?",
                    (code, int(max(1, days))),
                ).fetchall()
            except sqlite3.Error as exc:
                logger.warning("读取 %s 缓存失败：%s", code, exc)
                return empty_kline()
        if not rows:
            return empty_kline()
        records = [dict(zip(KLINE_COLUMNS, r)) for r in reversed(rows)]
        df = pd.DataFrame(records)
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        for col in KLINE_COLUMNS[1:]:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
        return df.dropna(subset=["date"]).reset_index(drop=True)[KLINE_COLUMNS]

    def get_many(self, codes: Iterable[str], days: int, max_bars: int = 600) -> dict[str, pd.DataFrame]:
        """批量读取；单只无数据时不出现在结果中。

        性能要点（实测踩过的坑）：
        - 逐只 ``SELECT`` 会产生 1000 次往返与 1000 次 DataFrame 构造，实测约 17 秒；
        - 若用 ``ROW_NUMBER() OVER (PARTITION BY code ORDER BY date DESC)`` 做每只取
          「最近 N 根」，因为索引 ``(code, date)`` 是**升序**，SQLite 需要建临时
          B 树对 25 万行倒序排序，实测约 3.5 秒，属于纯浪费。
        因此这里**按索引顺序直接读**（``WHERE code IN (...) ORDER BY code, date``
        恰好与索引同序，无需排序），每只只取最近 ``max_bars`` 根，
        再把精确的 ``days`` 截断留给调用方在内存里完成。

        ``max_bars`` 是内存保护上限：策略最长只需 250 根，留到 600 足够覆盖
        回测与长周期判据，同时避免把每只的全部历史都读进内存。
        """
        ids = [str(c) for c in codes]
        if not ids:
            return {}
        cap = int(max(1, max_bars))
        out: dict[str, pd.DataFrame] = {}

        # 关键性能点：pandas 的类型转换有固定开销（每次约 10~15ms），
        # 若对每只股票逐个转换要花 10~15 秒；但若把 137 万行一次性拼起来再转换，
        # 峰值内存会翻倍（实测约 280MB 全量中间表 + 130MB 结果），
        # 在 1~2GB 的小内存云主机上正是 OOM 的触发点。
        # 因此按「分片读取 → 分片转换 → 分片切分 → 立即释放」处理：
        # 峰值只与单个分片有关，总耗时不变（切分与切片的开销在分片内完成）。
        chunk_size = 500
        for start in range(0, len(ids), chunk_size):
            batch = ids[start : start + chunk_size]
            marks = ",".join("?" * len(batch))
            with self._lock:
                try:
                    rows = self.conn.execute(
                        "SELECT code, date, open, high, low, close, pre_close, pct_chg, volume, amount, turnover "
                        f"FROM bars WHERE code IN ({marks}) ORDER BY code, date",
                        batch,
                    ).fetchall()
                except sqlite3.Error as exc:
                    logger.warning("批量读取缓存失败：%s", exc)
                    return out
            if not rows:
                continue
            chunk = pd.DataFrame(rows, columns=["code", *KLINE_COLUMNS])
            del rows
            # 日期在库里固定是 YYYY-MM-DD 文本：**显式给出 format 极重要**，
            # 让 pandas 走快路径解析；不指定时会先尝试推断格式，差数秒。
            chunk["date"] = pd.to_datetime(chunk["date"], format="%Y-%m-%d", errors="coerce")
            # 只对「还不是 float64」的列做转换：SQLite 的 REAL 列经 DataFrame 构造后
            # 通常已是 float64，仅含 NULL 的列才退化成 object。
            for col in KLINE_COLUMNS[1:]:
                if chunk[col].dtype != "float64":
                    chunk[col] = pd.to_numeric(chunk[col], errors="coerce").astype("float64")
            chunk = chunk.dropna(subset=["date"])
            if chunk.empty:
                del chunk
                continue
            # 不再额外 sort_values：SQL 已是 ORDER BY code, date，与索引同序，
            # pandas 的 groupby 保持组内出现顺序，group.tail(cap) 即「最近 cap 根」。
            for code, group in chunk.groupby("code", sort=False):
                tail = group.tail(cap) if len(group) > cap else group
                df = tail[KLINE_COLUMNS].reset_index(drop=True)
                if not df.empty:
                    out[str(code)] = df
            del chunk
        return out

    # ------------------------------------------------------------------ 写
    @staticmethod
    def _upsert_sql() -> str:
        placeholders = ",".join("?" * (len(KLINE_COLUMNS) + 1))
        return (
            f"INSERT INTO bars (code, {', '.join(KLINE_COLUMNS)}) VALUES ({placeholders}) "
            f"ON CONFLICT(code, date) DO UPDATE SET "
            + ", ".join(f"{c}=excluded.{c}" for c in KLINE_COLUMNS[1:])
        )

    @staticmethod
    def _rows_for(code: str, df: pd.DataFrame) -> list[tuple[Any, ...]]:
        """把一只股票的日线整理成待写入的行（无效行直接丢弃）。"""
        if df is None or df.empty:
            return []
        code = str(code)
        rows: list[tuple[Any, ...]] = []
        for record in df.itertuples(index=False):
            try:
                date_value = pd.Timestamp(getattr(record, "date")).strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                continue
            values = [code, date_value]
            ok = True
            for col in KLINE_COLUMNS[1:]:
                raw = getattr(record, col, None)
                try:
                    num = float(raw)  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    num = None
                # NaN（例如源数据里该字段为 None 被 pandas 转成 float64）同样视为缺失。
                # 收盘价是策略计算的基础，必须要求有效值，否则丢弃整行。
                if num is not None and not math.isfinite(num):
                    num = None
                if col == "close" and num is None:
                    ok = False
                    break
                values.append(num)
            if ok:
                rows.append(tuple(values))
        return rows

    def upsert(self, code: str, df: pd.DataFrame) -> int:
        """写入/更新某只股票的日线，返回写入行数。

        以 ``(code, date)`` 为主键做 UPSERT：已存在的日期会被最新数据覆盖
        （当日盘中反复刷新时，最后一根 K 线会不断被更新为最新价）。

        批量场景请用 ``upsert_many``：逐只调用会产生**每只一次事务提交**，
        全市场下开销极大。
        """
        rows = self._rows_for(code, df)
        if not rows:
            return 0
        code = str(code)
        with self._lock:
            try:
                self.conn.executemany(self._upsert_sql(), rows)
                self.conn.commit()
            except sqlite3.Error as exc:
                logger.warning("写入 %s 缓存失败：%s", code, exc)
                return 0
        self._write_at[code] = time.time()
        return len(rows)

    def upsert_many(self, frames: dict[str, pd.DataFrame]) -> tuple[int, int]:
        """批量写入，返回 (写入股票数, 写入行数)。

        **性能要点（实测踩过的大坑）**：早期实现是逐只调用 ``upsert``，
        而 ``upsert`` 每次都会 ``commit()`` —— 全市场 5561 只就是 5561 次事务提交，
        每次都带 fsync，在云服务器磁盘上极其昂贵。改为**整批一个事务**提交，
        并按行数分块（避免一次构造百万级参数列表占满内存）。
        """
        if not frames:
            return (0, 0)

        sql = self._upsert_sql()
        total_codes = 0
        total_rows = 0
        batch: list[tuple[Any, ...]] = []
        batch_codes: set[str] = set()
        # 单块上限：约 20 万行，兼顾内存与事务效率
        chunk_rows = 200_000

        def flush() -> None:
            nonlocal batch, batch_codes, total_codes, total_rows
            if not batch:
                return
            rows_in_batch = batch
            codes_in_batch = batch_codes
            batch = []
            batch_codes = set()
            with self._lock:
                try:
                    self.conn.executemany(sql, rows_in_batch)
                    self.conn.commit()
                except sqlite3.Error as exc:
                    logger.warning("批量写入缓存失败：%s", exc)
                    return
            stamp = time.time()
            for code in codes_in_batch:
                self._write_at[code] = stamp
            total_codes += len(codes_in_batch)
            total_rows += len(rows_in_batch)

        for code, df in frames.items():
            rows = self._rows_for(code, df)
            if not rows:
                continue
            batch.extend(rows)
            batch_codes.add(str(code))
            if len(batch) >= chunk_rows:
                flush()
        flush()
        return (total_codes, total_rows)

    def invalidate(self, code: str | None = None) -> None:
        """让指定股票（或全部）的缓存被认为已过期，下次强制重新拉取。"""
        with self._lock:
            if code is None:
                self._write_at.clear()
            else:
                self._write_at.pop(str(code), None)

    def prune(self, keep_days: int | None = None) -> int:
        """裁剪过老的数据，控制库体积（每只股票保留最近 keep_days 根）。"""
        keep = int(keep_days or self.max_days)
        with self._lock:
            try:
                cur = self.conn.execute(
                    "DELETE FROM bars WHERE rowid IN ("
                    "  SELECT rowid FROM ("
                    "    SELECT rowid, ROW_NUMBER() OVER (PARTITION BY code ORDER BY date DESC) AS rn FROM bars"
                    "  ) WHERE rn > ?"
                    ")",
                    (keep,),
                )
                self.conn.commit()
                return int(cur.rowcount or 0)
            except sqlite3.Error as exc:
                logger.warning("缓存裁剪失败：%s", exc)
                return 0

    def clear(self) -> None:
        with self._lock:
            try:
                self.conn.execute("DELETE FROM bars")
                self.conn.commit()
            except sqlite3.Error as exc:
                logger.warning("清空缓存失败：%s", exc)

    def stats(self) -> dict[str, Any]:
        """缓存概览（供 /api/v1/settings 展示）。"""
        with self._lock:
            try:
                codes = self.count_codes()
                bars = self.conn.execute("SELECT COUNT(*) FROM bars").fetchone()
                latest = self.conn.execute("SELECT MAX(date) FROM bars").fetchone()
                size = self.path.stat().st_size if self.path.exists() else 0
                return {
                    "enabled": True,
                    "engine": "sqlite",
                    "file": self.path.name,
                    "codes": int(codes),
                    "bars": int(bars[0]) if bars else 0,
                    "latestDate": str(latest[0]) if latest and latest[0] else None,
                    "bytes": int(size),
                }
            except (sqlite3.Error, OSError):
                return {"enabled": True, "engine": "sqlite", "codes": 0, "bars": 0}
