# 销售计划 Excel 导入 · 对外查询接口 · 硬删除 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让外部 AI（和界面上的人）用一份 Excel 一步建好销售计划，并给外部 AI 提供映射 / 预测查询与硬删除接口。

**Architecture:** 共用逻辑（认领、推演、导入、删除）下沉到 `usecase/`；`api/ui/` 与新建的 `api/pub/` 各自是薄壳。
`api/pub/` 不许 import `api/ui/`（L10），所以 HTTP 公共件（错误信封、依赖、取数源装配）先挪到 `api/common/`。
Excel 的格式规则是纯函数（`rules/plan_import.py`），读写 xlsx 与对库校验在 `usecase/plan_import.py`。

**Tech Stack:** Python 3 · FastAPI · psycopg2 · openpyxl（新增）· python-multipart（新增，FastAPI 收文件要它）· React + Vite + TS · vitest。

**Spec:** `docs/superpowers/specs/2026-10-22-plan-excel-import-design.md`（裁定 `docs/00` IM-D1 ~ D10）

## Global Constraints

- 测试命令：后端 `uv run pytest -q <path>`（要连测试 PG；纯层 `JXD_SCM_CONFIG=/nonexistent uv run pytest -q -m pure`）；前端 `cd web && npx vitest run <path>`。
- 错误信封 = `ApiError(status, error, hint, fields)` → `{"error", "hint", **fields}`（字段平铺顶层）。
- pub 路径前缀 `/pub/v1`；界面路径前缀 `/v1`（L9：界面不许打 `/pub`）。
- AI 员工：actor id `ai.agent`，名 `AI 员工`；pub 写接口（导入、删除）只认它，别的 actor → 403 `actor_not_allowed`。
- 空 ≠ 0：Excel 空格 → `null`；`0` → 0。
- 导入上限：文件 5 MB、2,000 数据行（设计取值 · 未实测）；导入不受 `CELLS_BATCH_MAX = 1000` 限制。
- 导入整体拒绝：任何错误 → 422 `import_rejected`，`errors` 列全部错误，库里一行不写（不留 plan 行）。
- 删除：有任何 `plan_rev` → 409 `plan_submitted`；库里不留痕，删除日志是唯一痕迹。
- 每个对外调用 / 写操作的日志带：谁 · 对象 · 量 · 耗时 · outcome（CLAUDE.md 日志三问）。
- 测试写完先弄失败一次；迁移文件已执行的不可改，新迁移号 `029`。
- 代码注释只写「为什么」；界面文案只写当场发生的事。

## Review Focus

1. **同一 msku 在 Excel 里写了不同大小写 / 前后空格**（`msku-a ` vs `MSKU-A`）→ 期望：前后空格去掉，大小写**不改**（桥表区分大小写），对不上就 `unknown_msku`，不静默匹配。测试在 Task 5。
2. **Excel 月份表头被 Excel 自动转成日期**（单元格是 `datetime(2026,11,1)` 而不是字符串 `2026-11`）→ 期望：按年月识别为 `2026-11`。测试在 Task 5。
3. **导入过程中取数源（CH）不可用** → 期望：整体回滚、不留 plan 行，返回与 `claim` 相同的 503 码，日志带 `cause`。测试在 Task 6。
4. **删除一张已归档但从未提交的计划** → 期望：可以删（spec §1 ④）。测试在 Task 7。
5. **界面导入失败后用户改了文件再点「新建」** → 期望：再次提交新文件，旧错误列表清掉。测试在 Task 10。

---

### Task 1: HTTP 公共件挪到 `api/common/`

**Files:**
- Move: `api/ui/errors.py` → `api/common/errors.py`；`api/ui/deps.py` → `api/common/deps.py`；`api/ui/source_factory.py` → `api/common/source_factory.py`
- Create: `api/common/__init__.py`
- Modify: 全部 import 点（约 22 个文件，含 `tests/conftest.py` 的 `source_factory` 打桩）；`tests/test_layering.py` 若按目录列包则补 `api/common`
- Modify: `docs/01-架构设计.md` §7 目录树加 `api/common/  HTTP 公共件（ui 与 pub 共用）`

**Interfaces:**
- Produces: `api.common.errors.ApiError` · `api.common.deps.{actor, actor_optional, declared, ensure_plan, ensure_writable, require_fresh_mirrors}` · `api.common.source_factory.{source_dep, make_source}` —— 签名不变。

