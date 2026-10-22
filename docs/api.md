# API 契约（v1）

Base URL：`http://<host>:8000/api/v1`

## 通用约定

- 裸 REST 风格，HTTP 状态码表意；错误体 `{"detail": "错误信息"}`。
- 认证头：`Authorization: Bearer <access_token>`。
- JWT claims：`sub`(用户id字符串)、`scope`(`admin`|`user`)、`type`(`access`|`refresh`)、`exp`。
  - **admin token 与 user token 不通用**（scope 校验）。
- 分页：请求 `?page=1&page_size=20`（page 从 1 起，page_size 默认 20、最大 100）。
  响应：`{"items": [...], "total": 123, "page": 1, "page_size": 20}`。
- 时间：ISO 8601 UTC 字符串，如 `"2026-09-21T08:30:00Z"`。
- 健康检查：`GET /health`（无 /api 前缀）→ `{"status": "ok"}`。

### 后台角色权限矩阵（2026-09-22 起）

所有 `/admin/*` 写接口经权限点校验，无权限返回 `403 {"detail": "无权限执行该操作"}`。

| 权限点 | admin（管理员） | editor（编辑） |
|---|---|---|
| 仪表盘 / 各列表只读 | ✓ | ✓ |
| `essay.review` `essay.feature` `essay.offline` `essay.export` | ✓ | ✓ |
| `contest.manage`（创建/编辑/状态） | ✓ | ✓ |
| `contest.delete` | ✓ | ✗ |
| `user.manage`（封禁/解封） | ✓ | ✗ |
| `audit.read`（审计日志查询） | ✓ | ✗ |
| `mp.manage`（健康域全部状态变更与编辑） | ✓ | ✓ |
| `occupational.read`（职业健康投稿列表、联系方式、附件下载） | ✓ | ✓ |
| `occupational.initial_review`（职业健康投稿形式初审：领取/释放/通过/驳回） | ✓ | ✓ |
| `occupational.final_review`（职业健康投稿终审；与初审不得同一账号） | ✓ | ✗ |
| `volunteer.read`（杏临千行志愿者报名查看与状态管理） | ✓ | ✗ |
| `commerce.product.manage`（服务包与版本维护、上下架，2026-10-21 起） | ✓ | ✓ |
| `commerce.order.read`（订单、支付事件、退款队列查询） | ✓ | ✓ |
| `commerce.order.refund`（发起退款、登记退款结论、主动对账） | ✓ | ✗ |
| `commerce.points.adjust`（积分人工调整） | ✓ | ✗ |
| `commerce.batch.manage`（采购单位维护、单位查看账号、名单预览） | ✓ | ✓ |
| `commerce.batch.confirm`（批次确认发放 / 取消、权益作废 / 改号） | ✓ | ✗ |
| `steward.read`（服务履约实例、日历、时间线、人员名单查询，2026-10-22 起） | ✓ | ✓ |
| `steward.manage`（门禁录入、指派、暂停/恢复/结案、任务登记、人员管理） | ✓ | ✗ |

### 限流与防重复提交（2026-09-22 起）

C 端用户级写接口限流（Redis 固定窗口，超限 `429 {"detail": "操作过于频繁，请稍后再试"}`；
Redis 不可用时自动放行不阻断业务）：

| 接口 | 限额 |
|---|---|
| `POST /mp/appointments`（预约） | 10 次/分钟 |
| `POST /mp/consults/{id}/messages`（咨询发言） | 20 次/分钟 |

预约防重（业务幂等，2026-09-23 起为数据库约束保证）：同一用户存在 `pending`/`confirmed`
状态且**同日同时段**的预约时，再次提交返回 `409 {"detail": "该时段已有进行中的预约"}`。
并发请求下该不变量由数据库层强制（PostgreSQL 部分唯一索引
`uq_mp_appointments_active_slot`；SQLite 测试路径为等价触发器，迁移 `a1c4f7e2b9d8`）——
无论应用层预检与写入如何竞争，同槽位最多存在一条进行中预约，冲突请求统一得到上述 409。

## 枚举

| 枚举 | 取值 |
|---|---|
| Contest.status | `draft` / `published` / `closed` |
| Essay.status | `submitted` / `approved` / `rejected` / `offline` |
| AdminUser.role | `admin` / `editor` |
| OccupationalSubmission.work_type | `article`（图文 DOCX）/ `video`（MP4） |
| OccupationalSubmission.status | `submitted` / `reviewing` / `accepted` / `rejected`；`accepted` 仅表示收件审核通过，不代表获奖或公开发表 |

## 对象结构

以下 Contest、Essay、User 为旧文学活动及共用账号的历史数据结构，当前作者网页不再调用文学接口。

**Contest**
```json
{
  "id": 1, "title": "历史文学活动", "description": "……",
  "cover_url": null, "rules": "投稿须知……", "prizes": "一等奖……",
  "status": "published", "start_at": "2026-09-01T00:00:00Z",
  "end_at": "2026-10-31T23:59:59Z", "essay_count": 42,
  "created_at": "...", "updated_at": "..."
}
```

**Essay（列表项）**
```json
{
  "id": 1, "title": "巷口的桂花", "excerpt": "前 120 字摘要…",
  "author": {"id": 3, "nickname": "南屿", "avatar": null},
  "anonymous": false, "contest": {"id": 1, "title": "历史文学活动"},
  "status": "approved", "score": 88, "featured": true,
  "like_count": 23, "view_count": 451, "created_at": "..."
}
```
详情另含 `"content": "全文…"`、`"liked_by_me": false`、`"reviews": [...]`（仅 admin 详情）。

**User（C端）**
```json
{"id": 3, "nickname": "杏林友37", "avatar": null, "phone": "138****0001",
 "status": "active", "created_at": "...", "last_login_at": "..."}
```

---

## 一、后台认证 `/admin/auth`

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/admin/auth/login` | `{username, password}` → `{"access_token","refresh_token","admin":{"id","username","name","role"}}`；错误凭据 401 |
| POST | `/auth/refresh` | `{refresh_token}` → 新 token 对（admin/user 通用，按 refresh token 的 scope 签发） |
| GET | `/admin/auth/me` | 需 admin token → 当前后台账号 |

## 二、后台 · 历史统计接口（不用于当前工作台）

`GET /admin/dashboard/stats`（admin token）
```json
{
  "totals": {"users": 120, "essays": 89, "contests": 3, "pending_review": 7, "likes": 540},
  "essay_trend": [{"date": "2026-09-08", "submitted": 5, "approved": 3}, ...],
  "status_dist": [{"status": "approved", "count": 60}, {"status": "submitted", "count": 7}, ...]
}
```
`essay_trend` 为近 14 天（含空日期补零）。

## 三、后台 · 历史文学活动 `/admin/contests`（无界面入口）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/admin/contests?keyword=&status=&page=&page_size=` | 分页列表 |
| POST | `/admin/contests` | `{title, description, cover_url?, rules?, prizes?, start_at, end_at}` |
| GET | `/admin/contests/{id}` | 详情 |
| PATCH | `/admin/contests/{id}` | 部分更新 |
| DELETE | `/admin/contests/{id}` | 仅无投稿时允许（否则 409） |
| POST | `/admin/contests/{id}/status` | `{"status": "published"\|"closed"\|"draft"}` |

## 四、后台 · 历史文学作品 `/admin/essays`（无界面入口）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/admin/essays?status=&contest_id=&keyword=&page=&page_size=` | 分页（按 created_at 倒序） |
| GET | `/admin/essays/{id}` | 详情含 content 与 reviews |
| POST | `/admin/essays/{id}/review` | `{action:"approve"\|"reject", score?:0-100, comment?}` → 记录审核并改状态 |
| POST | `/admin/essays/{id}/feature` | `{"featured": true\|false}` |
| POST | `/admin/essays/{id}/offline` | 下架（approved → offline） |
| POST | `/admin/essays/export` | `{contest_id?, status?}` → `202 {"task_id": "..."}` |
| GET | `/admin/tasks/{task_id}` | `{"task_id","status":"pending"\|"success"\|"failed","result":{"url":"/admin/essays/exports/<随机文件名>.xlsx"}\|null}` |
| GET | `/admin/essays/exports/{filename}` | 后台 `essay.export` 权限；带 Bearer token 下载私有 xlsx，响应禁缓存；旧 `/uploads/exports/*` 路径不再公开 |

review 记录：`{"id","admin_name","action","score","comment","created_at"}`。

## 五、后台 · 共用用户 `/admin/users`（当前工作台无入口）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/admin/users?keyword=&page=&page_size=` | 分页（nickname/手机号模糊） |
| POST | `/admin/users/{id}/ban` · `/unban` | 封禁/解封（封禁后 C端 token 失效，登录 403） |

