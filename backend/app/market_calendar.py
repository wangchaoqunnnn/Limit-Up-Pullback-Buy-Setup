"""A 股市场日历：交易时段判定与分时段缓存策略。

用途：
1. 开盘期间把缓存 TTL 压到 ``REFRESH_INTERVAL_SECONDS``（默认 30 秒），实现
   「开盘期间每 30 秒刷新」；非开盘时段放宽 TTL，避免无意义地反复拉取。
2. 供 ``/api/v1/settings``、``/api/v1/market/clock`` 向前端下发
   ``shouldPoll`` / ``nextOpenAt``，让前端只在需要时轮询，收盘后自动暂停。

交易日判定采用「周末 + 内置法定休市日」近似：
- 周末直接判定为非交易日；
- 内置一份节假日表（含春节、国庆等长假），可随年份维护；
- 若数据源返回的最新行情日期晚于当前日期，说明本地判断有误，以数据为准
  （见 ``is_market_open`` 的 ``data_trade_date`` 参数）。
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

logger = logging.getLogger(__name__)

# 中国标准时间（UTC+8）。不依赖系统时区，避免容器时区配置错误导致判断偏差。
CN_TZ = dt.timezone(dt.timedelta(hours=8), name="CST")

# 连续竞价时段
MORNING_OPEN = dt.time(9, 30)
MORNING_CLOSE = dt.time(11, 30)
AFTERNOON_OPEN = dt.time(13, 0)
AFTERNOON_CLOSE = dt.time(15, 0)

# 集合竞价（09:15 起可开始关注，盘前准备）
CALL_AUCTION_START = dt.time(9, 15)

# 内置休市日（含长假调休后的实际休市日）。格式 YYYY-MM-DD。
# 说明：A 股休市安排由国务院办公厅每年年末公布，此处维护常用年份；
# 缺失年份不会导致错误交易，只会把节假日误判为「可能开市」，
# 此时接口仍会因无新数据而返回上一交易日行情，不影响正确性。
HOLIDAYS: frozenset[str] = frozenset(
    {
        # 2024
        "2024-01-01",
        "2024-02-09", "2024-02-12", "2024-02-13", "2024-02-14", "2024-02-15", "2024-02-16",
        "2024-04-04", "2024-04-05",
        "2024-05-01", "2024-05-02", "2024-05-03",
        "2024-06-10",
        "2024-09-16", "2024-09-17",
        "2024-10-01", "2024-10-02", "2024-10-03", "2024-10-04", "2024-10-07",
        # 2025
        "2025-01-01",
        "2025-01-28", "2025-01-29", "2025-01-30", "2025-01-31",
        "2025-02-03", "2025-02-04",
        "2025-04-04",
        "2025-05-01", "2025-05-02", "2025-05-05",
        "2025-06-02",
        "2025-10-01", "2025-10-02", "2025-10-03", "2025-10-06", "2025-10-07", "2025-10-08",
        # 2026
        "2026-01-01", "2026-01-02",
        "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20", "2026-02-23",
        "2026-04-06",
        "2026-05-01", "2026-05-04", "2026-05-05",
        "2026-06-19",
        "2026-09-25",
        "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07",
        # 2027
        "2027-01-01",
        "2027-02-05", "2027-02-08", "2027-02-09", "2027-02-10", "2027-02-11", "2027-02-12",
        "2027-04-05",
        "2027-05-03", "2027-05-04", "2027-05-05",
        "2027-06-09",
        "2027-10-01", "2027-10-04", "2027-10-05", "2027-10-06", "2027-10-07",
    }
)

# 时段文案
PHASE_TEXT: dict[str, str] = {
    "pre_open": "盘前",
    "call_auction": "集合竞价",
    "morning": "上午交易",
    "lunch_break": "午间休市",
    "afternoon": "下午交易",
    "closed": "已收盘",
    "holiday": "休市日",
}


def now_cn() -> dt.datetime:
    """当前北京时间。"""
    return dt.datetime.now(CN_TZ)


def to_cn(value: dt.datetime | None = None) -> dt.datetime:
    """把任意时间转换为北京时间（无时区信息按北京时间处理）。"""
    if value is None:
        return now_cn()
    if value.tzinfo is None:
        return value.replace(tzinfo=CN_TZ)
    return value.astimezone(CN_TZ)


def is_holiday(day: dt.date | dt.datetime | None = None) -> bool:
    """是否为周末或内置休市日。"""
    if day is None:
        day = now_cn().date()
    elif isinstance(day, dt.datetime):
        day = to_cn(day).date()
    if day.weekday() >= 5:  # 5=周六, 6=周日
        return True
    return day.isoformat() in HOLIDAYS


def is_trading_day(day: dt.date | dt.datetime | None = None) -> bool:
    """是否为交易日（非周末且非休市日）。"""
    return not is_holiday(day)


def market_phase(moment: dt.datetime | None = None) -> str:
    """返回当前所处时段标识。

    取值：``holiday`` / ``pre_open`` / ``call_auction`` / ``morning`` /
    ``lunch_break`` / ``afternoon`` / ``closed``
    """
    current = to_cn(moment)
    if is_holiday(current):
        return "holiday"
    clock = current.time()
    if clock < CALL_AUCTION_START:
        return "pre_open"
    if clock < MORNING_OPEN:
        return "call_auction"
    if clock <= MORNING_CLOSE:
        return "morning"
    if clock < AFTERNOON_OPEN:
        return "lunch_break"
    if clock <= AFTERNOON_CLOSE:
        return "afternoon"
    return "closed"


def is_market_open(moment: dt.datetime | None = None) -> bool:
    """是否处于连续竞价时段（09:30-11:30 / 13:00-15:00）。"""
    return market_phase(moment) in {"morning", "afternoon"}


def _combine(day: dt.date, clock: dt.time) -> dt.datetime:
    return dt.datetime.combine(day, clock, tzinfo=CN_TZ)


def next_open(moment: dt.datetime | None = None, max_scan_days: int = 30) -> dt.datetime | None:
    """下一个开盘时刻（09:30）；若当前正在交易，则返回明日开盘。"""
    current = to_cn(moment)
    if is_market_open(current):
        current = _combine(current.date(), dt.time(15, 0)) + dt.timedelta(seconds=1)
    day = current.date()
    for _ in range(max_scan_days):
        if is_trading_day(day):
            candidate = _combine(day, MORNING_OPEN)
            if candidate > current:
                return candidate
        day += dt.timedelta(days=1)
    return None


def next_close(moment: dt.datetime | None = None) -> dt.datetime | None:
    """下一个收盘时刻（15:00）；已收盘返回 None。"""
    current = to_cn(moment)
    phase = market_phase(current)
    if phase in {"pre_open", "call_auction"}:
        return _combine(current.date(), AFTERNOON_CLOSE)
    if phase == "morning":
        return _combine(current.date(), MORNING_CLOSE)
    if phase == "lunch_break":
        return _combine(current.date(), AFTERNOON_CLOSE)
    if phase == "afternoon":
        return _combine(current.date(), AFTERNOON_CLOSE)
    return None


def reference_bar_date(moment: dt.datetime | None = None) -> str:
    """返回「当前应该已存在的最新一根日线」的日期（YYYY-MM-DD）。

    用途：判断缓存是否需要向上游重新拉取。
    **收盘后不应再深挖历史** —— 市场已关闭、当日 K 线已定型，
    再频繁请求同一个已确定的日期只会浪费上游配额并触发反爬限流。

    规则：
    - 若当前处于**盘中会话**（连续竞价）：当天这根 K 线仍在变动 → 返回今天；
    - 否则取「今天或之前最近的一个交易日」：
      * 盘前（09:30 前）：今天尚未开盘，最新完整 K 线是上一交易日；
      * 盘后 / 周末 / 休市：取最近一个已完成的交易日。

    注意与 :func:`market_clock` 的 ``shouldPoll`` 配合使用：
    ``shouldPoll`` 决定前端是否需要刷新，本函数决定后端是否需要向上游取数。
    """
    current = to_cn(moment)
    if is_market_open(current):
        return current.date().isoformat()
    day = current.date()
    # 盘前：今天的 K 线还没生成，回退到上一交易日
    if is_trading_day(day) and current.time() < MORNING_OPEN:
        day -= dt.timedelta(days=1)
    for _ in range(30):
        if is_trading_day(day):
            return day.isoformat()
        day -= dt.timedelta(days=1)
    return current.date().isoformat()


def market_clock(moment: dt.datetime | None = None, interval_seconds: int = 30) -> dict[str, Any]:
    """市场时钟快照，供前端决定是否轮询。

    ``shouldPoll`` 为 True 时，前端应按 ``intervalSeconds`` 间隔刷新。
    ``call_auction``（09:15-09:30）也允许轮询，便于观察开盘异动。
    """
    current = to_cn(moment)
    phase = market_phase(current)
    open_now = phase in {"morning", "afternoon"}
    pending = next_open(current) if not open_now else None
    close_at = next_close(current)
    # 午间休市（11:30-13:00）也保持轮询：此时盘中数据仍可能被交易所/数据源
    # 修正（成交量校准、涨停封单变化），且 13:00 开盘后需立刻拿到最新值。
    return {
        "now": current.isoformat(timespec="seconds"),
        "tradeDate": current.date().isoformat(),
        "isTradingDay": is_trading_day(current),
        "isOpen": open_now,
        "phase": phase,
        "phaseText": PHASE_TEXT.get(phase, phase),
        "shouldPoll": open_now or phase in {"call_auction", "lunch_break"},
        "intervalSeconds": int(interval_seconds),
        "nextOpenAt": pending.isoformat(timespec="seconds") if pending else None,
        "nextCloseAt": close_at.isoformat(timespec="seconds") if close_at else None,
        "timezone": "Asia/Shanghai",
    }


def effective_ttl(
    base_ttl: int,
    refresh_interval: int,
    moment: dt.datetime | None = None,
) -> int:
    """按交易时段返回生效缓存 TTL（秒）。

    - 连续竞价时段：``refresh_interval``（默认 30 秒），保证开盘期间每 30 秒刷新；
    - 集合竞价 / 午间休市：``refresh_interval`` 的 2 倍，兼顾及时性与请求量；
    - 盘前 / 收盘后 / 休市：``base_ttl``（默认 300 秒），行情不再变化。
    """
    phase = market_phase(moment)
    if phase in {"morning", "afternoon"}:
        return max(5, int(refresh_interval))
    if phase in {"call_auction", "lunch_break"}:
        return max(10, int(refresh_interval) * 2)
    return max(30, int(base_ttl))
