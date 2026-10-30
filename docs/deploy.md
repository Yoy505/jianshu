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
python3.15 -m venv .venv && source .venv/bin/activate  # Python 3.15.1（官方最新稳定版，backend/.python-version 声明确切版本）；依赖按 backend/requirements.lock 精确安装
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
2. 后端 `.env` 配置同一个小程序的 `WX_APPID` / `WX_SECRET`（code2session、stable_token、getuserphonenumber 使用，密钥不进入小程序）。
3. 微信公众平台需具备手机号快速验证组件使用权限及可用额度；隐私保护指引填写“手机号”及登录用途。后端 Redis 应可用，服务端可访问 api.weixin.qq.com。该能力须在对应 AppID 真机测试，模拟器桩不能证明授权成功。
4. 先部署支持 `{code, phone_code}` 的后端，再发布带微信手机号授权按钮的小程序；旧版仅传 code 的真实微信登录将返回 400，需安排版本同步更新。保留 H5 原有短信登录接口。
5. 真机验收：游客浏览、点击使用提示、取消授权、同意授权后真实手机号掩码、回到原入口、退出登录、过期重登与账号绑定冲突；不记录 code、手机号明文、AppSecret 或 access_token。
6. H5 如需微信网页授权，另需公众号 appid，并在 `essay-h5` 中替换 wx oauth 接入点。

## 7. 文件存储与私有导出

`STORAGE_DRIVER=oss` 目前仍会抛出 `NotImplementedError`，不能仅凭配置切换到 OSS。若计划使用 OSS，须先实现上传、权限、回读和失败处理并完成环境验收。

征文 xlsx 导出写入 `EXPORT_DIR`（默认 `./private_exports`），不得设置在公开 `UPLOAD_DIR` 内。API 与 Celery 进程必须共享该目录；后台通过带 `essay.export` 权限的 `/admin/essays/exports/{filename}` 下载。历史 `/uploads/exports` 在 FastAPI 已拒绝，但若 Nginx/CDN 曾直接映射上传目录，还须在代理层禁用该路径并清理既有导出文件。

## 服务包交易（工作一，2026-10-22 补充）

- **迁移顺序**：`alembic upgrade head`（本批新增 `e5b8c2d4f6a1` 权益跨批次防重与事件、`f6c9d3e5a7b2` 开售放行与授权来源）；回滚按逆序 `downgrade`，不影响既有域。
- **微信支付回调**：`WXPAY_NOTIFY_URL` 建议 `https://mp.singlin.cn/api/v1/pay/wxpay/notify`，Nginx 无需特殊配置（POST JSON，签名在应用层校验）。商户号、APIv3 密钥、证书序列号、商户私钥文件、平台证书文件六项齐全才开放支付；未配置时支付能力按 503 关闭，**积分兑换不受影响但仍受开售放行约束**。
- **退款异常处理入口**：后台「订单与退款 → 退款/异常队列」。微信明确拒绝的退款单为 `failed`（订单 `refund_failed`，可直接重新发起）；网络超时类为 `processing`，用「查证微信状态」按钮（`POST /admin/mp/refunds/{id}/query`）以微信结果自动收账；人工退款登记成功结论必须附线下凭据。线上退款单不允许人工直接判成功。
- **开售放行**：商品默认不可售。管理员在「服务包商品 → 详情 → 开售放行」按版本/环境放行（必填依据），撤销立即回到不可售；`finance` 岗位仅有订单/退款/积分权限。
- **单位名单原件**：存于 `COMMERCE_BATCH_DIR`（私有目录，含手机号），经 `GET /admin/mp/batches/{id}/file` 鉴权下载并审计；请纳入备份与保留期策略。
- **监控建议**：关注 `mp_payment_events` 中 `notify_rejected`/`transaction_reused` 增量与 `mp_refunds` 中 `processing` 停留时长（超 24h 人工介入）。

## 技术栈统一（2026-10-22 补充）

- **Python**：后端统一为官方最新稳定版 **3.15.1**（`backend/.python-version`；`.venv-py315` 为默认 venv，旧 `.venv`(3.12.8) 保留回退）。依赖复现：`backend/requirements.lock`（生产 57 包）/ `requirements-test.lock`（全量 63 包），均来自 3.15.1 实测解析；`make venv-check` 校验 venv 版本一致性。
- **无 Docker 的本机集成验证**（T4 实测路径）：源码编译 Redis 7.2.12（`~/src-build/redis-7.2.12/src/redis-server --port 6399 --save '' --appendonly no`）与 PostgreSQL 16.9（`initdb` + `pg_ctl -o "-p 54329"`，`./configure --without-icu --without-readline --without-openssl`）到用户目录；`createdb jianshu_int_test` 后：
  ```bash
  export JIANSHU_PG_TEST_URL='postgresql+asyncpg://<user>@127.0.0.1:54329/jianshu_int_test'
  export JIANSHU_REDIS_TEST_URL='redis://127.0.0.1:6399/15'
  make test-integration   # 11 例：PG 迁移循环/并发/跨批次唯一/回调入账 + Redis 命令/限流/恢复 + Celery 跨进程
  ```
  入口自带防误连保护（PG 仅本机 host 且库名含 test/int；Redis db≥10；键前缀隔离清理）。
