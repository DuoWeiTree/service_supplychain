# C-a 备货草稿 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 排货员在一张需求网格里，把未完成计划（含关联采购单进度）与本地仓可发库存（含无计划来源）按 msku 安排到目的地（FBA 店 / 海外仓），系统派生排货单，过六闸后确认或取消；零出站调用。

**Architecture:** 库层持有全部不变量（白名单状态机 · 冻结 · 承重墙② · 延迟量约束），照 `010`/`014` 的形状；纯规则 `rules/dispatch.py` 做派生与闸；`dim/ch_stock.py` 按需读 CH 本地库存与采购行项（不做镜像）；`usecase/sheet.py` 管事务；`api/ui/sheets.py` 只做翻译；前端 `web/src/pages/dispatch/`。

**Tech Stack:** PostgreSQL（schema `scm`，迁移 `migrations/pg/NNN_*.sql`）· ClickHouse 只读 · Python 3 / FastAPI / psycopg2 · React 18 + Vite + vitest。

**Spec:** `docs/superpowers/specs/2026-10-10-dispatch-draft-design.md`

## Global Constraints

- 迁移不写 `scm.` 前缀、不写 BEGIN/COMMIT；已执行的迁移不可改（`apply.py` 核 checksum）。
- 命名禁用 `inbound` · `shipment` · `shipment_plan` · `allocation`；我们的词：排货表 sheet · 排货单 consignment · 排货行 consignment_line。
- 只追加表禁 UPDATE/DELETE（`forbid_update_delete()`）。
- 每个数字指到 17/18 或 spec §4 实测表，否则标「设计取值 · 未实测」；未决项进 `00`，不在代码里定。
- 空 ≠ 0：可发量读不到 = 未知（`None`），G3 = 无法判定，不放行。
- 统计被丢掉的一侧：网格响应带 `excluded{}`；CH 读取带 `dropped`。
- 日志三问（打的谁 · 多久 · 怎么失败，带 cause kind）；写操作 `op=… actor= ms=`。
- 界面文案只写当场发生的事；说明进 `Fold`。
- 测试先红一次；纯层不连网（`tests/conftest.py` 自动归类）。
- 零出站调用：本刀不 import `erp.*`。

## Review Focus

1. 同一格子重写（改量 / 改目的地）后，旧排货单被清空时要被删除，装箱孤儿要点名 —— `test_rules_dispatch.py::test_diff_names_orphan_packing`。
2. 计划行在别的排货表已排未发时，本表再排超总量 → 409 `over_total`（并发也只放一个）—— `test_api_sheets.py::test_concurrent_cells_same_line_one_409`。
3. CH 库存读不到 → 网格照常返回需求侧、库存列为 `null` 并带原因；确认 503 —— `test_api_sheets.py::test_grid_survives_stock_unavailable`。
4. 店铺桶属于别店 / 不在店铺镜像 → 不可用且计入 `excluded` —— `test_dim_ch_stock.py::test_unknown_seller_bucket_excluded`。
5. 已确认的表被取消后，已排未发释放、计划行待排量回升 —— `test_ddl_016_dispatch.py::test_cancel_confirmed_releases_committed`。

---

### Task 0: 文档先行

**Files:** `docs/00-待裁定清单.md` · `02` · `03` · `04` · `15` · `06` · `08` · `09` · `10` · `00e` · `00c`

- [ ] `00` 新增 CA 组：CA-D1..D9 原文 + Q9/Q10/Q12/Q13/Q16-Q20 待裁定；PB-U1 标结案（CA-D2）；B4 标被 CA-D2 推翻。
- [ ] `02`：排货行键改 msku（`:474`）；承重墙② 可空 + CA-D2 理由（`:749-760`）；§3.1 加「无计划来源」与本地库存桶（CA-D8）。
- [ ] `03` §5：DDL 改为指向迁移 016/017 的摘要；装箱单独成表；B4 措辞替换。
- [ ] `04`：§4.2 / §7.6b 白名单（S3：`[*]→草稿`·`草稿→已确认`·`草稿→已取消`·`已确认→已取消`；S4 加 `已确认→已取消`、`[*]→草稿`）；B4 相关行。
- [ ] `15`：§2.1 键、§2.2 安排→排货行 N:1、§5.1/5.2/5.3 逐格目的地（CA-D5）、§6 G1 仅 FBA、G3 按桶。
- [ ] `06:235`、`08` §1.3、`09` UC-D2/D4、`10` 阶段 C 屏幕：按 spec §6/§7 改。
- [ ] `00e`：阶段 C 拆 C-a/b/c；判据②含无计划来源；§5.2 登记 spec 与本计划；§8 进度。
- [ ] `00c` §5 作废说法：「没有计划单记录的禁止排货」·`no_plan_line_for_demand`。
- [ ] 跑 `node test_doc_index.js test_retired_claims.js test_doc_flow.js test_mermaid.js`（在 `docs/prototype/`）全绿。

