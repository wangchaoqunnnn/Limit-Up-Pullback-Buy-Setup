import type { ReactNode } from 'react';

export interface EmptyStateProps {
  title?: string;
  desc?: string;
  icon?: ReactNode;
  action?: ReactNode;
}

export function EmptyState({
  title = '暂无数据',
  desc = '当前条件下没有可展示的内容。',
  icon,
  action,
}: EmptyStateProps) {
  return (
    <div className="state" role="status">
      <div className="state-icon" aria-hidden="true">
        {icon ?? (
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
            <rect x="3" y="5" width="18" height="14" rx="2" />
            <path d="M3 10h18M7 15h4" strokeLinecap="round" />
          </svg>
        )}
      </div>
      <div className="state-title">{title}</div>
      <div className="state-desc">{desc}</div>
      {action && <div className="state-actions">{action}</div>}
    </div>
  );
}
