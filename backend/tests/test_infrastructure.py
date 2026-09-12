"""数据源健康度、A 股市场日历、日线增量缓存的单元测试。

这些模块承载三条核心需求，必须被测试覆盖：
- 需求 3「多源故障切换」→ ``SourceHealth`` 的退避与「从未成功则长期跳过」语义；
- 需求 2「开盘期间每 30 秒刷新」→ ``market_calendar`` 的时段判定与分时段 TTL；
- 需求 4「并发增量缓存」→ ``KlineCache`` 的 UPSERT、批量读取与写入时间回退。
"""

from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import pandas as pd
import pytest

from app.market_calendar import (
    CN_TZ,
    effective_ttl,
    is_holiday,
    is_market_open,
    is_trading_day,
    market_clock,
    market_phase,
    next_open,
)
from app.config import get_settings
from app.providers.base import KLINE_COLUMNS
from app.providers.klines_cache import KlineCache
from app.providers.resilient import ResilientProvider
from app.providers.source_base import NEVER_SUCCEEDED_THRESHOLD, SourceHealth
from app.providers.source_base import (
    board_of_code,
    eastmoney_secid,
    limit_pct,
    make_meta,
    market_of_code,
    normalize_code,
    prefixed,
)


def at(year: int, month: int, day: int, hour: int, minute: int) -> dt.datetime:
    """构造北京时间。"""
    return dt.datetime(year, month, day, hour, minute, tzinfo=CN_TZ)


# ===================================================================== 健康度
class TestSourceHealth:
    """数据源健康度与退避（需求 3）。"""

    def test_starts_available(self):
        h = SourceHealth(name="x")
        assert h.available() is True
        assert h.ever_succeeded is False
        assert h.consecutive_failures == 0

    def test_success_resets_failures(self):
        h = SourceHealth(name="x")
        h.record_failure("boom")
        h.record_failure("boom")
        assert h.consecutive_failures == 2
        h.record_success()
        assert h.consecutive_failures == 0
        assert h.ever_succeeded is True
        assert h.available() is True
        assert h.last_error is None

    def test_cooldown_grows_exponentially(self):
        h = SourceHealth(name="x", cooldown_base=3.0, cooldown_max=120.0)
        h.record_failure("e1")
        assert h.cooldown_seconds == pytest.approx(3.0)
        h.record_failure("e2")
        assert h.cooldown_seconds == pytest.approx(6.0)
        h.record_failure("e3")
        assert h.cooldown_seconds == pytest.approx(12.0)
        # 上限封顶
        for _ in range(10):
            h.record_failure("e")
        assert h.cooldown_seconds == pytest.approx(120.0)

    def test_temporary_failure_recovers_after_cooldown(self):
        """曾成功过的源：连续失败只在冷却期内不可用，冷却结束后重新参与。"""
        h = SourceHealth(name="x", cooldown_base=1.0, cooldown_max=2.0)
        h.record_success()
        h.record_failure("temporary")
        # 刚失败时处于冷却期
        assert h.available(now=time.time()) is False
        # 冷却结束后恢复可尝试
        assert h.available(now=time.time() + 5.0) is True

    def test_never_succeeded_source_is_skipped_permanently(self):
        """从未成功过的源：失败达阈值后长期跳过，避免每次白等超时。"""
        h = SourceHealth(name="blocked")
        for _ in range(NEVER_SUCCEEDED_THRESHOLD):
            h.record_failure("Server disconnected without sending a response.")
        assert h.ever_succeeded is False
        # 即使过了很久也不再尝试
        assert h.available(now=time.time() + 100000.0) is False
        snap = h.snapshot()
        assert snap["skipped"] is True
        assert snap["available"] is False

    def test_never_succeeded_skips_after_single_failure(self):
        """阈值默认为 1：本环境不可达的源只付一次超时代价，之后直接跳过。"""
        assert NEVER_SUCCEEDED_THRESHOLD == 1
        h = SourceHealth(name="blocked")
        h.record_failure("blocked")
        assert h.available(now=time.time() + 100000.0) is False

    def test_succeeded_source_is_not_permanently_skipped(self):
        """曾成功过的源即使连续失败也不应被永久跳过，否则会误杀临时故障的源。"""
        h = SourceHealth(name="flaky")
        h.record_success()
        for _ in range(10):
            h.record_failure("temporary")
        assert h.ever_succeeded is True
        # 退避有上限，过了上限仍会重新参与
        assert h.available(now=time.time() + h.cooldown_max + 1.0) is True
        assert h.snapshot()["skipped"] is False

    def test_reset_revives_skipped_source(self):
        """「重新探测」应让被跳过的源重新参与。"""
        h = SourceHealth(name="blocked")
        for _ in range(NEVER_SUCCEEDED_THRESHOLD):
            h.record_failure("blocked")
        assert h.available() is False
        h.reset()
        assert h.available() is True
        assert h.consecutive_failures == 0
        # 重置后若成功，则恢复为正常源
        h.record_success()
        assert h.ever_succeeded is True

    def test_snapshot_fields(self):
        h = SourceHealth(name="tencent")
        h.record_success()
        h.record_failure("oops")
        snap = h.snapshot()
        for key in (
            "name",
            "consecutiveFailures",
            "totalSuccess",
            "totalFailure",
            "successRate",
            "lastError",
            "cooldownSeconds",
            "available",
            "everSucceeded",
            "skipped",
            "lastSuccessAt",
        ):
            assert key in snap, f"缺少字段 {key}"
        assert snap["name"] == "tencent"
        assert snap["totalSuccess"] == 1
        assert snap["totalFailure"] == 1
        assert snap["successRate"] == pytest.approx(0.5)
        assert snap["lastSuccessAt"] is not None