## 五之二、后台 · 审计日志 `/admin/audit-logs`

后台命令端点的操作留痕（只追加，不提供修改/删除）。登录成功、活动增删改/状态变更、作品审核/精选/下架/导出、用户封禁/解封均写入。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/admin/audit-logs?action=&resource_type=&page=&page_size=` | 需 admin token，created_at 倒序分页 |

记录结构：
```json
{
  "id": 1,
  "actor_type": "admin",          // admin | user | system
  "actor_id": 1,
  "actor_name": "系统管理员",      // 冗余快照，便于人读
  "action": "essay.review",       // <域>.<动作>，见下表
  "resource_type": "essay",
  "resource_id": 7,
  "detail": {"score": 88, "comment": "..."},  // 变更要点 JSON；敏感字段不落库
  "ip_address": "203.0.113.10",
  "created_at": "..."
}
```

action 取值：`admin.login` · `contest.create/update/status/delete` · `essay.review/feature/offline/export` · `user.ban/unban`。
职业健康投稿另记 `occupational.detail_read`、`occupational.file_download`、`occupational.review`；审计不包含作者电话或原始文件内容。

## 六、C端 · 登录

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/wx/login` | 小程序：`{code, phone_code}` → `{token, refresh_token, user, is_new}`；两个 code 分别来自 wx.login 与 getPhoneNumber，后端验证微信身份和手机号后登录；真实登录缺少 phone_code 返回 400 |
| POST | `/h5/auth/sms/send` | `{phone}` → `202`；60s 内重发 429；每日 >5 条 429 |
| POST | `/h5/auth/sms/login` | `{phone, code}` → `{token, refresh_token, user, is_new}`；码错 400，5 次作废 |

## 七、历史文学作者接口（已下线）

`/h5/contests/current`、`/h5/essays`、`/h5/essays/{id}`、`/h5/my/essays` 及点赞接口已从公开 API 路由移除，统一返回 404；旧账号或小程序用户令牌也不能再向旧活动投稿。`/h5/auth/sms/*` 仍为杏临汇小程序共用的短信登录接口，不能随旧网页删除。旧文学活动的数据库表与已有记录保留归档。

## 七之二、职业健康科普作品征集（独立投稿域）

该入口独立于上述文学征文活动和短信登录。`OCCUPATIONAL_SUBMISSIONS_ENABLED=false` 为默认值；预发布/生产经隐私告知、运营和媒体校验验收后须显式开启。未开启时 `meta.enabled=false`、投稿返回 `503`，不会保存表单或文件。项目资料没有给出投稿截止日期或奖项，接口不自行制造这些规则。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/h5/occupational-submissions/meta` | 公开；`{enabled, accepted_work_types:["article","video"], topics:[...], accepts_other_topic:true, consent_text, limits:{...}}`，未开放仍返回 200 |
| POST | `/h5/occupational-submissions` | 公开 multipart 表单，无需 user token；字段见下；仅持久化成功后返回 `201 {id,receipt_no,status:"submitted",submitted_at}`，不返回电话、文件路径。同幂等键与同表单重试返回原回执（200） |
| GET | `/h5/occupational-submissions/receipt/{receipt_no}` | 公开回执查询（限流 30 次/时/IP，Redis 不可用 503）；仅返回 `{receipt_no,title,work_type,status,submitted_at,decided_at?,comment?}`，`comment` 仅 `rejected` 时给出驳回理由，不含任何联系方式或文件信息；回执号不存在返回 404 |
| GET | `/admin/occupational-submissions?status=&work_type=&q=&page=&page_size=` | 需 `occupational.read`；标准分页。列表项含 `id,receipt_no,work_type,title,topic,author_name,organization,phone_masked,status,submitted_at,word_count,duration_seconds` |
| GET | `/admin/occupational-submissions/{id}` | 需 `occupational.read`；在列表项之外给出完整 `phone,summary,original_filename,file_size,file_sha256,video_width,video_height,consent_review_contact,consent_text,consented_at,review_comment,reviewed_by,reviewed_at,client_submission_id`，另含 `reviews`（分级评审记录）与 `lock`（初审软锁信息），不返回私有存储路径 |
| GET | `/admin/occupational-submissions/{id}/file` | 需 `occupational.read`；附件鉴权下载，`Cache-Control: no-store`，无公开 `/uploads` 链接 |
| POST | `/admin/occupational-submissions/{id}/initial-review` | 需 `occupational.initial_review`；`{action:"claim"\|"release"\|"pass"\|"reject",comment?}` → 完整详情。`claim`：`submitted→initial_reviewing` 并上锁（同投稿被他人持有未过期锁返回 409）；`release`：仅持锁人，`initial_reviewing→submitted` 并清锁；`pass`：持锁人或锁超时后操作，`initial_reviewing→pending_final`；`reject`：必须填理由（422），`initial_reviewing→rejected`。pass/reject 落一条 `stage=initial` 评审记录并清锁 |
| POST | `/admin/occupational-submissions/{id}/final-review` | 需 `occupational.final_review`；`{action:"pass"\|"reject",comment?,score?}` → 完整详情；仅 `pending_final` 可操作（否则 409）；**初审记录的 reviewer 与当前终审人不得为同一账号（409）**；`reject` 必须填理由（422）；`score` 可选 0–100；pass/reject 落一条 `stage=final` 评审记录 |

投稿字段：`client_submission_id`（客户端在首次提交前生成的 UUID，重试保持不变）、`work_type`、`title`（1–100 字）、`topic`（1–100 字）、`author_name`（1–50 字）、`organization`（1–100 字）、`phone`（大陆手机号，或 `022-12345678` 形式的带区号座机，可加 `-123`/`#123`/`转123` 分机，最多 20 字符）、`summary`（10–1000 字）、`consent_review_contact=true`、`file`。同意文案由 `meta.consent_text` 返回，仅针对评审与联系。选题建议为“职业病危害防护、办公人群肌肉骨骼健康、职场心理健康疏导、新就业形态劳动者健康保护、职业健康法律科普、职场急救、工作场所健康生活方式”；原征集文本写“包含但不限于”，故 `topic` 不设封闭枚举。

图文仅收 `.docx`、最大 10 MiB；**不限字数**（2026-10-08 起按征集方要求放开，服务端仍解析正文统计字数并存档，供评审参考）；包内必须含 OOXML 必需关系部件 `_rels/.rels`（缺失即判定非有效 Word 文档拒收——Word 本地打开此类包会报损坏）。视频仅收 `.mp4`、最大 300 MiB；**不限时长**（ffprobe 仍校验文件为有效 MP4 及画面短边≥1080、长边≥1920，横竖屏均可），实际时长与分辨率存档供评审参考。若 `ffprobe` 缺失/超时，视频返回 `503`，不能依据扩展名放行。精确投稿 POST 在解析 multipart 前检查开关与声明的 `Content-Length`，关闭返回 503、声明的请求体大于 320 MiB 返回 413；无 `Content-Length` 的流式请求仍须由 Nginx 的 `client_max_body_size` 限制总请求体，路由内的逐块校验限制单文件大小，不能替代网关限制。私有目录由 `OCCUPATIONAL_SUBMISSION_DIR` 设置，禁止位于公开 `UPLOAD_DIR` 下；文件名随机，数据库与文件保存失败时不返回成功回执。

幂等键由数据库唯一约束保证：相同 `client_submission_id`、相同手机号、相同类型/标题/选题/姓名/单位/简介且文件 SHA256 相同，重试返回第一次的 ID、回执号、提交时间和原始 `submitted` 回执状态，不再保存新文件；同键不同手机号、字段或文件内容变化返回 409。客户端修改文件或表单以发起**新投稿**时须生成新 UUID。旧预览数据由下游迁移补 UUID，既有记录、回执号和文件不变。

公开限流：图文同 IP 每小时最多 30 次尝试、同联系电话每天最多 10 次尝试；视频分别为 6 次/小时、6 次/日。超限 `429`，Redis 不可用 `503` 并停止接收投稿。联系电话未经身份核验，因此回执只是提交成功证明，不能证明作者身份。后端按投稿当时的 `meta.consent_text` 保存同意文案快照。

**两级评审状态机（2026-10-08 起，方案 B）**：`submitted →(claim) initial_reviewing →(pass) pending_final →(final pass) accepted`；`initial_reviewing →(reject) rejected`、`pending_final →(final reject) rejected`；`initial_reviewing →(release) submitted`。含义：`submitted` 待初审、`initial_reviewing` 初审中（被领取并持有软锁）、`pending_final` 初审通过待终审、`accepted` 终审通过、`rejected` 未通过（任一级驳回）。旧单级状态 `reviewing` 由迁移改写为 `initial_reviewing`；既有 `accepted/rejected` 记录补写 `stage=final` 评审记录以保留历史留痕，回执号与文件不变。

