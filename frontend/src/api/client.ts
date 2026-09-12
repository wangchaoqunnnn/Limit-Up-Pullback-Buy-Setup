/**
 * 统一请求封装。
 * 约束：所有请求一律走相对路径 BASE = '/api/v1'，源码内不出现任何 host / IP / 绝对地址。
 * 开发态跨域由 vite.config.ts 的 server.proxy 处理。
 */
import type {
  BacktestData,
  BacktestQuery,
  Envelope,
  HealthData,
  MarketClock,
  MarketOverview,
  PoolCreateBody,
  PoolData,
  PoolItem,
  RuleSet,
  RuleSetPatch,
  RulesData,
  ScanTaskStart,
  ScanTaskState,
  SettingsData,
  SignalQuery,
  SignalsData,
  SourceStatus,
  StockDetailData,
  StockListData,
  StockQuery,
} from './types';

export const BASE = '/api/v1';

/**
 * 请求超时（毫秒），按接口「重量」分级。
 *
 * 为什么必须分级：全市场（约 5500 只）的首次取数需要数分钟 —— 本地实测
 * `/market/overview` 18～39 秒、`/backtest` 24 秒，服务器上更慢。
 * 早期所有接口统一 15 秒，导致「部署完成后整个页面全是加载失败」，
 * 而这其实只是首轮预热尚未完成，并非服务故障。
 */
export const REQUEST_TIMEOUT_MS = 15000;
/** 常规接口：命中缓存，正常在 1 秒内 */
export const TIMEOUT_LIGHT_MS = 15000;
/** 需读全市场日线的接口（行情列表 / 个股详情） */
export const TIMEOUT_MEDIUM_MS = 45000;
/** 重接口：全市场扫描、情绪总览、回测，首次可能数分钟 */
export const TIMEOUT_HEAVY_MS = 180000;

/** 业务/网络错误，message 为可直接展示的中文文案 */
export class ApiError extends Error {
  readonly status: number;
  readonly kind: 'business' | 'network' | 'timeout' | 'parse';

  constructor(message: string, status = 0, kind: ApiError['kind'] = 'business') {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.kind = kind;
  }
}

export function isApiError(err: unknown): err is ApiError {
  return err instanceof ApiError;
}

/** 把任意异常转成可展示的中文提示 */
export function toErrorMessage(err: unknown): string {
  if (isApiError(err)) return err.message;
  if (err instanceof Error) return err.message || '发生未知错误';
  return '发生未知错误';
}

function buildQuery(params?: object): string {
  if (!params) return '';
  const usp = new URLSearchParams();
  Object.entries(params).forEach(([key, raw]) => {
    if (raw === undefined || raw === null || raw === '') return;
    usp.append(key, String(raw));
  });
  const qs = usp.toString();
  return qs ? `?${qs}` : '';
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE';
  body?: unknown;
  /** 是否解析统一信封（/health 为 false） */
  envelope?: boolean;
  signal?: AbortSignal;
  timeoutMs?: number;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, envelope = true, signal, timeoutMs = REQUEST_TIMEOUT_MS } = options;

  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  const onExternalAbort = () => controller.abort();
  if (signal) {
    if (signal.aborted) controller.abort();
    else signal.addEventListener('abort', onExternalAbort, { once: true });
  }

  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      method,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
      credentials: 'same-origin',
    });
  } catch (err) {
    const aborted = controller.signal.aborted;
    const external = signal?.aborted === true;
    if (aborted && !external) {
      throw new ApiError(
        `请求超时（${Math.round(timeoutMs / 1000)} 秒）` +
          (timeoutMs >= TIMEOUT_HEAVY_MS
            ? '。全市场行情首次取数需要数分钟（服务器越慢越久），请稍后重试；若持续如此，请在服务器上运行 diagnose.sh 自检'
            : '，请检查后端服务后重试'),
        0,
        'timeout',
      );
    }
    if (external) throw new ApiError('请求已取消', 0, 'network');
    throw new ApiError('网络连接失败，无法访问后端服务，请确认服务已启动', 0, 'network');
  } finally {
    window.clearTimeout(timer);
    if (signal) signal.removeEventListener('abort', onExternalAbort);
  }

  const text = await res.text().catch(() => '');
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text) as unknown;
    } catch {
      if (!res.ok) {
        throw new ApiError(`服务端返回异常（HTTP ${res.status}）`, res.status, 'parse');
      }
      throw new ApiError('服务端返回内容无法解析（非 JSON 格式）', res.status, 'parse');
    }
  }

  if (!envelope) {
    if (!res.ok) throw new ApiError(`请求失败（HTTP ${res.status}）`, res.status, 'business');
    return payload as T;
  }

  const env = (payload ?? {}) as Partial<Envelope<T>>;
  const message =
    typeof env.message === 'string' && env.message.trim() ? env.message : null;

  if (!res.ok || env.ok === false) {
    const fallback =
      res.status === 404
        ? '未找到对应数据'
        : res.status === 409
          ? '该记录已存在'
          : res.status === 503
            ? '数据源不可用，请稍后重试'
            : res.status >= 500
              ? `服务端异常（HTTP ${res.status}）`
              : `请求失败（HTTP ${res.status}）`;
    throw new ApiError(message ?? fallback, res.status, 'business');
  }

  if (env.data === undefined || env.data === null) {
    throw new ApiError(message ?? '服务端未返回数据', res.status, 'business');
  }

  return env.data as T;
}

