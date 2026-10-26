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

| 权限点 | admin（管理员） | editor（编辑） | finance（财务） |
|---|---|---|---|
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
| `commerce.product.manage`（服务包与版本维护、上下架，2026-10-21 起） | ✓ | ✓ | ✗ |
| `commerce.order.read`（订单、支付事件、退款队列查询） | ✓ | ✓ | ✓ |
| `commerce.order.refund`（发起退款、查证退款、登记退款结论、主动对账） | ✓ | ✗ | ✓ |
| `commerce.points.adjust`（积分人工调整） | ✓ | ✗ | ✓ |
| `commerce.batch.manage`（采购单位维护、单位查看账号、名单预览与原件下载） | ✓ | ✓ | ✗ |
| `commerce.batch.confirm`（批次确认发放 / 取消、权益作废 / 改号） | ✓ | ✗ | ✗ |
| `commerce.sale.release`（开售放行 / 撤销，2026-10-22 起） | ✓ | ✗ | ✗ |

2026-10-22 起 `finance` 岗位（I1）加入矩阵：仅交易账务（订单查询 / 退款 / 积分调整），无内容域、商品维护、批次发放与放行权限；各列表只读能力仍随任一有效后台 token 提供。
| `steward.read`（服务履约实例、日历、时间线、人员名单查询，2026-10-22 起） | ✓ | ✓ |
| `steward.manage`（门禁录入、指派、暂停/恢复/结案、任务登记、人员管理） | ✓ | ✗ |
| `outreach.read`（触达待办、详情、尝试记录查询，2026-10-22 切片 B 起） | ✓ | ✓ |
| `outreach.manage`（触达转人工、领取/退回、提交结果、确认/驳回、取消、离岗交接） | ✓ | ✗ |
| `device.read`（设备厂商/绑定/同步记录/数据与质量查询，2026-10-22 切片 C 起） | ✓ | ✓ |
| `device.manage`（厂商注册、绑定/解绑、触发同步、游标重置、质量复核） | ✓ | ✗ |
| `phone.read`（电话话术/联系人/工单与尝试记录查询，2026-10-22 切片 E 起） | ✓ | ✓ |
| `phone.manage`（话术审核、联系人及授权核验、工单审核/接管/完成/停止、回调） | ✓ | ✗ |

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
| Refund.status | `pending`(已申请，人工渠道) / `processing`(微信受理中或网络超时结果未知，待查证) / `success` / `failed`(明确失败，可重新发起) |
| PointsEntry.biz_type | `admin_adjust`(后台调整) / `redeem`(兑换扣减) / `refund_revert`(退款返还) |
| Batch.status | `preview`(已预览待确认) / `confirmed`(已确认并发放) / `cancelled`(已取消) |
| BatchRow.status | `valid` / `duplicate`(与同批次或同单位同版本已有权益重复) / `format_error`(非大陆手机号) / `over_limit`(超出采购数量) |
| EntitlementEvent.event_type | `issued`(发放) / `claimed`(本人领取) / `rejected`(本人拒绝) / `reclaimed`(拒绝后再领取) / `reassigned`(后台改号) / `voided`(作废，含退款联动) —— 只追加，供工作二消费（2026-10-22 起） |
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
| GET | `/mp/packages/{id}` | 需登录；非 `on_shelf` 返回 404。`purchasable.reasons` 列出不可购买原因（未完成开售放行 / 支付通道未开通 / 未设置积分价等），客户端按此禁用按钮，不自行推断。放行规则（R8，2026-10-22）：存在未撤销且环境匹配（当前环境或 `all`）的放行记录才可购；微信与积分下单在服务端按同一记录判定 |

## 二）C 端 · 订单 `/mp/orders`

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/mp/orders` | 需登录；`{client_order_id(UUID), package_id, version_id, pay_type, agree_terms:true}`（2026-10-22 起 `version_id` 必填，为用户实际确认的版本）。`agree_terms≠true` 422；`version_id` 与当前上架版本不一致 409 `服务包已更新（当前为 vN），请重新确认后下单`；未完成开售放行 409（微信与积分一致）；商品不可售 409；`wxpay` 未配置 503；`points` 余额不足 409 `{"detail":"积分余额不足"}`。**幂等**：同用户同 `client_order_id` 且同 `package_id/pay_type` 返回原订单 200；参数不同 409。`points` 单在同一事务内：锁账户行 → 校验余额 → 写 `redeem` 流水 → 订单 `paid` → 发一份 `active` 权益 → 写 `package_purchase` 授权快照。`wxpay` 单创建为 `pending_payment`，`expires_at = now + 30 分钟` |
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
| GET | `/mp/entitlements/{id}/events` | 需登录（2026-10-22 起）；本人权益的操作历史（发放/领取/拒绝/再领取/改号/作废，`created_at` 倒序，≤50 条），跨用户 404 |
| POST | `/mp/entitlements/{id}/claim` | 需登录；仅 `batch` 来源且 `phone` 与本人手机号一致、状态 `pending`/`rejected` → `active`，写入 `user_id`、`activated_at`、`valid_until`；重复请求返回同一权益 200；`active` 再领也返回 200 同结果；他人或不存在 404 |
| POST | `/mp/entitlements/{id}/reject` | 需登录；`pending` → `rejected`；已 `active` 返回 409；重复拒绝 200 |

认领以单条 `UPDATE … WHERE id=:id AND phone=:phone AND status IN ('pending','rejected')` 完成，并发只会有一次状态跃迁；`order` 来源权益随支付成功 / 兑换直接 `active`，无需认领。

## 五）C 端 · 授权记录 `/mp/consents`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/mp/consents?purpose=` | 需登录；本人授权记录（含已撤回），`granted_at` 倒序 |
| POST | `/mp/consents/{id}/revoke` | 需登录；写 `revoked_at`，重复撤回 200；撤回不影响既有订单与权益状态，仅记录意愿供后续处理 |

授权记录由下单时自动写入（`purpose=package_purchase`，`version=版本号`，`text=当时售后规则全文快照`，`source=order_confirm`（2026-10-22 起记录授权来源）；下单必须携带用户确认的 `version_id`，与当前上架版本不一致返回 409 要求重新确认）。小程序「授权管理」页可查看全部记录并撤回。

## 六）微信支付回调 `/pay/wxpay/notify`

`POST /api/v1/pay/wxpay/notify`，无需业务 token。处理顺序（2026-10-22 起 R1/R2 收紧）：

1. 未配置平台证书 / APIv3 密钥 → `503`，写 `notify_rejected` 事件（不含请求体明文）。
2. 校验 `Wechatpay-Timestamp`（±5 分钟）、`Wechatpay-Nonce`、`Wechatpay-Signature`（RSA-SHA256 over `timestamp\nnonce\nbody\n`）；失败 → `401` + `notify_rejected` 审计。
3. 解析 JSON 并 AES-256-GCM 解密 `resource`；非 JSON、`resource` 非对象或认证标签失败 → `400`（不产生 500）。
4. **成功结果字段完整且匹配（R1）**：`trade_state=SUCCESS` 时 `amount.total` 必须为正整数分、`amount.currency=CNY`、`transaction_id` 非空且 ≤64 位 `[A-Za-z0-9-_]`；任一缺失/类型错误/币种不符 → `422` + `invalid_success_fields` 事件，订单与权益不变。金额 ≠ `total_cents` → `409`；`transaction_id` 已被其他订单使用 → `409` + `transaction_reused` 事件。
5. 按 `out_trade_no` 锁订单：不存在 → `404` + 审计；订单已 `paid` → `200 {"code":"SUCCESS"}` + `notify_replay`（**不重复发权益**）。
6. 入账（R2）：条件更新为 `paid` 后，支付记录**只落一条** `success` 并绑定该交易号——多轮预下单时最新一条 `created` 记 `success`、其余置 `failed(superseded)` 且不带交易号；无预下单记录则补插一条 `success`。随后发一份 `active` 权益（`UNIQUE(order_id)` 兜底，写 `issued` 权益事件）并记 `notify_accepted`。`pending_payment`/`closed`（支付成功晚于本地超时关闭）均可入账。
7. 其他 `trade_state`（`CLOSED/REVOKED/PAYERROR`）→ 最新预下单记录置 `failed`，订单不变，`200`。