初审软锁：`claim` 写入 `locked_by_admin_id + locked_at`，有效期 30 分钟；超时后任何持 `occupational.initial_review` 者可重新 `claim` 或直接 `pass/reject`（视为接管）。`release` 仅持锁人可用。`pass/reject` 完成后清锁。列表与详情返回锁持有者姓名与领取时间，供并行初审分工。

终审制衡：`final-review` 校验该投稿 `stage=initial` 评审记录的 `reviewer_id` 与当前操作者不同，同一账号既做初审又做终审返回 `409`；`score` 为可选 0–100 整数分（内容质量参考分，不代表评奖结果）。分级评审记录表 `occupational_review_records` 以 `(submission_id, stage)` 唯一，每投稿每阶段至多一条结论；`review_comment/reviewed_by/reviewed_at` 保留为最新一次结论的冗余展示字段。补正重提（驳回后修改再投）暂不提供，作者重新投稿需生成新的 `client_submission_id`。

权限与隐私（B 计划变化）：`editor` 自 2026-10-08 起参与形式初审，获得 `occupational.read`（含联系方式与附件下载）与 `occupational.initial_review`；`occupational.final_review` 仅 `admin`。旧权限点 `occupational.review` 移除。作者侧通过回执查询页获知分阶段进度与驳回理由，除此之外没有任何公开作品列表、奖项评定或三轮评审状态。后台详情读取、下载、初审、终审均记审计（`occupational.detail_read` / `occupational.file_download` / `occupational.initial_review` / `occupational.final_review`，审计不含作者电话或原始文件内容）。

## 七之三、杏临千行志愿者报名（公开填报域）

“杏临千行”守护职业健康公益行动的志愿者在线报名入口，独立于投稿域。`VOLUNTEER_APPLICATIONS_ENABLED=false` 为默认；开放前须完成隐私告知确认，未开启时 `meta.enabled=false`、提交返回 `503`。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/h5/volunteer-applications/meta` | 公开；`{enabled, tracks:[{key,label}], consent_text}` |
| POST | `/h5/volunteer-applications` | 公开 JSON：`{name, phone, city, birth_year?, occupation?, organization?, track, track_note?, offline_per_year, can_station, intro?, consent:true}` → `201 {id, receipt_no:"VOL-XXXX…", submitted_at}`；`consent≠true` 返回 422 |
| GET | `/admin/volunteer-applications?status=&q=&page=&page_size=` | 需 `volunteer.read`；分页项仅返回 `id/receipt_no/name/phone_masked/city/track/can_station/offline_per_year/status/submitted_at`，不返回明文电话、单位、简介、补充说明及同意文本；`q` 匹配姓名/电话后四位/城市/单位/回执号 |
| GET | `/admin/volunteer-applications/{id}` | 需 `volunteer.read`；完整详情（含明文电话），写审计 `volunteer.detail_read` |
| POST | `/admin/volunteer-applications/{id}/status` | 需 `volunteer.read`；`{status:"pending"\|"contacted"\|"enrolled"}` 三态自由切换，写审计 `volunteer.status` |

字段约束：`name` 1–50 字；`phone` 大陆手机号；`city` 1–50 字；`birth_year` 1900–2010（可空）；`occupation` ≤50、`organization` ≤100、`intro` ≤500（可空）；`track` ∈ `practice`(公益实践)/`publicity`(科普宣传)/`assist`(活动协助)/`promotion`(宣传支持)/`other`(其他，2026-10-16 增)；选 `other` 时 `track_note` 必填 1–50 字（自由填写的服务方向），其余取值忽略该字段；`offline_per_year` 1–99（次）；`can_station` 布尔（能否承担 3-5 天驻点）。性别字段已于 2026-10-16 从表单移除（数据库列保留兼容，不再采集）。同意文案快照随记录保存，仅用于报名与活动联系。

公开限流：同 IP 每小时 10 次、同手机号（HMAC 摘要键）每天 3 次；超限 429，Redis 不可用 503 拒绝。回执号仅作报名凭据，工作人员通过填报电话联系，不构成身份核验。后台仅 `admin` 角色具备 `volunteer.read`（editor 无权查看志愿者个人信息）。

## 八、上传 `/uploads`

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/uploads` | multipart `file`，需任一有效 token → `{"url": "/uploads/abc.jpg"}`（本地开发存 backend/uploads；生产可切换 OSS，服务层已抽象） |

静态访问：`GET /uploads/*` 由 FastAPI StaticFiles 挂载。

## 九、Redis Key 约定

| Key | TTL | 用途 |
|---|---|---|
| `sms:{phone}:code` | 300s | 验证码（值：code#尝试次数） |
| `sms:{phone}:cooldown` | 60s | 发送冷却 |
| `sms:{phone}:daily` | 至当日末 | 每日发送计数 |
| `h5:essays:hot:{contest_id|all}:{page}:{page_size}` | 60s | 热榜缓存（2026-09-23 起键含 page_size，防不同页大小互蹭缓存） |
| `occupational:submit:{work_type}:ip:{ip}` | 3600s | 公开职业健康投稿按文件类型的 IP 限流；Redis 不可用时拒绝 |
| `occupational:submit:{work_type}:phone:{HMAC摘要}` | 86400s | 同手机号按类型限流；键不含明文手机号 |
| `occupational:receipt:ip:{ip}` | 3600s | 公开回执查询 IP 限流（600 次/时，2026-10-08 起：投稿成功页自动轮询进度约 15 秒一次，终态即停）；Redis 不可用时 503 |
| `volunteer:apply:ip:{ip}` | 3600s | 志愿者报名 IP 限流（10 次/时）；Redis 不可用时拒绝 |
| `volunteer:apply:phone:{HMAC摘要}` | 86400s | 志愿者报名同手机号限流（3 次/日）；键不含明文手机号 |

---

# 第二部分 · 杏临汇小程序接口（/mp）

依据《全域健康（全科医学康复向）商业计划书》开发的 C 端小程序（杏临汇·全域健康管家）。当前人员、服务、排班和咨询仍含演示内容，不代表已接通真人或真实履约。认证复用 user scope（微信登录 `POST /wx/login` / 短信登录），分页与错误约定同第一部分；预发布及生产短信通道尚未接入时返回 503。

## 枚举

| 枚举 | 取值 |
|---|---|
| Scenario.key（九大场景） | `checkup`(体检慢病) `consult`(看病问诊) `medication`(合理用药) `rehab`(术后康复) `diet`(饮食调理) `emotion`(失眠情绪) `disable`(失能照护) `escort`(就医陪同) `mother-baby`(母婴·老年) |
| Service.service_type | `online`(线上服务) `offline`(线下上门) `third`(三方机构) |
| Appointment.status | `pending`(待确认) `confirmed`(已确认) `completed`(已完成) `cancelled`(已取消) |
| Consult.status | `open` / `closed` |
| Drug 分类标签 tags | `otc` `rx`(处方药) `essential`(国家基本药物) `insurance`(医保药品) |

## 对象结构

**Scenario** `{key, name, tagline, sort}`

**Service** `{id, scenario, scenario_name, service_type, name, desc, price, unit, duration_min, sales, provider_count}`
（price 单位为分；unit 如「次」「次·上门」；provider_count 为可匹配专业人员数）

**Provider（专业人员）** `{id, name, gender, title, dept, org, years, skills[], intro, avatar, rating, service_count, online}`
（title 职称：主任医师/副主任药师/主管护师/康复治疗师/心理咨询师/注册营养师/陪诊师等；当前 `online`、评分、服务次数和资质资料尚无真实在线/核验流程支撑，客户端不得用其作已上线或已认证声明）

**Drug（药品）** `{id, common_name, approval_no, specification, license_holder, source_name, source_url, source_checked_at, publication_status, form, manufacturer, tags[], indication, usage_note, contraindication, science, video_url, brand_name, favorited}`。官方目录试点仅录入可在原始页面核对的注册基础事实；未核实的医学内容、视频和标签为空。`publication_status` 为 `demo | pending | published | offline`，新增资料默认 `pending`。
（science 为科普正文；favorited 按当前登录用户。`brand_name` 因缺少品牌/广告审核状态，仅开发/测试环境返回，预发布/生产为 `null`）

**Product（商城商品）** `{id, category, name, desc, price, status}`，category：`food`(药食同源) `fsmp`(特医食品) `package`(健康服务包) `device`(器械耗材)；status 统一 `coming`(即将开放)

### 游客访问规则（2026-10-19）

无需登录可查看四个 Tab 首页、九大场景介绍、药品展厅首页、入驻介绍和协议。首页目录接口为 `/mp/home`、`/mp/scenarios`、`/mp/services`、`/mp/drugs`、`/mp/products`，只开放无检索条件的第一页；场景介绍可带 `scenario`。搜索、标签、分类、更多分页、服务/人员/药品详情和所有个人业务接口均要求 user access token；匿名访问返回 401。WebSocket 继续使用自身握手鉴权。

