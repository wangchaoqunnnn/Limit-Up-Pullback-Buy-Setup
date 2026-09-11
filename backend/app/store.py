"""持久化层：股票池与规则参数的 JSON 存储（原子写）。

- 文件位于 ``DATA_DIR/pool.json`` 与 ``DATA_DIR/rules.json``；
- 目录/文件不存在时自动创建并使用默认值；
- 写入采用「临时文件 + os.replace」保证原子性，避免半截文件。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any

from .config import get_settings
from .models import PoolItem, RuleSet, StockMeta
from .utils import now_iso, round2, safe_div

logger = logging.getLogger(__name__)

STATUS_TEXT = {
    "watching": "观察中",
    "triggered": "已进入买区",
    "stopped": "已跌破止损",
    "target": "已达目标位",
}


class JsonFileStore:
    """极简 JSON 文件存储（原子写 + 线程锁）。

    注意：锁必须使用可重入锁（RLock）。``read()`` 在文件缺失或损坏时会调用
    ``write()`` 落盘，而 ``write()`` 也需要持锁；若使用普通 ``Lock`` 会在同一
    线程内自我死锁，导致首次请求永久挂起。
    """

    def __init__(self, path: Path, default: Any) -> None:
        self.path = Path(path)
        self.default = default
        self._lock = threading.RLock()

    def read(self) -> Any:
        """读取数据；文件缺失或损坏时返回默认值并落盘。"""
        with self._lock:
            if not self.path.exists():
                self.write(self.default)
                return json.loads(json.dumps(self.default, ensure_ascii=False))
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("配置文件 %s 解析失败（%s），已重置为默认值", self.path.name, exc)
                self.write(self.default)
                return json.loads(json.dumps(self.default, ensure_ascii=False))

    def write(self, payload: Any) -> None:
        """原子写入。"""
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, ensure_ascii=False, indent=2)
                os.replace(tmp_name, self.path)
            except OSError as exc:
                logger.error("写入 %s 失败：%s", self.path.name, exc)
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)
                raise


class RuleStore:
    """策略参数存储（内存热更新 + 落盘）。"""

    def __init__(self) -> None:
        self._store: JsonFileStore | None = None
        self._cache: RuleSet | None = None
        self._lock = threading.Lock()

    @property
    def store(self) -> JsonFileStore:
        if self._store is None:
            self._store = JsonFileStore(get_settings().rules_file, RuleSet().model_dump())
        return self._store

    def get(self) -> RuleSet:
        """读取当前规则（带内存缓存）。"""
        with self._lock:
            if self._cache is not None:
                return self._cache
            raw = self.store.read()
            try:
                self._cache = RuleSet(**raw)
            except (TypeError, ValueError) as exc:
                logger.warning("规则文件内容非法（%s），已回退默认规则", exc)
                self._cache = RuleSet()
            return self._cache

    def update(self, patch: dict[str, Any]) -> RuleSet:
        """局部更新规则并持久化。"""
        with self._lock:
            current = self._cache or self.get()
            data = current.model_dump()
            for key, value in patch.items():
                if value is None:
                    continue
                if key == "weights" and isinstance(value, dict):
                    weights = dict(data.get("weights") or {})
                    for wkey, wvalue in value.items():
                        if wkey in weights and wvalue is not None:
                            weights[wkey] = float(wvalue)
                    total = sum(weights.values())
                    if total > 0:  # 权重归一化，保证总分仍在 0~100
                        weights = {k: round(v / total, 4) for k, v in weights.items()}
                    data["weights"] = weights
                elif key in data:
                    data[key] = value
            if data.get("buyScore", 0) < data.get("watchScore", 0):
                data["buyScore"], data["watchScore"] = data["watchScore"], data["buyScore"]
            rule_set = RuleSet(**data)
            self.store.write(rule_set.model_dump())
            self._cache = rule_set
            return rule_set

    def reset(self) -> RuleSet:
        """恢复默认规则。"""
        with self._lock:
            rule_set = RuleSet()
            self.store.write(rule_set.model_dump())
            self._cache = rule_set
            return rule_set


class PoolStore:
    """自选低吸池存储。"""

    def __init__(self) -> None:
        self._store: JsonFileStore | None = None
        self._lock = threading.Lock()

    @property
    def store(self) -> JsonFileStore:
        if self._store is None:
            self._store = JsonFileStore(get_settings().pool_file, [])
        return self._store

    # ------------------------------------------------------------------ 读
    def list_raw(self) -> list[dict[str, Any]]:
        data = self.store.read()
        return data if isinstance(data, list) else []

    def get_raw(self, code: str) -> dict[str, Any] | None:
        for item in self.list_raw():
            meta = item.get("meta") or {}
            if meta.get("code") == code:
                return item
        return None

    def contains(self, code: str) -> bool:
        return self.get_raw(code) is not None

    # ------------------------------------------------------------------ 写
    def add(self, item: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            items = self.list_raw()
            items.append(item)
            self.store.write(items)
            return item

    def remove(self, code: str) -> bool:
        with self._lock:
            items = self.list_raw()
            remaining = [i for i in items if (i.get("meta") or {}).get("code") != code]
            if len(remaining) == len(items):
                return False
            self.store.write(remaining)
            return True

    def replace_all(self, items: list[dict[str, Any]]) -> None:
        with self._lock:
            self.store.write(items)


def new_pool_id() -> str:
    """生成股票池条目 id。"""
    return uuid.uuid4().hex[:12]


def compute_status(
    last_close: float | None,
    buy_low: float | None,
    buy_high: float | None,
    stop_loss: float | None,
    take_profit1: float | None,
) -> tuple[str, str]:
    """依据最新价与买区/止损/目标位实时计算条目状态。"""
    if last_close is None:
        return "watching", STATUS_TEXT["watching"]
    if stop_loss is not None and last_close <= stop_loss:
        return "stopped", STATUS_TEXT["stopped"]
    if take_profit1 is not None and last_close >= take_profit1:
        return "target", STATUS_TEXT["target"]
    low = buy_low if buy_low is not None else float("-inf")
    high = buy_high if buy_high is not None else float("inf")
    if low <= last_close <= high:
        return "triggered", STATUS_TEXT["triggered"]
    return "watching", STATUS_TEXT["watching"]


def build_pool_item(
    meta: StockMeta,
    signal: Any | None,
    payload: dict[str, Any],
    last_close: float | None,
    last_date: str | None,
) -> PoolItem:
    """由信号 + 请求体构造股票池条目（省略的价格由战法自动计算）。"""
    plan = getattr(signal, "plan", None)
    auto_low = getattr(plan, "buyLow", None)
    auto_high = getattr(plan, "buyHigh", None)
    auto_stop = getattr(plan, "stopLoss", None)
    auto_tp1 = getattr(plan, "takeProfit1", None)
    auto_tp2 = getattr(plan, "takeProfit2", None)

    buy_low = round2(payload.get("buyLow") if payload.get("buyLow") is not None else auto_low)
    buy_high = round2(payload.get("buyHigh") if payload.get("buyHigh") is not None else auto_high)
    stop_loss = round2(payload.get("stopLoss") if payload.get("stopLoss") is not None else auto_stop)
    take_profit1 = round2(payload.get("takeProfit1") if payload.get("takeProfit1") is not None else auto_tp1)
    take_profit2 = round2(payload.get("takeProfit2") if payload.get("takeProfit2") is not None else auto_tp2)
    if buy_high and buy_low and buy_high < buy_low:
        buy_high = buy_low

    added_price = last_close if last_close is not None else getattr(signal, "lastClose", None)
    status, status_text = compute_status(last_close, buy_low, buy_high, stop_loss, take_profit1)
    pnl = safe_div(last_close - added_price, added_price, 0.0) if (last_close is not None and added_price) else 0.0

    return PoolItem(
        id=new_pool_id(),
        meta=meta,
        limitUpDate=getattr(signal, "limitUpDate", None),
        limitUpType=getattr(signal, "limitUpType", "NONE"),
        addedAt=now_iso(),
        addedPrice=round2(added_price) if added_price else None,
        buyLow=buy_low or None,
        buyHigh=buy_high or None,
        stopLoss=stop_loss or None,
        takeProfit1=take_profit1 or None,
        takeProfit2=take_profit2 or None,
        note=payload.get("note"),
        lastClose=round2(last_close) if last_close is not None else None,
        lastDate=last_date,
        pnlPct=round(pnl, 4),
        status=status,  # type: ignore[arg-type]
        statusText=status_text,
    )


def rule_store() -> RuleStore:
    """规则存储单例。"""
    global _RULE_STORE
    if _RULE_STORE is None:
        _RULE_STORE = RuleStore()
    return _RULE_STORE


def pool_store() -> PoolStore:
    """股票池存储单例。"""
    global _POOL_STORE
    if _POOL_STORE is None:
        _POOL_STORE = PoolStore()
    return _POOL_STORE


_RULE_STORE: RuleStore | None = None
_POOL_STORE: PoolStore | None = None
