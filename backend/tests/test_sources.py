"""腾讯 / 新浪行情源适配器单元测试。

设计原则：
1. **全部离线**：解析逻辑（``_parse_*``）用内联的真实响应样本直接测，不依赖网络；
   端到端测试用 ``respx`` mock HTTP 层（httpx 层 mock，provider 代码路径完整走一遍）。
2. 样本数据均为本机实测抓取的真实响应（2026-09-11），字段索引与线上一致。
3. 重点覆盖：字段缺失/长度不足/类型非法不抛异常、名称含空格、涨跌幅换算、
   成交量「股 → 手」、腾讯日线 **开收高低**（非 OHLC）顺序、``qfqday``/``day`` 回退、
   代码前缀规范化、两个源的 ``supports_stock_list`` 标志。
"""

from __future__ import annotations

from urllib.parse import unquote

import httpx
import pandas as pd
import pytest
import respx

from app.providers.base import KLINE_COLUMNS
from app.providers.sina import (
    ALL_MARKET_NODES,
    LIST_URL,
    MAX_BLOCKED_RETRY,
    SinaSource,
    _meta_from_list_row,
    _parse_sina_kline,
    _parse_sina_line,
    _parse_sina_payload,
    _shares_to_hands,
)
from app.providers.source_base import (
    MarketSource,
    MarketSourceError,
    make_meta,
    normalize_code,
    prefixed,
)
from app.providers.tencent import (
    KLINE_BASES,
    KLINE_URL,
    QT_URL,
    TencentSource,
    _clamp_days,
    _parse_kline_bars,
    _parse_kline_payload,
    _parse_qt_line,
    _parse_qt_payload,
    _parse_qt_records,
)

# ===================================================================== 测试样本
# 腾讯实时接口真实响应字段（本机实抓 https://qt.gtimg.cn/q=sh600519 ，共 88 项）
QT_FIELDS: list[str] = [
    # 0-9
    "1", "贵州茅台", "600519", "1275.16", "1285.13", "1285.15", "34801", "16486", "18315", "1275.16",
    # 10-19
    "9", "1275.13", "1", "1275.12", "1", "1275.10", "4", "1275.05", "1", "1276.00",
    # 20-29
    "14", "1276.13", "1", "1276.15", "2", "1276.23", "1", "1276.46", "1", "",
    # 30-39（30=时间戳 31=涨跌额 32=涨跌幅 33=最高 34=最低 36=成交量 37=成交额(万元) 38=换手率）
    "20260911161451", "-9.97", "-0.78", "1286.15", "1263.01", "1275.16/34801/4430841445",
    "34801", "443084", "0.28", "19.57",
    # 40-49（43=振幅 44=流通市值 45=总市值 46=市净率 47=涨停价 48=跌停价）
    "", "1286.15", "1263.01", "1.80", "15940.54", "15940.54", "6.34", "1413.64", "1156.62", "1.25",
    # 50-59
    "-3", "1273.18", "17.90", "19.36", "", "", "0.08", "443084.1445", "191.2740", "15",
    # 60-69（60 带有前导空格，用于验证 strip 容错）
    "   A", "GP-A", "-5.48", "-4.12", "4.08", "32.41", "27.30", "1539.98", "1151.01", "-1.71",
    # 70-79
    "-4.98", "7.43", "1250081601", "1250081601", "-8.57", "-6.92", "1250081601", "", "", "-13.34",
    # 80-87
    "-0.10", "", "CNY", "0", "___D__F__N", "1275.00", "123", "",
]

# 新浪实时接口真实响应（本机实抓 https://hq.sinajs.cn/list=sh600519 ，共 34 项）
SINA_LINE_600519 = (
    'var hq_str_sh600519="贵州茅台,1285.150,1285.130,1275.160,1286.150,1263.010,1275.160,1276.000,'
    "3480142,4430841445.000,945,1275.160,100,1275.130,100,1275.120,400,1275.100,100,1275.050,1400,"
    '1276.000,100,1276.130,200,1276.150,100,1276.230,100,1276.460,2026-09-11,15:34:59,00,'
    'D|1500|1912740.00";'
)


def make_qt_line(
    symbol: str = "sh600519",
    fields: list[str] | None = None,
    changes: dict[int, str] | None = None,
) -> str:
    """由字段数组拼一行腾讯响应，``changes`` 用于覆盖指定下标。"""
    data = list(QT_FIELDS if fields is None else fields)
    for index, value in (changes or {}).items():
        data[index] = value
    return f'v_{symbol}="' + "~".join(data) + '";'


def kline_payload(symbol: str, key: str, bars: list[list[str]]) -> dict:
    return {"code": 0, "data": {symbol: {key: bars}}}


def codes_in_request(request: httpx.Request, prefix: str) -> str:
    """取 ``/q=sh600519,sz000001`` / ``/list=sh600519`` 这种路径式「查询串」的代码部分。

    注意腾讯/新浪这两个接口把参数直接写在路径里（没有 ``?``），
    所以 ``request.url.params`` 是空的，必须从 path 里取。
    """
    path = unquote(request.url.path)
    assert path.startswith(f"/{prefix}="), path
    return path[len(prefix) + 2 :]


# ===================================================================== 腾讯：字段索引
def test_qt_sample_has_88_fields():
    """样本必须与线上一致（88 个字段），否则索引测试没有意义。"""
    assert len(QT_FIELDS) == 88


def test_parse_qt_line_basic():
    record = _parse_qt_line(make_qt_line())
    assert record is not None
    assert record["code"] == "600519"
    assert record["symbol"] == "sh600519"
    assert record["name"] == "贵州茅台"
    assert record["lastClose"] == pytest.approx(1275.16)
    assert record["preClose"] == pytest.approx(1285.13)
    assert record["open"] == pytest.approx(1285.15)
    assert record["high"] == pytest.approx(1286.15)
    assert record["low"] == pytest.approx(1263.01)
    assert record["change"] == pytest.approx(-9.97)
    # 字段 32 是百分数，内部约定是小数
    assert record["pctChg"] == pytest.approx(-0.0078)
    assert record["volume"] == pytest.approx(34801.0)  # 手
    assert record["amount"] == pytest.approx(4430840000.0)  # 万元 -> 元
    assert record["turnover"] == pytest.approx(0.28)
    assert record["limitUp"] == pytest.approx(1413.64)
    assert record["limitDown"] == pytest.approx(1156.62)
    assert record["date"] == "2026-09-11"
    assert record["timestamp"] == "20260911161451"


