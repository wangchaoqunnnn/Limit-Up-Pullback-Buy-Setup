/**
 * 枚举中文文案、色调与全局常量。所有映射严格对应 docs/API.md 第 1 节枚举定义。
 */
import type {
  DataSource,
  DataSourceMode,
  LimitUpType,
  MarketPhase,
  PoolStatus,
  SentimentLevel,
  SignalKey,
  SortField,
  Verdict,
} from '../api/types';

export const APP_TITLE = '涨停回调低吸战法';
/** 侧栏品牌副标题：需在 216px 侧栏内完整显示，过长会被省略号截断 */
export const APP_SUBTITLE = 'PULLBACK TACTIC';

/** 扫描任务轮询间隔 */
export const SCAN_POLL_INTERVAL_MS = 1200;
/** 健康检查轮询间隔 */
export const HEALTH_POLL_INTERVAL_MS = 10000;
/** 搜索防抖 */
export const SEARCH_DEBOUNCE_MS = 300;
/** 请求超时 */
export const REQUEST_TIMEOUT_MS = 15000;

export type BadgeTone = 'brand' | 'teal' | 'gold' | 'danger' | 'violet' | 'neutral';

/* ---------------------------- 涨停质量分类 ---------------------------- */
export const LIMIT_UP_TYPE_META: Record<LimitUpType, { label: string; short: string; tone: BadgeTone; desc: string }> = {
  QUALITY: {
    label: '优质实体放量首板',
    short: '优质首板',
    tone: 'gold',
    desc: '通过全部硬性门槛，可作为战法标的',
  },
  ONE_WORD: { label: '一字板（缩量）', short: '一字板', tone: 'violet', desc: '开盘即封死，无法低吸参与' },
  TAIL_SNEAK: { label: '尾盘偷袭板', short: '尾盘板', tone: 'violet', desc: '尾盘拉封，封单质量差' },
  WEAK_SEAL: { label: '烂板 / 炸板', short: '烂板', tone: 'danger', desc: '收盘未封住，抛压明显' },
  HIGH_POSITION: { label: '高位板', short: '高位板', tone: 'danger', desc: '处于近 120 日区间高位' },
  CONSECUTIVE: { label: '连板 / 妖股', short: '连板', tone: 'danger', desc: '前 1~2 日已涨停，情绪股' },
  ST_LIMIT: { label: 'ST 股涨停（5% 制度）', short: 'ST 板', tone: 'neutral', desc: '风险警示股，5% 涨跌幅限制' },
  NONE: { label: '非涨停', short: '非涨停', tone: 'neutral', desc: '当日涨幅未达涨停阈值' },
};

/* ------------------------------ 最终结论 ------------------------------ */
export const VERDICT_META: Record<Verdict, { label: string; tone: BadgeTone; desc: string }> = {
  BUY: { label: '可低吸', tone: 'brand', desc: '评分达标且五大信号全部通过' },
  WATCH: { label: '观察', tone: 'gold', desc: '评分接近或信号 4/5 通过，等待确认' },
  REJECT: { label: '放弃', tone: 'neutral', desc: '评分不足或命中硬性否决' },
};

export const VERDICT_OPTIONS: { value: Verdict; label: string }[] = [
  { value: 'BUY', label: '可低吸' },
  { value: 'WATCH', label: '观察' },
  { value: 'REJECT', label: '放弃' },
];

/* ---------------------------- 五大共振信号 ---------------------------- */
export const SIGNAL_KEYS: SignalKey[] = [
  'volume_shrink',
  'support_hold',
  'intraday_stabilize',
  'kline_bottom',
  'sector_resonance',
];

export const SIGNAL_META: Record<SignalKey, { label: string; short: string; weight: number; desc: string }> = {
  volume_shrink: {
    label: '回调持续缩量，量能逐日递减',
    short: '缩量回调',
    weight: 0.25,
    desc: '回调期间量能逐日递减，说明抛压衰竭、主力未出逃',
  },
  support_hold: {
    label: '守住关键支撑，不破安全区间',
    short: '支撑有效',
    weight: 0.25,
    desc: '涨停开盘价 / 实体半分位 / 均线系统构成的安全区间未被有效跌破',
  },
  intraday_stabilize: {
    label: '分时止跌企稳，低点逐步抬高',
    short: '分时企稳',
    weight: 0.15,
    desc: '日内低点不再创新低，说明承接资金开始入场',
  },
  kline_bottom: {
    label: 'K线筑底止跌，小阳十字星收尾',
    short: 'K线筑底',
    weight: 0.2,
    desc: '出现小实体、长下影或十字星，下跌动能衰竭',
  },
  sector_resonance: {
    label: '板块情绪同步回暖，题材有持续性',
    short: '板块共振',
    weight: 0.15,
    desc: '所属板块情绪抬升、涨停家数增加，题材具备延续性',
  },
};

