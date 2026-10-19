# 排货表详情 + 新建时自动分配默认排货量 —— 设计说明

> 裁定：`docs/00-待裁定清单.md` UX 组 UX-D7 ~ D10（负责人 2026-10-16）。评审：`.impeccable/critique/2026-10-16__dispatch-detail.md`（23/40）。
> 改动面：`usecase/sheet.py`（create · grid · load）· 新 `rules/autofill.py` · 迁移 `026_dispatch_autofill.sql` · `web/src/pages/dispatch/SheetPage.tsx` · `docs/15` §0 / §4 · `docs/03` · `docs/08` · `docs/10`。
> 本设计里自己定、没有裁定出处的细节一律登记 `00` UX-Q10 ~ Q19。

## 1. 自动分配（UX-D7 ~ D10）

### 1.1 什么时候跑

`POST /v1/sheets` 建表事务**提交之后**，同一个请求里再开一个事务跑一次（UX-D7「新建排货表时自动填一次」）。

- 海外仓表不跑（UX-D10），不写事件。
- 自动分配失败 ≠ 建表失败：表照建，事件表写一行 `outcome='failed'` + 原因，响应和页面都点名「默认分配没做成：原因」；日志 `op=sheet_autofill outcome=failed`。
- 事件写不进去（连 PG 都挂了）→ 响应里 `autofill.outcome='failed'`、`event_written=false`，日志 ERROR；页面读不到事件时**不写**「系统填了 0 格」，写「没有默认分配记录」。
- 「一次」由库层 `UNIQUE (sheet_id)` 保证；员工之后改格子走现有 `PUT /cells`，不重填。

### 1.2 规则（`rules/autofill.py`，纯函数）

输入：本计划活记录（含 `pending` = 总量 − 已排 − 已投送，跨全部排货表）· 每条记录的 `demand_by_seller` · 店铺镜像（has_fba）· msku 镜像（seller_sku, sid → sku）· 本地库存桶（CH 快照 + 比它新的领星现读，同网格 CA-D12）· 已承诺未发（`v_stock_committed`）。

```
记录按 (月份, 货号, line_id) 升序                         ← UX-D9：按月份先后
  还差 ≤ 0                    → 跳过 satisfied
  可分店 = demand_by_seller 的键 ∩ 镜像 ∩ has_fba ∩ 有可用 msku
  店份额 = 还差 按各店期望比例拆（最大余数法）           ← UX-D8
           只有 1 家可分店 → 全给它（比例无关）            ← UX-Q10
           期望为空 / 0 的店 → 不分、点名                ← UX-Q10
  店内 msku 份额 = 店份额 按 msku 期望比例拆（同上）       ← UX-Q11
  每个 msku 份额 → 本地仓按「这家店的可安排」降序取，取到为止，不够溢到下一个仓
                   每取一格就把它记进已承诺（后面的记录 / 店看到的是扣过的）← UX-D9：库存用完为止
  没取满 → 该记录记 stock_short（还差多少、可安排多少）
```

可用 msku：`demand_by_seller[sid].mskus` 里**认领的** msku（不是 msku_bridge 下的全部），在镜像里且对应这条记录的货号；名字以 `-FBM` 结尾的不填（UX-Q12）。

跳过原因（每条都进事件 `skipped`）：`satisfied` · `seller_not_in_mirror` · `no_fba` · `no_claimed_msku` · `fbm_msku` · `msku_not_in_bridge` · `expected_unknown` · `no_stock` · `stock_short`。

### 1.3 写入

每一格走与 `PUT /cells` 同一套闸（`_check_cell` · `_check_line`：本地仓、本店 FBA、msku↔货号、店在记录里、Σ ≤ total_units），写进 `dispatch_arrangement`，最后整表重派生一次。任何一格被闸挡下 → 整个事务回滚、事件记 `failed` + 那一格与错误码（不写一半）。

### 1.4 事件表 `dispatch_autofill`（迁移 026，只追加）

| 列 | |
|---|---|
| `sheet_id` UNIQUE | 一张表一次 |
| `rule` | `'UX-D8/D9 v1'` |
| `outcome` | `done` / `failed` |
| `inputs` jsonb | 输入快照：记录（line_id · 货号 · 月 · 还差 · 各店期望）· 库存截至 · 用到的 (货号, 仓) 桶与已承诺 |
| `filled` jsonb | `[{arrangement_id, line_id, seller_sku, sid, from_wid, units}]` |
| `skipped` jsonb | `[{line_id, sid?, seller_sku?, reason, …}]` |
| `error` jsonb | failed 时：stage · error · fields |
| `elapsed_ms` · `actor` · `at` | |

★ 不给 `dispatch_arrangement` 加来源列（铁律 6）。「哪格是系统填的」= 事件 `filled` 里的 arrangement_id；人改过件数 → 现值 ≠ 事件值，页面写「默认 N，已改」；人撤回或挪格 → arrangement_id 不在了，不再标。

## 2. 接口变化

| 接口 | 变化 |
|---|---|
| `POST /v1/sheets` | 响应加 `autofill: {outcome, filled, units, skipped, error, event_written}`（FBA 表才有；海外仓为 `null`） |
| `GET /v1/sheets/:id` | 加 `autofill: null | {rule, outcome, filled[], skipped[], error, at, actor, elapsed_ms}` |
| `GET /v1/sheets/:id/grid` | `staged_here` 改为只算未投送（草稿 / 已确认），与 `staged` 同口径 →「别的表已排未发」不再出负数（评审 P1 #11）；加 `dispatched_here` |

## 3. 页面

- 格子旁小标「默认」/「默认 N，已改」（来自 `detail.autofill.filled`）。
- 页头下一行：成功 →「新建时系统按还差与各店期望填了 X 格 Y 件，跳过 Z 条」+ 折叠区（规则一句话 + 跳过清单）；失败 →「默认分配没做成：原因」，不折叠。
- P0：只需补锁时弹窗按 `to_lock` 放行（已修，`440587a`）。
- 推迟到报告：整页重载、闸点名到行、G6 空表文案、未认领 msku 折叠。

## 4. 测试点

| 层 | 点 |
|---|---|
| rules（离线） | 比例拆 + 最大余数 · 单店全给 · 期望空的店点名 · 月份先后吃库存 · 店桶 / 共享桶（CA-D8）· 跨仓溢出 · stock_short / no_stock · -FBM 不填 · 未认领 msku 不填 · 还差 ≤ 0 跳过 |
| usecase + API（PG） | 建 FBA 表 → 格子写进 dispatch_arrangement、事件一行、派生出排货单 · 海外仓表不填 · 库存读不到 → 表照建、事件 failed、响应点名 · 闸挡下 → 一格不写、事件 failed · 事件只追加（UPDATE 被拒）· 一张表一次（UNIQUE）· 建后人改格子不重填 |
| web | 默认标 · 已改标 · 失败点名 · 折叠区默认收起 |
