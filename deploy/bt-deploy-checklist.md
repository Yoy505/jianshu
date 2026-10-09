# 宝塔部署清单（singlin.cn · 对齐 ClinInsOS 拓扑）

> 服务器：阿里云 ECS 2核2G3M「99计划」，公网 IP **<SERVER_IP>**，到期 2027-09-23（2026-09-23 购入，建树科技天津账号）（Ubuntu 26.04，实际购入）｜域名：singlin.cn（备案主体：健树科技（天津）有限公司）
> 子域：`mp.`（API+WS :8000）· `essay.`（H5）· `admin.`（后台）｜Nginx 模板：[nginx-singlin.conf](nginx-singlin.conf)

## 0. 备案前联调模式（域名未下号也能干）

- 域名解析未生效时，Nginx `server_name` 临时写 `_`，用 `http://<ECS公网IP>/api/v1/health` 联调
- 小程序继续用本地后端（`127.0.0.1:8000`）+ 开发者工具「不校验域名」
- H5/后台 dev 代理直接指向 `http://<ECS公网IP>:8000`（安全组临时放行 8000，联调完关掉）

## 1. 基础安装（SSH 登录后，一次性）

```bash
# 密钥登录（禁 root 密码：/etc/ssh/sshd_config → PasswordAuthentication no 后 systemctl restart ssh）
# 1) 系统更新 + 基础包
apt update && apt -y upgrade
apt -y install git curl nginx redis-server ufw ffmpeg

# 2) 宝塔（可选，ClinInsOS 同款；纯手工 nginx+supervisor 也行，二选一）
curl -sSO https://raw.githubusercontent.com/8838/btpanel-v7.7.0/main/install/install_panel.sh && bash install_panel.sh ed8484bec
#   官方版：curl -sSO https://raw.githubusercontent.com/8838/btpanel-v7.7.0/main/install/install_panel.sh && bash
#   宝塔里装：Nginx、Supervisor 管理器、（可选）PostgreSQL 管理器

# 3) PostgreSQL 15（本机自装，暂不买 RDS）
apt -y install postgresql postgresql-contrib
sudo -u postgres psql -c "CREATE USER jianshu WITH PASSWORD '改成强密码';"
sudo -u postgres psql -c "CREATE DATABASE jianshu OWNER jianshu;"

# 4) Redis 密码（/etc/redis/redis.conf → requirepass 改强密码，bind 127.0.0.1）后 systemctl restart redis-server

# 5) 防火墙：只放 80/443（22 建议改端口）
ufw allow 80,443/tcp && ufw allow 22/tcp && ufw enable
```

## 2. 应用部署

```bash
mkdir -p /www/wwwroot/singlin && cd /www/wwwroot/singlin
# 后端
git clone <仓库地址> app && cd app/backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env && vim .env
#   APP_ENV=prod  DATABASE_URL=postgresql+asyncpg://jianshu:***@127.0.0.1:5432/jianshu
#   REDIS_URL=redis://:***@127.0.0.1:6379/0  JWT_SECRET=至少 32 位随机串（不能沿用示例值）
#   CORS_ORIGINS=["https://essay.singlin.cn","https://admin.singlin.cn"]
#   WX_APPID/WX_SECRET 填小程序后台的
#   OCCUPATIONAL_SUBMISSION_DIR=/var/lib/singlin/occupational-submissions（API 用户私有目录；禁止放在 Nginx root 或公开 uploads 下）
#   OCCUPATIONAL_SUBMISSIONS_ENABLED=false（完成本节末的发布前核验后再显式设为 true）
python -c "from app.core.config import settings; assert settings.APP_ENV == 'prod'"
alembic upgrade head

# 首次管理员安全开通（仅空库一次；详见 app/bootstrap_admin.py 文档）
export BOOTSTRAP_ADMIN_USERNAME="ops.zhang"
export BOOTSTRAP_ADMIN_NAME="张运维"
export BOOTSTRAP_ADMIN_PASSWORD='≥12位强口令'
python -m app.bootstrap_admin   # 退出码 0=成功；重复执行退出码 1
unset BOOTSTRAP_ADMIN_USERNAME BOOTSTRAP_ADMIN_NAME BOOTSTRAP_ADMIN_PASSWORD

# 前端两站（本地 build 后上传 dist）
#   essay-h5:  npm run build → dist → /www/wwwroot/singlin/essay-h5
#   admin-web: npm run build → dist → /www/wwwroot/singlin/admin-web
```