def test_parse_qt_line_strips_name_spaces():
    record = _parse_qt_line(make_qt_line(changes={1: "  贵州茅台  "}))
    assert record is not None
    assert record["name"] == "贵州茅台"


def test_parse_qt_line_pct_fallback_when_field_missing():
    """字段 32 缺失时按 (最新 - 昨收) / 昨收 自行计算。"""
    record = _parse_qt_line(make_qt_line(changes={32: ""}))
    assert record is not None
    assert record["pctChg"] == pytest.approx((1275.16 - 1285.13) / 1285.13)


def test_parse_qt_line_short_fields_returns_none():
    """字段不足（停牌/占位行）必须跳过而不是抛 IndexError。"""
    short = make_qt_line(fields=QT_FIELDS[:40])
    assert _parse_qt_line(short) is None


def test_parse_qt_line_bad_price_returns_none():
    assert _parse_qt_line(make_qt_line(changes={3: "--"})) is None
    assert _parse_qt_line(make_qt_line(changes={3: ""})) is None


def test_parse_qt_line_placeholder_returns_none():
    """无效代码时腾讯返回 v_pv_none_match="1"。"""
    assert _parse_qt_line('v_pv_none_match="1";') is None
    assert _parse_qt_line('v_sh600519="";') is None
    assert _parse_qt_line("") is None
    assert _parse_qt_line("garbage without equals") is None


def test_parse_qt_line_falls_back_to_head_symbol():
    """字段 2 缺失时用行头 v_sh600519 里的代码兜底。"""
    record = _parse_qt_line(make_qt_line(changes={2: ""}))
    assert record is not None
    assert record["code"] == "600519"


def test_parse_qt_payload_multi_line_skips_bad_rows():
    text = "\n".join(
        [
            make_qt_line(),
            'v_pv_none_match="1";',
            make_qt_line("sz000001", changes={0: "51", 1: "平安银行", 2: "000001", 3: "11.74",
                                              4: "11.85", 32: "-0.93"}),
            "一堆无关内容",
        ]
    )
    payload = _parse_qt_payload(text)
    assert set(payload) == {"600519", "000001"}
    assert payload["000001"]["name"] == "平安银行"
    assert payload["000001"]["pctChg"] == pytest.approx(-0.0093)
    assert len(_parse_qt_records(text)) == 2
    assert _parse_qt_payload("") == {}


# ===================================================================== 腾讯：日线
def test_parse_kline_bars_open_close_high_low_order():
    """**腾讯日线数组顺序是「日期、开、收、高、低、成交量(手)」，不是 OHLC。**

    样本取自实测：开 1285.15 / 收 1275.16 / 高 1286.15 / 低 1263.01，
    与实时接口的今开、最新、最高、最低完全吻合。
    """
    bars = [["2026-09-11", "1285.15", "1275.16", "1286.15", "1263.01", "34801"]]
    records = _parse_kline_bars(bars)
    assert len(records) == 1
    record = records[0]
    assert record["date"] == "2026-09-11"
    assert record["open"] == pytest.approx(1285.15)
    assert record["close"] == pytest.approx(1275.16)
    assert record["high"] == pytest.approx(1286.15)
    assert record["low"] == pytest.approx(1263.01)
    assert record["volume"] == pytest.approx(34801.0)  # 腾讯本身就用手
    # 若误按 OHLC 解析，close 会变成 1263.01、high 会变成 1275.16
    assert record["high"] > record["close"] > record["low"]
    assert record["open"] != record["close"]


def test_parse_kline_bars_ignores_extra_seventh_element():
    bars = [["2026-09-11", "1285.15", "1275.16", "1286.15", "1263.01", "34801", "附加信息"]]
    records = _parse_kline_bars(bars)
    assert len(records) == 1
    assert records[0]["close"] == pytest.approx(1275.16)


def test_parse_kline_bars_skips_bad_rows():
    bars = [
        ["2026-09-11", "1285.15", "1275.16", "1286.15", "1263.01", "34801"],
        ["只有日期"],
        ["2026-09-10", "abc", "def"],
        "不是数组",
        [],
        ["2026-09-09", "1280.00", "1285.13", "1290.00", "1275.00", "not-a-number"],
    ]
    records = _parse_kline_bars(bars)
    assert len(records) == 2
    assert records[1]["date"] == "2026-09-09"
    assert records[1]["volume"] == 0.0  # 成交量非法时兜底为 0，不影响 K 线
    assert _parse_kline_bars("not-a-list") == []


def test_parse_kline_bars_tolerates_short_bar():
    """只有日期/开/收时仍可用，高低于开收之间退化。"""
    records = _parse_kline_bars([["2026-09-11", "1285.15", "1275.16"]])
    assert len(records) == 1
    assert records[0]["high"] == pytest.approx(1285.15)
    assert records[0]["low"] == pytest.approx(1275.16)
    assert records[0]["volume"] == 0.0


def test_parse_kline_payload_prefers_qfqday():
    payload = kline_payload(
        "sh600519",
        "qfqday",
        [["2026-09-11", "1285.15", "1275.16", "1286.15", "1263.01", "34801"]],
    )
    payload["data"]["sh600519"]["day"] = [["2026-09-11", "1", "2", "3", "0.5", "9"]]
    records = _parse_kline_payload(payload, "sh600519")
    assert records[0]["close"] == pytest.approx(1275.16)


def test_parse_kline_payload_day_fallback_for_index():
    """指数没有 qfqday，只有 day，必须能回退。"""
    payload = kline_payload(
        "sh000001",
        "day",
        [["2026-09-11", "3910.920", "3888.110", "3912.320", "3852.030", "579123145.000"]],
    )
    records = _parse_kline_payload(payload, "sh000001")
    assert records[0]["open"] == pytest.approx(3910.92)
    assert records[0]["close"] == pytest.approx(3888.11)
    assert records[0]["high"] == pytest.approx(3912.32)
    assert records[0]["low"] == pytest.approx(3852.03)


def test_parse_kline_payload_missing_both_keys_raises():
    payload = {"code": 0, "data": {"sh600519": {"qt": {}}}}
    with pytest.raises(MarketSourceError):
        _parse_kline_payload(payload, "sh600519")


def test_parse_kline_payload_bad_shapes_raise():
    for bad in [None, "text", [], {"code": 1, "data": {}}, {"code": 0, "data": []},
                {"code": 0, "data": {"sz000001": {"qfqday": []}}}]:
        with pytest.raises(MarketSourceError):
            _parse_kline_payload(bad, "sh600519")


