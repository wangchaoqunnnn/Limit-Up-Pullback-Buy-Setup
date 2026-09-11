import clsx from 'clsx';
import type { ReactNode } from 'react';
import type { BadgeTone } from '../../utils/constants';

export interface TagProps {
  children: ReactNode;
  tone?: BadgeTone | 'outline';
  className?: string;
  title?: string;
}

/** 小尺寸标签（行业、板块、类型） */
export function Tag({ children, tone = 'neutral', className, title }: TagProps) {
  const toneClass =
    tone === 'neutral' ? '' : tone === 'outline' ? 'tag-outline' : `tag-${tone}`;
  return (
    <span className={clsx('tag', toneClass, className)} title={title}>
      {children}
    </span>
  );
}