> 生产严禁运行 `app.seed`：它只用于开发/测试，会创建演示数据。首个管理员用 `python -m app.bootstrap_admin` 开通（仅空库一次、口令策略强制、审计留痕）。短信通道尚未接入，生产验证码接口返回 503；旧文学活动的手机号登录不得用于正式运营。2026 职业健康投稿采用独立公开表单，受私有文件存储、文件校验与限流保护。

## 3. Supervisor（进程名建议 `singlin-api`）

```
[program:singlin-api]
directory=/www/wwwroot/singlin/app/backend
command=/www/wwwroot/singlin/app/backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
autostart=true
autorestart=true
stdout_logfile=/var/log/supervisor/singlin-api.log
stderr_logfile=/var/log/supervisor/singlin-api.err.log
```

当前 WebSocket 连接表仅在单个 API 进程内；增加 API worker 前必须接入共享消息通道并做跨进程测试。

另起两个 Celery 进程（worker / beat），命令：
`celery -A app.workers.celery_app worker -l info -c 2` 与 `... beat -l info`

## 4. Nginx / SSL / 域名

1. 三个子域 A 记录指向 ECS 公网 IP（阿里云 DNS）
2. 复制 [nginx-singlin.conf](nginx-singlin.conf) 内容为宝塔站点配置（或 /www/server/panel/vhost/nginx/）
3. SSL：certbot webroot 一条命令签三域（模板顶部命令），90 天自动续期靠 certbot.timer（`systemctl status certbot.timer` 确认）
4. 验证顺序：`curl http://127.0.0.1:8000/health` → `curl https://essay.singlin.cn/api/v1/h5/occupational-submissions/meta` → 小程序切生产 BASE_URL + 后台配域名（request: https://mp.… ，socket: wss://mp.…）
5. 作者站与后台站都通过各自域名的 `/api/` 同域反代访问 FastAPI。作者站 `client_max_body_size 320m`；把模板中的 `limit_req_zone`、`limit_conn_zone` 放到 `http {}`，使上传精确路径在读取 multipart 前先限频、限并发，执行 `nginx -t`。API 私有投稿目录不配置 Nginx `alias`；`ffprobe -version` 必须正常输出。

## 5. 上线检查（对照 checklist）

- [ ] `/health` 200；Swagger `https://mp.singlin.cn/docs` 可开（或生产关掉 docs）
- [ ] 小程序真机：登录/预约/咚咚 WS（顶栏绿点）/药品厅 全通
- [ ] 首次管理员通过独立安全流程开通，未运行演示种子
- [ ] 短信服务真实发送并验证回执，手机号登录闭环通过
- [ ] 职业健康作者站：首页主题/两种格式/评审安排与 DOCX 一致；`/submit` 无短信登录阻断
- [ ] 职业健康投稿：真实 `.docx` 字数、真实 MP4 时长/分辨率均由服务端验证；不合格和超限拒绝；成功后可凭回执号在后台找到记录
- [ ] 私有文件：未授权请求无法下载；管理账号可查看详情/下载并留审计记录；文件目录不在任何静态站点根目录下
- [ ] 主办方确认投稿起止时间、主体名称/隐私告知及公开展示授权，线上关闭开关维持 `false` 直到发布决策完成
- [ ] 开放时将 `OCCUPATIONAL_SUBMISSIONS_ENABLED=true` 写入 API 的生产环境配置并重启，核对 `/api/v1/h5/occupational-submissions/meta` 的 `enabled=true`，做真实文件投稿和后台复核
- [ ] 旧文学网页深链由 Nginx 返回 410；旧 `/api/v1/h5/contests/current` 与 `/api/v1/h5/essays` 返回 404；已有历史稿件和用户数据保持原样
- [ ] 审计日志有记录；访问日志 request_id 正常
- [ ] 数据库每日备份（宝塔计划任务：pg_dump → OSS/本地）
- [ ] 阿里云监控告警：CPU>80%、磁盘>85% 短信通知
