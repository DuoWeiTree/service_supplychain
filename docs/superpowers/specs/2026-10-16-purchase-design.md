# 采购列表 `/purchase` + 采购单编辑器 / 详情 —— 设计说明

> 裁定：`docs/00-待裁定清单.md` UX 组 UX-D1 ~ D6。评审：`.impeccable/critique/2026-10-16__purchase-list.md`（23/40）、`__purchase-detail.md`（22/40）。
> 样板：`/dispatch` 列表（`2026-10-15-dispatch-list-redesign-design.md`）。
> 改动面：`web/src/pages/purchase/*` · `api/ui/lines.py` 新增 `GET /v1/purchase/plans` · `docs/10` §2.0 / §2.0a · `docs/08`。
> 不碰：AppShell / `.wrap`（UX-D17 由 plan 路做）、排货与销售计划页面。

## 1. 列表页 `/purchase`

```
采购                                          [搜 计划 / 货号]   [新建空白采购单]（次按钮）
待采购 · N 个计划                                      已选 3 条 · 还可组 420 件 [用选中的 3 条建采购单]
☐ ▸ 测试3 rev 1   1 个货号 · 4 条记录   需求 900 · 已组单（含草稿）0 · 已下单 0      还可组 898
☐ ▾ 测试1 rev 7   2 个货号 · 2 条记录   需求 380 · 已组单（含草稿）360 · 已下单 260  还可组 20
      [SCM-PO-5 草稿 · 100 件 · 接着编辑]
      货号           品名          2026-10                 还可组
      DCC1041294BW   …             已组满 180               0
      DCC1045014AI   …             ☐ 20 /200               20
不来自计划的草稿：SCM-PO-9 接着编辑                                     （有才出）
另有 2 个计划已全部组单下单，未列入（折叠，点名计划）                       （有才出）
需要人工处理（同现状）
▸ 全部采购单（4 张，含作废 0）                                             默认收起
```

| 层 | 内容 | 依据 |
|---|---|---|
| 计划抬头（主线） | 计划名 · rev · 「N 个货号 · M 条记录」· 需求 · **已组单（含草稿）** · **已下单** · ★ 还可组（最大字号） | UX-D1 / D2 |
| 草稿 chip | 涉及这个计划的每张草稿采购单一个 chip：`SCM-PO-x 草稿 · 本计划 N 件 · 接着编辑`；有未处理项写「待处理」 | UX-D2 / D3 |
| 哪些计划列出 | 有活记录（非已撤销 / 已完结）且（还可组 > 0 **或** 挂着草稿）的计划 | UX-D2：被草稿组满的计划不能消失 |
| 不列出的 | 全部组单且全部下单的计划 → 「另有 N 个计划已全部组单下单」折叠点名；后端 `excluded.all_placed` | 「被丢掉的一侧必须看得见」 |
| 默认 | 全部收起；`?plan=<id>` 进来时展开那一组并滚到它（UX-D6） | DL-D5 |
| 展开体 | 每计划一张货号 × 月小表，只列有记录的月。格子：可组 → 勾选框 + `还可组 /需求`；组满 →「已组满」；状态不可组 → 状态原文；没记录 →「—」 | DL-D2 / D6 / D10；空 ≠ 0 |
| 勾选 | 格子勾 = 那一格里可组的记录；抬头一个勾 = 本计划全部可组记录；★ 不再有跨计划的全局全选；跨计划组单仍允许（PO-6 实测跨计划） | 评审 P2 |
| 主动作 | 只有一个主按钮「用选中的 N 条建采购单」；「新建空白采购单」降为次按钮 | 评审 P1 |
| 全部采购单 | 折叠，默认收起，标题带张数与作废张数；「待处理」不再单占一列，>0 时在状态旁挂 chip；KPI 小卡删除；核对通过写在折叠内，不一致仍在外面红色点名 | UX-D3 · 评审 P2 |
| 搜索 | 计划名 / 货号，纯前端；货号命中自动展开、命中行高亮 | 同 `/dispatch` |

