import clsx from 'clsx';
import type { ReactNode } from 'react';

export interface CardProps {
  title?: ReactNode;
  sub?: ReactNode;
  extra?: ReactNode;
  children?: ReactNode;
  className?: string;
  bodyClassName?: string;
  /** 去掉 body 内边距（表格类内容） */
  flush?: boolean;
  /** hover 上浮微交互 */
  hover?: boolean;
  id?: string;
  ariaLabel?: string;
}

export function Card({
  title,
  sub,
  extra,
  children,
  className,
  bodyClassName,
  flush = false,
  hover = false,
  id,
  ariaLabel,
}: CardProps) {
  const hasHead = Boolean(title || extra || sub);
  return (
    <section
      id={id}
      aria-label={ariaLabel ?? (typeof title === 'string' ? title : undefined)}
      className={clsx('card', hover && 'card-hover', className)}
    >
      {hasHead && (
        <header className="card-head">
          <div style={{ minWidth: 0 }}>
            {title && <h3 className="card-title">{title}</h3>}
            {sub && <div className="card-sub">{sub}</div>}
          </div>
          {extra && <div className="card-extra">{extra}</div>}
        </header>
      )}
      <div className={clsx('card-body', flush && 'flush', bodyClassName)}>{children}</div>
    </section>
  );
}