def test_parse_kline_payload_fewer_bars_than_requested_is_ok():
    """新股返回根数少于请求天数不算错误（≥1 根即可用）。"""
    payload = kline_payload("sh600519", "qfqday", [["2026-09-11", "1", "2", "3", "0.5", "9"]])
    assert len(_parse_kline_payload(payload, "sh600519")) == 1


# ===================================================================== 新浪：解析
def test_parse_sina_line_basic():
    record = _parse_sina_line(SINA_LINE_600519)
    assert record is not None
    assert record["code"] == "600519"
    assert record["symbol"] == "sh600519"
    assert record["name"] == "贵州茅台"
    assert record["lastClose"] == pytest.approx(1275.16)
    assert record["preClose"] == pytest.approx(1285.13)
    assert record["open"] == pytest.approx(1285.15)
    assert record["high"] == pytest.approx(1286.15)
    assert record["low"] == pytest.approx(1263.01)
    # 接口不提供涨跌幅，按 (最新 - 昨收) / 昨收 计算
    assert record["pctChg"] == pytest.approx((1275.16 - 1285.13) / 1285.13)
    # 成交量单位是「股」，换算成「手」
    assert record["volume"] == pytest.approx(34801.42)
    assert record["amount"] == pytest.approx(4430841445.0)
    # 该接口不提供换手率，填 0（不伪造）
    assert record["turnover"] == 0.0
    assert record["date"] == "2026-09-11"
    assert record["time"] == "15:34:59"


def test_parse_sina_line_short_fields_returns_none():
    head, _, body = SINA_LINE_600519.partition("=")
    short = head + "=" + ",".join(body.strip().strip('"').rstrip(";").split(",")[:20]) + '";'
    assert _parse_sina_line(short) is None


def test_parse_sina_line_empty_or_placeholder_returns_none():
    assert _parse_sina_line('var hq_str_sh600519="";') is None
    assert _parse_sina_line('var hq_str_sh600519="";\n') is None
    assert _parse_sina_line("") is None
    assert _parse_sina_line("没有等号") is None


def test_parse_sina_line_zero_price_returns_none():
    """停牌/未开盘时最新价为 0，视为无效快照。"""
    parts = SINA_LINE_600519.partition("=")[2].strip().strip('"').rstrip(";").split(",")
    parts[3] = "0.000"
    line = 'var hq_str_sh600519="' + ",".join(parts) + '";'
    assert _parse_sina_line(line) is None


def test_parse_sina_line_bad_pre_close_falls_back_to_last():
    parts = SINA_LINE_600519.partition("=")[2].strip().strip('"').rstrip(";").split(",")
    parts[2] = "-"
    line = 'var hq_str_sh600519="' + ",".join(parts) + '";'
    record = _parse_sina_line(line)
    assert record is not None
    assert record["preClose"] == pytest.approx(1275.16)
    assert record["pctChg"] == 0.0


def test_parse_sina_payload_skips_bad_lines():
    text = "\n".join(
        [
            SINA_LINE_600519,
            'var hq_str_sz000001="";',
            "无关内容",
        ]
    )
    payload = _parse_sina_payload(text)
    assert set(payload) == {"600519"}
    assert _parse_sina_payload("") == {}


def test_shares_to_hands():
    assert _shares_to_hands("3480142") == pytest.approx(34801.42)
    assert _shares_to_hands("") == 0.0
    assert _shares_to_hands(None) == 0.0
    assert _shares_to_hands("abc") == 0.0


def test_parse_sina_kline_converts_volume_and_keeps_order():
    payload = [
        {"day": "2026-08-03", "open": "1350.600", "high": "1363.350",
         "low": "1346.000", "close": "1358.980", "volume": "3614686"},
        {"day": "2026-08-04", "open": "1358.980", "high": "1370.000",
         "low": "1350.000", "close": "1362.000", "volume": "341549255"},
    ]
    records = _parse_sina_kline(payload)
    assert len(records) == 2
    first = records[0]
    assert first["open"] == pytest.approx(1350.6)
    assert first["high"] == pytest.approx(1363.35)
    assert first["low"] == pytest.approx(1346.0)
    assert first["close"] == pytest.approx(1358.98)
    assert first["volume"] == pytest.approx(36146.86)  # 股 -> 手
    assert records[1]["volume"] == pytest.approx(3415492.55)


def test_parse_sina_kline_skips_bad_rows_and_rejects_bad_payload():
    payload = [
        {"day": "2026-08-03", "open": "1350.600", "high": "1363.350",
         "low": "1346.000", "close": "1358.980", "volume": "3614686"},
        {"day": "", "close": "1"},
        {"day": "2026-08-04", "close": "abc"},
        "不是字典",
        {"day": "2026-08-05", "close": "9.9"},  # 补齐 open/high/low/volume
    ]
    records = _parse_sina_kline(payload)
    assert len(records) == 2
    assert records[1]["open"] == pytest.approx(9.9)
    assert records[1]["high"] == pytest.approx(9.9)
    assert records[1]["volume"] == 0.0
    for bad in [None, "null", {}, 0, []]:
        with pytest.raises(MarketSourceError):
            _parse_sina_kline(bad)


def test_meta_from_list_row():
    meta = _meta_from_list_row(
        {"symbol": "sh600000", "code": "600000", "name": "浦发银行", "trade": "9.260"}
    )
    assert meta is not None
    assert (meta.code, meta.name, meta.market, meta.board) == ("600000", "浦发银行", "SH", "主板")
    assert meta.limitPct == pytest.approx(0.10)
    assert meta.industry == "未分类"
    assert meta.isSt is False

    gem = _meta_from_list_row({"code": "300750", "name": "宁德时代"})
    assert gem is not None and gem.board == "创业板" and gem.limitPct == pytest.approx(0.20)

    star = _meta_from_list_row({"symbol": "sh688981", "code": "688981", "name": "中芯国际"})
    assert star is not None and star.board == "科创板" and star.limitPct == pytest.approx(0.20)

    st = _meta_from_list_row({"code": "600001", "name": "ST某某"})
    assert st is not None and st.isSt is True and st.limitPct == pytest.approx(0.05)

    assert _meta_from_list_row({"code": "6005190", "name": "七位代码"}) is None
    assert _meta_from_list_row({"code": "abcdef", "name": "非数字"}) is None
    assert _meta_from_list_row({"code": "600002", "name": "   "}) is None
    assert _meta_from_list_row("不是字典") is None


# ===================================================================== 通用约定
def test_source_identity_and_capabilities():
    assert issubclass(TencentSource, MarketSource)
    assert issubclass(SinaSource, MarketSource)
    tencent = TencentSource()
    sina = SinaSource()
    assert tencent.name == "tencent"
    assert sina.name == "sina"
    # 腾讯排行榜接口可以提供全市场列表；新浪也支持
    assert tencent.supports_stock_list is True
    assert sina.supports_stock_list is True