/* ============================ 通用导出 ============================ */

export function get<T>(
  path: string,
  params?: object,
  signal?: AbortSignal,
  timeoutMs?: number,
): Promise<T> {
  return request<T>(`${path}${buildQuery(params)}`, { method: 'GET', signal, timeoutMs });
}

export function post<T>(
  path: string,
  body?: unknown,
  signal?: AbortSignal,
  timeoutMs?: number,
): Promise<T> {
  return request<T>(path, { method: 'POST', body, signal, timeoutMs });
}

export function put<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  return request<T>(path, { method: 'PUT', body, signal });
}

export function del<T>(path: string, signal?: AbortSignal): Promise<T> {
  return request<T>(path, { method: 'DELETE', signal });
}

/* ============================ 业务接口 ============================ */

export const api = {
  health: (signal?: AbortSignal) => request<HealthData>('/health', { envelope: false, signal }),

  // 重接口：需要全市场日线，首次取数可能数分钟，因此给足超时。
  // 早期这里统一用 15 秒，导致刚部署完「整个页面全是加载失败」——
  // 那其实只是首轮预热没跑完，不是服务故障。
  marketOverview: (refresh = false, signal?: AbortSignal) =>
    get<MarketOverview>(
      '/market/overview',
      { refresh: refresh ? true : undefined },
      signal,
      TIMEOUT_HEAVY_MS,
    ),

  stocks: (query: StockQuery = {}, signal?: AbortSignal) =>
    get<StockListData>('/stocks', { ...query }, signal, TIMEOUT_MEDIUM_MS),

  stockDetail: (code: string, klineLimit = 180, signal?: AbortSignal) =>
    get<StockDetailData>(
      `/stocks/${encodeURIComponent(code)}`,
      { klineLimit },
      signal,
      TIMEOUT_MEDIUM_MS,
    ),

  signals: (query: SignalQuery = {}, signal?: AbortSignal) =>
    get<SignalsData>('/signals', { ...query }, signal, TIMEOUT_HEAVY_MS),

  signalDetail: (code: string, signal?: AbortSignal) =>
    get<StockDetailData>(`/signals/${encodeURIComponent(code)}`, {}, signal, TIMEOUT_MEDIUM_MS),

  // 启动扫描前需先取全市场股票列表，服务器上可能较慢
  scan: (body: { refresh?: boolean; limitUpType?: string }, signal?: AbortSignal) =>
    post<ScanTaskStart>('/scan', body, signal, TIMEOUT_HEAVY_MS),

  scanTask: (taskId: string, signal?: AbortSignal) =>
    get<ScanTaskState>(`/scan/${encodeURIComponent(taskId)}`, {}, signal, TIMEOUT_LIGHT_MS),

  backtest: (query: BacktestQuery = {}, signal?: AbortSignal) =>
    get<BacktestData>('/backtest', { ...query }, signal, TIMEOUT_HEAVY_MS),

  rules: (signal?: AbortSignal) => get<RulesData>('/rules', {}, signal),

  updateRules: (patch: RuleSetPatch, signal?: AbortSignal) => put<RuleSet>('/rules', patch, signal),

  pool: (signal?: AbortSignal) => get<PoolData>('/pool', {}, signal),

  addPool: (body: PoolCreateBody, signal?: AbortSignal) => post<PoolItem>('/pool', body, signal),

  removePool: (code: string, signal?: AbortSignal) =>
    del<{ code: string }>(`/pool/${encodeURIComponent(code)}`, signal),

  settings: (signal?: AbortSignal) => get<SettingsData>('/settings', {}, signal),

  /** 市场时钟：前端据此控制「开盘期间自动刷新」（shouldPoll / intervalSeconds） */
  marketClock: (signal?: AbortSignal) =>
    get<MarketClock>('/market/clock', {}, signal, TIMEOUT_LIGHT_MS),

  /** 数据源状态与健康度；probe=true 时现场探测各源（较慢） */
  sources: (probe = false, signal?: AbortSignal) =>
    get<SourceStatus>(
      '/sources',
      { probe: probe ? true : undefined },
      signal,
      probe ? TIMEOUT_HEAVY_MS : TIMEOUT_LIGHT_MS,
    ),
};