`POST /wx/login` 中 `phone_code` 必须为用户点击微信手机号授权按钮得到的一次性授权码，不接受客户端提交的原始手机号。后端通过 stable_token（Redis 缓存）与 getuserphonenumber 获取号码，校验 appid 水印。已有同手机号且未关联微信的账号可绑定；两个既有账号冲突、手机号变化、手机号已绑定其他微信返回 409，不自动合并；封禁账号返回 403。唯一约束冲突回滚；账号绑定行加锁。返回手机号继续掩码展示。缺少微信配置返回 503；微信授权码失败返回 400；上游异常返回 502。无密钥的 dev/test 环境保留既有 `dev-code` 自动化测试入口，真实环境不可用。

前端登录前显式勾选协议，用户点击后由微信展示手机号授权。取消授权保留游客身份。登录后只返回原页面，不重放预约、消息等操作。无手机号的旧本地登录态在启动时清除，需重新授权。

## 一）首页聚合

- `GET /mp/home`（无需登录）→
```json
{
  "scenarios": [{"key": "consult", "name": "看病问诊", "tagline": "服务流程演示，真人接诊尚未开放", "sort": 2}],
  "notices": [{"id": 1, "content": "「杏临千行」公益义诊本周六走进滨海新区"}],
  "stats": {"providers": 8, "services": 12, "users": 4021}
}
```

## 二）场景与服务

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/mp/scenarios` | 九大场景列表（按 sort） |
| GET | `/mp/services?scenario=&service_type=&q=&page=&page_size=` | 服务分页（on_shelf=true，sales 倒序） |
| GET | `/mp/services/{id}` | 服务详情 |

## 三）专业人员匹配

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/mp/providers?service_id=&scenario=&q=&page=&page_size=` | 人员分页（online 优先，rating 倒序；q 匹配姓名/科室/特长） |
| GET | `/mp/providers/{id}` | 详情 + `slots` + 最近 5 条 `reviews`；`slots` 仅开发/测试环境返回哈希生成的演示时段，预发布及生产返回 `[]`，真实排班尚未接入 |

## 四）预约（五步闭环·第四步）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/mp/appointments` | 需登录；仅开发/测试环境接受演示预约 `{service_id, provider_id, date, period, address, contact_name, contact_phone, remark?}` → `{id, status:"pending"}`；online 类服务 address 可空；date 限今起 7 天、period ∈ 上午/下午/晚间。预发布及生产因无真实排班返回 503 |
| GET | `/mp/appointments?status=&page=&page_size=` | 需登录：我的预约（created_at 倒序，含 service/provider 摘要） |
| POST | `/mp/appointments/{id}/cancel` | 需登录：仅 pending/confirmed 可取消，其余 409 |
| POST | `/mp/appointments/{id}/review` | 需登录：仅 completed 且未评价，`{rating:1-5, content?}` → 记录评价并更新 provider.rating（均值）与 service_count |

## 五）咚咚在线咨询（6.2.1）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/mp/consults` | 需登录；仅开发/测试环境允许 `{service_id, provider_id, content}` 创建或复用演示会话。预发布/生产因健康信息授权和真人服务尚未接入返回 503 |
| GET | `/mp/consults?page=&page_size=` | 需登录：会话列表（last_message_at 倒序，含 provider 摘要与 last_message 预览） |
| GET | `/mp/consults/{id}/messages?after_id=0` | 需登录：增量消息（id > after_id），轮询用；消息 `{id, sender:"user"\|"provider"\|"system", content, created_at}` |
| POST | `/mp/consults/{id}/messages` | 需登录；开发/测试环境 `{content}` → `user_message` 与 `system_message`（明确的演示自动回执）；预发布/生产在写入前返回 503，不提供真人回复或个体化用药建议 |

## 五之二）咚咚实时通道（WebSocket）

`WS /api/v1/mp/ws?token=<user_access_token>`（小程序 `wx.connectSocket`；token 为 user scope 的
access token，无效/过期或连接期间用户被封禁，服务端关闭连接，code **4401**）。REST 发消息接口保留（降级/兼容用），
两条通道共享同一会话数据与限流（发言 20 次/分钟）。

**帧格式（JSON 文本帧）**

客户端 → 服务端：
```json
{"type": "ping"}
{"type": "message", "consult_id": 1, "content": "血压 145/95，需要注意什么？"}
```

服务端 → 客户端：
```json
{"type": "connected", "user_id": 3}
{"type": "pong"}
{"type": "message", "consult_id": 1, "message": {"id": 41, "sender": "user", "content": "…", "created_at": "…"}}
{"type": "message", "consult_id": 1, "message": {"id": 42, "sender": "system", "content": "…", "created_at": "…"}}
{"type": "error", "detail": "消息内容不能为空"}
```

- `message` 帧：开发/测试环境先回显 user 自己的消息（确认已落库），随后推送系统自动回执；预发布/生产因健康信息授权和真人服务尚未接入，返回 `error` 帧且不写入。小程序只接收当前 `consult_id` 的消息；真人接入与跨进程多端推送尚未完成验收。
- `error` 帧不关闭连接（除鉴权失败 4401 外），业务校验错误（空内容/超长/会话已关闭/限流 429 语义）均以 error 帧返回。
- 心跳：客户端每 30s 发 `ping`，服务端回 `pong`；90s 无任何帧服务端可主动断开。

## 六）药品数字展厅（6.2.3）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/mp/drugs?q=&tag=&letter=&page=&page_size=` | 检索：q 匹配通用名；开发/测试中的演示条目也支持商品名，tag ∈ 枚举，letter 为拼音首字母 A-Z；按 common_name 排序。开发/测试仅返回 `demo`、`published`，预发布/生产仅返回 `published`；`pending`、`offline` 均不可见 |
| GET | `/mp/drugs/{id}` | 非公开状态返回 404；详情的 `feedback_mode:"demo"\|"closed"` 仅在开发/测试的 `demo` 条目为 `demo`，其余为 `closed`、`board:[]`。正式条目的 `brand_name` 为空，待品牌审核流程接入后再开放 |
| POST | `/mp/drugs/{id}/favorite` | 需登录：仅公开条目可切换收藏 → `{favorited}`，其余返回 404 |
| POST | `/mp/drugs/{id}/feedback` | 需登录；仅开发/测试的演示条目允许普通测试用户提交 20–500 字演示留言并立即显示；正式条目及预发布/生产因医师身份核验和内容审核未接入返回 503；非公开条目返回 404 |
| GET | `/mp/favorites?page=&page_size=` | 需登录：仅返回当前公开条目，下架或待审核的已收藏条目不会在公开列表出现 |

## 七）健康档案（五步闭环·第五步）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/mp/profile/capabilities` | 需登录；`{write_mode:"demo"\|"closed", message}`。开发/测试为演示模式，预发布/生产因缺少健康信息单独同意流程为关闭模式；前端请求失败时也不开放编辑 |
| GET | `/mp/profile` | 需登录：本人档案，未建档返回 `null`（200） |
| PUT | `/mp/profile` | 需登录；仅开发/测试环境接受演示建档/更新 `{name, gender:"male"\|"female", birth:"1990-01", height_cm?, weight_kg?, blood?, chronic_tags[], allergy?, surgery_history?, smoke?, drink?, sport_freq?}` → 档案。预发布/生产在写入前返回 503，既有本人档案仍可读取 |

## 八）健康商城（6.2.4 规划中 → 浏览骨架）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/mp/products?category=&page=&page_size=` | 商品分页（status=coming） |

## 九）小程序端合规文案（前端固定展示）

- 首页/我的页脚：本平台提供健康管理与信息服务，不替代诊疗；就医请前往正规医疗机构。
- 咨询页：当前仅提供系统自动回执，不构成诊疗或个体化用药建议；紧急情况请拨打 120 或就近就医。
- 人员详情：职称/单位/年限目前是未核验资料，固定展示待核验提示；只有后续取得实际核验结果才能展示资质徽标。

---

# 第三部分 · 健康域管理后台接口（/admin/mp）

管理杏临汇健康域（需 admin token，所有状态变更写入审计日志，action 前缀 `mp.`）。枚举与对象复用第二部分。

## 一）概览

- `GET /admin/mp/stats` →
```json
{
  "appointments": {"pending": 1, "confirmed": 2, "completed": 1, "cancelled": 0, "total": 4},
  "consults_open": 1, "providers_total": 8, "providers_online": 5,
  "services_total": 12, "drugs_total": 14, "products_total": 8
}
```

