import clsx from 'clsx';
import { NavLink, useLocation } from 'react-router-dom';
import { APP_SUBTITLE, APP_TITLE, NAV_GROUPS, NAV_ITEMS } from '../../utils/constants';

/** 侧边栏图标（内联 SVG，无第三方图标库） */
const ICONS: Record<string, JSX.Element> = {
  dashboard: (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
      <rect x="3" y="3" width="7.5" height="8.5" rx="1.5" />
      <rect x="13.5" y="3" width="7.5" height="5" rx="1.5" />
      <rect x="3" y="14.5" width="7.5" height="6.5" rx="1.5" />
      <rect x="13.5" y="11" width="7.5" height="10" rx="1.5" />
    </svg>
  ),
  signals: (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
      <path d="M3 17.5 8.5 11l4 3.6L21 6" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M15.6 6H21v5.2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M3 21h18" strokeLinecap="round" />
    </svg>
  ),
  stocks: (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M3 9.5h18M9 9.5V20M15 9.5V20" />
    </svg>
  ),
  backtest: (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
      <path d="M12 7v5l3.2 2" strokeLinecap="round" />
      <path d="M3.5 12a8.5 8.5 0 1 0 2.6-6.1" strokeLinecap="round" />
      <path d="M3 4.5V9h4.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  ),
  pool: (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
      <path d="M12 3.6l2.6 5.3 5.9.9-4.3 4.1 1 5.9-5.2-2.8-5.2 2.8 1-5.9L3.5 9.8l5.9-.9L12 3.6Z" strokeLinejoin="round" />
    </svg>
  ),
  rules: (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
      <path d="M4 6h10M4 12h16M4 18h7" strokeLinecap="round" />
      <circle cx="18" cy="6" r="2" />
      <circle cx="14" cy="18" r="2" />
    </svg>
  ),
  settings: (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
      <circle cx="12" cy="12" r="3.2" />
      <path d="M12 3v2.2M12 18.8V21M4.2 7.5l1.9 1.1M17.9 15.4l1.9 1.1M4.2 16.5l1.9-1.1M17.9 8.6l1.9-1.1" strokeLinecap="round" />
    </svg>
  ),
};

export interface SidebarProps {
  collapsed: boolean;
  onToggleCollapse: () => void;
}

/** 左侧导航：分组导航 + 折叠 + 品牌区 */
export function Sidebar({ collapsed, onToggleCollapse }: SidebarProps) {
  const location = useLocation();
  // 个股详情归属「信号选股」，否则侧栏不会有任何项高亮
  const detailOwner = /^\/stock\//.test(location.pathname) ? '/signals' : null;

  return (
    <nav className={clsx('sidebar', collapsed && 'collapsed')} aria-label="主导航">
      <div className="sidebar-brand">
        <span className="brand-mark" aria-hidden="true">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4">
            <path d="M3 18l5.5-6.5L13 15l8-9" strokeLinecap="round" strokeLinejoin="round" />
            <circle cx="21" cy="6" r="2" fill="currentColor" stroke="none" />
          </svg>
        </span>
        {!collapsed && (
          <span className="brand-text">
            <span className="brand-name">{APP_TITLE}</span>
            <span className="brand-sub">{APP_SUBTITLE}</span>
          </span>
        )}
      </div>

      <div className="sidebar-nav">
        {NAV_GROUPS.map((group) => (
          <div key={group}>
            <div className="nav-group-label">{collapsed ? group.slice(0, 1) : group}</div>
            {NAV_ITEMS.filter((item) => item.group === group).map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === '/'}
                className={({ isActive }) => clsx('nav-item', (isActive || item.to === detailOwner) && 'active')}
                title={collapsed ? `${item.label} · ${item.desc}` : item.desc}
                aria-current={item.to === detailOwner ? 'page' : undefined}
              >
                <span className="nav-icon" aria-hidden="true">
                  {ICONS[item.icon]}
                </span>
                <span className="nav-label">{item.label}</span>
              </NavLink>
            ))}
          </div>
        ))}
      </div>

      <div className="sidebar-foot">
        <button
          type="button"
          className="btn btn-sm btn-ghost"
          onClick={onToggleCollapse}
          aria-label={collapsed ? '展开侧边栏' : '收起侧边栏'}
          title={collapsed ? '展开侧边栏' : '收起侧边栏'}
        >
          <span aria-hidden="true">{collapsed ? '»' : '«'}</span>
          {!collapsed && <span>收起侧边栏</span>}
        </button>
        {!collapsed && <span className="sidebar-foot-note fs-11">仅供技术研究与学习使用</span>}
      </div>
    </nav>
  );
}
