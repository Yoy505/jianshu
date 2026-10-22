import { useEffect } from 'react';
import { Link } from 'react-router-dom';
import { OccupationalFooter, OccupationalHeader } from '../components/OccupationalShell';
import { OCCUPATIONAL_TOPICS } from '../lib/occupational';

const stages = [
  {
    number: '01',
    date: '2026.10.08 — 11.12',
    title: '线上初筛',
    body: '围绕科学性、原创性与传播实用性开展初筛，并通知初筛合格作者后续安排。',
  },
  {
    number: '02',
    date: '2026.11.13 — 12.05',
    title: '区域复评',
    body: '按华北、华东、华中、华南、东北、西南、西北七大区域组织线上复评。',
  },
  {
    number: '03',
    date: '2026.12.10',
    title: '全国终评暨线上分享会',
    body: '集中展示点评优秀图文与视频作品，交流职业健康科普创作和传播实践。',
  },
];

// Writing prompts only; they do not add eligibility or submission requirements.
type TopicIdea = { scene: string; detail: string; tags: readonly [string, string]; image: string; alt: string };
const topicIdeas: Record<(typeof OCCUPATIONAL_TOPICS)[number], TopicIdea> = {
  职业病危害防护: { scene: '生产现场', detail: '从粉尘、噪声或化学因素等真实岗位切入，讲清风险识别与日常防护。', tags: ['风险识别', '个人防护'], image: '/occupational-images/workshop.jpg', alt: '佩戴护目镜和护耳设备的工作人员在车间操作设备' },
  办公人群肌肉骨骼健康: { scene: '办公工位', detail: '关注久坐、重复动作和工位设置，把常见不适讲成能理解的日常议题。', tags: ['工位设置', '活动习惯'], image: '/occupational-images/office.jpg', alt: '办公室工作人员调整显示器位置' },
  职场心理健康疏导: { scene: '团队与个人', detail: '围绕工作压力、情绪支持与求助途径，传递科学、平实的健康知识。', tags: ['压力识别', '支持与求助'], image: '/occupational-images/mental-wellbeing.jpg', alt: '两位同事在办公室里倾听与交流' },
  新就业形态劳动者健康保护: { scene: '流动岗位', detail: '关注外卖骑手、网约车司机等岗位的工作节奏与健康保护。', tags: ['灵活就业', '岗位场景'], image: '/occupational-images/courier.jpg', alt: '配送人员在户外休息' },
  职业健康法律科普: { scene: '权利与责任', detail: '用职场常见问题解释职业健康相关权利、责任与防护义务。', tags: ['常见问题', '法律科普'], image: '/occupational-images/health-rights.jpg', alt: '安全管理人员与劳动者一起查阅职业健康资料' },
  职场急救: { scene: '应急现场', detail: '从工作场所突发状况入手，普及科学应对和及时求助意识。', tags: ['现场应对', '及时求助'], image: '/occupational-images/first-aid-training.jpg', alt: '工作场所的急救培训中，讲师在人体模型上演示操作' },
  工作场所健康生活方式: { scene: '日常习惯', detail: '从饮食、活动和睡眠等生活细节出发，分享可实践的健康提示。', tags: ['健康习惯', '日常实践'], image: '/occupational-images/healthy-workday.jpg', alt: '同事们在工作间隙沿着绿树步道散步' },
};

export function DirectionsPage() {
  useEffect(() => { document.title = '征集方向 · 2026 职业健康科普作品征集'; }, []);
  return (
    <div className="oh-page oh-home oh-content-page">
      <OccupationalHeader />
      <main id="main-content" tabIndex={-1}>
        <section className="oh-section oh-directions">
          <div className="oh-container">
            <div className="oh-section-heading">
              <div>
                <p className="oh-kicker">01 / TOPICS</p>
                <h1>征集方向</h1>
                <p className="oh-section-subtitle">从岗位风险，到日常关怀</p>
              </div>
              <p>征集方向包含但不限于以下七类。欢迎从真实工作场景出发，用易懂的方式讲清职业健康知识。</p>
            </div>
            <ol className="oh-topic-grid">
              {OCCUPATIONAL_TOPICS.map((topic, index) => {
                const idea = topicIdeas[topic];
                return <li className="oh-topic oh-topic-photo" key={topic}>
                  <div className="oh-topic-media">
                    <img src={idea.image} alt={idea.alt} loading="lazy" />
                  </div>
                  <div className="oh-topic-body">
                    <div className="oh-topic-top"><span className="oh-topic-number">{String(index + 1).padStart(2, '0')} / 07</span><span className="oh-topic-scene">{idea.scene}</span></div>
                    <h2 className="oh-topic-name">{topic === '新就业形态劳动者健康保护' ? <>新就业形态<br />劳动者健康保护</> : topic}</h2>
                    <p className="oh-topic-detail">{idea.detail}</p>
                    <div className="oh-topic-tags" aria-label="创作切入点">{idea.tags.map((tag) => <span key={tag}>{tag}</span>)}</div>
                  </div>
                </li>;
              })}
            </ol>
            <nav className="oh-next-step" aria-label="下一步">
              <div><strong>选好创作方向，继续准备作品</strong><p>查看文件格式与提交要求，再进入投稿。</p></div>
              <Link className="oh-button oh-button-primary" to="/requirements">查看作品要求</Link>
            </nav>
          </div>
        </section>
      </main>
      <OccupationalFooter />
    </div>
  );
}

