"""API 契约测试：逐个调用 docs/API.md 中的全部接口并校验响应结构。"""

from __future__ import annotations

import time

import pytest

API = "/api/v1"


def body_of(response) -> dict:
    """断言统一信封并返回响应体。"""
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"ok", "data", "message", "serverTime"}, body
    assert body["ok"] is True, body
    assert body["serverTime"]
    return body


# --------------------------------------------------------------------- 3.1
def test_health(client):
    """health 不走统一信封。"""
    resp = client.get(f"{API}/health")
    assert resp.status_code == 200
    data = resp.json()
    # 基础字段必须齐全；后续为便于服务器排障追加了 scanReady / memoryMB /
    # sources / dataSourceMode，因此这里用「包含」而非「完全相等」断言，
    # 避免每加一个诊断字段就要改测试。
    assert {
        "status",
        "version",
        "uptimeSeconds",
        "dataSource",
        "lastSyncAt",
        "universeSize",
    } <= set(data)
    assert data["status"] == "ok"
    # 与 app/config.py 的 APP_VERSION 保持一致（当前 1.1.0：多源故障转移 + 盘口刷新）
    assert data["version"] == "1.1.0"
    assert data["dataSource"] == "synthetic"
    assert data["universeSize"] == 300
    assert data["uptimeSeconds"] >= 0
    # 排障字段：即便在没有任何上游请求的测试环境也必须存在且类型正确
    assert isinstance(data["scanReady"], bool)
    assert data["memoryMB"] is None or data["memoryMB"] >= 0
    assert set(data["sources"]) == {"total", "usable", "skipped", "allSkipped"}
    assert isinstance(data["sources"]["usable"], list)


# --------------------------------------------------------------------- 3.2
def test_market_overview(client):
    resp = client.get(f"{API}/market/overview")
    data = body_of(resp)["data"]
    assert data["dataSource"] == "synthetic"
    assert data["tradeDate"]
    sentiment = data["sentiment"]
    for key in (
        "score",
        "level",
        "limitUpCount",
        "limitDownCount",
        "brokenBoardCount",
        "brokenRate",
        "upCount",
        "downCount",
        "flatCount",
        "avgPctChg",
        "totalAmount",
    ):
        assert key in sentiment
    assert sentiment["level"] in {"冰点", "偏冷", "中性", "偏暖", "过热"}
    assert 0 <= sentiment["score"] <= 100
    assert sentiment["upCount"] + sentiment["downCount"] + sentiment["flatCount"] == 300
    assert len(data["indexes"]) >= 1
    for index in data["indexes"]:
        assert set(index) >= {"code", "name", "close", "pctChg", "sparkline"}
    distribution = data["scoreDistribution"]
    assert set(distribution) == {"buy", "watch", "reject"}
    assert sum(distribution.values()) == 300
    assert distribution["buy"] >= 30
    assert isinstance(data["topIndustries"], list) and data["topIndustries"]
    top = data["topIndustries"][0]
    assert set(top) >= {"name", "pctChg", "limitUpCount", "sentimentScore", "leader"}


# --------------------------------------------------------------------- 3.3
def test_stocks_list(client):
    resp = client.get(f"{API}/stocks", params={"page": 1, "pageSize": 20})
    data = body_of(resp)["data"]
    assert data["total"] == 300
    assert data["page"] == 1 and data["pageSize"] == 20
    assert len(data["items"]) == 20
    item = data["items"][0]
    for key in ("meta", "lastClose", "lastDate", "pctChg", "turnover", "amount", "limitUpType", "sparkline", "score", "verdict"):
        assert key in item, key
    assert set(item["meta"]) == {"code", "name", "market", "board", "industry", "isSt", "limitPct"}
    assert item["verdict"] in {"BUY", "WATCH", "REJECT"}

    # 关键字与行业过滤
    code = item["meta"]["code"]
    filtered = body_of(client.get(f"{API}/stocks", params={"keyword": code}))["data"]
    assert filtered["total"] >= 1
    assert all(code in i["meta"]["code"] or code in i["meta"]["name"] for i in filtered["items"])
    industry = item["meta"]["industry"]
    by_industry = body_of(client.get(f"{API}/stocks", params={"industry": industry}))["data"]
    assert by_industry["total"] >= 1
    assert all(i["meta"]["industry"] == industry for i in by_industry["items"])

    # 分页边界
    assert client.get(f"{API}/stocks", params={"pageSize": 500}).status_code == 422


