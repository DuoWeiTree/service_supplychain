# 采购前端（M4）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 采购员在 `web/` 里完成「看待处理 → 组单（草稿）→ ⛔ 下单到领星 → 看结果 / 处理待核实」全流程，不再需要 curl。

**Architecture:** 沿用 web/ 现有结构：`src/api/{types,client,http,mock}.ts` 加采购接口；`src/pages/purchase/*` 三个页面；`src/shell/AppShell.tsx` 加入口与待处理红条；后端只补 `GET /v1/purchase-options` 的候选来源。

**Tech Stack:** React 18 · react-router 6 · Vite · TS strict（`exactOptionalPropertyTypes`）· vitest + jsdom + Testing Library；后端 FastAPI + PG + CH（只读）

**Spec:** `docs/superpowers/specs/2026-10-07-purchase-order-design.md` §8 / §8.1（界面细化）· §6.6（转人工）

## Global Constraints

- 设计系统「供应链工作台」：颜色只用 `var(--…)`（`web/src/styles/tokens.test.ts` 门禁）；不引入 UI 组件库（`00d`、`docs/10:30`）。
- 屏幕给数据，说明进 `<Fold>`；文案只写当场发生的事；按钮动词与 toast / 报告同源；整屏不许出现 ` · ` 拼接（`e2e/copyDiscipline.test.tsx`）。
- 前端只请求后端真实存在的 `/v1` 路由，且路径只写在 `src/api/http.ts` 的 `call()` 里（L9，`tests/test_layering_web.py`）；web 里不许出现 `lingxing` / `app_secret` 等字样（L8）。
- 读接口各记各的错（`Promise.allSettled`）；写动作用 `useInFlight` 防重点、`useActionError` 记错、`ErrorDetail` 显示（含点名字段）。
- 取不到的数用 `<Qty v={{kind:'unknown'}}/>`，不显示 0（空 ≠ 0）。
- ★ mock 数据源只实现读与草稿增删改，**不模拟领星下单**：`place` 抛 `ApiError(501, 'not_in_mock', 'mock 数据源不下单')`；下单结果的界面用 http 打桩测试（PB-D14 精神）。
- `web/` 的测试在 `web/` 目录下跑：`cd web && npx vitest run <file>`；类型检查 `cd web && npx tsc --noEmit`。后端测试同 M1（一次一个 pytest，前台）。
- 不改 `web/package.json`（负责人有未提交改动）；提交只 `git add` 具体路径。
- 不启动 / 不重启负责人的服务（8091 / 5173）。

## Review Focus

1. 草稿有未过的检查时 ⛔ 下单按钮必须禁用并点名哪一闸——不能让人点了再收一个 409。
2. 下单的四种结果（placed / rejected / not_sent / needs_verify）各自落屏正确；needs_verify 必须把人带到红框，而不是只弹一个 toast。
3. 「确认领星无此单」收到 `409 contradicts_evidence` 时，界面必须点名找到过的领星单号，而不是泛泛报错。
4. 领星历史候选取不到时显示「暂不可用」，不显示空下拉（空 ≠ 0）。
5. 两个人同时改同一草稿：后保存者收到 `stale_draft`，界面给「重新载入」，不丢本地输入以外的东西也不静默覆盖。

---

### Task 1: 后端 —— `/v1/purchase-options` 补候选来源

**Files:** Modify `api/ui/purchase_options.py`；Create `dim/ch_source.py` 中 `SQL_PURCHASE_ACTORS` + `fetch_purchase_actors(query)`；Test `tests/test_api_purchase_options.py`、`tests/test_dim_ch_source.py`（追加）