主动查单 `POST /admin/mp/orders/{id}/sync` 走同一入账判定与字段校验；拒绝结果返回 422/409 而非入账。

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
| POST | `/admin/mp/packages/{id}/releases` | `commerce.sale.release`（仅 admin，2026-10-22 起）；`{version_id, env:dev\|test\|staging\|prod\|all, basis(1–500)}` → 201 放行记录（负责人、依据、时间）；同版本同环境已有生效记录 409。放行后该版本在匹配环境（当前环境或 all）可购，微信与积分一致判定 |
| POST | `/admin/mp/releases/{id}/revoke` | `commerce.sale.release`；`{reason(1–500)}` → 撤销放行（留痕 `revoked_at/by/原因`），该版本立即回到不可售；重复撤销 409 |

### 订单、对账与退款

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/admin/mp/orders?status=&pay_type=&q=&page=&page_size=` | `commerce.order.read`；q 匹配订单号 / 用户昵称 / 手机号后四位 |
| GET | `/admin/mp/orders/{id}` | `commerce.order.read`；含 payments / events / refunds / entitlement |
| POST | `/admin/mp/orders/{id}/sync` | `commerce.order.refund`；主动调用微信查单接口对账（回调丢失补偿），结果写 `query_sync` 事件；`SUCCESS` 且本地未入账则按回调同一逻辑入账；未配置 503 |
| POST | `/admin/mp/orders/{id}/refund` | `commerce.order.refund`；`{channel, amount_cents?, reason(1–200)}`；仅 `paid`/`refund_failed` 可发起。**仅允许全额退款**（R5，2026-10-22）：部分金额 409「部分退款规则尚未确认，当前仅支持全额退款…」，规则经业务确认后另行放开。退款意图先落库再调外部（R4）：先建 `pending` 退款单（稳定单号）并提交，再调微信；同订单存在 `pending`/`processing` 退款单时同参数幂等返回原单、异参数 409。`wxpay`：微信受理 → `processing`；**明确拒绝 → `failed` + 订单 `refund_failed`（可重新发起，R3）**；**网络超时/断连 → 保持 `processing`（结果未知，按单号查证，不当失败）**。`manual`：`pending`。订单置 `refund_pending`；积分单仅允许 `manual`，结论成功时写 `refund_revert` 返还流水 |
| GET | `/admin/mp/refunds?status=&page=&page_size=` | `commerce.order.read`；人工处理队列（`pending`/`processing`/`failed` 优先） |
| POST | `/admin/mp/refunds/{id}/query` | `commerce.order.refund`（2026-10-22 起）；按退款单号向微信查证并回写：微信 `SUCCESS` → `success`（自动联动订单/权益/积分，结论来源 `wxpay_query`）；`CLOSED/ABNORMAL` → `failed` + 订单 `refund_failed`；`PROCESSING` → 保持并记查证时间。查证超时 504、微信错误 502。用于超时/断连后的恢复，同一退款意图不重复退钱 |
| POST | `/admin/mp/refunds/{id}/resolve` | `commerce.order.refund`；`{status:"success"\|"failed", note?(≤200)}`。**线上（wxpay）退款单不得人工直接判成功**（409，须先 `query` 以微信结果为准）；`manual` 单成功须附线下凭据（note）。`success` → 订单 `refunded`、`order` 来源权益置 `void` 恰一次（`voided` 权益事件含退款单号）、积分单返还恰一次（`UNIQUE(biz_type, order_id)` 兜底）；`failed` → 订单 `refund_failed` 可重新发起。记录处理人、理由、结论来源与前后状态 |

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
| POST | `/admin/mp/batches/preview` | `commerce.batch.manage`；`{org_id, package_id, version_id, quantity(1–10000), file_name(≤255), rows:[string](1–10000)}`。服务端逐行规范化（去空白、去 `+86`）后判定 `format_error` / `duplicate`（本批内重复、或该单位同版本已有非 `void` 权益）/ `over_limit`（有效行序号 > quantity）；创建 `preview` 批次并返回 `{batch, rows, summary}`。无原件时 `file_sha256` = 规范化数据摘要；`uploaded_by` 为当前账号 |
| POST | `/admin/mp/batches/preview-file` | `commerce.batch.manage`（2026-10-22 起，I2 真实文件导入）；multipart `file` + `org_id/package_id/version_id/quantity`。仅收 UTF-8 文本 CSV/TXT（「手机号/phone」表头自动跳过，每行取首列，兼容逗号/制表符/分号），≤2 MiB、≤10000 行；超限 413/422、编码错误 422。**原件写入私有目录（含手机号，不入公开 uploads），`file_sha256` = 原文件字节摘要**，另存规范化摘要 `normalized_sha256`；解析与校验在服务端完成，确认只消费已预览数据 |
| GET | `/admin/mp/batches?org_id=&status=&page=&page_size=` | `commerce.batch.manage` |
| GET | `/admin/mp/batches/{id}` | `commerce.batch.manage`；含行明细（手机号掩码）与汇总 |
| GET | `/admin/mp/batches/{id}/file` | `commerce.batch.manage`；下载名单原件（鉴权 + 审计 `commerce.batch.file_download` + `Cache-Control: no-store`）；JSON 预览批次无原件 404 |
| POST | `/admin/mp/batches/{id}/confirm` | `commerce.batch.confirm`；仅 `preview` → `confirmed`；确认事务内复查跨批次重复（同单位×版本×手机号非 `void` 权益，R6 2026-10-22），数据库由 `uq_mp_entitlements_batch_phone`（PG 部分唯一索引 / SQLite 触发器）兜底；任一行冲突整批 409「与其他批次存在重复手机号…」，不静默少发。为每个 `valid` 行创建一条 `pending` 权益（`UNIQUE(batch_row_id)`，写 `issued` 权益事件）；记录 `confirmed_by/confirmed_at`；重复确认 409；版本已不是该商品版本不影响（按批次绑定版本发放） |
| POST | `/admin/mp/batches/{id}/cancel` | `commerce.batch.confirm`；仅 `preview` → `cancelled` |

### 权益异常处理

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/admin/mp/entitlements?status=&source_type=&q=&page=&page_size=` | `commerce.batch.manage`；q 匹配手机号后四位 / 订单号 / 批次号 |
| GET | `/admin/mp/entitlements/{id}/events` | `commerce.batch.manage`（2026-10-22 起）；该权益全部变更事件（含 actor 与 detail），供对账与工作二核对 |
| POST | `/admin/mp/entitlements/{id}/void` | `commerce.batch.confirm`；`{reason(1–200)}`；`pending`/`active` → `void`；已 `void` 409 |
| POST | `/admin/mp/entitlements/{id}/reassign` | `commerce.batch.confirm`；`{phone, reason(1–200)}`；仅 `batch` 来源且 `pending`/`rejected`（错号修正）；新号格式错误 422；同单位同版本已有非 `void` 权益 409 |

## 配置项与阻断

| 配置 | 缺失时 |
|---|---|
| `WXPAY_MCHID` `WXPAY_API_V3_KEY`(32 位) `WXPAY_CERT_SERIAL_NO` `WXPAY_PRIVATE_KEY_PATH` `WXPAY_PLATFORM_CERT_PATH` `WXPAY_NOTIFY_URL` | `capabilities.wxpay_enabled=false`；`wxpay` 下单 / 预支付 / 查单 / 退款 503「支付通道未开通」；回调 503 并审计。私钥与平台证书只从文件读取，不入库不入日志 |
| 服务包未录入版本 / 价格 / 权益 / 售后 | 后台 `blockers` 列出；不能上架；C 端不可见 |
| `COMMERCE_BATCH_DIR`（默认 `./private_commerce_batches`）、`COMMERCE_BATCH_FILE_MAX_BYTES`(2 MiB)、`COMMERCE_BATCH_MAX_ROWS`(10000) | 名单原件私存目录与导入上限；目录不得位于公开 `UPLOAD_DIR` 内（启动校验拒绝） |

## 审计 action（第四部分新增）

后台：`commerce.package.create/update/version_create/shelf` · `commerce.order.sync/refund` · `commerce.refund.resolve` · `commerce.points.adjust` · `commerce.org.create/account_create` · `commerce.batch.preview/confirm/cancel` · `commerce.entitlement.void/reassign`。
系统（actor_type=system）：`commerce.payment.notify_accepted/notify_replay/notify_rejected`。
用户（actor_type=user）：`commerce.consent.revoke` · `commerce.entitlement.claim/reject` · `commerce.order.close`。
审计 `detail` 不含手机号全号、支付密钥、回调明文或证书。