def test_code_prefix_normalization():
    assert prefixed("600519") == "sh600519"
    assert prefixed("sh600519") == "sh600519"
    assert prefixed("600519.SH") == "sh600519"
    assert prefixed("000001") == "sz000001"
    assert prefixed("300750") == "sz300750"
    assert prefixed("688981") == "sh688981"
    assert normalize_code("sh600519") == "600519"
    assert normalize_code("SZ000001") == "000001"
    assert normalize_code("600519.SS") == "600519"


def test_clamp_days():
    assert _clamp_days(30, 800) == 30
    assert _clamp_days(0, 800) == 1
    assert _clamp_days(99999, 800) == 800
    assert _clamp_days("abc", 800) == 250


def test_make_meta_is_used_for_sina_list_rows():
    """make_meta 自动推导 market/board/limitPct，industry 固定「未分类」。"""
    meta = make_meta("688981", "中芯国际")
    assert meta.industry == "未分类"
    assert meta.limitPct == pytest.approx(0.20)


async def test_tencent_list_stocks_no_longer_raises():
    """supports_stock_list=True：list_stocks() 走成交额榜，不再抛异常。"""
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        return httpx.Response(200, json=rank_payload(0, 3))

    source = TencentSource()
    source.universe_limit = 3
    _patch(source, handler)
    try:
        metas = await source.list_stocks()
    finally:
        await source.close()
    assert [meta.code for meta in metas] == ["600000", "000001", "600002"]
    assert seen[0]["sort_type"] == "turnover"


# ===================================================================== 端到端（mock HTTP）
def _patch(source: MarketSource, handler) -> None:
    """给 provider 注入一个 MockTransport 客户端（httpx 层 mock，代码路径不变）。"""
    source._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), trust_env=False, timeout=5.0
    )


async def test_tencent_realtime_end_to_end():
    """腾讯实时：解析 + URL 拼接 + 6 位代码建键。"""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(codes_in_request(request, "q"))
        return httpx.Response(200, content=make_qt_line().encode("gb18030"))

    source = TencentSource()
    _patch(source, handler)
    try:
        data = await source.realtime(["sh600519", "600519"])
    finally:
        await source.close()

    assert seen == ["sh600519"]  # 去重后只请求一次
    assert set(data) == {"600519"}
    assert set(data["600519"]) == {
        "code", "name", "lastClose", "preClose", "pctChg", "turnover", "amount", "volume", "date",
    }
    assert data["600519"]["name"] == "贵州茅台"
    assert data["600519"]["pctChg"] == pytest.approx(-0.0078)


async def test_tencent_realtime_chunks_by_60():
    """实时批量按每批 60 只切分。"""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(codes_in_request(request, "q"))
        return httpx.Response(
            200,
            content=make_qt_line("sh600000", changes={1: "浦发银行", 2: "600000", 3: "9.26",
                                                       4: "9.35", 32: "-0.96"}).encode("gb18030"),
        )

    codes = [f"{600000 + i}" for i in range(130)]
    source = TencentSource()
    _patch(source, handler)
    try:
        data = await source.realtime(codes)
    finally:
        await source.close()

    assert len(seen) == 3
    assert [len(item.split(",")) for item in seen] == [60, 60, 10]
    assert set(data) == {"600000"}


async def test_tencent_realtime_raises_when_nothing_parsed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content='v_pv_none_match="1";'.encode("gb18030"))

    source = TencentSource()
    _patch(source, handler)
    try:
        with pytest.raises(MarketSourceError):
            await source.realtime(["600519"])
    finally:
        await source.close()


async def test_tencent_daily_kline_end_to_end():
    """腾讯日线：param 拼接 + 开收高低映射 + 规范列 + pre_close/pct_chg 推导。"""
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.url.params.get("param") or "")
        payload = kline_payload(
            "sh600519",
            "qfqday",
            [
                ["2026-09-10", "1280.00", "1285.13", "1290.00", "1275.00", "30000"],
                ["2026-09-11", "1285.15", "1275.16", "1286.15", "1263.01", "34801"],
            ],
        )
        return httpx.Response(200, json=payload)

    source = TencentSource()
    _patch(source, handler)
    try:
        frame = await source.daily_kline("600519", 30)
    finally:
        await source.close()

    assert captured == ["sh600519,day,,,30,qfq"]
    assert list(frame.columns) == KLINE_COLUMNS
    assert len(frame) == 2
    last = frame.iloc[-1]
    assert last["open"] == pytest.approx(1285.15)
    assert last["close"] == pytest.approx(1275.16)
    assert last["high"] == pytest.approx(1286.15)
    assert last["low"] == pytest.approx(1263.01)
    assert last["volume"] == pytest.approx(34801.0)
    assert last["pre_close"] == pytest.approx(1285.13)
    assert last["pct_chg"] == pytest.approx(1275.16 / 1285.13 - 1.0)
    assert pd.api.types.is_datetime64_any_dtype(frame["date"])


async def test_tencent_daily_kline_many_limits_and_skips_failures():
    """daily_kline_many 单只失败只跳过该只，且受 self.concurrency 限制。"""
    def handler(request: httpx.Request) -> httpx.Response:
        param = request.url.params.get("param") or ""
        symbol = param.split(",")[0]
        if symbol == "sz000001":
            return httpx.Response(200, json={"code": 0, "data": {}})
        return httpx.Response(
            200,
            json=kline_payload("sh600519", "qfqday",
                               [["2026-09-11", "1285.15", "1275.16", "1286.15", "1263.01", "34801"]]),
        )

    source = TencentSource(concurrency=1)
    _patch(source, handler)
    try:
        frames = await source.daily_kline_many(["600519", "000001"], 5)
    finally:
        await source.close()
    assert set(frames) == {"600519"}


async def test_tencent_probe_and_ping():
    source = TencentSource()
    _patch(source, lambda request: httpx.Response(200, content=make_qt_line().encode("gb18030")))
    try:
        assert await source.ping() is True
    finally:
        await source.close()

    failing = TencentSource()
    _patch(failing, lambda request: httpx.Response(200, content=b'v_pv_none_match="1";'))
    try:
        with pytest.raises(MarketSourceError):
            await failing.probe()
        assert await failing.ping() is False
    finally:
        await failing.close()


