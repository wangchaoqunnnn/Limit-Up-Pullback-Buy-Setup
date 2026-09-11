"""雅虎财经（Yahoo Finance）适配器单元测试。

全部离线：解析逻辑用**本机实测抓取的真实响应样本**验证，不依赖网络。
样本取自 2026-09-11 实际请求（见 app/providers/yahoo.py 的模块说明），
并与同花顺同一天的日线逐字段比对一致（O/H/L/C/V 全同）。

重点覆盖：
- chart 响应中 timestamp / indicators.quote 的对齐解析；
- **成交量「股 → 手」换算**（雅虎给股，项目内部口径为手）；
- 停牌日 null 行必须跳过，不得用 0 冒充成交价；
- 北交所标的一律抛错（雅虎不提供），从而交给其他源；
- 指数覆盖不足（实测 399006.SZ 只有 1 根）时不得返回残缺曲线；
- 格式非法时抛 MarketSourceError，而不是泄漏 ValueError/IndexError。
"""

from __future__ import annotations

import json

import pytest

from app.providers.source_base import MarketSourceError
from app.providers.yahoo import (
    MIN_INDEX_BARS,
    SPARKLINE_BARS,
    YahooSource,
    _index_from_records,
    _parse_chart,
    yahoo_symbol,
)

# 真实响应样本（贵州茅台，最后三根即 2026-09-09 ~ 09-11）
CHART_SAMPLE = {
    "chart": {
        "result": [
            {
                "meta": {
                    "currency": "CNY",
                    "symbol": "600519.SS",
                    "exchangeName": "SHH",
                    "regularMarketPrice": 1275.16,
                    "regularMarketVolume": 3480142,
                    "timezone": "CST",
                },
                "timestamp": [1788917400, 1789003800, 1789090200],
                "indicators": {
                    "quote": [
                        {
                            "open": [1305.010009765625, 1291.0, 1285.1500244140625],
                            "high": [1309.300048828125, 1294.989990234375, 1286.1500244140625],
                            "low": [1286.6800537109375, 1282.0, 1263.010009765625],
                            "close": [1290.8800048828125, 1285.1300048828125, 1275.1600341796875],
                            "volume": [3222611, 1890022, 3480142],
                        }
                    ]
                },
            }
        ],
        "error": None,
    }
}


class TestSymbol:
    """代码 → 雅虎符号映射。"""

    def test_shanghai_uses_ss(self):
        assert yahoo_symbol("600519") == "600519.SS"
        assert yahoo_symbol("688981") == "688981.SS"

    def test_shenzhen_uses_sz(self):
        assert yahoo_symbol("000001") == "000001.SZ"
        assert yahoo_symbol("300750") == "300750.SZ"

    def test_beijing_is_rejected(self):
        """实测雅虎对 920000.BJ 返回 404，必须提前抛错而不是白跑一次请求。"""
        for code in ("920000", "920046", "430047", "830799"):
            with pytest.raises(MarketSourceError):
                yahoo_symbol(code)

    def test_illegal_code(self):
        with pytest.raises(MarketSourceError):
            yahoo_symbol("")


class TestParseChart:
    """chart 响应解析。"""

    def test_parses_all_rows(self):
        records = _parse_chart(CHART_SAMPLE)
        assert len(records) == 3
        last = records[-1]
        # 与同花顺同一天的数据完全一致（含成交量）
        assert last["date"] == "2026-09-11"
        assert last["open"] == pytest.approx(1285.15, abs=0.01)
        assert last["high"] == pytest.approx(1286.15, abs=0.01)
        assert last["low"] == pytest.approx(1263.01, abs=0.01)
        assert last["close"] == pytest.approx(1275.16, abs=0.01)
        # 雅虎给「股」，必须换算成「手」：3480142 股 = 34801.42 手
        assert last["volume"] == pytest.approx(34801.42, abs=0.01)

    def test_does_not_fabricate_amount(self):
        """雅虎不提供成交额：键不应存在，由框架统一估算，而不是填 0 冒充真实值。"""
        records = _parse_chart(CHART_SAMPLE)
        assert all("amount" not in r for r in records)

    def test_null_rows_are_skipped(self):
        """停牌日雅虎给 null，必须跳过而不是拿 0 当成交价。"""
        payload = json.loads(json.dumps(CHART_SAMPLE))
        q = payload["chart"]["result"][0]["indicators"]["quote"][0]
        q["close"][1] = None
        q["open"][1] = None
        records = _parse_chart(payload)
        assert len(records) == 2
        assert all(r["close"] > 0 for r in records)

    def test_ragged_arrays_do_not_raise(self):
        """数组长度不一致时只截断，不抛 IndexError。"""
        payload = json.loads(json.dumps(CHART_SAMPLE))
        payload["chart"]["result"][0]["timestamp"] = [1788917400, 1789003800, 1789090200, 1789176600]
        records = _parse_chart(payload)
        assert len(records) == 3

    def test_error_payload_raises(self):
        with pytest.raises(MarketSourceError):
            _parse_chart({"chart": {"result": None, "error": {"code": "Not Found"}}})

    def test_empty_result_raises(self):
        with pytest.raises(MarketSourceError):
            _parse_chart({"chart": {"result": [], "error": None}})

    def test_non_dict_raises(self):
        with pytest.raises(MarketSourceError):
            _parse_chart(["not", "a", "dict"])

    def test_all_null_raises(self):
        payload = json.loads(json.dumps(CHART_SAMPLE))
        q = payload["chart"]["result"][0]["indicators"]["quote"][0]
        for key in ("open", "high", "low", "close"):
            q[key] = [None, None, None]
        with pytest.raises(MarketSourceError):
            _parse_chart(payload)


class TestIndex:
    """指数快照。"""

    def test_builds_close_pct_and_sparkline(self):
        records = [{"date": f"2026-01-{i:02d}", "close": 3000.0 + i} for i in range(1, 26)]
        item = _index_from_records("000001", "上证指数", records)
        assert item["code"] == "000001"
        assert item["name"] == "上证指数"
        assert item["close"] == pytest.approx(3025.0)
        assert item["pctChg"] == pytest.approx(3025 / 3024 - 1, abs=1e-6)
        assert len(item["sparkline"]) == SPARKLINE_BARS

    def test_single_bar_has_no_pct(self):
        item = _index_from_records("399006", "创业板指", [{"date": "2026-09-11", "close": 3322.04}])
        assert item["pctChg"] == 0.0
        assert item["sparkline"] == [3322.04]


class TestCapabilities:
    """能力声明：决定它参与哪些操作的故障转移。"""

    def test_declares_limited_capabilities(self):
        source = YahooSource()
        assert source.supports_stock_list is False, "雅虎不提供全市场列表"
        assert source.supports_realtime is False, "逐只取实时会把 30 秒刷新拖成分钟级"
        assert source.supports_index is True
        assert source.name == "yahoo"

    def test_min_index_bars_is_sane(self):
        """实测 399006.SZ 只有 1 根：阈值必须能把它过滤掉。"""
        assert MIN_INDEX_BARS >= 2
