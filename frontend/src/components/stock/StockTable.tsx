import clsx from 'clsx';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import type { StockSignal } from '../../api/types';
import { Table } from '../ui/Table';
import type { Column, TableSort } from '../ui/Table';
import { Button } from '../ui/Button';
import { VerdictBadge } from './VerdictBadge';
import { LimitUpTypeTag } from './LimitUpTypeTag';
import { SignalSummaryGrid } from './SignalList';
import { Sparkline } from '../charts/Sparkline';
import { useWatchlist } from '../../hooks/useWatchlist';
import { fmtDate, fmtPct, fmtPrice, fmtScore, isNum } from '../../utils/format';

export interface StockTableProps {
  items: StockSignal[];
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
  sort?: TableSort | null;
  onSortChange?: (next: TableSort) => void;
  onRowClick?: (item: StockSignal) => void;
  expandedKey?: string | null;
  onAddPool?: (item: StockSignal) => void;
  addingCode?: string | null;
  footer?: ReactNode;
  emptyTitle?: string;
  emptyDesc?: string;
  ariaLabel?: string;
}

/** 当日涨跌幅：由 sparkline 末两点推算（契约未提供独立字段时的防御性推导） */
function dayPct(item: StockSignal): number | null {
  const series = Array.isArray(item.sparkline) ? item.sparkline.filter(isNum) : [];
  if (series.length >= 2 && series[series.length - 2] > 0) {
    return series[series.length - 1] / series[series.length - 2] - 1;
  }
  return null;
}

