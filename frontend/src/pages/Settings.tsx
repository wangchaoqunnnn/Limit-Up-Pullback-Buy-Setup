import { useMemo, useState } from 'react';
import clsx from 'clsx';
import { api, BASE, toErrorMessage } from '../api/client';
import type { SourceStatus } from '../api/types';
import { useApi } from '../hooks/useApi';
import { autoRefreshHint, useAutoRefresh, useAutoRefreshTarget } from '../hooks/useAutoRefresh';
import { useWatchlist } from '../hooks/useWatchlist';
import { PageHeader } from '../components/layout/PageHeader';
import { Card } from '../components/ui/Card';
import { Badge } from '../components/ui/Badge';
import { Button } from '../components/ui/Button';
import { ErrorState, InlineAlert } from '../components/ui/ErrorState';
import { Segmented } from '../components/ui/Segmented';
import { SkeletonRows } from '../components/ui/Skeleton';
import { Table } from '../components/ui/Table';
import type { Column } from '../components/ui/Table';
import { useToast } from '../components/ui/Toast';
import { useThemeMode, useUpDownMode } from '../utils/color';
import {
  APP_TITLE,
  DATA_SOURCE_LABELS,
  DISCLAIMER_TEXT,
  DISCLAIMER_TITLE,
  HEALTH_POLL_INTERVAL_MS,
  dataSourceModeLabel,
  dataSourceShortLabel,
} from '../utils/constants';
import { fmtBytes, fmtClockMs, fmtDateTime, fmtInt, fmtUptime, isNum } from '../utils/format';
import { marketClockView } from '../utils/marketClock';
import {
  fmtSuccessRate,
  normalizeSourceStatus,
  sourceHealthText,
  sourceHealthTone,
} from '../utils/sourceHealth';
import type { SourceHealthView } from '../utils/sourceHealth';

const DASH = '—';

function secondsText(v: unknown): string {
  return isNum(v) ? `${fmtInt(v)} 秒` : DASH;
}