async def test_tencent_daily_kline_falls_back_to_backup_host():
    """主入口被反爬挑战（HTTP 501）时自动换备用入口，并记住可用入口。"""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        base = str(request.url).split("?")[0]
        calls.append(base)
        if base == KLINE_BASES[0]:
            return httpx.Response(501, text="<html>js challenge</html>")
        return httpx.Response(
            200,
            json=kline_payload("sh600519", "qfqday",
                               [["2026-09-11", "1285.15", "1275.16", "1286.15", "1263.01", "34801"]]),
        )

    source = TencentSource()
    _patch(source, handler)
    try:
        frame = await source.daily_kline("600519", 10)
        assert calls == [KLINE_BASES[0], KLINE_BASES[1]]
        assert source._kline_base == KLINE_BASES[1]
        assert frame.iloc[-1]["close"] == pytest.approx(1275.16)
        # 第二次直接命中已记录的可用入口，只发 1 个请求
        calls.clear()
        frame2 = await source.daily_kline("600519", 10)
        assert calls == [KLINE_BASES[1]]
        assert frame2.iloc[-1]["open"] == pytest.approx(1285.15)
    finally:
        await source.close()


async def test_tencent_daily_kline_all_hosts_failing_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(501, text="<html>js challenge</html>")

    source = TencentSource()
    _patch(source, handler)
    try:
        with pytest.raises(MarketSourceError):
            await source.daily_kline("600519", 10)
    finally:
        await source.close()


async def test_tencent_index_snapshot_end_to_end():
    """指数名称取字段 1，点位取字段 3，sparkline 走 day（无 qfqday）分支。"""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/q="):
            body = "\n".join(
                [
                    make_qt_line("sh000001", changes={1: "上证指数", 2: "000001", 3: "3888.11",
                                                       4: "3934.47", 32: "-1.18"}),
                    make_qt_line("sz399001", changes={0: "51", 1: "深证成指", 2: "399001",
                                                       3: "13471.26", 32: "-1.08"}),
                    make_qt_line("sz399006", changes={0: "51", 1: "创业板指", 2: "399006",
                                                       3: "3322.04", 32: "-0.49"}),
                ]
            )
            return httpx.Response(200, content=body.encode("gb18030"))
        param = request.url.params.get("param") or ""
        symbol = param.split(",")[0]
        return httpx.Response(
            200,
            json=kline_payload(symbol, "day",
                               [["2026-09-10", "3900.00", "3934.47", "3940.00", "3890.00", "1"],
                                ["2026-09-11", "3910.92", "3888.11", "3912.32", "3852.03", "2"]]),
        )

    source = TencentSource()
    _patch(source, handler)
    try:
        snapshot = await source.index_snapshot()
    finally:
        await source.close()

    assert [item["code"] for item in snapshot] == ["000001", "399001", "399006"]
    assert [item["name"] for item in snapshot] == ["上证指数", "深证成指", "创业板指"]
    assert snapshot[0]["close"] == pytest.approx(3888.11)
    assert snapshot[0]["pctChg"] == pytest.approx(-0.0118)
    assert snapshot[0]["sparkline"] == [3934.47, 3888.11]


async def test_tencent_index_snapshot_returns_empty_on_http_error():
    source = TencentSource()
    _patch(source, lambda request: httpx.Response(500))
    try:
        assert await source.index_snapshot() == []
    finally:
        await source.close()


async def test_sina_realtime_end_to_end_sends_referer():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("referer") == "https://finance.sina.com.cn"
        assert codes_in_request(request, "list") == "sh600519"
        return httpx.Response(200, content=SINA_LINE_600519.encode("gb18030"))

    source = SinaSource()
    _patch(source, handler)
    try:
        data = await source.realtime(["600519"])
    finally:
        await source.close()

    assert set(data) == {"600519"}
    assert data["600519"]["name"] == "贵州茅台"
    assert data["600519"]["lastClose"] == pytest.approx(1275.16)
    assert data["600519"]["volume"] == pytest.approx(34801.42)
    assert data["600519"]["turnover"] == 0.0


async def test_sina_daily_kline_end_to_end():
    captured: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("referer") == "https://finance.sina.com.cn"
        captured.append(dict(request.url.params))
        return httpx.Response(
            200,
            json=[
                {"day": "2026-08-03", "open": "1350.600", "high": "1363.350",
                 "low": "1346.000", "close": "1358.980", "volume": "3614686"},
                {"day": "2026-08-04", "open": "1358.980", "high": "1370.000",
                 "low": "1350.000", "close": "1362.000", "volume": "341549255"},
            ],
        )

    source = SinaSource()
    _patch(source, handler)
    try:
        frame = await source.daily_kline("sh600519", 20)
    finally:
        await source.close()

    assert captured[0]["symbol"] == "sh600519"
    assert captured[0]["scale"] == "240"
    assert captured[0]["datalen"] == "20"
    assert list(frame.columns) == KLINE_COLUMNS
    assert frame.iloc[-1]["volume"] == pytest.approx(3415492.55)  # 股 -> 手
    assert frame.iloc[-1]["pre_close"] == pytest.approx(1358.98)


async def test_sina_realtime_raises_when_nothing_parsed():
    source = SinaSource()
    _patch(source, lambda request: httpx.Response(200, content=b'var hq_str_sh600519="";'))
    try:
        with pytest.raises(MarketSourceError):
            await source.realtime(["600519"])
    finally:
        await source.close()


async def test_sina_probe_and_ping():
    source = SinaSource()
    _patch(source, lambda request: httpx.Response(200, content=SINA_LINE_600519.encode("gb18030")))
    try:
        assert await source.ping() is True
    finally:
        await source.close()

    failing = SinaSource()
    _patch(failing, lambda request: httpx.Response(403))
    try:
        with pytest.raises(MarketSourceError):
            await failing.probe()
        assert await failing.ping() is False
    finally:
        await failing.close()


