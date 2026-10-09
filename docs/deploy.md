# 部署指南（阿里云）

> 买什么、怎么注册、备案怎么做：见 [purchase-guide.md](purchase-guide.md)（采购与备案落地方案）。
> 本文档从「资源已就绪」开始讲部署。

## 1. 资源清单

| 资源 | 选型 | 说明 |
|---|---|---|
| ECS | 2C4G 起（ubuntu 22.04/24.04） | 跑 FastAPI + Celery + Nginx |
| RDS PostgreSQL | PG 15+ 高可用版 | 与 ECS 同 VPC，内网连接 |
| Redis | 阿里云 Redis 社区版 1G | 或同 VPC 自建 |
| OSS | 标准存储 | 封面/导出文件（生产替换本地 uploads） |
| 域名 + SSL | 已备案域名 | H5 与小程序 request 合法域名均要求 HTTPS |
| 短信 | 阿里云短信服务 | 模板需报备；当前后端尚未接入发送，生产验证码接口返回 503 |

## 2. RDS PostgreSQL 要点

- 白名单：加入 ECS 内网 IP 段。
- 账号与库：`CREATE DATABASE jianshu;`（UTF8）。
- 连接串（内网，走 asyncpg）：
  `DATABASE_URL=postgresql+asyncpg://user:pass@rm-xxx.pg.rds.aliyuncs.com:3433/jianshu`
- 生产建议开 SSL（RDS 下载 ca.pem，URL 追加 `?ssl=true` 或 sslrootcert）。
- 连接池：asyncpg pool `pool_size=10, max_overflow=20`；或前置 PgBouncer（transaction 模式时 asyncpg 需 `statement_cache_size=0`）。

## 3. 后端部署

```bash
cd backend
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 设置 APP_ENV=prod、RDS/Redis 地址、随机 JWT_SECRET、真实 WX_APPID/SECRET
# JWT_SECRET 请用密钥管理服务或 openssl rand -base64 48 生成，不能沿用示例值
# 未显式设置 APP_ENV 时默认 staging；staging/prod 都会拒绝示例 JWT 或空微信凭据
python -c "from app.core.config import settings; assert settings.APP_ENV == 'prod'"

alembic upgrade head           # 数据库迁移

# API（当前 WebSocket 连接表仅在单进程内；多 worker 前需实现共享消息通道）
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1

# Celery worker + beat
celery -A app.workers.celery_app worker -l info -c 4
celery -A app.workers.celery_app beat -l info

# 首次管理员开通（仅空库执行一次；凭据经环境变量，不进 shell history）
export BOOTSTRAP_ADMIN_USERNAME="ops.zhang"      # 3-50 位字母/数字/._-
export BOOTSTRAP_ADMIN_NAME="张运维"
export BOOTSTRAP_ADMIN_PASSWORD='≥12位，含大写/小写/数字/特殊字符中至少三类'
# 或密钥文件：export BOOTSTRAP_ADMIN_PASSWORD_FILE=/etc/jianshu/bootstrap_pw
python -m app.bootstrap_admin                    # 退出码 0=已创建；重复执行退出码 1
unset BOOTSTRAP_ADMIN_USERNAME BOOTSTRAP_ADMIN_NAME BOOTSTRAP_ADMIN_PASSWORD
```

> `app.seed` 只允许开发/测试环境运行，会创建演示健康数据；生产不得运行。首个管理员用上面的 `python -m app.bootstrap_admin` 安全开通：仅空库可执行一次、口令策略强制、审计留痕（action=admin.bootstrap）、口令不落日志；已有管理员后重复执行会以退出码 1 拒绝。生产短信通道尚未接入：验证码发送和登录返回 503，接入真实服务并验收回执前不得开放手机号登录。当前实时连接仅支持单进程演示，正式扩容前需实现共享消息通道。本文命令仅用于部署准备，不代表通过上线验收。

> 本地开发先 `docker compose up -d` 起 PG/Redis，再执行迁移与演示种子。

## 4. 前端构建

```bash
cd admin-web && npm i && npm run build   # 产物 dist/ → Nginx /admin
cd essay-h5   && npm i && npm run build   # 产物 dist/ → Nginx /  (essay.example.com)
```

- `VITE_API_BASE` 指向 `https://api.example.com/api/v1`。
- H5 部署为独立域名（如 `essay.example.com`），Nginx try_files 回退 index.html。

## 5. Nginx 要点

```nginx
server {
  listen 443 ssl http2;
  server_name api.example.com;
  location / { proxy_pass http://127.0.0.1:8000; proxy_set_header X-Real-IP $remote_addr; }
  client_max_body_size 10m;
}
```

## 6. 微信小程序

1. mp.weixin.qq.com → 开发设置，填入服务器域名（request 合法域名 = API HTTPS 域名）。
2. 后端 `.env` 配置同一个小程序的 `WX_APPID` / `WX_SECRET`（code2session 用）。
3. H5 如需微信网页授权，另需公众号 appid，并在 `essay-h5` 中替换 wx oauth 接入点。

## 7. 文件存储与私有导出

`STORAGE_DRIVER=oss` 目前仍会抛出 `NotImplementedError`，不能仅凭配置切换到 OSS。若计划使用 OSS，须先实现上传、权限、回读和失败处理并完成环境验收。

征文 xlsx 导出写入 `EXPORT_DIR`（默认 `./private_exports`），不得设置在公开 `UPLOAD_DIR` 内。API 与 Celery 进程必须共享该目录；后台通过带 `essay.export` 权限的 `/admin/essays/exports/{filename}` 下载。历史 `/uploads/exports` 在 FastAPI 已拒绝，但若 Nginx/CDN 曾直接映射上传目录，还须在代理层禁用该路径并清理既有导出文件。
