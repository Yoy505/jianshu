# 杏临汇 · 系统架构设计

一套 FastAPI 后端支撑两个 C 端入口（依据《全域健康（全科医学康复向）商业计划书》，2026-09 起）：

- **杏临汇小程序**（原生微信小程序）——全域健康管家：九大场景服务匹配、专业人员预约、咚咚在线咨询、药品数字展厅、健康档案、健康商城骨架；
- **职业健康征集网页**（essay-h5）——职业健康科普作品介绍及作者投稿；
- 外加管理后台 admin-web（职业健康投稿与健康服务运营）。旧文学活动数据仅保留归档，不再提供公开页面或投稿 API。

## 1. 总体架构

```
                        ┌────────────────────────────────────────┐
                        │              阿里云（生产）              │
   管理人员 ──► admin-web (Vite+React+shadcn) ──┐                │
                                               │   ┌──────────┐ │
   C端用户 ──► essay-h5 (Vite+React 移动端H5) ──┼──►│ FastAPI  │ │
                                               ├──►│  :8000   │ │
   微信用户 ──► miniprogram (原生小程序) ────────┘   └────┬─────┘ │
                                                     │       │
                              ┌──────────────────────┼───────┤
                              ▼                      ▼       ▼
                        ┌──────────┐          ┌─────────┐ ┌────────┐
                        │ RDS      │          │ Redis   │ │ Celery │
                        │PostgreSQL│          │ (缓存/  │ │ Worker │
                        │ 15+      │          │ 限流/短信│ │ + Beat │
                        └──────────┘          │  /Broker)│ └───┬────┘
                                              └─────────┘     │
                                                        ┌──────▼──────┐
                                                        │ 异步任务      │
                                                        │ 短信/导出/统计│
                                                        └─────────────┘
```

## 2. 仓库结构（Monorepo）

```
jianshu/
├── docs/               # 架构 / API 契约 / 设计系统 / 部署文档
├── backend/            # FastAPI 服务（含 Celery worker）
│   ├── app/
│   │   ├── main.py         # 应用入口、路由挂载、CORS、静态目录
│   │   ├── core/           # 配置、安全(JWT/密码)、依赖项
│   │   ├── db/             # engine/session/Base
│   │   ├── models/         # SQLAlchemy 2.0 模型
│   │   ├── schemas/        # Pydantic v2 DTO
│   │   ├── api/            # 路由：admin/ wx/ h5/ uploads/
│   │   ├── services/       # 业务逻辑：统计、微信、短信、存储
│   │   └── workers/        # celery_app + tasks
│   ├── alembic/            # 数据库迁移
│   ├── uploads/            # 本地开发上传目录（生产走 OSS）
│   └── tests/
├── admin-web/          # 管理后台（React 19 + Vite + shadcn/ui + Zustand + TanStack）
├── essay-h5/           # 职业健康征集网页（React 19 + Vite，响应式）
└── miniprogram/        # 杏临汇小程序（原生，全域健康管家 C 端）
```

## 3. 技术选型

| 层 | 技术 | 版本基线 | 说明 |
|---|---|---|---|
| 后端框架 | FastAPI | ≥0.115 | async，自动 OpenAPI 文档 `/docs` |
| ORM | SQLAlchemy | 2.0 (async) | asyncpg 驱动，Alembic 迁移 |
| 数据库 | PostgreSQL | 15+（阿里云 RDS） | 生产 RDS，本地 docker |
| 缓存 | Redis | 7 | 热榜缓存、短信码、限流、Celery Broker |
| 异步任务 | Celery | 5.x | 短信发送、xlsx 导出、每日统计快照 |
| 认证 | PyJWT + pwdlib(argon2) | — | 双受众 JWT（admin / user） |
| 运行时 | Python | 3.13+（venv 虚拟环境） | 兼容 3.12–3.14 |
| 管理后台 | React + Vite + TS | React 19 | shadcn/ui、Tailwind 4 |
| 前端状态 | Zustand（客户端）+ TanStack Query v5（服务端） | — | auth 用 persist 中间件 |
| 表格 | TanStack Table v8 | — | essays 数据表（筛选/排序/分页） |
| H5 | React + Vite + Tailwind | — | 移动优先，编辑级排版设计 |
| 小程序 | 原生（Skyline 兼容） | 基础库 3.x | rpx 设计系统 |

## 4. 核心业务模型

```
Contest 征文活动 1 ──── n Essay 征文作品 n ──── 1 User(C端用户)
                          │
                          ├─ 1─n EssayReview (管理员审核记录)
                          └─ 1─n EssayLike   (点赞，唯一约束 essay+user)

AdminUser 后台账号（角色：admin 超管 / editor 审核）
DailyStats 每日统计快照（Celery Beat 生成）
```

**状态机**

- Contest：`draft → published → closed`
- Essay：`submitted(待审) → approved(通过) / rejected(驳回)`；`approved → offline(下架)`

## 5. 认证体系

- 双受众 JWT：`scope=admin`（后台）与 `scope=user`（C端），token 互不通用。
- access token 2h；refresh token 7d，统一走 `POST /auth/refresh`。
- C端登录方式：
  - 小程序：`wx.login` code → 后端 code2session → openid 自动注册；
  - H5：手机号 + 短信验证码（Redis 存码 5 分钟，60s 发送间隔 + 每日限额，Celery 异步发送）。

## 6. 异步任务（Celery）

| 任务 | 触发 | 说明 |
|---|---|---|
| `send_sms` | API 投递 | 验证码发送；dev 打日志，prod 接阿里云短信 |
| `export_essays_xlsx` | 后台点击导出 | openpyxl 生成 xlsx 至 uploads/exports，任务状态可查询 |
| `snapshot_daily_stats` | Beat 每日 00:30 | 汇总用户/投稿/点赞写入 daily_stats |

## 7. 缓存与性能

- H5 热榜 `sort=hot`：Redis 缓存 60s（key: `h5:essays:hot:{contest_id}:{page}`）。
- 短信限流：`sms:{phone}:ttl`（60s 冷却）、`sms:{phone}:daily`（每日 ≤5）。
- 浏览量：原子自增，容忍最终一致。

## 8. 环境与端口

| 服务 | 本地端口 |
|---|---|
| FastAPI | 8000（文档 `/docs`） |
| admin-web dev | 5173 |
| essay-h5 dev | 5174 |
| PostgreSQL (docker) | 5432 |
| Redis (docker) | 6379 |

> 完整 API 契约见 [api.md](api.md)；前端/小程序设计规范见 [design-system.md](design-system.md)；生产部署（RDS/OSS/域名）见 [deploy.md](deploy.md)。
