import clsx from 'clsx';
import type { SignalDetail, SignalKey } from '../../api/types';
import { SIGNAL_META } from '../../utils/constants';

export interface SignalBadgeProps {
  signalKey?: SignalKey | string;
  name?: string;
  passed: boolean;
  score?: number | null;
  showScore?: boolean;
  className?: string;
}

/** 单个信号通过状态徽标 */
export function SignalBadge({ signalKey, name, passed, score, showScore = false, className }: SignalBadgeProps) {
  const label =
    name ?? (signalKey ? SIGNAL_META[signalKey as SignalKey]?.short ?? String(signalKey) : '未知信号');
  return (
    <span
      className={clsx('badge', passed ? 'badge-up' : 'badge-neutral', className)}
      title={signalKey ? SIGNAL_META[signalKey as SignalKey]?.desc : undefined}
    >
      <i className="badge-dot" aria-hidden="true" />
      {label}
      {showScore && score !== null && score !== undefined && (
        <span className="mono" style={{ opacity: 0.85 }}>
          {score.toFixed(0)}
        </span>
      )}
      <span className="sr-only">{passed ? '已通过' : '未通过'}</span>
    </span>
  );
}

export function signalPassedCount(signals: SignalDetail[] | null | undefined): number {
  if (!Array.isArray(signals)) return 0;
  return signals.filter((s) => s?.passed).length;
}
