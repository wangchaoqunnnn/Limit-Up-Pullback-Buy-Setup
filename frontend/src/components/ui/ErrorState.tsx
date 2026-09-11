import { Button } from './Button';

export interface ErrorStateProps {
  message: string;
  onRetry?: () => void;
  title?: string;
  compact?: boolean;
}

export function ErrorState({ message, onRetry, title = '数据加载失败', compact = false }: ErrorStateProps) {
  return (
    <div className="state state-error" role="alert" style={compact ? { minHeight: 120, padding: '20px 12px' } : undefined}>
      <div className="state-icon" aria-hidden="true">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
          <path d="M12 8v5" strokeLinecap="round" />
          <circle cx="12" cy="16.6" r="1" fill="currentColor" stroke="none" />
          <path d="M10.3 3.9 2.6 17.2A2 2 0 0 0 4.3 20.2h15.4a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" strokeLinejoin="round" />
        </svg>
      </div>
      <div className="state-title">{title}</div>
      <div className="state-desc">{message}</div>
      {onRetry && (
        <div className="state-actions">
          <Button variant="primary" size="sm" onClick={onRetry}>
            重新加载
          </Button>
        </div>
      )}
    </div>
  );
}

export interface InlineAlertProps {
  message: string;
  onRetry?: () => void;
  /** 提示级别，默认警告（自动刷新失败但仍有旧数据） */
  tone?: 'warn' | 'danger';
  hint?: string;
}

/** 行内提示条：已有旧数据但刷新失败 / 数据可能过期时的明确提示 */
export function InlineAlert({ message, onRetry, tone = 'warn', hint }: InlineAlertProps) {
  return (
    <div className={`banner ${tone === 'danger' ? 'banner-danger' : ''}`} role="alert" style={{ marginBottom: 0 }}>
      <span className="banner-icon" aria-hidden="true">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M12 8v5M12 16.5h.01" strokeLinecap="round" />
          <path d="M12 3.6 2.9 19.4h18.2L12 3.6Z" strokeLinejoin="round" />
        </svg>
      </span>
      <div className="grow">
        <div className="strong">数据刷新失败，当前展示的是上一次成功获取的结果</div>
        <div className="fs-12 text-2" style={{ marginTop: 2, lineHeight: 1.7 }}>
          {hint ? `${hint} ` : ''}
          {message}
        </div>
      </div>
      {onRetry && (
        <Button size="sm" onClick={onRetry}>
          重试
        </Button>
      )}
    </div>
  );
}
