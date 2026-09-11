"""行业分类补充（用于「信号五：板块情绪同步回暖」）。

问题背景：
腾讯排行榜接口（我们取股票池的来源）**不返回行业字段**，如果不管，
``StockMeta.industry`` 会全部是「未分类」，导致 ``build_sector_context`` 把
1000 只股票塞进同一个板块 —— 板块情绪分失真、行业热度榜无意义、
信号五也失去区分度（实测正是如此）。

方案：
腾讯有板块接口，可以据此还原真实行业分类：
1. ``board_type=hy`` 拉取行业板块清单（约 100 个行业，如「食品饮料」「电力设备」）；
2. 逐个行业用 ``board_code=<板块号>`` 拉成分股前 N 只（按成交额降序）；
3. 汇总为 ``{股票代码: 行业名}`` 映射，落盘缓存（默认 24 小时有效）。

设计取舍：
- **纯增强、可失败**：任何一步出错都只是「行业仍为未分类」，绝不阻断主流程；
- 成分股按成交额降序取前 N 只，配合我们的股票池（同样是成交额靠前的股票），
  两者高度重合，因此少量请求即可覆盖绝大多数标的；
- 映射落盘，避免每次启动都重新拉上百个行业。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

RANK_BASE = "https://proxy.finance.qq.com/cgi/cgi-bin/rank"
#: 行业板块类型（腾讯的行业分类）
BOARD_TYPE = "hy"
#: 单个行业最多取多少只成分股（按成交额降序）
MEMBERS_PER_BOARD = 100
#: 板块清单请求上限
MAX_BOARDS = 200
#: 拉取行业列表/成分的并发（腾讯该接口较宽松，但不宜过高）
FETCH_CONCURRENCY = 8
#: 映射缓存有效期（秒）
CACHE_TTL = 86400


def _normalize(code: str) -> str:
    """把 ``sh600519`` 转成内部 6 位代码。"""
    text = str(code or "").strip().upper()
    for prefix in ("SH", "SZ", "BJ"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits.zfill(6) if digits else ""


class IndustryEnricher:
    """从腾讯板块接口还原「股票 → 行业」映射，带磁盘缓存。"""

    def __init__(self, cache_file: Path, timeout: float = 20.0) -> None:
        self.cache_file = Path(cache_file)
        self.timeout = float(timeout)
        self._mapping: dict[str, str] = {}
        self._loaded_at: float = 0.0
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ 缓存
    def load_cache(self) -> dict[str, str] | None:
        """读取磁盘映射；过期或损坏返回 None。"""
        if not self.cache_file.exists():
            return None
        try:
            raw = json.loads(self.cache_file.read_text(encoding="utf-8"))
            if (time.time() - float(raw.get("savedAt", 0))) > CACHE_TTL:
                return None
            mapping = raw.get("mapping") or {}
            if not isinstance(mapping, dict) or not mapping:
                return None
            return {str(k): str(v) for k, v in mapping.items()}
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("行业映射缓存读取失败：%s", exc)
            return None

    def save_cache(self, mapping: dict[str, str]) -> None:
        payload = {"savedAt": time.time(), "count": len(mapping), "mapping": mapping}
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self.cache_file.parent), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            os.replace(tmp, self.cache_file)
        except (OSError, TypeError) as exc:
            logger.warning("行业映射缓存写入失败：%s", exc)

    # ------------------------------------------------------------------ 抓取
    async def _fetch_boards(self, client: httpx.AsyncClient) -> list[tuple[str, str]]:
        """拉取行业板块清单，返回 [(板块号, 行业名)]。"""
        resp = await client.get(
            f"{RANK_BASE}/pt/getRank",
            params={
                "board_type": BOARD_TYPE,
                "sort_type": "price",
                "direct": "down",
                "offset": 0,
                "count": MAX_BOARDS,
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        rows = ((payload or {}).get("data") or {}).get("rank_list") or []
        boards: list[tuple[str, str]] = []
        for row in rows:
            code = str(row.get("code") or "").strip()
            name = str(row.get("name") or "").strip()
            if code and name:
                boards.append((code, name))
        return boards

    async def _fetch_members(
        self, client: httpx.AsyncClient, board_code: str, sem: asyncio.Semaphore
    ) -> list[str]:
        """拉取单个行业的成分股代码（按成交额降序取前 N 只）。"""
        async with sem:
            resp = await client.get(
                f"{RANK_BASE}/hs/getBoardRankList",
                params={
                    "board_code": board_code,
                    "sort_type": "turnover",
                    "direct": "down",
                    "offset": 0,
                    "count": MEMBERS_PER_BOARD,
                },
            )
            resp.raise_for_status()
            payload = resp.json()
            rows = ((payload or {}).get("data") or {}).get("rank_list") or []
            return [_normalize(str(row.get("code") or "")) for row in rows if row.get("code")]

    async def build(self) -> dict[str, str]:
        """构建完整的「股票 → 行业」映射（失败时返回已拿到的部分）。"""
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
        }
        mapping: dict[str, str] = {}
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=8.0),
            headers=headers,
            follow_redirects=True,
            trust_env=False,
        ) as client:
            try:
                boards = await self._fetch_boards(client)
            except (httpx.HTTPError, ValueError, OSError) as exc:
                logger.warning("行业板块清单获取失败：%s", exc)
                return mapping
            if not boards:
                logger.warning("行业板块清单为空，跳过行业补充")
                return mapping

            sem = asyncio.Semaphore(FETCH_CONCURRENCY)

            async def one(board_code: str, board_name: str) -> None:
                try:
                    codes = await self._fetch_members(client, board_code, sem)
                except (httpx.HTTPError, ValueError, OSError) as exc:
                    logger.debug("行业 %s(%s) 成分获取失败：%s", board_name, board_code, exc)
                    return
                # 同一只股票可能被多个板块收录（如概念重叠），以先到者为准，保证稳定
                for code in codes:
                    if code:
                        mapping.setdefault(code, board_name)

            await asyncio.gather(*(one(c, n) for c, n in boards))

        logger.info("行业映射构建完成：%d 个行业，覆盖 %d 只股票", len(set(mapping.values())), len(mapping))
        return mapping

    # ------------------------------------------------------------------ 对外
    async def get_mapping(self, force: bool = False) -> dict[str, str]:
        """获取映射（内存 → 磁盘 → 网络），任一层成功即返回。"""
        async with self._lock:
            if not force and self._mapping and (time.time() - self._loaded_at) < CACHE_TTL:
                return self._mapping
            if not force:
                cached = self.load_cache()
                if cached:
                    self._mapping = cached
                    self._loaded_at = time.time()
                    logger.info("行业映射命中缓存：%d 只股票", len(cached))
                    return self._mapping
            mapping = await self.build()
            if mapping:
                self._mapping = mapping
                self._loaded_at = time.time()
                self.save_cache(mapping)
            return self._mapping

    @property
    def size(self) -> int:
        return len(self._mapping)