/** 核心选股结果表：代码 / 名称 / 行业 / 现价 / 涨跌幅 / 涨停日 / 类型 / 回调 / 评分 / 结论 / 操作 */
export function StockTable({
  items,
  loading = false,
  error = null,
  onRetry,
  sort = null,
  onSortChange,
  onRowClick,
  expandedKey = null,
  onAddPool,
  addingCode = null,
  footer,
  emptyTitle = '暂无符合条件的标的',
  emptyDesc = '当前筛选条件下没有命中五大共振信号的标的，可放宽评分区间或更换涨停类型。',
  ariaLabel = '信号选股结果表',
}: StockTableProps) {
  const watchlist = useWatchlist();
  const rows = Array.isArray(items) ? items.filter(Boolean) : [];

  const columns: Column<StockSignal>[] = [
    {
      key: 'meta.code',
      title: '代码 / 名称',
      width: 160,
      render: (item) => (
        <div className="stock-cell">
          <span className="stock-cell-name">
            {item.meta?.name ?? '—'}
            {item.meta?.isSt && <span className="tag tag-danger">ST</span>}
            {watchlist.has(item.meta?.code ?? '') && (
              <span className="gold-text" title="已在本地关注列表" aria-label="已关注">
                ★
              </span>
            )}
          </span>
          <span className="stock-cell-meta">
            {item.meta?.code ?? '—'} · {item.meta?.market ?? '—'} · {item.meta?.board ?? '—'}
          </span>
        </div>
      ),
    },
    {
      key: 'meta.industry',
      title: '行业',
      width: 88,
      render: (item) => <span className="fs-12 text-2 ellipsis">{item.meta?.industry ?? '—'}</span>,
    },
    {
      key: 'lastClose',
      title: '现价',
      width: 78,
      align: 'right',
      render: (item) => <span className="cell-strong">{fmtPrice(item.lastClose)}</span>,
    },
    {
      key: 'pctChg',
      title: '当日涨跌',
      width: 92,
      align: 'right',
      sortable: true,
      render: (item) => {
        const pct = dayPct(item);
        const dir = pct === null ? 'flat' : pct > 0 ? 'up' : pct < 0 ? 'down' : 'flat';
        return <span className={clsx('mono', dir)}>{fmtPct(pct)}</span>;
      },
    },
    {
      key: 'sparkline',
      title: '近期走势',
      width: 104,
      render: (item) => <Sparkline data={item.sparkline ?? []} width={76} height={26} />,
    },
    {
      key: 'limitUpDate',
      title: '涨停日',
      width: 148,
      render: (item) => (
        <span className="row" style={{ gap: 6 }}>
          <span className="mono fs-12 text-2">{fmtDate(item.limitUpDate)}</span>
          <LimitUpTypeTag type={item.limitUpType} />
        </span>
      ),
    },
    {
      key: 'pullbackDays',
      title: '回调天数',
      width: 84,
      align: 'right',
      sortable: true,
      render: (item) => <span className="mono">{isNum(item.pullbackDays) ? `${item.pullbackDays} 天` : '—'}</span>,
    },
    {
      key: 'pullbackPct',
      title: '回调幅度',
      width: 90,
      align: 'right',
      render: (item) => (
        <span className={clsx('mono', isNum(item.pullbackPct) && item.pullbackPct < 0 ? 'down' : 'flat')}>
          {fmtPct(item.pullbackPct)}
        </span>
      ),
    },
    {
      key: 'score',
      title: '评分',
      width: 108,
      align: 'right',
      sortable: true,
      render: (item) => {
        const score = isNum(item.score) ? item.score : 0;
        return (
          <span className="row" style={{ gap: 8, justifyContent: 'flex-end' }}>
            <span
              className="bar"
              style={{ width: 44, height: 5 }}
              aria-hidden="true"
            >
              <span
                className={clsx('bar-fill', score >= 85 ? 'gold' : score >= 75 ? 'teal' : 'flat')}
                style={{ display: 'block', width: `${Math.max(0, Math.min(100, score))}%`, height: '100%' }}
              />
            </span>
            <span className="cell-strong" style={{ minWidth: 34, textAlign: 'right' }}>
              {fmtScore(score)}
            </span>
          </span>
        );
      },
    },
    {
      key: 'verdict',
      title: '结论',
      width: 88,
      render: (item) => <VerdictBadge verdict={item.verdict} size="sm" />,
    },
    {
      key: 'actions',
      title: '操作',
      width: 152,
      align: 'left',
      sticky: 'right',
      render: (item) => {
        const code = item.meta?.code ?? '';
        const name = item.meta?.name ?? code;
        return (
          <span className="row" style={{ gap: 6 }} onClick={(e) => e.stopPropagation()}>
            <Button
              size="sm"
              variant="primary"
              loading={addingCode === code}
              onClick={() => onAddPool?.(item)}
              title={`将 ${name} 加入低吸池`}
              aria-label={`将 ${name} 加入低吸池`}
            >
              加入低吸池
            </Button>
            <Link
              className="btn btn-sm btn-ghost"
              to={`/stock/${code}`}
              onClick={(e) => e.stopPropagation()}
              title={`查看 ${name} 详情`}
              aria-label={`查看 ${name} 详情`}
            >
              详情
            </Link>
          </span>
        );
      },
    },
  ];

  return (
    <Table<StockSignal>
      columns={columns}
      rows={rows}
      rowKey={(item, index) => item.meta?.code ?? `row-${index}`}
      loading={loading}
      error={error}
      onRetry={onRetry}
      sort={sort}
      onSortChange={onSortChange}
      onRowClick={onRowClick}
      minWidth={1196}
      ariaLabel={ariaLabel}
      emptyTitle={emptyTitle}
      emptyDesc={emptyDesc}
      expandedKey={expandedKey}
      skeletonRows={10}
      rowClassName={(item) => (item.verdict === 'BUY' ? 'row-buy' : item.verdict === 'WATCH' ? 'row-watch' : undefined)}
      renderExpanded={(item) => (
        <div className="expand-panel">
          <div className="row-between" style={{ marginBottom: 10 }}>
            <span className="row" style={{ gap: 8 }}>
              <span className="strong">{item.meta?.name}</span>
              <span className="mono fs-12 text-3">{item.meta?.code}</span>
              <VerdictBadge verdict={item.verdict} size="sm" />
              <LimitUpTypeTag type={item.limitUpType} full />
            </span>
            <span className="fs-12 text-2">{item.signalSummary}</span>
          </div>
          <SignalSummaryGrid signals={item.signals} />
        </div>
      )}
      footer={footer}
    />
  );
}
