"""通用工具：时间、数值格式化、均线计算、统一响应信封、JSON 清洗。"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

import numpy as np
import orjson
import pandas as pd
from pydantic import BaseModel
from starlette.responses import JSONResponse

# 中国标准时间（UTC+8）
CN_TZ = timezone(timedelta(hours=8))


class EnvelopeJSONResponse(JSONResponse):
    """统一信封响应：使用 orjson 序列化（内容已由 to_jsonable 清洗）。"""

    media_type = "application/json"

    def render(self, content: Any) -> bytes:
        return orjson.dumps(content)

CODE_PATTERN = re.compile(r"^\d{6}$")

# 沪深北交易所休市无需精确建模，使用工作日近似即可
TRADE_DAYS_PER_YEAR = 244


# --------------------------------------------------------------------- 时间
def now_cn() -> datetime:
    """当前北京时间（带时区）。"""
    return datetime.now(CN_TZ)


def now_iso() -> str:
    """ISO-8601 字符串（Asia/Shanghai）。"""
    return now_cn().isoformat(timespec="seconds")


def today_str() -> str:
    return now_cn().strftime("%Y-%m-%d")


def date_to_str(value: Any) -> str:
    """把日期类对象统一格式化为 YYYY-MM-DD。"""
    if isinstance(value, str):
        return value[:10]
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return pd.Timestamp(value).strftime("%Y-%m-%d")
    return str(value)


def trade_dates(count: int, end: date | str | None = None) -> np.ndarray:
    """生成截止到 end 的 count 个交易日期（工作日近似），返回 datetime64[D] 数组。"""
    if end is None:
        end_ts = pd.Timestamp(now_cn().date())
    else:
        end_ts = pd.Timestamp(end)
    idx = pd.bdate_range(end=end_ts, periods=int(count))
    return idx.values.astype("datetime64[D]")


# --------------------------------------------------------------------- 数值
def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def safe_div(a: float | None, b: float | None, default: float = 0.0) -> float:
    """安全除法，除零或非法输入返回默认值。"""
    try:
        a = float(a)  # type: ignore[arg-type]
        b = float(b)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if not math.isfinite(a) or not math.isfinite(b) or abs(b) < 1e-12:
        return default
    return a / b


def round2(value: Any, default: float = 0.0) -> float:
    """保留 2 位小数，非法值返回默认值。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(v):
        return default
    return round(v, 2)


def round4(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(v):
        return default
    return round(v, 4)


def nan_to_none(value: Any) -> float | None:
    """NaN/Inf 转 None，供 JSON 输出使用。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return round(v, 4)


# --------------------------------------------------------------------- 展示
def format_pct(value: Any, digits: int = 2) -> str:
    """涨跌幅格式化：+3.21% / -1.05%。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "--"
    if not math.isfinite(v):
        return "--"
    return f"{v * 100:+.{digits}f}%"


def format_amount(value: Any) -> str:
    """金额单位化：亿 / 万 / 元。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "--"
    if not math.isfinite(v):
        return "--"
    av = abs(v)
    if av >= 1e8:
        return f"{v / 1e8:.2f}亿"
    if av >= 1e4:
        return f"{v / 1e4:.2f}万"
    return f"{v:.0f}元"


def format_volume_lots(value: Any) -> str:
    """成交量（手）单位化。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "--"
    if abs(v) >= 1e8:
        return f"{v / 1e8:.2f}亿手"
    if abs(v) >= 1e4:
        return f"{v / 1e4:.0f}万手"
    return f"{v:.0f}手"


def limit_pct_of(board: str, is_st: bool = False) -> float:
    """按板块与 ST 状态给出涨停幅度。"""
    if is_st:
        return 0.05
    if board in {"创业板", "科创板"}:
        return 0.20
    if board == "北交所":
        return 0.30
    return 0.10


def is_valid_code(code: str) -> bool:
    return bool(CODE_PATTERN.match(str(code or "").strip()))


def market_of_code(code: str) -> str:
    """按代码段推断交易所。"""
    code = str(code)
    if code.startswith(("6", "9")):
        return "SH"
    if code.startswith(("4", "8")):
        return "BJ"
    return "SZ"


# --------------------------------------------------------------------- 均线
def moving_average(values: Any, window: int) -> pd.Series:
    """滚动均线（缺失值保持 NaN）。"""
    s = pd.Series(values, dtype="float64")
    return s.rolling(window, min_periods=window).mean()


def enrich_candles(df: pd.DataFrame) -> pd.DataFrame:
    """为日线数据补充 volRatio 与 MA5/10/20/60。

    ``volRatio`` = 当日成交量 / 前 5 日均量（不含当日）。
    """
    if df is None or len(df) == 0:
        return df
    out = df.copy()
    vol = pd.to_numeric(out["volume"], errors="coerce").astype("float64")
    base = vol.rolling(5, min_periods=5).mean().shift(1)
    out["volRatio"] = vol / base.where(base > 0)
    close = pd.to_numeric(out["close"], errors="coerce").astype("float64")
    for window in (5, 10, 20, 60):
        out[f"ma{window}"] = close.rolling(window, min_periods=window).mean()
    return out


# --------------------------------------------------------------------- JSON
def to_jsonable(obj: Any) -> Any:
    """把 numpy/pandas/pydantic 对象递归转换为可 JSON 序列化的原生类型。"""
    if obj is None or isinstance(obj, (str, bool, int)):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, BaseModel):
        return to_jsonable(obj.model_dump())
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return [to_jsonable(v) for v in obj.tolist()]
    if isinstance(obj, np.generic):
        return to_jsonable(obj.item())
    if isinstance(obj, (pd.Timestamp, datetime)):
        return pd.Timestamp(obj).isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    return obj


# --------------------------------------------------------------------- 信封
def _envelope(ok: bool, data: Any, message: str | None) -> dict[str, Any]:
    return {
        "ok": ok,
        "data": to_jsonable(data),
        "message": message,
        "serverTime": now_iso(),
    }


def api_ok(data: Any = None, status_code: int = 200, message: str | None = None) -> EnvelopeJSONResponse:
    """统一成功信封。"""
    return EnvelopeJSONResponse(status_code=status_code, content=_envelope(True, data, message))


def api_err(message: str, status_code: int = 400, data: Any = None) -> EnvelopeJSONResponse:
    """统一失败信封（message 为可直接展示的中文）。"""
    return EnvelopeJSONResponse(status_code=status_code, content=_envelope(False, data, message))