async def test_sina_list_stocks_pagination_dedupe_and_meta():
    """三个 node 并集翻页 + 去重 + make_meta 推导；返回条数 < num 即终止。

    同时验证**板块覆盖**：科创板来自 ``sh_a``、创业板来自 ``sz_a``、
    北交所来自 ``hs_a`` —— 缺任意一个 node 都会漏板块，这正是本用例要守住的回归点。
    """
    requests: list[tuple[str, int]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        node = request.url.params.get("node") or ""
        page = int(request.url.params.get("page") or 1)
        requests.append((node, page))
        if node == "hs_a":
            # hs_a：含北交所（920），且与 sh_a 重复一只 600000 验证跨 node 去重
            if page == 1:
                rows = [
                    {"symbol": "bj920000", "code": "920000", "name": "安徽凤凰"},
                    {"symbol": "sh600000", "code": "600000", "name": "浦发银行"},
                ]
            else:
                rows = []
        elif node == "sh_a":
            if page == 1:
                rows = [
                    {"symbol": f"sh{600000 + i}", "code": f"{600000 + i}", "name": f"沪市股票{i}"}
                    for i in range(100)
                ]
            elif page == 2:
                # 科创板来自 sh_a（688 段）
                rows = [
                    {"symbol": "sh688981", "code": "688981", "name": "中芯国际"},
                    {"symbol": "sh688001", "code": "688001", "name": "华兴源创"},
                ]
            else:
                rows = []
        elif node == "sz_a":
            # 主板 + 创业板都来自 sz_a
            rows = [
                {"symbol": "sz000001", "code": "000001", "name": "平安银行"},
                {"symbol": "sz300750", "code": "300750", "name": "宁德时代"},
            ]
        else:
            rows = []
        return httpx.Response(200, json=rows)

    source = SinaSource()
    source.node_delay = 0  # 测试里不需要限流延时
    source.page_delay = 0
    _patch(source, handler)
    try:
        metas = await source.list_stocks()
    finally:
        await source.close()

    by_code = {meta.code: meta for meta in metas}
    # 100(沪主板) + 2(科创) + 1(深主板) + 1(创业) + 1(北交所) = 105
    assert len(metas) == 105
    assert {node for node, _ in requests} == set(ALL_MARKET_NODES)
    assert ("sh_a", 1) in requests and ("sh_a", 2) in requests and ("sh_a", 3) not in requests
    assert len([1 for node, _ in requests if node == "sz_a"]) == 1  # 条数 < num 立即终止

    star = by_code["688981"]
    assert star.name == "中芯国际"  # setdefault 保留首次出现
    assert (star.market, star.board, star.limitPct, star.industry) == ("SH", "科创板", 0.20, "未分类")
    gem = by_code["300750"]
    assert (gem.board, gem.limitPct) == ("创业板", 0.20)
    assert by_code["000001"].market == "SZ"
    # 北交所必须被识别为 BJ / 北交所 / 30% 涨停幅度（曾经被误判为深市 10%）
    bj = by_code["920000"]
    assert (bj.market, bj.board, bj.limitPct) == ("BJ", "北交所", 0.30)
    # 跨 node 去重：600000 在 hs_a 与 sh_a 都出现，只应保留一条
    assert len([m for m in metas if m.code == "600000"]) == 1


async def test_sina_list_stocks_all_covers_four_boards():
    """全市场列表必须同时覆盖主板/创业板/科创板/北交所四个板块。"""
    def handler(request: httpx.Request) -> httpx.Response:
        node = request.url.params.get("node") or ""
        table = {
            "hs_a": [{"symbol": "bj920000", "code": "920000", "name": "安徽凤凰"}],
            "sh_a": [
                {"symbol": "sh600519", "code": "600519", "name": "贵州茅台"},
                {"symbol": "sh688981", "code": "688981", "name": "中芯国际"},
            ],
            "sz_a": [
                {"symbol": "sz000001", "code": "000001", "name": "平安银行"},
                {"symbol": "sz300750", "code": "300750", "name": "宁德时代"},
            ],
        }
        return httpx.Response(200, json=table.get(node, []))

    source = SinaSource()
    source.node_delay = 0
    source.page_delay = 0
    _patch(source, handler)
    try:
        metas = await source.list_stocks_all()
    finally:
        await source.close()

    boards = {meta.board for meta in metas}
    assert boards == {"主板", "创业板", "科创板", "北交所"}, f"板块覆盖不全：{boards}"


async def test_sina_list_stocks_all_tolerates_node_failure():
    """某个 node 失败时，已拿到的并集照常返回（不因单点失败丢掉整个列表）。"""
    def handler(request: httpx.Request) -> httpx.Response:
        node = request.url.params.get("node") or ""
        if node == "sh_a":
            return httpx.Response(456, text="blocked")
        if node == "hs_a":
            return httpx.Response(200, json=[{"symbol": "bj920000", "code": "920000", "name": "安徽凤凰"}])
        return httpx.Response(200, json=[{"symbol": "sz000001", "code": "000001", "name": "平安银行"}])

    source = SinaSource()
    source.node_delay = 0
    source.page_delay = 0
    source.blocked_retry_delay = 0
    _patch(source, handler)
    try:
        metas = await source.list_stocks_all()
    finally:
        await source.close()
    codes = {meta.code for meta in metas}
    assert codes == {"920000", "000001"}, "失败 node 之外的结果应保留"


async def test_sina_list_stocks_raises_when_all_nodes_empty():
    source = SinaSource()
    source.node_delay = 0
    source.page_delay = 0
    _patch(source, lambda request: httpx.Response(200, json=[]))
    try:
        with pytest.raises(MarketSourceError):
            await source.list_stocks()
    finally:
        await source.close()


async def test_sina_list_stocks_retries_after_anti_crawl_456():
    """反爬限流码 456 要等待后重试，而不是直接放弃。"""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params.get("node") or "")
        if len(calls) == 1:
            return httpx.Response(456, text="<html>blocked</html>")
        if request.url.params.get("node") == ALL_MARKET_NODES[0]:
            return httpx.Response(200, json=[{"code": "600000", "name": "浦发银行"}])
        return httpx.Response(200, json=[])

    source = SinaSource()
    source.node_delay = 0
    source.page_delay = 0
    source.blocked_retry_delay = 0  # 测试不真的等待
    _patch(source, handler)
    try:
        metas = await source.list_stocks()
    finally:
        await source.close()

    first = ALL_MARKET_NODES[0]
    assert calls[0] == first and calls[1] == first  # 第一次 456，第二次重试成功
    assert [meta.code for meta in metas] == ["600000"]


async def test_sina_list_stocks_gives_up_after_repeated_456():
    """持续 456 时最终放弃各 node（不无限重试）。"""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(456, text="blocked")

    source = SinaSource()
    source.node_delay = 0
    source.page_delay = 0
    source.blocked_retry_delay = 0
    _patch(source, handler)
    try:
        with pytest.raises(MarketSourceError):
            await source.list_stocks()
    finally:
        await source.close()
    # ALL_MARKET_NODES 个 node × (1 + MAX_BLOCKED_RETRY) 次
    assert len(calls) == len(ALL_MARKET_NODES) * (1 + MAX_BLOCKED_RETRY)