- [ ] **Step 1:** `git mv` 三个文件；`grep -rl "api\.ui\.\(errors\|deps\|source_factory\)"`（排除 `.claude/`）逐个改成 `api.common.*`。不留 re-export 垫片（垫片会让 pub 经由 ui 间接依赖，L10 扫不到）。
- [ ] **Step 2:** 跑 `uv run pytest -q`，Expected: 与改动前同样的通过数（先在改动前跑一次记下数字）。
- [ ] **Step 3:** Commit `refactor(api): HTTP 公共件挪到 api/common/，为 api/pub 让路（L10）`

### Task 2: 认领抽到 `usecase/plan_claim.py`

**Files:**
- Create: `usecase/plan_claim.py`
- Modify: `api/ui/plans.py`（`claim()`、`recompute_forecast()` 改调新函数；`_seed_forecast` / `_cell_payload` / `_lock_sku` / `_periods` / `_claimed_elsewhere` 的逻辑搬走）
- Test: `tests/test_usecase_plan_claim.py`；现有 `tests/test_api_plans.py` 等不改

**Interfaces:**
- Produces:
  - `class UnknownMsku(Exception)`（属性 `seller_sku, sid`）
  - `class ClaimedElsewhere(Exception)`（属性 `seller_sku, sid, holder: tuple[int, str, str] | None` = (plan_id, claimed_by, title)）
  - `claim_holder(cur, seller_sku: str, sid: str) -> tuple[int, str, str] | None` —— 活认领的占用者（口径同现 `claim()` 的 occupier 查询）
  - `claim_msku(cur, source, plan_id: int, seller_sku: str, sid: str, who: str) -> dict` —— 返回体与现 `claim()` 的 200 响应**同形**；撞唯一索引时**不 rollback**，抛 `ClaimedElsewhere(holder=None)`，由调用方回滚后用 `claim_holder` 补名
  - `plan_periods(cur, plan_id: int) -> list[dt.date]`
  - `seed_rows(source, seller_sku: str, sid: str, sku: str, platform: str, has_fba: bool, periods: list[dt.date]) -> list[tuple]` —— 只算不写：返回与 `_load_grid` demand 行同形的元组（`expected_units=None`），`claim_msku` 用它写格子，预览（Task 9）用它不写库
- 取数源异常（`ChUnavailable` 等）原样上抛，翻译仍在 api 层。

- [ ] **Step 1:** 写 `test_seed_rows_writes_nothing`（调用前后四张计划表行数不变）；写 `test_claim_msku_seeds_cells_like_the_endpoint`：同一 seed、同一 FixtureSource，`claim_msku` 后 `plan_demand_cell` / `plan_purchase_cell` 行与经 `POST /v1/plans/{id}/claims` 得到的逐列相等；`test_claim_msku_raises_unknown_msku`；`test_claim_msku_raises_claimed_elsewhere_without_rolling_back`（断言调用后连接仍在事务中、由测试自己回滚）。
- [ ] **Step 2:** 跑，Expected: ImportError。
- [ ] **Step 3:** 实现；`claim()` 变为：开连接 → `ensure_writable` → `claim_msku` → 把两种异常翻成现有的 404 `unknown_msku` / 409（`_claimed_elsewhere` 形状不变）。
- [ ] **Step 4:** 跑新测试 + `tests/test_api_plans.py tests/test_forecast_plan.py`，Expected: 全 PASS。
- [ ] **Step 5:** Commit `refactor(plan): 认领下沉 usecase/plan_claim，claim 与导入共用`

### Task 3: 推演入参与序列化抽到 `usecase/plan_projection.py`

**Files:**
- Create: `usecase/plan_projection.py`（`forecast_payload` 及其私有件从 `api/ui/grid_forecast.py` 整体搬来；`grid_forecast.py` 删掉或只留 `NO_SALES_SOURCE` 等界面常量）
- Modify: `api/ui/plans.py::grid`
- Test: `tests/test_usecase_plan_projection.py`；现有 grid 测试不改

**Interfaces:**
- Produces:
  - `forecast_payload(...)` —— 签名照旧，`plan_id: int | None`（预览时为 None，日志写 `plan_id=preview`）
  - `project(source, cur, demand_rows: list[tuple], purchase_rows: list[tuple], periods: list[dt.date], plan_id: int | None) -> dict` —— 把 `grid()` 里从 `out_demand` 组装到 `forecast_payload` 返回、再加 `source_notes` 的那一段整体搬进来；`demand_rows` 的元组形状 = `_load_grid` 的 demand 行（`seller_sku, sid, sku, period, system_units, system_extrapolated, expected_units, has_fba, system_basis`）。返回 `{**payload, "demand": [...], "source_notes": {...}}`。
