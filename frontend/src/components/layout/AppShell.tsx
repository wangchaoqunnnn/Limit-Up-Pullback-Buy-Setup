import { useEffect, useState } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import clsx from 'clsx';
import { Sidebar } from './Sidebar';
import { TopBar } from './TopBar';

/** 终端外壳：左侧导航 + 顶栏 + 内容区（响应式：<768 折叠为抽屉） */
export function AppShell() {
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();

  useEffect(() => {
    setMobileOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    if (!mobileOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMobileOpen(false);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [mobileOpen]);

  return (
    <div className="shell">
      <div className={clsx('shell-sidebar', mobileOpen && 'open')}>
        <Sidebar collapsed={collapsed} onToggleCollapse={() => setCollapsed((v) => !v)} />
      </div>

      {mobileOpen && (
        <div className="sidebar-scrim" onClick={() => setMobileOpen(false)} role="presentation" />
      )}

      <div className="shell-main">
        <TopBar onOpenMenu={() => setMobileOpen(true)} onToggleSidebar={() => setCollapsed((v) => !v)} />
        <a className="sr-only" href="#main-content">
          跳转到主内容
        </a>
        <main className="content" id="main-content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
