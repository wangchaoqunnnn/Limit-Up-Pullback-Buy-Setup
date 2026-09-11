import clsx from 'clsx';
import type { ReactNode } from 'react';
import { clamp } from '../../utils/format';

export interface ProgressBarProps {
  /** 0 ~ 1 的比例；也可用 percent 传 0~100 */
  value: number;
  tone?: 'brand' | 'up' | 'down' | 'gold' | 'teal' | 'flat';
  size?: 'md' | 'lg';
  label?: ReactNode;
  showValue?: boolean;
  valueText?: ReactNode;
  ariaLabel?: string;
  className?: string;
}

export function ProgressBar({
  value,
  tone = 'brand',
  size = 'md',
  label,
  showValue = false,
  valueText,
  ariaLabel,
  className,
}: ProgressBarProps) {
  const pct = clamp(value, 0, 1);
  return (
    <div className={clsx('bar-row', className)}>
      {label && <span className="text-2">{label}</span>}
      <span className="bar-val">{showValue ? (valueText ?? `${(pct * 100).toFixed(0)}%`) : ''}</span>
      <div
        className={clsx('bar', size === 'lg' && 'bar-lg')}
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(pct * 100)}
        aria-label={ariaLabel ?? (typeof label === 'string' ? label : '进度')}
      >
        <div className={clsx('bar-fill', tone !== 'brand' && tone)} style={{ width: `${pct * 100}%` }} />
      </div>
    </div>
  );
}

export interface ProgressLineProps {
  value: number;
  tone?: 'brand' | 'up' | 'down' | 'gold' | 'teal' | 'flat';
  height?: number;
  label?: ReactNode;
  percentText?: ReactNode;
}

/** 单行进度条（带左侧名称与右侧数值），用于信号明细与通过率 */
export function ProgressLine({ value, tone = 'brand', height = 6, label, percentText }: ProgressLineProps) {
  const pct = clamp(value, 0, 1);
  return (
    <div className="bar-row">
      {label && <span className="text-2">{label}</span>}
      <span className="bar-val">{percentText ?? `${(pct * 100).toFixed(0)}%`}</span>
      <div
        className={clsx('bar')}
        style={{ height }}
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(pct * 100)}
        aria-label={typeof label === 'string' ? label : '进度'}
      >
        <div className={clsx('bar-fill', tone !== 'brand' && tone)} style={{ width: `${pct * 100}%` }} />
      </div>
    </div>
  );
}