# --------------------------------------------------------------------- 3.4
def test_stock_detail(client, signals):
    code = signals["items"][0]["meta"]["code"]
    data = body_of(client.get(f"{API}/stocks/{code}", params={"klineLimit": 60}))["data"]
    assert data["meta"]["code"] == code
    assert data["dataSource"] == "synthetic"
    assert len(data["candles"]) == 60
    candle = data["candles"][-1]
    assert set(candle) == {
        "date",
        "open",
        "high",
        "low",
        "close",
        "preClose",
        "pctChg",
        "volume",
        "amount",
        "turnover",
        "volRatio",
        "ma5",
        "ma10",
        "ma20",
        "ma60",
        "isLimitUp",
    }
    assert data["signal"] is not None
    assert len(data["signals"]) == 5
    assert isinstance(data["limitUpHistory"], list) and data["limitUpHistory"]
    history = data["limitUpHistory"][0]
    assert set(history) >= {"date", "type", "pullbackDays", "score", "verdict"}
    assert set(data["industry"]) >= {"name", "sentimentScore", "pctChg", "limitUpCount", "memberCount", "trend"}
    assert isinstance(data["explain"], str) and len(data["explain"]) > 10

    # 非法代码 / 不存在的股票
    assert client.get(f"{API}/stocks/abc").status_code == 400
    assert client.get(f"{API}/stocks/999999").status_code == 404
    assert client.get(f"{API}/stocks/{code}", params={"klineLimit": 10}).status_code == 422


# --------------------------------------------------------------------- 3.5
def test_signals_default_and_item_structure(client, signals):
    assert signals["dataSource"] == "synthetic"
    assert signals["scannedAt"] and signals["tradeDate"]
    assert signals["universeSize"] == 300
    assert signals["matched"] == signals["total"] >= 30
    assert signals["page"] == 1 and signals["pageSize"] == 200
    assert set(signals["ruleSet"]) == {
        "version",
        "weights",
        "buyScore",
        "watchScore",
        "maxHighPositionRatio",
        "minVolRatio",
        "minTurnover",
        "maxTurnover",
        "minPullbackDays",
        "maxPullbackDays",
    }
    stats = signals["statistics"]
    assert set(stats["signalPassRate"]) == {
        "volume_shrink",
        "support_hold",
        "intraday_stabilize",
        "kline_bottom",
        "sector_resonance",
    }
    assert stats["buyCount"] >= 30
    assert stats["buyCount"] + stats["watchCount"] == signals["total"]
    assert 0 < stats["avgScore"] <= 100
    assert 3 <= stats["avgPullbackDays"] <= 15

    item = signals["items"][0]
    assert set(item) >= {
        "meta",
        "lastClose",
        "lastDate",
        "limitUpDate",
        "limitUpType",
        "limitUpRejectReason",
        "limitUpClose",
        "pullbackDays",
        "pullbackPct",
        "retraceRatio",
        "score",
        "verdict",
        "signals",
        "signalSummary",
        "support",
        "plan",
        "risk",
        "sparkline",
        "sparklineDates",
    }
    assert item["verdict"] in {"BUY", "WATCH"}
    assert item["limitUpType"] == "QUALITY"
    assert item["score"] >= 55
    assert len(item["signals"]) == 5
    assert [s["key"] for s in item["signals"]] == [
        "volume_shrink",
        "support_hold",
        "intraday_stabilize",
        "kline_bottom",
        "sector_resonance",
    ]
    for detail in item["signals"]:
        assert set(detail) == {"key", "name", "passed", "score", "weight", "contribution", "detail", "metrics"}
        assert 0 <= detail["score"] <= 100
        assert detail["contribution"] == pytest.approx(round(detail["score"] / 100 * detail["weight"] * 100, 1), abs=0.05)
    assert set(item["plan"]) == {
        "buyLow",
        "buyHigh",
        "stopLoss",
        "takeProfit1",
        "takeProfit2",
        "riskReward",
        "positionPct",
        "batchCount",
    }
    assert item["plan"]["buyLow"] <= item["plan"]["buyHigh"]
    assert item["plan"]["batchCount"] == 3
    assert item["plan"]["positionPct"] in {10, 20, 30, 40}
    assert set(item["support"]) == {
        "limitOpen",
        "strongHalf",
        "ma5",
        "ma10",
        "ma20",
        "activeSupport",
        "activeSupportName",
        "distanceToSupportPct",
    }
    assert set(item["risk"]) == {"riskLevel", "riskPoints"}
    assert item["risk"]["riskLevel"] in {"低", "中", "高"}
    assert isinstance(item["risk"]["riskPoints"], list) and item["risk"]["riskPoints"]
    assert len(item["sparkline"]) == 20 and len(item["sparklineDates"]) == 20
    assert all(isinstance(v, (int, float)) for v in item["sparkline"])