各店期望 / 已组的分店明细从列表移走 —— 它只在分配时有用，编辑器左栏有（屏幕给数据，不重复给）。

## 2. 编辑器 `/purchase/new`、`/purchase/:id`（草稿）

| 项 | 做法 | 依据 |
|---|---|---|
| 续编左栏 | 只列本单来源计划（`lines[].sources[].plan_id`）的可组记录，页头写「来自 销售计划「X」revN 的 M 条记录」；「加入其他待采购记录」放开。本单无来源 → 不收窄 | UX-D4 |
| 左栏结构 | 顶部货号搜索；按货号折叠，货号行写「N 条记录 · 剩 X · 本单已填 Y」；默认：只有一个货号、或本单已填该货号时展开，其余收起；搜索命中自动展开；「全部展开 / 全部收起」 | UX-D5 |
| 右栏 | 一行一货号的表：# · 货号 · 数量 · 单价 · 预计到货 · 来源（N 条 · M 件，行内折叠展开来源链与「移除」）；无计划来源理由在行下 | UX-D5 |
| 面包屑 | 页内「采购 / SCM-PO-5」，「采购」链 `/purchase?plan=<本单件数最多的来源计划>` | UX-D6 |
| 离开保护 | 有未保存的改动：站内跳转弹「有未保存的改动」（离开 / 留下）；关页 / 刷新走 `beforeunload` | 评审 P1 |
| 新建保存 | 保存成功 `navigate(..., { replace: true })`，浏览器返回不再落到空编辑器 | 评审 P1 |

## 3. 详情 `/purchase/:id`（已下单 / 待处理 / 作废）

- 名字统一：供应商名、店名、仓名、采购员名取编辑器同一套（`/purchase-options` + `/sellers`）；取不到退回编号并点名「取不到，按编号显示」。
- 单头补：采购方 · 采购员 · 质检方式 · 仓；去掉与标题重复的「幂等键」。
- 表头写「N 行 · N 件 · 金额 X」。
- 面包屑同编辑器。★ 已下单打开即 read-back（PB-D27）不变。

## 4. 接口变化

新增 `GET /v1/purchase/plans`（只读 PG，`timed` 日志带耗时；挂在 `api/ui/lines.py`，与 `/plan-lines` 同一个镜像新鲜度依赖）：

```
{ plans: [{ plan_id, title, rev, lines, total, confirmed, ordered,
            orderable_units, orderable_lines,
            drafts: [{ po_id, custom_order_sn, units, open_attention }],
            records: [{ line_id, sku, sku_name, period, state, total, confirmed, ordered, remaining, orderable }] }],
  unplanned_drafts: [{ po_id, custom_order_sn, units, open_attention }],
  excluded: { all_placed: [{ plan_id, title, rev }] } }
```

- `orderable` 与 `/plan-lines?orderable=1` 同一判据（状态 ∈ 已提交 / 已确认 且 remaining > 0），代码共用一处。
- `confirmed` 含草稿、`ordered` 只算已下单（`v_plan_line_po_qty`，不另算）。
- `drafts[].units` = 该草稿里**来自本计划**的来源件数；一张跨计划草稿在每个计划下各出一次。
- 编辑器 / 详情不改接口。

## 5. 测试点

- 后端：草稿组满的计划仍列出且挂草稿；全部下单的计划进 `excluded.all_placed`；`orderable` 与 `/plan-lines?orderable=1` 一致；跨计划草稿两边各一次、件数分开；无来源草稿进 `unplanned_drafts`；契约样本 `purchase-plans.json`。
- 前端模型：透视（同格多记录相加、组满 / 不可组 / 无记录三态分开）；搜索自动展开；计划勾选只勾可组记录。
- 页面：两个按钮只有一个 primary；没有全局全选；`?plan=` 展开该组；折叠标题带张数；dirty 时跳转被拦；新建保存后 replace；续编左栏只列来源计划；详情写名字与合计。
