/**
 * 通用请求 Hook：统一管理 loading / error / data / refetch / 轮询，并做竞态与卸载保护。
 *
 * 刷新语义：refetch / refetchAsync 与轮询都是**静默刷新**——保留旧数据不清空，
 * 仅在首次加载（尚无任何数据）时置 loading，用于骨架屏；已有数据时只置 refreshing，
 * 因此全局自动刷新不会造成骨架屏闪烁。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import type { RefObject } from 'react';
import { toErrorMessage } from '../api/client';

export interface UseApiOptions {
  /** 是否立即请求，默认 true */
  enabled?: boolean;
  /** 轮询间隔（毫秒），0 表示不轮询 */
  pollMs?: number;
}

export interface UseApiResult<T> {
  data: T | null;
  error: string | null;
  /** 首次加载（无数据）时为 true，用于骨架屏 */
  loading: boolean;
  /** 已有数据的后台刷新 */
  refreshing: boolean;
  /** 数据更新时间戳（毫秒） */
  updatedAt: number | null;
  refetch: () => void;
  /** 与 refetch 等价，但返回 Promise：在本次请求结束后 resolve（供全局自动刷新等待在途请求） */
  refetchAsync: () => Promise<void>;
  setData: (updater: T | ((prev: T | null) => T | null)) => void;
}

interface ApiState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  refreshing: boolean;
  updatedAt: number | null;
}

export function useApi<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  deps: unknown[] = [],
  options: UseApiOptions = {},
): UseApiResult<T> {
  const { enabled = true, pollMs = 0 } = options;

  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const hasDataRef = useRef(false);
  const [tick, setTick] = useState(0);
  const [state, setState] = useState<ApiState<T>>({
    data: null,
    error: null,
    loading: enabled,
    refreshing: false,
    updatedAt: null,
  });

  /** 当前 enabled（供 refetchAsync 判断是否会真正发起请求） */
  const enabledRef = useRef(enabled);
  enabledRef.current = enabled;

  /**
   * refetchAsync 的等待者：一次 refetch 恰好触发一次 effect 重跑，
   * 请求结束（成功或失败）后统一 resolve，避免请求风暴与悬挂 Promise。
   */
  const pendingRef = useRef<Array<() => void>>([]);
  const flushPending = useCallback(() => {
    const pending = pendingRef.current;
    if (pending.length === 0) return;
    pendingRef.current = [];
    pending.forEach((resolve) => resolve());
  }, []);

  // 卸载时兜底 resolve，避免调用方永久等待
  useEffect(() => () => flushPending(), [flushPending]);

  useEffect(() => {
    if (!enabled) {
      setState((s) => (s.loading ? { ...s, loading: false } : s));
      flushPending();
      return;
    }

    let cancelled = false;
    let controller: AbortController | null = null;
    let timer: number | undefined;

    const run = async (first: boolean) => {
      controller?.abort();
      controller = new AbortController();
      const signal = controller.signal;

      if (first && !hasDataRef.current) {
        setState((s) => ({ ...s, loading: true, error: null }));
      } else {
        setState((s) => ({ ...s, refreshing: true, error: null }));
      }

      try {
        const data = await fetcherRef.current(signal);
        if (cancelled) return;
        hasDataRef.current = true;
        setState({ data, error: null, loading: false, refreshing: false, updatedAt: Date.now() });
      } catch (err) {
        if (cancelled) return;
        setState((s) => ({
          data: s.data,
          error: toErrorMessage(err),
          loading: false,
          refreshing: false,
          updatedAt: s.updatedAt,
        }));
      } finally {
        if (!cancelled) flushPending();
      }
    };

    void run(true);

    if (pollMs > 0) {
      timer = window.setInterval(() => {
        void run(false);
      }, pollMs);
    }

    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearInterval(timer);
      controller?.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, pollMs, tick, flushPending, ...deps]);

  const refetchAsync = useCallback((): Promise<void> => {
    if (!enabledRef.current) return Promise.resolve();
    return new Promise<void>((resolve) => {
      pendingRef.current.push(resolve);
      setTick((t) => t + 1);
    });
  }, []);

  const refetch = useCallback(() => {
    void refetchAsync();
  }, [refetchAsync]);

  const setData = useCallback((updater: T | ((prev: T | null) => T | null)) => {
    setState((s) => ({
      ...s,
      data: typeof updater === 'function' ? (updater as (prev: T | null) => T | null)(s.data) : updater,
    }));
  }, []);

  return { ...state, refetch, refetchAsync, setData };
}

export interface UseMutationResult<TArgs extends unknown[], TResult> {
  run: (...args: TArgs) => Promise<TResult>;
  loading: boolean;
  error: string | null;
  reset: () => void;
}

/** 写操作（POST / PUT / DELETE）通用 Hook */
export function useMutation<TArgs extends unknown[], TResult>(
  fn: (...args: TArgs) => Promise<TResult>,
): UseMutationResult<TArgs, TResult> {
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(async (...args: TArgs) => {
    setLoading(true);
    setError(null);
    try {
      return await fnRef.current(...args);
    } catch (err) {
      setError(toErrorMessage(err));
      throw err;
    } finally {
      setLoading(false);
    }
  }, []);

  const reset = useCallback(() => setError(null), []);

  return { run, loading, error, reset };
}

/** 防抖值 */
export function useDebounced<T>(value: T, delay = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

/** 当前时间（用于顶栏时钟），每秒更新 */
export function useClock(intervalMs = 1000): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs]);
  return now;
}

/** 元素尺寸观察（手写 SVG 图表按容器宽度自适应） */
export function useElementWidth<T extends HTMLElement>(): [RefObject<T>, number] {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const update = () => setWidth(el.clientWidth);
    update();
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', update);
      return () => window.removeEventListener('resize', update);
    }
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  return [ref, width];
}
