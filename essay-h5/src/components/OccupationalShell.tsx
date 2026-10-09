import { Link, NavLink } from 'react-router-dom';

export function OccupationalHeader() {
  return (
    <header className="oh-header">
      <div className="oh-container oh-header-inner">
        <Link className="oh-brand" to="/" aria-label="杏临汇职业健康科普作品征集首页">
          <img className="oh-brand-mark" src="/brand/xinglinhui-mark.png" alt="" width="40" height="40" />
          <span className="oh-brand-wordmark">
            <strong>杏临汇</strong>
            <small>职业健康专题</small>
          </span>
        </Link>
        <nav className="oh-nav" aria-label="页面导航">
          <NavLink to="/" end>首页</NavLink>
          <NavLink to="/directions">征集方向</NavLink>
          <NavLink to="/requirements">作品要求</NavLink>
          <NavLink to="/process">评审安排</NavLink>
          <NavLink to="/receipt">进度查询</NavLink>
        </nav>
        <NavLink className="oh-header-action" to="/submit">提交作品</NavLink>
      </div>
    </header>
  );
}

export function OccupationalFooter() {
  return (
    <footer className="oh-footer">
      <div className="oh-container oh-footer-inner">
        <div className="oh-footer-identity">
          <span className="oh-footer-label">杏临汇 · 2026</span>
          <strong>职业健康科普作品征集</strong>
        </div>
        <div className="oh-footer-meta">
          <span>图文作品 / 科普视频</span>
          <p>投稿通道状态以提交作品页实时提示为准</p>
        </div>
      </div>
    </footer>
  );
}