/** 系统设置：应用与数据源信息 + 源健康度 + 刷新策略 + 市场时钟 + 界面偏好 + 免责声明 */
export default function Settings() {
  const toast = useToast();
  const [theme, setTheme] = useThemeMode();
  const [updown, setUpDown] = useUpDownMode();
  const watchlist = useWatchlist();

  const settings = useApi((signal) => api.settings(signal), [], { pollMs: 30000 });
  const health = useApi((signal) => api.health(signal), [], { pollMs: HEALTH_POLL_INTERVAL_MS });
  const sources = useApi((signal) => api.sources(false, signal), [], { pollMs: 60000 });
  const refresh = useAutoRefresh();

  // 本页数据也纳入全局自动刷新（静默刷新，保留旧数据）
  useAutoRefreshTarget(settings.refetchAsync, sources.refetchAsync);

  /* 「重新探测」为一次性现场探测（较慢），结果覆盖常规轮询结果 */
  const [probe, setProbe] = useState<{ data: SourceStatus; at: number } | null>(null);
  const [probing, setProbing] = useState(false);
  const probed = probe?.data ?? null;
  const probedAt = probe?.at ?? null;

  const runProbe = async () => {
    setProbing(true);
    try {
      const data = await api.sources(true);
      setProbe({ data, at: Date.now() });
      toast.success('已现场探测各数据源可用性');
    } catch (err) {
      toast.error(`数据源探测失败：${toErrorMessage(err)}`);
    } finally {
      setProbing(false);
    }
  };

  const data = settings.data;

  /* /sources 不可用时回落到 /settings 的同名字段，保证面板始终有内容 */
  const status = useMemo(() => {
    const fromSources = normalizeSourceStatus(probed ?? sources.data);
    if (fromSources.ok) return fromSources;
    return normalizeSourceStatus(data ?? null);
  }, [probed, sources.data, data]);

  const isSynthetic = data?.dataSourceActive === 'synthetic' || status.active === 'synthetic';
  const usingFallback = data?.usingFallback === true || status.usingFallback || isSynthetic;
  const showFallbackBanner = usingFallback;

  const healthOk = health.data?.status === 'ok';
  const healthTone = health.error ? 'bad' : healthOk ? '' : 'warn';

  const clock = marketClockView(refresh.clock ?? data?.clock ?? null);
  const kline = status.cache;
  const klineFromSettings = data?.klineCache;

  const sourceColumns: Column<SourceHealthView>[] = [
    {
      key: 'name',
      title: '数据源',
      width: 168,
      render: (row) => (
        <span className="stock-cell">
          <span className="stock-cell-name">
            {row.label}
            {row.name === status.active && <Badge tone="brand">当前生效</Badge>}
          </span>
          <span className="stock-cell-meta mono">{row.name}</span>
        </span>
      ),
    },
    {
      key: 'available',
      title: '状态',
      width: 96,
      render: (row) => (
        <Badge tone={sourceHealthTone(row)} dot>
          {sourceHealthText(row)}
        </Badge>
      ),
    },
    {
      key: 'consecutiveFailures',
      title: '连续失败',
      width: 92,
      align: 'right',
      render: (row) => (
        <span className={clsx('mono', row.consecutiveFailures > 0 && 'gold-text')}>
          {fmtInt(row.consecutiveFailures)}
        </span>
      ),
    },
    {
      key: 'successRate',
      title: '成功率',
      width: 84,
      align: 'right',
      render: (row) => (
        <span className="mono" title={`成功 ${row.totalSuccess} 次 / 失败 ${row.totalFailure} 次`}>
          {fmtSuccessRate(row.successRate)}
        </span>
      ),
    },
    {
      key: 'cooldownSeconds',
      title: '冷却剩余',
      width: 110,
      align: 'right',
      render: (row) =>
        row.cooldownSeconds > 0 ? (
          <span className="mono gold-text">冷却中 {Math.ceil(row.cooldownSeconds)}s</span>
        ) : (
          <span className="text-3">{DASH}</span>
        ),
    },
    {
      key: 'lastError',
      title: '最近错误',
      render: (row) =>
        row.lastError ? (
          <span className="fs-12 danger-text" title={row.lastError}>
            {row.lastError}
          </span>
        ) : (
          <span className="text-3">{DASH}</span>
        ),
    },
  ];

  return (
    <>
      <PageHeader
        title="系统设置"
        sub="数据源、刷新策略、市场时钟与界面偏好"
        actions={
          <>
            <Button
              loading={refresh.refreshing}
              onClick={() => {
                void refresh.refreshNow();
              }}
              title="立即刷新本页配置与数据源状态"
            >
              立即刷新
            </Button>
            <Button loading={settings.refreshing} onClick={settings.refetch}>
              刷新配置
            </Button>
            <Button loading={health.refreshing} onClick={health.refetch}>
              刷新健康状态
            </Button>
          </>
        }
      />

      {showFallbackBanner && (
        <div className="banner mt-4" role="status">
          <span className="banner-icon" aria-hidden="true">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M12 8v5M12 16.5h.01" strokeLinecap="round" />
              <path d="M12 3.6 2.9 19.4h18.2L12 3.6Z" strokeLinejoin="round" />
            </svg>
          </span>
          <div>
            <div className="strong">全部真实数据源当前不可用，已降级为内置演示数据</div>
            <div className="fs-12 text-2" style={{ marginTop: 2, lineHeight: 1.7 }}>
              东方财富 / 腾讯财经 / 新浪财经三个真实行情源均取数失败，后端已降级为内置合成演示数据
              （<span className="mono">synthetic</span>）。所有信号、评分与买卖点均基于模拟数据生成，
              <strong className="gold-text">不可用于任何实盘判断</strong>。
              各源的可用性、连续失败次数、冷却剩余与最近错误可在下方<strong>「数据源健康度」</strong>卡片查看，
              也可点击该卡片的「重新探测」立即重试真实源。
            </div>
          </div>
        </div>
      )}

      {settings.error && data && (
        <div className="mt-4">
          <InlineAlert message={settings.error} onRetry={settings.refetch} hint="以下为上一次成功获取的配置。" />
        </div>
      )}

      {settings.error && !data && (
        <Card className="mt-4">
          <ErrorState message={settings.error} onRetry={settings.refetch} />
        </Card>
      )}

      <div className="settings-grid mt-4">
        <Card title="应用信息" sub="来自 GET /settings">
          {settings.loading && !data ? (
            <SkeletonRows rows={8} cols={2} />
          ) : data ? (
            <div className="col" style={{ gap: 0 }}>
              <div className="kv-row">
                <span className="kv-k">应用名称</span>
                <span className="kv-v">{data.appName || APP_TITLE}</span>
              </div>
              <div className="kv-row">
                <span className="kv-k">版本号</span>
                <span className="kv-v">{data.version}</span>
              </div>
              <div className="kv-row">
                <span className="kv-k">数据源模式</span>
                <span className="kv-v">{dataSourceModeLabel(data.dataSourceMode)}</span>
              </div>
              <div className="kv-row">
                <span className="kv-k">当前生效数据源</span>
                <span className="kv-v">
                  <Badge tone={isSynthetic ? 'violet' : usingFallback ? 'gold' : 'teal'} dot>
                    {DATA_SOURCE_LABELS[data.dataSourceActive] ?? data.dataSourceActive ?? DASH}
                  </Badge>
                </span>
              </div>
              <div className="kv-row">
                <span className="kv-k">股票池规模</span>
                <span className="kv-v">{fmtInt(data.universeSize)} 只</span>
              </div>
              <div className="kv-row">
                <span className="kv-k">行情缓存有效期</span>
                <span className="kv-v">{secondsText(data.cacheTtlSeconds)}</span>
              </div>
              <div className="kv-row">
                <span className="kv-k">合成数据开关</span>
                <span className="kv-v">{data.syntheticEnabled ? '已启用（可降级）' : '已关闭'}</span>
              </div>
              <div className="kv-row">
                <span className="kv-k">时区</span>
                <span className="kv-v">{data.timezone || DASH}</span>
              </div>
              <div className="kv-row">
                <span className="kv-k">服务器时间</span>
                <span className="kv-v">{fmtDateTime(data.serverTime)}</span>
              </div>
              <div className="kv-row">
                <span className="kv-k">前端接口前缀</span>
                <span className="kv-v">{BASE}（相对路径）</span>
              </div>
            </div>
          ) : (
            <span className="fs-12 text-3">暂无配置数据</span>
          )}
        </Card>

        <Card
          title="服务健康状态"
          sub={`每 ${HEALTH_POLL_INTERVAL_MS / 1000} 秒自动轮询 GET /health`}
          extra={
            <span className="row" style={{ gap: 6 }}>
              <i className={`health-dot ${healthTone}`} aria-hidden="true" />
              <span className="fs-12">
                {health.error ? '不可用' : healthOk ? '运行正常' : '状态未知'}
              </span>
            </span>
          }
        >
          {health.loading && !health.data ? (
            <SkeletonRows rows={5} cols={2} />
          ) : health.error && health.data ? (
            <div className="col" style={{ gap: 12 }}>
              <InlineAlert message={health.error} onRetry={health.refetch} hint="健康检查每 10 秒轮询一次。" />
              <div className="kv-row">
                <span className="kv-k">最近一次成功状态</span>
                <span className="kv-v">{health.data.status}</span>
              </div>
              <div className="kv-row">
                <span className="kv-k">最近一次运行时长</span>
                <span className="kv-v">{fmtUptime(health.data.uptimeSeconds)}</span>
              </div>
            </div>
          ) : health.error && !health.data ? (
            <ErrorState
              message={`${health.error}（健康检查未走统一信封，若后端未就绪会持续失败）`}
              onRetry={health.refetch}
            />
          ) : health.data ? (
            <div className="col" style={{ gap: 0 }}>
              <div className="kv-row">
                <span className="kv-k">服务状态</span>
                <span className="kv-v">
                  <Badge tone={healthOk ? 'teal' : 'gold'} dot>
                    {health.data.status}
                  </Badge>
                </span>
              </div>
              <div className="kv-row">
                <span className="kv-k">服务版本</span>
                <span className="kv-v">{health.data.version}</span>
              </div>
              <div className="kv-row">
                <span className="kv-k">运行时长</span>
                <span className="kv-v">{fmtUptime(health.data.uptimeSeconds)}</span>
              </div>
              <div className="kv-row">
                <span className="kv-k">数据源</span>
                <span className="kv-v">{DATA_SOURCE_LABELS[health.data.dataSource] ?? health.data.dataSource ?? DASH}</span>
              </div>
              <div className="kv-row">
                <span className="kv-k">最近同步时间</span>
                <span className="kv-v">{fmtDateTime(health.data.lastSyncAt)}</span>
              </div>
              <div className="kv-row">
                <span className="kv-k">股票池规模</span>
                <span className="kv-v">{fmtInt(health.data.universeSize)} 只</span>
              </div>
            </div>
          ) : (
            <span className="fs-12 text-3">暂无健康检查数据</span>
          )}
        </Card>

        {/* ---------------- 数据源健康度 ---------------- */}
        <Card
          className="span-all"
          title="数据源健康度"
          sub={
            probed
              ? `已现场探测各源可用性（${probedAt ? fmtClockMs(probedAt) : DASH}）· GET /sources?probe=true`
              : sources.error && !probed
                ? 'GET /sources 暂时不可用，以下回落到 GET /settings 的同名字段'
                : '来自 GET /sources（每 60 秒自动刷新，可手动重新探测）'
          }
          extra={
            <Button
              loading={probing}
              disabled={probing}
              onClick={() => {
                void runProbe();
              }}
              title="现场探测东方财富 / 腾讯财经 / 新浪财经的可用性（较慢，请耐心等待）"
            >
              重新探测
            </Button>
          }
          flush
        >
          <div className="col" style={{ gap: 0, padding: '0 var(--sp-4)' }}>
            <div className="kv-row">
              <span className="kv-k">数据源模式</span>
              <span className="kv-v">{dataSourceModeLabel(status.mode)}</span>
            </div>
            <div className="kv-row">
              <span className="kv-k">优先级顺序（故障转移）</span>
              <span className="kv-v row wrap" style={{ gap: 6 }}>
                {status.order.length > 0 ? (
                  status.order.map((name, index) => (
                    <span key={name} className="row" style={{ gap: 6 }}>
                      {index > 0 && (
                        <span className="text-3" aria-hidden="true">
                          →
                        </span>
                      )}
                      <Badge tone={name === status.active ? 'teal' : 'neutral'}>{dataSourceShortLabel(name)}</Badge>
                    </span>
                  ))
                ) : (
                  <span className="text-3">{DASH}</span>
                )}
              </span>
            </div>
            <div className="kv-row">
              <span className="kv-k">当前生效数据源</span>
              <span className="kv-v">
                <Badge tone={usingFallback ? 'violet' : 'teal'} dot>
                  {status.activeLabel}
                </Badge>
              </span>
            </div>
            <div className="kv-row">
              <span className="kv-k">股票池来源 / 规模</span>
              <span className="kv-v">
                {status.universeSource ? dataSourceShortLabel(status.universeSource) : DASH} ·{' '}
                {isNum(status.universeSize) ? `${fmtInt(status.universeSize)} 只` : DASH}
              </span>
            </div>
          </div>

          <Table<SourceHealthView>
            columns={sourceColumns}
            rows={status.sources}
            rowKey={(row) => row.name}
            minWidth={700}
            ariaLabel="数据源健康度"
            loading={sources.loading && status.sources.length === 0}
            error={sources.error && status.sources.length === 0 ? sources.error : null}
            onRetry={sources.refetch}
            emptyTitle="暂无数据源健康信息"
            emptyDesc="后端未返回 sources 列表（或 /sources 与 /settings 均不可用），可点击「重新探测」重试。"
            rowClassName={(row) => (row.name === status.active ? 'row-buy' : undefined)}
            skeletonRows={3}
          />

          <div className="pick-panel">
            <div className="fs-12 text-2" style={{ marginBottom: 6 }}>
              各操作最近一次实际成功使用的数据源（lastPickByOperation）
            </div>
            {status.lastPickByOperation.length > 0 ? (
              <div className="pick-list">
                {status.lastPickByOperation.map((pick) => (
                  <span className="pick-item" key={pick.operation}>
                    <span className="pick-op">{pick.operation}</span>
                    <span className="text-3" aria-hidden="true">
                      →
                    </span>
                    <Badge tone="teal">{pick.label}</Badge>
                  </span>
                ))}
              </div>
            ) : (
              <span className="fs-12 text-3">暂无记录：后端尚未成功从任一时段的数据源取数。</span>
            )}
          </div>
        </Card>

        {/* ---------------- 刷新策略 ---------------- */}
        <Card
          title="刷新策略"
          sub="开盘期间自动刷新与缓存有效期（来自 GET /settings）"
          extra={
            <span className="row" style={{ gap: 6 }}>
              <i className={clsx('health-dot', refresh.shouldPoll && !refresh.paused ? '' : 'warn')} aria-hidden="true" />
              <span className="fs-12">
                {refresh.shouldPoll ? `开盘 · 每 ${refresh.intervalSeconds} 秒` : '已暂停'}
              </span>
            </span>
          }
        >
          {settings.loading && !data ? (
            <SkeletonRows rows={6} cols={2} />
          ) : (
            <div className="col" style={{ gap: 0 }}>
              <div className="kv-row">
                <span className="kv-k">开盘刷新间隔</span>
                <span className="kv-v">{secondsText(data?.refreshIntervalSeconds ?? status.refreshIntervalSeconds)}</span>
              </div>
              <div className="kv-row">
                <span className="kv-k">当前实际生效 TTL</span>
                <span className="kv-v">{secondsText(data?.effectiveTtlSeconds ?? status.ttlSeconds)}</span>
              </div>
              <div className="kv-row">
                <span className="kv-k">基础缓存 TTL</span>
                <span className="kv-v">{secondsText(data?.cacheTtlSeconds)}</span>
              </div>
              <div className="kv-row">
                <span className="kv-k">自动刷新状态</span>
                <span className="kv-v fs-12">{autoRefreshHint(refresh)}</span>
              </div>
              <div className="kv-row">
                <span className="kv-k">下次自动刷新</span>
                <span className="kv-v">
                  {refresh.shouldPoll && !refresh.paused ? `${refresh.secondsToNextRefresh} 秒后` : DASH}
                </span>
              </div>
              <div className="kv-row">
                <span className="kv-k">日线缓存（klineCache）</span>
                <span className="kv-v">
                  {kline.present
                    ? kline.enabled === false
                      ? '未启用'
                      : `已启用${kline.engine ? ` · ${kline.engine}` : ''}`
                    : DASH}
                </span>
              </div>
              <div className="kv-row">
                <span className="kv-k">缓存文件</span>
                <span className="kv-v">{kline.file ?? klineFromSettings?.file ?? DASH}</span>
              </div>
              <div className="kv-row">
                <span className="kv-k">缓存股票数</span>
                <span className="kv-v">{isNum(kline.codes) ? `${fmtInt(kline.codes)} 只` : DASH}</span>
              </div>
              <div className="kv-row">
                <span className="kv-k">缓存 K 线根数</span>
                <span className="kv-v">{isNum(kline.bars) ? `${fmtInt(kline.bars)} 根` : DASH}</span>
              </div>
              <div className="kv-row">
                <span className="kv-k">缓存最新日期</span>
                <span className="kv-v">{kline.latestDate ?? klineFromSettings?.latestDate ?? DASH}</span>
              </div>
              <div className="kv-row">
                <span className="kv-k">缓存占用空间</span>
                <span className="kv-v">{fmtBytes(kline.bytes ?? klineFromSettings?.bytes)}</span>
              </div>
            </div>
          )}
        </Card>

        {/* ---------------- 市场时钟 ---------------- */}
        <Card
          title="市场时钟"
          sub="来自 GET /market/clock，前端据此决定是否自动刷新"
          extra={
            <Badge tone={clock.isOpen ? 'teal' : 'neutral'} dot>
              {clock.phaseText || DASH}
            </Badge>
          }
        >
          <div className="col" style={{ gap: 0 }}>
            <div className="kv-row">
              <span className="kv-k">当前时段</span>
              <span className="kv-v">{clock.phaseText || DASH}</span>
            </div>
            <div className="kv-row">
              <span className="kv-k">phase</span>
              <span className="kv-v mono">{clock.phase ?? DASH}</span>
            </div>
            <div className="kv-row">
              <span className="kv-k">是否交易日</span>
              <span className="kv-v">{clock.isTradingDay ? '是' : '否'}</span>
            </div>
            <div className="kv-row">
              <span className="kv-k">交易日</span>
              <span className="kv-v">{clock.tradeDate ?? DASH}</span>
            </div>
            <div className="kv-row">
              <span className="kv-k">下次开盘</span>
              <span className="kv-v">{fmtDateTime(clock.nextOpenAt)}</span>
            </div>
            <div className="kv-row">
              <span className="kv-k">下次收盘</span>
              <span className="kv-v">{fmtDateTime(clock.nextCloseAt)}</span>
            </div>
            <div className="kv-row">
              <span className="kv-k">是否自动刷新</span>
              <span className="kv-v">
                <Badge tone={refresh.shouldPoll && !refresh.paused ? 'teal' : 'neutral'} dot>
                  {refresh.paused ? '页面隐藏 · 已暂停' : refresh.shouldPoll ? '是' : '否'}
                </Badge>
              </span>
            </div>
            <div className="kv-row">
              <span className="kv-k">最近刷新时间</span>
              <span className="kv-v">{refresh.lastUpdatedAt ? fmtClockMs(refresh.lastUpdatedAt) : DASH}</span>
            </div>
            {refresh.clockError && (
              <div className="kv-row">
                <span className="kv-k">时钟异常</span>
                <span className="kv-v fs-12 danger-text">{refresh.clockError}</span>
              </div>
            )}
          </div>
        </Card>

        <Card title="界面偏好" sub="保存在浏览器 localStorage，不随账号同步">
          <div className="col" style={{ gap: 16 }}>
            <div className="col" style={{ gap: 8 }}>
              <span className="fs-12 text-2">主题</span>
              <Segmented<'dark' | 'light'>
                value={theme}
                ariaLabel="主题切换"
                options={[
                  { value: 'dark', label: '深色终端（默认）' },
                  { value: 'light', label: '浅色办公' },
                ]}
                onChange={(value) => {
                  setTheme(value);
                  toast.info(value === 'dark' ? '已切换为深色终端主题' : '已切换为浅色主题');
                }}
              />
            </div>

            <div className="col" style={{ gap: 8 }}>
              <span className="fs-12 text-2">涨跌配色</span>
              <Segmented<'red-up' | 'green-up'>
                value={updown}
                ariaLabel="涨跌配色切换"
                options={[
                  { value: 'red-up', label: '红涨绿跌（A 股习惯）' },
                  { value: 'green-up', label: '绿涨红跌（色盲友好）' },
                ]}
                onChange={(value) => {
                  setUpDown(value);
                  toast.info(value === 'red-up' ? '已切换为红涨绿跌' : '已切换为绿涨红跌（色盲友好）');
                }}
              />
              <span className="fs-11 text-3" style={{ lineHeight: 1.8 }}>
                涨跌配色仅影响行情涨跌方向（K 线、涨跌幅、盈亏）；品牌色（蓝 / 青 / 金）与结论色语义固定不变，
                避免「结论色」与「涨跌色」混用造成误读。
              </span>
            </div>

            <div className="divider" />

            <div className="col" style={{ gap: 8 }}>
              <span className="fs-12 text-2">本地数据</span>
              <span className="fs-11 text-3">
                本地关注列表共 {watchlist.count} 只标的，与后端低吸池（/pool）互不影响。
              </span>
              <div className="row" style={{ gap: 8 }}>
                <Button
                  variant="danger"
                  onClick={() => {
                    watchlist.clear();
                    toast.info('已清空本地关注列表');
                  }}
                  disabled={watchlist.count === 0}
                >
                  清空本地关注
                </Button>
              </div>
            </div>
          </div>
        </Card>

        <Card title="关于与免责声明">
          <div className="col" style={{ gap: 12 }}>
            <div className="row" style={{ gap: 8 }}>
              <Badge tone="brand">{APP_TITLE}</Badge>
              <Badge tone="neutral">仅技术研究</Badge>
              {isSynthetic && (
                <Badge tone="violet" dot>
                  演示数据
                </Badge>
              )}
            </div>
            <p className="disclaimer">
              <strong>{DISCLAIMER_TITLE}：</strong>
              {DISCLAIMER_TEXT}
            </p>
            <div className="divider" />
            <ul className="risk-points">
              <li>所有接口均通过相对路径 <span className="mono">{BASE}</span> 访问，页面不含任何硬编码的服务地址。</li>
              <li>信号、评分、支撑位与交易计划均由后端按固定规则机械计算，前端仅做展示与排序。</li>
              <li>当后端返回 <span className="mono">ok: false</span> 时会展示中文错误与「重新加载」按钮；超时为 15 秒。</li>
              <li>
                自动刷新由后端市场时钟驱动：<span className="mono">shouldPoll</span> 为 true 时按{' '}
                <span className="mono">intervalSeconds</span> 静默刷新当前页面数据，收盘 / 休市自动停止，
                页面隐藏时暂停，回到前台立即刷新一次。
              </li>
              <li>所有列表与图表均具备加载中、空数据、错误三种状态，异常时不会出现空白页面。</li>
            </ul>
          </div>
        </Card>
      </div>
    </>
  );
}