## 二）预约管理

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/admin/mp/appointments?status=&date=&q=&page=&page_size=` | 分页（created_at 倒序；q 匹配联系人/联系电话/人员姓名/服务名；date 为预约日 YYYY-MM-DD）；列表项含 user(掩码手机)/service/provider 摘要与联系人（contact_phone 完整展示，供线下服务联络） |
| POST | `/admin/mp/appointments/{id}/confirm` | pending → confirmed；非 pending 409 |
| POST | `/admin/mp/appointments/{id}/complete` | confirmed → completed；非 confirmed 409 |
| POST | `/admin/mp/appointments/{id}/cancel` | pending/confirmed → cancelled；其余 409 |

## 三）咨询管理

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/admin/mp/consults?status=&q=&page=&page_size=` | 分页（last_message_at 倒序；q 匹配用户昵称/人员姓名/服务名）；含 last_message 预览与消息数 |
| GET | `/admin/mp/consults/{id}` | 详情：会话 + user/provider/service 摘要 + messages（最近 100 条，正序） |
| POST | `/admin/mp/consults/{id}/close` | open → closed；非 open 409 |

## 四）专业人员管理

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/admin/mp/providers?q=&online=&page=&page_size=` | 分页（rating 倒序；q 匹配姓名/科室/单位） |
| PATCH | `/admin/mp/providers/{id}` | 部分更新 `{name?, gender?, title?, dept?, org?, years?, skills?, intro?, avatar?, online?}`（评分/服务次数只读，由评价体系维护） |

## 五）服务与商城管理

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/admin/mp/services?scenario=&service_type=&page=&page_size=` | 分页（含 provider_count） |
| PATCH | `/admin/mp/services/{id}` | 部分更新 `{name?, desc?, price?, unit?, duration_min?, on_shelf?}`（price 单位分） |
| GET | `/admin/mp/notices` | 公告全量（sort 升序） |
| POST | `/admin/mp/notices` | `{content, sort?}` → 201 |
| DELETE | `/admin/mp/notices/{id}` | 204；无投稿约束 |
| GET | `/admin/mp/products?category=&page=&page_size=` | 商品分页（v1 只读，status 恒为 coming） |

## 六）药品展厅管理

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/admin/mp/drugs?q=&tag=&publication_status=&page=&page_size=` | 分页（common_name 排序），可按 `demo | pending | published | offline` 筛选；返回来源和批准文号字段。仅后台可查看全部状态 |
| GET | `/admin/mp/drugs/{id}` | 详情（含来源、状态及既有留言记录最近 50 条）。目前无药品审核/发布写接口，不能把后台可见等同公开发布 |
| GET | `/admin/mp/drug-leaflets?status=&approval_no=&page=&page_size=` | 说明书原件元数据分页；批准文号精确匹配，状态可选 `pending | approved | published | withdrawn`；返回 `identity_check_method` 供区分机器文本匹配与未记录校验；仅管理员可读，不返回私有文件路径或下载地址 |
| GET | `/admin/mp/drug-leaflets/{id}` | 单份说明书元数据与来源校验值；未找到为 404。当前无原件下载、医学审核或发布接口 |
| DELETE | `/admin/mp/drugs/feedback/{feedback_id}` | 删除违规留言（204，审计） |

## 审计 action（第三部分新增）

`mp.appointment.confirm` · `mp.appointment.complete` · `mp.appointment.cancel` ·
`mp.consult.close` · `mp.provider.update` · `mp.service.update` ·
`mp.drug.feedback_delete` · `mp.notice.create` · `mp.notice.delete`

---

# 第四部分 · 全域健康管家服务包交易（工作一，2026-10-21 起）

依据《全域健康管家服务包_工作一_ZCode开发执行指导》。本部分覆盖：服务包商品与版本、订单与微信支付、积分账本与兑换、采购单位批次与权益认领、授权记录、采购单位查看端与运营后台。**不覆盖** 45 天服务履约、每日触达（服务号 / 企微 / 订阅消息 / 短信均未选型、未接入，订单与权益模型不含渠道字段）、设备、医师与电话外呼（工作二）。

金额一律整数「分」；时间存 UTC、输出 ISO-Z；权益有效期按 `Asia/Shanghai` 自然日计算。所有状态由服务端持久化；客户端不得以本地状态冒充已支付、已发放或已送达。既有 `GET /mp/products`（`status=coming` 骨架）保留给其他品类展示，**不改名冒充服务包**；可售服务包走下文 `/mp/packages`。

## 枚举

| 枚举 | 取值 |
|---|---|
| Package.status | `draft`(草稿，不对外) / `on_shelf`(上架可售) / `off_shelf`(已下架，详情 404) |
| Order.pay_type | `wxpay`(微信支付) / `points`(积分兑换) |
| Order.status | `pending_payment`(待支付) / `paid`(已支付) / `closed`(已关闭：超时或用户取消，未支付) / `refund_pending`(退款处理中) / `refund_failed`(退款失败，待人工) / `refunded`(已退款) |
| Payment.status | `created`(已预下单) / `success`(支付成功) / `failed`(支付失败或关闭) |
| PaymentEvent.event_type | `notify_accepted`(回调验签通过并入账) / `notify_replay`(重复回调，幂等忽略) / `notify_rejected`(验签 / 金额 / 未知单被拒) / `query_sync`(主动查单对账) |
| Refund.channel | `wxpay`(调用微信退款接口) / `manual`(线下人工退款，仅登记) |
| Refund.status | `pending`(已申请) / `processing`(微信受理中) / `success` / `failed` |
| PointsEntry.biz_type | `admin_adjust`(后台调整) / `redeem`(兑换扣减) / `refund_revert`(退款返还) |
| Batch.status | `preview`(已预览待确认) / `confirmed`(已确认并发放) / `cancelled`(已取消) |
| BatchRow.status | `valid` / `duplicate`(与同批次或同单位同版本已有权益重复) / `format_error`(非大陆手机号) / `over_limit`(超出采购数量) |
| Entitlement.source_type | `order`(本人购买 / 兑换) / `batch`(单位分配) |
| Entitlement.status | `pending`(待本人认领，仅 batch) / `active`(已生效) / `rejected`(本人拒绝) / `void`(后台作废) |
| Consent.purpose | `package_purchase`(服务包购买协议与售后规则) |

## 对象结构

**PackageSummary（C 端列表项）**
```json
{"id": 1, "name": "全域健康管家 · 45 天基础包", "subtitle": "……", "cover_url": null,
 "price_cents": 0, "points_cost": null, "validity_days": 45, "benefit_count": 4,
 "purchasable": {"wxpay": false, "points": false, "reasons": ["支付通道未开通"]}}
```
**PackageDetail** = PackageSummary + `{"version_id", "version_no", "benefits": [{"name", "desc"}], "service_desc", "terms", "terms_version"}`。`terms_version` 即当前版本号字符串（如 `"v3"`），下单时作为授权版本快照。

**Order**
```json
{"id": 10, "order_no": "SP20261021A1B2C3", "status": "pending_payment", "pay_type": "wxpay",
 "package": {"id": 1, "name": "…", "version_id": 3, "version_no": 3},
 "total_cents": 0, "points_cost": null, "expires_at": "…", "paid_at": null, "closed_at": null,
 "entitlement_id": null, "created_at": "…"}
```
详情另含 `refunds: [{id, refund_no, channel, status, amount_cents, reason, created_at, finished_at}]`。后台详情再含 `user: {id, nickname, phone(掩码)}`、`payments: [{id, prepay_id, transaction_id, status, amount_cents, created_at}]`、`events: [{id, event_type, result, detail, created_at}]`。

**Entitlement**
```json
{"id": 5, "source_type": "batch", "status": "pending", "package": {"id": 1, "name": "…", "version_no": 3},
 "benefits": [{"name": "…", "desc": "…"}], "validity_days": 45,
 "activated_at": null, "valid_until": null, "org_name": "××公司", "order_id": null, "created_at": "…"}
```
C 端只返回本人（`user_id` 匹配或 `phone` 与本人核验手机号匹配）的权益；不匹配一律 404，不返回任何字段。后台视图另含 `phone`（掩码）、`batch_id`、`void_reason`。

**PointsAccount** `{"balance": 0, "updated_at": "…"}`；**PointsEntry** `{"id", "delta", "balance_after", "biz_type", "order_id", "note", "created_at"}`。

**Consent** `{"id", "purpose", "version", "text", "granted_at", "revoked_at"}`。

## 一）C 端 · 服务包 `/mp`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/mp/commerce/capabilities` | 需登录；`{wxpay_enabled, points_enabled:true, wxpay_reason?}`。`wxpay_enabled` 仅当商户号、APIv3 密钥、证书序列号、私钥、平台证书与回调地址全部配置时为 true |
| GET | `/mp/packages?page=&page_size=` | 仅 `on_shelf` 且指向有效版本的服务包，按 `sort, id`；无检索条件的第一页游客可见（同 2026-10-19 游客规则），翻页需登录 |
| GET | `/mp/packages/{id}` | 需登录；非 `on_shelf` 返回 404。`purchasable.reasons` 列出不可购买原因（支付通道未开通 / 未设置积分价 / 积分不足等），客户端按此禁用按钮，不自行推断 |

