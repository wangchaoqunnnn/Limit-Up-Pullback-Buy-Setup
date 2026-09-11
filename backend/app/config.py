"""全局配置模块。

所有路径均以 ``BASE_DIR``（即 ``backend/``）为基准推导，禁止出现绝对路径字面量。
环境变量统一通过 pydantic-settings 读取，缺省值保证完全离线可运行。
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ 目录（本文件位于 backend/app/config.py）
BASE_DIR: Path = Path(__file__).resolve().parent.parent

APP_NAME = "涨停回调低吸战法"
APP_VERSION = "1.1.0"
TIMEZONE = "Asia/Shanghai"

# 开发态跨域白名单默认值（仅此处允许出现开发服务器地址，且不写死在任何业务逻辑里）
DEFAULT_CORS_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """后端运行配置（全部可由环境变量覆盖）。"""

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_host: str = "0.0.0.0"
    app_port: int = 8000

    # 数据源模式：auto / real / eastmoney / tencent / sina / synthetic
    #   auto      —— 按 DATA_SOURCE_ORDER 依次尝试真实源，全部不可用才降级为合成数据
    #   real      —— 同 auto，但绝不降级为合成数据（全部真实源失败则报错）
    #   单个源名  —— 强制只用该源（eastmoney / tencent / sina / synthetic）
    data_source_mode: str = "auto"

    # 真实数据源优先级（逗号分隔）。故障转移按此顺序尝试；
    # 出现连续失败的源会进入指数退避冷却，冷却结束后自动恢复参与。
    #
    # 顺序说明（实测依据）：
    #   eastmoney 全市场列表字段最全，排第一（部分网络会阻断该域名，会快速失败跳过）
    #   tencent   列表与日线都稳，但列表缺科创板/北交所
    #   ths       同花顺：日线**覆盖四个板块含北交所**，且速度最快（实测 0.03s/只）；
    #             同时提供股票中文名称，是补齐科创板/北交所的关键补充源
    #   sina      列表覆盖四板块（三 node 并集），但列表接口有反爬限流
    data_source_order: str = "eastmoney,tencent,ths,sina"

    # 数据目录（相对路径按 backend/ 解析；留空则使用 backend/data）
    data_dir: str = ""

    cache_ttl_seconds: int = 300
    # 股票池规模：**0 表示不限量、覆盖全部 A 股**（默认，对应用户要求
    # 「主板、创业板、科创板、北交所等所有交易所的股票都不能遗漏」）。
    # 设为正整数 N 时只监控 N 只，并按「板块轮转」截取以保证四个板块都有代表。
    # 性能参考：全市场约 5500 只，首轮全量拉取在 24 并发下约 3~5 分钟（一次性），
    # 之后每轮为增量刷新。
    universe_size: int = 0
    # 合成演示数据的股票数量（与 UNIVERSE_SIZE 解耦：
    # 演示数据要的是「构造出足够多的形态样本」，不是「越多越好」）。
    synthetic_universe_size: int = 300
    # 开盘期间的刷新间隔（秒）。前端按此间隔轮询，后端缓存 TTL 也取此值。
    refresh_interval_seconds: int = 30
    # 日线缓存每只股票保留的最大根数（控制 SQLite 体积）
    kline_cache_max_days: int = 400
    # 单个数据源的并发请求数（过高易被上游限流）
    source_concurrency: int = 12
    # 日线批量抓取的并发数。日线请求量最大（全市场约 5500 只），可单独调高；
    # 实测腾讯/新浪在 16~24 并发下稳定，过高会触发反爬（新浪返回 HTTP 456）。
    kline_concurrency: int = 24
    http_timeout: float = 10.0
    log_level: str = "INFO"
    cors_origins: str = DEFAULT_CORS_ORIGINS
    timezone: str = TIMEZONE

    @field_validator("data_source_mode")
    @classmethod
    def _check_mode(cls, v: str) -> str:
        v = (v or "auto").strip().lower()
        allowed = {"auto", "real", "eastmoney", "tencent", "sina", "synthetic"}
        if v not in allowed:
            logger.warning("非法的 DATA_SOURCE_MODE=%s，已回退为 auto", v)
            return "auto"
        return v

    @field_validator("universe_size")
    @classmethod
    def _check_universe(cls, v: int) -> int:
        # 允许 0（= 全市场不限量）；负数视为 0
        if v < 0:
            logger.warning("UNIVERSE_SIZE=%s 为负，按 0（全市场）处理", v)
            return 0
        return int(v)

    @field_validator("refresh_interval_seconds")
    @classmethod
    def _check_interval(cls, v: int) -> int:
        # 下限 5 秒，避免配置成 0 或负数导致请求风暴
        if v < 5:
            logger.warning("REFRESH_INTERVAL_SECONDS=%s 过小，已回退为 30 秒", v)
            return 30
        return v

    @field_validator("source_concurrency")
    @classmethod
    def _check_concurrency(cls, v: int) -> int:
        if v < 1:
            return 12
        return min(int(v), 64)

    @field_validator("kline_concurrency")
    @classmethod
    def _check_kline_concurrency(cls, v: int) -> int:
        if v < 1:
            return 16
        return min(int(v), 64)

    @property
    def source_order_list(self) -> list[str]:
        """真实数据源优先级列表（去空、保序、去重）。"""
        seen: set[str] = set()
        out: list[str] = []
        for item in (self.data_source_order or "").split(","):
            name = item.strip().lower()
            if name and name not in seen:
                seen.add(name)
                out.append(name)
        return out or ["eastmoney", "tencent", "sina"]

    # ------------------------------------------------------------------ 路径
    @property
    def data_path(self) -> Path:
        """数据目录：DATA_DIR 为空时使用 backend/data。"""
        raw = (self.data_dir or "").strip()
        if not raw:
            return BASE_DIR / "data"
        p = Path(raw)
        return p if p.is_absolute() else (BASE_DIR / p)

    @property
    def cache_path(self) -> Path:
        return self.data_path / "cache"

    @property
    def kline_db_file(self) -> Path:
        """日线增量缓存数据库。"""
        return self.data_path / "klines.db"

    @property
    def stock_list_file(self) -> Path:
        """股票列表快照（真实源，避免每次刷新都重新翻页拉取）。"""
        return self.data_path / "stock_list.json"

    @property
    def industry_map_file(self) -> Path:
        """股票 → 行业映射缓存（真实源不返回行业，需单独补充）。"""
        return self.data_path / "industry_map.json"

    @property
    def pool_file(self) -> Path:
        return self.data_path / "pool.json"

    @property
    def rules_file(self) -> Path:
        return self.data_path / "rules.json"

    @property
    def cors_origin_list(self) -> list[str]:
        items = [x.strip() for x in (self.cors_origins or "").split(",")]
        return [x for x in items if x]

    def ensure_dirs(self) -> None:
        """创建数据与缓存目录（幂等）。"""
        self.data_path.mkdir(parents=True, exist_ok=True)
        self.cache_path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """获取配置单例。"""
    return Settings()


def reload_settings() -> Settings:
    """清除缓存并重新读取环境变量（测试与热更新使用）。"""
    get_settings.cache_clear()
    return get_settings()


def configure_logging() -> None:
    """初始化标准日志：格式含时间/级别/模块。"""
    settings = get_settings()
    level = getattr(logging, (settings.log_level or "INFO").upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=False,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