def test_signals_filters_and_sorting(client):
    buy_only = body_of(client.get(f"{API}/signals", params={"verdict": "BUY", "pageSize": 200}))["data"]
    assert buy_only["total"] >= 30
    assert all(i["verdict"] == "BUY" for i in buy_only["items"])
    assert all(all(s["passed"] for s in i["signals"]) for i in buy_only["items"])

    scored = body_of(client.get(f"{API}/signals", params={"minScore": 85, "pageSize": 200}))["data"]
    assert all(i["score"] >= 85 for i in scored["items"])

    ascending = body_of(client.get(f"{API}/signals", params={"sort": "score", "order": "asc", "pageSize": 200}))["data"]
    scores = [i["score"] for i in ascending["items"]]
    assert scores == sorted(scores)

    by_days = body_of(client.get(f"{API}/signals", params={"sort": "pullbackDays", "order": "desc", "pageSize": 200}))["data"]
    days = [i["pullbackDays"] for i in by_days["items"]]
    assert days == sorted(days, reverse=True)

    paged = body_of(client.get(f"{API}/signals", params={"page": 2, "pageSize": 10}))["data"]
    assert paged["page"] == 2 and len(paged["items"]) <= 10

    all_types = body_of(client.get(f"{API}/signals", params={"limitUpType": "ALL", "verdict": "REJECT", "pageSize": 200}))["data"]
    assert all_types["total"] >= 1
    assert all(i["verdict"] == "REJECT" for i in all_types["items"])

    industry = body_of(client.get(f"{API}/signals", params={"pageSize": 1}))["data"]["items"][0]["meta"]["industry"]
    by_industry = body_of(client.get(f"{API}/signals", params={"industry": industry, "pageSize": 200}))["data"]
    assert all(i["meta"]["industry"] == industry for i in by_industry["items"])

    # 参数校验
    assert client.get(f"{API}/signals", params={"verdict": "HOLD"}).status_code == 400
    assert client.get(f"{API}/signals", params={"sort": "unknown"}).status_code == 400
    assert client.get(f"{API}/signals", params={"minScore": 90, "maxScore": 10}).status_code == 400


# --------------------------------------------------------------------- 3.6
def test_signal_detail(client, signals):
    code = signals["items"][0]["meta"]["code"]
    data = body_of(client.get(f"{API}/signals/{code}"))["data"]
    assert data["meta"]["code"] == code
    assert len(data["signals"]) == 5
    assert len(data["candles"]) == 60
    assert set(data["industry"]) >= {"name", "sentimentScore", "pctChg", "limitUpCount", "memberCount", "trend"}
    assert client.get(f"{API}/signals/999999").status_code == 404
    assert client.get(f"{API}/signals/abc").status_code == 400


# --------------------------------------------------------------------- 3.7 / 3.8
def test_scan_task_flow(client):
    resp = client.post(f"{API}/scan", json={"refresh": True, "limitUpType": "QUALITY"})
    data = body_of(resp)["data"]
    task_id = data["taskId"]
    assert set(data) >= {"taskId", "status", "progress", "total", "message"}
    assert data["total"] == 300
    assert data["status"] in {"pending", "running", "finished"}

    task = None
    for _ in range(60):
        task = body_of(client.get(f"{API}/scan/{task_id}"))["data"]
        if task["status"] in {"finished", "failed"}:
            break
        time.sleep(0.2)
    assert task is not None
    assert task["status"] == "finished", task
    assert task["progress"] == task["total"] == 300
    assert task["startedAt"] and task["finishedAt"]
    assert task["matched"] >= 30
    assert "扫描完成" in task["message"]

    assert client.get(f"{API}/scan/not-exist").status_code == 404


