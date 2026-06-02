import { useEffect } from 'react';
import { Outlet } from 'react-router-dom';
import TopNav from './TopNav';
import { useThemeStore } from '../store/themeStore';
import '../styles/agent-theme.css';

export default function Layout() {
  const theme = useThemeStore((s) => s.theme);

  // 同步 data-theme 属性到 html 根元素
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
  }, [theme]);

  return (
    <div
      className="h-screen flex flex-col overflow-hidden"
      style={{
        backgroundColor: 'var(--agent-bg-deep)',
        transition: 'background-color 0.3s ease',
      }}
    >
      {/* 全局顶部导航栏 — 所有页面统一显示 */}
      <TopNav />

      {/* 主内容区域 — overflow:hidden 防止全局滚动抢夺内部滚动 */}
      <main className="flex-1 flex flex-col" style={{ minHeight: 0, overflow: 'hidden' }}>
        <Outlet />
      </main>
    </div>
  );
}