# ===================================================================== 日历
class TestMarketCalendar:
    """A 股市场日历与分时段 TTL（需求 2）。"""

    def test_weekend_is_holiday(self):
        # 2026-09-12 是周六，2026-09-13 是周日
        assert is_holiday(dt.date(2026, 9, 12)) is True
        assert is_holiday(dt.date(2026, 9, 13)) is True
        assert is_trading_day(dt.date(2026, 9, 12)) is False

    def test_weekday_is_trading_day(self):
        # 2026-09-11 是周五，且不在内置休市日表中
        assert is_holiday(dt.date(2026, 9, 11)) is False
        assert is_trading_day(dt.date(2026, 9, 11)) is True

    def test_builtin_holiday(self):
        # 国庆休市
        assert is_trading_day(dt.date(2026, 10, 1)) is False
        # 春节休市
        assert is_trading_day(dt.date(2026, 2, 17)) is False

    @pytest.mark.parametrize(
        "hour,minute,expected",
        [
            (8, 0, "pre_open"),
            (9, 15, "call_auction"),
            (9, 29, "call_auction"),
            (9, 30, "morning"),
            (10, 30, "morning"),
            (11, 30, "morning"),
            (11, 31, "lunch_break"),
            (12, 59, "lunch_break"),
            (13, 0, "afternoon"),
            (14, 59, "afternoon"),
            (15, 0, "afternoon"),
            (15, 1, "closed"),
            (20, 0, "closed"),
        ],
    )
    def test_phases_on_trading_day(self, hour, minute, expected):
        assert market_phase(at(2026, 9, 11, hour, minute)) == expected

    def test_holiday_phase_overrides_clock(self):
        # 周六即使处在交易时段内，也应判为休市
        assert market_phase(at(2026, 9, 12, 10, 0)) == "holiday"
        assert market_phase(at(2026, 9, 12, 14, 0)) == "holiday"

    def test_is_market_open_only_continuous_auction(self):
        assert is_market_open(at(2026, 9, 11, 10, 0)) is True
        assert is_market_open(at(2026, 9, 11, 14, 0)) is True
        # 集合竞价与午休不算连续竞价
        assert is_market_open(at(2026, 9, 11, 9, 20)) is False
        assert is_market_open(at(2026, 9, 11, 12, 0)) is False
        assert is_market_open(at(2026, 9, 11, 16, 0)) is False

    def test_effective_ttl_bands(self):
        """开盘 30 秒、集合竞价与午休 60 秒、盘后 300 秒。"""
        assert effective_ttl(300, 30, at(2026, 9, 11, 10, 0)) == 30
        assert effective_ttl(300, 30, at(2026, 9, 11, 14, 0)) == 30
        assert effective_ttl(300, 30, at(2026, 9, 11, 9, 20)) == 60
        assert effective_ttl(300, 30, at(2026, 9, 11, 12, 0)) == 60
        assert effective_ttl(300, 30, at(2026, 9, 11, 8, 0)) == 300
        assert effective_ttl(300, 30, at(2026, 9, 11, 16, 0)) == 300
        assert effective_ttl(300, 30, at(2026, 9, 12, 10, 0)) == 300  # 周六

    def test_effective_ttl_has_floor(self):
        """刷新间隔配得过小时必须兜底，避免请求风暴。"""
        assert effective_ttl(300, 1, at(2026, 9, 11, 10, 0)) == 5
        assert effective_ttl(300, 2, at(2026, 9, 11, 9, 20)) == 10

    def test_market_clock_should_poll(self):
        """shouldPoll 决定前端是否轮询：交易时段与集合竞价/午休为 True。"""
        assert market_clock(at(2026, 9, 11, 10, 0), 30)["shouldPoll"] is True
        assert market_clock(at(2026, 9, 11, 9, 20), 30)["shouldPoll"] is True
        assert market_clock(at(2026, 9, 11, 12, 0), 30)["shouldPoll"] is True
        assert market_clock(at(2026, 9, 11, 8, 0), 30)["shouldPoll"] is False
        assert market_clock(at(2026, 9, 11, 16, 0), 30)["shouldPoll"] is False
        assert market_clock(at(2026, 9, 12, 10, 0), 30)["shouldPoll"] is False

    def test_market_clock_fields_and_next_open(self):
        clock = market_clock(at(2026, 9, 11, 16, 0), 30)
        assert clock["phase"] == "closed"
        assert clock["intervalSeconds"] == 30
        assert clock["timezone"] == "Asia/Shanghai"
        assert clock["isOpen"] is False
        # 周五收盘后，下次开盘应是下周一 09:30
        assert clock["nextOpenAt"] is not None
        assert clock["nextOpenAt"].startswith("2026-09-14T09:30")
        assert clock["nextCloseAt"] is None

    def test_next_open_skips_weekend(self):
        nxt = next_open(at(2026, 9, 11, 16, 0))
        assert nxt is not None
        assert (nxt.year, nxt.month, nxt.day, nxt.hour, nxt.minute) == (2026, 9, 14, 9, 30)

    def test_next_open_during_session_returns_next_day(self):
        nxt = next_open(at(2026, 9, 11, 10, 0))
        assert nxt is not None
        # 盘中询问「下次开盘」应指下一个交易日的开盘
        assert nxt > at(2026, 9, 11, 10, 0)


