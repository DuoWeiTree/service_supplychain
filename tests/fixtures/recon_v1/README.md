# 兄弟仓 228 格对账 fixture（冻结）

逐字节拷自 `jxd_model_group/model_inventory_forecast/models/inventory_forecast/tests/fixtures/`
（兄弟仓 commit `e2ddce048a1496ee2bb891ac0ed03083bc9c350b`，2026-09-24 拷贝），来源见那边的 README：
2026-08-21 从运营 Excel（lucy-库存盘点 - A4PET.xlsx）一次性导出。

★ 这是**台账**门禁（Open point OP-1）：期望值是运营 Excel 的半月检查点余额，需求来自运营手填的
`overrides`，不经过 `baseline_rate`。`identity.csv` / `locations.csv` 在 compat 口径下不参与计算，
一并搬来是为了与 00 D-3「搬 9 个 CSV」逐字一致。

★ 不许手工编辑。要换基准就新建 `recon_v2/` 并写明理由。
