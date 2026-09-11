import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import clsx from 'clsx';
import { api, isApiError, toErrorMessage } from '../api/client';
import type { LimitUpHistoryItem, StockSignal } from '../api/types';
import { useApi } from '../hooks/useApi';
import { useAutoRefreshTarget } from '../hooks/useAutoRefresh';
import { useWatchlist } from '../hooks/useWatchlist';
import { PageHeader } from '../components/layout/PageHeader';
import { Card } from '../components/ui/Card';
import { Button } from '../components/ui/Button';
import { Badge } from '../components/ui/Badge';
import { Table } from '../components/ui/Table';
import type { Column } from '../components/ui/Table';
import { EmptyState } from '../components/ui/EmptyState';
import { ErrorState, InlineAlert } from '../components/ui/ErrorState';
import { Tooltip } from '../components/ui/Tooltip';
import { useToast } from '../components/ui/Toast';
import { Skeleton, SkeletonChart, SkeletonRows } from '../components/ui/Skeleton';
import { KLineChart } from '../components/charts/KLineChart';
import type { SupportLine } from '../components/charts/KLineChart';
import { LineChart } from '../components/charts/LineChart';
import { ScoreRing } from '../components/stock/ScoreRing';
import { VerdictBadge } from '../components/stock/VerdictBadge';
import { LimitUpTypeTag } from '../components/stock/LimitUpTypeTag';
import { SignalList } from '../components/stock/SignalList';
import { SupportLadder } from '../components/stock/SupportLadder';
import { TradePlanCard } from '../components/stock/TradePlanCard';
import { RiskCard } from '../components/stock/RiskCard';
import { dataSourceLabel } from '../utils/constants';
import { fmtAmount, fmtDate, fmtPct, fmtPrice, fmtScore, fmtTimes, fmtVolume, isNum } from '../utils/format';