## 二）C 端 · 订单 `/mp/orders`

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/mp/orders` | 需登录；`{client_order_id(UUID), package_id, pay_type, agree_terms:true}`。`agree_terms≠true` 422；商品不可售 409；`wxpay` 未配置 503；`points` 余额不足 409 `{"detail":"积分余额不足"}`。**幂等**：同用户同 `client_order_id` 且同 `package_id/pay_type` 返回原订单 200；参数不同 409。`points` 单在同一事务内：锁账户行 → 校验余额 → 写 `redeem` 流水 → 订单 `paid` → 发一份 `active` 权益 → 写 `package_purchase` 授权快照。`wxpay` 单创建为 `pending_payment`，`expires_at = now + 30 分钟` |
| GET | `/mp/orders?status=&page=&page_size=` | 需登录；仅本人，`created_at` 倒序。读取时已过期的 `pending_payment` 懒更新为 `closed` |
| GET | `/mp/orders/{id}` | 需登录；非本人 404 |
| POST | `/mp/orders/{id}/prepay` | 需登录；仅本人 `pending_payment` 且未过期（否则 409）；调用微信 JSAPI 下单，返回 `wx.requestPayment` 参数 `{timeStamp, nonceStr, package:"prepay_id=…", signType:"RSA", paySign}`；每次调用新建一条 `created` 支付记录；未配置 503，微信侧失败 502 |
| POST | `/mp/orders/{id}/close` | 需登录；仅本人 `pending_payment` → `closed`，其余 409；已向微信预下单的会尝试关单（失败不阻断） |

**支付成功的唯一依据**是服务端回调验签入账或主动查单对账结果；客户端 `wx.requestPayment` 成功回调只用于跳转结果页并轮询 `GET /mp/orders/{id}`，不得据此展示「已支付」。

## 三）C 端 · 积分 `/mp/points`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/mp/points` | 需登录；本人余额，无账户时 `{"balance":0}` |
| GET | `/mp/points/entries?page=&page_size=` | 需登录；本人流水，`id` 倒序 |

工作一不预置积分获取规则；余额只能由后台 `commerce.points.adjust` 调整或退款返还产生。

## 四）C 端 · 权益 `/mp/entitlements`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/mp/entitlements?status=&page=&page_size=` | 需登录；返回 `user_id` 为本人的权益 + `phone` 与本人核验手机号一致的 `pending`/`rejected` 待认领权益；本人无核验手机号时只返回前者 |
| GET | `/mp/entitlements/{id}` | 需登录；不属于本人（含手机号不匹配）404 |
| POST | `/mp/entitlements/{id}/claim` | 需登录；仅 `batch` 来源且 `phone` 与本人手机号一致、状态 `pending`/`rejected` → `active`，写入 `user_id`、`activated_at`、`valid_until`；重复请求返回同一权益 200；`active` 再领也返回 200 同结果；他人或不存在 404 |
| POST | `/mp/entitlements/{id}/reject` | 需登录；`pending` → `rejected`；已 `active` 返回 409；重复拒绝 200 |

认领以单条 `UPDATE … WHERE id=:id AND phone=:phone AND status IN ('pending','rejected')` 完成，并发只会有一次状态跃迁；`order` 来源权益随支付成功 / 兑换直接 `active`，无需认领。

## 五）C 端 · 授权记录 `/mp/consents`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/mp/consents?purpose=` | 需登录；本人授权记录（含已撤回），`granted_at` 倒序 |
| POST | `/mp/consents/{id}/revoke` | 需登录；写 `revoked_at`，重复撤回 200；撤回不影响既有订单与权益状态，仅记录意愿供后续处理 |

授权记录由下单时自动写入（`purpose=package_purchase`，`version=版本号`，`text=当时售后规则全文快照`）。

## 六）微信支付回调 `/pay/wxpay/notify`

`POST /api/v1/pay/wxpay/notify`，无需业务 token。处理顺序：

1. 未配置平台证书 / APIv3 密钥 → `503`，写 `notify_rejected` 事件（不含请求体明文）。
2. 校验 `Wechatpay-Timestamp`（±5 分钟）、`Wechatpay-Nonce`、`Wechatpay-Signature`（RSA-SHA256 over `timestamp\nnonce\nbody\n`）；失败 → `401 {"code":"FAIL","message":"签名校验失败"}` + `notify_rejected` 审计。
3. AES-256-GCM 解密 `resource`，取 `out_trade_no`、`transaction_id`、`trade_state`、`amount.total`。
4. 按 `out_trade_no` 锁订单：不存在 → `404` + 审计；金额 ≠ `total_cents` → `409` + 审计；订单已 `paid` 且 `transaction_id` 相同 → `200 {"code":"SUCCESS"}` + `notify_replay`（**不重复发权益**）。
5. `trade_state=SUCCESS` 且订单 `pending_payment`/`closed`（支付成功晚于本地超时关闭也入账）→ 条件更新为 `paid`，支付记录 `success`，发一份 `active` 权益（`UNIQUE(order_id)` 兜底），写 `notify_accepted`。
6. 其他 `trade_state`（`CLOSED/REVOKED/PAYERROR`）→ 支付记录 `failed`，订单不变，`200`。

响应体按微信约定 `{"code":"SUCCESS","message":"成功"}` / `{"code":"FAIL","message":"…"}`。

## 七）采购单位查看端 `/org`（scope=org）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/org/auth/login` | `{username, password}` → `{access_token, refresh_token, org:{id,name}, account:{id,username}}`；错误 401；账号停用 403 |
| POST | `/auth/refresh` | 复用，按 refresh token 的 `scope=org` 重签 |
| GET | `/org/me` | 当前单位与账号 |
| GET | `/org/batches?page=&page_size=` | 本单位批次汇总：`{id, batch_no, package:{id,name,version_no}, quantity, valid_count, issued_count, claimed_count, rejected_count, pending_count, status, confirmed_at}`。**不返回任何手机号、姓名或个人健康信息** |
| GET | `/org/batches/{id}` | 同上单条；非本单位 404 |

`org` token 对 `/mp/*`、`/admin/*` 一律 401；`user`/`admin` token 对 `/org/*` 一律 401。

## 八）后台 `/admin/mp/*`（commerce 权限点见第一部分矩阵）

### 服务包与版本（`commerce.product.manage`；GET 仅需 admin token）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/admin/mp/packages?status=&q=&page=&page_size=` | 分页，含 `current_version` 摘要与 `blockers`（当前不可上架原因） |
| POST | `/admin/mp/packages` | `{name(1–100), subtitle?(≤200), cover_url?, sort?}` → 201 `draft` |
| GET | `/admin/mp/packages/{id}` | 详情含全部 `versions` |
| PATCH | `/admin/mp/packages/{id}` | `{name?, subtitle?, cover_url?, sort?}`；不改状态与版本 |
| POST | `/admin/mp/packages/{id}/versions` | `{price_cents(>0), points_cost?(>0), validity_days(1–3650), benefits:[{name(1–50), desc?(≤200)}](1–20 项), service_desc(1–5000), terms(1–5000)}` → 201 新版本（`version_no` 自增）；**版本一经创建不可修改**，调价即建新版本 |
| POST | `/admin/mp/packages/{id}/shelf` | `{on_shelf:true, version_id}` → `on_shelf` 并设 `current_version_id`；版本不属于该商品 404；配置不完整 409 `{"detail":"…", "blockers":[…]}`。`{on_shelf:false}` → `off_shelf`（已售订单与权益不受影响） |

