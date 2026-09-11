import { useLocation } from 'react-router-dom';
import clsx from 'clsx';
import { api } from '../../api/client';
import { useApi, useClock } from '../../hooks/useApi';
import { useAutoRefresh } from '../../hooks/useAutoRefresh';
import { useThemeMode, useUpDownMode } from '../../utils/color';
import { APP_TITLE, NAV_ITEMS } from '../../utils/constants';
import { activeSourceView } from '../../utils/sourceHealth';
import { offlineRefreshText, pollingRefreshText } from '../../utils/marketClock';
import { fmtClockMs, fmtDateTime } from '../../utils/format';
import { Badge } from '../ui/Badge';
import { Tooltip } from '../ui/Tooltip';

export interface TopBarProps {
  onOpenMenu: () => void;
  onToggleSidebar: () => void;
}

/** 顶栏：面包屑 + 数据源标识 + 市场状态 + 刷新倒计时 / 手动刷新 + 时钟 + 主题 / 涨跌配色切换 */
export function TopBar({ onOpenMenu, onToggleSidebar }: TopBarProps) {
  const location = useLocation();
  const now = useClock(1000);
  const [theme, setTheme] = useThemeMode();
  const [updown, setUpDown] = useUpDownMode();

  const settings = useApi((signal) => api.settings(signal), [], { pollMs: 60000 });
  const refresh = useAutoRefresh();

  const matched = NAV_ITEMS.filter((item) =>
    item.to === '/' ? location.pathname === '/' : location.pathname.startsWith(item.to),
  );
  const current = matched[matched.length - 1] ?? NAV_ITEMS[0];
  // 个股详情没有独立的导航项，面包屑显式指出来路，避免与「市场总览」错配
  const onStockDetail = /^\/stock\//.test(location.pathname);
  const trail = onStockDetail ? '信号选股 › 个股详情' : current.desc;

  /* 数据源标识：synthetic / usingFallback → 醒目警示徽标；真实源 → 具名 + 成功色调 */
  const source = activeSourceView(settings.data?.dataSourceActive, settings.data?.usingFallback);

  /* 市场状态：阶段文案 + 是否处于交易时段 */
  const phaseText = refresh.clockError && !refresh.clockReady ? '时钟未就绪' : refresh.phaseText || '状态未知';
  const marketTitle = [
    `市场状态：${phaseText}`,
    `交易日：${refresh.tradeDate ?? '—'}`,
    refresh.nextOpenAt ? `下次开盘：${fmtDateTime(refresh.nextOpenAt)}` : null,
    refresh.nextCloseAt ? `下次收盘：${fmtDateTime(refresh.nextCloseAt)}` : null,
    refresh.isOpen ? '当前处于交易时段，行情实时刷新中' : '当前非交易时段',
    refresh.clockError ? `市场时钟异常：${refresh.clockError}` : null,
  ]
    .filter(Boolean)
    .join('\n');

  /* 刷新策略：开盘期间显示倒计时，非交易时段显示下次开盘时间 */
  const clockUsable = refresh.clockReady || refresh.clock !== null;
  const countdownText = refresh.paused
    ? '页面隐藏 · 已暂停'
    : refresh.shouldPoll
      ? pollingRefreshText(refresh.secondsToNextRefresh > 0 ? refresh.secondsToNextRefresh : refresh.intervalSeconds)
      : clockUsable
        ? offlineRefreshText({ phaseText: refresh.phaseText, nextOpenAt: refresh.nextOpenAt })
        : '自动刷新不可用';

  const refreshTitle = refresh.paused
    ? '页面处于后台，自动刷新已暂停；回到前台会立即刷新一次'
    : refresh.shouldPoll
      ? `开盘期间每 ${refresh.intervalSeconds} 秒自动刷新当前页面数据（后端市场时钟 shouldPoll=true）`
      : clockUsable
        ? '当前为收盘 / 休市时段，自动刷新已停止，避免无意义请求；开盘后自动恢复'
        : `市场时钟接口暂不可用，已降级为不自动刷新${refresh.clockError ? `：${refresh.clockError}` : ''}`;

  const manualTitle = refresh.refreshing
    ? '正在刷新…'
    : `立即刷新当前页面数据。${refresh.shouldPoll ? `开盘期间也会每 ${refresh.intervalSeconds} 秒自动刷新。` : '当前非交易时段，自动刷新已暂停。'}`;

  const pad = (v: number) => String(v).padStart(2, '0');
  const timeText = `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
  const dateText = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
  const updownText = updown === 'red-up' ? '红涨绿跌' : '绿涨红跌';

  return (
    <header className="topbar">
      <button
        type="button"
        className="icon-btn only-mobile"
        onClick={onOpenMenu}
        aria-label="打开导航菜单"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M4 7h16M4 12h16M4 17h16" strokeLinecap="round" />
        </svg>
      </button>

      <button
        type="button"
        className="icon-btn hide-mobile"
        onClick={onToggleSidebar}
        aria-label="折叠或展开侧边栏"
        title="折叠 / 展开侧边栏"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
          <rect x="3" y="4" width="18" height="16" rx="2" />
          <path d="M9.5 4v16" />
        </svg>
      </button>

      {/* 顶栏不再重复页面 H1，仅显示应用名 + 面包屑 */}
      <div className="topbar-title">
        <span className="topbar-page">{APP_TITLE}</span>
        <span className="topbar-crumb hide-mobile">{trail}</span>
      </div>

      <div className="topbar-right">
        {/* 数据源标识：小屏也不隐藏 */}
        <Tooltip content={source.title} align="right">
          <Badge tone={source.tone} dot>
            {source.label}
          </Badge>
        </Tooltip>

        {/* 市场状态：小屏也不隐藏，isOpen 时柔和脉冲 */}
        <span
          className={clsx('market-state', refresh.isOpen && !refresh.paused && 'is-open', refresh.paused && 'is-paused')}
          title={marketTitle}
        >
          <i className="market-dot" aria-hidden="true" />
          <span className="market-state-text">{phaseText}</span>
        </span>

        {/* 刷新倒计时 + 最后更新时间：<1100px 折叠 */}
        <span className="refresh-meta hide-narrow" title={refreshTitle}>
          <span className="mono refresh-countdown">{countdownText}</span>
          {refresh.lastUpdatedAt !== null && (
            <span className="refresh-updated">更新 {fmtClockMs(refresh.lastUpdatedAt)}</span>
          )}
        </span>

        {/* 手动刷新：在途期间禁用 */}
        <button
          type="button"
          className={clsx('icon-btn', refresh.refreshing && 'is-busy')}
          onClick={() => {
            void refresh.refreshNow();
          }}
          disabled={refresh.refreshing}
          aria-label="立即刷新当前页面数据"
          title={manualTitle}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
            <path d="M20 11.5a8 8 0 1 0-2.6 6" strokeLinecap="round" />
            <path d="M20 4.5v6h-6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>

        <span className="clock hide-mobile" title={`本地时间 ${dateText} ${timeText}`}>
          {dateText} {timeText}
        </span>

        <Tooltip
          content={
            updown === 'red-up'
              ? '涨跌配色：红涨绿跌（A 股习惯）。点击切换为绿涨红跌。'
              : '涨跌配色：绿涨红跌（色盲友好）。点击切换为红涨绿跌。'
          }
        >
          <button
            type="button"
            className="switch-pill"
            onClick={() => setUpDown(updown === 'red-up' ? 'green-up' : 'red-up')}
            aria-label={`切换涨跌配色方案，当前为${updownText}`}
          >
            <span className="switch-pill-label">涨跌配色</span>
            <span className={clsx('switch-pill-value', updown === 'red-up' ? 'up' : 'down')}>{updownText}</span>
          </button>
        </Tooltip>

        <Tooltip content={theme === 'dark' ? '切换到浅色主题' : '切换到深色主题'}>
          <button
            type="button"
            className="icon-btn"
            onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
            aria-label="切换主题"
          >
            {theme === 'dark' ? (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                <circle cx="12" cy="12" r="4" />
                <path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.2 5.2l1.4 1.4M17.4 17.4l1.4 1.4M18.8 5.2l-1.4 1.4M6.6 17.4l-1.4 1.4" strokeLinecap="round" />
              </svg>
            ) : (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                <path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5Z" strokeLinejoin="round" />
              </svg>
            )}
          </button>
        </Tooltip>
      </div>
    </header>
  );
}