---

# 第五部分 · 全域健康管家服务履约（工作二，2026-10-22 起）

覆盖：服务实例与激活门禁、45 天日历与履约任务、暂停 / 补时 / 到期 / 退款联动 / 续期、履约人员、服务时间线、触达任务框架（切片 B，见第七节）。
表名前缀 `steward_`（触达域 `outreach_`、设备域 `device_`、电话域 `phone_`）。
**通道边界**：本部分不包含任何消息发送能力；无发送 API、无发送回执字段。每日触达通道未决策前，任何界面与接口不得出现「已发送 / 已送达」状态。

## 枚举

| 枚举 | 取值 |
|---|---|
| 服务实例状态 | `preparing`(准备中) `active`(服务中) `paused`(已暂停) `expired`(已到期未结案) `closed`(已结案) `terminated`(提前终止) |
| 准备检查项 check_type | `identity_phone`(本人手机号与微信身份核验) `entitlement_valid`(权益属于本人且有效) `delivery`(收货完成) `device_paired`(设备配对完成) `device_vendor_consent`(设备厂商数据授权) `platform_consent`(平台服务授权) `first_valid_data`(首条本人有效设备数据) `physician_plan`(医师初评并签发个人计划，按签发记录自动判定) `staffing_ready`(管家及替补排班就绪) |
| 激活放行 `pilot_release`（第十项门禁，非 prep_check 行） | 存在未撤销、在有效期内、覆盖该实例 `package_version_id` 与当前 `APP_ENV` 的试点放行记录；缺失 / 已撤销 / 环境或版本不匹配均判未过，激活 409 的 `missing` 中列 `pilot_release`。默认无记录 = 一律阻断 |
| 准备检查状态 | `pending` `passed` `blocked`；`source`：`auto`(服务端实时判定) `manual`(运营带凭据录入) |
| 履约任务 task_type | `daily`(每日事项) `follow_up`(管家随访，第 7/14/21/28/35/42 天) `review_prep`(复评资料准备，第 43 天) `final_review`(期末复评，第 44 天) `closure`(结案，第 45 天) |
| 履约任务状态 | `pending` `done` `cancelled` |
| 任务责任人类型 | `user`(本人) `butler`(管家) `physician`(医师) `system` |
| 暂停原因 cause | `provider_interruption`(服务方核心中断) `physician_advised`(医师建议暂停) `user_request`(用户申请，不补时) `other` |
| 时间线事件 | `instance_created` `prep_check_passed` `prep_check_reset` `activated` `task_completed` `task_cancelled` `paused` `resumed` `compensated` `expired` `closed` `terminated` `staff_assigned` `renewed` `consent_revoked` `care_plan_bound` |
| 履约人员角色 | `butler`(管家) `physician`(医师) |
| 授权 purpose 扩展 | 在 `package_purchase` 基础上新增 `steward_service`(平台服务授权) `steward_device_data`(设备厂商数据授权) `steward_phone_outreach`(本人电话外呼授权，切片 E 启用) `steward_contact_outreach`(联系人电话授权，切片 E 启用) |
| 条款版本状态 | `active`(当前版本，同 purpose 唯一) `retired`(历史版本，仅留存) |
| 放行环境 env | `dev` `test` `staging` `prod`（与 `APP_ENV` 一致） |

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
  "plan_version": null,               // 激活时固化的医师签发个人计划版本；preparing 期为 null
  "renewed_from_instance_id": null,   // 仅显式续期动作写入；普通再次购买不自动挂链
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
  "shifted_days": 0,                   // 补时平移天数留痕
  "actual_occurred_at": null,          // 补录时的实际发生时间
  "backfill_reason": null }            // 补录原因（历史任务补登记必填）

// 暂停记录
{ "id": 1, "cause": "provider_interruption", "reason": "设备云接口故障",
  "started_at": "...Z", "ended_at": null, "duration_seconds": null,
  "compensated_seconds": null, "tasks_shifted_days": null }

// 时间线事件
{ "event_type": "activated", "actor_type": "user", "actor_name": "138****0001",
  "detail": {"ends_at": "..."}, "created_at": "...Z" }

// 授权条款（当前版本；C 端展示全文供确认，后台发布新版本即成为当前版本）
{ "purpose": "steward_service", "version": "v1", "title": "平台服务授权条款",
  "text": "条款全文……", "effective_from": "...Z" }

// 试点放行（后台可审计的激活前置放行；evidence 为结构化凭据，非布尔勾选）
{ "id": 1, "package_id": 3, "package_version_id": 5, "env": "dev",
  "owner_name": "运营负责人", "evidence": [
    {"item": "contract_attachment", "source": "合同系统", "reference": "HT-2026-001", "note": "服务包合同附件"},
    {"item": "device_supply", "source": "采购单", "reference": "CG-2026-118"},
    {"item": "org_contract", "source": "机构合同", "reference": "JG-2026-07"},
    {"item": "physician_staffing", "source": "排班表", "reference": "PB-2026-W43"},
    {"item": "care_plan_rules", "source": "医务文档", "reference": "PLAN-SLEEP-V2"},
    {"item": "refund_interruption", "source": "售后方案", "reference": "SH-2026-09"},
    {"item": "duty_ops", "source": "值班表", "reference": "ZB-2026-10"},
    {"item": "outreach_channel", "source": "触达决策记录", "reference": "TD-2026-01"}],
  "effective_from": "...Z", "effective_until": null, "revoked_at": null }

// 医师签发个人计划（A1 最小结构；切片 D 扩展为完整版本化签发与适用范围）
{ "id": 1, "user_id": 1, "instance_id": null, "physician_id": 3,
  "org_name": "杏临汇签约服务机构", "version": "v1", "content": {"sleep_plan": "..."},
  "issued_at": "...Z", "valid_until": null, "revoked_at": null }

// 激活凭证（与激活同事务写入的不可变快照；preparing 草稿不产生凭证）
{ "instance_id": 1, "user_id": 1, "confirmed_at": "...Z",
  "gate_snapshot": {"identity_phone": "passed", "delivery": "passed", "...": "..."},
  "consent_versions": {"steward_service": "v1", "steward_device_data": "v1"},
  "plan_version": "v1",
  "butler_id": 1, "backup_butler_id": 2, "physician_id": 3,
  "service_started_at": "...Z", "service_ends_at": "...Z" }
```

## 一）C 端 · 我的服务 `/mp/steward`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/mp/steward/instances` | 本人服务实例列表（懒创建：本人每份 `active` 权益自动生成唯一 `preparing` 实例，`UNIQUE(entitlement_id)` 兜底幂等）。返回同时做懒迁移：到期 → `expired`、权益作废 → `terminated` |
| GET | `/mp/steward/instances/{id}` | 详情：实例 + 九项门禁（`auto` 项服务端实时判定并回写快照）+ 指派 + 当日任务摘要 + 激活放行状态 `pilot_release_ready`。跨用户 404 |
| POST | `/mp/steward/instances/{id}/activate` | 本人确认激活。九项门禁 + 试点放行（`pilot_release`）任一未过 → 409 `{"detail": {"message": "激活条件未全部满足", "missing": [check_type..., "pilot_release"]}}`；全过 → 生成实例编号、`activated_at`、`ends_at = activated_at + service_days×24h`、日历任务、激活凭证快照（门禁事实 / 条款版本 / 计划版本 / 责任人），条件 UPDATE 保证并发恰一次；重复请求返回原实例（幂等）。preparing 草稿与正式激活凭证是两个概念：只有本人确认且全部门禁通过才产生凭证并开始计时 |
| GET | `/mp/steward/instances/{id}/calendar?month=YYYY-MM` | 月视图：该月逐日 `{date, service_day, tasks:[...]}`；跨月正确（服务日 → 日期映射在补时平移后仍一致） |
| GET | `/mp/steward/instances/{id}/tasks?service_day=&date=` | 任务明细（二选一过滤；都空返回当日） |
| POST | `/mp/steward/instances/{id}/tasks/{task_id}/complete` | 本人完成本人任务（`assignee_type=user` 且归属校验）：`{result?(1–500), backfill_reason?(1–200)}` → `done`。门禁：实例须 `active`（`paused` 409「服务暂停中」、`expired/terminated/closed` 409）；任务 `service_date` 须 ≤ 当日（未来任务 409「不能提前完成未来任务」）；`service_date <` 当日的补录必填 `backfill_reason`（422）；重复完成 409；管家 / 医师任务仅后台登记 |
| GET | `/mp/steward/instances/{id}/timeline` | 本人实例时间线（只读） |
| GET | `/mp/steward/consent-terms` | 各 purpose 当前有效条款 `{purpose, version, title, text, effective_from}` 全文，供确认前展示；未发布的 purpose 不返回（前端显示待配置） |
| POST | `/mp/steward/consents` | 授予履约授权：`{purpose: steward_service|steward_device_data, version: v\d+}`。服务端比对当前条款版本：未发布条款 → 503「该授权条款尚未配置」；`version` 与当前版本不一致 → 409「条款版本已更新，请查看最新条款」（旧版本不能冒充新版本同意）；一致 → 保存条款文本快照。**不接受客户端自备文本**。同版本重复提交幂等返回原记录 |
| GET | `/mp/steward/consent-records` | 本人各 purpose 当前授权状态：`{purpose, granted: bool, version, granted_at, consent_id}`；`version` 为已授权版本，若低于当前条款版本则 `outdated: true`（须重新确认） |

