import clsx from 'clsx';
import type { ReactNode } from 'react';
import type { BadgeTone } from '../../utils/constants';

export interface BadgeProps {
  children: ReactNode;
  tone?: BadgeTone | 'up' | 'down' | 'flat';
  dot?: boolean;
  className?: string;
  title?: string;
}

/** 结论 / 状态徽标（圆角胶囊 + 语义色） */
export function Badge({ children, tone = 'neutral', dot = false, className, title }: BadgeProps) {
  return (
    <span className={clsx('badge', `badge-${tone}`, className)} title={title}>
      {dot && <i className="badge-dot" aria-hidden="true" />}
      {children}
    </span>
  );
}
