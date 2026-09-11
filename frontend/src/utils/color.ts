/**
 * 主题与涨跌配色（纯 TS 实现，无 JSX，避免循环依赖）。
 * - 主题：深色（默认）/ 浅色，通过 :root[data-theme] 切换，持久化到 localStorage。
 * - 涨跌配色：红涨绿跌（默认）/ 绿涨红跌，通过 :root[data-updown] 切换。
 * 图表统一使用 CSS 变量取色（style={{ stroke: 'var(--up)' }}），因此切换即时生效。
 */
import { useSyncExternalStore } from 'react';

export type ThemeMode = 'dark' | 'light';
export type UpDownMode = 'red-up' | 'green-up';

export const THEME_STORAGE_KEY = 'lupbs.theme';
export const UPDOWN_STORAGE_KEY = 'lupbs.updown';

function readStored<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    if (raw && (allowed as readonly string[]).includes(raw)) return raw as T;
  } catch {
    /* 隐私模式下忽略 */
  }
  return fallback;
}

interface Store<T extends string> {
  get: () => T;
  set: (value: T) => void;
  subscribe: (listener: () => void) => () => void;
}

function createDomStore<T extends string>(
  storageKey: string,
  attribute: string,
  allowed: readonly T[],
  fallback: T,
): Store<T> {
  let current: T = fallback;
  let initialized = false;
  const listeners = new Set<() => void>();

  const apply = (value: T) => {
    try {
      document.documentElement.setAttribute(attribute, value);
    } catch {
      /* ignore */
    }
  };

  const init = () => {
    if (initialized) return;
    initialized = true;
    const existing = document.documentElement.getAttribute(attribute);
    current =
      existing && (allowed as readonly string[]).includes(existing)
        ? (existing as T)
        : readStored(storageKey, allowed, fallback);
    apply(current);
  };

  return {
    get: () => {
      init();
      return current;
    },
    set: (value: T) => {
      init();
      if (current === value) return;
      current = value;
      apply(value);
      try {
        window.localStorage.setItem(storageKey, value);
      } catch {
        /* ignore */
      }
      listeners.forEach((l) => l());
    },
    subscribe: (listener: () => void) => {
      init();
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}

export const themeStore = createDomStore<ThemeMode>(
  THEME_STORAGE_KEY,
  'data-theme',
  ['dark', 'light'],
  'dark',
);

export const upDownStore = createDomStore<UpDownMode>(
  UPDOWN_STORAGE_KEY,
  'data-updown',
  ['red-up', 'green-up'],
  'red-up',
);

/** 读取当前主题（订阅式，切换后组件自动重渲染） */
export function useThemeMode(): [ThemeMode, (mode: ThemeMode) => void] {
  const mode = useSyncExternalStore(themeStore.subscribe, themeStore.get, () => 'dark' as ThemeMode);
  return [mode, themeStore.set];
}

/** 读取当前涨跌配色方案 */
export function useUpDownMode(): [UpDownMode, (mode: UpDownMode) => void] {
  const mode = useSyncExternalStore(upDownStore.subscribe, upDownStore.get, () => 'red-up' as UpDownMode);
  return [mode, upDownStore.set];
}

export function toggleTheme(): void {
  themeStore.set(themeStore.get() === 'dark' ? 'light' : 'dark');
}

/** CSS 变量名常量，图表与内联样式统一引用，避免散落魔法字符串 */
export const CSS_VARS = {
  up: 'var(--up)',
  down: 'var(--down)',
  flat: 'var(--flat)',
  brand: 'var(--brand)',
  teal: 'var(--teal)',
  gold: 'var(--gold)',
  danger: 'var(--danger)',
  violet: 'var(--violet)',
  text1: 'var(--text-1)',
  text2: 'var(--text-2)',
  text3: 'var(--text-3)',
  grid: 'var(--grid-line)',
  border: 'var(--border)',
  surface: 'var(--surface)',
  surfaceSunken: 'var(--surface-sunken)',
  areaTop: 'var(--chart-area-top)',
  areaBottom: 'var(--chart-area-bottom)',
} as const;

/** 评分 → 颜色（用于环形进度与进度条） */
export function scoreColorVar(score: number | null | undefined): string {
  if (score === null || score === undefined || !Number.isFinite(score)) return CSS_VARS.text3;
  if (score >= 85) return CSS_VARS.gold;
  if (score >= 75) return CSS_VARS.up;
  if (score >= 55) return CSS_VARS.brand;
  return CSS_VARS.text3;
}

/** 0~1 通过率 / 占比 → 颜色 */
export function rateColorVar(rate: number | null | undefined): string {
  if (rate === null || rate === undefined || !Number.isFinite(rate)) return CSS_VARS.text3;
  if (rate >= 0.6) return CSS_VARS.up;
  if (rate >= 0.35) return CSS_VARS.gold;
  return CSS_VARS.down;
}
