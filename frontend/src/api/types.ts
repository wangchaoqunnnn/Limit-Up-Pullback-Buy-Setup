/**
 * 与 docs/API.md (v1.0.0, FROZEN) 一一对应的 TypeScript 类型定义。
 * 所有字段名、枚举取值严格照契约书写，未在契约中出现的字段一律不得在此臆造。
 */

/* ============================ 1. 枚举 ============================ */

export type LimitUpType =
  | 'QUALITY'
  | 'ONE_WORD'
  | 'TAIL_SNEAK'
  | 'WEAK_SEAL'
  | 'HIGH_POSITION'
  | 'CONSECUTIVE'
  | 'ST_LIMIT'
  | 'NONE';

export type SignalKey =
  | 'volume_shrink'
  | 'support_hold'
  | 'intraday_stabilize'
  | 'kline_bottom'
  | 'sector_resonance';

export type Verdict = 'BUY' | 'WATCH' | 'REJECT';

export type Market = 'SH' | 'SZ' | 'BJ';

export type SentimentLevel = '冰点' | '偏冷' | '中性' | '偏暖' | '过热';

/**
 * 数据源标识（docs/API.md 0.4 + 3.18）：
 * eastmoney / tencent / sina 为三个真实行情源，synthetic 仅在全部真实源不可用时启用，
 * cache 表示命中本地文件缓存（历史契约 0.4 保留值，用于兼容旧响应）。
 */
export type DataSource = 'eastmoney' | 'tencent' | 'sina' | 'synthetic' | 'cache';

export type DataSourceMode = 'auto' | 'eastmoney' | 'synthetic';

/** 市场时段（docs/API.md 3.16） */
export type MarketPhase =
  | 'pre_open'
  | 'call_auction'
  | 'morning'
  | 'lunch_break'
  | 'afternoon'
  | 'closed'
  | 'holiday';

export type ScanStatus = 'pending' | 'running' | 'finished' | 'failed';

export type PoolStatus = 'watching' | 'triggered' | 'stopped' | 'target';

export type SortField = 'score' | 'pullbackDays' | 'pctChg' | 'turnover';

export type SortOrder = 'asc' | 'desc';

export type CriteriaGroupKey = 'limitUp' | 'entry' | 'discipline' | 'avoid';

/* ============================ 0. 统一信封 ============================ */

export interface Envelope<T> {
  ok: boolean;
  data: T | null;
  message: string | null;
  serverTime?: string | null;
}

/* ============================ 2. 数据模型 ============================ */

export interface StockMeta {
  code: string;
  name: string;
  market: Market;
  board: string;
  industry: string;
  isSt: boolean;
  limitPct: number;
}

export interface Candle {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  preClose: number;
  pctChg: number;
  volume: number;
  amount: number;
  turnover: number;
  volRatio: number;
  ma5: number | null;
  ma10: number | null;
  ma20: number | null;
  ma60: number | null;
  isLimitUp: boolean;
}

export type SignalMetrics = Record<string, unknown>;

export interface SignalDetail {
  key: SignalKey;
  name: string;
  passed: boolean;
  score: number;
  weight: number;
  contribution: number;
  detail: string;
  metrics: SignalMetrics;
}

export interface SupportLevels {
  limitOpen: number | null;
  strongHalf: number | null;
  ma5: number | null;
  ma10: number | null;
  ma20: number | null;
  activeSupport: number | null;
  activeSupportName: string | null;
  distanceToSupportPct: number | null;
}

export interface TradePlan {
  buyLow: number | null;
  buyHigh: number | null;
  stopLoss: number | null;
  takeProfit1: number | null;
  takeProfit2: number | null;
  riskReward: number | null;
  positionPct: number | null;
  batchCount: number | null;
}

export interface RiskInfo {
  riskLevel: string;
  riskPoints: string[];
}

export interface StockSignal {
  meta: StockMeta;
  lastClose: number;
  lastDate: string;
  limitUpDate: string | null;
  limitUpType: LimitUpType;
  limitUpRejectReason: string | null;
  limitUpClose: number | null;
  pullbackDays: number;
  pullbackPct: number;
  retraceRatio: number | null;
  score: number;
  verdict: Verdict;
  signals: SignalDetail[];
  signalSummary: string;
  support: SupportLevels;
  plan: TradePlan;
  risk: RiskInfo;
  sparkline: number[];
  sparklineDates: string[];
}

export interface PoolItem {
  id: string;
  meta: StockMeta;
  limitUpDate: string | null;
  limitUpType: LimitUpType;
  addedAt: string;
  addedPrice: number | null;
  buyLow: number | null;
  buyHigh: number | null;
  stopLoss: number | null;
  takeProfit1: number | null;
  takeProfit2: number | null;
  note: string | null;
  lastClose: number | null;
  lastDate: string | null;
  pnlPct: number;
  status: PoolStatus;
  statusText: string;
}