### 订单、对账与退款

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/admin/mp/orders?status=&pay_type=&q=&page=&page_size=` | `commerce.order.read`；q 匹配订单号 / 用户昵称 / 手机号后四位 |
| GET | `/admin/mp/orders/{id}` | `commerce.order.read`；含 payments / events / refunds / entitlement |
| POST | `/admin/mp/orders/{id}/sync` | `commerce.order.refund`；主动调用微信查单接口对账（回调丢失补偿），结果写 `query_sync` 事件；`SUCCESS` 且本地未入账则按回调同一逻辑入账；未配置 503 |
| POST | `/admin/mp/orders/{id}/refund` | `commerce.order.refund`；`{channel, amount_cents?, reason(1–200)}`；仅 `paid`/`refund_failed` 可发起；`amount_cents` 默认全额，累计不得超订单金额（409）。`wxpay`：调用退款接口 → `processing`，失败 → `failed` + 订单 `refund_failed`；`manual`：`pending`，由人工线下完成后登记结论。订单置 `refund_pending`；积分单仅允许 `manual`，结论成功时写 `refund_revert` 返还流水 |
| GET | `/admin/mp/refunds?status=&page=&page_size=` | `commerce.order.read`；人工处理队列（`pending`/`processing`/`failed` 优先） |
| POST | `/admin/mp/refunds/{id}/resolve` | `commerce.order.refund`；`{status:"success"\|"failed", note?(≤200)}`；`success` → 订单 `refunded`、`order` 来源权益置 `void`（原因「订单已退款」）、积分单返还；`failed` → 订单 `refund_failed`。记录处理人、理由、前后状态 |

### 积分

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/admin/mp/points/{user_id}?page=&page_size=` | `commerce.order.read`；余额 + 流水 |
| POST | `/admin/mp/points/adjust` | `commerce.points.adjust`；`{user_id, delta(≠0, |delta|≤1,000,000), note(1–200)}`；扣减导致负数 409；写 `admin_adjust` 流水 + 审计 |

### 采购单位与批次

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/admin/mp/orgs?q=&page=&page_size=` | `commerce.batch.manage` |
| POST | `/admin/mp/orgs` | `commerce.batch.manage`；`{name(1–100 唯一), contact_name?(≤50), contact_phone?(≤20), note?(≤500)}` → 201；重名 409 |
| POST | `/admin/mp/orgs/{id}/accounts` | `commerce.batch.manage`；`{username(4–32，字母数字下划线，唯一), password(≥10)}` → 201 单位查看账号；不返回口令 |
| POST | `/admin/mp/batches/preview` | `commerce.batch.manage`；`{org_id, package_id, version_id, quantity(1–10000), file_name(≤255), rows:[string](1–10000)}`。服务端逐行规范化（去空白、去 `+86`）后判定 `format_error` / `duplicate`（本批内重复、或该单位同版本已有非 `void` 权益）/ `over_limit`（有效行序号 > quantity）；创建 `preview` 批次并返回 `{batch, rows:[{row_no, phone(掩码), status, error_detail}], summary:{total, valid, duplicate, format_error, over_limit}}`；`file_sha256` 为服务端对规范化行内容计算，`uploaded_by` 为当前账号 |
| GET | `/admin/mp/batches?org_id=&status=&page=&page_size=` | `commerce.batch.manage` |
| GET | `/admin/mp/batches/{id}` | `commerce.batch.manage`；含行明细（手机号掩码）与汇总 |
| POST | `/admin/mp/batches/{id}/confirm` | `commerce.batch.confirm`；仅 `preview` → `confirmed`；为每个 `valid` 行创建一条 `pending` 权益（`UNIQUE(batch_row_id)`）；记录 `confirmed_by/confirmed_at`；重复确认 409；版本已不是该商品版本不影响（按批次绑定版本发放） |
| POST | `/admin/mp/batches/{id}/cancel` | `commerce.batch.confirm`；仅 `preview` → `cancelled` |

### 权益异常处理

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/admin/mp/entitlements?status=&source_type=&q=&page=&page_size=` | `commerce.batch.manage`；q 匹配手机号后四位 / 订单号 / 批次号 |
| POST | `/admin/mp/entitlements/{id}/void` | `commerce.batch.confirm`；`{reason(1–200)}`；`pending`/`active` → `void`；已 `void` 409 |
| POST | `/admin/mp/entitlements/{id}/reassign` | `commerce.batch.confirm`；`{phone, reason(1–200)}`；仅 `batch` 来源且 `pending`/`rejected`（错号修正）；新号格式错误 422；同单位同版本已有非 `void` 权益 409 |

## 配置项与阻断

| 配置 | 缺失时 |
|---|---|
| `WXPAY_MCHID` `WXPAY_API_V3_KEY`(32 位) `WXPAY_CERT_SERIAL_NO` `WXPAY_PRIVATE_KEY_PATH` `WXPAY_PLATFORM_CERT_PATH` `WXPAY_NOTIFY_URL` | `capabilities.wxpay_enabled=false`；`wxpay` 下单 / 预支付 / 查单 / 退款 503「支付通道未开通」；回调 503 并审计。私钥与平台证书只从文件读取，不入库不入日志 |
| 服务包未录入版本 / 价格 / 权益 / 售后 | 后台 `blockers` 列出；不能上架；C 端不可见 |

## 审计 action（第四部分新增）

后台：`commerce.package.create/update/version_create/shelf` · `commerce.order.sync/refund` · `commerce.refund.resolve` · `commerce.points.adjust` · `commerce.org.create/account_create` · `commerce.batch.preview/confirm/cancel` · `commerce.entitlement.void/reassign`。
系统（actor_type=system）：`commerce.payment.notify_accepted/notify_replay/notify_rejected`。
用户（actor_type=user）：`commerce.consent.revoke` · `commerce.entitlement.claim/reject` · `commerce.order.close`。
审计 `detail` 不含手机号全号、支付密钥、回调明文或证书。

---

# 第五部分 · 全域健康管家服务履约（工作二，2026-10-22 起）

覆盖：服务实例与激活门禁、45 天日历与履约任务、暂停 / 补时 / 到期 / 退款联动 / 续期、履约人员、服务时间线。
表名前缀 `steward_`。触达任务框架（切片 B）、设备（C）、医师工单（D）、CCC 电话（E）后续以第五之子部分追加。
**通道边界**：本部分不包含任何消息发送能力；无发送 API、无发送回执字段。每日触达通道未决策前，任何界面与接口不得出现「已发送 / 已送达」状态。

## 枚举

| 枚举 | 取值 |
|---|---|
| 服务实例状态 | `preparing`(准备中) `active`(服务中) `paused`(已暂停) `expired`(已到期未结案) `closed`(已结案) `terminated`(提前终止) |
| 准备检查项 check_type | `identity_phone`(本人手机号与微信身份核验) `entitlement_valid`(权益属于本人且有效) `delivery`(收货完成) `device_paired`(设备配对完成) `device_vendor_consent`(设备厂商数据授权) `platform_consent`(平台服务授权) `first_valid_data`(首条本人有效设备数据) `physician_plan`(医师初评并签发个人计划) `staffing_ready`(管家及替补排班就绪) |
| 准备检查状态 | `pending` `passed` `blocked`；`source`：`auto`(服务端实时判定) `manual`(运营带凭据录入) |
| 履约任务 task_type | `daily`(每日事项) `follow_up`(管家随访，第 7/14/21/28/35/42 天) `review_prep`(复评资料准备，第 43 天) `final_review`(期末复评，第 44 天) `closure`(结案，第 45 天) |
| 履约任务状态 | `pending` `done` `cancelled` |
| 任务责任人类型 | `user`(本人) `butler`(管家) `physician`(医师) `system` |
| 暂停原因 cause | `provider_interruption`(服务方核心中断) `physician_advised`(医师建议暂停) `user_request`(用户申请，不补时) `other` |
| 时间线事件 | `instance_created` `prep_check_passed` `prep_check_reset` `activated` `task_completed` `task_cancelled` `paused` `resumed` `compensated` `expired` `closed` `terminated` `staff_assigned` `renewed` |
| 履约人员角色 | `butler`(管家) `physician`(医师) |
| 授权 purpose 扩展 | 在 `package_purchase` 基础上新增 `steward_service`(平台服务授权) `steward_device_data`(设备厂商数据授权) `steward_phone_outreach`(本人电话外呼授权，切片 E 启用) `steward_contact_outreach`(联系人电话授权，切片 E 启用) |

## 对象结构

```jsonc
// 服务实例（C 端视图）
{
  "id": 1, "instance_no": "SL20261022A1B2C3D4",
  "package_name": "全域健康管家 · 45 天服务包", "version_no": 1,
  "benefits": [{"name": "...", "desc": "..."}],
  "service_days": 45,                 // 服务期天数（快照，激活时固化）
  "status": "preparing",
  "activated_at": null, "ends_at": null, "original_ends_at": null,
  "compensated_seconds": 0,           // 累计补时（秒）
  "current_day": null,                // active/paused 时按 Asia/Shanghai 业务日推算的第 N 天
  "butler": {"id": 1, "name": "张管家"}, "backup_butler": {...}, "physician": {...},
  "plan_version": null,               // 医师签发的个人计划版本（切片 D 前恒为 null）
  "renewed_from_instance_id": null,
  "termination_reason": null, "terminated_at": null, "closed_at": null
}

// 准备检查项
{ "check_type": "delivery", "title": "收货完成", "source": "manual",
  "status": "pending", "missing_reason": null,
  "evidence": null, "completed_by": null, "completed_at": null }

// 履约任务
{ "id": 10, "service_day": 7, "service_date": "2026-11-05",
  "task_type": "follow_up", "title": "第 7 天 · 管家随访",
  "assignee_type": "butler", "status": "pending",
  "due_at": "2026-11-05T15:59:59Z", "result": null, "completed_at": null,
  "shifted_days": 0 }                  // 补时平移天数留痕

// 暂停记录
{ "id": 1, "cause": "provider_interruption", "reason": "设备云接口故障",
  "started_at": "...Z", "ended_at": null, "duration_seconds": null,
  "compensated_seconds": null, "tasks_shifted_days": null }

// 时间线事件
{ "event_type": "activated", "actor_type": "user", "actor_name": "138****0001",
  "detail": {"ends_at": "..."}, "created_at": "...Z" }
```

