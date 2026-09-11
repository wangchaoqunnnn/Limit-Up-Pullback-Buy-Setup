import clsx from 'clsx';
import type { SignalDetail } from '../../api/types';
import { fmtMetricByKey, fmtScore, metricLabel } from '../../utils/format';
import { SIGNAL_META } from '../../utils/constants';
import { EmptyState } from '../ui/EmptyState';

export interface SignalListProps {
  signals: SignalDetail[] | null | undefined;
  /** 紧凑模式：仅标题 + 进度条 */
  compact?: boolean;
  /** metrics 展示的最大条数 */
  maxMetrics?: number;
  ariaLabel?: string;
}

/** 五大共振信号明细列表：通过状态 + 得分进度条 + 中文 detail + metrics 键值对 */
export function SignalList({ signals, compact = false, maxMetrics = 6, ariaLabel = '五大信号明细' }: SignalListProps) {
  const list = Array.isArray(signals) ? signals.filter(Boolean) : [];

  if (list.length === 0) {
    return <EmptyState title="暂无信号数据" desc="该标的当日未生成信号明细。" />;
  }

  return (
    <ul className="signal-list" aria-label={ariaLabel}>
      {list.map((signal) => {
        const meta = SIGNAL_META[signal.key];
        const score = typeof signal.score === 'number' ? signal.score : 0;
        const metrics = signal.metrics && typeof signal.metrics === 'object' ? Object.entries(signal.metrics) : [];
        return (
          <li className="signal-item" key={signal.key ?? signal.name}>
            <div className="signal-head">
              <span className="signal-name">
                <span className={clsx('signal-dot', signal.passed ? 'pass' : 'fail')} aria-hidden="true">
                  {signal.passed ? '✓' : '✕'}
                </span>
                <span className="ellipsis" title={signal.name || meta?.label}>
                  {signal.name || meta?.label || signal.key}
                </span>
                <span className="tag" title="该信号在综合评分中的权重">
                  权重 {((signal.weight ?? meta?.weight ?? 0) * 100).toFixed(0)}%
                </span>
              </span>
              <span className="signal-score">
                {fmtScore(score)}
                <span className="text-3"> · 贡献 {fmtScore(signal.contribution)}</span>
              </span>
            </div>

            <div
              className="bar"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(score)}
              aria-label={`${signal.name || signal.key} 得分`}
            >
              <div
                className={clsx('bar-fill', signal.passed ? 'teal' : 'gold')}
                style={{ width: `${Math.max(0, Math.min(100, score))}%` }}
              />
            </div>

            {!compact && signal.detail && <p className="signal-detail">{signal.detail}</p>}

            {!compact && metrics.length > 0 && (
              <div className="metrics-grid">
                {metrics.slice(0, maxMetrics).map(([key, value]) => (
                  <span className="metric-chip" key={key} title={key}>
                    <span className="metric-k">{metricLabel(key)}</span>
                    <span className="metric-v">{fmtMetricByKey(key, value)}</span>
                  </span>
                ))}
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

export interface SignalSummaryGridProps {
  signals: SignalDetail[] | null | undefined;
}

/** 表格展开行内的五信号紧凑网格 */
export function SignalSummaryGrid({ signals }: SignalSummaryGridProps) {
  const list = Array.isArray(signals) ? signals.filter(Boolean) : [];
  if (list.length === 0) return <div className="text-3 fs-12">暂无信号明细</div>;
  return (
    <div className="expand-grid">
      {list.map((signal) => {
        const meta = SIGNAL_META[signal.key];
        const score = typeof signal.score === 'number' ? signal.score : 0;
        return (
          <div className="signal-mini" key={signal.key ?? signal.name}>
            <div className="signal-mini-head">
              <span className="row" style={{ gap: 6, minWidth: 0 }}>
                <span className={clsx('signal-dot', signal.passed ? 'pass' : 'fail')} aria-hidden="true">
                  {signal.passed ? '✓' : '✕'}
                </span>
                <span className="ellipsis" title={signal.name || meta?.label}>
                  {meta?.short ?? signal.name}
                </span>
              </span>
              <span className="mono fs-12">{fmtScore(score)}</span>
            </div>
            <div className="bar" aria-hidden="true">
              <div
                className={clsx('bar-fill', signal.passed ? 'teal' : 'gold')}
                style={{ width: `${Math.max(0, Math.min(100, score))}%` }}
              />
            </div>
            <div className="fs-11 text-3 ellipsis" title={signal.detail}>
              {signal.detail}
            </div>
          </div>
        );
      })}
    </div>
  );
}
