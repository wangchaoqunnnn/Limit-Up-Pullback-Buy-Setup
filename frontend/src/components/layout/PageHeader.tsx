import type { ReactNode } from 'react';
import clsx from 'clsx';

export interface PageHeaderProps {
  title: string;
  sub?: ReactNode;
  actions?: ReactNode;
  className?: string;
}

/** 页面标题区：标题 + 副标题 + 右侧操作 */
export function PageHeader({ title, sub, actions, className }: PageHeaderProps) {
  return (
    <header className={clsx('page-header', className)}>
      <div style={{ minWidth: 0 }}>
        <h1 className="page-title">{title}</h1>
        {sub && <div className="page-sub">{sub}</div>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  );
}