- **迁移**：新增 `a7d0e4f6b8c3`（PG 集成发现 `mp_user_consents` 缺 created_at/updated_at 的模型/迁移漂移，SQLite create_all 路径曾掩盖）；生产升级照常 `alembic upgrade head`。

## 工作二（服务履约）部署补充（2026-10-22）

- **迁移链**：`d4a7b1c2e3f5`（工作一）→ `e5c9a3f7b1d8` → `f8d2b6a9c4e1` → `a2c8e4f1b7d3` → `b6d3f8a2c9e4` → `c7e1a5b9d3f2` → `d4f7b2c8e6a1`（电话域 + 授权 purpose 放宽）。部署执行 `alembic upgrade head`；不可逆项与回退说明见各迁移文件头与《GLM交付记录-工作二》各批次「部署与回退」。
- **Beat 周期任务**：`steward-lifecycle-sweep`（每 5 分钟，实例到期/作废）、`outreach-sweep`（每 5 分钟，触达生成与停止）、`device-sync-sweep`（每 15 分钟，无适配器时如实 blocked）、`professional-sweep`（每 15 分钟，工单超时升级）、`phone-call-sweep`（每 5 分钟，单飞调度）。worker/beat 需重启加载。
- **配置**：全部 `STEWARD_*` 项见 `backend/.env.example`（条款已表驱动；CCC 密钥仅生产注入）。
- **监控点位（告警阈值由运维依据定，不自造医疗阈值）**：Celery 失败/积压、`device_sync_logs` 中 failed/blocked 增长、`outreach_intents` failed 堆积、`steward_professional_orders` 超时未结与高优先级未结（阻断结案）、`phone_call_orders` pending_config 长期滞留（CCC 未接）、`steward_pilot_releases` 全部撤销（激活全阻断）。人工入口：各后台工作台页面与接口。
- **外部阻断态（预期现象，非故障）**：无设备适配器 → 设备同步逐条 blocked 待配置；无 CCC → 电话工单审核后待配置；通道未决策 → 触达仅人工处理；均不伪造接通/送达。

## 代码整改与功能补齐部署补充（2026-10-26）

- **迁移链（合并后）**：工作一线 `… → f6c9d3e5a7b2 → a7d0e4f6b8c3` 与工作二线 `… → d4f7b2c8e6a1 → e9a4c7f1b5d8` 在 `9d5def0e96c6` 合并，之后 `b3c7e9d1a5f2`（商品版本履约配置 + 实例配置快照，F03）→ `c4d8f2a6b9e3`（电话回调事件表 + 在途请求号，F05）→ `d5e9a3b7c1f4`（签发计划来源 / 核验责任人 + 激活凭证责任人快照，C3；存量计划回填 ops_entry）→ `e6f1b4c8d2a7`（人员资质核验字段 + 排班表，C2）。**C2/C3 上线后尚未激活的实例门禁会按事实显示缺项**（存量人员无核验记录、无排班；存量计划为运营录入），需运营补核验、排班、签发或 verify 后方可激活；已激活实例不受影响。
- **升级前先核对当前修订**：`alembic current`。无论库停在工作一旧头 `a7d0e4f6b8c3`、工作二旧头 `e9a4c7f1b5d8` 还是更早，直接 `alembic upgrade head` 均已在 SQLite 与 PG 16.9 实测通过（F01 修补：两条线对 `mp_user_consents` 时间戳列的同源修复改为"列归属感知"，升级只补缺失列、回退只在另一条线不再需要时删列）。回退到分叉点 `e5c9a3f7b1d8` 与全量回退同样验证通过；存量同意记录不丢失。
- **`b3c7e9d1a5f2` 数据回填**：迁移前已存在的全部商品版本回填为 `fulfillment_type='steward'`、`service_days=45`（即此前"所有权益一律生成 45 天管家实例"的既成事实），并加 CHECK 约束；回退（`downgrade -1`）删除三列与约束，不可恢复回填值以外的信息。新建版本默认 `none`（普通商品不建实例），需要管家履约的商品在后台版本表单显式选择并填写实际服务期。
- **后台行为变化（F02）**：`editor` 等非管理员账号须经「账号↔人员绑定」（`/admin/steward/staff-links`）绑定到在职人员后，才能看到该人员当前指派实例的健康正文；未绑定账号列表为空、详情 403。管理员（`steward.scope.all`）不受限但跨范围读详情会写审计 `steward.scope.cross_read`。上线前为运营人员建立绑定。
- **F04**：医师名下有未结专业工单时 `PATCH /admin/steward/staff/{id}` 停用返回 409，须先转交；停用 / 撤销绑定 / 转交后的医师账号不能再完成工单或签发更正报告。
- **Makefile**：`make backend-run / worker / beat / seed / test / test-integration` 统一使用 `VENV_BIN := $(abspath $(VENV))/bin`，可用 `VENV=` 覆盖为相对或绝对路径；`seed` 仅开发库。

