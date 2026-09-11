/**
 * 市场时钟（docs/API.md 3.16）归一化与展示辅助。
 *
 * 防御性原则：后端可能尚未就绪或字段缺失，任何字段读不到都不得抛错——
 * 归一化后统一返回「可用 / 不可用」两态：不可用时 shouldPoll 一律为 false，
 * 前端自动刷新降级为「不刷新」，绝不空转请求。
 */
import type { MarketClock, MarketPhase } from '../api/types';
import { MARKET_PHASE_LABELS } from './constants';
import { fmtDateTimeShort } from './format';

/** 后端未给出 intervalSeconds 时的兜底刷新间隔（秒） */
export const DEFAULT_REFRESH_INTERVAL_SECONDS = 30;
/** 允许的最小刷新间隔（秒）：防止后端配置异常导致请求风暴 */
export const MIN_REFRESH_INTERVAL_SECONDS = 5;
/** 允许的最大刷新间隔（秒）：防止配置异常导致长时间不刷新 */
export const MAX_REFRESH_INTERVAL_SECONDS = 1800;

function asText(value: unknown): string | null {
  return typeof value === 'string' && value.trim() !== '' ? value : null;
}

function asBool(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null;
}

function asNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim() !== '' && Number.isFinite(Number(value))) {
    return Number(value);
  }
  return null;
}

/** 刷新间隔（秒）归一化：非法值回落到默认 30 秒，并夹在 [5, 1800] 之间 */
export function clampIntervalSeconds(value: unknown): number {
  const n = asNumber(value);
  if (n === null || n <= 0) return DEFAULT_REFRESH_INTERVAL_SECONDS;
  return Math.min(MAX_REFRESH_INTERVAL_SECONDS, Math.max(MIN_REFRESH_INTERVAL_SECONDS, Math.round(n)));
}

/**
 * 原始响应 → 可安全消费的 MarketClock。
 * 完全无效（既无 phase 也无 tradeDate 也无 shouldPoll）时返回 null，
 * 调用方据此把自动刷新降级为关闭状态。
 */
export function normalizeMarketClock(raw: unknown): MarketClock | null {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return null;
  const source = raw as Record<string, unknown>;

  const phase = asText(source.phase);
  const tradeDate = asText(source.tradeDate);
  const shouldPoll = asBool(source.shouldPoll);
  if (phase === null && tradeDate === null && shouldPoll === null) return null;

  const phaseText = asText(source.phaseText) ?? (phase ? MARKET_PHASE_LABELS[phase as MarketPhase] ?? '' : '');

  return {
    now: asText(source.now) ?? '',
    tradeDate: tradeDate ?? '',
    isTradingDay: asBool(source.isTradingDay) ?? false,
    isOpen: asBool(source.isOpen) ?? false,
    phase: (phase ?? 'closed') as MarketPhase,
    phaseText,
    shouldPoll: shouldPoll ?? false,
    intervalSeconds: clampIntervalSeconds(source.intervalSeconds),
    nextOpenAt: asText(source.nextOpenAt),
    nextCloseAt: asText(source.nextCloseAt),
    timezone: asText(source.timezone) ?? '',
  };
}

export interface MarketClockView {
  phase: string | null;
  phaseText: string;
  isOpen: boolean;
  isTradingDay: boolean;
  tradeDate: string | null;
  nextOpenAt: string | null;
  nextCloseAt: string | null;
  shouldPoll: boolean;
  intervalSeconds: number;
  timezone: string | null;
}

export const EMPTY_CLOCK_VIEW: MarketClockView = {
  phase: null,
  phaseText: '',
  isOpen: false,
  isTradingDay: false,
  tradeDate: null,
  nextOpenAt: null,
  nextCloseAt: null,
  shouldPoll: false,
  intervalSeconds: DEFAULT_REFRESH_INTERVAL_SECONDS,
  timezone: null,
};

export function marketClockView(clock: MarketClock | null | undefined): MarketClockView {
  if (!clock) return EMPTY_CLOCK_VIEW;
  return {
    phase: clock.phase ?? null,
    phaseText: clock.phaseText?.trim() ? clock.phaseText : MARKET_PHASE_LABELS[clock.phase] ?? '',
    isOpen: clock.isOpen === true,
    isTradingDay: clock.isTradingDay === true,
    tradeDate: clock.tradeDate?.trim() ? clock.tradeDate : null,
    nextOpenAt: clock.nextOpenAt ?? null,
    nextCloseAt: clock.nextCloseAt ?? null,
    shouldPoll: clock.shouldPoll === true,
    intervalSeconds: clock.intervalSeconds,
    timezone: clock.timezone?.trim() ? clock.timezone : null,
  };
}

/** 开盘期间：倒计时文案，如「30s 后刷新」 */
export function pollingRefreshText(seconds: number): string {
  const s = asNumber(seconds);
  const value = s === null || s <= 0 ? DEFAULT_REFRESH_INTERVAL_SECONDS : Math.round(s);
  return `${value}s 后刷新`;
}

/** 非开盘时段：如「已收盘 · 下次开盘 02-14 09:30」；nextOpenAt 为空时只显示时段文案 */
export function offlineRefreshText(view: Pick<MarketClockView, 'phaseText' | 'nextOpenAt'>): string {
  const phase = view.phaseText?.trim() ? view.phaseText : '已收盘';
  if (!view.nextOpenAt) return phase;
  return `${phase} · 下次开盘 ${fmtDateTimeShort(view.nextOpenAt)}`;
}
