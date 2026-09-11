/**
 * 全局自动刷新控制器（单例 + useSyncExternalStore）。
 *
 * 设计要点：
 * 1. 单例：整个应用只有一份市场时钟轮询与一份数据刷新定时器，页面只注册「刷新目标」，
 *    不会各起一个定时器；
 * 2. 时钟驱动：每 60 秒取一次 GET /market/clock，只有 clock.shouldPoll === true
 *    （集合竞价 / 上午 / 午间 / 下午）才按 clock.intervalSeconds 刷新当前页面数据；
 *    收盘 / 休市 / 盘前停止数据刷新，但时钟仍继续轮询以便开盘自动恢复；
 * 3. 页面隐藏（document.visibilitychange）时暂停数据刷新，重新可见时立即刷新一次并恢复；
 * 4. 单飞：同一时刻只允许一次在途刷新，重复触发复用同一个 Promise；
 * 5. 静默：刷新只调用各页面 useApi 的 refetchAsync（保留旧数据，不清空、不显示骨架屏）；
 * 6. 防御：clock 请求失败或字段缺失时，一律降级为「不自动刷新」，绝不空转请求。
 */
import { useEffect, useRef, useSyncExternalStore } from 'react';
import { api, toErrorMessage } from '../api/client';
import type { MarketClock, MarketPhase } from '../api/types';
import {
  DEFAULT_REFRESH_INTERVAL_SECONDS,
  clampIntervalSeconds,
  normalizeMarketClock,
} from '../utils/marketClock';

/** 市场时钟轮询间隔：定期校准交易时段（比数据刷新稀疏，避免无意义请求） */
export const CLOCK_POLL_INTERVAL_MS = 60000;
/** 手动刷新时「在途」状态的最短可见时长（毫秒），避免按钮状态一闪而过 */
const MIN_REFRESH_VISIBLE_MS = 450;

export interface AutoRefreshState {
  /** 原始市场时钟；接口未就绪时为 null */
  clock: MarketClock | null;
  /** 是否成功获取过市场时钟 */
  clockReady: boolean;
  /** 市场时钟请求失败原因（成功时为 null） */
  clockError: string | null;
  /** 是否应自动刷新（唯一由后端 clock.shouldPoll 决定） */
  shouldPoll: boolean;
  /** 自动刷新间隔（秒） */
  intervalSeconds: number;
  phase: MarketPhase | null;
  phaseText: string;
  isOpen: boolean;
  isTradingDay: boolean;
  tradeDate: string | null;
  nextOpenAt: string | null;
  nextCloseAt: string | null;
  /** 距下一次自动刷新的倒计时（秒）；不刷新时为 0 */
  secondsToNextRefresh: number;
  /** 最近一次刷新完成时间（毫秒时间戳） */
  lastUpdatedAt: number | null;
  /** 是否有一次刷新在途（手动按钮据此禁用） */
  refreshing: boolean;
  /** 是否因页面隐藏而暂停 */
  paused: boolean;
  /** 当前页面已注册的刷新目标数量 */
  targetCount: number;
}

const INITIAL_STATE: AutoRefreshState = {
  clock: null,
  clockReady: false,
  clockError: null,
  shouldPoll: false,
  intervalSeconds: DEFAULT_REFRESH_INTERVAL_SECONDS,
  phase: null,
  phaseText: '',
  isOpen: false,
  isTradingDay: false,
  tradeDate: null,
  nextOpenAt: null,
  nextCloseAt: null,
  secondsToNextRefresh: 0,
  lastUpdatedAt: null,
  refreshing: false,
  paused: false,
  targetCount: 0,
};

export type RefreshTarget = () => void | Promise<unknown>;

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

class AutoRefreshController {
  private state: AutoRefreshState = INITIAL_STATE;
  private readonly listeners = new Set<() => void>();
  private readonly targets = new Set<RefreshTarget>();

  private clockTimer: number | null = null;
  private refreshTimer: number | null = null;
  private refreshTimerMs = 0;
  private countdownTimer: number | null = null;

  /** 下一次自动刷新的时间戳（毫秒） */
  private deadline = 0;
  private inFlight: Promise<void> | null = null;
  private clockFetching = false;
  private started = false;
  private paused = false;
  private visibilityBound = false;