授权撤回复用第四部分 `POST /mp/consents/{id}/revoke`；撤回后：对应自动门禁项回到 `pending`（未激活实例不能激活）、本人全部非终态实例写入时间线 `consent_revoked` + 审计 `steward.consent.revoke`，并为切片 B/C/E 预留「执行前重查授权」的统一判定函数。撤回授权不等于申请退款，二者互不触发。

## 二）后台 · 履约工作台 `/admin/steward`

权限点：`steward.read`（编辑含；列表 / 详情 / 时间线 / 日历只读）、`steward.manage`（仅 admin；门禁录入、指派、暂停 / 恢复、结案、任务登记、员工管理）。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/admin/steward/instances?status=&q=&page=&page_size=` | `steward.read`；q 匹配实例号 / 姓名 / 手机号后四位 |
| GET | `/admin/steward/instances/{id}` | `steward.read`；详情含门禁、任务汇总、暂停史、时间线 |
| GET | `/admin/steward/instances/{id}/calendar?month=` · `/timeline` | `steward.read` |
| POST | `/admin/steward/instances/{id}/prep-checks/{check_type}/pass` | `steward.manage`；仅 `manual` 项（auto 项 409）；`{evidence(1–500 必填), note?}` → `passed` + 审计 + 时间线 |
| POST | `/admin/steward/instances/{id}/prep-checks/{check_type}/reset` | `steward.manage`；回退人工项 `{reason(1–200)}` → `pending` + 审计（误录纠正，不删历史） |
| POST | `/admin/steward/instances/{id}/assign` | `steward.manage`；`{butler_id?, backup_butler_id?, physician_id?}`；角色不匹配 422；**负责管家与替补不能为同一人**（同时存在时 422）；指派变化写审计 + 时间线 |
| POST | `/admin/steward/instances/{id}/pause` | `steward.manage`；`{cause, reason(1–200)}`；仅 `active` → `paused`；开启暂停记录。暂停期间服务计时停止：`paused` 实例**不**按 `ends_at` 过期（见「时间与补时口径」） |
| POST | `/admin/steward/instances/{id}/resume` | `steward.manage`；`{note?}`；结束暂停记录并按规则结算补偿：`physician_advised` → 全额补时；`provider_interruption` 且中断**严格大于** 24h（≥ 24h+1s）→ 全额补时；恰 24h 及不足 24h / `user_request` / `other` → 仅记录不补时。补时 = `ends_at` 顺延精确秒数 + 待办任务按天平移（`shifted_days` 留痕）；平移范围 = **暂停开始日（上海日期）当天及之后**的全部 `pending` 任务（暂停期间滑入过去日期的任务一并顺延补交）；暂停开始日之前已到期的本人任务不平移（用户不记录不顺延，走补录）。全部写时间线 |
| POST | `/admin/steward/instances/{id}/close` | `steward.manage`；`{closure_report(1–2000 必填)}`；要求 `final_review` 任务已完成且到期，否则 409；存在未结高优先级医师工单时 409（切片 D 落地前查询钩子返回 0，接入后查真表）；→ `closed` |
| POST | `/admin/steward/tasks/{task_id}/result` | `steward.manage`；`{result(1–500 必填), actual_occurred_at?, backfill_reason?(1–200)}` → `done`；重复 409（结果更正不覆盖，D 域报告版本化时统一）；未来日期任务须同时提供 `actual_occurred_at` + `backfill_reason`（补录留痕），否则 422；写审计 |
| GET | `/admin/steward/staff?role=&page=` | `steward.read`；履约人员名单 |
| POST | `/admin/steward/staff` | `steward.manage`；`{name(1–50), phone, role, org_name?(1–100), title?(1–50), license_no?(1–50 医师建议必填), note?}` |
| PATCH | `/admin/steward/staff/{id}` | `steward.manage`；改名 / 停用 `{status: active|inactive}` 等；停用不影响已指派实例（留痕） |
| GET | `/admin/steward/consent-terms` | `steward.read`；条款版本史（含 retired） |
| POST | `/admin/steward/consent-terms` | `steward.manage`；发布新版本 `{purpose, version: v\d+, title(1–100), text(1–5000), note?}`：同 purpose 旧版本自动 `retired`，新版本 `active` 并即刻成为授权依据；已有旧版本授权在门禁判定中判「条款已更新，请重新确认」；发布写审计 `steward.terms.publish` |
| GET | `/admin/steward/pilot-releases?package_id=&page=` | `steward.read`；放行记录列表（含已撤销，凭据全文仅 manage 可见完整 reference） |
| POST | `/admin/steward/pilot-releases` | `steward.manage`；`{package_id, package_version_id, env, owner_name(1–50), evidence[8 项结构化凭据 {item, source(1–50), reference(1–100), note?}], effective_from?, effective_until?}`。八项凭据 `item` 缺一不可（422）：`contract_attachment` `device_supply` `org_contract` `physician_staffing` `care_plan_rules` `refund_interruption` `duty_ops` `outreach_channel`；凭据必须给出真实来源与编号，不接受「已完成」式描述。发布写审计 |
| POST | `/admin/steward/pilot-releases/{id}/revoke` | `steward.manage`；`{reason(1–200)}` → 立即失效，激活阻断；写审计 |
| GET | `/admin/steward/care-plans?user_id=&page=` | `steward.read`；签发计划列表（含撤销史） |
| POST | `/admin/steward/care-plans` | `steward.manage`；`{user_id, physician_id, version: v\d+, org_name(1–100), content(JSON), valid_until?}`。校验：医师 `active` 且角色 `physician`（422）；同用户同版本唯一（409）。签发后 `physician_plan` 门禁自动判定；若用户已有 preparing 实例且 `physician_id` 一致，写时间线 `care_plan_bound` |
| POST | `/admin/steward/care-plans/{id}/revoke` | `steward.manage`；`{reason(1–200)}` → 已撤销计划不再满足门禁；已激活实例的 `plan_version` 快照不回改（历史留痕）；写审计 |

## 三）门禁判定规则（服务端唯一权威）

| check_type | source | 通过条件 |
|---|---|---|
| identity_phone | auto | `user.phone` 非空**且 `user.wx_openid` 非空**（手机号须来自微信身份核验，非自填）且权益 `user_id` = 实例 `user_id` |
| entitlement_valid | auto | 权益 `status=active` 且（`valid_until` 为空或未过期） |
| delivery | manual | 运营凭据（物流单号 / 签收记录）；切片 C 前唯一来源 |
| device_paired | manual | 运营凭据；切片 C 接入后由设备绑定事实自动判定 |
| device_vendor_consent | auto | 当前条款已发布，且存在**未撤回且版本 = 当前条款版本**的 `steward_device_data` 授权；条款未发布时显示「待配置」判 `pending`；条款升版后旧版本授权判「条款已更新，请重新确认」 |
| platform_consent | auto | 同上（`steward_service`） |
| first_valid_data | manual | 运营凭据（首条有效数据校验记录）；切片 C 后自动 |
| physician_plan | auto | 存在未撤销、未过期的本人签发计划（`steward_care_plans`），且签发医师 = 实例 `physician_id`（未指派医师时判「未指派签约医师」） |
| staffing_ready | auto | `butler_id`、`backup_butler_id`、`physician_id` 均已指派、人员 `active`，且**负责管家 ≠ 替补管家** |
| pilot_release（第十项） | auto | 存在未撤销、在有效期、`package_version_id` 与 `env=APP_ENV` 均匹配的放行记录 |

`auto` 项在每次门禁读取（C 端详情 / 激活尝试 / 后台详情）时实时重判并回写快照；`manual` 项只能经后台接口带凭据置 `passed`。激活 = 十项全部 `passed` + 本人确认，二者缺一不可；激活成功在**同一事务**内写激活凭证（门禁快照 / 条款版本 / 计划版本 / 责任人 / 起止时间）。

## 四）时间与补时口径

- 时间存储 UTC（带时区）；业务服务日按 `Asia/Shanghai`。
- 服务期 = 激活时刻起连续 `service_days×24h`（默认 45，配置 `STEWARD_SERVICE_DAYS`，激活时快照到实例）+ Σ补偿秒数；与权益自然日有效期（第四部分 `valid_until`）是两个口径。
- `service_day N` 的业务日期 = 激活日的上海日期 + (N−1) 天，再叠加补时平移（按天取整向上，`shifted_days` 留痕）。
- **暂停期间计时停止**：`paused` 实例不按 `ends_at` 判到期（即使自然时间越过原 `ends_at`）；其出路只有恢复（结算补偿）或终止。恢复时不补时的场景（如用户申请暂停）随后仍按 `active` 到期规则正常过期。
- 补偿边界口径（固定）：医师建议暂停 → 全额补时；服务方核心中断**严格大于 24 小时**（≥ 24h+1s）→ 全额补时；**恰 24 小时（86400s）及不足 24 小时 → 不补时**，仅记录中断并按平移规则补交任务；用户申请 / 其他 → 不补时。
- 平移范围：暂停开始日（上海日期）当天及之后的全部 `pending` 任务顺延 `ceil(补偿秒/86400)` 天（`service_date`、`due_at`、`shifted_days` 一致更新，随访 / 复评 / 结案节点相对顺序不变）；暂停开始日之前已到期的本人任务不平移，走补录（`backfill_reason`）。
- `current_day`：`active` 时按当日所属服务日；`paused` 时冻结为**暂停开始日**所属服务日（计时已停止，页面文案一致）。
- 到期与作废判定：读路径懒触发（`active` 且 `now ≥ ends_at` → `expired`）之外，另有周期任务 `steward_lifecycle_sweep`（Celery beat 每 5 分钟）批量推进到期与权益作废终止——无人访问页面也能及时停止后续任务；到期后不再生成新任务，未完成任务保留为历史不删除。

## 五）配置项与阻断

| 配置 | 缺失时 |
|---|---|
| `STEWARD_SERVICE_DAYS`(默认 45) | 使用默认；激活时快照 |
| `STEWARD_FOLLOWUP_DAYS`(默认 `7,14,21,28,35,42`) | 使用默认；随访节点按此生成 |
| 条款表 `steward_consent_terms` 无 active 版本 | 对应授权不能授予（503「该授权条款尚未配置」）；门禁项恒 `pending`，无法激活。原 `STEWARD_*_CONSENT_TEXT` 配置项已由条款表取代（S1），环境变量残留不生效 |
| 放行表 `steward_pilot_releases` 无有效记录 | 激活一律 409（missing 含 `pilot_release`）——默认未放行是安全态 |

## 六）审计 action（第五部分新增）

后台：`steward.prep.pass/reset` · `steward.assign` · `steward.pause/resume/close` · `steward.task.result` · `steward.staff.create/update` · `steward.terms.publish` · `steward.release.create/revoke` · `steward.careplan.create/revoke` · `outreach.intent.route/assign/release/submit/confirm/reject/cancel` · `outreach.handover`。
用户（actor_type=user）：`steward.instance.activate` · `steward.task.complete` · `steward.consent.grant` · `steward.consent.revoke` · `outreach.subscription.set`。
系统（actor_type=system）：`steward.instance.create/expire/terminate`（读路径懒迁移与 sweep 共用同一组 action）。
审计 detail 不含手机号全号与健康档案正文。

## 七）触达任务框架（切片 B，2026-10-22 起，渠道无关）

表前缀 `outreach_`。**通道边界（最高优先级）**：本节不包含任何消息发送能力——无发送 API、无通道凭据、无发送/送达/阅读回执字段；每日触达通道未决策前，任何界面与接口不得出现「已发送 / 已送达 / 已读」。通道选定后另开接入切片，届时增加 `channel_receipt_ref`（真实回执引用）并区分「通道受理 / 发送成功 / 用户打开 / 事项办理」为独立真实事实。

身份绑定：意图直接以 `(instance_id, user_id)` 关联本人与权益实例，**禁止**凭昵称、头像、手机号相似性合并或推断触达对象。

### 枚举

| 枚举 | 取值 |
|---|---|
| 意图状态 | `pending_channel`(待通道配置) `pending_manual`(待人工处理) `pending_confirm`(待确认) `resolved`(事项已办理) `failed`(失败，尝试耗尽) `cancelled`(已取消) |
| 意图目的 purpose | `daily_reminder`(每日提醒) `follow_up`(随访跟进) |
| 停止原因 stop_reason | `instance_expired`(到期) `instance_terminated`(终止) `service_paused`(暂停，恢复后按顺延日期重新生成) `unsubscribed`(本人退订) `consent_revoked`(平台服务授权撤回) `manual_cancel`(人工取消) |
| 尝试动作 action | `generated`(系统生成) `routed_manual`(转人工) `assigned`(领取/指派) `released`(退回) `submitted`(提交联系结果) `confirmed`(确认办理) `rejected`(驳回重办) `cancelled`(取消) `stopped`(条件停止) `reassigned`(交接) |
| 联系方式 contact_method | `phone`(电话) `chat`(咚咚会话) `other`(其他) |

### 幂等与生成规则

- 业务幂等键 `UNIQUE(instance_id, purpose, service_date)`：重复调度、多 worker 并发生成只建一份意图（冲突读回原记录）。
- 生成来源：`outreach_sweep`（Celery beat 每 5 分钟，与人工触发共用同一服务函数）为 `active` 且可联系（未退订、平台服务授权未撤回）实例的当日（Asia/Shanghai）`daily` 与 `follow_up` 任务生成意图；`preparing/paused/expired/closed/terminated` 或不可联系时不生成。通道日去重与目标人群策略属将来通道配置边界，当前不实现。
- 意图复活：同一幂等键至多一条意图。被 `service_paused` / `unsubscribed` / `consent_revoked` 停止的意图，在条件恢复（实例恢复服务、重新订阅、重新授权）后由 sweep 复活为 `pending_channel` 并重挂当日任务（补时平移后日期可能对应不同服务日）；全部历史经尝试记录保留。`manual_cancel` 与实例终态不自动复活。
- 初始状态恒为 `pending_channel`（通道未决策的准确状态，绝不伪装「已提交通道」）。
- 内容版本：`daily_reminder` 固定 `daily_v1`；`follow_up` 取任务 `plan_version`（无则 `followup_v1`）。

### 停止规则（执行器副作用前重查）

`outreach_sweep` 及每次人工流转前重查：实例 `expired/terminated` → 取消；`paused` → 取消（恢复服务后同一意图复活并重挂当日任务）；本人退订 → 取消（重新订阅后复活）；`steward_service` 授权撤回 → 取消（重新授权后复活）。取消写尝试记录 `stopped/cancelled` + 原因，不删除历史。任务完成（`steward_tasks.done`）与触达状态**互不冒充**：任务完成不自动把触达标为已办理，触达办理也不替用户完成任务。

### 对象结构

```jsonc
// 触达意图
{ "id": 1, "instance_id": 1, "user_id": 1, "task_id": 10, "service_date": "2026-11-05",
  "purpose": "daily_reminder", "content_version": "daily_v1",
  "status": "pending_channel",            // 见枚举；pending_channel=通道未决策
  "contactable": true, "contact_note": null,  // 可联系判断快照（退订/撤回时 false+原因）
  "assignee_staff_id": 2,                 // 责任管家（StewardStaff）；未指派为 null
  "attempts": 0,                          // 人工驳回重办次数（上限配置）
  "stop_reason": null, "channel_receipt_ref": null,  // 回执引用：通道接入前恒 null
  "result": null, "created_at": "...Z", "updated_at": "...Z" }