- `grid()` 变为：`_load_grid` → `project(...)` → 补 `plan_id` / `periods` / `purchase` / `has_image`。

- [ ] **Step 1:** 写 `test_project_matches_grid_for_the_same_rows`：建计划认领 `msku_a`、`msku_d`，取 `GET /grid` 响应；用同一批行直接调 `project`，断言 `forecast` / `forecast_sku` / `forecast_msku` / `source_notes` 相等。
- [ ] **Step 2:** 跑，Expected: ImportError。
- [ ] **Step 3:** 搬移实现。
- [ ] **Step 4:** 跑新测试 + `uv run pytest -q tests -k "grid or forecast"`，Expected: PASS。
- [ ] **Step 5:** Commit `refactor(forecast): 推演组装下沉 usecase/plan_projection，grid 与预览共用`

### Task 4: AI 员工身份

**Files:**
- Create: `migrations/pg/029_seed_ai_actor.sql`（`INSERT INTO actor (actor_id, name, active) VALUES ('ai.agent', 'AI 员工', true) ON CONFLICT (actor_id) DO NOTHING;`，头注出处 IM-D8）
- Modify: `tests/test_actor_seed.py`：断言改为 `ACTORS 的 id 集合 == 007 种子 ∪ 029 种子 − NON_UI_ACTORS`，`NON_UI_ACTORS = {"ai.agent"}` 定义在 `api/common/deps.py`（pub 也要用它判身份）
- Modify: `api/common/deps.py`：加 `NON_UI_ACTORS: frozenset[str]`、`AI_ACTOR = "ai.agent"`、`require_ai_actor(request) -> str`（先走 `actor()`，再要求 `== AI_ACTOR`，否则 `ApiError(403, "actor_not_allowed", "这个接口只给 AI 员工调用", {"actor": who})`）

- [ ] **Step 1:** 写 `test_require_ai_actor_rejects_human_actor`（alice → 403 `actor_not_allowed`）与 `test_require_ai_actor_accepts_ai_agent`；改 `test_actor_seed`，先让它因 `ai.agent` 不在 TS 而红一次再改断言。
- [ ] **Step 2:** 跑，Expected: FAIL。
- [ ] **Step 3:** 写迁移与依赖；★ 按记忆「迁移随改动一起应用」：`uv run python -m migrations.pg.apply` 打到生产 `scm`（照仓内既有做法，先看 `apply.py` 用法）。
- [ ] **Step 4:** 跑，Expected: PASS。
- [ ] **Step 5:** Commit `feat(actor): AI 员工身份 ai.agent（IM-D8），不进界面下拉`

### Task 5: Excel 格式规则（纯函数）+ xlsx 读写

**Files:**
- Modify: `pyproject.toml` 依赖加 `openpyxl>=3.1`、`python-multipart>=0.0.9`；`uv lock`
- Create: `rules/plan_import.py`（纯：吃「已读出的单元格」，出行与错误）
- Create: `usecase/plan_xlsx.py`（openpyxl：bytes → 单元格表；periods → 模板 bytes）
- Test: `tests/test_rules_plan_import.py`（纯层）、`tests/test_plan_xlsx.py`（纯层，内存里造工作簿）

**Interfaces:**
- Produces（`rules/plan_import.py`）：
  - `@dataclass(frozen=True) class RowError: row: int; col: str | None; code: str; message: str; seller_sku: str | None = None; sid: str | None = None; sku: str | None = None; extra: dict = field(default_factory=dict)`
  - `@dataclass(frozen=True) class DemandRow: row: int; seller_sku: str; sid: str; sku: str | None; seller_name: str | None; market: str | None; units: dict[str, int | None]`（键 `YYYY-MM`）
  - `@dataclass(frozen=True) class PurchaseRow: row: int; sku: str; units: dict[str, int | None]`
  - `@dataclass(frozen=True) class Parsed: demand: list[DemandRow]; purchase: list[PurchaseRow]; errors: list[RowError]`
  - `parse_rows(header: list[object], rows: list[list[object]], periods: list[str]) -> Parsed` —— `rows[i]` 对应 Excel 第 `i+2` 行；列字母按表头位置算（A、B…）
  - 固定列名常量 `COLUMNS = ("类型", "货号", "店铺ID", "店铺", "国家", "msku")`；`MAX_ROWS = 2000`