**Interfaces（Produces）:** 响应：
```json
{"suppliers":[{"supplier_id","name"}],
 "warehouses":[{"wid","name"}],
 "purchasers":[{"purchaser_id","source":"本服务"|"领星历史","count":int|null}],
 "opt_uids":[{"opt_uid","name":str|null,"source":"本服务"|"领星历史","count":int|null}],
 "history":{"available":bool,"cause":str|null,"as_of":"YYYY-MM-DD"|null}}
```
- `warehouses`：PG `warehouse` 镜像 `kind='local'`，按 `wid`。
- 领星历史：CH `jxd_raw.lingxing_purchase_order_list`，按 `order_sn` 取最新一行后 `GROUP BY purchaser_id` / `GROUP BY opt_uid, opt_realname`，计数降序（实测：purchaser 111 = 135、185 = 8；opt_uid 10925905 陈彩凤 = 143，spec §2）。
- 两源合并去重：同一 id 同时出现时 `source` 取「本服务」。
- ★ CH 连不上 / 报错：`history.available=false`、`cause` 填 `ch_client.describe_failure` 的分类与原因，其余字段照常返回；记 WARNING（日志三问）。**不得 503 整个接口。**
- 删掉现响应里的 `note` 文案（说明进文档，不进响应）。

- [ ] Step 1: 写失败测试 —— ① 有 PG 仓 + 本服务已建单 + 打桩 CH 返回历史 → 合并结果、source 标注、计数；② CH 抛真连接错误（用 `tests/helpers.make_ch_unreachable`）→ `history.available=false` 且 `cause` 非空、其余照常；③ `fetch_purchase_actors` 对空结果的处理沿用 `_nonempty` 口径之外的「历史为空不是错误」——空返回 `[]` 且 `available=true`
- [ ] Step 2: 实现；Step 3: 跑 `tests/test_api_purchase_options.py tests/test_dim_ch_source.py tests/test_layering.py`；全量一次；ruff；提交

### Task 2: 前端数据层 —— 采购接口类型、http、mock、fixtures

**Files:** Modify `web/src/api/{types.ts,client.ts,http.ts,mock.ts}`；Create `web/src/api/fixtures/purchase*.json`；Test `web/src/api/http.test.ts`、`web/src/api/mock.test.ts`（追加）

**Interfaces（Produces，名称固定，后续任务按此调用）:**
`SupplyChainApi` 新增：
`listPurchaseOrders(state?: '草稿'|'已下单')` · `getPurchaseOrder(poId)` · `createPurchaseOrder(body: PoBody)` → `{po_id}` · `updatePurchaseOrder(poId, body: PoBody & {draft_version:number})` → `{po_id,draft_version}` · `deletePurchaseOrder(poId)` · `getPreflight(poId)` · `placePurchaseOrder(poId, confirmToken)` → `PlaceResult` · `getTimeline(poId)` · `recheck(poId)` · `registerExisting(poId, orderSn)` · `confirmAbsent(poId, reason)` · `acknowledgeMismatch(poId, reason)` · `listAttention()` · `getPurchaseOptions()` · `getPurchaseChecks()` · `listCandidateLines(params: {plan_ids?: number[]; sku?: string})`（固定带 `orderable=1`）· `getLineTrace(lineId)`

类型逐字段照后端（字段与路径见 spec §8.1 与 `docs/08` 采购接口表；价格为字符串）。`PlaceResult` 为 `outcome` 判别联合：`placed | rejected | not_sent | needs_verify`。

- [ ] Step 1: http.test 逐方法断言 method / URL / body / x-actor；一个 `409 contradicts_evidence` 的错误体被解析成 `ApiError` 且 `fields.found_order_sns` 可读
- [ ] Step 2: mock：内存实现读与草稿增删改（含 `over_seller_cap` / `stale_draft` 两个闸的复刻，形状同后端）；`placePurchaseOrder` 抛 `ApiError(501,'not_in_mock','mock 数据源不下单')`；mock.test 覆盖这三点
- [ ] Step 3: `npx tsc --noEmit`；`npx vitest run src/api`；后端 `tests/test_layering_web.py`（L9 路由比对）一次；提交

### Task 3: 外壳 —— 入口、待处理红条、Modal 警示参数化

