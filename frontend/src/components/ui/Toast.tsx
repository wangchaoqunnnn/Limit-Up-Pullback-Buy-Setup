import { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import clsx from 'clsx';

type ToastKind = 'success' | 'error' | 'info' | 'warn';

interface ToastItem {
  id: number;
  kind: ToastKind;
  message: string;
}

interface ToastApi {
  show: (message: string, kind?: ToastKind) => void;
  success: (message: string) => void;
  error: (message: string) => void;
  info: (message: string) => void;
  warn: (message: string) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToast 必须在 ToastProvider 内使用');
  return ctx;
}

const ICONS: Record<ToastKind, ReactNode> = {
  success: (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--teal)" strokeWidth="2.2">
      <path d="m4 12 5 5L20 6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  ),
  error: (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--danger)" strokeWidth="2.2">
      <path d="M12 7v6M12 17h.01" strokeLinecap="round" />
      <circle cx="12" cy="12" r="9" />
    </svg>
  ),
  warn: (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--gold)" strokeWidth="2.2">
      <path d="M12 8v5M12 16.5h.01" strokeLinecap="round" />
      <path d="M12 3.6 2.9 19.4h18.2L12 3.6Z" strokeLinejoin="round" />
    </svg>
  ),
  info: (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--brand)" strokeWidth="2.2">
      <path d="M12 11v5M12 7.6h.01" strokeLinecap="round" />
      <circle cx="12" cy="12" r="9" />
    </svg>
  ),
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const seq = useRef(0);

  const remove = useCallback((id: number) => {
    setItems((list) => list.filter((t) => t.id !== id));
  }, []);

  const show = useCallback(
    (message: string, kind: ToastKind = 'info') => {
      seq.current += 1;
      const id = seq.current;
      setItems((list) => [...list.slice(-3), { id, kind, message }]);
      window.setTimeout(() => remove(id), kind === 'error' ? 6000 : 3600);
    },
    [remove],
  );

  const api = useMemo<ToastApi>(
    () => ({
      show,
      success: (message: string) => show(message, 'success'),
      error: (message: string) => show(message, 'error'),
      info: (message: string) => show(message, 'info'),
      warn: (message: string) => show(message, 'warn'),
    }),
    [show],
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="toast-stack" role="region" aria-live="polite" aria-label="消息提示">
        {items.map((item) => (
          <div key={item.id} className={clsx('toast', `toast-${item.kind}`)} role="status">
            <span className="toast-bar" aria-hidden="true" />
            <span style={{ marginTop: 1 }} aria-hidden="true">
              {ICONS[item.kind]}
            </span>
            <span className="toast-msg">{item.message}</span>
            <button className="toast-close" type="button" onClick={() => remove(item.id)} aria-label="关闭提示">
              ✕
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
