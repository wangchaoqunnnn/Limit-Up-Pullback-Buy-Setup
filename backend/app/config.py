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

    # 数据源模式：auto / real / eastmoney / tencent / ths / sina / yahoo / synthetic
    #   auto      —— 按 DATA_SOURCE_ORDER 依次尝试真实源，全部不可用才降级为合成数据
    #   real      —— 同 auto，但绝不降级为合成数据（全部真实源失败则报错）
    #   单个源名  —— 强制只用该源（eastmoney / tencent / ths / sina / yahoo / synthetic）
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
    #   yahoo     雅虎财经：**完全独立的境外源**，日线约 243 根且与同花顺逐字段一致，
    #             但不提供北交所与全市场列表 —— 仅作最后兜底，
    #             防范国内几个免费源因同一网络策略/反爬批次同时失效
    data_source_order: str = "eastmoney,tencent,ths,sina,yahoo"

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
    # 进程内日线读穿缓存的有效期（秒），0 = 关闭。
    #
    # 为什么必须要它：全市场 5561 只、137 万行时，从 SQLite 重建日线
    # 本机实测要 **43.94 秒**（写回也要数十秒）。若每个请求都读一遍数据库，
    # 页面每次刷新都要等这么久，云服务器上表现为大面积超时。
    # 该缓存只是「代替数据库读取」，**不参与新鲜度判定** ——
    # 是否向数据源补数仍由 CACHE_TTL_SECONDS/REFRESH_INTERVAL_SECONDS 决定，
    # 因此开盘期间 30 秒刷新一轮的行为完全不受影响。
    # 内存占用参考：全市场约 5561 × 250 根 ≈ 150MB 量级。
    kline_memory_cache_seconds: int = 900
    # 进程内缓存最多持有多少只股票（超出即整体丢弃重建，避免无限增长）
    kline_memory_cache_max_codes: int = 8000
    # 是否在服务启动后**后台预热**全市场日线。
    #
    # 为什么需要：全市场首轮取数实测约 3 分钟（5561 只：本地 195 秒，
    # 云服务器更久）。若把它留给「用户打开页面的那一刻」，用户首屏就要
    # 等几分钟并看到超时。改为启动后台任务预先跑完，部署完成后过一会儿
    # 再访问即可秒开。预热是后台任务，不阻塞服务启动，失败也不影响服务。
    warmup_on_startup: bool = True
    # 请求的股票数达到该值时，会先等待正在进行的预热完成，
    # 避免「预热」与「用户首个请求」同时向同一批股票取数（重复请求上游易被限流）。
    # 取数较少的请求（如单只股票详情）不等待，保持随时可用。
    warmup_wait_min_codes: int = 500
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
        allowed = {"auto", "real", "eastmoney", "tencent", "ths", "sina", "yahoo", "synthetic"}
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
        return out or ["eastmoney", "tencent", "ths", "sina", "yahoo"]

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
