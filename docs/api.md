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
| POST | `/wx/login` | 小程序：`{code}` → `{token, refresh_token, user, is_new}`；后端调 code2session，openid 自动注册 |
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
