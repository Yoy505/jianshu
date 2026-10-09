# 杏临汇健康项目与作品征集代码仓库

一套 FastAPI 后端支撑杏临汇小程序与独立的作品征集网页，另有管理后台。2026 职业健康科普作品征集的改造范围及上线条件见[活动系统计划](docs/2026职业健康科普作品征集-系统改造与上线计划.md)；杏临汇健康服务的真实业务边界见[总体开发计划](docs/总体开发计划-2026-09-23.md)。

| 目录 | 端 | 技术栈 |
|---|---|---|
| `backend/` | API 服务 :8000 | FastAPI(async) · SQLAlchemy 2.0(asyncpg) · Alembic · Redis · Celery · JWT · Python 3.13(venv) |
| `miniprogram/` | **杏临汇小程序**（C 端主入口） | 原生微信小程序（rpx 设计系统，健康品牌深青+杏金） |
| `essay-h5/` | 作者投稿网页 :5174 | Vite 7 · React 19 · Tailwind 4 · TanStack Query（手机与桌面浏览器） |
| `admin-web/` | 征文管理后台 :5173 | Vite 7 · React 19 · TypeScript · Tailwind 4 · shadcn/ui · Zustand · TanStack Query/Table · recharts |
| `docs/` | 文档 | [总体开发计划](docs/总体开发计划-2026-09-23.md) · [药品展厅 R1 契约草案](docs/药品数字展厅-首期需求与数据契约.md) · [代码质量审核与方向订正](docs/代码质量审核与方向订正-2026-09-23.md) · [GLM 开发规范](docs/GLM-开发规范.md) · [架构](docs/architecture.md) · [API 契约](docs/api.md)（第一部分征文 / 第二部分 /mp 小程序） · [设计系统](docs/design-system.md) · [历史排期](docs/schedule.md) · [部署](docs/deploy.md) |

## 杏临汇小程序（全域健康管家）

依据商业计划书实现，信息架构为 4 个 tab「首页 / 管家 / 商城 / 我的」：

- **九大需求场景**：体检慢病 · 看病问诊 · 合理用药 · 术后康复 · 饮食调理 · 失眠情绪 · 失能照护 · 就医陪同 · 母婴·老年
- **演示服务流程**：进入平台 → 选场景 → 查看人员资料（尚无资质核验）→ 演示预约（尚无真实排班）→ 查看健康档案。预约创建在预发布及生产环境关闭。
- **咚咚会话**：会话列表 + 聊天；当前只有明确标识的系统自动回执，没有真人接诊。WebSocket 与轮询用于演示消息同步；接入真人需要另建身份、授权和接管流程。
- **药品数字展厅**：名称/标签（OTC/处方/基药/医保）/首字母检索 → 通用名展示页（适应症/用法/科普/品牌位/收藏）；留言仅在开发/测试环境演示，正式环境待医师核验和审核流程完成后再开放。
- **健康档案**：开发/测试环境可体验资料表单；预发布/生产环境在单独同意与删除流程完成前暂停新增和修改，仅允许本人查看已有档案。**健康商城**：药食同源/特医食品/服务包/器械浏览骨架（计划书状态：规划中）
- **服务边界**：页面展示健康信息与服务规划；人员资质、诊疗、AI 问答、真实预约和药品互动需各自完成准入与验收。免责声明不代表取得医疗服务资质。

## 作品征集网页 / 管理后台

- **2026 职业健康科普作品征集**：`/` 介绍项目书明确的主题、两类作品格式与评审安排；`/submit` 公开提交 Word `.docx` 或 MP4、作者资料及作品简介，服务端校验并生成回执。投稿开关默认关闭，开发联调需在后端配置 `OCCUPATIONAL_SUBMISSIONS_ENABLED=true`；生产开放前须完成活动规则与真实环境验收。
- **历史文学数据**：旧文学网页、公开作品与投稿 API 已下线；已有稿件、关联用户和审核记录留在原表作历史存档，不迁入职业健康活动。本地演示种子也不再生成旧文学活动。
- **管理后台**：仅在导航中提供「职业健康投稿」及健康服务工作台，授权账号可查看投稿详情、下载私有附件并审核；历史文学管理页面不再提供入口，审计记录仍保留。

## 异步任务（Celery）

短信任务接口（真实通道尚未接入）、xlsx 导出、每日统计快照（Beat 00:30）。

## 本地快速开始

```bash
# 0. 依赖：Python 3.13+、Node 20+、Docker

# 1. 基础设施（PostgreSQL 16 + Redis 7）
make up

# 2. 后端
make backend-setup          # venv + pip install
cd backend && cp .env.example .env && cd ..
# 在 backend/.env 中明确改为 APP_ENV=dev，且只使用本地演示数据库；示例默认 staging 会拒绝示例密钥启动
make seed                   # 仅本地开发/测试：迁移并生成固定口令演示数据；生产严禁运行
make backend-run            # http://localhost:8000 （接口文档 /docs）
make worker                 # 另开终端：Celery worker（短信/导出）
# make beat                 # 可选：定时统计

# 3. 管理后台
make admin-dev              # http://localhost:5173  admin / admin123

# 4. 作者投稿网页（图文可本地验收；视频另需 ffprobe）
# 在 backend/.env 中设置 OCCUPATIONAL_SUBMISSIONS_ENABLED=true 后重启 API
make h5-dev                 # http://localhost:5174 ；/submit 无需旧短信登录

# 5. 杏临汇小程序
# 微信开发者工具 → 导入 miniprogram/ → 勾选「不校验合法域名」（配置里已关 urlCheck）
# 「我的」页支持「微信一键登录」：本地联调可在 backend/.env 留空 WX_APPID，
# code 传 "dev-code" 即走本地后门（仅显式 dev/test）
# 咨询为系统演示回执；开发/测试预约及部分资料可落库，但不代表真实服务

# 无 Docker 的本机演示（可选）
cd backend && python dev_fake_redis.py &        # 极简 Redis 替身（验证码打印在控制台）
APP_ENV=dev DATABASE_URL="sqlite+aiosqlite:///./dev.db" .venv/bin/uvicorn app.main:app --port 8000
```

## 测试

```bash
cd backend && .venv/bin/python -m pytest  # sqlite 内存库 + FakeRedis，无需外部服务
```

## 生产部署（阿里云）

部署拓扑规划为 ECS + FastAPI/Celery/Nginx、PostgreSQL、Redis 与 HTTPS 域名。数据库托管方案尚未定稿；OSS 驱动、生产短信等能力仍需开发和验收。管理员已有独立初始化流程，但须在真实环境验收。现有部署文档仅供环境准备，不能视为上线许可。详见 [docs/deploy.md](docs/deploy.md)。

## 约定

- API 契约以 [docs/api.md](docs/api.md) 为唯一权威，改接口先改契约。
- 分页统一 `?page=&page_size=` → `{items,total,page,page_size}`；错误统一 `{detail}`。
- 双受众 JWT：`scope=admin`（后台）与 `scope=user`（C 端）互不通用；refresh 统一 `POST /auth/refresh`。
- 品牌：暖白纸底 `#F7F5F0` + 墨色 `#1F1F1E` + 朱砂 `#9F2F2D`，展示字体衬线（Songti SC / Noto Serif SC）。