### Task 1: 维度补齐（015）

**Files:** Create `migrations/pg/015_dispatch_dims.sql` · Modify `dim/ch_source.py`（`SQL_SKU_CATALOG`/`fetch_sku_catalog` 加 `id`；`SQL_MSKU_BRIDGE`/`fetch_msku_bridge` 加 `fnsku`；`SQL_WAREHOUSE`/`fetch_warehouse` 加 `country_code` → 海外仓 market）· `dim/registry.py` columns · Test `tests/test_dim_ch_source.py`、`tests/test_ddl_015_dispatch_dims.py`

**Interfaces:** Produces 列 `sku_catalog.product_id bigint NULL` · `msku_bridge.fnsku text NULL` · `warehouse.market` 对 `oversea_*` 填代码（`GB→UK`，空 → NULL，认不出 → `UnknownShape`）。

```sql
ALTER TABLE sku_catalog ADD COLUMN IF NOT EXISTS product_id bigint;
ALTER TABLE msku_bridge ADD COLUMN IF NOT EXISTS fnsku text;
```

- [ ] 先写测试：`fetch_sku_catalog` 回放行带 id → 行第 3 列；`fetch_msku_bridge` 空 fnsku → None（不是 ''）并计数 `fnsku_null`；`fetch_warehouse` 海外仓 `GB` → `UK`、本地仓恒 None、未知代码硬失败。跑红。
- [ ] 改 SQL 与 fetch、registry columns、迁移；跑绿；`tests/test_mirror_registry*.py` 绿。

### Task 2: CH 本地库存与采购行项读取（`dim/ch_stock.py`）

**Files:** Create `dim/ch_stock.py` · `tests/test_dim_ch_stock.py` · Modify `api/ui/source_factory.py`（`make_stock()` + `stock_dep`）

**Interfaces:**
```python
class StockSnapshot(NamedTuple):
    as_of: dt.date
    buckets: dict[tuple[str, int], dict[str, int]]   # (sku, wid) -> {"shared": n, sid: n}
    dropped: dict                                     # {"not_local_wid": rows, "unknown_seller": {"rows", "units"}}
    rejected: tuple[dict, ...]
class PoItemProgress(NamedTuple):   # 按 (order_sn, sku)
    entered: int; pending: int; captured: dt.date
class ChStock:
    def __init__(self, query, *, local_wids: set[int], known_sids: set[str],
                 classify_failure=..., lookback_days=7, settle_minutes=30, drop_threshold=0.30,
                 min_rows=7927, min_wids=57): ...
    def snapshot(self, skus: set[str]) -> StockSnapshot   # raises ChUnavailable / ChDataUnusable
    def po_progress(self, order_sns: set[str]) -> dict[tuple[str, str], PoItemProgress]
```
- `SQL_STOCK_DAYS`：近 lookback 天每日 `count(), uniq(wid), settled`；复用 `pick_snapshot_date`（第 3 列传 uniq(wid)）。`min_rows=7927`、`min_wids=57` 出处 spec §4 实测表（09-28~10-10 最小值）。
- `SQL_STOCK`：`_captured_date = as_of AND sku IN {skus:Array(String)}`，返回 `wid, sku, seller_id, sum(product_valid_num)`。
- `seller_id ∈ {'0',''}` → `shared`；在 `known_sids` → 该 sid；否则计入 `dropped.unknown_seller`；wid 不在 `local_wids` → `dropped.not_local_wid`。
- `SQL_PO_ITEMS`：`lingxing_purchase_order_list_items` 最新采集日，`order_sn IN {sns}`，`sum(quantity_entry), sum(quantity_receive)` by `order_sn, sku`。
- 日志：`op=ch_local_stock outcome=ok target= as_of= rows= dropped= elapsed_ms=`；失败 `outcome=fail cause=`。