# --------------------------------------------------------------------- 3.9
def test_backtest(client):
    data = body_of(client.get(f"{API}/backtest", params={"lookbackDays": 120, "minScore": 75, "horizon": 5}))["data"]
    assert data["dataSource"] == "synthetic"
    assert data["lookbackDays"] == 120 and data["minScore"] == 75.0 and data["horizon"] == 5
    assert data["sampleSize"] >= 1
    assert 0.0 <= data["winRate"] <= 1.0
    assert data["returnCurve"], "returnCurve 不能为空"
    for point in data["returnCurve"]:
        assert set(point) == {"date", "index", "equity"}
        assert point["index"] > 0
    assert [b["bucket"] for b in data["returnDistribution"]] == [
        "<-5%",
        "-5%~-2%",
        "-2%~0%",
        "0%~2%",
        "2%~5%",
        ">5%",
    ]
    assert sum(b["count"] for b in data["returnDistribution"]) == data["sampleSize"]
    assert len(data["bySignal"]) == 5
    for entry in data["bySignal"]:
        assert set(entry) == {"key", "name", "sampleSize", "winRate", "avgReturn"}
    assert len(data["trades"]) <= 200
    assert data["profitFactor"] >= 0
    assert data["maxDrawdown"] <= 0
    assert client.get(f"{API}/backtest", params={"horizon": 50}).status_code == 422


# --------------------------------------------------------------------- 3.10 / 3.11
def test_rules_get_and_put(client):
    data = body_of(client.get(f"{API}/rules"))["data"]
    assert set(data) == {"ruleSet", "criteria"}
    assert set(data["criteria"]) == {"limitUp", "entry", "discipline", "avoid"}
    assert len(data["criteria"]["entry"]) == 5
    assert len(data["criteria"]["discipline"]) >= 6
    assert len(data["criteria"]["avoid"]) >= 4
    for group in data["criteria"].values():
        for criterion in group:
            assert set(criterion) >= {"key", "name", "enabled", "desc"}
            assert criterion["desc"]
    rule_set = data["ruleSet"]
    assert rule_set["buyScore"] == 75.0 and rule_set["watchScore"] == 55.0
    assert set(rule_set["weights"]) == {
        "volume_shrink",
        "support_hold",
        "intraday_stabilize",
        "kline_bottom",
        "sector_resonance",
    }

    updated = body_of(client.put(f"{API}/rules", json={"buyScore": 80, "minTurnover": 4.0}))["data"]
    assert updated["buyScore"] == 80.0
    assert updated["minTurnover"] == 4.0
    after = body_of(client.get(f"{API}/rules"))["data"]["ruleSet"]
    assert after["buyScore"] == 80.0

    restored = body_of(client.put(f"{API}/rules", json={"buyScore": 75.0, "minTurnover": 3.0}))["data"]
    assert restored["buyScore"] == 75.0 and restored["minTurnover"] == 3.0