**Files:** Modify `web/src/shell/AppShell.tsx`、`web/src/components/Modal.tsx`、`web/src/routes.tsx`；Create `web/src/shell/AttentionBanner.tsx`；Test `web/src/shell/AttentionBanner.test.tsx`、`web/src/components/Modal.test.tsx`（追加）

- 顶栏品牌位：「销售计划」（`/`）「采购」（`/purchase`）两个链接，当前所在高亮。
- `AttentionBanner`：挂载即 `listAttention()`；`count>0` → 红条「⛔ {count} 张采购单需要人工处理」链 `/purchase`，无关闭按钮；取数失败 → 红条位显示「待处理数取不到」+ `ErrorDetail`（不隐藏，空 ≠ 0）；`count=0` → 不渲染。导出 `refreshAttention()` 供写动作后调用。
- `Modal`：新增可选 `warn?: string`；`danger` 且未给 `warn` 时保持现有文案（不破 `PlanRevs`）。
- 路由占位：`/purchase`、`/purchase/new`、`/purchase/:poId`（页面组件在 Task 4~6 落地，先挂空页避免 L9 / copyDiscipline 漂移）。

- [ ] Step 1: 失败测试（红条三态、Modal warn 参数、导航高亮）· Step 2: 实现 · Step 3: tsc + vitest（shell / components / e2e/copyDiscipline）· 提交

### Task 4: 采购首页 `/purchase`

**Files:** Create `web/src/pages/purchase/PurchaseHome.tsx` (+ `.test.tsx`)；Modify `web/src/routes.tsx`、`web/src/e2e/copyDiscipline.test.tsx`（加入 screens）；Create `web/src/e2e/sameScreenPurchase.test.tsx`

- 并发取 `listAttention` / `listPurchaseOrders` / `getPurchaseChecks`，各记各的错。
- 待处理区（仅有项时）：每项 = 类型文字（`needs_verify` 下单结果待核实 / `landed_mismatch` 领星明细不一致 / `stuck_call` 下单请求卡住）、单号、打开链接。
- 计数：草稿 N、已下单 N（`Qty`）；核对：`ok` → 「核对全部通过」；否则「核对有 N 项不一致」+ `<Fold>` 列 `violations`。
- 表格列：单号（`custom_order_sn`）、供应商、状态、领星单号（无则 `—`）、行、件数、待处理（>0 用 danger chip）、建单人；行点击进 `/purchase/:poId`。
- 「新建采购单」按钮 → `/purchase/new`。
- sameScreen：mock 与 http 打桩（按后端规则写桩）渲染同屏。

- [ ] Step 1: 失败测试 · Step 2: 实现 · Step 3: tsc + vitest（页 + e2e）· 提交

### Task 5: 采购单编辑器（新建 / 草稿）

**Files:** Create `web/src/pages/purchase/PoEditor.tsx`、`poEditorModel.ts`（纯函数：来源合并、无计划来源量、预填数量、本地校验）(+ tests)；Modify `routes.tsx`

- 单头：供应商（`suppliers`）、采购方 / 采购员（下拉 = `purchasers` / `opt_uids`，选项文字带来源「领星历史」铅笔灰；允许手填数字）、质检方式（0 仓库质检 / 1 预检 / 2 免检）。`history.available=false` → 下拉旁写「领星历史暂不可用」。
- 左栏「可组单记录」：`listCandidateLines({})` 按货号分组；每条记录显示计划号、月份、剩余 / 总量；每店一行：店、剩余、数量输入、「加入」。`blocked` 的记录灰显并写原因（`no_seller_basis` → 「无分店依据：{sellers}」）；`excluded` 计数在分组头下方用 `.dropline` 列出。
- 右栏「明细」：按（货号, 仓）合并；仓下拉 = `warehouses`；数量默认 = 来源合计；数量 > 来源合计 → 出现「无计划来源 {n} 件」+ 理由输入；单价、预计到货；每条来源可移除。
- 底部：下单前检查（新建时用本地 `poEditorModel.validate`；已保存的草稿用 `getPreflight`）逐闸列出，未过红色点名。
- 按钮：「保存草稿」（新建 → `createPurchaseOrder` 后跳 `/purchase/:poId`；草稿 → `updatePurchaseOrder` 带 `draft_version`）；「删除草稿」（二次确认，非 danger 级）；`stale_draft` → `ErrorDetail` + 「重新载入」。
- toast 动词同源：保存草稿 → 已保存草稿；删除草稿 → 已删除草稿。