# ===================================================================== 缓存
def make_frame(dates: list[str], base: float = 10.0) -> pd.DataFrame:
    """构造规范日线表。"""
    rows = []
    for i, day in enumerate(dates):
        close = base + i * 0.1
        rows.append(
            {
                "date": pd.Timestamp(day),
                "open": close - 0.05,
                "high": close + 0.1,
                "low": close - 0.1,
                "close": close,
                "pre_close": close - 0.1,
                "pct_chg": 0.01,
                "volume": 1000.0 + i,
                "amount": (1000.0 + i) * 100 * close,
                "turnover": 1.5,
            }
        )
    return pd.DataFrame(rows)[KLINE_COLUMNS]


@pytest.fixture()
def cache(tmp_path: Path) -> KlineCache:
    c = KlineCache(tmp_path / "klines.db")
    yield c
    c.close()


class TestKlineCache:
    """日线增量缓存（需求 4）。"""

    def test_read_empty_returns_empty_frame(self, cache: KlineCache):
        df = cache.get("600519", 250)
        assert df.empty
        assert list(df.columns) == KLINE_COLUMNS

    def test_upsert_then_get(self, cache: KlineCache):
        frame = make_frame(["2026-09-01", "2026-09-02", "2026-09-03"])
        written = cache.upsert("600519", frame)
        assert written == 3
        df = cache.get("600519", 250)
        assert len(df) == 3
        assert list(df.columns) == KLINE_COLUMNS
        # 日期必须升序
        assert df["date"].is_monotonic_increasing
        assert float(df["close"].iloc[-1]) == pytest.approx(10.2)

    def test_upsert_is_idempotent_and_updates(self, cache: KlineCache):
        """同一 (code, date) 重复写入应更新而非重复插入（盘中刷新依赖此语义）。"""
        cache.upsert("600519", make_frame(["2026-09-01", "2026-09-02"]))
        # 重写同一日期，收盘价改为 99
        updated = make_frame(["2026-09-01", "2026-09-02"])
        updated.loc[updated.index[-1], "close"] = 99.0
        cache.upsert("600519", updated)
        df = cache.get("600519", 250)
        assert len(df) == 2, "重复日期不应产生新行"
        assert float(df["close"].iloc[-1]) == pytest.approx(99.0)

    def test_get_returns_latest_n(self, cache: KlineCache):
        dates = [f"2026-08-{d:02d}" for d in range(1, 21)]
        cache.upsert("600519", make_frame(dates))
        df = cache.get("600519", 5)
        assert len(df) == 5
        # 应是最近的 5 根
        assert df["date"].iloc[-1] == pd.Timestamp("2026-08-20")

    def test_get_many_matches_single_reads(self, cache: KlineCache):
        """批量读取结果必须与逐只读取完全一致（性能优化不能改变语义）。"""
        cache.upsert("600519", make_frame(["2026-09-01", "2026-09-02", "2026-09-03"]))
        cache.upsert("000001", make_frame(["2026-09-01", "2026-09-02"], base=11.0))
        cache.upsert("300750", make_frame(["2026-09-01"], base=300.0))
        many = cache.get_many(["600519", "000001", "300750", "999999"], 250)
        assert set(many) == {"600519", "000001", "300750"}, "不存在的代码不应出现在结果中"
        for code in ("600519", "000001", "300750"):
            single = cache.get(code, 250)
            pd.testing.assert_frame_equal(many[code], single)

    def test_get_many_handles_large_universe(self, cache: KlineCache):
        """批量读取要能一次处理上千只（超过 SQLite 变量上限，必须自动分片）。"""
        dates = [f"2026-08-{d:02d}" for d in range(1, 11)]
        codes = [f"{600000 + i}" for i in range(1200)]
        for code in codes[:60]:  # 抽样写入，避免测试过慢
            cache.upsert(code, make_frame(dates))
        result = cache.get_many(codes, 250)
        assert len(result) == 60
        assert cache.count_codes() == 60

    def test_latest_date_and_latest_dates(self, cache: KlineCache):
        cache.upsert("600519", make_frame(["2026-09-01", "2026-09-05"]))
        cache.upsert("000001", make_frame(["2026-09-01", "2026-09-03"]))
        assert cache.latest_date() == "2026-09-05"
        got = cache.latest_dates(["600519", "000001", "999999"])
        assert got["600519"] == "2026-09-05"
        assert got["000001"] == "2026-09-03"
        assert "999999" not in got

    def test_last_write_age_falls_back_to_file_mtime(self, cache: KlineCache, tmp_path: Path):
        """关键回归：进程重启后内存时间表为空，必须回退到数据库文件时间。

        曾经这里直接返回 None，导致调用方 `now - None` 抛 TypeError，
        并且会把所有股票误判为「从未缓存」而全量重拉。
        """
        cache.upsert("600519", make_frame(["2026-09-01"]))
        assert cache.last_write_age("600519") is not None
        # 模拟进程重启：清空内存时间表
        cache._write_at.clear()
        age = cache.last_write_age("600519")
        assert age is not None, "重启后必须回退到文件 mtime，不能返回 None"
        assert age >= 0.0
        # 从未写入过的代码，文件存在时也可给出近似年龄（同库共享 mtime）
        assert cache.last_write_age("000000") is not None

    def test_invalidate_forces_refetch(self, cache: KlineCache):
        cache.upsert("600519", make_frame(["2026-09-01"]))
        cache._write_at["600519"] = time.time()
        cache.invalidate("600519")
        assert "600519" not in cache._write_at
        cache.invalidate()
        assert cache._write_at == {}

    def test_upsert_skips_invalid_rows(self, cache: KlineCache):
        """缺收盘价或日期非法的行应被跳过，而不是写入脏数据。"""
        frame = make_frame(["2026-09-01", "2026-09-02"])
        frame.loc[frame.index[-1], "close"] = None
        written = cache.upsert("600519", frame)
        assert written == 1
        assert len(cache.get("600519", 250)) == 1

    def test_upsert_empty_frame_is_noop(self, cache: KlineCache):
        assert cache.upsert("600519", pd.DataFrame(columns=KLINE_COLUMNS)) == 0
        assert cache.get("600519", 250).empty

    def test_prune_keeps_recent_bars(self, cache: KlineCache):
        dates = [f"2026-08-{d:02d}" for d in range(1, 21)]
        cache.upsert("600519", make_frame(dates))
        removed = cache.prune(keep_days=5)
        assert removed >= 15
        df = cache.get("600519", 250)
        assert len(df) == 5
        assert df["date"].iloc[-1] == pd.Timestamp("2026-08-20")

    def test_stats(self, cache: KlineCache):
        cache.upsert("600519", make_frame(["2026-09-01", "2026-09-02"]))
        stats = cache.stats()
        assert stats["enabled"] is True
        assert stats["codes"] == 1
        assert stats["bars"] == 2
        assert stats["latestDate"] == "2026-09-02"

    def test_clear(self, cache: KlineCache):
        cache.upsert("600519", make_frame(["2026-09-01"]))
        cache.clear()
        assert cache.count_codes() == 0


