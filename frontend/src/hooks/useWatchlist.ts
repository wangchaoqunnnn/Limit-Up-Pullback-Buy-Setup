/**
 * 本地关注列表（localStorage 持久化）。
 * 与服务端低吸池（/pool）互为补充：本地关注用于快速标记，不依赖后端可用性。
 */
import { useSyncExternalStore } from 'react';

export const WATCHLIST_STORAGE_KEY = 'lupbs.watchlist';

type Listener = () => void;

const listeners = new Set<Listener>();
let cache: string[] = [];
let loaded = false;

function normalize(input: unknown): string[] {
  if (!Array.isArray(input)) return [];
  const set = new Set<string>();
  input.forEach((item) => {
    if (typeof item === 'string' && /^\d{6}$/.test(item)) set.add(item);
  });
  return Array.from(set);
}

function load(): string[] {
  if (loaded) return cache;
  loaded = true;
  try {
    const raw = window.localStorage.getItem(WATCHLIST_STORAGE_KEY);
    cache = raw ? normalize(JSON.parse(raw) as unknown) : [];
  } catch {
    cache = [];
  }
  return cache;
}

function persist(next: string[]) {
  cache = next;
  try {
    window.localStorage.setItem(WATCHLIST_STORAGE_KEY, JSON.stringify(next));
  } catch {
    /* 隐私模式忽略 */
  }
  listeners.forEach((l) => l());
}

function subscribe(listener: Listener): () => void {
  load();
  listeners.add(listener);
  const onStorage = (event: StorageEvent) => {
    if (event.key !== WATCHLIST_STORAGE_KEY) return;
    loaded = false;
    load();
    listeners.forEach((l) => l());
  };
  window.addEventListener('storage', onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener('storage', onStorage);
  };
}

export interface WatchlistApi {
  codes: string[];
  count: number;
  has: (code: string) => boolean;
  add: (code: string) => void;
  remove: (code: string) => void;
  toggle: (code: string) => boolean;
  clear: () => void;
}

export function useWatchlist(): WatchlistApi {
  const codes = useSyncExternalStore(subscribe, load, () => cache);

  return {
    codes,
    count: codes.length,
    has: (code: string) => codes.includes(code),
    add: (code: string) => {
      if (!/^\d{6}$/.test(code)) return;
      const current = load();
      if (current.includes(code)) return;
      persist([code, ...current]);
    },
    remove: (code: string) => {
      persist(load().filter((c) => c !== code));
    },
    toggle: (code: string) => {
      const current = load();
      const exists = current.includes(code);
      persist(exists ? current.filter((c) => c !== code) : [code, ...current]);
      return !exists;
    },
    clear: () => persist([]),
  };
}
