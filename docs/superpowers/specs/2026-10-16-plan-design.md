# 销售计划三页（列表 · 版本 · 详情）交互改造 —— 设计说明

> 裁定：`docs/00` UX 组 UX-D11 ~ D17（负责人 2026-10-16，N = 20）。
> 评审：`.impeccable/critique/2026-10-16__plan-list.md`（19/40）· `2026-10-16__plan-detail.md`（23/40）。
> 改动面：`web/src/pages/{OpsHome,PlanRevs,PlanGrid,PlanSheet}.tsx` 及其模型 · `web/src/shell/AppShell.tsx` ·
> `api/ui/{dashboard,plans,submit}.py` · `rules/digest.py` · 迁移 `028` · `docs/08` · `docs/10` · `docs/00`（UX-Q30~）。

## 1. 列表 `/`（OpsHome）

```
我的计划                                                       [新建销售计划]
提交后又改过 2 · 未提交 1 │ 进行中 3 · 已提交 2 · 已确认 1 · 已完结 0 · 已撤销 0 …   ← 前两个可点 = 筛选
[周期][计划][版本][状态▾][排序▾]
下一步                       计划名        周期             版本  状态
[提交后又改过] 人改了 2 格 · 比 rev 7   测试1   2026-10 跨 6 月   7   已确认   打开
[未提交]                     测试2   …                     —   未提交   打开
                             测试3   …                     1   已提交   打开
已完结 N 张 · 已归档 N 张不在列表         ← 没取到时写「正在取…」，不出数
```

| 项 | 做法 | 依据 |
|---|---|---|
| KPI | `dash.counts` 的键全集 − `scope_note.unreachable_in_stage_a`，**不手列**；前面加两个可点的待办计数 | UX-D13 / S-20 |
| 状态筛选 | 同一个全集 − 派生桶（`scope_note.derived_buckets`，后端新给）+「未提交」；选「已完结」时列出已完结行 | UX-D13 |
| 下一步 chip | 改过 → `提交后又改过`（主色，旁注「人改了 N 格 · 比 rev X」）；从未提交 → `未提交`（主色）；其余不出 chip | UX-D11；词取本页原文 |
| 排序 | 默认「要动手的在前」，再按周期降序；原三种排序保留 | UX-D11 |
| 两栏面板 | 拆掉，信号并进行 | UX-D11 |
| 加载 | 列表没取到：表格与底行写「正在取…」 | 空 ≠ 0 |

## 2. 「提交后又改过」只算人改的（UX-D12）

取证：`tests/test_api_dashboard.py::test_recompute_alone_does_not_count_as_changed_since_submit`
在旧口径下红 —— 重算预估改 `system_units`，`content_digest` 跟着变。

- 迁移 `028`：`plan_rev.input_snapshot jsonb NULL` —— 提交那一刻**人填的那一层**：
  `{"purchase": {"sku|YYYY-MM": planned_units}, "demand": {"seller_sku|sid|YYYY-MM": expected_units}}`，
  `null` 照存（格子在、没人填）。只在 INSERT 时写。
- `rules/digest.py` 加 `input_snapshot()` / `human_changes()`；`content_digest` **不动**（S-13 的算法一变等同迁移）。
- 看板：快照在 → 逐格比，`human_cells = N`、`basis = "input_snapshot"`；
  028 之前的版本没有快照 → 退回 `content_digest`，`human_cells = null`、`basis = "content_digest"`，**写日志**（回退必须有声）。
- 认领 / 移出 msku 让格子多了少了 —— 算人改的（人的动作）。改了又改回 —— 不算。

## 3. 版本页 `/plans/:id/revs`

h1「版本 · {计划名}」（`getPlan`，取不到写「版本」并点名错误）；「撤销」只给 `in_flight` 的那一版
（`04` §1 #8：撤的是非终态记录；`cancel_rev` 只撤 `rev` 列仍是它且非终态的 —— 不在流转的版本撤了也是 0 条）；
「设为当前使用」降成 ghost；64 位摘要收进 `<details>`；没有当前使用 / 没有流转中都说出来。

## 4. 详情 `/plans/:id`

### 4.1 批量写接口（UX-D14）

`PUT /v1/plans/{plan_id}/cells`
`{demand: [{seller_sku, sid, period, expected_units}], purchase: [{sku, period, planned_units}]}`

- 一个事务，**全有或全无**：任一格不合法 / 不存在 → 整批不写，`cells[]` 逐格点名（400 `bad_units` / 404 `cell_not_found`）。
- 成功 → `{demand: [十键行], purchase: [{sku, period, planned_units}]}` 逐格回执。
- 上限 `CELLS_BATCH_MAX = 1000`（设计取值 · 未实测，UX-Q30）→ 超了 400 `batch_too_large {max, got}`；同一格出现两次 → 400 `duplicate_cell`；空批 → 400 `empty_batch`。
- 日志：`op=put_cells plan_id demand=N purchase=M elapsed_ms failed=[...]`。
- 前端：粘贴 / ⌘D / ⌘R / Del 收成一批 → 一次 `putCells` → 成功后只 `load()` 一次。单格编辑仍走单格 PUT。

### 4.2 超 20 格先确认（UX-D15）

一批里「清空 / 覆盖」的格（人填过、且这次要变的）> 20 → 弹窗列出全部（行 · 月 · 原值 → 新值），确认才发。≤ 20 直接发。

### 4.3 粘贴对齐（UX-D16）

- 首列有一格认得出是本计划的 msku（`seller_sku`）→ **按 msku 对齐**：每行找那个 msku，值从选区左列起铺；
  认不出 / 一个 seller_sku 对多家店 → 点名，不写。
- 否则按位置铺开；落到**不同类的行**（选区左上是采购行、这一行是销量行，或反之）→ 不写、点名。
- 结果留在表上方（`paste-report`），不是一闪的 toast。

### 4.4 光标 · 搜索 · 紧凑 · 宽度

- 光标 / 锚点记 `行 key（p:sku / d:seller_sku:sid）+ 月`；编辑框开着时目标格在开编辑那一刻定死。
- 搜索命中 msku（而非货号 / 店铺）时只显示命中的 msku 行（店铺块强制展开），命中行高亮。
- 紧凑模式：格子只留主数，副数收进 title；行头不截货号。
- UX-D17：`<AppShell wide>` → `.wrap--wide`（不设上限）；给全部详情页（计划编辑 · 排货表 · 采购单编辑器 / 详情）；列表页不变（00 UX-D17「详情页」原文，此前只做计划详情是越权收窄）。

## 5. 测试点

后端：看板口径五条（重算不算 / 人改计数 / 改回不算 / 移出算 / 旧版本回退有声）· `put_cells` 全有或全无 / 上限 / 重复 / 回执形状。
前端：KPI 全集 · 已确认可筛 · chip 与排序 · 加载不出数 · 版本页撤销只给流转中 · 粘贴 msku 对齐与行类点名 ·
批量只发一次 + 只重拉一次 · >20 确认清单 · 光标随排序走 · 搜索 msku 只显示命中 · AppShell wide。
