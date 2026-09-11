import clsx from 'clsx';
import type { ReactNode } from 'react';

export interface TooltipProps {
  content: ReactNode;
  children: ReactNode;
  /** 靠右对齐（避免溢出容器右边界） */
  align?: 'center' | 'right';
  className?: string;
}

/** 纯 CSS 提示浮层：hover 与键盘 focus 均可见，无需 JS 定位 */
export function Tooltip({ content, children, align = 'center', className }: TooltipProps) {
  return (
    <span className={clsx('tt', align === 'right' && 'tt-right', className)}>
      {children}
      <span className="tt-bubble" role="tooltip">
        {content}
      </span>
    </span>
  );
}
