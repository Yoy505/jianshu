# 全类别药品监管来源暂存

此流程接收**本地、可复核的监管目录来源记录**，范围可包括国产/进口化学药、中药和生物制品。它不下载官网数据、不导入说明书、不创建 `mp_drugs`、不把资料公开到小程序。现有 3 条 CDE 化药和 3 份私有说明书保持原状。清单中的每条记录仅表示某个来源在某次核验时呈现的登记事实；批准上市不等于当前实际销售，`approved_current` 也不代表本项目已经完成医学、来源或使用范围审核。

## 输入契约

运行时必须同时提供离线 JSON 清单和 `--source-root` 本地来源文件目录。清单顶层恰有 `schema_version: 1`、`dataset_name`、`records`。每条恰有：

| 字段 | 要求 |
|---|---|
| `source_namespace`, `source_record_id` | 来源命名空间及其稳定条目 ID，联合唯一；不能用药名代替 ID |
| `registration_identifier` | 来源原文中的批准文号或注册证编号；无则 `null`；不设全局唯一 |
| `source_product_name` | 来源原文的药品名称，不据此自动匹配 |
| `drug_class` | `chemical`、`tcm`、`biologic` |
| `origin` | `domestic`、`imported` |
| `regulatory_status` | `approved_current`、`revoked`、`expired`、`suspended`、`unknown` |
| `source_status_text` | 来源对监管状态的原文；缺失为 `null` |
| `regulatory_as_of` | 该状态所据日期，`YYYY-MM-DD` 或 `null` |
| `source_url` | 已核实主机上的 HTTPS 页面链接；当前精确白名单为 `www.nmpa.gov.cn`、`app1.nmpa.gov.cn`、`www.cde.org.cn`、`app.gjzwfw.gov.cn` |
| `source_document_path` | `--source-root` 内的相对原件路径，不接受符号链接 |
| `source_document_sha256` | 本地原件的 64 位小写 SHA-256；导入前重新计算核对 |
| `source_checked_at` | 查询核验日期，`YYYY-MM-DD` |

`approved_current` 仅允许来源状态原文**恰为**“有效”“现行有效”或“有效期内”且有状态日期。单纯“已批准”“获批上市”不能推断当前注册有效，须填 `unknown` 并保留原文。其他已知状态也需要原文与状态日期。此规则只阻止明显夸大；脚本无法自动证明清单中的状态原文确实来自对应文件或监管页面，仍须人工复核。遇到其他官方状态用语时先标 `unknown`，经来源核实后再维护明确映射。

`source_document_sha256` 验证原件完整性，**不等于**验证清单字段与原件逐项一致，更不等于取得小程序转载或说明书全文使用依据。`--apply` 将来源原件按 SHA-256 复制一次到 `DRUG_REGULATORY_SOURCE_DIR`（相对配置始终锚定 `backend/`，且禁止放在公开 `UPLOAD_DIR` 内）。主表保存最新私有存储键；`mp_drug_regulatory_observations` 保存每条来源记录所见的历史快照与待审状态。原件仅供受控内部复核，不提供公开下载。

`app.gjzwfw.gov.cn` 是国家政务服务平台[国产药品查询](https://app.gjzwfw.gov.cn/jmopen/webapp/html5/datasearchgcyp/index.html)和[进口药品查询](https://app.gjzwfw.gov.cn/jmopen/webapp/html5/datasearchjkyp/index.html)的精确主机；页面均标明服务由国家药监局提供。[国家药监局信息中心网站](https://www.nmpaic.org.cn/gywm/znjs/)虽可核实其机构身份，但尚未核实本批药品注册条目的具体查询页，因此未加入本导入器白名单；后续需要对应官方查询页面证据再扩充。

## 命令与对账

在 `backend/` 下运行：

```sh
.venv/bin/python scripts/import_drug_regulatory_sources.py \
  --manifest /private/path/catalog-manifest.json \
  --source-root /private/path/source-files \
  --report /private/path/dry-run-report.jsonl

.venv/bin/python scripts/import_drug_regulatory_sources.py \
  --manifest /private/path/catalog-manifest.json \
  --source-root /private/path/source-files \
  --batch-size 100 --apply \
  --report /private/path/apply-report.jsonl

.venv/bin/python scripts/import_drug_regulatory_sources.py --audit-orphans
.venv/bin/python scripts/import_drug_regulatory_sources.py --audit-orphans --remove-orphans
```

默认只预览；`--apply` **必须**提供私有 `--report`，才会分批写入 `mp_drug_regulatory_sources`，新增行固定 `review_status=pending`、`canonical_drug_id=null`。同一来源键、同一来源快照和同一事实重跑为 `unchanged`；只更新核验日期为 `rechecked`；单条事实未变、整库导出文件哈希或来源页面 URL 变化为 `new_snapshot`，旧原件及旧 URL 的观察记录保留，新观察记录和主表最新指针均回到 `pending`。药品行事实或监管状态变化为 `conflict`：旧事实与旧快照指针不覆盖，来件原件复制到私有存储，新增 `kind=conflict` 的待审观察记录保存来件事实和指纹，同时主表 `review_status` 立即降为 `pending`。因此曾获内部接受的条目若收到撤销、暂停或过期来件，也不能继续以旧 `accepted` 状态通过未来公开门禁；新状态仍须人工核实，不能自动代替旧事实。已有 `canonical_drug_id` 不自动改写。

每批在同一数据库事务中提交主表与观察记录；报告先以 `0600` 创建，再按 `started → batch_committed/batch_previewed → complete` 追加 JSONL，并在每批后刷盘。若晚批或报告写入失败，之前批次可能已提交，日志会给出已提交批次数；操作方须查看 JSONL 检查点并幂等重跑。遇 `conflict` 时其他批次可已提交，正常完成后仍以退出码 **2** 报冲突。相同注册编号来自不同来源键时允许并列，报告给出重复数量和至多 5 个来源键样例，绝不自动合并。报告另有 `unmatched`（未找到编号完全相等的非演示 `mp_drugs` 候选）、药品类别/国产进口/监管状态、导入动作及来源命名空间的分层数量，可单列疫苗专项来源，不会从 `biologic` 推断全部是疫苗。候选 ID 仅供人工对照，脚本不自动建立关联；`--report` 禁止写入公开 `UPLOAD_DIR`。

即使全部来源记录暂存成功，也不能宣称“全部获批上市药品已覆盖”：必须另以官方目录快照做来源总数、类别、国产/进口、现行/历史状态、缺失及冲突对账。说明书附件的覆盖率和版本对账使用独立流程。
