import clsx from 'clsx';
import type { ReactNode } from 'react';
import { Skeleton } from './Skeleton';

export interface StatProps {
  label: ReactNode;
  value: ReactNode;
  /** 数值单位，渲染在数值右侧（避免写成「标签 + 单位」造成「平均回调天数 天」式重复） */
  unit?: ReactNode;
  /** 数值方向：涨跌色（up/down/flat）或结论色（brand/teal） */
  dir?: 'up' | 'down' | 'flat' | 'brand' | 'teal' | 'none';
  hint?: ReactNode;
  foot?: ReactNode;
  /** 数值右侧附加内容（如迷你图） */
  extra?: ReactNode;
  loading?: boolean;
  size?: 'md' | 'sm';
  className?: string;
  title?: string;
}

export function Stat({
  label,
  value,
  unit,
  dir = 'none',
  hint,
  foot,
  extra,
  loading = false,
  size = 'md',
  className,
  title,
}: StatProps) {
  return (
    <div className={clsx('stat', size === 'sm' && 'stat-sm', className)} title={title}>
      <div className="stat-label">
        <span className="ellipsis">{label}</span>
        {hint && <span className="text-3 fs-11 nowrap">{hint}</span>}
      </div>
      {loading ? (
        <Skeleton width="70%" height={size === 'sm' ? 18 : 24} className="mt-2" />
      ) : (
        <div className={clsx('stat-value', dir !== 'none' && dir)}>
          {value}
          {unit !== undefined && unit !== null && unit !== '' && <span className="stat-unit">{unit}</span>}
        </div>
      )}
      {(foot || extra) && (
        <div className="stat-foot">
          <span className="ellipsis">{foot}</span>
          {extra}
        </div>
      )}
    </div>
  );
}
