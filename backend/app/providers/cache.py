"""本地文件缓存：JSON 落盘于 DATA_DIR/cache/，按 CACHE_TTL_SECONDS 判断新鲜度。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class FileCache:
    """极简文件缓存（单进程使用，写入采用原子替换）。"""

    def __init__(self, cache_dir: Path, ttl_seconds: int = 300) -> None:
        self.cache_dir = Path(cache_dir)
        self.ttl_seconds = int(ttl_seconds)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ 内部
    def _path(self, key: str) -> Path:
        digest = hashlib.sha1(str(key).encode("utf-8")).hexdigest()[:20]
        return self.cache_dir / f"{digest}.json"

    # ------------------------------------------------------------------ 接口
    def get(self, key: str, ttl: int | None = None) -> Any | None:
        """读取缓存；不存在、已过期或解析失败返回 None。"""
        path = self._path(key)
        if not path.exists():
            return None
        ttl = self.ttl_seconds if ttl is None else int(ttl)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("缓存读取失败 %s：%s", path.name, exc)
            return None
        created_at = float(raw.get("createdAt", 0))
        if ttl >= 0 and (time.time() - created_at) > ttl:
            return None
        return raw.get("payload")

    def set(self, key: str, payload: Any, ttl: int | None = None) -> None:
        """写入缓存（原子写：临时文件 + os.replace）。"""
        path = self._path(key)
        record = {
            "key": key,
            "createdAt": time.time(),
            "ttl": self.ttl_seconds if ttl is None else int(ttl),
            "payload": payload,
        }
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(dir=str(self.cache_dir), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(record, fh, ensure_ascii=False)
            os.replace(tmp_name, path)
        except (OSError, TypeError) as exc:
            logger.warning("缓存写入失败 %s：%s", path.name, exc)

    def age(self, key: str) -> float | None:
        """返回缓存年龄（秒），不存在返回 None。"""
        path = self._path(key)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return max(0.0, time.time() - float(raw.get("createdAt", 0)))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return None

    def invalidate(self, key: str) -> bool:
        """按 key 失效单条缓存。"""
        path = self._path(key)
        try:
            if path.exists():
                path.unlink()
                return True
        except OSError as exc:
            logger.warning("缓存删除失败 %s：%s", path.name, exc)
        return False

    def clear(self) -> int:
        """清空缓存目录，返回删除文件数。"""
        removed = 0
        try:
            for item in self.cache_dir.glob("*.json"):
                try:
                    item.unlink()
                    removed += 1
                except OSError:
                    continue
        except OSError as exc:
            logger.warning("缓存清理失败：%s", exc)
        return removed

    def stats(self) -> dict[str, Any]:
        """缓存目录概览。"""
        files = list(self.cache_dir.glob("*.json"))
        total = 0
        for f in files:
            try:
                total += f.stat().st_size
            except OSError:
                continue
        return {"count": len(files), "bytes": total, "dir": str(self.cache_dir)}