- Produces（`usecase/plan_xlsx.py`）：`read_first_sheet(data: bytes) -> tuple[list[object], list[list[object]]]`（非 xlsx → 抛 `NotXlsx`）；`build_template(periods: list[str]) -> bytes`；`MAX_BYTES = 5 * 1024 * 1024`
- 本任务只做 spec §2 里**不碰库**的错误码：`bad_header` `missing_month` `extra_month` `bad_type` `missing_field` `bad_units` `duplicate_row` `empty_file` `file_too_large` `not_xlsx` `purchase_sku_not_claimed`（后者只看文件内：采购货号 ∉ 销量行已填的 `货号`。销量行没填货号的情况留给 Task 6 用桥表补齐后再判）。

- [ ] **Step 1:** 写纯层测试（每条一个断言点，值取 spec §2）：
  - 空格 → `None`，`0` → 0，`12.0` → 12，`12.5` / `-1` / `"十"` → `bad_units`（带 `row`、`col`）
  - 表头列顺序打乱照样认；列名前后空格忽略
  - 月份表头是 `datetime(2026, 11, 1)` → 认作 `2026-11`（Review Focus 2）
  - 少一个月 → `missing_month`；多一个月 → `extra_month`
  - `类型` 为 `退货` → `bad_type`；销量行缺 `msku` / `店铺ID` → `missing_field`；采购行缺 `货号` → `missing_field`
  - 同 (msku, 店铺ID) 两行 → `duplicate_row`（两行行号都点名）；同货号两条采购 → `duplicate_row`
  - msku 前后空格去掉、大小写保留（Review Focus 1：`" msku-a "` → `"msku-a"`）
  - 一行数据都没有 → `empty_file`；2,001 行 → `file_too_large`
  - **多处错误一次全报**：一个表里放 3 种错，断言 `len(errors) == 3`
  - `build_template(["2026-11","2026-12"])` 读回来表头 = `COLUMNS + ("2026-11","2026-12")`，且 `parse_rows` 对模板的示例行之外无错
  - `read_first_sheet(b"not a zip")` → `NotXlsx`
- [ ] **Step 2:** 跑，Expected: FAIL（模块不存在）。
- [ ] **Step 3:** 实现。模板示例行：一行销量、一行采购，`类型` 列写 `销量（示例，删掉这行）` —— 这样不删就导入会因 `bad_type` 被拒，不会把示例当真数。
- [ ] **Step 4:** 跑，Expected: PASS。
- [ ] **Step 5:** Commit `feat(import): Excel 格式规则与 xlsx 读写（IM 组 spec §2）`

### Task 6: 导入 `usecase/plan_import.py`

**Files:**
- Create: `usecase/plan_import.py`
- Test: `tests/test_usecase_plan_import.py`

**Interfaces:**
- Consumes: Task 2 `claim_msku` / `claim_holder` / `ClaimedElsewhere`；Task 5 `read_first_sheet` / `parse_rows` / `MAX_BYTES`
- Produces:
  - `@dataclass(frozen=True) class PlanHeader: title: str; period_start: dt.date; months: int; owner_actor: str`
  - `class ImportRejected(Exception)`（属性 `errors: list[RowError]`）
  - `import_plan(source, header: PlanHeader, data: bytes, who: str) -> dict` —— 自己开 `pg_conn()`，一个事务；返回 `{"plan_id", "mskus", "skus", "demand_cells", "purchase_cells", "elapsed_ms"}`
- 抬头校验沿用 `POST /plans` 的规则；抬头错误（标题空、months 越界或非整数、起始月非月初）**也**进 `ImportRejected`，`row=0`、`code` 用 `title_required` / `validation_error` / `bad_period_start`。

