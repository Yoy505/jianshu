# 说明书待核私有队列

`import_drug_leaflet_intake.py` 用于暂存已经取得、但还不能安全关联到药品主表的说明书原件。它适用于批准文号缺失、说明书日期是占位符、受理号与批准文号无法一一对应，或需要人工核对版本的资料。

该流程不会联网，不会绕过验证码，不会创建 `mp_drugs`，不会自动把受理号当批准文号，也没有公开 API。所有新增记录固定为 `review_status=pending`，`distribution_authorization_ref` 为空；待核记录不会出现在小程序公开药品列表、详情、收藏或说明书接口中。

## 清单

顶层字段必须是 `schema_version`、`dataset_name`、`records`。每条记录保存来源命名空间和稳定记录 ID、受理号（可空）、来源药品名、官方详情页、页面公示日期、原件中声称的批准文号（可空）、能确认的说明书日期（可空）、版本备注、核验日期、相对 PDF 路径和 SHA-256。日期占位符（如 `XXXX` 或 `XX`）必须填 `null`，在 `version_note` 保留原文情况。来源公示日期不能代替说明书版本日期。

来源链接只允许当前核实的官方主机：`www.cde.org.cn`、`www.nmpa.gov.cn`、`app1.nmpa.gov.cn`、`app.gjzwfw.gov.cn`。原件根目录和待核目录均禁止位于公开 `UPLOAD_DIR`；待核目录还必须和既有 `DRUG_LEAFLET_DIR` 分开，避免旧版孤儿清理误删待核文件。

## 运行

从 `backend/` 执行，先在目标环境升级迁移到 `e74c5da19033`：

```sh
.venv/bin/alembic upgrade head

# 默认仅预检，不写数据库和私有目录
.venv/bin/python scripts/import_drug_leaflet_intake.py \
  --manifest data/official_drugs/cde_leaflet_intake_2026-09-24.json \
  --source-root data/official_drugs/leaflet_samples_all_scope

# 明确内部暂存时，报告必须写入待核私有目录
.venv/bin/python scripts/import_drug_leaflet_intake.py \
  --manifest data/official_drugs/cde_leaflet_intake_2026-09-24.json \
  --source-root data/official_drugs/leaflet_samples_all_scope \
  --apply --report private_drug_leaflet_intake/cde-sample-2026-09-24.json

.venv/bin/python scripts/import_drug_leaflet_intake.py --audit-orphans
.venv/bin/python scripts/import_drug_leaflet_intake.py --audit-orphans --remove-orphans
```

`--apply` 按批提交并以内容哈希保存 PDF；相同来源 ID + 相同哈希重跑为 `unchanged`，同来源 ID 的不同哈希作为新的版本记录。清单事实变化返回 `conflict`，旧记录不覆盖。批次失败后先查报告和 `--audit-orphans`，再重跑。文件哈希只证明本地原件未被修改，不证明清单字段与原件逐项一致，也不证明取得公开转载或商业使用许可。

人工核验至少要补齐：批准文号与药品主表的证据、说明书核准/最近修改日期、附件完整性、监管状态、公开展示使用依据和医学内容审核。完成前不得把 `pending` 改成 `verified`，也不得复制到 `mp_drug_leaflets` 或发布到小程序。
