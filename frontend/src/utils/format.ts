/**
 * 数值 / 日期格式化工具。
 * 约定：比例值（0.0042）→ 百分比字符串（+0.42%）；金额自动单位化（万 / 亿）；成交量单位「手」。
 */
import { metricMeta } from './constants';

const DASH = '—';

export function isNum(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v);
}

/** 安全取数，非法值返回 null */
export function num(v: unknown): number | null {
  if (isNum(v)) return v;
  if (typeof v === 'string' && v.trim() !== '' && Number.isFinite(Number(v))) return Number(v);
  return null;
}

export function clamp(v: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, v));
}

/** 价格：2 位小数 */
export function fmtPrice(v: unknown, digits = 2): string {
  const n = num(v);
  return n === null ? DASH : n.toFixed(digits);
}

/** 普通数字：千分位 */
export function fmtNum(v: unknown, digits = 2): string {
  const n = num(v);
  if (n === null) return DASH;
  return n.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

/** 整数：千分位、无小数 */
export function fmtInt(v: unknown): string {
  const n = num(v);
  if (n === null) return DASH;
  return Math.round(n).toLocaleString('zh-CN');
}

export type Direction = 'up' | 'down' | 'flat';

export function direction(v: unknown): Direction {
  const n = num(v);
  if (n === null || n === 0) return 'flat';
  return n > 0 ? 'up' : 'down';
}

/** 比例 → 百分比字符串：0.0042 → +0.42% */
export function fmtPct(v: unknown, digits = 2, withSign = true): string {
  const n = num(v);
  if (n === null) return DASH;
  const pct = n * 100;
  const sign = withSign && pct > 0 ? '+' : '';
  return `${sign}${pct.toFixed(digits)}%`;
}

/** 已是百分数 → 字符串：4.12 → 4.12% */
export function fmtPctValue(v: unknown, digits = 2, withSign = false): string {
  const n = num(v);
  if (n === null) return DASH;
  const sign = withSign && n > 0 ? '+' : '';
  return `${sign}${n.toFixed(digits)}%`;
}

/** 带符号百分比 + 方向类名：0.0121 → { text: '+1.21%', dir: 'up' } */
export function pctView(v: unknown, digits = 2): { text: string; dir: Direction } {
  return { text: fmtPct(v, digits), dir: direction(v) };
}

/** 金额自动单位化：元 → 万 / 亿 */
export function fmtAmount(v: unknown, digits = 2): string {
  const n = num(v);
  if (n === null) return DASH;
  const abs = Math.abs(n);
  if (abs >= 1e12) return `${(n / 1e12).toFixed(digits)}万亿`;
  if (abs >= 1e8) return `${(n / 1e8).toFixed(digits)}亿`;
  if (abs >= 1e4) return `${(n / 1e4).toFixed(digits)}万`;
  return `${n.toFixed(0)}元`;
}

/** 成交量：单位「手」→ 万手 / 亿手 */
export function fmtVolume(v: unknown, digits = 2): string {
  const n = num(v);
  if (n === null) return DASH;
  const abs = Math.abs(n);
  if (abs >= 1e8) return `${(n / 1e8).toFixed(digits)}亿手`;
  if (abs >= 1e4) return `${(n / 1e4).toFixed(digits)}万手`;
  return `${n.toFixed(0)}手`;
}

/** 评分：1 位小数 */
export function fmtScore(v: unknown): string {
  const n = num(v);
  return n === null ? DASH : n.toFixed(1);
}

/** 倍数：2 位小数 + × */
export function fmtTimes(v: unknown, digits = 2): string {
  const n = num(v);
  return n === null ? DASH : `${n.toFixed(digits)}×`;
}

/** 数量：整数 */
export function fmtCount(v: unknown): string {
  const n = num(v);
  if (n === null) return DASH;
  return Math.round(n).toLocaleString('zh-CN');
}

export function fmtDate(v: unknown): string {
  if (typeof v !== 'string' || !v) return DASH;
  return v.length >= 10 ? v.slice(0, 10) : v;
}

/** 短日期：MM-DD */
export function fmtDateShort(v: unknown): string {
  const d = fmtDate(v);
  return d === DASH ? DASH : d.slice(5);
}

export function fmtDateTime(v: unknown): string {
  if (typeof v !== 'string' || !v) return DASH;
  const t = new Date(v);
  if (Number.isNaN(t.getTime())) return v.slice(0, 16).replace('T', ' ');
  const pad = (x: number) => String(x).padStart(2, '0');
  return `${t.getFullYear()}-${pad(t.getMonth() + 1)}-${pad(t.getDate())} ${pad(t.getHours())}:${pad(t.getMinutes())}:${pad(t.getSeconds())}`;
}

export function fmtDateTimeShort(v: unknown): string {
  if (typeof v !== 'string' || !v) return DASH;
  const t = new Date(v);
  if (Number.isNaN(t.getTime())) return v.slice(0, 16).replace('T', ' ');
  const pad = (x: number) => String(x).padStart(2, '0');
  return `${pad(t.getMonth() + 1)}-${pad(t.getDate())} ${pad(t.getHours())}:${pad(t.getMinutes())}`;
}

export function fmtTime(v: unknown): string {
  if (typeof v !== 'string' || !v) return DASH;
  const t = new Date(v);
  if (Number.isNaN(t.getTime())) return DASH;
  const pad = (x: number) => String(x).padStart(2, '0');
  return `${pad(t.getHours())}:${pad(t.getMinutes())}:${pad(t.getSeconds())}`;
}

/** 字节 → 人类可读（B / KB / MB / GB） */
export function fmtBytes(v: unknown): string {
  const n = num(v);
  if (n === null) return DASH;
  const abs = Math.abs(n);
  if (abs >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(2)} GB`;
  if (abs >= 1024 ** 2) return `${(n / 1024 ** 2).toFixed(2)} MB`;
  if (abs >= 1024) return `${(n / 1024).toFixed(2)} KB`;
  return `${Math.round(n)} B`;
}

/** 毫秒时间戳 → HH:mm:ss（局部刷新时间展示） */
export function fmtClockMs(ms: unknown): string {
  const n = num(ms);
  if (n === null) return DASH;
  const t = new Date(n);
  if (Number.isNaN(t.getTime())) return DASH;
  const pad = (x: number) => String(x).padStart(2, '0');
  return `${pad(t.getHours())}:${pad(t.getMinutes())}:${pad(t.getSeconds())}`;
}

/** 运行时长：秒 → 天/小时/分/秒 */
export function fmtUptime(seconds: unknown): string {
  const n = num(seconds);
  if (n === null) return DASH;
  const s = Math.max(0, Math.floor(n));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (d > 0) return `${d} 天 ${h} 小时 ${m} 分`;
  if (h > 0) return `${h} 小时 ${m} 分 ${sec} 秒`;
  if (m > 0) return `${m} 分 ${sec} 秒`;
  return `${sec} 秒`;
}

/** metrics 自由对象的取值渲染（无键名信息时的通用兜底） */
export function fmtMetricValue(v: unknown): string {
  if (v === null || v === undefined) return DASH;
  if (typeof v === 'boolean') return v ? '是' : '否';
  if (typeof v === 'number') {
    if (!Number.isFinite(v)) return DASH;
    if (Number.isInteger(v)) return fmtInt(v);
    return Math.abs(v) >= 1000 ? fmtNum(v, 1) : v.toFixed(2);
  }
  if (typeof v === 'string') return v || DASH;
  if (Array.isArray(v)) return v.map((x) => fmtMetricValue(x)).join(' / ');
  return JSON.stringify(v);
}

/**
 * 按 metrics 键的语义格式化取值（价格 / 百分比 / 次数 / 布尔），
 * 键名 → 中文标签与格式的映射见 utils/constants.ts 的 METRIC_META。
 */
export function fmtMetricByKey(key: string, value: unknown): string {
  const meta = metricMeta(key);
  if (value === null || value === undefined) return DASH;
  switch (meta.format) {
    case 'bool':
      return value === true ? '是' : value === false ? '否' : fmtMetricValue(value);
    case 'price':
      return withUnit(fmtPrice(value), meta.unit);
    case 'percent': {
      const n = num(value);
      if (n === null) return DASH;
      const pct = n * 100;
      const sign = meta.signed && pct > 0 ? '+' : '';
      return withUnit(`${sign}${pct.toFixed(2)}%`, meta.unit);
    }
    case 'ratio': {
      const n = num(value);
      if (n === null) return DASH;
      return withUnit(`${(n * 100).toFixed(0)}%`, meta.unit);
    }
    case 'count':
      return withUnit(fmtInt(value), meta.unit);
    case 'score':
      return withUnit(fmtScore(value), meta.unit);
    case 'times':
      return withUnit(fmtNum(value, 2), meta.unit);
    default:
      return withUnit(fmtMetricValue(value), meta.unit);
  }
}

function withUnit(text: string, unit?: string): string {
  return unit ? `${text} ${unit}` : text;
}

/** metrics 键 → 中文标签（未命中时做可读化兜底） */
export { metricLabel, metricMeta } from './constants';

/** 涨跌方向类名 */
export function dirClass(v: unknown): string {
  return direction(v);
}

/** Candle 涨跌方向（相对前收） */
export function candleDir(open: number, close: number, preClose: number): Direction {
  const base = isNum(preClose) && preClose > 0 ? preClose : open;
  if (!isNum(close) || !isNum(base)) return 'flat';
  if (close > base) return 'up';
  if (close < base) return 'down';
  return 'flat';
}
