import { useEffect } from 'react';
import { Link, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import InfoPage from './InfoPage';
import ApplyPage from './ApplyPage';
import { useApplication } from './useApplication';

const NAV_ITEMS = [
  { href: '#tenet', label: '招募宗旨' },
  { href: '#conditions', label: '招募条件' },
  { href: '#tracks', label: '服务内容' },
];

export default function App() {
  const { pathname, hash } = useLocation();
  const application = useApplication();
  const isInfo = pathname === '/';

  useEffect(() => {
    if (isInfo) document.title = '杏临千行·守护职业健康 · 志愿者招募';
  }, [isInfo]);

  useEffect(() => {
    if (hash) document.getElementById(hash.slice(1))?.scrollIntoView({ block: 'start' });
    else window.scrollTo({ top: 0, behavior: 'instant' });
  }, [pathname, hash]);

  return (
    <div className="vol-page">
      <a className="vol-skip-link" href="#main-content">跳到主要内容</a>

      <header className="vol-header">
        <div className="vol-container vol-header-inner">
          <Link className="vol-brand" to="/" aria-label="杏临千行志愿者招募首页">
            <img className="vol-brand-mark" src="/brand/xinglinhui-mark.png" alt="" width="40" height="40" />
            <span className="vol-brand-wordmark">
              <strong>杏临千行</strong>
              <small>守护职业健康</small>
            </span>
          </Link>
          <nav className="vol-nav" aria-label="页面导航">
            {isInfo ? (
              NAV_ITEMS.map((item) => (
                <a key={item.href} href={item.href}>{item.label}</a>
              ))
            ) : (
              <Link to="/">招募说明</Link>
            )}
          </nav>
          {isInfo ? <Link className="vol-header-action" to="/apply">在线报名</Link> : <span className="vol-header-current">志愿者报名</span>}
        </div>
      </header>

      <Routes>
        <Route path="/" element={<InfoPage />} />
        <Route path="/apply" element={<ApplyPage application={application} />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>

      <footer className="vol-footer">
        <div className="vol-container vol-footer-inner">
          <div>
            <span className="vol-footer-label">杏临千行</span>
            <strong>守护职业健康 · 志愿者招募</strong>
          </div>
          <p>千行聚力 · 志愿同行</p>
        </div>
      </footer>
    </div>
  );
}