// 尝试记录（只追加）
{ "id": 1, "intent_id": 1, "action": "submitted", "actor_type": "admin",
  "actor_admin_id": 1, "actor_name": "管理员", "contact_method": "phone",
  "outcome": "已电话提醒完成今日记录", "detail": {...}, "created_at": "...Z" }

// 退订状态（user 级，默认订阅）
{ "user_id": 1, "subscribed": true, "updated_at": "...Z" }
```

### 后台 · 触达工作台 `/admin/outreach`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/admin/outreach/intents?status=&instance_id=&assignee_staff_id=&service_date=&page=&page_size=` | `outreach.read`；意图列表（含用户号掩码、状态、责任人、原因） |
| GET | `/admin/outreach/intents/{id}` | `outreach.read`；详情含全部尝试记录（谁、何时、做了什么、结果） |
| POST | `/admin/outreach/intents/{id}/route-manual` | `outreach.manage`；`{assignee_staff_id, note?}`：仅 `pending_channel` → `pending_manual`；管家须在职；写尝试 `routed_manual` |
| POST | `/admin/outreach/intents/{id}/assign` | `outreach.manage`；`{assignee_staff_id, note?}`：`pending_manual` 内改派/领取（改派留 `reassigned` 记录） |
| POST | `/admin/outreach/intents/{id}/release` | `outreach.manage`；`{reason}`：退回未指派池（`assignee_staff_id=null`，仍 `pending_manual`），写 `released` |
| POST | `/admin/outreach/intents/{id}/submit` | `outreach.manage`；`{contact_method, outcome(1–500), note?}`：`pending_manual`（已指派）→ `pending_confirm`；记录方式、处理人、结果——**人工联系不得冒充通道成功**，回执引用恒空；执行前重查实例/退订/授权，失效则 409 |
| POST | `/admin/outreach/intents/{id}/confirm` | `outreach.manage`；`{note?}`：`pending_confirm` → `resolved`（事项已办理） |
| POST | `/admin/outreach/intents/{id}/reject` | `outreach.manage`；`{reason}`：`pending_confirm` → `pending_manual`（attempts+1）；attempts ≥ `STEWARD_OUTREACH_MAX_ATTEMPTS`(默认 3) → `failed`（失败队列终态，需人工另建意图或线下处理） |
| POST | `/admin/outreach/intents/{id}/cancel` | `outreach.manage`；`{reason(1–200)}`：人工取消（`manual_cancel`） |
| POST | `/admin/outreach/handover` | `outreach.manage`；`{from_staff_id, to_staff_id, reason(1–200)}`：批量转交该管家全部未终态意图（`reassigned` 留痕）；`to` 须在职管家 |
| GET | `/admin/outreach/summary?date=` | `outreach.read`；按状态汇总当日意图数量（工作台概览） |