/** 个股详情：专业 K 线 + 五大信号 + 支撑位阶梯 + 交易计划 + 风险 + 涨停历史 + 板块情绪 */
export default function StockDetail() {
  const { code = '' } = useParams<{ code: string }>();
  const toast = useToast();
  const watchlist = useWatchlist();
  const [adding, setAdding] = useState(false);

  const detail = useApi((signal) => api.stockDetail(code, 180, signal), [code], { enabled: /^\d{6}$/.test(code) });

  // 开盘期间自动刷新个股行情与信号（静默刷新：K 线与信号原地更新，不闪骨架屏）
  useAutoRefreshTarget(detail.refetchAsync);

  const meta = detail.data?.meta;
  const candles = detail.data?.candles ?? [];
  const signal: StockSignal | null = detail.data?.signal ?? null;
  const signals = (detail.data?.signals ?? signal?.signals ?? []).filter(Boolean);
  const lastCandle = candles.length > 0 ? candles[candles.length - 1] : null;
  const industry = detail.data?.industry ?? null;

  const supportLines: SupportLine[] = [];
  if (signal?.support) {
    const s = signal.support;
    if (isNum(s.limitOpen)) supportLines.push({ price: s.limitOpen, label: '涨停开盘价', tone: 'gold' });
    if (isNum(s.strongHalf)) supportLines.push({ price: s.strongHalf, label: '实体半分位', tone: 'brand' });
    if (isNum(s.activeSupport)) {
      supportLines.push({ price: s.activeSupport, label: s.activeSupportName ?? '关键支撑', tone: 'text' });
    }
  }

  const addToPool = async () => {
    if (!meta?.code) return;
    setAdding(true);
    try {
      await api.addPool({
        code: meta.code,
        buyLow: isNum(signal?.plan?.buyLow) ? signal?.plan?.buyLow : undefined,
        buyHigh: isNum(signal?.plan?.buyHigh) ? signal?.plan?.buyHigh : undefined,
        stopLoss: isNum(signal?.plan?.stopLoss) ? signal?.plan?.stopLoss : undefined,
        takeProfit1: isNum(signal?.plan?.takeProfit1) ? signal?.plan?.takeProfit1 : undefined,
        takeProfit2: isNum(signal?.plan?.takeProfit2) ? signal?.plan?.takeProfit2 : undefined,
        note: signal?.signalSummary || undefined,
      });
      toast.success(`${meta.name} 已加入低吸池`);
    } catch (err) {
      if (isApiError(err) && err.status === 409) toast.warn(`${meta.name} 已在低吸池中`);
      else toast.error(`加入低吸池失败：${toErrorMessage(err)}`);
    } finally {
      setAdding(false);
    }
  };

  const historyColumns: Column<LimitUpHistoryItem>[] = [
    { key: 'date', title: '涨停日', width: 110, render: (row) => <span className="mono">{fmtDate(row.date)}</span> },
    { key: 'type', title: '涨停类型', width: 110, render: (row) => <LimitUpTypeTag type={row.type} /> },
    {
      key: 'pullbackDays',
      title: '回调天数',
      width: 96,
      align: 'right',
      render: (row) => <span className="mono">{isNum(row.pullbackDays) ? `${row.pullbackDays} 天` : '—'}</span>,
    },
    {
      key: 'score',
      title: '评分',
      width: 84,
      align: 'right',
      render: (row) => <span className="cell-strong">{fmtScore(row.score)}</span>,
    },
    { key: 'verdict', title: '结论', width: 90, render: (row) => <VerdictBadge verdict={row.verdict} size="sm" /> },
  ];

  if (!/^\d{6}$/.test(code)) {
    return (
      <>
        <PageHeader title="个股详情" sub="股票代码需为 6 位数字" />
        <Card>
          <ErrorState message={`代码「${code}」格式非法，请返回股票池重新选择。`} title="参数非法" />
          <div className="row" style={{ justifyContent: 'center' }}>
            <Link className="btn" to="/stocks">
              返回股票池
            </Link>
          </div>
        </Card>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title={meta ? `${meta.name} ${meta.code}` : `个股详情 ${code}`}
        sub={
          meta
            ? `${meta.market} · ${meta.board} · ${meta.industry}${meta.isSt ? ' · ST 股' : ''} · 涨停幅度 ${(meta.limitPct * 100).toFixed(0)}%${detail.data ? ` · 数据源 ${dataSourceLabel(detail.data.dataSource)}` : ''}`
            : '加载中…'
        }
        actions={
          <>
            <Button
              variant="primary"
              loading={adding}
              onClick={addToPool}
              disabled={!meta || !signal}
              title={!signal ? '该标的当前无有效涨停信号' : undefined}
            >
              加入低吸池
            </Button>
            <Button
              variant={watchlist.has(code) ? 'gold' : 'default'}
              onClick={() => {
                const added = watchlist.toggle(code);
                toast.info(added ? `${meta?.name ?? code} 已加入本地关注` : `${meta?.name ?? code} 已取消关注`);
              }}
            >
              {watchlist.has(code) ? '★ 已关注' : '☆ 本地关注'}
            </Button>
            <Link className="btn" to="/signals">
              返回选股
            </Link>
          </>
        }
      />

      {detail.error && detail.data && (
        <div className="mt-2">
          <InlineAlert message={detail.error} onRetry={detail.refetch} hint="以下为上一次成功获取的分析结果。" />
        </div>
      )}

      {detail.error && !detail.data && (
        <Card className="mt-4">
          <ErrorState
            message={detail.error}
            title={detail.error.includes('未找到') ? '未找到该股票数据' : '数据分析加载失败'}
            onRetry={detail.refetch}
          />
        </Card>
      )}

      {detail.loading && !detail.data && (
        <div className="detail-layout mt-4">
          <Card title="日线走势">
            <SkeletonChart height={460} />
          </Card>
          <div className="detail-side">
            <Card title="综合评分">
              <Skeleton width="100%" height={120} />
            </Card>
            <Card title="五大信号">
              <SkeletonRows rows={5} cols={2} />
            </Card>
          </div>
        </div>
      )}

      {detail.data && (
        <div className="detail-layout mt-4">
          {/* 主列 */}
          <div className="col" style={{ gap: 16, minWidth: 0 }}>
            <Card
              title="日线走势"
              sub={`最近 ${candles.length} 根日 K · MA5 / MA10 / MA20 · 金色三角为涨停日`}
              extra={
                <div className="chart-legend hide-mobile">
                  <span className="legend-item" style={{ color: 'var(--gold)' }}>
                    <i className="legend-dot" style={{ background: 'currentColor' }} />
                    涨停日
                  </span>
                  <span className="legend-item" style={{ color: 'var(--brand)' }}>
                    <i className="legend-dot" style={{ background: 'currentColor' }} />
                    买入区间
                  </span>
                  <span className="legend-item" style={{ color: 'var(--text-2)' }}>
                    <i className="legend-dot" style={{ background: 'currentColor' }} />
                    支撑位
                  </span>
                </div>
              }
            >
              {candles.length === 0 ? (
                <EmptyState title="暂无 K 线数据" desc="后端未返回该标的的历史行情。" />
              ) : (
                <KLineChart
                  candles={candles}
                  height={470}
                  supportLines={supportLines}
                  buyZone={
                    isNum(signal?.plan?.buyLow) && isNum(signal?.plan?.buyHigh)
                      ? { low: signal?.plan?.buyLow ?? null, high: signal?.plan?.buyHigh ?? null }
                      : null
                  }
                  ariaLabel={`${meta?.name ?? code} 日线 K 线图`}
                />
              )}
            </Card>

            {signal && (
              <Card
                title="关键指标"
                sub="来自最新一个交易日与该轮涨停回调区间"
                extra={
                  <Tooltip content="回调幅度 =（最新收盘 - 涨停收盘）/ 涨停收盘；回撤比例 =（涨停收盘 - 最新收盘）/（涨停收盘 - 涨停开盘）" align="right">
                    <span className="fs-11 text-3">指标口径</span>
                  </Tooltip>
                }
              >
                <div className="stat-grid">
                  <div className="stat">
                    <div className="stat-label">最新收盘</div>
                    <div className={clsx('stat-value', lastCandle && (lastCandle.pctChg > 0 ? 'up' : lastCandle.pctChg < 0 ? 'down' : 'flat'))}>
                      {fmtPrice(signal.lastClose)}
                    </div>
                    <div className="stat-foot">
                      <span>{fmtDate(signal.lastDate)}</span>
                      <span>{lastCandle ? fmtPct(lastCandle.pctChg) : '—'}</span>
                    </div>
                  </div>
                  <div className="stat">
                    <div className="stat-label">涨停收盘价</div>
                    <div className="stat-value">{fmtPrice(signal.limitUpClose)}</div>
                    <div className="stat-foot">
                      <span>涨停开盘 {fmtPrice(signal.support?.limitOpen)}</span>
                      <LimitUpTypeTag type={signal.limitUpType} />
                    </div>
                  </div>
                  <div className="stat">
                    <div className="stat-label">回调天数</div>
                    <div className="stat-value">
                      {isNum(signal.pullbackDays) ? signal.pullbackDays : '—'}
                      <span className="stat-unit">天</span>
                    </div>
                    <div className="stat-foot">
                      <span>回调幅度</span>
                      <span>{fmtPct(signal.pullbackPct)}</span>
                    </div>
                  </div>
                  <div className="stat">
                    <div className="stat-label">回撤比例</div>
                    <div className="stat-value">
                      {isNum(signal.retraceRatio) ? `${(signal.retraceRatio * 100).toFixed(1)}%` : '—'}
                    </div>
                    <div className="stat-foot">
                      <span>相对涨停实体</span>
                    </div>
                  </div>
                  <div className="stat">
                    <div className="stat-label">最新成交量</div>
                    <div className="stat-value">{fmtVolume(lastCandle?.volume)}</div>
                    <div className="stat-foot">
                      <span>量比 {fmtTimes(lastCandle?.volRatio)}</span>
                      <span>换手 {isNum(lastCandle?.turnover) ? `${lastCandle?.turnover.toFixed(2)}%` : '—'}</span>
                    </div>
                  </div>
                  <div className="stat">
                    <div className="stat-label">最新成交额</div>
                    <div className="stat-value">{fmtAmount(lastCandle?.amount)}</div>
                    <div className="stat-foot">
                      <span>{fmtDate(lastCandle?.date ?? signal.lastDate)}</span>
                    </div>
                  </div>
                </div>
              </Card>
            )}

            <Card
              title="涨停历史"
              sub="该标的历次涨停及对应战法评估结果"
              flush
            >
              <Table<LimitUpHistoryItem>
                columns={historyColumns}
                rows={(detail.data.limitUpHistory ?? []).filter(Boolean)}
                rowKey={(row, index) => `${row.date}-${index}`}
                minWidth={560}
                ariaLabel="涨停历史表"
                emptyTitle="暂无涨停记录"
                emptyDesc="统计区间内该标的未出现符合战法口径的涨停。"
                skeletonRows={3}
              />
            </Card>

            <Card
              title="板块情绪"
              sub={industry ? `${industry.name} · 板块内 ${industry.memberCount ?? '—'} 只成分股` : '板块情绪走势'}
            >
              {industry ? (
                <div className="col" style={{ gap: 14 }}>
                  <div className="stat-grid">
                    <div className="stat">
                      <div className="stat-label">板块情绪评分</div>
                      <div className="stat-value gold-text">{(industry.sentimentScore ?? 0).toFixed(1)}</div>
                    </div>
                    <div className="stat">
                      <div className="stat-label">板块涨跌幅</div>
                      <div className={`stat-value ${industry.pctChg > 0 ? 'up' : industry.pctChg < 0 ? 'down' : 'flat'}`}>
                        {fmtPct(industry.pctChg)}
                      </div>
                    </div>
                    <div className="stat">
                      <div className="stat-label">板块涨停家数</div>
                      <div className="stat-value">{industry.limitUpCount ?? 0}</div>
                    </div>
                    <div className="stat">
                      <div className="stat-label">成分股数量</div>
                      <div className="stat-value">{industry.memberCount ?? '—'}</div>
                    </div>
                  </div>
                  {Array.isArray(industry.trend) && industry.trend.length >= 2 ? (
                    <LineChart
                      data={industry.trend.map((value, index) => ({
                        label: `T-${industry.trend.length - 1 - index}`,
                        value,
                      }))}
                      height={190}
                      tone="var(--gold)"
                      seriesName="板块情绪分"
                      valueFormat={(v) => v.toFixed(1)}
                      ariaLabel="板块情绪趋势折线图"
                    />
                  ) : (
                    <div className="chart-empty">板块情绪趋势数据不足</div>
                  )}
                </div>
              ) : (
                <EmptyState title="暂无板块数据" desc="后端未返回该标的的板块情绪信息。" />
              )}
            </Card>

            {detail.data.explain && (
              <Card title="战法解读" sub="依据选股标准与信号明细机械生成的中文说明">
                <p className="disclaimer" style={{ color: 'var(--text-2)' }}>
                  {detail.data.explain}
                </p>
              </Card>
            )}
          </div>

          {/* 侧栏 */}
          <div className="detail-side">
            <Card
              title="综合评分"
              extra={<VerdictBadge verdict={signal?.verdict ?? null} />}
            >
              {signal ? (
                <div className="col" style={{ gap: 14 }}>
                  <div className="row" style={{ gap: 16 }}>
                    <ScoreRing score={signal.score} size={96} thickness={8} />
                    <div className="col" style={{ gap: 6, minWidth: 0 }}>
                      <span className="row" style={{ gap: 6 }}>
                        <Badge tone={signal.verdict === 'BUY' ? 'teal' : signal.verdict === 'WATCH' ? 'gold' : 'neutral'}>
                          {signal.verdict === 'BUY' ? '五信号共振' : signal.verdict === 'WATCH' ? '等待确认' : '暂不参与'}
                        </Badge>
                      </span>
                      <span className="fs-12 text-2" style={{ lineHeight: 1.7 }}>
                        {signal.signalSummary || '暂无结论摘要'}
                      </span>
                      {signal.limitUpRejectReason && (
                        <span className="fs-11 gold-text">否决原因：{signal.limitUpRejectReason}</span>
                      )}
                    </div>
                  </div>

                  <div className="col" style={{ gap: 0 }}>
                    <div className="kv-row">
                      <span className="kv-k">通过信号数</span>
                      <span className="kv-v">
                        {signals.filter((s) => s.passed).length} / {signals.length || 5}
                      </span>
                    </div>
                    <div className="kv-row">
                      <span className="kv-k">生效支撑</span>
                      <span className="kv-v">
                        {signal.support?.activeSupportName ?? '—'} {fmtPrice(signal.support?.activeSupport)}
                      </span>
                    </div>
                    <div className="kv-row">
                      <span className="kv-k">距支撑</span>
                      <span className="kv-v">{fmtPct(signal.support?.distanceToSupportPct)}</span>
                    </div>
                    <div className="kv-row">
                      <span className="kv-k">盈亏比</span>
                      <span className="kv-v gold-text">
                        {isNum(signal.plan?.riskReward) ? signal.plan?.riskReward.toFixed(2) : '—'}
                      </span>
                    </div>
                    <div className="kv-row">
                      <span className="kv-k">风险等级</span>
                      <span className="kv-v">{signal.risk?.riskLevel ?? '—'}</span>
                    </div>
                  </div>
                </div>
              ) : (
                <EmptyState
                  title="暂无有效信号"
                  desc="该标的当前没有符合战法口径的涨停回调结构，可仅参考 K 线与板块数据。"
                />
              )}
            </Card>

            <Card title="五大共振信号" sub="权重合计 100%，通过状态与得分见下" flush>
              {signals.length > 0 ? (
                <SignalList signals={signals} />
              ) : (
                <EmptyState title="暂无信号明细" desc="该标的无有效涨停信号，未生成五大信号评分。" />
              )}
            </Card>

            <Card title="支撑位阶梯" sub="由下至上：低位支撑 → 高位支撑">
              {signal ? (
                <SupportLadder support={signal.support} lastClose={signal.lastClose} />
              ) : (
                <EmptyState title="暂无支撑位" desc="缺少涨停结构，未计算支撑位阶梯。" />
              )}
            </Card>

            <Card title="交易计划" sub="分批建仓 · 明确止损 · 两档止盈">
              <TradePlanCard plan={signal?.plan ?? null} lastClose={signal?.lastClose ?? lastCandle?.close ?? null} />
            </Card>

            <Card title="风险提示" sub="机械规则生成的风险点">
              <RiskCard risk={signal?.risk ?? null} />
            </Card>
          </div>
        </div>
      )}
    </>
  );
}