# ===================================================================== 代码工具
class TestCodeHelpers:
    """代码规范化（多源适配的前提）。"""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("600519", "600519"),
            ("sh600519", "600519"),
            ("SH600519", "600519"),
            ("600519.SH", "600519"),
            ("600519.SS", "600519"),
            ("sz000001", "000001"),
            (" 300750 ", "300750"),
            ("1", "000001"),
        ],
    )
    def test_normalize_code(self, raw, expected):
        assert normalize_code(raw) == expected

    def test_market_and_board(self):
        assert market_of_code("600519") == "SH"
        assert market_of_code("688981") == "SH"
        assert market_of_code("000001") == "SZ"
        assert market_of_code("300750") == "SZ"
        assert market_of_code("920000") == "BJ"
        assert board_of_code("600519") == "主板"
        assert board_of_code("300750") == "创业板"
        assert board_of_code("301368") == "创业板"
        assert board_of_code("302132") == "创业板"  # 实测 302132 中航成飞为创业板
        assert board_of_code("688981") == "科创板"
        assert board_of_code("920000") == "北交所"
        assert board_of_code("830799") == "北交所"

    def test_all_four_boards_have_distinct_limit_pct(self):
        """四个板块的涨停幅度必须各不相同（3 个档位），否则涨停判定会错。"""
        assert limit_pct(board_of_code("600519"), False) == 0.10  # 主板
        assert limit_pct(board_of_code("300750"), False) == 0.20  # 创业板
        assert limit_pct(board_of_code("688981"), False) == 0.20  # 科创板
        assert limit_pct(board_of_code("920000"), False) == 0.30  # 北交所

    def test_prefixed_and_secid(self):
        assert prefixed("600519") == "sh600519"
        assert prefixed("000001") == "sz000001"
        assert prefixed("600519", style="upper") == "SH600519"
        assert eastmoney_secid("600519") == "1.600519"
        assert eastmoney_secid("000001") == "0.000001"

    def test_limit_pct_by_board(self):
        assert limit_pct("主板", False) == 0.10
        assert limit_pct("创业板", False) == 0.20
        assert limit_pct("科创板", False) == 0.20
        assert limit_pct("主板", True) == 0.05

    def test_make_meta_derives_fields(self):
        meta = make_meta("sh688981", "中芯国际")
        assert meta.code == "688981"
        assert meta.market == "SH"
        assert meta.board == "科创板"
        assert meta.limitPct == pytest.approx(0.20)
        assert meta.isSt is False
        assert meta.industry == "未分类"

    def test_make_meta_detects_st(self):
        meta = make_meta("600001", "ST示例")
        assert meta.isSt is True
        assert meta.limitPct == pytest.approx(0.05)


