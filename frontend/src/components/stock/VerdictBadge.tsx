import clsx from 'clsx';
import type { Verdict } from '../../api/types';
import { VERDICT_META } from '../../utils/constants';

export interface VerdictBadgeProps {
  verdict: Verdict | null | undefined;
  size?: 'sm' | 'md';
  className?: string;
}

/** 最终结论徽标：可低吸 / 观察 / 放弃 */
export function VerdictBadge({ verdict, size = 'md', className }: VerdictBadgeProps) {
  const key = (verdict ?? '') as Verdict;
  const meta = VERDICT_META[key];
  const label = meta?.label ?? (verdict ? verdict : '未评级');
  const toneClass = verdict ? `verdict-${verdict.toLowerCase()}` : 'verdict-reject';
  return (
    <span
      className={clsx('verdict', toneClass, className)}
      style={size === 'sm' ? { fontSize: 11, padding: '1px 7px' } : undefined}
      title={meta?.desc}
    >
      {label}
    </span>
  );
}