### 审核后修补补充（2026-10-26，修订 f7a2c9e4b6d8）

本轮已验证本机 SQLite 与隔离 PG，尚未部署。上线需先备份并执行 `alembic upgrade head`，该批次修订为 `f7a2c9e4b6d8`；当前目标头见下方 2026-10-27 补充。

- 电话尝试在调用适配器前持久化，适配器必须传递工单 ID 与当前尝试号；回调至少返回 `attempt_no` 或持久化的供应商 `request_id`。旧在途工单缺可靠关联的回调拒绝推进，需供应商查询与人工对账，不能默认关联最新轮次。回退删除关联列，`accepted` 尝试转为 `blocked` 历史，须复核在途工单。
- 新商品版本冻结完整随访规则，旧版本与实例仅补缺项，明确配置和已生成任务保留。未知的购买时全局参数无法恢复；固定兼容基线为 7/14/21/28/35/42（按服务期截断），上线前核对已售条款和原始配置证据。回退保留冻结后的 JSON。
- 医师资质有效性开始约束实际签发、复评、工单完成、结案和更正；运营账号不能完成医学复评或签发报告。历史复评缺可信签发证据时，创建 `order_type=final_review` 且关联复评 `task_id` 的专业工单，由当前合资格医师完成，再结案；原任务结果保留。
- 业务 PG 测试必须使用 `.venv-py315/bin/python -m pytest --business-db=postgres tests/…` 并注入 `JIANSHU_PG_TEST_URL=postgresql+asyncpg://…@127.0.0.1:54329/…test`。每例创建并清理独立 schema；账号须具备建 schema 权限。`DATABASE_URL` 不能选择业务夹具，连不上 PG 直接失败。专用 SQLite 夹具与无数据库断言需另计，Redis 仍使用内存桩；独立 `tests/integration` 按其入口验证实际 PG/Redis/Celery。

交付记录第 2.3、5.3、6.3 节此前无效的 PG 业务结论已撤回；本轮证据与未验收范围见该记录第 8 节。

### 并发修补与复审（2026-10-27，当前目标头 a8d3f5b7c9e1）

- 上线先备份并执行 `alembic upgrade head` 至 `a8d3f5b7c9e1`（父修订 `f7a2c9e4b6d8`）。本修订扩展 `phone_callback_events.result` 至 `VARCHAR(24)`，CHECK 加入 `pending_correlation`；没有业务事件的伪造回填。旧的商品规则、资质、拨打关联处理要求仍按前两节执行。
- 请求号先到、受理响应后到时，签名有效的回调可先落待关联事件。事件仅归属接收时的尝试号；受理信息落库的同一事务自动匹配并应用，未知请求不推进，旧请求和终态保持拒绝。调度与回调先锁工单、再处理尝试和事件，同一结果不能互相覆盖。
- 监控待关联事件长期积压、`in_progress` 工单长期未收尾及 `stale / rejected_state` 增长；本修补未实现供应商查询、真实 CCC 或中断任务恢复的外部验收，超时未知结果仍需按实际供应商对账。
- 回退前须清理或对账 `pending_correlation` 事件并备份。`alembic downgrade f7a2c9e4b6d8` 会把尚未关联事件留为 `rejected_state`（绝不标为已完成），恢复旧 CHECK 和 `VARCHAR(16)`；原事件号、报文摘要和详情保留。升级回来不会把这些拒绝记录自动当成成功回执。
- `tests/test_postgres_write_races.py` 为常驻真实 PG 并发入口，必须显式使用 `--business-db=postgres` 与本机隔离 `JIANSHU_PG_TEST_URL`。测试用独立连接及 `pg_blocking_pids()` 确认真实阻塞；不连 PG 时这些用例明确跳过，不计为通过。业务 Redis 仍为桩，PG 业务回归不等于 Redis/Celery 完整联调。

本次未部署、未变更 H5 与 nginx 原有未提交内容。验证结果与修补前后对照见交付记录第 9 节。