export function RequirementsPage() {
  useEffect(() => { document.title = '作品要求 · 2026 职业健康科普作品征集'; }, []);
  return (
    <div className="oh-page oh-home oh-content-page">
      <OccupationalHeader />
      <main id="main-content" tabIndex={-1}>
        <section className="oh-section oh-requirements">
          <div className="oh-container">
            <div className="oh-section-heading">
              <div>
                <p className="oh-kicker">02 / FORMATS</p>
                <h1>作品要求</h1>
                <p className="oh-section-subtitle">两种作品形式，讲好一个健康问题</p>
              </div>
              <p>内容应科学严谨、通俗易懂，适合面向广大职场劳动者传播。</p>
            </div>
            <div className="oh-format-grid">
              <article className="oh-format-card">
                <div className="oh-format-top"><span>01 / ARTICLE</span><span className="oh-format-symbol" aria-hidden="true">文</span></div>
                <h2>图文作品</h2>
                <p>围绕具体职业健康问题写作，提交 Word 文档，并填写作者信息与作品简介。</p>
                <div className="oh-format-feature"><strong>字数不限</strong><span>讲清一个健康问题</span></div>
                <dl>
                  <div><dt>提交格式</dt><dd>.docx 文档</dd></div>
                  <div><dt>平台上传限制（单文件）</dt><dd>10 MiB</dd></div>
                </dl>
                <p className="oh-format-note">如文件仍为 .doc，请先在 Word 中另存为 .docx。</p>
              </article>
              <article className="oh-format-card oh-format-card-accent">
                <div className="oh-format-top"><span>02 / VIDEO</span><span className="oh-format-symbol" aria-hidden="true">影</span></div>
                <h2>科普视频</h2>
                <p>真人讲解、情景短剧、动画科普、公益短片等形式均可，内容须科学准确、积极正向。</p>
                <div className="oh-format-feature"><strong>时长不限</strong><span>内容完整，表达清晰</span></div>
                <dl>
                  <div><dt>画质与格式</dt><dd>不低于 1080P · MP4</dd></div>
                  <div><dt>平台上传限制（单文件）</dt><dd>300 MiB</dd></div>
                </dl>
                <p className="oh-format-note">支持横屏和竖屏；投稿时同步填写作者信息。</p>
              </article>
            </div>
            <nav className="oh-next-step" aria-label="下一步">
              <div><strong>作品准备好了？</strong><p>准备作品文件、作者信息和作品简介。</p></div>
              <Link className="oh-button oh-button-primary" to="/submit">前往投稿</Link>
            </nav>
          </div>
        </section>
      </main>
      <OccupationalFooter />
    </div>
  );
}

export function ProcessPage() {
  useEffect(() => { document.title = '评审安排 · 2026 职业健康科普作品征集'; }, []);
  return (
    <div className="oh-page oh-home oh-content-page">
      <OccupationalHeader />
      <main id="main-content" tabIndex={-1}>
        <section className="oh-section oh-process">
          <div className="oh-container">
            <div className="oh-section-heading">
              <div>
                <p className="oh-kicker">03 / PROCESS</p>
                <h1>评审安排</h1>
                <p className="oh-section-subtitle">三阶段活动流程</p>
              </div>
              <p>以下日期为评审与分享安排，并非投稿截止时间。投稿通道状态以投稿页实时提示为准。</p>
            </div>
            <ol className="oh-stage-list">
              {stages.map((stage) => (
                <li className="oh-stage" key={stage.number}>
                  <span className="oh-stage-number">{stage.number}</span>
                  <div className="oh-stage-content">
                    <p className="oh-stage-date">{stage.date}</p>
                    <h2>{stage.title}</h2>
                    <p>{stage.body}</p>
                  </div>
                </li>
              ))}
            </ol>
            <nav className="oh-next-step" aria-label="下一步">
              <div><strong>查看你的作品进度</strong><p>活动安排与个人稿件状态分开显示，查询以回执为准。</p></div>
              <Link className="oh-button oh-button-primary" to="/receipt">查询投稿进度</Link>
            </nav>
          </div>
        </section>
      </main>
      <OccupationalFooter />
    </div>
  );
}
