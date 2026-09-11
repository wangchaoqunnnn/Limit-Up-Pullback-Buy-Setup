"""同花顺（10jqka）适配器单元测试。

全部离线：解析逻辑用**本机实测抓取的真实响应样本**直接验证，不依赖网络。
样本取自 2026-09-11 实际请求（见 app/providers/ths.py 的模块说明）。

重点覆盖：
- 日线 11 字段的解析与「股 → 手」成交量换算；
- 单行异常不影响整批（防御性）；
- 分时 payload 取末条为最新价、昨收与涨跌幅换算；
- 数据量不足/格式非法时抛 MarketSourceError 而非泄漏 ValueError/IndexError。
"""

from __future__ import annotations

import pytest

from app.providers.source_base import MarketSourceError
from app.providers.ths import (
    MIN_FIELDS,
    TongHuaShunSource,
    _parse_line_payload,
    _parse_time_payload,
    _unwrap_jsonp,
)

# 真实响应样本（贵州茅台，最后一行即 2026-09-11 当日）
LINE_SAMPLE = (
    'quotebridge_v6_line_hs_600519_01_last({"num":3,"year":{"2026":3},'
    '"data":"20260909,1305.01,1309.30,1286.68,1290.88,3222611,4168500500.00,0.258,,600.00,774528;'
    "20260910,1291.00,1294.99,1282.00,1285.13,1890022,2428698800.00,0.151,,800.00,1028104;"
    '20260911,1285.15,1286.15,1263.01,1275.16,3480142,4430841400.00,0.278,,1500.00,1912740"})'
)

# 真实分时样本（中芯国际）
TIME_SAMPLE = (
    'quotebridge_v6_time_hs_688981_last({"hs_688981":{"name":"\\u4e2d\\u82af\\u56fd\\u9645",'
    '"open":0,"stop":0,"isTrading":0,"rt":"0930-1130,1300-1500,1505-1530",'
    '"pre":"119.18","date":"20260911",'
    '"data":"0930,118.00,41150503,118.000,32020;0931,117.50,93108447,117.800,72680;'
    '1459,117.41,88664670,117.500,69400"}})'
)


class TestJsonp:
    def test_unwrap_jsonp(self):
        obj = _unwrap_jsonp('cb({"a":1})')
        assert obj == {"a": 1}

    def test_unwrap_plain_json(self):
        assert _unwrap_jsonp('{"a":2}') == {"a": 2}

    def test_unwrap_invalid_raises(self):
        with pytest.raises(MarketSourceError):
            _unwrap_jsonp("not json at all")


class TestLinePayload:
    """日线解析。"""

    def test_parses_all_rows(self):
        records = _parse_line_payload(LINE_SAMPLE)
        assert len(records) == 3
        assert records[0]["date"] == "2026-09-09"
        assert records[-1]["date"] == "2026-09-11"

    def test_ohlc_mapping_correct(self):
        """字段顺序必须是 日期,开,高,低,收（最容易写错的地方）。"""
        last = _parse_line_payload(LINE_SAMPLE)[-1]
        assert last["open"] == pytest.approx(1285.15)
        assert last["high"] == pytest.approx(1286.15)
        assert last["low"] == pytest.approx(1263.01)
        assert last["close"] == pytest.approx(1275.16)
        # 高 >= 低等基本不变量
        assert last["high"] >= last["low"]

    def test_volume_converted_shares_to_hands(self):
        """同花顺给的是「股」，必须 ÷100 变成「手」。"""
        last = _parse_line_payload(LINE_SAMPLE)[-1]
        assert last["volume"] == pytest.approx(3480142 / 100.0)
        assert last["volume"] == pytest.approx(34801.42)

    def test_amount_and_turnover(self):
        last = _parse_line_payload(LINE_SAMPLE)[-1]
        assert last["amount"] == pytest.approx(4430841400.00)
        assert last["turnover"] == pytest.approx(0.278)

    def test_skips_malformed_rows_without_failing_batch(self):
        """单行残缺/异常只跳过该行，不能整批失败。"""
        sample = (
            'cb({"data":"20260910,1291.00,1294.99,1282.00,1285.13,1890022,2428698800.00,0.151;'
            "BADROW,1,2;"
            "20260911,1285.15,1286.15,1263.01,1275.16,3480142,4430841400.00,0.278;"
            '20260912,1,2"})'
        )
        records = _parse_line_payload(sample)
        assert [r["date"] for r in records] == ["2026-09-10", "2026-09-11"]

    def test_empty_data_raises(self):
        with pytest.raises(MarketSourceError):
            _parse_line_payload('cb({"data":""})')

    def test_missing_data_field_raises(self):
        with pytest.raises(MarketSourceError):
            _parse_line_payload('cb({"num":0})')

    def test_all_rows_skipped_raises(self):
        with pytest.raises(MarketSourceError):
            _parse_line_payload('cb({"data":"x,y,z"})')

    def test_min_fields_threshold_is_sane(self):
        """阈值至少覆盖到换手率字段（索引 7）。"""
        assert MIN_FIELDS >= 8


class TestTimePayload:
    """分时（报价）解析。"""

    def test_takes_last_tick_as_price(self):
        parsed = _parse_time_payload(TIME_SAMPLE)
        assert parsed["price"] == pytest.approx(117.41)
        assert parsed["preClose"] == pytest.approx(119.18)
        assert parsed["date"] == "2026-09-11"

    def test_pct_change_computed_from_pre_close(self):
        parsed = _parse_time_payload(TIME_SAMPLE)
        expected = 117.41 / 119.18 - 1.0
        assert parsed["pctChg"] == pytest.approx(expected, rel=1e-6)
        assert parsed["pctChg"] < 0  # 该样本为下跌

    def test_name_decoded(self):
        parsed = _parse_time_payload(TIME_SAMPLE)
        assert parsed["name"] == "中芯国际"

    def test_falls_back_to_pre_close_when_no_ticks(self):
        """停牌/未开盘无分时数据时，最新价退回昨收而不是 0。"""
        sample = 'cb({"hs_600519":{"name":"x","pre":"1285.13","date":"20260911","data":""}})'
        parsed = _parse_time_payload(sample)
        assert parsed["price"] == pytest.approx(1285.13)
        assert parsed["pctChg"] == pytest.approx(0.0)

    def test_non_object_raises(self):
        with pytest.raises(MarketSourceError):
            _parse_time_payload("cb([1,2,3])")


class TestSourceCapabilities:
    def test_name_and_capabilities(self):
        src = TongHuaShunSource()
        assert src.name == "ths"
        # 同花顺不提供全市场列表（由新浪/东方财富提供）
        assert src.supports_stock_list is False
        # 提供分时报价
        assert src.supports_realtime is True

    @pytest.mark.asyncio
    async def test_list_stocks_raises(self):
        """基类默认实现应抛 MarketSourceError（而非 NotImplementedError 泄漏）。"""
        src = TongHuaShunSource()
        try:
            with pytest.raises(MarketSourceError):
                await src.list_stocks()
        finally:
            await src.close()

    @pytest.mark.asyncio
    async def test_index_snapshot_raises(self):
        """指数体系不同，明确不支持而不是返回错误数据。"""
        src = TongHuaShunSource()
        try:
            with pytest.raises(MarketSourceError):
                await src.index_snapshot()
        finally:
            await src.close()

    @pytest.mark.asyncio
    async def test_invalid_code_rejected(self):
        src = TongHuaShunSource()
        try:
            with pytest.raises(MarketSourceError):
                await src.daily_kline("", 30)
        finally:
            await src.close()