流转均为条件 UPDATE（rowcount 校验），并发重复提交只生效一次；越状态操作 409。

**人员离岗联动**：`PATCH /admin/steward/staff/{id}` 将管家置 `inactive` 时，若其名下仍有未终态触达意图 → 409「须先完成触达交接」（防任务丢失与旧账号继续处理）；处理路径 = 先 `handover` 再停用。已停用人员不能被指派（422）。

### C 端 · 小程序可见状态 `/mp/steward/outreach`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/mp/steward/outreach/status` | `{subscribed, channel_decided: false, status_note}`：通道未决策时如实显示「提醒通道待配置，服务期事项由管家人工跟进」，不承诺每日送达 |
| POST | `/mp/steward/outreach/subscription` | `{subscribed: false|true}`：退订立即取消本人全部未终态意图（`unsubscribed`）；重新订阅后恢复生成（当日未完结意图复活为待通道配置）。退订不影响服务履约本身，也不等于申请退款。写审计 `outreach.subscription.set` |

### 配置

| 配置 | 说明 |
|---|---|
| `STEWARD_OUTREACH_MAX_ATTEMPTS`(默认 3) | 人工驳回重办次数上限，达到后意图 `failed` |

## 八）设备云数据（切片 C，2026-10-22 起，标准域 + 适配接口）

表前缀 `device_`。**外部边界**：设备型号与厂商尚未书面确定——本节交付标准数据域、质量策略、适配器接口与隔离测试；**无真实厂商 API 调用**（生产环境适配器注册表为空，触发同步返回「厂商适配待配置」并记录，不伪造数据）。真实接入须另备：已确认型号、书面厂商范围、测试凭据与脱敏回执。手环数据不得单独作为疾病诊断或紧急判断。

### 枚举

| 枚举 | 取值 |
|---|---|
| 绑定状态 | `active`(绑定中) `unbound`(已解绑；换绑=解绑后新建行，历史保留) |
| 游标状态 | `idle`(正常) `blocked`(连续失败达上限，须人工重置) |
| 数据质量 quality | `valid`(有效) `invalid_value`(数值超出生理合理范围) `late`(晚到，超过补传窗口) `reviewed`(晚到经复核解除) |
| 同步结果 status | `success` `partial`(部分失败仍推进游标) `failed`(异常未推进) `blocked`(授权缺失/解绑/无适配器等前置不满足) |
| 指标 metric_type | `sleep_duration`(分钟, 0–1440) `sleep_score`(0–100) `heart_rate`(次/分, 20–250) `steps`(0–200000) |

### 幂等、去重与修订

- 去重键 `dedup_key = vendor_record_id:metric_type`，`UNIQUE(binding_id, dedup_key, revision)`：同一厂商记录重复拉取跳过（重复）；`vendor_updated_at` 前进则追加 `revision+1` 新行（合法修订），趋势取每键最大 revision。
- 游标按绑定持久化（`device_sync_cursors`）：断点恢复=重跑同游标仅得重复跳过；连续失败 ≥ `STEWARD_DEVICE_MAX_SYNC_FAILURES`(默认 5) → `blocked`，人工重置后方可继续。
- 同步前置检查（每次执行前重查）：绑定 `active`、厂商 `active`、`steward_device_data` 授权未撤回且版本=当前条款、适配器已注册。任一不满足 → `blocked` + 原因，不拉取。

### 质量策略（可测试、留痕、不静默改正）

| 判定 | 规则 | 去向 |
|---|---|---|
| 错人 wrong_user | 厂商记录的账号标识 ≠ 绑定归属用户 | **拒绝入库**（不落健康数据），同步日志计数留痕 |
| 无效 invalid_value | 数值超出该指标合理范围 | 入库标 `invalid_value`，不入趋势/规则 |
| 晚到 late | 接收时 `business_date` 距今 > `STEWARD_DEVICE_LATE_DAYS`(默认 7 天) | 入库标 `late`，不入趋势；后台复核（`review`）附原因后转 `reviewed` 入趋势 |
| 缺失 | 某日无数据 | **不用 0 填补**：趋势如实返回缺口与最近同步时间 |

趋势与任何规则判断只使用 `valid/reviewed` 且最大 revision 的数据；授权撤回、解绑后旧数据保留（归属与来源可追溯）但不再新增。

### 对象结构

```jsonc
// 绑定（换绑=新行；跨用户重复绑定被唯一约束拒绝）
{ "id": 1, "user_id": 1, "vendor_id": 1, "device_model": "WRIST-X1(待定型号占位)",
  "device_sn": "SN-001", "status": "active", "bound_at": "...Z",
  "unbound_at": null, "unbind_reason": null }

// 同步游标
{ "binding_id": 1, "cursor": "rec-100", "status": "idle",
  "last_success_at": "...Z", "failure_count": 0 }

// 数据点（append-only；revision 区分修订）
{ "id": 1, "binding_id": 1, "user_id": 1, "metric_type": "sleep_duration",
  "value": 412, "unit": "min", "business_date": "2026-11-01",
  "tz_offset_minutes": 480, "vendor_record_id": "rec-100",
  "vendor_updated_at": "...Z", "received_at": "...Z",
  "quality": "valid", "revision": 1, "review_note": null }
```