export interface SignalWeights {
  volume_shrink: number;
  support_hold: number;
  intraday_stabilize: number;
  kline_bottom: number;
  sector_resonance: number;
}

export interface RuleSet {
  version: string;
  weights: SignalWeights;
  buyScore: number;
  watchScore: number;
  maxHighPositionRatio: number;
  minVolRatio: number;
  minTurnover: number;
  maxTurnover: number;
  minPullbackDays: number;
  maxPullbackDays: number;
}

/* ============================ 3. 接口响应 ============================ */

/** GET /health —— 不走统一信封 */
export interface HealthData {
  status: string;
  version: string;
  uptimeSeconds: number;
  dataSource: DataSource;
  lastSyncAt: string | null;
  universeSize: number;
}

export interface Sentiment {
  score: number;
  level: SentimentLevel;
  limitUpCount: number;
  limitDownCount: number;
  brokenBoardCount: number;
  brokenRate: number;
  upCount: number;
  downCount: number;
  flatCount: number;
  avgPctChg: number;
  totalAmount: number;
}

export interface IndexQuote {
  code: string;
  name: string;
  close: number;
  pctChg: number;
  sparkline: number[];
}

export interface ScoreDistribution {
  buy: number;
  watch: number;
  reject: number;
}

export interface IndustryHeat {
  name: string;
  pctChg: number;
  limitUpCount: number;
  sentimentScore: number;
  leader: string;
}

/** GET /market/overview */
export interface MarketOverview {
  dataSource: DataSource;
  updatedAt: string;
  tradeDate: string;
  sentiment: Sentiment;
  indexes: IndexQuote[];
  scoreDistribution: ScoreDistribution;
  topIndustries: IndustryHeat[];
}

/** GET /stocks */
export interface StockListItem {
  meta: StockMeta;
  lastClose: number;
  lastDate: string;
  pctChg: number;
  turnover: number;
  amount: number;
  limitUpType: LimitUpType;
  sparkline: number[];
  score: number | null;
  verdict: Verdict | null;
}

export interface StockListData {
  total: number;
  page: number;
  pageSize: number;
  items: StockListItem[];
}

export interface LimitUpHistoryItem {
  date: string;
  type: LimitUpType;
  pullbackDays: number;
  score: number;
  verdict: Verdict;
}

export interface IndustryDetail {
  name: string;
  sentimentScore: number;
  pctChg: number;
  limitUpCount: number;
  memberCount: number;
  trend: number[];
}

/** GET /stocks/{code} */
export interface StockDetailData {
  meta: StockMeta;
  dataSource: DataSource;
  candles: Candle[];
  signal: StockSignal | null;
  signals: SignalDetail[];
  limitUpHistory: LimitUpHistoryItem[];
  industry: IndustryDetail | null;
  explain: string | null;
}

export interface SignalStatistics {
  avgScore: number;
  buyCount: number;
  watchCount: number;
  avgPullbackDays: number;
  signalPassRate: Partial<Record<SignalKey, number>>;
}

/** GET /signals */
export interface SignalsData {
  dataSource: DataSource;
  scannedAt: string;
  tradeDate: string;
  universeSize: number;
  matched: number;
  total: number;
  page: number;
  pageSize: number;
  ruleSet: RuleSet;
  items: StockSignal[];
  statistics: SignalStatistics;
}

/** POST /scan */
export interface ScanTaskStart {
  taskId: string;
  status: ScanStatus;
  progress: number;
  total: number;
  message: string;
}

/** GET /scan/{taskId} */
export interface ScanTaskState {
  taskId: string;
  status: ScanStatus;
  progress: number;
  total: number;
  startedAt: string | null;
  finishedAt: string | null;
  matched: number | null;
  message: string | null;
}

export interface ReturnCurvePoint {
  date: string;
  index: number;
  equity: number;
}

export interface DistributionBucket {
  bucket: string;
  count: number;
}

export interface BacktestBySignal {
  key: SignalKey;
  name: string;
  sampleSize: number;
  winRate: number;
  avgReturn: number;
}

export interface BacktestTrade {
  code: string;
  name: string;
  signalDate: string;
  entryPrice: number;
  exitPrice: number;
  returnPct: number;
  maxGainPct: number;
  maxLossPct: number;
  score: number;
  holdDays: number;
}