- [ ] Step 1: `poEditorModel` 纯函数测试先红（合并、无计划来源、校验列表）· Step 2: 页面测试（加入来源 → 明细出现；blocked 记录可见不可加；stale_draft 给重新载入）· Step 3: 实现 · Step 4: tsc + vitest · 提交

### Task 6: ⛔ 下单确认 + 采购单详情 + 转人工动作

**Files:** Create `web/src/pages/purchase/PlaceDialog.tsx`、`PoDetail.tsx`、`AttentionPanel.tsx` (+ tests)；Modify `PoEditor.tsx`（挂 ⛔ 下单）、`routes.tsx`（`/purchase/:poId` 按状态分发编辑器 / 详情）

- ⛔ 下单按钮：右对齐、与其他按钮分开；草稿 `preflight` 有未过 → 禁用 + 写「{闸} 未过」。
- `PlaceDialog`：`<Modal danger warn="这一步会在领星建出真实采购单，不可撤销" title="⛔ 下单到领星">`；正文 = 供应商、采购方、采购员、质检方式、幂等键、逐行（货号、仓、数量、单价、其中无计划来源）、合计件数与金额；「确认下单」（danger）/「取消」。确认 → `placePurchaseOrder(poId, confirm_token)`：
  - `placed` → toast「已下单」+ 进详情；`rejected` → 弹窗内显示领星 code / msg，单仍是草稿；`not_sent` → 「没有发到领星，可以再次下单」；`needs_verify` → 关弹窗进详情（红框展开）；`409 stale_confirm_token` → 「草稿已变，请重新打开下单」。
  - 每次写动作后 `refreshAttention()`。
- `PoDetail`：头部两轴（本地状态 | 领星状态「未读回」）、领星单号、幂等键；明细带领星行号与来源（来源链到记录反查，显示 `getLineTrace` 的已确认 / 已下单）。
- `AttentionPanel`（有未处理项时置顶红框）：结论（`conclusion.known` / `unknown` / `last_lookup`）→ 时间线表（时间、人、接口、耗时、结果、cause；请求 / 响应体进 `<Fold>`）→ 动作：
  - `needs_verify`：「重新核对」「登记已有单」（单号输入）「确认领星无此单」（理由必填）；`409 contradicts_evidence` → 点名 `found_order_sns`；`409 lookup_in_progress` → 「查询进行中，稍后再试」。
  - `landed_mismatch`：问题行表 + 「确认知悉」（理由必填）+ 一行提示「去领星改对后点重新核对」（PB-D16）。
  - `stuck_call`：「等待自动恢复」+ 开始时间，无按钮。
- 测试用 http 打桩覆盖四种下单结果与 contradicts_evidence 点名；mock 下点确认下单显示 `not_in_mock` 错误。

- [ ] Step 1: 失败测试 · Step 2: 实现 · Step 3: tsc + vitest（pages/purchase + e2e）· 全量后端 `tests/test_layering_web.py` 一次 · 提交

### Task 7: 文档

**Files:** `docs/10-前端交互界面.md`（§2 页面表加 web 采购三页；§2.6 delta 加「采购前端」一行；不改 §2.5 原型流程表——它由 `test_doc_flow.js` 对核冻结原型）· `docs/08`（`/purchase-options` 新响应形状）· `web/README.md`（采购页面入口一句）

- [ ] Step 1: 改文档 · Step 2: 四项文档门禁全绿 · Step 3: 提交
