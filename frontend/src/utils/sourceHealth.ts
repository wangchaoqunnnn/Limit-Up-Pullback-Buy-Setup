/**
 * 数据源健康度（docs/API.md 3.15 / 3.17 / 3.18）的视图模型与防御性归一化。
 *
 * 后端接口可能暂时不可用或返回字段不完整，此处一律容错：
 * 缺字段 → null / 空数组 / 「—」，UI 不会因 undefined 崩溃。
 */
import type { BadgeTone } from './constants';
import { dataSourceLabel, dataSourceShortLabel } from './constants';

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function asText(value: unknown): string | null {
  return typeof value === 'string' && value.trim() !== '' ? value : null;
}

function asNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim() !== '' && Number.isFinite(Number(value))) {
    return Number(value);
  }
  return null;
}

function asBool(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null;
}

/* ------------------------------ 单源健康度 ------------------------------ */

export interface SourceHealthView {
  name: string;
  label: string;
  available: boolean;
  consecutiveFailures: number;
  totalSuccess: number;
  totalFailure: number;
  /** 0~1 比例，无法解析时为 null */
  successRate: number | null;
  cooldownSeconds: number;
  lastError: string | null;
  lastSuccessAt: string | null;
}

export function toSourceHealthView(raw: unknown, index = 0): SourceHealthView | null {
  if (!isRecord(raw)) return null;
  const name = asText(raw.name) ?? `源 ${index + 1}`;
  return {
    name,
    label: dataSourceShortLabel(name),
    available: asBool(raw.available) ?? false,
    consecutiveFailures: asNumber(raw.consecutiveFailures) ?? 0,
    totalSuccess: asNumber(raw.totalSuccess) ?? 0,
    totalFailure: asNumber(raw.totalFailure) ?? 0,
    successRate: asNumber(raw.successRate),
    cooldownSeconds: asNumber(raw.cooldownSeconds) ?? 0,
    lastError: asText(raw.lastError),
    lastSuccessAt: asText(raw.lastSuccessAt),
  };
}

export function normalizeSourceHealthList(raw: unknown): SourceHealthView[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .map((item, index) => toSourceHealthView(item, index))
    .filter((item): item is SourceHealthView => item !== null);
}

/** 成功率（0~1）→ 百分比整数文案 */
export function fmtSuccessRate(rate: number | null): string {
  if (rate === null) return '—';
  const pct = rate <= 1 ? rate * 100 : rate;
  return `${pct.toFixed(0)}%`;
}

export function sourceHealthTone(view: Pick<SourceHealthView, 'available' | 'cooldownSeconds'>): BadgeTone {
  if (view.cooldownSeconds > 0) return 'gold';
  return view.available ? 'teal' : 'danger';
}

export function sourceHealthText(view: Pick<SourceHealthView, 'available' | 'cooldownSeconds'>): string {
  if (view.cooldownSeconds > 0) return '冷却中';
  return view.available ? '可用' : '不可用';
}

/* ------------------------------ K 线缓存 ------------------------------ */

export interface KlineCacheView {
  /** 后端是否返回了缓存统计对象 */
  present: boolean;
  enabled: boolean | null;
  engine: string | null;
  file: string | null;
  codes: number | null;
  bars: number | null;
  latestDate: string | null;
  bytes: number | null;
}

export const EMPTY_KLINE_CACHE: KlineCacheView = {
  present: false,
  enabled: null,
  engine: null,
  file: null,
  codes: null,
  bars: null,
  latestDate: null,
  bytes: null,
};

export function toKlineCacheView(raw: unknown): KlineCacheView {
  // 后端在缓存未就绪时会返回空对象 {}，此时视为「无统计数据」，UI 显示「—」
  if (!isRecord(raw) || Object.keys(raw).length === 0) return EMPTY_KLINE_CACHE;
  return {
    present: true,
    enabled: asBool(raw.enabled),
    engine: asText(raw.engine),
    file: asText(raw.file),
    codes: asNumber(raw.codes),
    bars: asNumber(raw.bars),
    latestDate: asText(raw.latestDate),
    bytes: asNumber(raw.bytes),
  };
}

