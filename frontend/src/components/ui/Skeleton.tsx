import clsx from 'clsx';
import type { CSSProperties } from 'react';

export interface SkeletonProps {
  variant?: 'text' | 'rect' | 'circle';
  width?: number | string;
  height?: number | string;
  className?: string;
  style?: CSSProperties;
}

/** 骨架屏基础块（shimmer 动画） */
export function Skeleton({ variant = 'text', width, height, className, style }: SkeletonProps) {
  return (
    <span
      aria-hidden="true"
      className={clsx('sk', variant === 'text' && 'sk-text', variant === 'circle' && 'sk-circle', className)}
      style={{ width, height, display: 'block', ...style }}
    />
  );
}

/** 表格骨架行 */
export function SkeletonRows({ rows = 6, cols = 6 }: { rows?: number; cols?: number }) {
  const widths = ['52%', '34%', '60%', '40%', '46%', '30%', '56%', '38%'];
  return (
    <div aria-busy="true" aria-live="polite">
      <span className="sr-only">数据加载中</span>
      {Array.from({ length: rows }).map((_, r) => (
        <div className="sk-row" key={r}>
          {Array.from({ length: cols }).map((__, c) => (
            <Skeleton
              key={c}
              width={c === 0 ? 56 : widths[(r + c) % widths.length]}
              height={12}
              style={{ flex: c === 0 ? '0 0 56px' : '1 1 auto' }}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

export function SkeletonChart({ height = 200 }: { height?: number }) {
  return (
    <div aria-busy="true">
      <span className="sr-only">图表加载中</span>
      <Skeleton variant="rect" height={height} />
    </div>
  );
}