流程（spec §1 ③）：
1. `len(data) > MAX_BYTES` → `file_too_large`；读表、`parse_rows`。
2. 对库只读校验，错误**追加**不中断：桥表无 (msku, sid) → `unknown_msku`；`店铺ID` 不在 `seller` → `unknown_seller`；`店铺` / `国家` 填了但不等于 `seller.name` / `seller.market` → `seller_mismatch` / `market_mismatch`；`货号` 填了但 ≠ 桥表货号 → `sku_mismatch`；`claim_holder` 非空 → `claimed_elsewhere`（`extra={"plan_id", "title"}`）；采购货号 ∉ 销量行（用桥表补齐后的）货号集合 → `purchase_sku_not_claimed`。
3. 有错 → `ImportRejected`，此前**没开写事务**。
4. 写：`INSERT plan` → 逐个 `claim_msku` → 按 (seller_sku, sid, period) 写 `expected_units`、按 (sku, period) 写 `planned_units`（直接 UPDATE 刚种出的格子；`updated_by = who`）→ commit。
5. 第 4 步里 `ClaimedElsewhere`（并发撞上）→ rollback → `claim_holder` 补名 → `ImportRejected([claimed_elsewhere])`；取数源异常 → rollback 后原样上抛。
- 日志：`op=plan_import actor title rows mskus skus cells file_bytes parse_ms validate_ms write_ms outcome errors=<code:count,...>`；拒绝时逐条写前 20 条错误。

- [ ] **Step 1:** 写集成测试（`seed` + FixtureSource）：
  - `test_import_creates_plan_with_claims_and_human_cells`：2 条销量（`msku_a`、`msku_d`）+ 1 条采购（`sku_a`），断言 plan 行、2 条活认领、`expected_units` / `planned_units` 等于表里的数，空格为 NULL
  - `test_import_rejects_everything_and_leaves_no_plan`：表里同时放 `unknown_msku`、`market_mismatch`、`bad_units`，断言 `ImportRejected.errors` 三条、`SELECT count(*) FROM plan` 仍为 0
  - `test_import_rejects_claimed_elsewhere_naming_the_holder`：先用别的计划认领 `msku_a`，断言错误带 `plan_id` 与 `title`
  - `test_import_rolls_back_when_source_is_down`（Review Focus 3）：`use_source` 换成取数抛 `ChUnavailable` 的桩，断言异常上抛且 `plan` 表为空
  - `test_import_purchase_sku_resolved_via_bridge`：销量行不填 `货号`，采购行填 `sku_a` → 通过
- [ ] **Step 2:** 跑，Expected: FAIL。
- [ ] **Step 3:** 实现。
- [ ] **Step 4:** 跑，Expected: PASS。
- [ ] **Step 5:** Commit `feat(import): 导入建计划——先全查后一次写，整体拒绝（IM-D1/D3）`

### Task 7: 硬删除 `usecase/plan_delete.py`

**Files:**
- Create: `usecase/plan_delete.py`
- Test: `tests/test_usecase_plan_delete.py`

**Interfaces:**
- Produces:
  - `class PlanNotFound(Exception)`；`class PlanSubmitted(Exception)`（属性 `revs: int`）；`class PlanReferenced(Exception)`（属性 `table: str, constraint: str`）
  - `delete_plan(plan_id: int, who: str) -> dict` —— 一个事务：`SELECT … FOR UPDATE` 锁 plan 行 → 有 `plan_rev` 抛 `PlanSubmitted` → 记下 msku 列表与格子数 → `DELETE plan_demand_cell` / `plan_purchase_cell` / `msku_claim` / `plan`；`ForeignKeyViolation` → rollback，抛 `PlanReferenced`（表名取 `pg_error_fields`）。返回 `{"plan_id", "title", "mskus": [{"seller_sku","sid"}], "demand_cells", "purchase_cells"}`
- 日志：`op=delete_plan plan_id title actor mskus=<s/sid,...> demand_cells purchase_cells elapsed_ms outcome`（成功与每种拒绝都打）

- [ ] **Step 1:** 写测试：删未提交计划后四张表都无该 plan_id 的行，且这些 msku 可被新计划认领；已提交计划 → `PlanSubmitted`，四张表行数不变；已归档未提交 → 可删（Review Focus 4）；不存在 → `PlanNotFound`；成功时 `scm_log` 里有 `op=delete_plan` 且含 msku 列表。
- [ ] **Step 2:** 跑，Expected: FAIL。
- [ ] **Step 3:** 实现（造「已提交」状态照 `tests/test_api_plans.py` 里现有提交测试的做法）。
- [ ] **Step 4:** 跑，Expected: PASS。
- [ ] **Step 5:** Commit `feat(plan): 硬删除未提交计划（IM-D4/D5），日志是唯一痕迹`