# --------------------------------------------------------------------- 3.12~3.14
def test_pool_flow(client, signals):
    code = signals["items"][0]["meta"]["code"]
    empty = body_of(client.get(f"{API}/pool"))["data"]
    assert set(empty) == {"total", "items", "summary"}
    assert set(empty["summary"]) == {"avgPnlPct", "triggeredCount", "stoppedCount", "targetCount"}

    created = body_of(client.post(f"{API}/pool", json={"code": code, "note": "回调缩量良好"}))["data"]
    assert created["meta"]["code"] == code
    assert created["buyLow"] and created["buyHigh"] and created["stopLoss"]
    assert created["takeProfit1"] and created["takeProfit2"]
    assert created["buyLow"] <= created["buyHigh"]
    assert created["status"] in {"watching", "triggered", "stopped", "target"}
    assert created["statusText"]
    assert created["addedAt"] and created["addedPrice"]
    assert created["note"] == "回调缩量良好"
    assert created["id"]

    # 重复添加 → 409
    assert client.post(f"{API}/pool", json={"code": code}).status_code == 409

    listed = body_of(client.get(f"{API}/pool"))["data"]
    assert listed["total"] == 1
    assert listed["items"][0]["meta"]["code"] == code
    assert listed["items"][0]["lastClose"] is not None
    assert listed["items"][0]["pnlPct"] is not None

    removed = body_of(client.delete(f"{API}/pool/{code}"))["data"]
    assert removed == {"code": code}
    assert body_of(client.get(f"{API}/pool"))["data"]["total"] == 0
    assert client.delete(f"{API}/pool/{code}").status_code == 404
    assert client.post(f"{API}/pool", json={"code": "abc"}).status_code == 400
    assert client.post(f"{API}/pool", json={"code": "999999"}).status_code == 404


# --------------------------------------------------------------------- 3.15
def test_settings(client):
    data = body_of(client.get(f"{API}/settings"))["data"]
    assert set(data) == {
        "appName",
        "version",
        "dataSourceMode",
        "dataSourceActive",
        "dataSourceOrder",
        "usingFallback",
        "sources",
        "universeSize",
        "universeSource",
        "cacheTtlSeconds",
        "effectiveTtlSeconds",
        "refreshIntervalSeconds",
        "klineCache",
        "clock",
        "syntheticEnabled",
        "serverTime",
        "timezone",
    }
    assert data["appName"] == "涨停回调低吸战法"
    assert data["dataSourceMode"] == "synthetic"
    assert data["dataSourceActive"] == "synthetic"
    assert data["universeSize"] == 300
    assert data["cacheTtlSeconds"] == 300
    assert data["syntheticEnabled"] is True
    assert data["timezone"] == "Asia/Shanghai"
    # 刷新策略与市场时钟必须下发，前端据此决定是否轮询
    assert data["refreshIntervalSeconds"] == 30
    assert isinstance(data["effectiveTtlSeconds"], int) and data["effectiveTtlSeconds"] >= 5
    assert data["clock"]["intervalSeconds"] == 30
    assert data["clock"]["timezone"] == "Asia/Shanghai"
    assert isinstance(data["clock"]["shouldPoll"], bool)


def test_market_clock(client):
    """市场时钟接口：必须返回时段、是否轮询与下次开盘时间。"""
    data = body_of(client.get(f"{API}/market/clock"))["data"]
    assert data["timezone"] == "Asia/Shanghai"
    assert data["phase"] in {
        "pre_open",
        "call_auction",
        "morning",
        "lunch_break",
        "afternoon",
        "closed",
        "holiday",
    }
    assert isinstance(data["shouldPoll"], bool)
    assert isinstance(data["isOpen"], bool)
    assert isinstance(data["isTradingDay"], bool)
    assert data["intervalSeconds"] == 30
    # 收盘后应给出下一次开盘时刻
    if not data["isOpen"]:
        assert data["nextOpenAt"] is not None


def test_sources_endpoint(client):
    """数据源状态接口：synthetic 模式下应报告降级状态且源列表为空。"""
    data = body_of(client.get(f"{API}/sources"))["data"]
    assert data["mode"] == "synthetic"
    assert data["active"] == "synthetic"
    assert isinstance(data["sources"], list)


# --------------------------------------------------------------------- 错误信封
def test_error_envelope(client):
    resp = client.get(f"{API}/not-exist-endpoint")
    assert resp.status_code == 404
    body = resp.json()
    assert body["ok"] is False
    assert body["data"] is None
    assert isinstance(body["message"], str) and body["message"]
    assert body["serverTime"]

    resp = client.get(f"{API}/stocks", params={"page": 0})
    assert resp.status_code == 422
    body = resp.json()
    assert body["ok"] is False
    assert "参数校验失败" in body["message"]


def test_root_hint_available(client):
    """无前端构建产物时，根路径返回中文提示页且不报错。"""
    resp = client.get("/")
    assert resp.status_code in {200, 404}
    if resp.status_code == 200:
        assert "text/html" in resp.headers.get("content-type", "")
