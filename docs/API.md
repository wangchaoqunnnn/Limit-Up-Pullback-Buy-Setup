# 涨停回调低吸战法 —— 接口契约 (API Contract)

> 版本: v1.0.0
> 状态: **已冻结 (FROZEN)**。前端与后端均以此为唯一契约，任何一方不得单方面修改。
> Base URL: `/api/v1`

---

## 0. 全局约定

### 0.1 统一响应信封

**除 `/api/v1/health` 外**，所有接口均返回统一信封:

```json
{
  "ok": true,
  "data": { },
  "message": null,
  "serverTime": "2026-02-13T15:03:21+08:00"
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `ok` | bool | 业务是否成功。HTTP 状态码同时反映结果。 |
| `data` | object / array / null | 业务数据。失败时为 `null`。 |
| `message` | string / null | 失败原因（中文，可直接展示给用户）。 |
| `serverTime` | string | ISO-8601 带时区，Asia/Shanghai。 |

失败示例（HTTP 404）:

```json
{
  "ok": false,
  "data": null,
  "message": "未找到股票 999999 的行情数据",
  "serverTime": "2026-02-13T15:03:21+08:00"
}
```

### 0.2 错误码

| HTTP | 场景 |
|---|---|
| 400 | 参数非法（如 `code` 非 6 位数字、日期区间倒置） |
| 404 | 资源不存在（股票无数据 / 不在股票池） |
| 409 | 状态冲突（股票已在股票池中） |
| 422 | Pydantic 校验失败（FastAPI 默认，message 会被规整） |
| 500 | 服务端异常 |
| 503 | 数据源不可用且降级失败 |

### 0.3 静态资源

- 前端构建产物由后端挂载在 `/`（SPA fallback 到 `index.html`）。
- 本契约所有路径均以 `/api/v1` 前缀开头，前端开发态通过 Vite proxy 转发。
- **禁止硬编码任何 host / IP / 绝对 URL**。前端一律使用相对路径 `/api/v1/...`。

### 0.4 数据源标识

所有行情相关响应都带 `dataSource` 字段:

| 值 | 含义 |
|---|---|
| `eastmoney` | 东方财富公开行情接口（真实数据） |
| `synthetic` | 内置合成演示数据（离线兜底，UI 必须显著提示） |
| `cache` | 命中的本地文件缓存（真实数据） |

---

## 1. 枚举定义

### 1.1 LimitUpType —— 涨停质量分类

| 值 | 中文 | 判定规则 |
|---|---|---|
| `QUALITY` | 优质实体放量首板 | 通过全部硬性门槛，可作为战法标的 |
| `ONE_WORD` | 一字板（缩量） | `open == high == close` 或 `open_pct >= 9.5`，或 `vol_ratio < 1.2` |
| `TAIL_SNEAK` | 尾盘偷袭板 | `close == high` 且 `(close - open) / pre_close >= 0.06` 且换手 `turnover < 3` |
| `WEAK_SEAL` | 烂板 / 炸板 | `close < high` 且 `(high - close) / pre_close >= 0.03` |
| `HIGH_POSITION` | 高位板 | 涨停日收盘价处于近 120 日区间的 `(close - low120) / (high120 - low120) > 0.80` |
| `CONSECUTIVE` | 连板 / 妖股 | 前 1 日或前 2 日存在涨停 |
| `ST_LIMIT` | ST 股涨停（5% 制度） | 名称含 `ST`，且当日涨幅落在 4.8%~5.2% |
| `NONE` | 非涨停 | 当日涨幅未达涨停阈值 |

判定优先级（自上而下，命中即返回）:
`ST_LIMIT` → `NONE` → `CONSECUTIVE` → `HIGH_POSITION` → `ONE_WORD` → `TAIL_SNEAK` → `WEAK_SEAL` → `QUALITY`

### 1.2 SignalKey —— 五大共振信号

| key | 中文名称 | weight |
|---|---|---|
| `volume_shrink` | 回调持续缩量，量能逐日递减 | 0.25 |
| `support_hold` | 守住关键支撑，不破安全区间 | 0.25 |
| `intraday_stabilize` | 分时止跌企稳，低点逐步抬高 | 0.15 |
| `kline_bottom` | K线筑底止跌，小阳十字星收尾 | 0.20 |
| `sector_resonance` | 板块情绪同步回暖，题材有持续性 | 0.15 |

### 1.3 Verdict —— 最终结论

| 值 | 中文 | 触发条件 |
|---|---|---|
| `BUY` | 可低吸 | `score >= buyScore` 且 5 个信号全部 `passed` |
| `WATCH` | 观察 | `watchScore <= score < buyScore`，或信号 4/5 通过 |
| `REJECT` | 放弃 | `score < watchScore`，或命中任一硬性否决 |

---

## 2. 数据模型

### 2.1 StockMeta

```json
{
  "code": "600519",
  "name": "贵州茅台",
  "market": "SH",
  "board": "主板",
  "industry": "白酒",
  "isSt": false,
  "limitPct": 0.10
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `code` | string | 6 位代码 |
| `name` | string | 中文简称 |
| `market` | `"SH"` / `"SZ"` / `"BJ"` | 交易所 |
| `board` | string | `主板` / `创业板` / `科创板` / `北交所` |
| `industry` | string | 所属行业/板块（用于信号五） |
| `isSt` | bool | 是否 ST |
| `limitPct` | number | 涨停幅度：主板 0.10，创业/科创 0.20，ST 0.05 |

### 2.2 Candle —— 日线K线

```json
{
  "date": "2026-01-08",
  "open": 12.35,
  "high": 13.58,
  "low": 12.20,
  "close": 13.58,
  "preClose": 12.35,
  "pctChg": 0.0996,
  "volume": 1823456,
  "amount": 2415000000.0,
  "turnover": 8.42,
  "volRatio": 2.35,
  "ma5": 12.88,
  "ma10": 12.41,
  "ma20": 11.97,
  "ma60": 11.02,
  "isLimitUp": true
}
```

`volume` 单位：手。`amount` 单位：元。`turnover` 单位：%。`volRatio` = 当日量 / 前 5 日均量。均线缺失时该字段为 `null`。

### 2.3 SignalDetail

```json
{
  "key": "volume_shrink",
  "name": "回调持续缩量，量能逐日递减",
  "passed": true,
  "score": 92.0,
  "weight": 0.25,
  "contribution": 23.0,
  "detail": "回调 4 日量能依次 118万→96万→72万→51万手，逐日递减；最大量能仅为涨停日的 0.41 倍（阈值 1.00）",
  "metrics": {
    "pullbackDays": 4,
    "maxVolRatioToLimit": 0.41,
    "isMonotonicShrink": true,
    "shrinkStreak": 4
  }
}
```

`metrics` 为自由对象（object），前端按 key 渲染键值对即可，无需硬编码。

### 2.4 StockSignal —— 核心筛选结果

```json
{
  "meta": { },
  "lastClose": 13.02,
  "lastDate": "2026-02-13",
  "limitUpDate": "2026-02-05",
  "limitUpType": "QUALITY",
  "limitUpRejectReason": null,
  "limitUpClose": 14.85,
  "pullbackDays": 6,
  "pullbackPct": -0.1232,
  "retraceRatio": 0.452,
  "score": 86.4,
  "verdict": "BUY",
  "signals": [ ],
  "signalSummary": "五信号共振，可分批低吸",
  "support": {
    "limitOpen": 13.50,
    "strongHalf": 14.18,
    "ma5": 13.31,
    "ma10": 13.60,
    "ma20": 12.88,
    "activeSupport": 13.31,
    "activeSupportName": "MA5",
    "distanceToSupportPct": -0.0218
  },
  "plan": {
    "buyLow": 13.10,
    "buyHigh": 13.45,
    "stopLoss": 13.37,
    "takeProfit1": 14.85,
    "takeProfit2": 16.34,
    "riskReward": 2.14,
    "positionPct": 30,
    "batchCount": 3
  },
  "risk": {
    "riskLevel": "低",
    "riskPoints": ["回调第 3 日量能小幅放大至涨停日的 0.88 倍"]
  },
  "sparkline": [13.58, 13.91, 14.55, 14.85, 14.52, 14.31, 14.05, 13.74, 13.55, 13.30, 13.02],
  "sparklineDates": ["2026-02-02", "2026-02-03"]
}
```

### 2.5 PoolItem —— 自选低吸池条目

```json
{
  "id": "1f0c...",
  "meta": { },
  "limitUpDate": "2026-02-05",
  "limitUpType": "QUALITY",
  "addedAt": "2026-02-13T14:22:05+08:00",
  "addedPrice": 13.02,
  "buyLow": 13.10,
  "buyHigh": 13.45,
  "stopLoss": 13.37,
  "takeProfit1": 14.85,
  "takeProfit2": 16.34,
  "note": "回调缩量良好",
  "lastClose": 13.02,
  "lastDate": "2026-02-13",
  "pnlPct": 0.0,
  "status": "watching",
  "statusText": "观察中"
}
```

`status` ∈ `watching` | `triggered` | `stopped` | `target`。
`statusText` 为中文展示文案。

### 2.6 RuleSet —— 策略参数（可在 UI 调整）

```json
{
  "version": "1.0.0",
  "weights": {
    "volume_shrink": 0.25,
    "support_hold": 0.25,
    "intraday_stabilize": 0.15,
    "kline_bottom": 0.20,
    "sector_resonance": 0.15
  },
  "buyScore": 75.0,
  "watchScore": 55.0,
  "maxHighPositionRatio": 0.80,
  "minVolRatio": 1.2,
  "minTurnover": 3.0,
  "maxTurnover": 25.0,
  "minPullbackDays": 3,
  "maxPullbackDays": 15
}
```

---

## 3. 接口清单

### 3.1 `GET /api/v1/health`

**不走统一信封**（供探针使用）。

```json
{
  "status": "ok",
  "version": "1.0.0",
  "uptimeSeconds": 128.4,
  "dataSource": "synthetic",
  "lastSyncAt": "2026-02-13T15:00:00+08:00",
  "universeSize": 300
}
```

---

### 3.2 `GET /api/v1/market/overview`

市场情绪总览（顶部仪表盘）。

**Query**

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `refresh` | bool | false | true 时强制刷新数据源 |

**data**

```json
{
  "dataSource": "synthetic",
  "updatedAt": "2026-02-13T15:00:00+08:00",
  "tradeDate": "2026-02-13",
  "sentiment": {
    "score": 62.5,
    "level": "偏暖",
    "limitUpCount": 48,
    "limitDownCount": 6,
    "brokenBoardCount": 12,
    "brokenRate": 0.20,
    "upCount": 2310,
    "downCount": 2455,
    "flatCount": 121,
    "avgPctChg": 0.0032,
    "totalAmount": 892300000000.0
  },
  "indexes": [
    { "code": "000001", "name": "上证指数", "close": 3218.44, "pctChg": 0.0042, "sparkline": [] },
    { "code": "399001", "name": "深证成指", "close": 10233.11, "pctChg": -0.0018, "sparkline": [] },
    { "code": "399006", "name": "创业板指", "close": 2044.87, "pctChg": 0.0113, "sparkline": [] }
  ],
  "scoreDistribution": {
    "buy": 7,
    "watch": 23,
    "reject": 270
  },
  "topIndustries": [
    { "name": "半导体", "pctChg": 0.0231, "limitUpCount": 6, "sentimentScore": 81.0, "leader": "600584" }
  ]
}
```

`level` ∈ `冰点` | `偏冷` | `中性` | `偏暖` | `过热`。

---

### 3.3 `GET /api/v1/stocks`

股票池列表（带最新一句话行情）。

**Query**

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `keyword` | string | - | 代码或名称模糊匹配 |
| `industry` | string | - | 行业精确匹配 |
| `page` | int | 1 | 页码 |
| `pageSize` | int | 20 | 每页条数，1~200 |

**data**

```json
{
  "total": 300,
  "page": 1,
  "pageSize": 20,
  "items": [
    {
      "meta": { },
      "lastClose": 13.02,
      "lastDate": "2026-02-13",
      "pctChg": -0.0121,
      "turnover": 4.12,
      "amount": 512000000.0,
      "limitUpType": "QUALITY",
      "sparkline": [],
      "score": 86.4,
      "verdict": "BUY"
    }
  ]
}
```

---

### 3.4 `GET /api/v1/stocks/{code}`

单只股票完整分析（个股详情页主数据）。

**Query**

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `klineLimit` | int | 120 | 返回最近 N 根K线，30~500 |

**data**

```json
{
  "meta": { },
  "dataSource": "synthetic",
  "candles": [ ],
  "signal": { },
  "signals": [ ],
  "limitUpHistory": [
    { "date": "2026-02-05", "type": "QUALITY", "pullbackDays": 6, "score": 86.4, "verdict": "BUY" }
  ],
  "industry": {
    "name": "半导体",
    "sentimentScore": 81.0,
    "pctChg": 0.0231,
    "limitUpCount": 6,
    "memberCount": 42,
    "trend": [62.0, 66.5, 71.0, 75.5, 81.0]
  },
  "explain": "该股 2026-02-05 以放量实体首板启动…"
}
```

个股无有效涨停记录时 `signal` 为 `null`，`limitUpHistory` 可为空数组。

---

### 3.5 `GET /api/v1/signals`

**核心接口** —— 按战法筛选当前可低吸标的。

**Query**

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `verdict` | string | `BUY,WATCH` | 逗号分隔，可选 `BUY` / `WATCH` / `REJECT` |
| `industry` | string | - | 行业过滤 |
| `minScore` | float | 0 | 最低评分 |
| `maxScore` | float | 100 | 最高评分 |
| `limitUpType` | string | `QUALITY` | 涨停类型过滤，`ALL` 表示不过滤 |
| `sort` | string | `score` | `score` / `pullbackDays` / `pctChg` / `turnover` |
| `order` | string | `desc` | `asc` / `desc` |
| `page` | int | 1 | |
| `pageSize` | int | 50 | 1~200 |
| `refresh` | bool | false | 强制重新扫描 |

**data**

```json
{
  "dataSource": "synthetic",
  "scannedAt": "2026-02-13T15:03:00+08:00",
  "tradeDate": "2026-02-13",
  "universeSize": 300,
  "matched": 30,
  "total": 30,
  "page": 1,
  "pageSize": 50,
  "ruleSet": { },
  "items": [ ],
  "statistics": {
    "avgScore": 71.2,
    "buyCount": 7,
    "watchCount": 23,
    "avgPullbackDays": 5.4,
    "signalPassRate": {
      "volume_shrink": 0.63,
      "support_hold": 0.58,
      "intraday_stabilize": 0.41,
      "kline_bottom": 0.52,
      "sector_resonance": 0.47
    }
  }
}
```

`items` 为 `StockSignal[]`。

---

### 3.6 `GET /api/v1/signals/{code}`

单只标的信号详情，**data** 结构同 `StockSignal`，额外附 `candles`（最近 60 根）与 `industry`。无信号时返回 `ok: false`, HTTP 404。

---

### 3.7 `POST /api/v1/scan`

触发全市场扫描任务（K线数据可能较慢，异步返回）。

**Body**

```json
{ "refresh": true, "limitUpType": "QUALITY" }
```

**data**

```json
{
  "taskId": "b3f1c2d4",
  "status": "running",
  "progress": 0,
  "total": 300,
  "message": "开始扫描"
}
```

---

### 3.8 `GET /api/v1/scan/{taskId}`

**data**

```json
{
  "taskId": "b3f1c2d4",
  "status": "finished",
  "progress": 300,
  "total": 300,
  "startedAt": "2026-02-13T15:03:00+08:00",
  "finishedAt": "2026-02-13T15:03:12+08:00",
  "matched": 30,
  "message": "扫描完成，命中 30 只"
}
```

`status` ∈ `pending` | `running` | `finished` | `failed`。
任务状态保存在内存，仅保留最近 20 条。

---

### 3.9 `GET /api/v1/backtest`

信号历史回溯统计（用历史K线回放到「信号日」，统计 T+1..T+5 表现）。

**Query**

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `lookbackDays` | int | 120 | 回放窗口，30~500 |
| `minScore` | float | 75 | 仅统计评分达标的样本 |
| `horizon` | int | 5 | 持有天数，1~10 |

**data**

```json
{
  "dataSource": "synthetic",
  "lookbackDays": 120,
  "minScore": 75.0,
  "horizon": 5,
  "sampleSize": 128,
  "winRate": 0.664,
  "avgReturn": 0.0412,
  "avgWin": 0.0871,
  "avgLoss": -0.0498,
  "profitFactor": 2.31,
  "maxDrawdown": -0.1132,
  "avgMaxGain": 0.1023,
  "avgMaxLoss": -0.0355,
  "returnCurve": [
    { "date": "2026-01-05", "index": 100.0, "equity": 100.0 }
  ],
  "returnDistribution": [
    { "bucket": "<-5%", "count": 12 },
    { "bucket": "-5%~-2%", "count": 19 }
  ],
  "bySignal": [
    { "key": "volume_shrink", "name": "回调持续缩量", "sampleSize": 128, "winRate": 0.71, "avgReturn": 0.0488 }
  ],
  "trades": [
    {
      "code": "600584",
      "name": "长电科技",
      "signalDate": "2026-01-12",
      "entryPrice": 21.35,
      "exitPrice": 23.02,
      "returnPct": 0.0782,
      "maxGainPct": 0.1120,
      "maxLossPct": -0.0180,
      "score": 88.1,
      "holdDays": 5
    }
  ]
}
```

`returnCurve[].index` 为等权持有期收益累乘指数（起点 100）。
`trades` 最多返回 200 条。

---

### 3.10 `GET /api/v1/rules`

返回 `RuleSet` 全文 + 规则出处说明。

**data**

```json
{
  "ruleSet": { },
  "criteria": {
    "limitUp": [
      { "key": "low_position_first_board", "name": "只做低位首板，不做连板妖股", "enabled": true, "desc": "…" }
    ],
    "entry": [
      { "key": "volume_shrink", "name": "回调持续缩量，量能逐日递减", "enabled": true, "desc": "…" }
    ],
    "discipline": [
      { "key": "no_early_dip_buy", "name": "绝不提前抄底", "enabled": true, "desc": "…" }
    ],
    "avoid": [
      { "key": "broken_volume", "name": "回调放量超过涨停日", "enabled": true, "desc": "…" }
    ]
  }
}
```

### 3.11 `PUT /api/v1/rules`

更新策略参数（服务端持久化到 `data/rules.json`，内存热更新）。

**Body**：`RuleSet` 的部分字段。响应 **data** 为更新后的完整 `RuleSet`。

---

### 3.12 `GET /api/v1/pool`

**data**

```json
{
  "total": 3,
  "items": [ ],
  "summary": {
    "avgPnlPct": 0.0121,
    "triggeredCount": 1,
    "stoppedCount": 0,
    "targetCount": 1
  }
}
```

### 3.13 `POST /api/v1/pool`

**Body**

```json
{
  "code": "600584",
  "buyLow": 13.10,
  "buyHigh": 13.45,
  "stopLoss": 13.37,
  "takeProfit1": 14.85,
  "takeProfit2": 16.34,
  "note": "回调缩量良好"
}
```

`buyLow/buyHigh/stopLoss/takeProfit1/takeProfit2/note` 均可省略，省略时服务端按战法自动计算。
重复添加返回 HTTP 409。

**data**：新建的 `PoolItem`。

### 3.14 `DELETE /api/v1/pool/{code}`

成功返回 `{ "ok": true, "data": { "code": "600584" }, "message": null }`。不存在返回 404。

---

### 3.15 `GET /api/v1/settings`

**Query**

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `deep` | bool | false | true 时访问数据源以获取准确股票池规模（较慢）；默认只读缓存 |

**data**（v1.1 扩展：新增多源状态、市场时钟与刷新策略）

```json
{
  "appName": "涨停回调低吸战法",
  "version": "1.1.0",
  "dataSourceMode": "auto",
  "dataSourceActive": "tencent",
  "dataSourceOrder": ["eastmoney", "tencent", "sina"],
  "usingFallback": false,
  "sources": [
    {
      "name": "eastmoney",
      "consecutiveFailures": 3,
      "totalSuccess": 0,
      "totalFailure": 5,
      "successRate": 0.0,
      "lastError": "eastmoney 请求失败：ConnectError",
      "cooldownSeconds": 12.0,
      "available": false,
      "lastSuccessAt": null
    },
    {
      "name": "tencent",
      "consecutiveFailures": 0,
      "totalSuccess": 42,
      "totalFailure": 0,
      "successRate": 1.0,
      "lastError": null,
      "cooldownSeconds": 0.0,
      "available": true,
      "lastSuccessAt": "2026-02-13T10:04:12"
    }
  ],
  "universeSize": 1000,
  "universeSource": "sina",
  "cacheTtlSeconds": 300,
  "effectiveTtlSeconds": 30,
  "refreshIntervalSeconds": 30,
  "klineCache": {
    "enabled": true,
    "engine": "sqlite",
    "file": "klines.db",
    "codes": 1000,
    "bars": 250000,
    "latestDate": "2026-02-13",
    "bytes": 18874368
  },
  "clock": {
    "now": "2026-02-13T10:04:12+08:00",
    "tradeDate": "2026-02-13",
    "isTradingDay": true,
    "isOpen": true,
    "phase": "morning",
    "phaseText": "上午交易",
    "shouldPoll": true,
    "intervalSeconds": 30,
    "nextOpenAt": null,
    "nextCloseAt": "2026-02-13T15:00:00+08:00",
    "timezone": "Asia/Shanghai"
  },
  "syntheticEnabled": true,
  "serverTime": "2026-02-13T10:04:12+08:00",
  "timezone": "Asia/Shanghai"
}
```

**字段说明（新增部分）**

| 字段 | 说明 |
|---|---|
| `dataSourceOrder` | 真实数据源优先级顺序，故障转移按此尝试 |
| `usingFallback` | 是否已降级为合成演示数据（仅当全部真实源不可用） |
| `sources[]` | 各源健康度：连续失败次数、成功率、冷却剩余秒数、当前是否可用 |
| `sources[].everSucceeded` | 该源在本进程内是否**曾经成功过**（用于区分「临时故障」与「本环境不可达」） |
| `sources[].skipped` | 是否已被**长期跳过**：从未成功过且失败达阈值 → 不再尝试，避免每次白等一个连接超时 |
| `effectiveTtlSeconds` | 按交易时段折算后的**实际**缓存 TTL（开盘=刷新间隔） |
| `refreshIntervalSeconds` | 开盘期间的刷新间隔（默认 30 秒） |
| `klineCache` | 日线增量缓存（SQLite）规模 |
| `clock.shouldPoll` | **前端据此决定是否开启自动刷新**；收盘/休市为 false |

---

### 3.16 `GET /api/v1/market/clock`

市场时钟。前端据此控制「开盘期间每 30 秒自动刷新」——`shouldPoll` 为 false 时停止轮询，避免收盘后产生无意义请求。

**data**

```json
{
  "now": "2026-02-13T10:04:12+08:00",
  "tradeDate": "2026-02-13",
  "isTradingDay": true,
  "isOpen": true,
  "phase": "morning",
  "phaseText": "上午交易",
  "shouldPoll": true,
  "intervalSeconds": 30,
  "nextOpenAt": null,
  "nextCloseAt": "2026-02-13T15:00:00+08:00",
  "timezone": "Asia/Shanghai"
}
```

`phase` 取值与含义：

| 值 | 时段 | shouldPoll | 缓存 TTL |
|---|---|---|---|
| `pre_open` | 09:15 之前 | false | `CACHE_TTL_SECONDS`（300） |
| `call_auction` | 09:15–09:30 集合竞价 | **true** | 刷新间隔 ×2（60） |
| `morning` | 09:30–11:30 | **true** | `REFRESH_INTERVAL_SECONDS`（30） |
| `lunch_break` | 11:30–13:00 | **true** | 刷新间隔 ×2（60） |
| `afternoon` | 13:00–15:00 | **true** | `REFRESH_INTERVAL_SECONDS`（30） |
| `closed` | 15:00 之后 | false | `CACHE_TTL_SECONDS`（300） |
| `holiday` | 周末与法定休市日 | false | `CACHE_TTL_SECONDS`（300） |

---

### 3.17 `GET /api/v1/sources`

数据源状态与健康度（多源故障转移的可观测性入口）。

**Query**

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `probe` | bool | false | true 时现场探测各源可用性后再返回（较慢） |

**data**

```json
{
  "mode": "auto",
  "active": "tencent",
  "usingFallback": false,
  "order": ["eastmoney", "tencent", "sina"],
  "sources": [],
  "lastPickByOperation": { "获取股票列表": "sina", "日线": "tencent" },
  "universeSize": 1000,
  "universeSource": "sina",
  "cache": {},
  "ttlSeconds": 30,
  "refreshIntervalSeconds": 30,
  "clock": {}
}
```

`lastPickByOperation` 记录每个操作**最近一次实际成功**的数据源，用于快速定位「哪个源在真正供数」。

---

### 3.18 数据源与故障转移说明

#### 数据源标识（`dataSource` 字段）

| 值 | 含义 |
|---|---|
| `eastmoney` | 东方财富公开行情接口 |
| `tencent` | 腾讯财经行情接口 |
| `ths` | 同花顺行情接口（10jqka） |
| `sina` | 新浪财经行情接口 |
| `synthetic` | 内置合成演示数据（**仅当全部真实源不可用时**） |

#### 各源能力对照（均为实测结论）

| 源 | 全市场列表 | 日线 | 实时 | 主板 | 创业板 | 科创板 | 北交所 | 备注 |
|---|---|---|---|---|---|---|---|---|
| 东方财富 | ✅ 字段最全 | ✅ 前复权 | ✅ | ✅ | ✅ | ✅ | ✅ | 部分网络在 TLS 层阻断该域名，适配器快速失败 |
| 腾讯 | ✅（仅主板+创业板） | ✅ 前复权 | ✅ | ✅ | ✅ | ❌ | ❌ | `aStock` 共 4602 只；`ksh`/`cyb` 板块号间歇可用 |
| **同花顺** | ❌ | ✅ 约 140 根 | ✅ 报价（**含股票名称**） | ✅ | ✅ | ✅ | ✅ | **速度最快（实测 0.03 s/只）**，是补齐科创板/北交所的关键 |
| 新浪 | ✅ 三 node 并集 | ✅ 不复权 | ✅ | ✅ | ✅ | ✅ | ✅ | 列表接口有反爬限流（HTTP 456，约 10 分钟） |

**为什么同花顺是关键补充**：实测当东方财富被网络阻断、新浪被反爬限流时，
腾讯的列表缺科创板与北交所，而**同花顺对四个板块都提供日线**
（含北交所 `920xxx`），且是唯一同时给出**股票中文名称**的报价源。

同花顺日线的已知局限：接口固定返回最近约 140 个交易日，
少于其他源的 250 根；策略所需的最长回看窗口为 120 日，因此足够使用。

#### 故障转移规则

1. 取股票列表、取日线、取实时行情**各自独立**在多个源之间切换；
2. 某个源返回错误、超时、**空结果**或**数据量明显不足**时，立即尝试下一个源；
3. 连续失败的源进入**指数退避冷却**（3s → 6s → 12s … 上限 120s），冷却期被跳过，
   冷却结束后自动恢复参与，避免永久剔除临时抽风的源；
4. **从未成功过**的源（例如所在网络阻断了该域名）在失败达阈值后**长期跳过**，
   否则每个请求都要为它白等一个连接超时（实测约 1 秒）。一旦它成功过一次，
   即恢复为第 3 条的正常退避策略；
5. 只有当**全部真实源**都失败时，才降级为 `synthetic` 并在响应中标注。

> 若网络策略变化（如新开了某域名的出网权限），可在设置页点「重新探测」，
> 或调用 `GET /api/v1/sources?probe=true` —— 它会**先重置健康度再探测**，
> 使被跳过的源重新参与，无需重启服务。

#### 「数据量不足」也算取数失败（重要）

第 2 条中的「数据量明显不足」指单只日线少于 `MIN_BARS`（默认 20 根）。

这不是洁癖，而是真实踩到的坑：**腾讯日线接口对北交所（`920xxx`）只返回 1 根当日 K 线**，
既不报错也不返回空。若按「非空即成功」处理，这 343 只北交所股票会带着 1 根数据
进入策略引擎，所有均线、120 日位置、回调天数判据全部失真 —— 表现为
「股票进了池子却永远出不了信号」，属于最难发现的静默数据缺失。

判定为取数失败后会切换到其他源：实测**新浪对同一批标的有完整的 250 根历史**，
切换后北交所股票即可正常参与战法计算。

#### 板块完整性校验

各源的市场覆盖范围不同（均为实测结论）：

| 源 | 主板 | 创业板 | 科创板 | 北交所 | 说明 |
|---|---|---|---|---|---|
| 东方财富 | ✅ | ✅ | ✅ | ✅ | 覆盖最全，但部分网络环境阻断该域名 |
| 腾讯排行榜 | ✅ | ✅ | ❌ | ❌ | `data.total≈4602`，只有 `GP-A` 与 `GP-A-CYB` |
| 新浪（`hs_a`+`sh_a`+`sz_a` 并集） | ✅ | ✅ | ✅ | ✅ | 实测 5561 只，四板块齐全 |

因此 `ResilientProvider` **不会「先到先得」**：它会逐个源尝试、统计各板块数量，
优先采用覆盖最全的那一份，并在日志中明确指出哪个源缺哪个板块。
若所有源都无法覆盖某个板块，会打印 WARNING 显式告警（而不是静默遗漏）。

**残缺列表不会落盘快照**：只有四个板块齐全时才把股票列表写入
`data/stock_list.json`（有效期 1 天）。否则一次被上游限流导致的残缺列表会被缓存
24 小时，演变成持续性的板块遗漏。

#### 股票池覆盖范围

`UNIVERSE_SIZE=0`（默认）表示**不限量、覆盖全部 A 股**。实测全市场规模与板块分布：

| 板块 | 数量 | 涨停幅度 |
|---|---|---|
| 主板（沪 `600/601/603/605` + 深 `000/001/002/003`） | 3195 | 10% |
| 创业板（`300/301/302`） | 1407 | 20% |
| 科创板（`688/689`） | 616 | 20% |
| 北交所（`920` 及存量 `43x/83x/87x/88x`） | 343 | 30% |
| **合计** | **5561** | — |

> 代码段→板块的映射经腾讯 `stock_type` 标记实测确认（`GP-A` / `GP-A-CYB` / `GP-A-KCB`）。
> 尤其注意 **`920` 必须归北交所而非深交所**：否则涨跌停幅度会按 10% 计算（实际 30%），
> 直接影响涨停判定。

#### 股票池来源与规模

- **股票列表**优先取自支持全市场列表的源（东方财富 / 新浪），
  结果按成交额降序截取前 `UNIVERSE_SIZE` 只并落盘快照（有效期 1 天）；
- 腾讯不提供全市场列表，因此其 `supports_stock_list = false`，
  由故障转移层自动改用其他源获取列表、再用腾讯取日线。


---

## 4. 前端路由契约

| 路径 | 页面 | 主接口 |
|---|---|---|
| `/` | 市场总览 Dashboard | `market/overview`, `signals?pageSize=5` |
| `/signals` | 信号选股（核心） | `signals`, `scan`, `scan/{id}` |
| `/stocks` | 股票池浏览 | `stocks` |
| `/stock/:code` | 个股详情 | `stocks/{code}`, `signals/{code}` |
| `/backtest` | 回测分析 | `backtest` |
| `/pool` | 自选低吸池 | `pool` |
| `/rules` | 战法规则与参数 | `rules`, `settings` |
| `/settings` | 系统设置 | `settings`, `health` |

---

## 5. 后端环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `APP_HOST` | `0.0.0.0` | 监听地址 |
| `APP_PORT` | `8000` | 监听端口 |
| `DATA_SOURCE_MODE` | `auto` | `auto` / `eastmoney` / `synthetic` |
| `DATA_DIR` | `./data` | 缓存与配置目录（相对路径） |
| `CACHE_TTL_SECONDS` | `300` | 行情缓存有效期 |
| `UNIVERSE_SIZE` | `300` | 合成数据股票数量 |
| `HTTP_TIMEOUT` | `10` | 数据源请求超时（秒） |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | 开发态跨域白名单 |

---

## 6. 交付约束（对两端均适用）

1. **禁止绝对地址**：不得出现 `http://localhost:8000`、写死的 IP、`D:/...`、`C:\...` 等。一律使用相对路径、环境变量或 `Path(__file__).resolve().parent` 推导。
2. **禁止在源码中出现任何密钥**；统一走环境变量。
3. 后端所有路径基于 `BASE_DIR = Path(__file__).resolve().parent` 推导。
4. 前端所有请求走 `src/api/client.ts` 中的 `BASE = '/api/v1'` 常量。
5. 数值展示：涨幅带 `+/-` 与颜色（涨红跌绿，中国习惯）；百分比保留 2 位小数；金额自动单位化（万/亿）。
6. 中文为主，界面文案不得出现英文占位符（`TODO`、`Lorem`）。