  /* --------------------------- 订阅与快照 --------------------------- */

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    this.start();
    return () => {
      this.listeners.delete(listener);
      this.stopIfIdle();
    };
  };

  getSnapshot = (): AutoRefreshState => this.state;

  /** 注册一个刷新目标（页面的 refetch），返回注销函数 */
  addTarget = (target: RefreshTarget): (() => void) => {
    this.targets.add(target);
    this.patch({ targetCount: this.targets.size });
    this.start();
    return () => {
      this.targets.delete(target);
      this.patch({ targetCount: this.targets.size });
      this.stopIfIdle();
    };
  };

  /* --------------------------- 生命周期 --------------------------- */

  private start(): void {
    if (this.started) return;
    this.started = true;
    this.syncPausedFromDom();
    this.bindVisibility();
    void this.pollClock();
    this.clockTimer = window.setInterval(() => {
      void this.pollClock();
    }, CLOCK_POLL_INTERVAL_MS);
    this.countdownTimer = window.setInterval(() => this.tickCountdown(), 1000);
    this.syncRefreshTimer();
  }

  /** 以 document.visibilityState 为准校准暂停态（首次进入 / 重新挂载时都可能落在后台标签页） */
  private syncPausedFromDom(): void {
    const hidden = typeof document !== 'undefined' && document.visibilityState === 'hidden';
    this.paused = hidden;
    this.patch(hidden ? { paused: true, secondsToNextRefresh: 0 } : { paused: false });
  }

  private stopIfIdle(): void {
    if (!this.started) return;
    if (this.listeners.size > 0 || this.targets.size > 0) return;
    this.started = false;
    if (this.clockTimer !== null) window.clearInterval(this.clockTimer);
    if (this.countdownTimer !== null) window.clearInterval(this.countdownTimer);
    this.clockTimer = null;
    this.countdownTimer = null;
    this.clearRefreshTimer();
    this.deadline = 0;
    if (this.state.secondsToNextRefresh !== 0) this.patch({ secondsToNextRefresh: 0 });
    this.unbindVisibility();
  }

  private bindVisibility(): void {
    if (this.visibilityBound) return;
    if (typeof document === 'undefined') return;
    this.visibilityBound = true;
    document.addEventListener('visibilitychange', this.onVisibilityChange);
  }

  private unbindVisibility(): void {
    if (!this.visibilityBound) return;
    if (typeof document === 'undefined') return;
    this.visibilityBound = false;
    document.removeEventListener('visibilitychange', this.onVisibilityChange);
  }

  /* --------------------------- 状态更新 --------------------------- */

  /** 仅在与旧值不同时才替换快照并通知订阅者，避免无意义重渲染 */
  private patch(partial: Partial<AutoRefreshState>): void {
    let changed = false;
    (Object.keys(partial) as Array<keyof AutoRefreshState>).forEach((key) => {
      if (this.state[key] !== partial[key]) changed = true;
    });
    if (!changed) return;
    this.state = { ...this.state, ...partial };
    this.listeners.forEach((listener) => listener());
  }

  /* --------------------------- 市场时钟 --------------------------- */

  private async pollClock(): Promise<void> {
    if (this.clockFetching) return;
    this.clockFetching = true;
    try {
      const raw = await api.marketClock();
      if (!this.started) return;
      const clock = normalizeMarketClock(raw);
      if (!clock) {
        this.applyClockFailure('市场时钟返回内容不完整，已暂停自动刷新');
        return;
      }

      const previousReady = this.state.clockReady;
      const previousShouldPoll = this.state.shouldPoll;
      const intervalChanged = this.state.intervalSeconds !== clock.intervalSeconds;

      this.patch({
        clock,
        clockReady: true,
        clockError: null,
        shouldPoll: clock.shouldPoll === true,
        intervalSeconds: clock.intervalSeconds,
        phase: clock.phase,
        phaseText: clock.phaseText,
        isOpen: clock.isOpen === true,
        isTradingDay: clock.isTradingDay === true,
        tradeDate: clock.tradeDate || null,
        nextOpenAt: clock.nextOpenAt,
        nextCloseAt: clock.nextCloseAt,
      });

      // 开盘瞬间（非首次加载）立即刷新一次，并重建定时器
      const justOpened = previousReady && !previousShouldPoll && clock.shouldPoll === true;
      this.syncRefreshTimer({ restart: intervalChanged || justOpened });
      if (justOpened) void this.refreshNow();
    } catch (err) {
      if (!this.started) return;
      this.applyClockFailure(toErrorMessage(err));
    } finally {
      this.clockFetching = false;
    }
  }

  /** 时钟不可用：降级为不自动刷新（保留上一次可展示的时段信息） */
  private applyClockFailure(message: string): void {
    this.patch({ clockError: message, shouldPoll: false });
    this.syncRefreshTimer();
  }

  /* --------------------------- 定时器 --------------------------- */

  private clearRefreshTimer(): void {
    if (this.refreshTimer !== null) {
      window.clearInterval(this.refreshTimer);
      this.refreshTimer = null;
    }
    this.refreshTimerMs = 0;
  }

  /**
   * 同步数据刷新定时器：
   * - 非开盘 / 页面隐藏 / 未启动 → 停止定时器；
   * - intervalSeconds 变化或强制重启 → 重建定时器（避免沿用旧间隔）。
   */
  private syncRefreshTimer(options: { restart?: boolean } = {}): void {
    const shouldRun = this.started && this.state.shouldPoll && !this.paused;
    const intervalMs = clampIntervalSeconds(this.state.intervalSeconds) * 1000;

    if (!shouldRun) {
      this.clearRefreshTimer();
      this.deadline = 0;
      if (this.state.secondsToNextRefresh !== 0) this.patch({ secondsToNextRefresh: 0 });
      return;
    }

    const alive = this.refreshTimer !== null;
    if (alive && this.refreshTimerMs === intervalMs && options.restart !== true) return;

    this.clearRefreshTimer();
    this.refreshTimerMs = intervalMs;
    this.deadline = Date.now() + intervalMs;
    this.refreshTimer = window.setInterval(() => {
      if (this.paused) return;
      void this.refreshNow();
    }, intervalMs);
    this.tickCountdown();
  }

  private tickCountdown(): void {
    const next =
      this.deadline > 0 && !this.paused
        ? Math.max(0, Math.ceil((this.deadline - Date.now()) / 1000))
        : 0;
    if (next !== this.state.secondsToNextRefresh) this.patch({ secondsToNextRefresh: next });
  }

  /* --------------------------- 页面可见性 --------------------------- */

  private onVisibilityChange = (): void => {
    if (typeof document === 'undefined') return;
    const hidden = document.visibilityState === 'hidden';

    if (hidden) {
      this.paused = true;
      this.deadline = 0;
      this.patch({ paused: true, secondsToNextRefresh: 0 });
      this.syncRefreshTimer();
      return;
    }

    this.paused = false;
    this.patch({ paused: false });
    // 恢复可见：先校准时钟，再立即刷新一次并重建定时器
    void this.pollClock();
    this.syncRefreshTimer({ restart: true });
    if (this.state.shouldPoll) void this.refreshNow();
  };

  /* --------------------------- 刷新 --------------------------- */

  /**
   * 立即刷新当前页面数据（单飞）。
   * 在途时重复调用返回同一个 Promise，不会造成请求风暴。
   */
  refreshNow = (): Promise<void> => {
    if (this.inFlight) return this.inFlight;

    const targets = Array.from(this.targets);
    const run = async (): Promise<void> => {
      this.patch({ refreshing: true });
      const startedAt = Date.now();
      try {
        if (targets.length > 0) {
          await Promise.allSettled(targets.map((target) => Promise.resolve().then(() => target())));
        } else {
          // 当前页面未注册刷新目标时，至少校准一次市场时钟
          await this.pollClock();
        }
        const elapsed = Date.now() - startedAt;
        if (elapsed < MIN_REFRESH_VISIBLE_MS) await delay(MIN_REFRESH_VISIBLE_MS - elapsed);
      } finally {
        this.patch({ refreshing: false, lastUpdatedAt: Date.now() });
      }
    };

    const settled = run().then(
      () => undefined,
      () => undefined,
    );
    this.inFlight = settled;
    void settled.then(() => {
      if (this.inFlight === settled) this.inFlight = null;
    });
    return settled;
  };
}

