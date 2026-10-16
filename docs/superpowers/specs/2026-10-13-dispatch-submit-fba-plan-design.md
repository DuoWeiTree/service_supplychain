# C-c1 · FBA 排货单提交为领星 FBA 发货计划 —— 设计

| | |
|---|---|
| 日期 | 2026-10-13 |
| 状态 | 负责人已批准（「开工」）· 已实现（计划 `superpowers/plans/2026-10-13-dispatch-submit-fba-plan.md`，迁移 024）· 待手动验收 · 实调 `00` CC-D2~D4 |
| 依据 | `00` CC-D1（先做到领星 FBA 发货计划，STA 由人在领星操作）· AR-D1（业务事实只走领星 API）· CA-D13（平台仓 · FBA 表）· CA-D14（发到领星才锁死）· C-b spec `2026-10-12-dispatch-submit-ows-design.md`（同一套查重 / 待核实 / sheet_op / 回执骨架）· 领星文档 `FBA/CreateShipmentPlan.md` · `FBA/ShipmentPlanLists.md` · `FBA/FbaPlanStorageAllocate.md` · `FBA/FbaPlanStorageRelease.md` · `07` §3（createShipmentPlan 可逆、无幂等键、调用前查重） |
| 不在本篇 | STA 四步 / Placement 选择 / SP 发货单（C-c2，待 C-5 · O-7 · C-9）|

---

## 1. 一张 FBA 排货单 × 来源仓 → 一个领星发货计划批次

★ 实施裁定（10-13）：按 **排货单 × 来源仓** 一批（同 CB-D5 海外仓），参考号 `SCM-FBA-{consignment_id}-{from_wid}`。理由：`ext_ref` 一行 = 一个来源仓，「已投送」库层判据按来源仓核（`consignment_dispatch_incomplete`），待核实 / 断点续跑整套照用。代价：一张排货单从两个本地仓发，领星里是两个批次。


| 我们 | 领星 `createShipmentPlan` |
|---|---|
| 排货单（平台仓 · FBA 表，目的 = 本店 `dest_sid`） | 一次调用 = 一个批次（`seq` RP…），批次下按行生成计划（`order_sn[]` R…） |
| 排货行 `(seller_sku, sid, from_wid)` | `product_list[]`：`sid` · `msku` · `fnsku`（★ 必填）· `shipment_plan_quantity` · `wid`（= 来源本地仓）· `packing_type` · `quantity_in_case` · `box_num` |
| 装箱（`consignment_packing`，G1） | `quantity_in_case` = 单箱件数 · `box_num` = 箱数 |
| 参考号 `SCM-FBA-{consignment_id}-{from_wid}` | ★ 写进批次 `remark`（领星无幂等键，`07` §3） |

`packing_type`（1 混装 / 2 原厂）：★ **待裁定 CC-Q3**，暂行一律 2 原厂（设计取值 · 未实测）。

## 2. 阻断项（提交前点名，不发请求）

| code | 什么时候 |
|---|---|
| `fnsku_missing` | 某 msku 在镜像里没有 fnsku（★ 文档必填；AR-D1 迁移前暂读镜像，迁移后改领星 listing） |
| `packing_missing` | 装箱没填（G1 已挡，确认后不会出现；保险） |
| `route_not_submittable` | 不是平台仓 · FBA 表 / 旧版混合表 |

## 3. 提交（同 C-b 骨架）

`sheet_op` 锁 → 每张未建的 FBA 排货单：
1. 查重：`shipmentPlanLists`（`sids` = 店，★ 逗号分隔字符串（CC-D2 实测）· 创建时间近 N 天 · ★ 客户端按 `remark` = 参考号筛）→ 命中 = 认下；读失败 = 不建（`lookup_failed`）。
2. 建：`createShipmentPlan`（reversible，`erp_call` 留痕）→ 拿 `seq` + `order_sn[]` 写 `ext_ref`（kind `fba_plan`，`ext_no` = `seq`，计划号存明细）。
3. 结果未知 → 再查重；仍不知道 → 待核实（三出口同 C-b）；★ 绝不自动重试。
4. ★ 锁库存：CC-D3 实测**建计划不锁**（`lock_status=1`）→ 建后紧跟 `allocateStorage(order_sn[])`，★ 按 `data.failNum` 判（`code=0` 不代表每张都锁上）；锁失败 = 计划已建、库存未锁 → 点名 `lock_failed`，排货单不算已投送，可单独重试锁（只锁，不重建）。

