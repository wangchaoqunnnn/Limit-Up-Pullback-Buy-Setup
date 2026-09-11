/**
 * 全市场扫描任务：POST /scan 触发 + GET /scan/{taskId} 轮询进度。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { api, toErrorMessage } from '../api/client';
import type { ScanTaskState } from '../api/types';
import { SCAN_POLL_INTERVAL_MS } from '../utils/constants';

export interface StartScanOptions {
  refresh?: boolean;
  limitUpType?: string;
}

export interface UseScanTaskResult {
  task: ScanTaskState | null;
  starting: boolean;
  error: string | null;
  running: boolean;
  progressPct: number;
  start: (options?: StartScanOptions) => Promise<void>;
  reset: () => void;
}

export function useScanTask(onFinished?: (task: ScanTaskState) => void): UseScanTaskResult {
  const [task, setTask] = useState<ScanTaskState | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const onFinishedRef = useRef(onFinished);
  onFinishedRef.current = onFinished;

  const start = useCallback(async (options: StartScanOptions = {}) => {
    setStarting(true);
    setError(null);
    try {
      const started = await api.scan({
        refresh: options.refresh ?? true,
        limitUpType: options.limitUpType ?? 'QUALITY',
      });
      setTask({
        taskId: started.taskId,
        status: started.status,
        progress: started.progress ?? 0,
        total: started.total ?? 0,
        startedAt: null,
        finishedAt: null,
        matched: null,
        message: started.message ?? null,
      });
    } catch (err) {
      setError(toErrorMessage(err));
    } finally {
      setStarting(false);
    }
  }, []);

  const reset = useCallback(() => {
    setTask(null);
    setError(null);
  }, []);

  const taskId = task?.taskId ?? null;
  const active = task?.status === 'pending' || task?.status === 'running';

  useEffect(() => {
    if (!taskId || !active) return;
    let cancelled = false;
    let timer: number | undefined;

    const poll = async () => {
      try {
        const next = await api.scanTask(taskId);
        if (cancelled) return;
        setTask(next);
        if (next.status === 'finished' || next.status === 'failed') {
          if (timer !== undefined) window.clearInterval(timer);
          if (next.status === 'finished') onFinishedRef.current?.(next);
        }
      } catch (err) {
        if (cancelled) return;
        setError(toErrorMessage(err));
        if (timer !== undefined) window.clearInterval(timer);
      }
    };

    void poll();
    timer = window.setInterval(() => {
      void poll();
    }, SCAN_POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearInterval(timer);
    };
  }, [taskId, active]);

  const total = task?.total ?? 0;
  const progress = task?.progress ?? 0;
  const progressPct = total > 0 ? Math.min(100, Math.round((progress / total) * 100)) : task?.status === 'finished' ? 100 : 0;

  return {
    task,
    starting,
    error,
    running: starting || active,
    progressPct,
    start,
    reset,
  };
}