const controller = new AutoRefreshController();

/* ============================ React 接口 ============================ */

export interface AutoRefreshResult extends AutoRefreshState {
  /** 立即刷新（手动「立即刷新」按钮）；在途期间重复点击会被合并 */
  refreshNow: () => Promise<void>;
}

/** 订阅全局自动刷新状态（顶栏 / 设置页使用） */
export function useAutoRefresh(): AutoRefreshResult {
  const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot);
  return { ...state, refreshNow: controller.refreshNow };
}

/**
 * 把页面的 refetch 注册为自动刷新目标。
 * 目标在组件卸载时自动注销；不订阅状态，因此不会引起额外的每秒重渲染。
 *
 * @example
 * useAutoRefreshTarget(overview.refetchAsync, top.refetchAsync);
 */
export function useAutoRefreshTarget(...targets: Array<RefreshTarget | null | undefined>): void {
  const targetsRef = useRef(targets);
  useEffect(() => {
    targetsRef.current = targets;
  });

  useEffect(() => {
    const runner: RefreshTarget = () => {
      const current = targetsRef.current.filter((target): target is RefreshTarget => typeof target === 'function');
      if (current.length === 0) return undefined;
      return Promise.allSettled(current.map((target) => Promise.resolve().then(() => target())));
    };
    return controller.addTarget(runner);
  }, []);
}

/** 供非 React 场景/调试使用 */
export const autoRefreshController = controller;

/** 供 Settings 使用：把倒计时/是否暂停渲染成一行说明 */
export function autoRefreshHint(state: Pick<AutoRefreshState, 'shouldPoll' | 'paused' | 'targetCount'>): string {
  if (state.paused) return '页面处于后台，自动刷新已暂停，回到前台后立即刷新一次';
  if (!state.shouldPoll) return '非交易时段，自动刷新已停止（时钟仍在轮询，开盘后自动恢复）';
  if (state.targetCount === 0) return '开盘期间自动刷新已开启，但当前页面未注册可刷新的数据';
  return '开盘期间按后端下发的间隔自动刷新当前页面数据';
}
