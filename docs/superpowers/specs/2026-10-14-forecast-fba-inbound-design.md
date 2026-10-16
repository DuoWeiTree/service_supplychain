# 预测计入 FBA 在途货件 + 调仓中预留 —— 设计

| | |
|---|---|
| 日期 | 2026-10-14 |
| 状态 | 负责人已定取值（「按建议」）· 待审 spec |
| 依据 | `00` OP-19（负责人投诉「库存 37」对不上领星）· OP-14（`in_transit_excludes: ["fba_inbound"]`，设计取值，本篇推翻）· S-34 ⑥（期初只读 `afn_fulfillable_quantity`，本篇修订）· AR-D1（预测可用 CH）· S-34 ④（逾期在途不挪进未来月）· `14` §1 |
| 不在本篇 | 已建未发货件（WORKING / READY_TO_SHIP）进推演 · 订单预留 / 处理中预留 · 欧洲共享池（M-4）|

---

## 1. 问题（10-14 实测，计划 #2 · DCC1042015GLZ2B · 店 11072）

| | 领星 | 我们 | |
|---|---|---|---|
| 可售 | 49（实时） | 52（CH 10-14 采集） | 当天变动，正常 |
| 发往 FBA 在途 | 31 | **不计** | ★ OP-14 → 预测 12-13 断货 |
| 调仓中预留 | 2 | 不计 | S-34 ⑥ |

## 2. 裁定（负责人 10-14「按建议」）

| # | 取值 |
|---|---|
| a | 到货日 = 亚马逊送达时段**结束日**（`sta_delivery_end_date`）—— 宁晚不早，断货日不报晚 |
| b | 只算**已发出**的货件；已建未发（WORKING / READY_TO_SHIP）不进推演，只在依据里列 |
| c | 期初 = 可售 + **调仓中**（`reserved_fc_transfers`）；订单预留（已卖）与处理中预留不算 |

## 3. 取数（CH，AR-D1）

`jxd_raw.lingxing_fba_shipment_list` × `lingxing_fba_shipment_list_items`（按 `shipment_id`），快照日 = 预测 `as_of`（与 `fba_detail` 同一天）。

| 规则 | |
|---|---|
| 状态 | `SHIPPED · IN_TRANSIT · DELIVERED · CHECKED_IN · RECEIVING` |
| 件数 | 每 (货件, msku) 剩余 = `quantity_shipped − quantity_received`；< 0 → 0，计入 `dropped.fba_inbound.over_received` |
| 归属 | (货件 `sid`, `msku`) 直接对 msku，不分摊 |
| 键重复 | 同一快照日 (货件, msku) 多行 → 合并并计数（`rows_collapsed`，同 `parse_onhand`）|
| 快照缺 | as_of 当天没有货件快照 → 该批 FBA 在途**未知**（reason `fba_inbound_unavailable`），不当 0；★ 不回退旧快照（设计取值 · 未实测；货件状态一天就能变）|
| 对账 | Σ 剩余（SHIPPED + IN_TRANSIT）对 `fba_detail.afn_inbound_shipped_quantity`，不等只记日志（`op=fba_inbound_reconcile`），不阻断 |

10-14 实测：DCC1042015GLZ2B 已发出未签收 = 7（11-08）+ 5（11-15）+ 19（11-22）= **31** = `afn_inbound_shipped_quantity` 31 ✓。

期初：`SQL_FBA_ONHAND` 加 `reserved_fc_transfers`（`Nullable(Int64)`；10-14 实测本 msku = 2，另有订单预留 1 · 处理中 1 不算），返回分两列（可售 · 调仓中），台账起点 = 两者和；`basis` 里分开写。该列为 NULL 的行按 0 计入并计数 `dropped.fc_transfers_null`（可售列有值时调仓缺失不该把整个 msku 拖成未知）。

## 4. 进推演

| 情形 | 处理 |
|---|---|
| 送达结束日 ∈ [as_of+1, 推演窗末] | 作为该 msku 的到货（`Arrival`，`note.kind = "fba_shipment"` · 货件号 · 状态 · 送达时段）|
| 结束日 > 推演窗末 | 进 `beyond_horizon`（reason `arrives_after_horizon`）|
| 结束日 ≤ as_of（逾期未签收）/ 无送达时段（非 STA 货件）| ★ 不进台账，进 `unallocated`（`scope: "msku"`，reason `fba_overdue` / `fba_no_eta`），点名件数 —— 同 S-34 ④ |

★ FBA 到货在**第一遍基线就进**台账：计划采购按缺口分配（`allocate`），缺口必须已扣掉路上的货，否则采购会被分得过多。

恒等式不变：月末 = 期初 − 期望 + 到货；到货 = 计划采购分配 + FBA 货件，`basis.arrivals[]` 逐条带 `kind`。

## 5. 接口（`08` grid）

- `forecast[].basis.as_of_start` / `bridge`：加 `onhand_parts: {fulfillable, fc_transfers}`，`onhand_as_of` = 和。
- `forecast[].basis.arrivals[]`：FBA 条目 `{kind: "fba_shipment", date, units, shipment_id, status, deliver_from, deliver_to}`。
- `forecast_sku[].pipeline.fba_inbound` = Σ 已进推演的 FBA 剩余（原恒 null）；`fba_inbound_reason` null；`in_transit` 口径不变（采购管道），`in_transit_excludes` 保留 `fba_inbound` —— 因为它已进「月末」，不在「在途」里重复。
- `source_notes.dropped.fba_inbound`：`over_received` · `rows_collapsed` · `not_shipped`（WORKING / READY_TO_SHIP 件数，裁定 b 只列）。

## 6. 界面（`10`）

- 「现可售」改「**现有**」：title「FBA 可售 N + 调仓中 M，采集于 …」。
- 依据面板到货段：FBA 货件逐条「货件号 · N 件 · 送达 x~y（按结束日）」；逾期 / 无送达时段的单列「未计入」。
- 货号行「在途」title：「采购管道；另有 FBA 货件 N 件已计入月末」。

## 7. 日志

`op=ch_fba_inbound as_of= keys= shipments= units= dropped= elapsed_ms=`；失败带 `cause.code`（同 `ch_onhand`）。`op=fba_inbound_reconcile msku sid ours lx diff`（只在不等时 WARNING）。

## 8. 测试（先红）

纯：`parse_fba_inbound`（状态过滤 · 超收 → 0 计数 · 重复合并 · 非 STA 无日期）；`forecast_plan`（FBA 到货进基线 → 采购分配缺口变小 · 逾期进 unallocated · 越窗进 beyond · 恒等式）；期初含调仓。集成：grid 响应形状 · 冻结 fixture 重生（`test_label_grid_fixture.py`）。Web：依据面板 FBA 条目 · 「现有」title。★ 对账：用 10-14 这个 msku 的真实 31 件做一条回归样例（落 fixture，不连 CH）。

## 9. 待裁定（登记 `00`）

| # | 问题 | 暂行 |
|---|---|---|
| FI-Q1 | 货件快照缺当天是否允许回退前一天 | 不回退，未知 |
| FI-Q2 | 逾期未签收货件（如 FBA19MSRJ7LG 窗口 10-11，还差 1 件）要不要按「明天到」计 | 不计，点名 |