# --------------------------------------------------------------------- 资产编码
# 以下用例锁定一类**反复踩到、且症状极具误导性**的缺陷：文本资产的编码/行尾。
# 实测踩坑记录：
#   1) deploy.ps1 若不带 UTF-8 BOM，Windows PowerShell 5.1 会按 GBK 解码其中的中文，
#      乱码字符会“吃掉”紧跟其后的引号 → 报「The string is missing the terminator」，
#      看起来像语法错误，实际是编码问题；
#   2) .sh / Dockerfile 若带 BOM 或 CRLF，在 Linux 上会分别报
#      「bad interpreter」与「$'\r': command not found」，部署直接失败。
REPO_ROOT = Path(__file__).resolve().parents[2]


class TestScriptEncoding:
    """部署脚本与容器构建文件的编码必须正确（跨平台硬约束）。"""

    def test_deploy_ps1_has_utf8_bom(self):
        """deploy.ps1 必须带 UTF-8 BOM：否则 PowerShell 5.1 按 GBK 读中文导致语法错。"""
        raw = (REPO_ROOT / "deploy.ps1").read_bytes()
        assert raw[:3] == b"\xef\xbb\xbf", (
            "deploy.ps1 缺少 UTF-8 BOM。Windows PowerShell 5.1 会把无 BOM 文件按 GBK 解码，"
            "中文乱码后可能吞掉引号并报「字符串未闭合」。"
            "修复：python -c \"import pathlib;p=pathlib.Path('deploy.ps1');"
            "p.write_bytes(b'\\xef\\xbb\\xbf'+p.read_text(encoding='utf-8').encode('utf-8'))\""
        )

    def test_deploy_ps1_has_no_mojibake(self):
        """不应出现替换字符：那是「已经用错编码解码过」的痕迹。"""
        text = (REPO_ROOT / "deploy.ps1").read_text(encoding="utf-8-sig")
        assert "\ufffd" not in text, "deploy.ps1 含 U+FFFD，说明中文已被错误编码破坏"

    def test_shell_scripts_are_lf_without_bom(self):
        """.sh 必须是无 BOM 的 LF：BOM 会导致 bad interpreter，CRLF 会导致 $'\\r' 报错。"""
        for name in ("deploy.sh", "diagnose.sh"):
            raw = (REPO_ROOT / name).read_bytes()
            assert not raw.startswith(b"\xef\xbb\xbf"), f"{name} 不应带 BOM"
            assert b"\r\n" not in raw, f"{name} 必须使用 LF 换行（CRLF 在 Linux 上直接报错）"

    def test_docker_assets_are_lf_without_bom(self):
        """Dockerfile / compose 同样不能带 BOM 或 CRLF。"""
        for name in ("Dockerfile", "docker-compose.yml"):
            raw = (REPO_ROOT / name).read_bytes()
            assert not raw.startswith(b"\xef\xbb\xbf"), f"{name} 不应带 BOM"
            assert b"\r\n" not in raw, f"{name} 必须使用 LF 换行"

