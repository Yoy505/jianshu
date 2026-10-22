import { HeartHandshake, Presentation, ClipboardList, Megaphone } from 'lucide-react';
import { Link } from 'react-router-dom';
import {
  CONDITIONS,
  SERVICE_TRACKS,
  TENET_TEXT,
} from './content';

const SERVICE_ICONS = [HeartHandshake, Presentation, ClipboardList, Megaphone];

/** 第一级：招募要求说明页 */
export default function InfoPage() {
  return (
    <main id="main-content" tabIndex={-1} className="vol-main">
      {/* Hero */}
      <section className="vol-hero">
        <div className="vol-container">
          <p className="vol-kicker">VOLUNTEER / 志愿者招募</p>
          <h1>杏临千行<span className="vol-hero-sep" aria-hidden="true">·</span><span className="vol-hero-line2">守护职业健康</span></h1>
          <p className="vol-hero-tagline">千行聚力 · 志愿同行</p>
          <ul className="vol-hero-facts" aria-label="参与要求摘要"><li>不限行业、不限专业</li><li>每年至少参加1次线下活动</li></ul>
          <div className="vol-hero-actions">
            <Link className="vol-button vol-button-primary" to="/apply">在线报名成为志愿者</Link>
            <a className="vol-button vol-button-secondary" href="#tracks">了解服务内容</a>
          </div>
        </div>
      </section>

      {/* 招募条件 */}
      <section className="vol-section vol-section-alt" id="conditions" aria-labelledby="vol-cond-title">
        <div className="vol-container">
          <div className="vol-section-heading">
            <p className="vol-kicker">QUALIFICATIONS / 招募条件</p>
            <h2 id="vol-cond-title">四项基本要求</h2>
            <p className="vol-section-subtitle">不限行业、不限专业，有热忱即可报名。</p>
          </div>
          <ol className="vol-cond-list">
            {CONDITIONS.map((item, index) => (
              <li key={item.title}>
                <span className="vol-cond-number" aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>
                <div>
                  <h3>{item.title}</h3>
                  <p>{item.desc}</p>
                </div>
              </li>
            ))}
          </ol>
        </div>
      </section>

      {/* 志愿服务内容 */}
      <section className="vol-section" id="tracks" aria-labelledby="vol-tracks-title">
        <div className="vol-container">
          <div className="vol-section-heading">
            <p className="vol-kicker">SERVICE / 志愿服务内容</p>
            <h2 id="vol-tracks-title">四类志愿服务</h2>
            <p className="vol-section-subtitle">公益实践 · 科普宣传 · 活动协助 · 宣传支持</p>
          </div>
          <div className="vol-grid">
            {SERVICE_TRACKS.map((track, index) => {
              const Icon = SERVICE_ICONS[index];
              return (
              <article className="vol-card" key={track.title}>
                <span className="vol-card-icon"><Icon size={24} aria-hidden="true" /></span>
                <h3>{track.title}</h3>
                <p>{track.desc}</p>
              </article>
            ); })}
          </div>
        </div>
      </section>

      {/* 招募宗旨 */}
      <section className="vol-section" id="tenet" aria-labelledby="vol-tenet-title">
        <div className="vol-container">
          <div className="vol-section-heading">
            <p className="vol-kicker">MISSION / 招募宗旨</p>
            <h2 id="vol-tenet-title">让专业能力服务社会所需</h2>
          </div>
          <blockquote className="vol-tenet">
            <p>{TENET_TEXT}</p>
          </blockquote>
        </div>
      </section>

      {/* 底部报名引导 */}
      <section className="vol-section vol-section-alt" aria-labelledby="vol-join-title">
        <div className="vol-container">
          <div className="vol-join">
            <div className="vol-join-copy">
              <p className="vol-kicker">JOIN US / 加入志愿者团队</p>
              <h2 id="vol-join-title">千行聚力，志愿同行</h2>
              <p>期待与你并肩，在服务奉献中收获实践成长。确认符合招募条件后，进入下一步填写报名信息。</p>
              <p>报名提交后，请保存回执号；工作人员将通过所填手机号联系。</p>
            </div>
            <Link className="vol-button vol-button-primary" to="/apply">填写报名信息</Link>
          </div>
        </div>
      </section>
    </main>
  );
}