export function signalLabel(key: string): string {
  return SIGNAL_META[key as SignalKey]?.short ?? key;
}

/* ------------------------------ 市场情绪 ------------------------------ */
export const SENTIMENT_LEVEL_META: Record<SentimentLevel, { tone: BadgeTone; desc: string }> = {
  冰点: { tone: 'brand', desc: '情绪极度低迷，涨停稀少、炸板率高' },
  偏冷: { tone: 'brand', desc: '赚钱效应偏弱，宜控制仓位' },
  中性: { tone: 'neutral', desc: '多空相对均衡，结构行情为主' },
  偏暖: { tone: 'gold', desc: '赚钱效应回暖，可适度参与' },
  过热: { tone: 'danger', desc: '情绪过热，注意分歧与回落风险' },
};

export function sentimentTone(level: string): BadgeTone {
  return SENTIMENT_LEVEL_META[level as SentimentLevel]?.tone ?? 'neutral';
}

/* ------------------------------ 低吸池状态 ------------------------------ */
export const POOL_STATUS_META: Record<PoolStatus, { label: string; tone: BadgeTone }> = {
  watching: { label: '观察中', tone: 'brand' },
  triggered: { label: '已触发', tone: 'gold' },
  stopped: { label: '已止损', tone: 'teal' },
  target: { label: '已止盈', tone: 'danger' },
};

export function poolStatusMeta(status: string, fallbackText?: string | null): { label: string; tone: BadgeTone } {
  const meta = POOL_STATUS_META[status as PoolStatus];
  if (meta) return meta;
  return { label: fallbackText ?? status, tone: 'neutral' };
}

/* ------------------------------ 排序字段 ------------------------------ */
export const SORT_OPTIONS: { value: SortField; label: string }[] = [
  { value: 'score', label: '综合评分' },
  { value: 'pullbackDays', label: '回调天数' },
  { value: 'pctChg', label: '当日涨跌幅' },
  { value: 'turnover', label: '换手率' },
];

/* ------------------------------ 涨幅类型筛选项 ------------------------------ */
export const LIMIT_UP_TYPE_OPTIONS: { value: string; label: string }[] = [
  { value: 'QUALITY', label: '优质实体放量首板' },
  { value: 'ONE_WORD', label: '一字板' },
  { value: 'TAIL_SNEAK', label: '尾盘偷袭板' },
  { value: 'WEAK_SEAL', label: '烂板 / 炸板' },
  { value: 'HIGH_POSITION', label: '高位板' },
  { value: 'CONSECUTIVE', label: '连板 / 妖股' },
  { value: 'ST_LIMIT', label: 'ST 股涨停' },
  { value: 'NONE', label: '非涨停' },
  { value: 'ALL', label: '全部类型' },
];

/* ------------------------------ 数据源 ------------------------------ */
export const DATA_SOURCE_LABELS: Record<DataSource, string> = {
  eastmoney: '东方财富实时行情',
  tencent: '腾讯财经实时行情',
  sina: '新浪财经实时行情',
  synthetic: '内置合成演示数据',
  cache: '本地文件缓存',
};

/** 顶栏等窄空间使用的短标签（docs/API.md 3.18 的四个数据源标识） */
export const DATA_SOURCE_SHORT_LABELS: Record<DataSource, string> = {
  eastmoney: '东方财富',
  tencent: '腾讯财经',
  sina: '新浪财经',
  synthetic: '演示数据',
  cache: '缓存数据',
};

export const DATA_SOURCE_MODE_LABELS: Record<DataSourceMode, string> = {
  auto: '自动（优先真实数据，失败降级演示数据）',
  eastmoney: '强制东方财富行情',
  synthetic: '强制合成演示数据',
};

