# 排货按去向分表 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 排货表建时选去向（平台仓 · FBA / 海外仓），表内只排本去向，各自确认、各自提交。

**Architecture:** `sheet.route` 列 + 库层触发器 / 部分唯一索引强制（迁移 021）；usecase 按去向过滤闸与提交预览；web 首页两个入口、页头徽标、加一格只列本去向目的地。

**Tech Stack:** PostgreSQL plpgsql · FastAPI · React/Vite/TS · pytest · vitest

**Spec:** `docs/superpowers/specs/2026-10-12-dispatch-route-split-design.md`

## Global Constraints

- 迁移已执行不可改；新迁移 `migrations/pg/021_dispatch_route.sql`，不写 `scm.` 前缀、不写 BEGIN/COMMIT。
- 约束名：`sheet_route_known` · `sheet_needs_route` · `sheet_route_fixed` · `arrangement_route_mismatch` · `sheet_route_missing` · `sheet_one_draft_per_plan_route`。
- 接口错误码：`missing_required`（缺 route）· `draft_exists`（409，带 `sheet_id`）· 阻断项 `route_not_submittable` / `legacy_mixed`。
- 不适用的闸不列：`fba` 表 G1~G6，`oversea` 表 G3~G7，`route = null` 全列。
- 每条新测试先红一次；日志三问；界面只写当场发生的事。

## Review Focus

1. 旧版混合表 / 空草稿（route NULL）：撤回格子（DELETE）仍要能做，取消要能做，写格子被拒。
2. 同计划同去向第二张草稿：库层撞唯一索引时接口给 409 `draft_exists` 而不是 500。
3. 已确认的表不占草稿名额：确认后能立刻按同去向再开一张。
4. 计划还差两去向共用：FBA 表排 30，海外仓表 grid 的还差少 30。
5. 迁移回填对含已放弃 / 已取消排货单的历史表也按渠道归类（不只看活排货单）。

---

### Task 1: 迁移 021 + DDL 测试

**Files:** Create `migrations/pg/021_dispatch_route.sql` · `tests/test_ddl_021_route.py`；Modify `tests/test_ddl_016_dispatch.py::new_sheet`（带 route）。

- [ ] 写测试：R1 无 route 建表 → `sheet_needs_route`；R2 改 route → `sheet_route_fixed`；R3 fba 表写海外仓格子 / oversea 表写 FBA 格子 → `arrangement_route_mismatch`；R5 route NULL 表写格子 → `sheet_route_missing`、删格子可行；R4 同计划同去向两张草稿 → `sheet_one_draft_per_plan_route`，不同去向并存、已确认不占名额。
- [ ] 跑：FAIL（列不存在）。
- [ ] 迁移：加列 + CHECK；回填（按 `consignment.channel` ∪ `dispatch_arrangement.channel` 去重，单一 → 该值，否则 NULL；`RAISE NOTICE` 计数）；`sheet_guard` 覆盖加 R1/R2；新触发器 `t_arrangement_route`（BEFORE INSERT OR UPDATE）做 R3/R5；R4 部分唯一索引（`state='草稿' AND route IS NOT NULL`）。
- [ ] 跑：PASS；`test_ddl_016/017/020` 不回归。

### Task 2: 后端

**Files:** Modify `repo/dispatch.py`（create/list/read/plans 带 route）· `usecase/sheet.py`（create 校验、`draft_exists`、preflight/confirm 按去向过滤闸）· `rules/dispatch.py`（`gates_for(route, gates)`）· `usecase/submit_ows.py`（阻断项）· `api/ui/errors.py`（新约束名）· tests（`test_api_sheets.py` 的 `new_sheet` 带 route；新用例）。

- [ ] 测试：建表缺 route 400；第二张同去向草稿 409 `draft_exists` 带 sheet_id；详情 / 列表 / plans.sheets 带 route；grid 带 `sheet.route`；fba 表 preflight 只含 G1~G6、oversea 表只含 G3~G7；fba 表 submission 阻断 `route_not_submittable`；还差两去向共用。
- [ ] 实现 → PASS；`test_api_submit_ows` / `test_e2e_c_dispatch_draft` / `test_web_contract`（重写样本）不回归。

### Task 3: 前端

**Files:** `web/src/api/{types,client,http}.ts`（`createSheet(planId, route)`、`route` 字段）· `pages/dispatch/DispatchHome.tsx`（两个按钮 / 接着 #N / 去向列）· `pages/dispatch/SheetPage.tsx`（页头徽标、AddCell 目的地按去向、fba 底部文案、混合表只给取消）· `dispatchGridModel.ts`（`destOptions` 按去向）· tests。

- [ ] 测试先红 → 实现 → `npm test` + `tsc` 全绿。

### Task 4: 文档 · e2e · 全量 · 应用迁移

- [ ] `02` `03` `04` `08` `10` `15` `00c` 改；spec 状态。
- [ ] e2e：同计划开 FBA 表与海外仓表、还差共用、海外仓表提交 → 取消。
- [ ] 全量 py / web / 文档闸；`uv run python -m migrations.pg.apply` 应用 021 到 scm，核对 #2 / #5 的回填结果。