/* ------------------------------ 整体状态 ------------------------------ */

export interface SourcePickView {
  operation: string;
  source: string;
  label: string;
}

export interface SourceStatusView {
  /** 接口是否返回了有效对象 */
  ok: boolean;
  mode: string | null;
  active: string | null;
  activeLabel: string;
  usingFallback: boolean;
  order: string[];
  sources: SourceHealthView[];
  lastPickByOperation: SourcePickView[];
  universeSize: number | null;
  universeSource: string | null;
  cache: KlineCacheView;
  ttlSeconds: number | null;
  refreshIntervalSeconds: number | null;
}

export const EMPTY_SOURCE_STATUS: SourceStatusView = {
  ok: false,
  mode: null,
  active: null,
  activeLabel: '—',
  usingFallback: false,
  order: [],
  sources: [],
  lastPickByOperation: [],
  universeSize: null,
  universeSource: null,
  cache: EMPTY_KLINE_CACHE,
  ttlSeconds: null,
  refreshIntervalSeconds: null,
};

/** GET /sources 或 /settings 的 sources/order/… 归一化为可直接渲染的视图 */
export function normalizeSourceStatus(raw: unknown): SourceStatusView {
  if (!isRecord(raw)) return EMPTY_SOURCE_STATUS;

  const active = asText(raw.active) ?? asText(raw.dataSourceActive);
  const orderRaw = Array.isArray(raw.order) ? raw.order : Array.isArray(raw.dataSourceOrder) ? raw.dataSourceOrder : [];
  const order = orderRaw.map((item) => asText(item)).filter((item): item is string => item !== null);

  const picks: SourcePickView[] = [];
  if (isRecord(raw.lastPickByOperation)) {
    Object.entries(raw.lastPickByOperation).forEach(([operation, source]) => {
      const value = asText(source);
      if (!value) return;
      picks.push({ operation, source: value, label: dataSourceShortLabel(value) });
    });
  }

  return {
    ok: true,
    mode: asText(raw.mode) ?? asText(raw.dataSourceMode),
    active,
    activeLabel: active ? dataSourceShortLabel(active) : '—',
    usingFallback: asBool(raw.usingFallback) ?? active === 'synthetic',
    order,
    sources: normalizeSourceHealthList(raw.sources),
    lastPickByOperation: picks,
    universeSize: asNumber(raw.universeSize),
    universeSource: asText(raw.universeSource),
    cache: toKlineCacheView(raw.cache ?? raw.klineCache),
    ttlSeconds: asNumber(raw.ttlSeconds) ?? asNumber(raw.effectiveTtlSeconds),
    refreshIntervalSeconds: asNumber(raw.refreshIntervalSeconds),
  };
}

/* ------------------------------ 顶栏生效源徽标 ------------------------------ */

export interface ActiveSourceView {
  name: string | null;
  label: string;
  tone: BadgeTone;
  /** 是否处于降级演示态（全部真实源不可用） */
  isFallback: boolean;
  title: string;
}

/**
 * 顶栏数据源徽标：synthetic / usingFallback → 醒目警示；
 * eastmoney / tencent / sina → 「东方财富」「腾讯财经」「新浪财经」+ 成功色调。
 */
export function activeSourceView(active: unknown, usingFallback?: unknown): ActiveSourceView {
  const name = asText(active);
  const isFallback = usingFallback === true || name === 'synthetic';
  const label = isFallback ? '演示数据' : name ? dataSourceShortLabel(name) : '数据源未知';
  const tone: BadgeTone = isFallback ? 'violet' : name === 'cache' ? 'neutral' : name ? 'teal' : 'neutral';
  const title = isFallback
    ? '全部真实数据源不可用，当前为内置合成演示数据（非真实行情，不可用于实盘判断）。可在「系统设置 → 数据源健康度」查看各源状态。'
    : name
      ? `当前生效数据源：${dataSourceLabel(name)}`
      : '尚未获取到数据源信息，后端可能未就绪。';
  return { name, label, tone, isFallback, title };
}