## 一）C 端 · 我的服务 `/mp/steward`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/mp/steward/instances` | 本人服务实例列表（懒创建：本人每份 `active` 权益自动生成唯一 `preparing` 实例，`UNIQUE(entitlement_id)` 兜底幂等）。返回同时做懒迁移：到期 → `expired`、权益作废 → `terminated` |
| GET | `/mp/steward/instances/{id}` | 详情：实例 + 九项门禁（`auto` 项服务端实时判定并回写快照）+ 指派 + 当日任务摘要。跨用户 404 |
| POST | `/mp/steward/instances/{id}/activate` | 本人确认激活。九项门禁任一未过 → 409 `{"detail": {"message": "激活条件未全部满足", "missing": [check_type...]}}`；全过 → 生成实例编号、`activated_at`、`ends_at = activated_at + service_days×24h`、日历任务，条件 UPDATE 保证并发恰一次；重复请求返回原实例（幂等） |
| GET | `/mp/steward/instances/{id}/calendar?month=YYYY-MM` | 月视图：该月逐日 `{date, service_day, tasks:[...]}`；跨月正确（服务日 → 日期映射在补时平移后仍一致） |
| GET | `/mp/steward/instances/{id}/tasks?service_day=&date=` | 任务明细（二选一过滤；都空返回当日） |
| POST | `/mp/steward/instances/{id}/tasks/{task_id}/complete` | 本人完成本人任务（`assignee_type=user` 且归属校验）：`{result?(1–500)}` → `done`；重复完成 409；管家 / 医师任务仅后台登记 |
| GET | `/mp/steward/instances/{id}/timeline` | 本人实例时间线（只读） |
| POST | `/mp/steward/consents` | 授予履约授权：`{purpose: steward_service|steward_device_data}`；条款文本由服务端配置提供（`STEWARD_*_CONSENT_TEXT`），未配置 → 503「该授权条款尚未配置」，**不接受客户端自备文本**（防止绕过版本管理） |

授权撤回复用第四部分 `POST /mp/consents/{id}/revoke`；撤回后对应自动门禁项回到 `pending`，未激活的实例不能激活，已激活实例记录时间线并停止相应后续动作（设备数据切片 C 生效）。

## 二）后台 · 履约工作台 `/admin/steward`

权限点：`steward.read`（编辑含；列表 / 详情 / 时间线 / 日历只读）、`steward.manage`（仅 admin；门禁录入、指派、暂停 / 恢复、结案、任务登记、员工管理）。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/admin/steward/instances?status=&q=&page=&page_size=` | `steward.read`；q 匹配实例号 / 姓名 / 手机号后四位 |
| GET | `/admin/steward/instances/{id}` | `steward.read`；详情含门禁、任务汇总、暂停史、时间线 |
| GET | `/admin/steward/instances/{id}/calendar?month=` · `/timeline` | `steward.read` |
| POST | `/admin/steward/instances/{id}/prep-checks/{check_type}/pass` | `steward.manage`；仅 `manual` 项（auto 项 409）；`{evidence(1–500 必填), note?}` → `passed` + 审计 + 时间线 |
| POST | `/admin/steward/instances/{id}/prep-checks/{check_type}/reset` | `steward.manage`；回退人工项 `{reason(1–200)}` → `pending` + 审计（误录纠正，不删历史） |
| POST | `/admin/steward/instances/{id}/assign` | `steward.manage`；`{butler_id?, backup_butler_id?, physician_id?}`；角色不匹配 422；指派变化写审计 + 时间线 |
| POST | `/admin/steward/instances/{id}/pause` | `steward.manage`；`{cause, reason(1–200)}`；仅 `active` → `paused`；开启暂停记录 |
| POST | `/admin/steward/instances/{id}/resume` | `steward.manage`；`{note?}`；结束暂停记录并按规则结算：`provider_interruption` 且中断 >24h → 补足全部时长；`physician_advised` → 补足全部时长；`<24h` 中断 / `user_request` / `other` → 仅记录不补时。补时 = `ends_at` 顺延 + 未开始任务按天平移（`shifted_days` 留痕）；全部写时间线 |
| POST | `/admin/steward/instances/{id}/close` | `steward.manage`；`{closure_report(1–2000 必填)}`；要求 `final_review` 任务已完成且到期，否则 409；未结高优先级医师工单未转交时 409（切片 D 起检查）；→ `closed` |
| POST | `/admin/steward/tasks/{task_id}/result` | `steward.manage`；`{result(1–500 必填), done=true}` → `done`；重复 409；写审计 |
| GET | `/admin/steward/staff?role=&page=` | `steward.read`；履约人员名单 |
| POST | `/admin/steward/staff` | `steward.manage`；`{name(1–50), phone, role, org_name?(1–100), title?(1–50), license_no?(1–50 医师建议必填), note?}` |
| PATCH | `/admin/steward/staff/{id}` | `steward.manage`；改名 / 停用 `{status: active|inactive}` 等；停用不影响已指派实例（留痕） |

## 三）门禁判定规则（服务端唯一权威）

| check_type | source | 通过条件 |
|---|---|---|
| identity_phone | auto | `user.phone` 非空（微信手机号核验）且权益 `user_id` = 实例 `user_id` |
| entitlement_valid | auto | 权益 `status=active` 且（`valid_until` 为空或未过期） |
| delivery | manual | 运营凭据（物流单号 / 签收记录）；切片 C 前唯一来源 |
| device_paired | manual | 运营凭据；切片 C 接入后由设备绑定事实自动判定 |
| device_vendor_consent | auto | 存在未撤回的 `steward_device_data` 授权；条款未配置时显示「待配置」也判 `pending` |
| platform_consent | auto | 存在未撤回的 `steward_service` 授权 |
| first_valid_data | manual | 运营凭据（首条有效数据校验记录）；切片 C 后自动 |
| physician_plan | manual | 运营凭据（初评 + 签发计划编号）；切片 D 后由签发事实自动 |
| staffing_ready | auto | `butler_id`、`backup_butler_id`、`physician_id` 均已指派且人员 `active` |

`auto` 项在每次门禁读取（C 端详情 / 激活尝试 / 后台详情）时实时重判并回写快照；`manual` 项只能经后台接口带凭据置 `passed`。激活 = 九项全部 `passed` + 本人确认，二者缺一不可。

## 四）时间与补时口径

- 时间存储 UTC（带时区）；业务服务日按 `Asia/Shanghai`。
- 服务期 = 激活时刻起连续 `service_days×24h`（默认 45，配置 `STEWARD_SERVICE_DAYS`，激活时快照到实例）；与权益自然日有效期（第四部分 `valid_until`）是两个口径。
- `service_day N` 的业务日期 = 激活日的上海日期 + (N−1) 天，再叠加补时平移（按天取整向上，`shifted_days` 留痕）。
- 到期判定在实例读取路径懒触发（`active/paused` 且 `now ≥ ends_at` → `expired` + 时间线）；到期后不再生成新任务，未完成任务保留为历史不删除。

## 五）配置项与阻断

| 配置 | 缺失时 |
|---|---|
| `STEWARD_SERVICE_DAYS`(默认 45) | 使用默认；激活时快照 |
| `STEWARD_SERVICE_CONSENT_TEXT` | 平台服务授权不能授予（503「该授权条款尚未配置」）；对应门禁项恒 `pending`，无法激活 |
| `STEWARD_DEVICE_CONSENT_TEXT` | 同上（厂商数据授权） |
| `STEWARD_FOLLOWUP_DAYS`(默认 `7,14,21,28,35,42`) | 使用默认；随访节点按此生成 |

## 六）审计 action（第五部分新增）

后台：`steward.prep.pass/reset` · `steward.assign` · `steward.pause/resume/close` · `steward.task.result` · `steward.staff.create/update`。
用户（actor_type=user）：`steward.instance.activate` · `steward.task.complete` · `steward.consent.grant`。
系统（actor_type=system）：`steward.instance.create/expire/terminate`。
审计 detail 不含手机号全号与健康档案正文。