export function dataSourceLabel(source: string | null | undefined): string {
  if (!source) return '未知数据源';
  return DATA_SOURCE_LABELS[source as DataSource] ?? source;
}

export function dataSourceShortLabel(source: string | null | undefined): string {
  if (!source) return '未知数据源';
  return DATA_SOURCE_SHORT_LABELS[source as DataSource] ?? source;
}

export function dataSourceModeLabel(mode: string | null | undefined): string {
  if (!mode) return '—';
  return DATA_SOURCE_MODE_LABELS[mode as DataSourceMode] ?? mode;
}

/* ------------------------------ 市场时段 ------------------------------ */
/** 时段中文兜底：后端未给 phaseText 时按 phase 取值展示（docs/API.md 3.16） */
export const MARKET_PHASE_LABELS: Record<MarketPhase, string> = {
  pre_open: '盘前',
  call_auction: '集合竞价',
  morning: '上午交易',
  lunch_break: '午间休市',
  afternoon: '下午交易',
  closed: '已收盘',
  holiday: '休市日',
};

export function marketPhaseLabel(phase: string | null | undefined): string {
  if (!phase) return '—';
  return MARKET_PHASE_LABELS[phase as MarketPhase] ?? phase;
}

/* ------------------------------ metrics 键名映射 ------------------------------ */
/**
 * 后端 SignalDetail.metrics 的取值格式。
 * - price   价格（2 位小数 + 元）
 * - percent 0~1 比例 → 百分比（如 0.0245 → 2.45%）
 * - ratio   0~1 位置/占比 → 整数百分比（如 0.62 → 62%）
 * - count   次数 / 天数 / 家数
 * - score   分值（1 位小数）
 * - times   倍数
 * - bool    布尔（是 / 否）
 * - text    原样文本
 */
export type MetricFormat = 'price' | 'percent' | 'ratio' | 'count' | 'score' | 'times' | 'bool' | 'text';

export interface MetricMeta {
  label: string;
  unit?: string;
  format: MetricFormat;
  /** 百分比是否强制带正负号（用于涨跌幅类指标） */
  signed?: boolean;
}

/**
 * metrics 键 → 中文标签 / 单位 / 格式。
 * 已按 backend/app/strategy.py 各 signal_* 函数的 metrics 字典穷举（含 docs/API.md 2.3 示例），
 * 另附若干历史/防御性别名，避免出现英文键名直接暴露给用户。
 */