### 后台 `/admin/devices`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/admin/devices/vendors` | `device.read`；厂商注册表（含停用） |
| POST | `/admin/devices/vendors` | `device.manage`；`{code(2–32), name(1–100), note?}`；同 code 409。注册≠接通：真实适配器上线前同步一律待配置 |
| GET | `/admin/devices/bindings?user_id=&status=&page=` | `device.read`；绑定列表（含解绑史、手机号掩码） |
| POST | `/admin/devices/bindings` | `device.manage`；`{user_id, vendor_id, device_model(1–50), device_sn(1–64)}`；同一设备已有 active 绑定（不论归属）→ 409（跨用户归属校验）；同用户同设备重复 active → 409 |
| POST | `/admin/devices/bindings/{id}/unbind` | `device.manage`；`{reason(1–200)}` → `unbound`；旧数据保留 |
| POST | `/admin/devices/bindings/{id}/sync` | `device.manage`；触发一次同步（幂等）；返回同步日志摘要 |
| POST | `/admin/devices/bindings/{id}/cursor/reset` | `device.manage`；`{reason}`：blocked 游标人工重置（失败清零、游标回退可重拉补传） |
| GET | `/admin/devices/sync-logs?binding_id=&status=&page=` | `device.read`；同步与失败列表（各计数、错误、时长） |
| GET | `/admin/devices/metrics?binding_id=&quality=&business_date=&page=` | `device.read`；数据点与质量筛选 |
| POST | `/admin/devices/metrics/{id}/review` | `device.manage`；`{reason(1–200)}`：`late` → `reviewed`（解除排除并留痕）；非 late 409 |

### C 端 `/mp/steward/devices`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/mp/steward/devices` | 本人绑定（状态/型号/最近同步）+ 数据授权状态 + 趋势摘要提示 |
| GET | `/mp/steward/devices/trend?metric=&days=`(默认 14) | 仅 `valid/reviewed` 最大 revision，按业务日聚合；**无数据日不填补**，返回 `{dates:[], series:[], gaps:[], last_synced_at, quality_note}`；附质量与延迟提示文案 |
| POST | `/mp/steward/devices/bindings/{id}/unbind` | 本人解绑 `{reason(1–200)}`；解绑即停止后续同步，不影响已发生数据归属 |

### 门禁联动（切片 C 起）

| check_type | source（本批起） | 通过条件 |
|---|---|---|
| device_paired | **auto**（原 manual） | 本人存在 `active` 设备绑定 |
| first_valid_data | **auto**（原 manual） | 本人 active 绑定下存在 ≥1 条 `valid/reviewed` 数据 |

既有 manual 行由迁移转 `auto/pending`（原人工凭据不自动视作设备事实，如实重判）。厂商适配器未上线时运营可经绑定 + 隔离导入通道（测试/开发环境合成适配器）达成门禁；生产真实数据接入仍待外部条件。

### 配置

| 配置 | 说明 |
|---|---|
| `STEWARD_DEVICE_LATE_DAYS`(默认 7) | 晚到判定窗口（天） |
| `STEWARD_DEVICE_MAX_SYNC_FAILURES`(默认 5) | 连续失败上限，达限 blocked |

### 审计 action（本节新增）

后台：`device.vendor.create` · `device.binding.create/unbind` · `device.sync.trigger` · `device.cursor.reset` · `device.metric.review`。
系统（actor_type=system）：`device.sync.run`（每次同步结果摘要，detail 不含健康数值明细）。
## 九）专业任务与异常工单（切片 D，2026-10-22 起）

**医疗边界（最高优先级）**：无签发资料时只做格式及数据质量检查，**不运行任何医学判断**；本节接口不做自动诊断、治疗建议、风险分级，不自行决定电话对象。医学阈值只能来自签发计划内容并关联签发人与机构规则版本。设备指标不单独触发疾病或紧急判断。紧急提示为固定经确认文案：「如遇紧急症状，请立即拨打 120 或前往当地急救机构」；平台不提供夜间连续监护。**签发身份一致**：工单结果与报告签发须由与指派医师绑定的后台账号录入，通用运营账号不能冒充医师意见。

### 枚举

| 枚举 | 取值 |
|---|---|
| 工单类型 order_type | `initial_assessment`(初评) `data_quality`(数据质量任务) `physician_review`(医师复核) `final_review`(期末复评) `exception`(异常工单) |
| 工单优先级 | `normal` `high`（high 未结且未转交 → 阻断结案） |
| 工单状态 | `pending`(待处理) `in_progress`(处理中) `done`(已完成) `cancelled`(已取消) |
| 工单事件 action | `created` `started` `transferred` `escalated` `completed` `cancelled` |
| 报告类型 | `closure`(结案报告) `correction`(结案后更正) |

### 对象与规则

- `steward_care_rules`（机构规则版本）：`(org_name, version)` 唯一；发布新版本即转旧版 `retired`；签发计划可引用 `rules_version`（引用时校验该机构该版本存在且 active，撤销后不能被新计划引用）。
- 签发计划（第五部分对象）新增可选 `rules_version`：内容中的任何阈值展示均随计划快照固化；计划 / 规则升级不改写已引用快照（任务与工单在创建时固化 `care_plan_version` / `rules_version`）。
- `steward_staff_admin_links`：后台账号 ↔ 履约人员（管家 / 医师）受控绑定；`UNIQUE(staff_id, admin_id)`。工单结果录入与报告签发校验当前账号绑定 = 工单指派医师，否则 403。
- `steward_professional_orders`（专业工单）：`(instance_id, order_type, task_id?)`；`physician_id` 指派；`due_at` 超时依据；`transferred_at/transfer_reason` 转交事实（接收人、时间、依据）；创建时若类型属医学判断类（`physician_review` / `final_review`）必须存在该用户 + 该医师的有效签发计划（无签发阻断 422）。数据质量类不需要签发。
- `steward_professional_events`（只追加）：全部流转留痕。
- `steward_service_reports`（版本化报告）：`(instance_id, version)` 唯一；结案时自动生成 v1（含服务数据摘要与高优先级转交依据）；结案后更正形成新版本（`supersedes_id` 链），不覆盖。
- `steward_followup_records`（结构化随访）：第 7/14/21/28/35/42 天 `follow_up` 任务的结构化结果（摘要 / 数据质量备注 / 下一步安排），`UNIQUE(task_id)`。

### 超时升级与结案联动

- `professional_sweep`（beat 每 15 分钟）：`pending/in_progress` 且 `due_at` 已过且未升级的工单 → 记 `escalated_at` + 事件 + 审计（升级为待人工处置标记，不自动外呼）。
- `close_instance`（第 5.x 节）的高优先级检查接真表：存在 `priority=high` 且状态 `pending/in_progress` 且 `transferred_at IS NULL` 的工单 → 409；明确转交（有接收人 / 时间 / 依据）不阻断。结案成功同事务写报告 v1。

### 后台端点（`steward.read` 查询 / `steward.manage` 操作；结果录入另校验账号绑定）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST | `/admin/steward/care-rules` | 规则版本列表 / 发布新版本 `{org_name(1–100), version: v\d+, content(JSON), note?}`（同机构旧版自动 retired） |
| GET/POST | `/admin/steward/staff-links`；DELETE | `/admin/steward/staff-links/{id}` | 账号↔人员绑定管理 `{staff_id, admin_id}`（绑定不改变权限点，仅建立录入身份） |
| GET/POST | `/admin/steward/professional/orders` | 工单列表（按实例/状态/优先级/类型筛选）/ 创建 `{instance_id, order_type, title(1–100), detail?, priority, physician_id, task_id?, due_at?}`；医学判断类无有效签发计划 → 422 |
| GET | `/admin/steward/professional/orders/{id}` | 详情含全部事件 |
| POST | `/admin/steward/professional/orders/{id}/start` · `/{id}/cancel` | `pending→in_progress`；`{reason}` 取消 |
| POST | `/admin/steward/professional/orders/{id}/transfer` | `{to_physician_id, reason(1–200)}`：接收人须在职医师；留事件与转交事实 |
| POST | `/admin/steward/professional/orders/{id}/complete` | `{result(1–1000), result_meta?}`：当前账号须绑定指派医师（403 冒充防护）→ `done` |
| GET/POST | `/admin/steward/reports`；`/admin/steward/reports/{id}` | 报告列表（按实例）/ 结案后更正 `{instance_id, title, content(1–5000), basis?}` → 新版本（须绑定实例签约医师） |
| GET/POST | `/admin/steward/followups` | 结构化随访列表（按实例）/ 录制 `{task_id, summary(1–500), data_quality_note?, next_action?}`（任务须为本人实例 `follow_up` 且未录过） |

### C 端 `/mp/steward/instances/{id}/professional`