- [ ] 测试（纯，注入 query）：共享/店铺/未知店分桶；非本地仓丢弃计数；被拒采集日回退且留痕；CH 抛错 → `ChUnavailable` 带 target；行项进度映射。跑红 → 实现 → 绿。
- [ ] `tests/test_dim_ch_stock_live.py`（integration）：真跑三条 SQL，行宽对、`as_of` 非空。

### Task 3: 本域表与状态机（016）

**Files:** Create `migrations/pg/016_dispatch.sql` · `tests/test_ddl_016_dispatch.py` · Modify `tests/conftest.py`（DATA_TABLES 加新表）

**Interfaces（表/约束名，后续任务与 `errors.py` 依赖）：**
- `sheet(sheet_id, state, watermark_at_build, watermark_at_confirm, created_by, created_at, updated_at)`；`sheet_transition`、`sheet_event(seq, sheet_id, from_state, to_state, actor, reason, src, at)`。约束名：`sheet_frozen`、`sheet_born_as_draft`、`sheet_cancel_needs_reason`。
- `dispatch_arrangement(arrangement_id, sheet_id, line_id NULL, sku, seller_sku, sid, from_wid, channel, dest_wid, dest_sid, units, ledger_seq, updated_by, updated_at)`；部分唯一索引 `arrangement_planned_cell`、`arrangement_unplanned_cell`（键含 channel 与目的地）；约束名：`arrangement_line_not_dispatchable`、`arrangement_sku_mismatch`、`arrangement_msku_not_this_sku`、`arrangement_seller_not_in_line`、`arrangement_from_not_local`、`arrangement_invalid_destination`、`arrangement_units_positive`、`dispatch_over_total`（延迟）。
- `consignment(consignment_id, sheet_id, channel, dest_wid, dest_sid, our_stage, idem_key)`；`consignment_transition`、`consignment_event`；`consignment_one_dest` CHECK；部分唯一 `consignment_one_per_dest_wid/sid`。
- `consignment_line(consignment_id, seller_sku, sid, from_wid, sku, units)`；`dispatch_source_map(arrangement_id PK, consignment_id, seller_sku, sid, from_wid, line_id NULL, units)`。
- `consignment_packing(sheet_id, seller_sku, sid, from_wid, box_count, box_len, box_wid, box_hei, box_weight, units_per_box, updated_by, updated_at)`。
- `sheet_gate_run(run_id, sheet_id, actor, ok, seen_watermark, ledger_at, stock_as_of, stock_rejected, gates, at)` 只追加。
- 白名单：S3 `[*]→草稿`·`草稿→已确认`·`草稿→已取消`(需理由)·`已确认→已取消`(需理由)；S4 `[*]→草稿`·`草稿→已确认`·`已确认→已投送`·`已确认→草稿`(C-a 由触发器拒，约束 `consignment_back_needs_dispatch`)·`已投送→已归档`·`草稿→已放弃`·`已确认→已取消`。
- 排货表 → 排货单状态联动：表确认时同事务推所有单 `草稿→已确认`；表取消时同事务推 `草稿→已放弃` / `已确认→已取消`。
- 函数 `assert_line_dispatch_qty(line_id)`：`FOR NO KEY UPDATE` 锁记录，`staged + dispatched > total` → `dispatch_over_total`。

- [ ] 测试（integration，每条先红）：出生事件；状态只能经事件推进；确认后 arrangement/packing 增删改全拒（`sheet_frozen`）；line_id 为空可写；line 状态不在 {已下单, 准备排货} 拒；sid 不在 demand_by_seller 拒；msku 属别的货号拒；来源仓非本地拒；oversea 目的地非海外仓拒、fba dest_sid≠sid 拒；两表合计超总量在提交时拒；取消需理由；已确认取消释放；`已确认→草稿` 在 C-a 拒；白名单行集合 == `04` §7.6b 表（解析文档表格比对）；sheet_event / consignment_event / sheet_gate_run 只追加。
- [ ] 实现迁移；`uv run pytest tests/test_ddl_016_dispatch.py tests/test_migrations.py -q` 绿。

### Task 4: 视图（017）