export const METRIC_META: Record<string, MetricMeta> = {
  /* 信号一：回调持续缩量 */
  pullbackDays: { label: '回调天数', unit: '天', format: 'count' },
  maxVolRatioToLimit: { label: '最大量能比', unit: '倍', format: 'times' },
  isMonotonicShrink: { label: '量能逐日递减', format: 'bool' },
  shrinkStreak: { label: '连续缩量天数', unit: '天', format: 'count' },

  /* 信号二：守住关键支撑 */
  limitOpen: { label: '涨停日开盘价', unit: '元', format: 'price' },
  strongHalf: { label: '涨停实体半分位', unit: '元', format: 'price' },
  breakLevel: { label: '破位底线', unit: '元', format: 'price' },
  minPullbackLow: { label: '回调期间最低价', unit: '元', format: 'price' },
  lastClose: { label: '最新收盘价', unit: '元', format: 'price' },
  halfHeld: { label: '守住半分位', format: 'bool' },
  effectiveBreak: { label: '有效跌破', format: 'bool' },

  /* 信号三：分时止跌企稳（日线代理） */
  risingLows: { label: '低点抬高次数', unit: '次', format: 'count' },
  slopeEasing: { label: '下跌斜率趋缓', format: 'bool' },
  closePositionTrend: { label: '收盘位置抬升', format: 'bool' },
  recentClosePosition: { label: '近 3 日收盘位置', format: 'ratio' },

  /* 信号四：K 线筑底 */
  bigBearishCount: { label: '破位大阴线', unit: '根', format: 'count' },
  lastBodyPct: { label: '收尾 K 线实体占比', format: 'percent' },
  centerHolding: { label: '股价重心不下移', format: 'bool' },
  tailNonFalling: { label: '尾部未创新低', format: 'bool' },

  /* 信号五：板块情绪共振 */
  industry: { label: '所属板块', format: 'text' },
  sentimentScore: { label: '板块情绪分', unit: '分', format: 'score' },
  avgPct5: { label: '近 5 日平均涨幅', format: 'percent', signed: true },
  limitUp5Count: { label: '近 5 日板块涨停', unit: '家', format: 'count' },
  memberCount: { label: '板块成分股', unit: '只', format: 'count' },
  upRatio: { label: '板块上涨占比', format: 'ratio' },
  passScore: { label: '板块通过线', unit: '分', format: 'score' },

  /* 防御性别名：契约允许 metrics 为自由对象，遇到旧字段也不显示英文 */
  volList: { label: '量能序列', format: 'text' },
  supportName: { label: '支撑位名称', format: 'text' },
  supportPrice: { label: '支撑价', unit: '元', format: 'price' },
  distancePct: { label: '距支撑', format: 'percent' },
  breakCount: { label: '跌破次数', unit: '次', format: 'count' },
  higherLowCount: { label: '低点抬高次数', unit: '次', format: 'count' },
  lowerHighCount: { label: '高点下移次数', unit: '次', format: 'count' },
  stabilizeScore: { label: '企稳分', unit: '分', format: 'score' },
  bodyRatio: { label: '实体占比', format: 'percent' },
  dojiCount: { label: '十字星数量', unit: '根', format: 'count' },
  closeAboveMa5: { label: '收于 MA5 上方', format: 'bool' },
  sectorPctChg: { label: '板块涨跌幅', format: 'percent', signed: true },
  limitUpCount: { label: '板块涨停家数', unit: '家', format: 'count' },
  trendUp: { label: '趋势向上', format: 'bool' },
  weight: { label: '权重', format: 'ratio' },
  score: { label: '得分', unit: '分', format: 'score' },
  threshold: { label: '阈值', format: 'text' },
  ratio: { label: '比率', format: 'percent' },
};

/** 兜底可读化：camelCase / snake_case / kebab-case → 空格分词，绝不原样输出英文键名 */
export function humanizeMetricKey(key: string): string {
  const text = key
    .replace(/[_-]+/g, ' ')
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/\s+/g, ' ')
    .trim();
  return text || key;
}

export function metricMeta(key: string): MetricMeta {
  const meta = METRIC_META[key];
  if (meta) return meta;
  return { label: humanizeMetricKey(key), format: 'text' };
}

export function metricLabel(key: string): string {
  return metricMeta(key).label;
}

/* ------------------------------ 风险等级 ------------------------------ */
export function riskTone(level: string | null | undefined): BadgeTone {
  switch (level) {
    case '低':
      return 'teal';
    case '中':
      return 'gold';
    case '高':
      return 'danger';
    default:
      return 'neutral';
  }
}

/* ------------------------------ 导航 ------------------------------ */
export interface NavItem {
  to: string;
  label: string;
  icon: string;
  group: string;
  desc: string;
}

export const NAV_ITEMS: NavItem[] = [
  { to: '/', label: '市场总览', icon: 'dashboard', group: '行情', desc: '指数与市场情绪' },
  { to: '/signals', label: '信号选股', icon: 'signals', group: '行情', desc: '五大共振信号筛选' },
  { to: '/stocks', label: '股票池', icon: 'stocks', group: '行情', desc: '全市场行情浏览' },
  { to: '/backtest', label: '回测分析', icon: 'backtest', group: '研究', desc: '历史信号回溯统计' },
  { to: '/pool', label: '自选低吸池', icon: 'pool', group: '研究', desc: '跟踪中的低吸标的' },
  { to: '/rules', label: '战法规则', icon: 'rules', group: '配置', desc: '选股标准与参数' },
  { to: '/settings', label: '系统设置', icon: 'settings', group: '配置', desc: '数据源与偏好' },
];

/** 侧边栏分组顺序 */
export const NAV_GROUPS = ['行情', '研究', '配置'];

/* ------------------------------ 免责声明 ------------------------------ */
export const DISCLAIMER_TITLE = '免责声明';
export const DISCLAIMER_TEXT =
  '本工具仅为技术研究与学习用途，所有信号、评分、支撑位与交易计划均由公开行情数据按固定规则机械计算得出，不构成任何投资建议。股市有风险，入市需谨慎，据此操作风险自担。';
