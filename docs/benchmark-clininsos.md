# 对标 ClinInsOS · 当前系统问题清单（2026-09-21）

对标对象：`/Users/huo/Desktop/ClinInsOS`（杏临千行/杏临荟 · 职业健康调研与义诊数字化运营系统）
当前系统：jianshu（杏临汇小程序 + 征文 H5 + 征文管理后台 + FastAPI 后端）

> ClinInsOS 规模基线：双 FastAPI 服务（backend 25.7k 行 + survey-service 3.5k 行）、19 领域模块、45 表、
> 24 个 Alembic 迁移、4 小程序 + 2 管理端 + 问卷 H5、审计/幂等/发件箱、三层测试（8.7k 行 pytest +
> miniprogram-automator E2E + pixelmatch 视觉回归）、生产部署于阿里云 ECS。

## P0 · 上线阻断项

| # | 问题 | 现状 | ClinInsOS 基线 |
|---|---|---|---|
| 1 | ~~不是 git 仓库~~ **已解决（2026-09-22）** | git 初始化 + 基线提交，后续改动逐步提交 | git 管理 + ADR 决策记录 |
| 2 | ~~无数据库迁移链~~ **已解决（2026-09-22）** | Alembic 全量初始迁移（22 表，与模型对账通过）；`make seed` 先走迁移链 | 24 个迁移版本，可回滚 |
| 3 | ~~健康域无管理后台~~ **已解决（2026-09-22）** | /admin/mp 全套接口（预约状态机/咨询/人员/服务/公告/药品留言治理 + 概览统计）；admin-web 新增健康服务 5 页（健康预约/在线咨询/专业人员/药品展厅/服务与商城），状态变更全部接审计 | admin-web 15 个页面域覆盖全业务 |
| 4 | ~~在线咨询是假 IM~~ **已解决（2026-09-22，传输层）** | WebSocket 自研实时通道 `WS /api/v1/mp/ws`（token 鉴权 4401 / ping-pong 心跳 / error 帧不断连 / 与 REST 同限流）；小程序 chat 页 WS 优先 + 轮询自动降级（通道状态点可视化）；应答源仍是演示自动回复（build_provider_reply 单点替换即可接真人） | 真实业务闭环（报名→签到→分诊→叫号→报告→随访） |
| 5 | ~~无审计日志~~ **已解决（2026-09-22）** | audit_logs 只追加表；登录/活动/审核/导出/封禁 11 个命令端点同事务写入；`GET /admin/audit-logs` + admin-web 审计日志页（筛选/分页）；契约 api.md 五之二 | AuditLog（只追加）+ OutboxEvent + 幂等键，11 处命令端点接审计 |

## P1 · 对标差距（上线前应补）

| # | 问题 | 现状 | ClinInsOS 基线 |
|---|---|---|---|
| 6 | ~~权限模型过简~~ **已解决（2026-09-22）** | 7 个权限点（essay.*/contest.*/user.manage/audit.read/mp.manage）× admin/editor 矩阵，`require_permission()` 接入全部 19 个后台写接口（403 中文文案）；admin-web 同步 UI 显隐（lib/permissions.ts）；契约含权限矩阵 | ~23 个权限点 + `require_permission()` + AdminRole/StaffRole 表 |
| 7 | ~~无幂等与全局限流~~ **已解决（2026-09-22）** | 用户级 Redis 固定窗口限流（投稿 3/min、点赞 30/min、预约 10/min、咨询发言 20/min，429 + 故障放行）+ 预约业务幂等（同人同日同时段 409） | IdempotencyKey 表 + survey-service 5 次/60s 提交限流 |
| 8 | 生产部署链路为零 | deploy.md 只是文档 | Supervisor + Nginx 反代 + RDS SSL + miniprogram-ci 小程序上传 |
| 9 | 测试厚度差两个数量级 | 11 个 pytest 用例，前端零测试 | 31 个测试文件 8.7k 行 + 小程序 automator E2E + 视觉回归 |
| 10 | ~~无请求级可观测性~~ **已解决（2026-09-22）** | RequestLoggingMiddleware：X-Request-ID 透传 + 单行 JSON 访问日志（method/path/status/duration_ms/request_id/client）+ ContextVar 供业务日志挂载 | request-id ContextVar + 结构化日志 + /health |
| 11 | 双系统用户体系割裂 | jianshu 自己的 users 表；ClinInsOS 有独立 participants + 4 套小程序 AppID | 计划书 P32 要求「统一后台、数据交互」——需统一登录/互通 |

## P2 · 演进项

12. **问卷/评估服务化**：健康档案评估、慢病问卷出现时应拆独立服务（对齐 survey-service 的模板版本化 + 评分引擎 + Celery）。
13. **健康域运营看板**：预约趋势、咨询量、场景热度、药品检索排行（ClinInsOS 有 statistics 页与数据大屏）。
14. **监控告警**：两家都缺（无 Prometheus/Sentry）——医疗系统上线前必须有进程/接口/磁盘告警。
15. **软著与合规材料流程**：ClinInsOS 已沉淀 SoftwareCopyright-Skill 自动生成材料；本系统尚无。
16. **设计→实现视觉契约**：ClinInsOS 用 ui-comparison（49 张对比图）+ visual-contracts 保证还原度。
17. **敏感数据分级**：健康档案属敏感数据，需要字段级策略（掩码规则、导出脱敏默认开）。

## 不要照抄的「反面教材」（ClinInsOS 自身问题）

- paramiko 部署脚本**明文硬编码 root 密码**；曾把公众号 AppSecret 硬编码进代码泄露
- 根目录堆积 40+ 一次性清库/诊断脚本，测试产物散落未归档
- 数据大屏纯 mock 数据、Taro 脚手架残留 4 份、docs-reports/prototypes 空目录
- 无托管 CI（只有本地视觉回归）、无监控、无多租户

**结论**：当前系统是「三天搭建的高质量 MVP」，业务闭环与文档驱动是对的；与 ClinInsOS 的差距集中在
**工程成熟度**（迁移链/审计/幂等/权限点/测试/部署）与**运营完备性**（管理后台覆盖、真实 IM、看板）。
P0 五项是上线硬门槛，P1 七项决定能否像 ClinInsOS 一样长期演进。