全部建齐 → 排货单 `已投送`（同 C-b，库层核 ext_ref）。

## 4. 取消（CB-D6 同理：发到领星的要先撤）

★ CC-D3 实测：**没有作废 / 删除接口**，数量改 0 被拒。CC-D4 定：

1. 先现查计划状态：已处理（10，人已在领星推进到 STA / 发货）→ 拒绝取消，点名（同 `ows_not_cancellable`）。
2. `releaseStorage(order_sn[])` 释放库存（按 `failNum` 判）→ `updateShipmentPlan` 计划备注改「SCM 已取消 请作废」→ 记作废留痕（`void_reason` 带「领星发货计划需人工作废」）→ 表取消。
3. 页面与取消结果点名「领星发货计划 RP… 需在领星手工作废」。

## 5. 回执（同 spec 2026-10-13，AR-D1 只读领星）

`shipmentPlanLists` 按批次号读回：计划状态（-5 已驳回 · 0 待审核 · 5 待处理 · 10 已处理）+ `lock_status` + 关联货件（`mws_relate.shipment_mws_sn`，人在领星做完 STA 后才会有）。
★ 归档判据 **待裁定 CC-Q4**：FBA 的「已完成」= 计划已处理？货件已发？亚马逊已收？（`04` §4.4 陷阱③：`DELIVERED` 不等于入库）。暂行：只读回、不归档。

## 6. 界面

FBA 表确认后底部同海外仓：「提交领星（N 张）」⛔ 确认框（逐张：店 · 来源仓 · 行数 · 件数 · 装箱）→ 逐张结果；排货单写「领星发货计划 RP… · 计划 R…」+ 领星进度；底部原「提交到亚马逊（STA）还没做」改为「提交后请在领星完成 STA 建货件 / 发货」。

## 7. ★ 实调（CB-D4 同法，需负责人授权）

用测试货号在一个店建 **1 个**发货计划，回答：

| # | 问题 |
|---|---|
| CC-Q1 | 建计划后 `lock_status` 是 1 未锁还是 2 已锁？ |
| CC-Q2 | 有没有作废 / 删除计划的接口；没有的话 `releaseStorage` 后计划状态如何、领星页面能否手工作废 |
| CC-Q5 | 同一 `remark` 重复建是否被拒（查重不依赖它，但要知道） |
| CC-Q6 | `wid` 不传 / 传本地仓的差别；`fnsku` 与 `msku` 不匹配时报什么 |

实调后人工在领星页面作废这个计划（若无接口）。

## 8. 测试（先红）

纯：分组 / product_list 组装 / 阻断项 / token。API：查重命中认下 · 建成 · 被拒停 · 结果未知查重认下 / 待核实 · 断点续跑 · 取消路径（按 CC-Q2 结果）· sheet_op 互斥。Web：提交按钮 / 确认框 / 结果 / 计划号显示。

## 9. 待裁定（登记 `00`）

| # | 问题 | 暂行 |
|---|---|---|
| CC-Q1 | 建计划是否自动锁库存 | 实调 |
| CC-Q2 | 作废计划的接口 | 实调；没有则释放库存 + 人工作废 |
| CC-Q3 | `packing_type` 混装 / 原厂 | 2 原厂 · 设计取值 · 未实测 |
| CC-Q4 | FBA 排货单归档判据 | 只读回、不归档 |
| CC-Q5 | 重复 remark 是否被拒 | 实调；不依赖 |
| CC-Q6 | `wid` / fnsku 不匹配行为 | 实调 |