本人实例的专业进展聚合：工单（类型 / 状态 / 标题 / 截止，不含内部备注）、报告（版本 / 标题 / 签发时间 / 正文）、随访记录（第 N 天 / 摘要 / 下一步）；固定急救文案随响应返回（`emergency_note`），不承诺夜间连续监护。

### 审计 action（本节新增）

`steward.carerules.publish` · `steward.stafflink.create/delete` · `steward.proforder.create/start/transfer/complete/cancel`（系统：`steward.proforder.escalate`）· `steward.report.correct` · `steward.followup.record`。

## 十）CCC 电话通路（切片 E，2026-10-22 起，隔离状态机 + 适配接口）

**外部边界**：CCC 号码、业务准入、合同与话术审核流程均未落实——本节交付隔离的电话工单状态机、分立授权、话术版本、回调验证与适配接口；**真实外呼保持关闭**（生产适配器注册表为空：工单审核后进入 `pending_config` 待配置并如实展示，不拨打、不伪造接通）。mock / 合成适配器结果**不计真实电话接通**，仅用于状态机与异常路径测试。

**AI 边界**：AI（若将来接入自动外呼）仅执行经审核话术的**有限告知**——不解释疾病与药量、不承诺救援、不自行选择联系对象；接通先核实对象，错号不披露任何服务或健康信息；紧急情况按固定文案引导拨打 120。必须支持人工接管与结果审计。

### 授权（三类分立，缺一拦截）

| 授权 | purpose | 说明 |
|---|---|---|
| 本人电话外呼 | `steward_phone_outreach` | 本人授权平台为其服务目的外呼其电话（条款版本化管理，同 S1） |
| 指定联系人外呼 | `steward_contact_outreach` | 平台可外呼指定联系人的**框架授权**；每次外呼还须该联系人自身的授权状态为 `granted`（记录于联系人档案，含来源与时间）——**本人同意不等于联系人已同意** |
| 通话录音 | `steward_call_recording` | 单独授权；引用 `requires_recording` 话术的工单在录音授权缺失时拦截 |

撤回任一授权 → 本人全部未终态电话工单按原因停止（不自动退款）；退订（触达域）不影响电话工单。

### 枚举

| 枚举 | 取值 |
|---|---|
| 工单状态 | `pending_review`(待审核) `queued`(待拨打) `pending_config`(待配置·CCC 未接) `in_progress`(执行中) `no_answer`(未接通) `busy`(座席忙) `wrong_number`(错号) `refused`(拒绝) `transferred_human`(转人工) `completed`(完成) `failed`(失败·重试耗尽) `cancelled`(已停止) |
| 对象类型 | `self`(本人) `contact`(指定联系人) |
| 联系人授权状态 | `none` `granted` `refused` `revoked` |
| 尝试结果 outcome | `no_answer` `busy` `wrong_number` `refused` `needs_human` `completed` `blocked` |

重试口径：`no_answer` / `busy` 有限重试（`STEWARD_PHONE_MAX_ATTEMPTS` 默认 3，间隔 `STEWARD_PHONE_RETRY_DELAY_MINUTES` 默认 30），耗尽 → `failed`；`refused` / `wrong_number` **立即终态不重试**（拒绝停止；错号不披露信息）。

### 对象结构

```jsonc
// 话术版本（审核通过才可被引用）
{ "id": 1, "code": "followup_reminder", "version": "v1", "title": "随访提醒话术",
  "content": "……有限告知内容……", "target_type": "self", "requires_recording": false,
  "status": "active", "reviewed_by_admin_id": 1, "review_note": "合规审核通过" }

// 指定联系人（身份关系须核验；授权状态独立于本人）
{ "id": 1, "user_id": 1, "name": "王某某", "relation": "子女", "phone": "139****0002",
  "identity_note": "核验来源与依据", "consent_status": "granted",
  "consent_source": "首次核身通话中口头确认（录音引用）", "consent_at": "...Z" }

// 电话工单（话术版本与对象快照随单固化）
{ "id": 1, "instance_id": 1, "purpose": "followup_reminder", "target_type": "self",
  "contact_id": null, "script_code": "followup_reminder", "script_version": "v1",
  "requires_recording": false, "status": "pending_review", "attempts": 0,
  "next_retry_at": null, "result": null, "receipt_ref": null,  // 真实回执引用；mock 恒 null
  "taken_over_by_admin_id": null, "taken_over_at": null }

// 尝试记录（只追加；真实回执经验签后落 receipt_ref，mock 结果标记 simulated）
{ "id": 1, "order_id": 1, "outcome": "no_answer", "actor": "system",
  "simulated": false, "receipt_ref": null, "detail": {...}, "started_at": "...Z" }
```

### 后台 `/admin/phone`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST | `/admin/phone/scripts` | 话术列表 / 发布 `{code(2–32), version: v\d+, title, content(1–5000), target_type, requires_recording}`——创建为 `draft`，**审核通过才 active** |
| POST | `/admin/phone/scripts/{id}/review` | `phone.manage`；`{approve: bool, note(1–200)}` → `active` / `retired`；同 code 旧 active 自动 retired |
| GET/POST | `/admin/phone/contacts` | 联系人列表 / 登记 `{user_id, name(1–50), relation(1–50), phone, identity_note(1–200)}`（身份关系来源必填） |
| POST | `/admin/phone/contacts/{id}/consent` | `phone.manage`；`{status: granted|refused|revoked, source(1–200), note?}`——联系人自身授权状态（来源必填）；`refused/revoked` 即停止其未终态工单 |
| GET/POST | `/admin/phone/orders` | 工单列表（按实例/状态/对象）/ 创建 `{instance_id, purpose(1–32), target_type, contact_id?, script_id}`——校验：话术 `active`；`self` 须本人电话授权（版本匹配）；`contact` 须联系人 `granted`；创建即 `pending_review` |
| GET | `/admin/phone/orders/{id}` | 详情含尝试记录（回执与签名密钥不回显） |
| POST | `/admin/phone/orders/{id}/review` | `{approve: bool, note}` → `queued`；无适配器时置 `pending_config`（配置接通后由 sweep 转回 `queued`） |
| POST | `/admin/phone/orders/{id}/takeover` | 转人工：`{note}` → `transferred_human` 并记录接管人 |
| POST | `/admin/phone/orders/{id}/complete` | 人工结果：`{result(1–500)}`（须已接管）→ `completed` |
| POST | `/admin/phone/orders/{id}/cancel` | `{reason}` 停止（授权撤回 / 业务取消） |
| POST | `/admin/phone/callback` | CCC 回调：`X-Phone-Signature`（HMAC-SHA256，密钥 `STEWARD_PHONE_CCC_SECRET`；未配置密钥 → 403 拒收）`{order_id, outcome, receipt_ref?, detail?}`——验签、幂等（重复回调返回现状不重复应用）、超时 / 未知单先查原状态 |

### 调度（`phone_call_sweep`，beat 每 5 分钟）

`queued` 且到达重试时间且未超限的工单：执行前重查（实例在服务、三类授权有效、话术 active、适配器已注册）→ 任一失效按原因 `cancelled` / 保持 `pending_config`；通过则 `in_progress` → 适配器拨打 → 结果落尝试记录并按口径流转（见枚举重试口径）。多 worker 由条件 UPDATE 保证单飞（同一时刻至多一个进行中尝试）。

### C 端 `/mp/steward/phone/status`

本人电话通路状态：三类授权现状（含 outdated）、联系人列表（号码掩码、授权状态）、近期电话工单状态（不含通话内容与回执）。授权 / 撤回复用 `GET /mp/steward/consent-terms` + `POST /mp/steward/consents` + `POST /mp/consents/{id}/revoke`（条款发布后自动出现在授权管理卡片）。

### 配置

| 配置 | 说明 |
|---|---|
| `STEWARD_PHONE_MAX_ATTEMPTS`(默认 3) | 未接通 / 忙线重试上限 |
| `STEWARD_PHONE_RETRY_DELAY_MINUTES`(默认 30) | 重试间隔 |
| `STEWARD_PHONE_CCC_SECRET`(默认空) | 回调验签密钥；为空即拒收回调（真实 CCC 未接入的安全态） |

### 审计 action（本节新增）

`phone.script.publish/review` · `phone.contact.create/consent` · `phone.order.create/review/takeover/complete/cancel`（系统：`phone.order.dispatch` · `phone.callback`）。detail 不含完整号码与通话内容。
