import { useEffect } from 'react';
import { Link } from 'react-router-dom';
import { OccupationalFooter, OccupationalHeader } from '../components/OccupationalShell';

export default function OccupationalHomePage() {
  useEffect(() => {
    document.title = '2026 职业健康科普作品征集 · 杏临汇';
  }, []);

  return (
    <div className="oh-page oh-home oh-landing-page">
      <OccupationalHeader />
      <main id="main-content" tabIndex={-1}>
        <section className="oh-hero">
          <div className="oh-container oh-landing-grid">
            <div className="oh-hero-content">
              <p className="oh-eyebrow oh-campaign-title"><span className="oh-campaign-year">2026</span><span aria-hidden="true">·</span><strong>杏临汇科普作品征集</strong></p>
              <h1><span className="oh-title-main">职业健康</span><span className="oh-title-sub">从工作现场出发<span className="oh-title-period">.</span></span></h1>
              <p className="oh-hero-lead">把专业知识讲清楚，让防护方法用得上。</p>
              <p className="oh-hero-description">面向医疗卫生从业者、企业 EHS 管理人员、科普创作者及社会各界，欢迎提交科学、实用、易传播的图文与短视频。</p>
              <div className="oh-landing-facts" aria-label="征集信息">
                <span>图文作品</span><span>科普视频</span><span>七大征集方向</span>
              </div>
              <div className="oh-hero-actions">
                <Link className="oh-button oh-button-primary" to="/submit">提交作品</Link>
                <Link className="oh-button oh-button-secondary" to="/requirements">查看作品要求</Link>
              </div>
              <p className="oh-hero-returning">已经投稿？<Link to="/receipt">查询评审进度</Link></p>
            </div>
            <div className="oh-landing-visual" role="img" aria-label="生产车间、办公工位和城市配送等工作场景">
              <img src="/occupational-images/workshop.jpg" alt="" />
              <img src="/occupational-images/office.jpg" alt="" />
              <img src="/occupational-images/courier.jpg" alt="" />
              <span className="oh-landing-visual-label">健康<br />在现场</span>
            </div>
          </div>
        </section>
      </main>
      <OccupationalFooter />
    </div>
  );
}