/** GET /backtest */
export interface BacktestData {
  dataSource: DataSource;
  lookbackDays: number;
  minScore: number;
  horizon: number;
  sampleSize: number;
  winRate: number;
  avgReturn: number;
  avgWin: number;
  avgLoss: number;
  profitFactor: number;
  maxDrawdown: number;
  avgMaxGain: number;
  avgMaxLoss: number;
  returnCurve: ReturnCurvePoint[];
  returnDistribution: DistributionBucket[];
  bySignal: BacktestBySignal[];
  trades: BacktestTrade[];
}

export interface CriterionItem {
  key: string;
  name: string;
  enabled: boolean;
  desc: string;
}

export type CriteriaGroups = Record<CriteriaGroupKey, CriterionItem[]>;

/** GET /rules */
export interface RulesData {
  ruleSet: RuleSet;
  criteria: CriteriaGroups;
}

/** PUT /rules 请求体：RuleSet 的部分字段 */
export type RuleSetPatch = Partial<Omit<RuleSet, 'version'>>;

/** GET /pool */
export interface PoolSummary {
  avgPnlPct: number;
  triggeredCount: number;
  stoppedCount: number;
  targetCount: number;
}

export interface PoolData {
  total: number;
  items: PoolItem[];
  summary: PoolSummary;
}

/** POST /pool 请求体（字段均可省略，省略时服务端按战法自动计算） */
export interface PoolCreateBody {
  code: string;
  buyLow?: number;
  buyHigh?: number;
  stopLoss?: number;
  takeProfit1?: number;
  takeProfit2?: number;
  note?: string;
}

/** GET /market/clock —— 市场时钟（3.16） */
export interface MarketClock {
  now: string;
  tradeDate: string;
  isTradingDay: boolean;
  isOpen: boolean;
  phase: MarketPhase;
  phaseText: string;
  /** 前端据此决定是否开启自动刷新；收盘 / 休市为 false */
  shouldPoll: boolean;
  /** 开盘期间建议的刷新间隔（秒） */
  intervalSeconds: number;
  nextOpenAt: string | null;
  nextCloseAt: string | null;
  timezone: string;
}

/** 单个数据源的健康度（3.15 / 3.17 的 sources[]） */
export interface SourceHealth {
  name: DataSource;
  consecutiveFailures: number;
  totalSuccess: number;
  totalFailure: number;
  /** 0~1 比例 */
  successRate: number;
  lastError: string | null;
  /** 冷却剩余秒数，0 表示未在冷却 */
  cooldownSeconds: number;
  available: boolean;
  lastSuccessAt: string | null;
}

/** 日线增量缓存（SQLite）规模（3.15 klineCache） */
export interface KlineCacheStats {
  enabled: boolean;
  engine: string;
  file: string;
  codes: number;
  bars: number;
  latestDate: string | null;
  bytes: number;
}

/** GET /sources —— 数据源状态与健康度（3.17） */
export interface SourceStatus {
  mode: DataSourceMode;
  active: DataSource;
  usingFallback: boolean;
  order: DataSource[];
  sources: SourceHealth[];
  /** 每个操作最近一次实际成功使用的数据源 */
  lastPickByOperation: Record<string, string>;
  universeSize: number;
  universeSource: DataSource | null;
  cache: KlineCacheStats | null;
  ttlSeconds: number;
  refreshIntervalSeconds: number;
  clock: MarketClock;
}

/** GET /settings */
export interface SettingsData {
  appName: string;
  version: string;
  dataSourceMode: DataSourceMode;
  dataSourceActive: DataSource;
  /** 真实数据源优先级顺序，故障转移按此尝试 */
  dataSourceOrder: DataSource[];
  /** 是否已降级为合成演示数据（仅当全部真实源不可用） */
  usingFallback: boolean;
  /** 各源健康度 */
  sources: SourceHealth[];
  universeSize: number;
  universeSource: DataSource | null;
  cacheTtlSeconds: number;
  /** 按交易时段折算后的实际缓存 TTL（开盘 = 刷新间隔） */
  effectiveTtlSeconds: number;
  /** 开盘期间的刷新间隔（默认 30 秒） */
  refreshIntervalSeconds: number;
  klineCache: KlineCacheStats | null;
  /** 市场时钟，字段同 GET /market/clock */
  clock: MarketClock;
  syntheticEnabled: boolean;
  serverTime: string;
  timezone: string;
}

/* ============================ 4. 前端内部类型 ============================ */

export interface SignalQuery {
  verdict?: string;
  industry?: string;
  minScore?: number;
  maxScore?: number;
  limitUpType?: string;
  sort?: SortField;
  order?: SortOrder;
  page?: number;
  pageSize?: number;
  refresh?: boolean;
}

export interface StockQuery {
  keyword?: string;
  industry?: string;
  page?: number;
  pageSize?: number;
}

export interface BacktestQuery {
  lookbackDays?: number;
  minScore?: number;
  horizon?: number;
}