### Task 8: 界面层接口 `api/ui/plan_import.py`

**Files:**
- Create: `api/ui/plan_import.py`（router：`POST /plans/import`、`GET /plans/import/template`）
- Modify: `api/__init__.py` 挂到 `/v1`
- Test: `tests/test_api_plan_import.py`

**Interfaces:**
- Consumes: Task 6 `import_plan` / `ImportRejected` / `PlanHeader`；Task 5 `build_template`
- `POST /v1/plans/import`：multipart `title, period_start(YYYY-MM), months, file`，`x-actor` 必填 → 201 `import_plan` 返回体；`ImportRejected` → `ApiError(422, "import_rejected", f"有 {n} 处问题，计划没建", {"errors": [asdict(e) …]})`；取数源异常照 `plans.py::_source_error` 的翻译（把它挪进 `api/common` 或在本模块复用 —— 不许另写一份）。
- `GET /v1/plans/import/template?period_start=2026-11&months=3` → `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`，`Content-Disposition: attachment; filename="plan-import-2026-11-3m.xlsx"`；months 越界 → 400 `validation_error`。

- [ ] **Step 1:** 写测试：成功 201 且 `GET /v1/plans/{id}/grid` 能看到导入的 `expected_units`；422 时 `body["errors"]` 列全、`/v1/plans` 列表里没有新计划；模板下载回来能被 `read_first_sheet` 读出正确表头；缺 `x-actor` → 400 `unknown_actor`。
- [ ] **Step 2:** 跑，Expected: 404 / FAIL。
- [ ] **Step 3:** 实现。
- [ ] **Step 4:** 跑，Expected: PASS。
- [ ] **Step 5:** Commit `feat(api/ui): 计划导入与模板下载`

### Task 9: 对外接口 `api/pub/`

**Files:**
- Create: `api/pub/__init__.py`、`api/pub/v1.py`（router，前缀 `/pub/v1`）
- Modify: `api/__init__.py` 挂载 pub router
- Test: `tests/test_api_pub.py`；确认 `tests/test_layering_web.py::test_l10_api_pub_must_not_import_api_ui` 现在**真的扫到文件**（给它加 `assert` 扫到 ≥1 个文件，并先在 pub 里临时 import 一次 `api.ui` 看它红，再删掉）

**Interfaces:**
- Consumes: Task 1 `api.common.*`、Task 2 `UnknownMsku`、Task 3 `project`、Task 4 `require_ai_actor`、Task 6 `import_plan`、Task 7 `delete_plan`
- `GET /pub/v1/mappings?sku=&seller_sku=&sid=&market=&limit=` —— 读接口，`actor_optional`；返回 `{need_query, matched, truncated, limit, dropped, items}`，`items[]` 字段 = spec §1 ①（`sku, sku_name, seller_sku, sid, seller_name, market, platform, has_fba, claimed_by`）；`dropped` = 桥表里因店铺 / 货号不在镜像被 join 吃掉的行数。`limit` 默认 200，上限 2000（设计取值 · 未实测）。
- `POST /pub/v1/forecast/preview` body `{period_start: "YYYY-MM", months, mskus?: [{seller_sku, sid}], skus?: [str]}` —— 读接口；`skus` 按桥表展开；不在桥表的 msku → 404 `unknown_msku`（逐个列在 `mskus` 字段）；用 Task 2 的 `seed_rows` 得到预测行（不写库），交 `project(..., plan_id=None)`。返回 `project` 的结果 + `periods`。
- `POST /pub/v1/plans/import` —— 同 Task 8，但依赖 `require_ai_actor`。
- `GET /pub/v1/plans/import/template` —— 同 Task 8。
- `DELETE /pub/v1/plans/{plan_id}` —— `require_ai_actor`；`PlanNotFound` → 404 `plan_not_found`；`PlanSubmitted` → 409 `plan_submitted`（`revs`）；`PlanReferenced` → 409 `plan_referenced`（`table`）；成功 200 `delete_plan` 返回体。