**Files:** Create `migrations/pg/017_dispatch_views.sql` · `tests/test_ddl_017_views.py` · Modify `api/ui/checks.py`

**Interfaces:**
- `v_plan_line_dispatch_qty(line_id, total_units, state, staged_units, dispatched_units, pending_units)` —— 排货单 `our_stage ∈ {草稿,已确认}` = staged，`∈ {已投送,已归档}` = dispatched。
- `v_stock_committed(sku, from_wid, sid, planned_units, unplanned_units, units)` —— 排货单 ∈ {草稿, 已确认}，跨表。
- `v_check_dispatch(problem, sheet_id, consignment_id, line_id, detail jsonb)`：`map_ne_arrangement` · `line_ne_sum_map` · `over_total` · `msku_sku_drift`。
- `GET /v1/checks/dispatch` 同 `checks.py` 现有形状。

- [ ] 测试：正常流程四类均空；直接写坏（绕过触发器用 `session_replication_role=replica` 只在测试里）各自非空；取消后 staged 回 0。红 → 绿。

### Task 5: 纯规则（`rules/dispatch.py`）

**Files:** Create `rules/dispatch.py` · `tests/test_rules_dispatch.py`

**Interfaces:**
```python
Cell = TypedDict(line_id: int|None, sku, seller_sku, sid, from_wid: int, channel: "fba"|"oversea",
                 dest_wid: int|None, dest_sid: str|None, units: int)
def dest_key(cell) -> tuple[str, int|str]
def derive(cells: list[Cell]) -> dict   # {"consignments": {dest_key: {"lines": {(seller_sku,sid,from_wid): {"sku","units","planned","unplanned"}}}}}
def diff(old: dict, new: dict, packing_keys: set) -> dict   # created/removed/changed dest_keys + orphan_packing
def bucket_feasible(need_by_sid: dict[str,int], buckets: dict[str,int]) -> tuple[bool, int]  # (ok, shared_overflow)
def available_for(sid, need_by_sid, buckets) -> int
def gates(*, consignments, packing, committed, stock, stock_error, ledger_conflicts) -> list[dict]
    # 永远 6 项：{"gate","ok": True|False|None,"code","detail","fix"}
```
- [ ] 测试：`15:254-255` 两例（同店同货号两仓 → 两行；两店同海外仓 → 一单两行）；计划+无计划同格 → 一行、planned/unplanned 分开计；diff 点名孤儿装箱；桶可行（own 足 / 溢出共享 / 共享不足）；`available_for`；G1 只查 FBA 行且齐全；G3 `stock=None` → `ok=None`；永远 6 项；G5 `ok=None` 文案「待裁定·未检查」。红 → 绿。

### Task 6: 仓储 + 用例 + 接口（建表 / 网格 / 格子）

**Files:** Create `repo/dispatch.py` · `usecase/sheet.py` · `api/ui/sheets.py` · `tests/test_api_sheets.py` · Modify `api/__init__.py`（注册）· `api/ui/errors.py`（新约束名）· `tests/conftest.py`（stock 依赖覆盖 fixture）

**Interfaces:**
- `usecase.sheet.Conflict`（同 `draft_po.Conflict`）。
- `create(actor) -> int`；`list_sheets(state) -> list`；`load(sheet_id) -> dict`；`grid(sheet_id, stock: ChStock) -> dict`；`put_cell(sheet_id, body, actor) -> dict`。
- 格子写：锁表行 + 锁计划行 → 校验（Python 侧给带数字的 409）→ 替换一条 arrangement（units=0/None 删除）→ 整表重派生（`rules.dispatch.derive` → 删空单、upsert 单与行、重写 source_map）→ 返回 diff 与 `warnings`（超可安排：只警告，Q20）。
- 网格：需求行 = state ∈ {已下单, 准备排货} 且（待排>0 或本表有安排）的记录 × demand_by_seller 的店 × 该店该货号的 msku（`msku_bridge`）；关联采购单 = `po_source_map → v_po_effective`（state, order_sn, is_void, 本记录各店量）+ `po_progress`（已入库 / 待到货，截至）；库存 = `snapshot(skus)` 桶 + `v_stock_committed` → 每 (sku, wid, sid) 的 `own/shared/committed/available`；无计划区 = 快照里本地仓有可发的货号 × 有 msku 的店；`excluded` = {line_state_not_dispatchable, seller_not_in_mirror, msku_missing, stock_dropped}。
- CH 失败：网格 200，`stock: null, stock_error: {target, cause}`。
- 路由挂 `require_fresh_mirrors`。