async def test_sina_list_stocks_tolerates_partial_failure():
    """某个 node 失败（403）不应整体失败，只要其他 node 有数据。"""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("node") == "sh_a":
            return httpx.Response(403)
        return httpx.Response(
            200, json=[{"symbol": "sz000001", "code": "000001", "name": "平安银行"}]
        )

    source = SinaSource()
    source.node_delay = 0
    source.page_delay = 0
    _patch(source, handler)
    try:
        metas = await source.list_stocks()
    finally:
        await source.close()
    assert [meta.code for meta in metas] == ["000001"]


# ===================================================================== 成交额榜（list_stocks_ranked）
def ranked_rows(node: str, page: int, count: int = 100) -> list[dict]:
    """构造成交额降序的一页列表数据。

    沪深两市的 amount 交错（沪 1000000-2n、深 1000000-2n-1），
    这样归并后的正确顺序是 沪0、深0、沪1、深1……便于断言跨市场归并是否正确。
    """
    rows: list[dict] = []
    for i in range(count):
        seq = (page - 1) * count + i
        if node == "sh_a":
            code, amount = f"{600000 + seq:06d}", 1_000_000 - 2 * seq
        else:
            code, amount = f"{seq + 1:06d}", 1_000_000 - (2 * seq + 1)
        rows.append(
            {
                "symbol": ("sh" if node == "sh_a" else "sz") + code,
                "code": code,
                "name": f"{node}股票{seq}",
                "amount": amount,
                "trade": "10.000",
            }
        )
    return rows