# --------------------------------------------------------------------- 内存守护
# 实测踩坑：1.7GB 内存、无 swap 的云主机上按全市场运行会被内核 OOM 杀掉
# （dmesg 里能看到 uid=1000 的 python 进程 anon-rss 约 480MB 时被 kill）。
# 其表现是接口时好时坏、整页加载失败，而容器日志只有一次「启动成功」，
# 极难排查。以下用例锁定探测与降级逻辑。
class TestMemoryGuard:
    """内存探测与低内存自动降级。"""

    def test_effective_limit_takes_the_smaller(self):
        from app.memory import MemoryInfo

        info = MemoryInfo(total_mb=8192, available_mb=4096, cgroup_limit_mb=1024)
        assert info.effective_limit_mb == 1024  # 容器限额优先

    def test_low_memory_detected_by_available(self):
        from app.memory import MemoryInfo

        # 总量很大但当前可用很少 → 同样算吃紧（宿主机上还有其他进程）
        info = MemoryInfo(total_mb=1730, available_mb=340, cgroup_limit_mb=None)
        assert info.is_low is True
        assert info.has_swap is False

    def test_not_low_when_plenty(self):
        from app.memory import MemoryInfo

        info = MemoryInfo(total_mb=8192, available_mb=4096, swap_total_mb=2048)
        assert info.is_low is False
        assert info.has_swap is True

    def test_unknown_memory_is_not_treated_as_low(self):
        """探测不到（如 Windows 无 /proc/meminfo）时不得误判为低内存。"""
        from app.memory import MemoryInfo

        info = MemoryInfo()
        assert info.is_low is False
        assert info.effective_limit_mb is None

    def test_guard_disables_memory_cache_when_low(self, monkeypatch):
        """低内存时必须自动关掉进程内日线缓存（不改变监控范围）。"""
        from app.memory import MemoryInfo
        from app.providers import resilient as resilient_mod

        low = MemoryInfo(total_mb=1730, available_mb=340, swap_total_mb=0)
        monkeypatch.setattr(resilient_mod, "detect_memory", lambda: low)
        settings = get_settings()
        monkeypatch.setattr(settings, "memory_guard", True)
        monkeypatch.setattr(settings, "kline_memory_cache_seconds", 900)

        provider = ResilientProvider()
        try:
            assert settings.kline_memory_cache_seconds == 0, "低内存时应关闭内存缓存"
            guard = provider.status()["memoryGuard"]
            assert guard["enabled"] is True
            assert guard["lowMemory"] is True
            assert guard["applied"], "必须记录实际执行了哪些降级动作"
        finally:
            provider.cache.close()

    def test_guard_can_be_disabled(self, monkeypatch):
        """显式关闭守护时不得改动任何配置。"""
        from app.memory import MemoryInfo
        from app.providers import resilient as resilient_mod

        low = MemoryInfo(total_mb=1730, available_mb=340)
        monkeypatch.setattr(resilient_mod, "detect_memory", lambda: low)
        settings = get_settings()
        monkeypatch.setattr(settings, "memory_guard", False)
        monkeypatch.setattr(settings, "kline_memory_cache_seconds", 900)

        provider = ResilientProvider()
        try:
            assert settings.kline_memory_cache_seconds == 900
            assert provider.status()["memoryGuard"]["applied"] == []
        finally:
            provider.cache.close()

    @pytest.mark.skipif(not Path("/proc/meminfo").exists(), reason="仅 Linux 有 /proc/meminfo")
    def test_detect_memory_reads_linux_procfs(self):
        """.NET/Linux 上必须能读到真实数值（容器内 cgroup 限额可为 None）。"""
        from app.memory import detect_memory

        info = detect_memory()
        assert info.total_mb and info.total_mb > 0
        assert info.available_mb is not None
        assert info.swap_total_mb is not None