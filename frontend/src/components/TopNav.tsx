import { Link, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { LayoutDashboard, BarChart3, TrendingUp, Newspaper, PieChart, Wrench, Calculator, CalendarClock, Bot, Sun, Moon } from 'lucide-react';
import clsx from 'clsx';
import { LanguageSwitcher } from './LanguageSwitcher';
import { useThemeStore } from '../store/themeStore';

const navItems = [
  { path: '/portfolio', labelKey: 'nav.portfolio', icon: LayoutDashboard },
  { path: '/trading', labelKey: 'nav.trading', icon: BarChart3 },
  { path: '/market', labelKey: 'nav.market', icon: TrendingUp },
  { path: '/news', labelKey: 'nav.news', icon: Newspaper },
  { path: '/backtest', labelKey: 'nav.backtest', icon: PieChart },
  { path: '/analytics', labelKey: 'nav.analytics', icon: Wrench },
  { path: '/calculator', labelKey: 'nav.calculator', icon: Calculator },
  { path: '/scheduler', labelKey: 'nav.scheduler', icon: CalendarClock },
  { path: '/agent', labelKey: 'nav.agent', icon: Bot },
];

export default function TopNav() {
  const { t } = useTranslation();
  const location = useLocation();
  const theme = useThemeStore((s) => s.theme);
  const toggleTheme = useThemeStore((s) => s.toggleTheme);

  return (
    <header className="top-nav">
      {/* 左侧：股智通 · AI 品牌 */}
      <div className="top-nav-brand">
        <div className="top-nav-avatar">
          <i className="fas fa-robot" style={{ fontSize: '18px', color: 'var(--topnav-avatar-icon)' }}></i>
        </div>
        <div>
          <span className="top-nav-brand-title">股智通 · AI</span>
          <span className="top-nav-brand-sub">
            <span className="top-nav-dot"></span>
            实时行情 · 深度分析
          </span>
        </div>
      </div>

      {/* 中间：导航菜单 */}
      <nav className="top-nav-menu">
        {navItems.map(({ path, labelKey, icon: Icon }) => (
          <Link
            key={path}
            to={path}
            className={clsx(
              'top-nav-link',
              location.pathname === path && 'active'
            )}
          >
            <Icon size={16} />
            <span>{t(labelKey)}</span>
          </Link>
        ))}
      </nav>

      {/* 右侧：主题切换 + 语言切换 + 状态 */}
      <div className="top-nav-right">
        {/* 深色/浅色模式切换 */}
        <button
          className="theme-toggle-btn"
          onClick={toggleTheme}
          title={t(theme === 'dark' ? 'theme.switchToLight' : 'theme.switchToDark')}
          aria-label={t(theme === 'dark' ? 'theme.switchToLight' : 'theme.switchToDark')}
        >
          {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
        </button>

        <LanguageSwitcher />
        <div className="top-nav-status">
          <span className="top-nav-status-dot"></span>
          <span className="top-nav-status-text">System Online</span>
        </div>
      </div>
    </header>
  );
}
