import clsx from 'clsx';
import type { KeyboardEvent } from 'react';

export interface SegmentedOption<T extends string> {
  value: T;
  label: string;
  title?: string;
}

export interface SegmentedProps<T extends string> {
  value: T;
  options: SegmentedOption<T>[];
  onChange: (value: T) => void;
  ariaLabel?: string;
  className?: string;
}

/** 分段控件（等价单选按钮组，支持键盘方向键） */
export function Segmented<T extends string>({ value, options, onChange, ariaLabel, className }: SegmentedProps<T>) {
  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const index = options.findIndex((o) => o.value === value);
    if (index < 0) return;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
      event.preventDefault();
      onChange(options[(index + 1) % options.length].value);
    } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
      event.preventDefault();
      onChange(options[(index - 1 + options.length) % options.length].value);
    }
  };

  return (
    <div className={clsx('seg', className)} role="radiogroup" aria-label={ariaLabel} onKeyDown={handleKeyDown}>
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={active}
            title={option.title}
            tabIndex={active ? 0 : -1}
            className={clsx('seg-item', active && 'active')}
            onClick={() => onChange(option.value)}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