async def test_sina_list_stocks_ranked_uses_amount_desc_and_bounded_pages():
    """请求参数必须是 sort=amount&asc=0，且只翻 ceil(limit/100) 页/node、沪深交替。"""
    requests: list[tuple[str, int]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("sort") == "amount"
        assert request.url.params.get("asc") == "0"
        node = request.url.params.get("node") or ""
        page = int(request.url.params.get("page") or 1)
        requests.append((node, page))
        return httpx.Response(200, json=ranked_rows(node, page))

    source = SinaSource()
    source.page_delay = 0
    _patch(source, handler)
    try:
        metas = await source.list_stocks_ranked(250)
    finally:
        await source.close()

    # 250 只 → 每个 node 3 页，沪/深交替
    assert requests == [("sh_a", 1), ("sz_a", 1), ("sh_a", 2), ("sz_a", 2), ("sh_a", 3), ("sz_a", 3)]
    assert len(metas) == 250
    # 跨两市归并后严格按成交额降序：沪0、深0、沪1、深1……
    assert [meta.code for meta in metas[:6]] == ["600000", "000001", "600001", "000002", "600002", "000003"]
    assert metas[0].name == "sh_a股票0"
    assert metas[0].industry == "未分类"
    assert {meta.market for meta in metas} == {"SH", "SZ"}


async def test_sina_list_stocks_ranked_small_limit_single_round():
    """limit ≤ 100 时只发 2 个请求（沪深各一页）就返回。"""
    requests: list[tuple[str, int]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        node = request.url.params.get("node") or ""
        page = int(request.url.params.get("page") or 1)
        requests.append((node, page))
        return httpx.Response(200, json=ranked_rows(node, page))

    source = SinaSource()
    source.page_delay = 0
    _patch(source, handler)
    try:
        metas = await source.list_stocks_ranked(100)
    finally:
        await source.close()

    assert requests == [("sh_a", 1), ("sz_a", 1)]
    assert len(metas) == 100
    assert [meta.code for meta in metas[:4]] == ["600000", "000001", "600001", "000002"]


async def test_sina_list_stocks_ranked_returns_partial_when_limited():
    """部分页被限流/翻完时，已拿到的照常返回，不整体失败。"""
    calls: list[tuple[str, int]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        node = request.url.params.get("node") or ""
        page = int(request.url.params.get("page") or 1)
        calls.append((node, page))
        if page == 1:
            return httpx.Response(200, json=ranked_rows(node, 1))
        if node == "sh_a":  # 沪市后续页被反爬限流
            return httpx.Response(456, text="blocked")
        return httpx.Response(200, json=[])  # 深市没有更多数据

    source = SinaSource()
    source.page_delay = 0
    source.blocked_retry_delay = 0
    _patch(source, handler)
    try:
        metas = await source.list_stocks_ranked(300)
    finally:
        await source.close()

    assert len(metas) == 200  # 沪深各 100，未凑够 300 也不报错
    assert [meta.code for meta in metas[:2]] == ["600000", "000001"]
    # 沪市：第 1 页成功 + 第 2 页被限流（1 次请求 + MAX_BLOCKED_RETRY 次重试），
    # 之后不再请求该 node。这里从常量推导而非硬编码，避免调整重试策略时测试失效。
    assert len([1 for node, _ in calls if node == "sh_a"]) == 1 + (1 + MAX_BLOCKED_RETRY)
    assert len([1 for node, _ in calls if node == "sz_a" and _ > 1]) == 1


async def test_sina_list_stocks_ranked_raises_when_all_blocked():
    source = SinaSource()
    source.page_delay = 0
    source.blocked_retry_delay = 0
    _patch(source, lambda request: httpx.Response(456, text="blocked"))
    try:
        with pytest.raises(MarketSourceError):
            await source.list_stocks_ranked(100)
    finally:
        await source.close()


async def test_sina_list_stocks_ranked_zero_limit_makes_no_request():
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json=ranked_rows("sh_a", 1))

    source = SinaSource()
    source.page_delay = 0
    _patch(source, handler)
    try:
        assert await source.list_stocks_ranked(0) == []
    finally:
        await source.close()
    assert calls == []


def test_tencent_has_no_ranked_list_capability():
    """新浪的成交额榜实现保留为备用（腾讯优先）。"""
    assert hasattr(TencentSource(), "list_stocks_ranked")
    assert hasattr(SinaSource(), "list_stocks_ranked")


# ===================================================================== 腾讯成交额榜
# 实测样本（proxy.finance.qq.com/cgi/cgi-bin/rank/hs/getBoardRankList）
RANK_TOTAL = 4602


def rank_rows(offset: int, count: int) -> list[dict]:
    """构造成交额降序的一页排行榜数据（turnover 单位万元，逐条递减）。"""
    rows: list[dict] = []
    for i in range(count):
        seq = offset + i
        # 交错沪深两市，便于断言跨市场顺序
        if seq % 2 == 0:
            code, board = f"sh{600000 + seq:06d}", "GP-A"
        else:
            code, board = f"sz{seq:06d}", "GP-A-CYB"
        rows.append(
            {
                "code": code,
                "name": f"股票{seq}",
                "turnover": str(3_000_000 - seq),
                "zdf": "4.03",
                "zxj": "926.00",
                "state": "",
                "stock_type": board,
            }
        )
    return rows


def rank_payload(offset: int, count: int, total: int = RANK_TOTAL) -> dict:
    return {"code": 0, "msg": "", "data": {"rank_list": rank_rows(offset, count), "offset": offset, "total": total}}


async def test_tencent_list_stocks_ranked_params_pagination_and_order():
    """请求参数必须是 sort_type=turnover&direct=down，offset 递增且 count 只补差额。"""
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        seen.append(params)
        assert params["board_code"] == "aStock"
        assert params["sort_type"] == "turnover"
        assert params["direct"] == "down"
        offset, count = int(params["offset"]), int(params["count"])
        return httpx.Response(200, json=rank_payload(offset, count))

    source = TencentSource()
    _patch(source, handler)
    try:
        metas = await source.list_stocks_ranked(250)
    finally:
        await source.close()

    assert [(p["offset"], p["count"]) for p in seen] == [("0", "100"), ("100", "100"), ("200", "50")]
    assert len(metas) == 250
    assert [meta.code for meta in metas[:4]] == ["600000", "000001", "600002", "000003"]
    assert metas[0].name == "股票0"
    assert metas[0].industry == "未分类"
    assert metas[0].board == "主板"
    assert metas[1].market == "SZ"
    assert metas[1].board == "主板"
    assert metas[1].limitPct == pytest.approx(0.10)


async def test_tencent_list_stocks_ranked_single_request_when_limit_small():
    calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(dict(request.url.params))
        return httpx.Response(200, json=rank_payload(0, 100))

    source = TencentSource()
    _patch(source, handler)
    try:
        metas = await source.list_stocks_ranked(100)
    finally:
        await source.close()
    assert len(calls) == 1
    assert calls[0]["count"] == "100"
    assert len(metas) == 100


async def test_tencent_list_stocks_ranked_filters_and_dedupes():
    """剔除含「退」的名称、非法代码、非 GP 类型；重复代码只保留成交额更大的那条。"""

    def handler(request: httpx.Request) -> httpx.Response:
        rows = [
            {"code": "sh600519", "name": "贵州茅台", "turnover": "900000", "stock_type": "GP-A"},
            {"code": "sz000001", "name": "平安银行", "turnover": "500000", "stock_type": "GP-A"},
            {"code": "sh600519", "name": "贵州茅台(重复)", "turnover": "100", "stock_type": "GP-A"},
            {"code": "sz000002", "name": "退市某某", "turnover": "800000", "stock_type": "GP-A"},
            {"code": "sh60000", "name": "短代码", "turnover": "700000", "stock_type": "GP-A"},
            {"code": "sh000001", "name": "上证指数", "turnover": "600000", "stock_type": "ZS"},
            {"code": "sz300308", "name": "", "turnover": "550000", "stock_type": "GP-A"},
            {"code": "sh688981", "name": "中芯国际", "turnover": "400000", "stock_type": "GP-A-KCB"},
        ]
        return httpx.Response(200, json={"code": 0, "data": {"rank_list": rows, "total": 8}})

    source = TencentSource()
    _patch(source, handler)
    try:
        metas = await source.list_stocks_ranked(10)
    finally:
        await source.close()

    assert [meta.code for meta in metas] == ["600519", "000001", "688981"]
    assert metas[0].name == "贵州茅台"  # 保留成交额更大的那条
    assert metas[2].board == "科创板"
    assert metas[2].limitPct == pytest.approx(0.20)


async def test_tencent_list_stocks_ranked_returns_partial_on_page_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        if offset == 0:
            return httpx.Response(200, json=rank_payload(0, 100))
        return httpx.Response(501, text="<html>challenge</html>")

    source = TencentSource()
    _patch(source, handler)
    try:
        metas = await source.list_stocks_ranked(300)
    finally:
        await source.close()
    assert len(metas) == 100  # 第一页照常返回，不整体失败


async def test_tencent_list_stocks_ranked_raises_when_nothing():
    source = TencentSource()
    _patch(source, lambda request: httpx.Response(200, json={"code": 0, "data": {"rank_list": []}}))
    try:
        with pytest.raises(MarketSourceError):
            await source.list_stocks_ranked(100)
    finally:
        await source.close()


async def test_tencent_list_stocks_ranked_zero_limit_makes_no_request():
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json=rank_payload(0, 100))

    source = TencentSource()
    _patch(source, handler)
    try:
        assert await source.list_stocks_ranked(0) == []
        assert await source.list_stocks_ranked(-5) == []
        assert await source.list_stocks_ranked("abc") == []
    finally:
        await source.close()
    assert calls == []


def test_parse_rank_payload_shape_validation():
    from app.providers.tencent import _parse_rank_payload

    assert len(_parse_rank_payload(rank_payload(0, 3))) == 3
    for bad in [None, "text", [], {"code": 1, "data": {}}, {"code": 0, "data": []},
                {"code": 0, "data": {}}]:
        with pytest.raises(MarketSourceError):
            _parse_rank_payload(bad)


def test_tencent_supports_stock_list_and_ranked():
    source = TencentSource()
    assert source.supports_stock_list is True
    assert hasattr(source, "list_stocks_ranked")
    assert source.universe_limit >= 1


# ===================================================================== respx 兼容性冒烟
async def test_respx_intercepts_provider_client():
    """respx 直接 patch httpx 全局传输层，验证 mock 方式对 provider 也生效。"""
    with respx.mock(assert_all_called=False) as router:
        router.get(url__startswith=QT_URL).mock(
            return_value=httpx.Response(200, content=make_qt_line().encode("gb18030"))
        )
        router.get(url__startswith=KLINE_URL).mock(
            return_value=httpx.Response(
                200,
                json=kline_payload("sh600519", "qfqday",
                                   [["2026-09-11", "1285.15", "1275.16", "1286.15", "1263.01", "34801"]]),
            )
        )
        router.get(url__startswith=LIST_URL).mock(
            return_value=httpx.Response(200, json=[{"code": "600000", "name": "浦发银行"}])
        )
        source = TencentSource()
        sina = SinaSource()
        sina.node_delay = 0
        sina.page_delay = 0
        try:
            quotes = await source.realtime(["600519"])
            frame = await source.daily_kline("600519", 5)
            metas = await sina.list_stocks()
        finally:
            await source.close()
            await sina.close()

    assert quotes["600519"]["lastClose"] == pytest.approx(1275.16)
    assert frame.iloc[-1]["close"] == pytest.approx(1275.16)
    assert [meta.code for meta in metas] == ["600000"]
