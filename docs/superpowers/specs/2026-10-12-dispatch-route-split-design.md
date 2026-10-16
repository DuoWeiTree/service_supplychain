# 排货按去向分表：平台仓 / 海外仓 —— 设计

| | |
|---|---|
| 日期 | 2026-10-12 |
| 状态 | 负责人已批准（「好的。执行」）· 已实现（计划 `superpowers/plans/2026-10-12-dispatch-route-split.md`，迁移 021）· 待手动验收 |
| 依据 | `00` CA-D13（10-12 负责人三问三答 + 「好的」）· C-a spec `2026-10-10-dispatch-draft-design.md` · C-b spec `2026-10-12-dispatch-submit-ows-design.md` |
| 推翻 | CA-D5「逐格任选 FBA 或海外仓」→ 只在本表去向内选；CA-D11「一计划一表」→ 一计划每种去向一表 |
| 不在本篇 | FBA 提交（C-c，STA）· Walmart WFS 的接入（只预留取值） |

---

## 1. 为什么

负责人（10-12）：「走海外仓还是平台仓在备货时要分开进行……调用的接口与流程不同……必须在交互中区分。」

实际踩到的：一张表里 FBA 与海外仓混排，「加一格」目的地预选 FBA，负责人排完 77 件以为是海外仓，确认后才发现没有领星备货单可提交（FBA 提交属 C-c，未做）。去向混在格子层，流程差异（装箱 / 物流方式 / 提交接口）只能在确认之后才显形。

## 2. 去向（route）

| 取值 | 名字 | 目的地 | 提交流程 |
|---|---|---|---|
| `fba` | 平台仓 · 亚马逊 FBA | 本店 FBA（`dest_sid`） | STA（C-c，未做） |
| `oversea` | 海外仓 | 海外仓 `dest_wid` | 领星海外仓备货单（C-b） |
| （预留）`wfs` | 平台仓 · Walmart WFS | — | 未定，加取值时另起 spec |

★ 平台仓表绑定**一个平台**（CA-D13）：一张表 = 一条提交流程。
★ `route` 取值与排货单 `channel` 同一套词（`fba` / `oversea`），表的去向 = 表内每张排货单的渠道。

## 3. 规则

| # | 规则 | 由谁强制 | 出处 |
|---|---|---|---|
| R1 | 新建排货表必须带 `route` | 库：`sheet_needs_route`（INSERT 触发器）· 接口 400 `missing_required` | CA-D13 |
| R2 | `route` 建后不可改 | 库：`sheet_route_fixed` | CA-D13 |
| R3 | 格子的渠道必须等于本表去向 | 库：`arrangement_route_mismatch`（`dispatch_arrangement` 触发器）· 前端只列本去向的目的地 | CA-D13 |
| R4 | 同一计划、同一去向最多一张**草稿** | 库：部分唯一索引 `sheet_one_draft_per_plan_route`（`state = '草稿'`）· 接口 409 `draft_exists` 带 `sheet_id` | 负责人 10-12 确认 |
| R5 | `route` 为空 = 旧版混合表：不能写格子、不能提交，只能看与取消 | 库：R3 触发器对 `route IS NULL` 一律拒（`sheet_route_missing`）· 提交预览阻断项 `legacy_mixed` | CA-D13 |
| R6 | 计划的还差两种去向共用一个数 | 不变（`v_plan_line_dispatch_qty` 按记录汇总，不分去向） | 承重墙② |

## 4. 迁移 `021_dispatch_route.sql`

1. `ALTER TABLE sheet ADD COLUMN route text CONSTRAINT sheet_route_known CHECK (route IN ('fba','oversea'))`（可空：只给旧表）。
2. 回填（★ 迁移内统计并 `RAISE NOTICE` 每类张数，不静默）：
   - 表内（含已放弃 / 已取消的排货单与安排）只出现一种渠道 → 该渠道；
   - 两种都有 → NULL（旧版混合表）；
   - 一种都没有（空表）→ NULL。★ 不猜去向：空的草稿由 R5 只许取消，负责人按去向重开新表即可（R4 的唯一索引不计 NULL，不挡新表）。
3. R1 / R2 触发器；R3 加进 `dispatch_arrangement` 现有校验触发器；R4 部分唯一索引。
4. 已执行的迁移不改（CLAUDE.md）。

## 5. 接口（`08` §1.3 增改）

| 方法 | 路径 | 变化 |
|---|---|---|
| POST | `/v1/sheets` | `{plan_id, route}`；`route` 必填 ∈ `fba` / `oversea`；同计划同去向已有草稿 → 409 `draft_exists`（带 `sheet_id`） |
| GET | `/v1/sheets` · `/v1/sheets/{id}` · `/grid` | 带 `route`（`null` = 旧版混合表） |
| GET | `/v1/dispatch/plans` | `sheets[]` 每项带 `route` |
| GET | `/v1/sheets/{id}/preflight` · confirm | 闸按去向列：`fba` 表 G1~G6，`oversea` 表 G3~G7（★ 不适用的闸不列，不是「过」） |
| GET | `/v1/sheets/{id}/submission` | `fba` 表：`orders: []` + 阻断项 `route_not_submittable`（`detail`：C-c 未做）；旧版混合表：`legacy_mixed` |

## 6. 界面（`10` §2.0b 增改）

- `/dispatch` 计划行：两个按钮 **平台仓备货 · FBA** / **海外仓备货**；该去向已有草稿 → 「接着 #N」。排货表 chip 带去向。全部排货表多一列「去向」。
- 排货表页头：去向徽标（「平台仓 · 亚马逊 FBA」/「海外仓」/「旧版混合表」）。
- 加一格：`fba` 表目的地只有「FBA 本店」，自动带上；`oversea` 表列本市场海外仓，**不预选**。
- 底部：`fba` 表确认后写「提交到亚马逊（STA）还没做」，无领星按钮；`oversea` 表照 C-b。旧版混合表只给「取消排货表」。

## 7. 测试（先红）

| 层 | 用例 |
|---|---|
| DDL | R1 无 route 建表被拒；R2 改 route 被拒；R3 FBA 表写海外仓格子被拒（反之亦然）；R4 第二张同去向草稿被拒、不同去向可并存、已确认不占名额；R5 NULL route 表写格子被拒、取消可行；回填三类各一例 |
| API | 建表缺 route 400 / `draft_exists` 409；grid 目的地只列本去向；preflight 按去向列闸；fba 表 submission 阻断 `route_not_submittable` |
| Web | 首页两个按钮与「接着 #N」；页头徽标；fba 表加一格目的地自动带上、oversea 表不预选；fba 表底部无提交按钮并写明原因 |
| e2e | 一个计划同时开 FBA 表与海外仓表：两表各排一部分，还差共用；海外仓表确认 → 提交领星 → 取消；FBA 表确认后无提交 |

★ 每条测试先弄失败一次（CLAUDE.md）。

## 8. 文档

`00` CA-D13（已登记）· `02` 排货表对象加「去向」· `03` §5 sheet 列与约束 · `04` 排货表约束 R1~R5 · `08` §1.3 · `10` §2.0b · `15` §5（逐格目的地改为表级去向）· `00c` 作废说法登记「逐格任选 FBA 或海外仓」「一计划一表」· `00e` 文档地图。