- [ ] 测试（integration，stock 用注入的假 `ChStock` 查询函数）：建表→列表；网格需求行展开到 msku、带关联采购单；计划格子写入→派生一单一行；同格改目的地→旧单删除；无计划格子；over_total 409；seller_not_in_line 409；冻结后写 409；并发同记录恰一个 409；CH 不可用网格仍 200。红 → 绿。

### Task 7: 装箱 · 预检 · 确认 · 取消

**Files:** Modify `repo/dispatch.py` · `usecase/sheet.py` · `api/ui/sheets.py` · `tests/test_api_sheets.py`

**Interfaces:** `put_packing(sheet_id, seller_sku, sid, from_wid, body, actor)`（oversea 行 → 409 `packing_not_applicable`）；`preflight(sheet_id, stock) -> {"ok","gates"}`；`confirm(sheet_id, seen_watermark, actor, stock)`（每次写 `sheet_gate_run`；不过 → 409 `confirm_blocked` + gates；CH 不可用 → 503 `stock_unavailable`）；`cancel(sheet_id, reason, actor)`。
- [ ] 测试：装箱齐全后 G1 过；FBA 缺装箱确认 409 列出全部闸；库存不足 G3 失败；成功确认后表/单 = 已确认、冻结；取消（草稿 / 已确认）需理由、释放；gate_run 每次一行。红 → 绿。

### Task 8: 前端 API 层

**Files:** Modify `web/src/api/types.ts` · `client.ts` · `http.ts` · `web/src/testing/memoryBackend.ts`（排货表内存实现）· `contract.test.tsx`

- [ ] 类型：`SheetSummary` · `SheetDetail` · `SheetGrid`（`demand[]`、`unplanned[]`、`stock`、`stock_error`、`excluded`）· `CellPut` · `CellResult` · `Gate` · `PackingPut`。
- [ ] client 方法：`listSheets` · `createSheet` · `getSheet` · `getSheetGrid` · `putSheetCell` · `putSheetPacking` · `sheetPreflight` · `confirmSheet` · `cancelSheet`。
- [ ] memoryBackend 实现上述 + 契约测试。`npm test` 绿。

### Task 9: 排货页面

**Files:** Create `web/src/pages/dispatch/{DispatchHome,SheetPage,DemandGrid,UnplannedStock,ConsignmentPanel}.tsx` · `dispatchGridModel.ts` · 对应 `*.test.tsx` · Modify `web/src/routes.tsx` · `web/src/shell/AppShell.tsx`（导航「排货」）· 把 `GateList`/`CommitButton` 从 `pages/purchase/parts.tsx` 提到 `components/`（purchase 改为引用）

- [ ] `dispatchGridModel.ts`（纯）：行键、待提交编辑、Σ格子 = Σ排货行。
- [ ] `DispatchHome`：列表（状态 · 排货单数 · 创建人 · 时间）+「新建排货表」→ 跳详情。
- [ ] `SheetPage`：需求网格（计划·rev·货号·月 / 店·msku · 待排 · 关联采购单折叠「已入库 / 待到货 · 截至」· 各本地仓 可安排 · 格子：来源仓 + 目的地选择 + 件数，回车/失焦提交）· 无计划来源区 · 排货单面板（装箱列仅 FBA）· 闸列表 · 确认 / 取消（取消要理由）· 库存截至 / 不可用提示。
- [ ] 测试：网格渲染、提交格子后派生单出现、超可安排警告、确认失败列出闸、取消要理由。`npm test` + `npm run typecheck` 绿。

### Task 10: e2e 与收尾

**Files:** Create `tests/test_e2e_c_dispatch_draft.py` · Modify `docs/00e` §8 进度

- [ ] 计划 → 采购单下单（用现有 e2e 夹具）→ 排货表：计划格 + 无计划格 → 确认 → 取消；断言 `v_check_dispatch` 全程为空。
- [ ] 全量：`uv run pytest -q`；`cd web && npm test && npm run typecheck`；`docs/prototype` 下四个文档闸。
- [ ] 告诉负责人起服务的命令，由其手动验收 `/dispatch`。
