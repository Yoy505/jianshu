# 药品说明书原件内部暂存

此流程只处理项目方提供或已核准用于**内部核验**的本地 PDF。脚本不联网、不抓取网站、不导入用药文字，也不修改药品主表或小程序公开页面。`authorization_ref` 是导入清单中记录的依据编号，程序不能据此判断公开转载、分发或医学审核已经获得批准。

## 清单和私有文件

`cde_leaflets_internal_2026-09-24.json` 对应 `leaflet_samples/` 中 3 份官方详情页附件。PDF 样本位于本机且被 `.gitignore` 排除；仅复制代码仓库无法复现导入。清单中的 SHA-256 会在每次运行时重新校验；导入前还用 `pypdf` 对可提取文字的 PDF 检查正文同时出现批准文号和药品通用名。这个机械匹配仅防明显错配，不证明版本有效、内容准确或取得公开使用许可。来源详情链接用于人工复核；`source_document_id` 记录附件标识。资料发布日期和版本不可核实的值为 `null`，不能用查询日期代替。

通用清单顶层必须恰有 `schema_version=1`、`dataset_name`、`records`。每条必须恰有：`approval_no`、`pdf_path`、`source_name`、`source_document_id`、`source_url`、`authorization_ref`、`document_date`、`document_version`、`source_checked_at`、`expected_sha256`。`pdf_path` 只能是 `--source-root` 内的相对 `.pdf` 路径，整个根目录也不能位于公开 `UPLOAD_DIR` 下；`expected_sha256` 必须是 64 位小写十六进制；`source_url` 是 HTTPS 或 `null`；日期采用 `YYYY-MM-DD` 或允许的 `null`。不接受适应症、用量、禁忌等临床文字字段。

## 运行

从 `backend/` 目录执行，先备份目标数据库并升级迁移。示例中 `DATABASE_URL`、`APP_ENV` 应由执行环境正确配置：

```sh
.venv/bin/alembic upgrade head
.venv/bin/python scripts/import_drug_leaflets.py \
  --manifest data/official_drugs/cde_leaflets_internal_2026-09-24.json \
  --source-root data/official_drugs/leaflet_samples
.venv/bin/python scripts/import_drug_leaflets.py \
  --manifest data/official_drugs/cde_leaflets_internal_2026-09-24.json \
  --source-root data/official_drugs/leaflet_samples \
  --apply --batch-size 200 \
  --report /path/to/private/leaflet-import-report.json
```

默认仅预览，不写原件或数据库。`--apply` 将原件按 SHA-256 存入 `DRUG_LEAFLET_DIR`（默认 `./private_drug_leaflets`，配置禁止位于公开 `/uploads` 下），并写入 `mp_drug_leaflets`，状态固定为 `pending`，`identity_check_method=pypdf_text_match` 仅表示机械文本身份匹配。必须先有批准文号完全匹配的非演示 `mp_drugs` 行；未匹配、演示药品、文字无法可靠提取（`manual_review`）或正文身份不符（`identity_mismatch`）均跳过原件暂存、列入对账并使进程以非零状态退出。脚本不会自动创建药品，也不会改写其现有医学内容或发布状态。

每批独立提交，失败后重新运行即可按 `(drug_id, file_sha256)` 跳过已暂存的同一原件。早期已暂存且仍为 `pending` 的同一原件，只有重新通过正文身份核对后才补记 `identity_check_method`；发布状态和原件不变。相同批准文号的新 PDF 哈希会保留为另一个待审核版本，旧原件保持不变。全量导入应分批执行，并记录清单总数、匹配数、文件校验通过数、插入数、重复数、人工核对数、错配数、缺失批准文号数及最终私有存储数；目录记录数不能视为说明书覆盖数。

若批次提交失败，脚本查询数据库后只回收该批新建且无人引用的文件。若数据库不可查询，或进程被强制终止，文件可能留在私有目录，执行以下对账。清理前暂停其他写入方；审计和导入脚本自身使用同一文件锁，已被数据库任何药品版本引用的相同哈希 PDF 不会删除：

```sh
.venv/bin/python scripts/import_drug_leaflets.py --audit-orphans
.venv/bin/python scripts/import_drug_leaflets.py --audit-orphans --remove-orphans
```

扫描 PDF、无法可靠抽取文字的文件需另设人工核验流程，本脚本会标记为 `manual_review` 并跳过。`pypdf` 已列入 `requirements.txt`；目标服务器需正常安装该依赖并验收后再批量执行。

**公开边界：** 当前没有说明书审核、发布或公开下载 API。`pending` 原件仅可内部核对。公开展示须另行完成资料使用依据、医学审核、版本切换、撤回及权限控制。