- [ ] **Step 1:** 写测试：mappings 不给条件 → `need_query=true`；按 `market=UK` 只回 `msku_c`；被别的计划占着的 msku 带 `claimed_by.title`；preview 对 `msku_a` 的 `forecast` 逐月行数 = months、跑完后 `plan` / `msku_claim` 仍为空；`skus=[sku_a]` 展开成 `msku_a/b/c`；导入与删除用 `x-actor: alice` → 403 `actor_not_allowed`，用 `ai.agent`（测试里 INSERT 这行 actor，`wipe` 会清表）→ 201 / 200；删已提交 → 409。
- [ ] **Step 2:** 跑，Expected: 404 / FAIL。
- [ ] **Step 3:** 实现。
- [ ] **Step 4:** 跑本文件 + `tests/test_layering.py tests/test_layering_web.py`，Expected: PASS。
- [ ] **Step 5:** Commit `feat(api/pub): 对外映射 · 预测预览 · 导入 · 模板 · 硬删除（IM-D7）`

### Task 10: 界面 —— 新建计划表单里的 Excel 导入

**Files:**
- Modify: `web/src/api/client.ts`（接口加 `importPlan(input: CreatePlanInput & { file: File }): Promise<ImportPlanResult>`、`importTemplateUrl(periodStart: string, months: number): string`）、`web/src/api/http.ts`（multipart 调用，超时用 `READ_BACK_TIMEOUT_MS`：导入要逐个 msku 取预测）、`web/src/api/types.ts`（`ImportPlanResult`、`ImportRowError`）
- Modify: `web/src/pages/OpsHome.tsx`（新建表单加 `从 Excel 导入（可选）` 文件框 + `下载模板` 链接；422 时表单内列出错误）
- Test: `web/src/pages/OpsHome.test.tsx`；`web/src/api/contract.test.tsx` 若对路由清单做契约核对则补上

**Interfaces:**
- Consumes: Task 8 两个 `/v1` 接口
- 文案（原文）：标签 `从 Excel 导入（可选）`；链接 `下载模板`；错误列表标题 `计划没建：有 N 处问题`；每条 `第 {row} 行{ col ? ` {col} 列` : ''} · {msku 或 货号} · {message}`。
- 行为：不选文件 → 走原 `createPlan`；选了 → `importPlan`，成功跳 `/plans/{plan_id}`；422 → 列错误、保留表单输入；重新选文件或再次提交时清掉旧错误（Review Focus 5）。按钮请求中禁用（沿用 `CREATE_KEY`）。

- [ ] **Step 1:** 写测试：不选文件时仍调 `createPlan`；选文件调 `importPlan` 且带 `title/period_start/months/file`；422 渲染 `计划没建：有 2 处问题` 与两条明细；改选文件后错误消失；`下载模板` 的 href 随起始月 / 月数变化。
- [ ] **Step 2:** `cd web && npx vitest run src/pages/OpsHome.test.tsx`，Expected: FAIL。
- [ ] **Step 3:** 实现。
- [ ] **Step 4:** 跑 `cd web && npx vitest run` 与 `npx tsc --noEmit`，Expected: PASS。
- [ ] **Step 5:** Commit `feat(web): 新建销售计划可直接从 Excel 导入`

### Task 11: 文档与给外部 AI 的 skill

**Files:**
- Modify: `docs/08-后端接口清单.md`（pub 五个接口逐条标「契约」+ ui 两个；错误码表加 spec §2 全部码与 `actor_not_allowed` `plan_submitted` `plan_referenced` `plan_not_found`）
- Modify: `docs/10-前端交互界面.md` §2.5（新建计划表单的按钮 / 链接原文）+ `docs/prototype/test_doc_flow.js` 两边对核（原型冻结，只在测试对照表里登记新文案，若该测试要求原型也有则在测试里标注「原型冻结，不对核」—— 先读测试再定）
- Modify: `docs/01-架构设计.md` §3.1 / §8：`api/pub/` 落地、`usecase/` 新增 plan_claim / plan_projection / plan_import / plan_delete / plan_xlsx
- Create: `docs/skills/scm-plan-import/SKILL.md`（spec §6：frontmatter `name: scm-plan-import`、`description` 写触发场景；正文按 spec §0 的五步 + 每个错误码的处理；Excel 格式引用 spec §2，不抄）

- [ ] **Step 1:** 改文档、写 skill。
- [ ] **Step 2:** 跑 `cd docs/prototype && node test_doc_flow.js && node test_doc_index.js && node test_retired_claims.js`，Expected: 全过。
- [ ] **Step 3:** 全量 `uv run pytest -q` 与 `cd web && npx vitest run`，Expected: 全过。
- [ ] **Step 4:** Commit `docs(08,10,01): 计划导入 · 对外接口；外部 AI 用的 scm-plan-import skill`
