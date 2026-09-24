# 计划整表的预测销量与预测库存 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 计划整表每一格给出「预测销量」（历史推出来的、期望销量的默认值）与「库存」（梦倩逐日台账按我们的口径：期初 − 期望 + 入库，逐月相接），并把计划采购穿过 生产→货运 管道、按两遍法分给 msku。

**Architecture:** 全部计算是 `forecast/` 里的**纯函数**（标准库 + `fractions.Fraction`，不落库）：半月摊分 → 逐日台账（带符号 / 截断两视图 + 恒等式）→ 批次管道与两遍分配 → 按月读出 → msku/店铺/货号三层汇总。`api/ui/plans.py::grid()` 只负责取数、调一次 `forecast_plan()`、序列化；`claim()` 用移植来的 `baseline_rate` 种预测销量并把依据落 `plan_demand_cell.system_basis`。提前期是 `lead_time` 表里的用户配置（`GET/PUT /v1/lead-time`）。前端 `PlanSheet` 三种行按 spec §5 显示标签、点开看依据链、可改提前期。

**Tech Stack:** Python 3.12 · FastAPI · psycopg2 · clickhouse-connect（只经注入的 `Query`）· pytest（真 PG `scm_test`）· React 18 + Vite + TS · vitest

**Spec:** `docs/superpowers/specs/2026-09-24-plan-forecast-design.md`（★ 唯一裁决依据；每个任务标了对应的 § 号）

**工作目录：** 全部命令在 worktree `/home/fido/work/2026/jxd_service_group/service_supplychain-forecast` 根下跑（分支 `feat/plan-forecast`）。★ 不碰主 checkout `/home/fido/work/2026/jxd_service_group/service_supplychain`（那里跑着使用者的 dev server）。★ 不起、不杀任何服务。

---

## Global Constraints

来自 spec（原文照录，括号内为出处）：

- 提前期：`make_days` 生产 **30** · `ship_days` 货运 **60**（国际海运）—— 用户变量、全局配置、界面可改、改完立即重算（spec §4.4 · `14` B12 · `00` M-2）
- 锚点：下单月 **15 号** —— 不开放（spec §4.4 · `14` §2.3 · M-12）。例：**10 月下单、30 + 60 天 → 2027-01 到货**（spec §7）
- `embargo_days = 7` —— ★ **设计取值 · 未实测**（spec §3.1 · `00` W-5）
- `预测销量[msku, 月] = base_rate × 该月天数`，**向上取整**；季节指数停用（spec §3.1 · §3.3）
- `base_rate` 常数 **56 / 14 / 90 / 3 / p95** 全部来自兄弟仓 `demand.py:67-77`，**不另调**（spec §3.1）
- 进库存的是**期望销量**（人改过的以人为准），不是预测销量（spec §2 · §4.2 · `02` §3.1b）
- 月度数摊到每天：**半月均分**；`Σ当月每日 ≡ 期望销量[月]`（构造保证，断言）（spec §4.2）
- ★ W-6：as_of 到计划首月之间的那几天**按预测销量扣**，依据单列 —— 这是建议不是裁定（spec §4.2 · `00` W-6）
- 期望销量为空 → 该 msku 从该月起库存**未知**，向后传染，不当 0；无 FBA → **不适用**（spec §4.2 · §4.6 · `14` B1/B2/B5）
- `期末[m] = 期初[m] − 期望销量[m] + 当期入库[m]`（带符号，负数照实显示）；`期初[m+1] ≡ 期末[m]`（spec §4.5 · `14` §1.1 ③④ · B8）。例：**10 月期望 100，期初 200 → 期末 100 → 11 月期初 100**
- 预计断货日 = 截断台账里第一个 `daily_unmet > 0` 的日子；月末「期末」用带符号头寸（spec §4.5）
- `带符号头寸 ≡ 可售 − 累计卖不出去`（每轮断言）（spec §4.1）
- 两遍分配（spec §4.4 · `14` §3.1）：谁都不缺 → 全按期望销量比例摊；缺口 ≤ 这批 → 先补缺口、余量按期望销量摊；缺口 > 这批 → 按缺口比例、取整、余数给缺口最大的；★ `Σ分到的 ≡ 这批件数`。★ 不写任何表、不产生动作
- 领星已下单未到（CH 采购单 `status=2`）**不定到货日**，只进货号行「在途·已下单」，不进可售；FBA 货件已发未到阶段 A 未取数 → 显示「未取」不显示 0；本地仓在库不进可售（spec §4.3）
- 到货落在计划周期之外的采购**逐笔列出**（spec §4.3 · `14` B3）
- 汇总：店铺 = Σ msku，货号 = Σ 店铺；合计行不许独立算一遍；任一 msku 未知 → 上层未知；无 FBA 店不进求和（spec §4.6）
- `system_extrapolated` 由算法如实给 **false**（spec §3.3）；没有历史 ≠ 0：`system_units` 留 NULL 并点名（spec §3.3）
- 库存推演按需纯函数，**不落库**；`03` §7 的七张推演表不建（spec §6）
- 界面说明进折叠区，默认收起（spec §5 · CLAUDE.md「屏幕给数据，文档给论证」）

本仓规则（CLAUDE.md，每个任务隐含遵守）：

- 每个数字要么指到出处，要么标「设计取值 · 未实测」；指不到出处的约束**不写**，记进下面的 Open points
- ClickHouse **只读**；结构变更**只走新迁移文件**（`migrations/pg/008_*.sql`），已执行的不改
- 外部调用日志三问：打的谁 · 多久 · 怎么失败的（带 `cause` / `e.cause?.code`）；静默兜底禁止
- `forecast/` 只许 import 标准库 + `rules`（`tests/test_layering.py::test_l2_forecast_only_stdlib_and_rules`）—— 见 Open point OP-2
- **测试写完先弄失败一次**：每个 RED 步骤写了预期的失败信息，看不到就说明没测到
- 命名：领星占用词 `inbound` · `shipment` · `shipment_plan` · `allocation` 不作**我们的**表名/类名/模块名（spec §6 的 JSON 字段名 `inbound_planned` 是 spec 原文，照用）
- 验证命令：后端 `.venv/bin/python -m pytest -q <files>` · `.venv/bin/ruff check .`；前端 `cd web && npx vitest run <files>` · `cd web && npx tsc -b`

## Review Focus

spec 没点名、但最可能咬到使用者的五种输入（每条的测试已加进所属任务）：

1. **计划从 as_of 当月或更早的月份开始**（新建计划时 `period_start` 选了本月）—— 已过去的月份给「已过去」，as_of 所在月从 as_of 次日起算（`partial`），后面的月照常相接；不许给出一个假的期初。→ Task 6 `test_as_of_inside_first_month_*` / `test_elapsed_month_*`
2. **一个货号有计划采购，但没有可分给的 msku**（全是无 FBA 店 / 在仓没取到 / 期望未填）—— 这批货点名进 `unallocated`，受影响的 msku 从到货日起「未知」，不许悄悄消失，也不许全给某一家。→ Task 7 `test_batch_with_unknown_gap_*` / `test_batch_with_no_fba_msku_*`，Task 9 `test_grid_unknown_demand_makes_the_batch_unallocated`
3. **提前期改成极端值**：`0/0`（到货日早于 as_of）与 `720`（落在周期外）—— 两种都逐笔列进 `beyond_horizon` 并写明原因。→ Task 7 `test_zero_lead_time_before_as_of_is_listed`，Task 8 `test_put_rejects_out_of_range`
4. **`base_rate × 天数` 恰好是整数**（`1.1 × 30` 在 float 里是 `33.000000000000004`）—— 向上取整不许因为浮点噪声多出一件。→ Task 2 `test_units_for_month_ignores_float_noise`
5. **几百个货号的整表**（`PlanSheet` 就是为「几百个货号一张表」建的）—— `Fraction` 逐日台账的耗时要有数、有日志。→ Task 7 `test_hundred_mskus_twelve_months_is_fast_enough`，Task 9 grid 日志带 `elapsed_ms`

## Open points（spec 未定 / spec 与代码·文档冲突 —— 不许静默自定，这里给建议裁定）

| # | 问题 | 建议裁定（本计划按此写；负责人推翻则改对应任务） |
|---|---|---|
| **OP-1** | ★ spec §3.1/§7 把「兄弟仓 228 格 recon」当成 `base_rate` 移植的门禁，并说 ② embargo 会让它变红。实查 `test_recon.py`：228 格是**台账**对账（运营 Excel 的期末余额，需求来自运营手填的 `overrides`、`compat` 模式），**完全不经过 `baseline_rate`**；embargo 改不动它任何一格。`demand_history.csv` 只有 8/3 一天，也喂不了 56 天窗口 | ① 228 格照 D-3 原样搬，但作为**台账门禁**放在 Phase 1（Task 4，已在 scratch 里用 `Fraction` 验过：228/228 **逐格精确相等**、2 格缓存过期、226 格与缓存一致）。② `baseline_rate` 的「一次绿」门禁另立：用兄弟仓 v1 在**固定输入**上算出的**金标向量**（Task 11，D-3「任意一组固定输入」）；embargo 让**它**变红（Task 12），再改指向 `as_of − 7` 的金标。建议把 `00` D-3 的门禁描述改成这两条 |
| **OP-2** | `00` G-1 裁定「`forecast/` 允许 numpy」，但 `tests/test_layering.py::test_l2` 仍只许标准库，`forecast/__init__.py` 也写着标准库；venv 里没有 numpy | **标准库移植，不加依赖，L2 不动**。G-1 当初放开 numpy 是怕「改写和移植混在一起，红了分不清」；金标门禁逐 case 定位，这个担心不再成立。scratch 实测：标准库版与兄弟仓在 785 组随机输入上最大相对差 **4.9e-16** |
| **OP-3** | ★ 兄弟仓 `baseline_rate` 的潜在缺陷：`first_seen` 只在 56 天窗里找，于是一个卖了很久的货号 90 天收缩目标被除以 56 而不是 90。实测：**每天恒卖 4 件、120 天** → `base_rate = 4.325`（高估 **8.1%**） | D-3 ①「先搬不修」：**原样移植**（金标会钉住它）。建议在 `00` W 组新登一条，修复作为单独的一次「红」，不和本计划混 |
| **OP-4** | `03` §41 的 `lead_time` 是 `(route, segment, valid_from)` 版本化表、外键指向 `pipeline_segment`（`03` §38，尚未建），种 4 行（含空运 10、国内 3）；spec §6 只说「种 30 / 60」 | 迁移 008 照 `03` §38/§41 **原文**建两张表、种 `03` 的 4 行；预测只读 `route = 'oversea_sea'`（= spec 的 30/60）。`PUT` 版本化：关当前行（`valid_to = 今天`）+ 插今天的新行；同一天再改就原地改今天那行 |
| **OP-5** | spec §6 写 `GET/PUT /v1/ui/lead-time`；本仓所有 ui 路由都是 `/v1/<资源>`（`api/__init__.py:212-218`，没有 `/ui` 段） | 用 **`/v1/lead-time`**，与现有路由一致 |
| **OP-6** | spec §6「响应头带本次用的 `params`」 | 放在响应体顶层 `params` 键（前端 mock 与真 API 同源 fixture 只比对响应体；HTTP 头进不了 fixture） |
| **OP-7** | W-6 桥接扣减、以及 as_of 所在月的剩余需求，按半月权重切出来是分数 → 月末期初/期末会是小数 | 每一段 = `ceil(月总量 × 该段半月权重之和)`，整件再按权重摊回逐日；于是所有月末数都是整数，恒等式③④精确成立。桥接段的「月总量」= `units_for_month(base_rate, 该月天数)`（与预测销量同一个函数） |
| **OP-8** | 没有 `base_rate`（新品 / Phase 2 之前认领的格子 / 不适用）时，W-6 的桥接段扣不了 | 该 msku 整条「未知」，`reason = bridge_rate_unknown`，不当 0。已有认领要**重新认领**一次才会种上 `system_basis`（`claim` 本来就幂等 upsert；属 W-7 重算时机） |
| **OP-9** | spec §4.5 只定义了「首月在 as_of 之后」的期初；计划若从 as_of 当月/更早开始，兄弟仓用 +AN（月初至 as_of 实际已售）修正，本仓 spec 没提 | 月末 ≤ as_of 的月 → `state = elapsed`；as_of 所在月 → 从 as_of 次日起按期望销量剩余份额扣（`state = partial`，不给期初，不做 +AN 修正，依据写明）；其后照常。+AN 只在 Task 4 的对账适配器里复现，不进生产 |
| **OP-10** | `ceil(rate × days)` 的浮点噪声 | 先 `round(·, 9)` 再 `ceil` —— 9 位是 **设计取值 · 未实测** |
| **OP-11** | 两遍分配的对象集合、权重全 0 与并列的处理 spec 未说 | 对象 = **本计划里**该货号下、店铺有 FBA 的已认领 msku（按 `(sid, seller_sku)` 排序）；谁都不缺且期望全为 0 → 按个数均分；余数并列时按上述顺序给前面的（照原型 `forecast_demo.py::_spread` 的「余数给权重最大」+ 稳定排序） |
| **OP-12** | 到货月里有 msku 缺口未知（期望空 / 在仓未取到）时这批货怎么分 | 整批**不分**，点名进 `unallocated`（`reason = gap_unknown`），该货号所有参与 msku 从到货日起 `inbound_unknown` —— 分给别人就等于替未知的那家做了决定 |
| **OP-13** | 计划采购格为空（null）算什么 | 不产生批次（与提交时 `skipped` 同口径），但货号行依据里逐月列出 `unfilled_purchase`，不静默 |
| **OP-14** | 货号行「在途」要含「FBA 货件（未取）」—— 按「任一未知 → 未知」这一格就永远是未知 | `in_transit = 生产中 + 货运中 + 已下单未到`，并随行给 `in_transit_excludes: ["fba_inbound"]`，界面写「不含 FBA 货件（未取）」—— 显式排除，不是当 0 |
| **OP-15** | censor 的「那天断货」怎么判（spec 只说 `afn_fulfillable_quantity ≤ 0`） | 采集日必须过 `min_rows = 7934` / `min_distinct_sid = 21` 两道（同 `pick_snapshot_date`，E-1 09-23 实测；★ 对 90 天前的历史日**未实测**）；过了的日子里该 msku **缺行或 ≤ 0** 即断货（L-1：FBA 报表不返回 0 库存的行）；没过 / 没采到的日子**不 censor**，天数进依据 |
| **OP-16** | 迁移期间新旧两套库存数并存 | Task 9 起 grid 同时返回旧 `inventory[]`（阶段 A 口径）与新 `forecast*`；前端 Task 17 起只读新的；Task 21 删掉旧的与 `forecast/projection.py`。`monthly_estimate` 在 Task 16 删；`monthly_sales_history` **保留**（阶段 D「计划 vs 实际」要月度实际） |
| **OP-17** | 前端 mock 不重跑台账 —— 改了格子 / 提前期之后 mock 的库存数会是旧的 | mock 在改动后把受影响货号的 `forecast*` 行置为 `state = unknown, reason = not_recomputed_in_mock`（显示「—」，不显示旧数）；真 API 下页面在保存结束后重拉 grid |
| **OP-18** | spec §6 把 `stockout_date` 放在 msku × 月块里 | 取「**该月内**第一个 `daily_unmet > 0` 的日子」，该月不断货为 `null` |
| **OP-19** | ② 之后「用当前数据跟运营对新基准」是人的动作 | 本计划只做到金标改指 `as_of − 7`；W-5 保持「未实测」，Task 12 在 `00` W-5 行尾补一句「② 已做，新基准待与运营对」 |

---

## 文件结构

| 文件 | 职责 | 任务 |
|---|---|---|
| `migrations/pg/008_plan_forecast.sql` | `pipeline_segment` · `lead_time` · `plan_demand_cell.system_basis` | 1 |
| `forecast/calendar.py` | 月份算术 · 半月权重 · 段内整件摊分 · 下单→完工→到货 · 向上取整 | 2 |
| `forecast/ledger.py` | 逐日台账（带符号 / 截断 / 累计卖不出去 · 恒等式 · 未知传染） | 3 |
| `tests/fixtures/recon_v1/*` · `tests/recon_adapter.py` · `tests/test_recon_ledger.py` | 兄弟仓 228 格对账门禁（台账） | 4 |
| `forecast/allocate.py` | 批次 · 两遍分配 · 整数摊分 | 5 |
| `forecast/plan_forecast.py` | 单 msku 推演 + 按月读出（Task 6）· 整张计划编排与三层汇总（Task 7） | 6, 7 |
| `api/ui/lead_time.py` | `GET/PUT /v1/lead-time` · `read_lead_time()` | 8 |
| `api/ui/grid_forecast.py` | grid 的预测块：组装入参 + 序列化 | 9 |
| `forecast/baseline.py` | `baseline_rate` 移植 · `estimate_rate` · embargo | 11, 12 |
| `tests/fixtures/baseline_golden/*` | 兄弟仓 v1 在固定输入上的金标 | 11 |
| `dim/source.py` · `dim/fixture_source.py` · `dim/ch_source.py` | 日销 + 断货日取数 | 13, 14 |
| `web/src/api/types.ts` · `web/src/pages/planGridModel.ts` | 新字段与模型 | 17 |
| `web/src/pages/PlanSheet.tsx` | 三种行的标签 | 18 |
| `web/src/pages/ForecastBasis.tsx` | 点开看依据 | 19 |
| `web/src/pages/LeadTimeBar.tsx` · `web/src/api/{client,http,mock}.ts` | 提前期设置 | 20 |

---

# Phase 1 · 台账与管道（spec §4 · §6 · §9 第 1 步）

### Task 1: 迁移 008 —— `pipeline_segment` · `lead_time` · `system_basis`

spec §6「迁移 008」· `03` §38 · `03` §41 · OP-4

**Files:**
- Create: `migrations/pg/008_plan_forecast.sql`
- Test: `tests/test_ddl_008_plan_forecast.py`（新建）

**Interfaces:**
- Consumes: 迁移由 `migrations/pg/apply.py::apply()` 在测试 session 夹具里自动应用（按文件名排序）
- Produces:
  - 表 `pipeline_segment(segment text PK, ord int UNIQUE, label text, directed boolean)`，4 行
  - 表 `lead_time(route text, segment text FK→pipeline_segment, days int CHECK 0..720, valid_from date, valid_to date, PK(route, segment, valid_from))`，4 行种子
  - 列 `plan_demand_cell.system_basis jsonb NULL`

- [ ] **Step 1: 写失败的测试**

`tests/test_ddl_008_plan_forecast.py`：

```python
"""008 的结构守卫（spec §6 · 03 §38/§41）。"""
import psycopg2
import pytest

from shared.pg_client import pg_conn


def test_pipeline_segment_is_data_not_code(business_db):
    """03 §38：段做成数据。★ directed 就是 14 §1 那条横线。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT segment, ord, directed FROM pipeline_segment ORDER BY ord")
        assert cur.fetchall() == [("making", 1, False), ("local", 2, False),
                                  ("staged", 3, True), ("shipping", 4, True)]


def test_lead_time_seed_is_the_ruled_default(business_db):
    """spec §4.4 · 03 §41：oversea_sea 生产 30 / 货运 60（负责人 09-24）。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT route, segment, days FROM lead_time"
                    " WHERE valid_from = DATE '2026-01-01' ORDER BY route, segment")
        assert cur.fetchall() == [("air", "shipping", 10), ("domestic", "shipping", 3),
                                  ("oversea_sea", "making", 30), ("oversea_sea", "shipping", 60)]


@pytest.mark.parametrize("days", [-1, 721])
def test_lead_time_days_range_is_enforced(business_db, days):
    with pytest.raises(psycopg2.errors.CheckViolation), pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO lead_time (route, segment, days, valid_from)"
                    " VALUES ('oversea_sea', 'making', %s, DATE '2030-01-01')", (days,))


def test_lead_time_segment_must_exist(business_db):
    with pytest.raises(psycopg2.errors.ForeignKeyViolation), pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO lead_time (route, segment, days, valid_from)"
                    " VALUES ('oversea_sea', 'flying', 3, DATE '2030-01-01')")


def test_system_basis_is_a_nullable_jsonb_column(business_db):
    """spec §3.3：依据随数一起存。★ 可空 —— 不适用 / 还没算过的格子没有依据。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT data_type, is_nullable FROM information_schema.columns"
                    " WHERE table_schema = current_schema() AND table_name = 'plan_demand_cell'"
                    "   AND column_name = 'system_basis'")
        assert cur.fetchone() == ("jsonb", "YES")
```

- [ ] **Step 2: 跑，确认失败**

Run: `.venv/bin/python -m pytest -q tests/test_ddl_008_plan_forecast.py`
Expected: FAIL —— `psycopg2.errors.UndefinedTable: relation "pipeline_segment" does not exist`（system_basis 那条是 `assert None == ('jsonb', 'YES')`）

- [ ] **Step 3: 写迁移**

`migrations/pg/008_plan_forecast.sql`：

```sql
-- 008 · 计划整表的预测销量与预测库存
-- 出处：docs/superpowers/specs/2026-09-24-plan-forecast-design.md §6；表结构照 03 §38 / §41 原文。
-- ★ 不写 scm. 前缀、不写 BEGIN/COMMIT：同 001 的理由（事务边界由 apply() 持有）。

-- spec §3.3：预测销量的依据随数一起存（base_rate、窗口、censor 天数……）。
-- ★ 可空：不适用 / 没算过的格子没有依据，空 ≠ 「依据为空对象」。
ALTER TABLE plan_demand_cell ADD COLUMN system_basis jsonb;

-- 03 §38：段做成数据，不做成代码。★ directed 就是 14 §1 那条横线。
CREATE TABLE pipeline_segment (
    segment  text PRIMARY KEY,
    ord      int  NOT NULL UNIQUE,
    label    text NOT NULL,
    directed boolean NOT NULL
);

INSERT INTO pipeline_segment (segment, ord, label, directed) VALUES
    ('making',   1, '生产中',            false),
    ('local',    2, '本地仓在库(待排货)', false),
    ('staged',   3, '排货中(已排未发)',   true ),
    ('shipping', 4, '货运中(已发未到)',   true );

-- 03 §41：提前期（★ 天，用户自己改；不许写死在代码里）。
-- ★ 当前生效行 = valid_to IS NULL。改动走 PUT /v1/lead-time：关旧行、插新行（OP-4）。
CREATE TABLE lead_time (
    route      text NOT NULL,
    segment    text NOT NULL REFERENCES pipeline_segment(segment),
    days       integer NOT NULL CHECK (days >= 0 AND days <= 720),
    valid_from date NOT NULL,
    valid_to   date,
    PRIMARY KEY (route, segment, valid_from)
);

INSERT INTO lead_time (route, segment, days, valid_from) VALUES
    ('oversea_sea', 'making',   30, DATE '2026-01-01'),
    ('oversea_sea', 'shipping', 60, DATE '2026-01-01'),
    ('air',         'shipping', 10, DATE '2026-01-01'),
    ('domestic',    'shipping',  3, DATE '2026-01-01');
```

- [ ] **Step 4: 跑，确认通过；全量回归**

Run: `.venv/bin/python -m pytest -q tests/test_ddl_008_plan_forecast.py tests/test_migrations.py`
Expected: PASS（5 + 既有）

- [ ] **Step 5: 提交**

```bash
git add migrations/pg/008_plan_forecast.sql tests/test_ddl_008_plan_forecast.py
git commit -m "feat(db): 008 —— lead_time 提前期表（03 §41，种 30/60）+ pipeline_segment + system_basis 依据列"
```

---

### Task 2: `forecast/calendar.py` —— 半月摊分 · 15 号锚 · 向上取整

spec §4.2（半月均分，Σ每日 ≡ 月期望）· §4.4（15 号锚）· §3.3（向上取整）· §7（10 月 + 30 + 60 → 2027-01）· OP-7 · OP-10

**Files:**
- Create: `forecast/calendar.py`
- Test: `tests/test_forecast_calendar.py`（新建）

**Interfaces:**
- Produces（后续任务全部依赖这些名字）：
  - `ANCHOR_DAY: int = 15`
  - `month_start(d: dt.date) -> dt.date` · `days_in_month(d: dt.date) -> int` · `month_end(d: dt.date) -> dt.date` · `add_months(m: dt.date, n: int) -> dt.date` · `span(first: dt.date, last: dt.date) -> list[dt.date]`（含两端）
  - `halfmonth_weight(d: dt.date) -> Fraction` —— 这一天占当月总量的份额
  - `halfmonth_daily(total: int | Fraction, month: dt.date) -> dict[dt.date, Fraction]`
  - `segment_units(total: int, first: dt.date, last: dt.date) -> int` —— `ceil(total × Σ权重[first..last])`，first/last 同月
  - `segment_daily(total: int, first: dt.date, last: dt.date) -> dict[dt.date, Fraction]` —— 把 `segment_units` 件按权重摊回每天，Σ ≡ `segment_units`
  - `order_arrival(order_period: dt.date, make_days: int, ship_days: int, anchor_day: int = ANCHOR_DAY) -> tuple[dt.date, dt.date, dt.date]` —— `(placed, made, arrive)`
  - `units_for_month(rate: float, days: int) -> int`

- [ ] **Step 1: 写失败的测试**

`tests/test_forecast_calendar.py`：

```python
import datetime as dt
from fractions import Fraction

import pytest

from forecast.calendar import (
    ANCHOR_DAY, add_months, days_in_month, halfmonth_daily, halfmonth_weight, month_end,
    order_arrival, segment_daily, segment_units, units_for_month,
)

D = dt.date


@pytest.mark.parametrize("month", [D(2026, 10, 1), D(2026, 11, 1), D(2027, 2, 1), D(2028, 2, 1)])
def test_halfmonth_split_keeps_the_month_total_exactly(month):
    """spec §4.2：摊分不改月总数 —— Σ当月每日 ≡ 期望销量[月]，且 15 号累计正好一半。"""
    daily = halfmonth_daily(100, month)
    assert len(daily) == days_in_month(month)
    assert sum(daily.values()) == 100
    assert sum(v for d, v in daily.items() if d.day <= 15) == 50


def test_halfmonth_weight_values():
    assert halfmonth_weight(D(2026, 10, 15)) == Fraction(1, 30)
    assert halfmonth_weight(D(2026, 10, 16)) == Fraction(1, 32)   # 31 天月：后半 16 天
    assert halfmonth_weight(D(2027, 2, 16)) == Fraction(1, 26)    # 28 天月：后半 13 天


def test_segment_units_rounds_up_and_full_month_is_exact():
    """OP-7：段 = ceil(月总量 × 段权重)。"""
    # 9/22~9/30：9 天 × 1/30 = 0.3
    assert segment_units(120, D(2026, 9, 22), D(2026, 9, 30)) == 36
    assert segment_units(65, D(2026, 9, 22), D(2026, 9, 30)) == 20        # 19.5 → 20
    # 10/11~10/31：5×1/30 + 16×1/32 = 2/3
    assert segment_units(30, D(2026, 10, 11), D(2026, 10, 31)) == 20
    assert segment_units(137, D(2026, 10, 1), D(2026, 10, 31)) == 137


def test_segment_daily_sums_to_segment_units():
    daily = segment_daily(65, D(2026, 9, 22), D(2026, 9, 30))
    assert sorted(daily) == [D(2026, 9, d) for d in range(22, 31)]
    assert sum(daily.values()) == 20


def test_segment_must_stay_inside_one_month():
    with pytest.raises(ValueError):
        segment_units(10, D(2026, 9, 30), D(2026, 10, 1))


def test_order_in_october_with_30_plus_60_arrives_in_january():
    """spec §7 · 14 §2.3：10 月下单、30 + 60 天 → 2027-01（15 号锚）。"""
    placed, made, arrive = order_arrival(D(2026, 10, 1), 30, 60)
    assert ANCHOR_DAY == 15
    assert placed == D(2026, 10, 15)
    assert made == D(2026, 11, 14)          # 14 §2.3「10 月下单 + 生产 30 天 → 2026-11 完工」
    assert arrive == D(2027, 1, 13)


def test_domestic_33_days_lands_in_november():
    """14 §2.3：10 月下单 + 国内 33 天 → 2026-11 可售。"""
    assert order_arrival(D(2026, 10, 1), 30, 3)[2] == D(2026, 11, 17)


def test_units_for_month_rounds_up():
    """spec §3.3：向上取整。"""
    assert units_for_month(4.13, 30) == 124        # spec §5 的算例
    assert units_for_month(4.325139122267326, 31) == 135


def test_units_for_month_ignores_float_noise():
    """Review Focus 4：1.1 × 30 = 33.000000000000004 —— 不许多出一件（OP-10）。"""
    assert 1.1 * 30 > 33
    assert units_for_month(1.1, 30) == 33


def test_month_helpers():
    assert month_end(D(2027, 2, 3)) == D(2027, 2, 28)
    assert add_months(D(2026, 11, 1), 3) == D(2027, 2, 1)
```

- [ ] **Step 2: 跑，确认失败**

Run: `.venv/bin/python -m pytest -q tests/test_forecast_calendar.py`
Expected: FAIL —— `ModuleNotFoundError: No module named 'forecast.calendar'`

- [ ] **Step 3: 实现**

`forecast/calendar.py`：

```python
"""天算、月显示（14 §2.3）与半月均分（spec §4.2）。纯函数。"""
from __future__ import annotations

import calendar as _cal
import datetime as dt
import math
from fractions import Fraction

#: 14 §2.3 · M-12：下单月 15 号。★ 不开放（spec §4.4）。
ANCHOR_DAY = 15


def month_start(d: dt.date) -> dt.date:
    return d.replace(day=1)


def days_in_month(d: dt.date) -> int:
    return _cal.monthrange(d.year, d.month)[1]


def month_end(d: dt.date) -> dt.date:
    return d.replace(day=days_in_month(d))


def add_months(m: dt.date, n: int) -> dt.date:
    k = m.year * 12 + m.month - 1 + n
    return dt.date(k // 12, k % 12 + 1, 1)


def span(first: dt.date, last: dt.date) -> list[dt.date]:
    return [first + dt.timedelta(days=i) for i in range((last - first).days + 1)]


def halfmonth_weight(d: dt.date) -> Fraction:
    """前半月（1..15）每天 1/30，后半月每天 1/(2·(月长−15))：两个半月各拿一半。
    ★ 与兄弟仓 calendar.halfmonth_weights 同一约定（formulas.md §3.3 第一种）。"""
    n = days_in_month(d)
    return Fraction(1, 30) if d.day <= 15 else Fraction(1, 2 * (n - 15))


def halfmonth_daily(total: int | Fraction, month: dt.date) -> dict[dt.date, Fraction]:
    m = month_start(month)
    out = {d: Fraction(total) * halfmonth_weight(d) for d in span(m, month_end(m))}
    assert sum(out.values()) == Fraction(total), "半月摊分改了月总数"
    return out


def _check_segment(first: dt.date, last: dt.date) -> None:
    if month_start(first) != month_start(last) or first > last:
        raise ValueError(f"段必须在同一个月内且 first ≤ last：{first} ~ {last}")


def segment_units(total: int, first: dt.date, last: dt.date) -> int:
    """同一个月里 [first, last] 分到的**整数**件数（OP-7：向上取整，与预测销量同向）。"""
    _check_segment(first, last)
    return math.ceil(Fraction(total) * sum(halfmonth_weight(d) for d in span(first, last)))


def segment_daily(total: int, first: dt.date, last: dt.date) -> dict[dt.date, Fraction]:
    """把 segment_units 件按半月权重摊回每天。★ Σ ≡ segment_units（构造保证）——
    于是所有月末头寸都是整数，恒等式③④精确成立。"""
    units = segment_units(total, first, last)
    days = span(first, last)
    ws = [halfmonth_weight(d) for d in days]
    w = sum(ws)
    out = {d: Fraction(units) * x / w for d, x in zip(days, ws)}
    assert sum(out.values()) == units
    return out


def order_arrival(order_period: dt.date, make_days: int, ship_days: int,
                  anchor_day: int = ANCHOR_DAY) -> tuple[dt.date, dt.date, dt.date]:
    """(下单日, 完工日, 到货日)。下单日 = 下单月的 anchor_day 号（14 §2.3 期望值模型）。"""
    placed = month_start(order_period).replace(day=anchor_day)
    made = placed + dt.timedelta(days=make_days)
    return placed, made, made + dt.timedelta(days=ship_days)


def units_for_month(rate: float, days: int) -> int:
    """spec §3.3：预测销量向上取整（17 表 system_value）。
    ★ 先 round 到 9 位（设计取值 · 未实测，OP-10）：1.1×30 在 float 里是 33.000000000000004，
      直接 ceil 会凭空多出一件。"""
    return math.ceil(round(rate * days, 9))
```

- [ ] **Step 4: 跑，确认通过 + 分层**

Run: `.venv/bin/python -m pytest -q tests/test_forecast_calendar.py tests/test_layering.py`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add forecast/calendar.py tests/test_forecast_calendar.py
git commit -m "feat(forecast): 天算月显示 —— 半月摊分(Σ≡月期望)、15 号锚、段内整件、向上取整"
```

---

### Task 3: `forecast/ledger.py` —— 逐日台账

spec §4.1（三式 + 恒等式）· §4.2（空 → 未知向后传染）· §4.5（断货日来自截断台账）

**Files:**
- Create: `forecast/ledger.py`
- Test: `tests/test_forecast_ledger.py`（新建）

**Interfaces:**
- Consumes: 无（`fractions.Fraction`）
- Produces:
  - `@dataclass(frozen=True) class DailyLedger`：`start: dt.date`（第一天 = as_of + 1）· `onhand0: Fraction` · `signed: tuple[Fraction | None, ...]` · `available: tuple[...]` · `daily_unmet: tuple[...]` · `unknown_from: dt.date | None`
  - `DailyLedger.signed_at(d: dt.date) -> Fraction | None` —— `d == start − 1` 时返回 `onhand0`
  - `DailyLedger.first_unmet(first: dt.date, last: dt.date) -> dt.date | None`
  - `run_ledger(onhand0: int | Fraction, start: dt.date, demand: list[Fraction | None], arrivals: list[Fraction | None]) -> DailyLedger`

- [ ] **Step 1: 写失败的测试**

`tests/test_forecast_ledger.py`：

```python
import datetime as dt
from fractions import Fraction as F

import pytest

from forecast.ledger import run_ledger

D = dt.date


def test_signed_and_available_follow_the_three_formulas():
    """spec §4.1：可售 = max(0, 昨天 + 到货 − 卖)；卖不出去 = max(0, 卖 − 昨天 − 到货)；带符号不截断。"""
    L = run_ledger(5, D(2026, 10, 1), [F(3), F(4), F(2)], [F(0), F(0), F(10)])
    assert L.signed == (F(2), F(-2), F(6))
    assert L.available == (F(2), F(0), F(8))
    assert L.daily_unmet == (F(0), F(2), F(0))
    assert L.unknown_from is None


def test_identity_signed_equals_available_minus_cumulative_unmet():
    """spec §4.1 恒等式：带符号头寸 ≡ 可售 − 累计卖不出去（每一天）。"""
    L = run_ledger(1, D(2026, 10, 1), [F(2)] * 5, [F(0), F(0), F(3), F(0), F(0)])
    cum = F(0)
    for s, a, u in zip(L.signed, L.available, L.daily_unmet):
        cum += u
        assert s == a - cum


def test_today_arrival_serves_today_demand():
    """FBA 先入库后出单（兄弟仓 ledger.project 的约定）。"""
    L = run_ledger(0, D(2026, 10, 1), [F(5)], [F(5)])
    assert L.daily_unmet == (F(0),)


def test_unknown_demand_propagates_forward_not_as_zero():
    """spec §4.2 · 14 B1/B2：空 ≠ 0，而且从那天起向后全部未知。"""
    L = run_ledger(10, D(2026, 10, 1), [F(1), None, F(1)], [F(0), F(0), F(0)])
    assert L.signed == (F(9), None, None)
    assert L.unknown_from == D(2026, 10, 2)


def test_unknown_arrival_also_propagates():
    L = run_ledger(10, D(2026, 10, 1), [F(1), F(1)], [None, F(0)])
    assert L.signed == (None, None)
    assert L.unknown_from == D(2026, 10, 1)


def test_signed_at_the_day_before_start_is_the_opening():
    L = run_ledger(200, D(2026, 10, 1), [F(1)], [F(0)])
    assert L.signed_at(D(2026, 9, 30)) == 200
    assert L.signed_at(D(2026, 10, 1)) == 199


def test_first_unmet_is_the_first_day_that_could_not_be_served():
    """spec §4.5：预计断货日 = 截断台账里第一个 daily_unmet > 0 的日子。"""
    L = run_ledger(7, D(2026, 10, 1), [F(4, 3)] * 10, [F(0)] * 10)
    assert L.first_unmet(D(2026, 10, 1), D(2026, 10, 10)) == D(2026, 10, 6)
    assert L.first_unmet(D(2026, 10, 1), D(2026, 10, 5)) is None


def test_negative_inputs_are_rejected():
    with pytest.raises(ValueError):
        run_ledger(-1, D(2026, 10, 1), [F(0)], [F(0)])
    with pytest.raises(ValueError):
        run_ledger(0, D(2026, 10, 1), [F(-1)], [F(0)])
    with pytest.raises(ValueError):
        run_ledger(0, D(2026, 10, 1), [F(0), F(0)], [F(0)])
```

- [ ] **Step 2: 跑，确认失败**

Run: `.venv/bin/python -m pytest -q tests/test_forecast_ledger.py`
Expected: FAIL —— `ModuleNotFoundError: No module named 'forecast.ledger'`

- [ ] **Step 3: 实现**

`forecast/ledger.py`：

```python
"""逐日台账（spec §4.1 · 兄弟仓 ledger.py / formulas.md §一）。纯函数。

★ 用 Fraction 而不是 float：半月摊分的每日量是分数，float 累加会让「月末 ≡ 期初 − 期望 + 入库」
  只在 1e-9 意义下成立 —— 恒等式应当逐位成立，不是「差不多」。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from fractions import Fraction

_ZERO = Fraction(0)


@dataclass(frozen=True)
class DailyLedger:
    start: dt.date
    onhand0: Fraction
    signed: tuple[Fraction | None, ...]
    available: tuple[Fraction | None, ...]
    daily_unmet: tuple[Fraction | None, ...]
    unknown_from: dt.date | None

    def signed_at(self, d: dt.date) -> Fraction | None:
        i = (d - self.start).days
        if i == -1:
            return self.onhand0
        if not 0 <= i < len(self.signed):
            raise ValueError(f"{d} 不在台账 [{self.start - dt.timedelta(days=1)}, ...] 内")
        return self.signed[i]

    def first_unmet(self, first: dt.date, last: dt.date) -> dt.date | None:
        for i in range((first - self.start).days, (last - self.start).days + 1):
            u = self.daily_unmet[i]
            if u is not None and u > 0:
                return self.start + dt.timedelta(days=i)
        return None


def run_ledger(onhand0: int | Fraction, start: dt.date,
               demand: list[Fraction | None], arrivals: list[Fraction | None]) -> DailyLedger:
    if len(demand) != len(arrivals):
        raise ValueError(f"demand {len(demand)} 天 ≠ arrivals {len(arrivals)} 天")
    s = a = Fraction(onhand0)
    if s < 0:
        raise ValueError(f"期初可售为负：{onhand0}")
    cum = _ZERO
    signed: list[Fraction | None] = []
    avail: list[Fraction | None] = []
    unmet: list[Fraction | None] = []
    unknown_from: dt.date | None = None
    for i, (dem, arr) in enumerate(zip(demand, arrivals)):
        if unknown_from is None and (dem is None or arr is None):
            unknown_from = start + dt.timedelta(days=i)
        if unknown_from is not None:
            signed.append(None)
            avail.append(None)
            unmet.append(None)
            continue
        if dem < 0 or arr < 0:
            raise ValueError(f"{start + dt.timedelta(days=i)} 需求 {dem} / 到货 {arr} 为负")
        short = max(_ZERO, dem - a - arr)
        a = max(_ZERO, a + arr - dem)
        s = s + arr - dem
        cum += short
        if s != a - cum:   # ★ spec §4.1 恒等式：每轮断言
            raise AssertionError(f"台账恒等式被破坏：{s} ≠ {a} − {cum}")
        signed.append(s)
        avail.append(a)
        unmet.append(short)
    return DailyLedger(start, Fraction(onhand0), tuple(signed), tuple(avail), tuple(unmet),
                       unknown_from)
```

- [ ] **Step 4: 跑，确认通过；再弄失败一次**

Run: `.venv/bin/python -m pytest -q tests/test_forecast_ledger.py`
Expected: PASS（8）

临时把 `a = max(_ZERO, a + arr - dem)` 改成 `a = a + arr - dem`（不截断）再跑：`test_signed_and_available_follow_the_three_formulas` 必须红（`available` 出现 `-2`）。改回。

- [ ] **Step 5: 提交**

```bash
git add forecast/ledger.py tests/test_forecast_ledger.py
git commit -m "feat(forecast): 逐日台账 —— 带符号/截断两视图 + 恒等式每轮断言 + 未知向后传染"
```

---

### Task 4: 兄弟仓 228 格对账门禁（台账）—— 搬，必须一次绿

spec §7「228 格 recon 一次绿」· `00` D-3 ① · ★ OP-1（这 228 格是**台账**门禁，不是 base_rate 门禁）

**Files:**
- Create: `tests/fixtures/recon_v1/`（9 个 CSV **逐字节拷贝** + `README.md`）
- Create: `tests/recon_adapter.py`（把兄弟仓 compat 口径的输入还原成逐日需求/到货 —— ★ 只在测试里，不进生产：+AN 修正与供应事件筛选是 Excel 的口径，不是本仓的）
- Test: `tests/test_recon_ledger.py`（新建）

**Interfaces:**
- Consumes: `forecast.calendar.halfmonth_daily`、`forecast.ledger.run_ledger`（Task 2/3）
- Produces: 无生产接口

- [ ] **Step 1: 逐字节搬 fixture 并核对哈希**

```bash
SRC=/home/fido/work/2026/jxd_model_group/model_inventory_forecast/models/inventory_forecast/tests/fixtures
mkdir -p tests/fixtures/recon_v1
cp "$SRC"/{demand_history,identity,lanes,locations,onhand,overrides,recon_components,recon_expected,supply_events}.csv tests/fixtures/recon_v1/
git -C /home/fido/work/2026/jxd_model_group/model_inventory_forecast rev-parse HEAD
sha256sum tests/fixtures/recon_v1/*.csv
```

Expected（sha256 前 12 位）：`demand_history 2f5342c8a6e8` · `identity 69c76ca0e5bb` · `lanes 959390438b36` · `locations 79eedc16ece7` · `onhand dce3eb03bdf6` · `overrides 73f2b6c58faa` · `recon_components 95c361f03cfc` · `recon_expected 7516d5a42121` · `supply_events 355217e302d6`。对不上就停。

写 `tests/fixtures/recon_v1/README.md`：

```markdown
# 兄弟仓 228 格对账 fixture（冻结）

逐字节拷自 `jxd_model_group/model_inventory_forecast/models/inventory_forecast/tests/fixtures/`
（兄弟仓 commit `<上一步 rev-parse 的输出>`，2026-09-24 拷贝），来源见那边的 README：
2026-08-21 从运营 Excel（lucy-库存盘点 - A4PET.xlsx）一次性导出。

★ 这是**台账**门禁（Open point OP-1）：期望值是运营 Excel 的半月检查点余额，需求来自运营手填的
`overrides`，不经过 `baseline_rate`。`identity.csv` / `locations.csv` 在 compat 口径下不参与计算，
一并搬来是为了与 00 D-3「搬 9 个 CSV」逐字一致。

★ 不许手工编辑。要换基准就新建 `recon_v2/` 并写明理由。
```

- [ ] **Step 2: 写对账适配器**

`tests/recon_adapter.py`：

```python
"""把兄弟仓 compat 口径的 9 个 CSV 还原成本仓台账的输入（00 D-3 ①）。

★ 只在测试里：+AN 修正（月初至锚点的实际已售）与供应事件筛选是**运营 Excel 的口径**，
  本仓生产走 spec §4.2 的 W-6（Open point OP-9）。这里复现它们只为证明
  「半月摊分 + 逐日台账」这两块生产代码与 Excel 逐格相同。
  规则逐条对照兄弟仓 recon.run / supply.live_events / supply.arrival_dates /
  demand.build_daily_demand（spread 修正）。
"""
from __future__ import annotations

import csv
import datetime as dt
from fractions import Fraction as F
from pathlib import Path

from forecast.calendar import halfmonth_daily
from forecast.ledger import run_ledger

FX = Path(__file__).parent / "fixtures" / "recon_v1"
ANCHOR = dt.date(2026, 8, 4)          # 兄弟仓 recon.ANCHOR_DEFAULT
HORIZON = 120                         # RunConfig.horizon_days
CHECKPOINTS = ("2026-08-15", "2026-08-31", "2026-09-15",
               "2026-09-30", "2026-10-15", "2026-10-31")
DELIVERED = "送达"
MAX_TIER = 2                          # Tier.MANUAL_EXCEL


def rows(name: str) -> list[dict]:
    with open(FX / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _num(s: str | None) -> F | None:
    s = (s or "").strip()
    return None if s == "" else F(s)


def _d(s: str) -> dt.date:
    return dt.date.fromisoformat(s[:10])


def signed_positions(*, halfmonth: bool = True, anchor_correction: bool = True
                     ) -> dict[tuple[str, str], list[F]]:
    """(sku, node) → 锚点次日起 120 天的带符号头寸。两个开关只给「承重」测试用。"""
    dates = [ANCHOR + dt.timedelta(days=i + 1) for i in range(HORIZON)]
    oh = rows("onhand.csv")
    nodes = sorted({r["node"] for r in oh if r["node"]})
    series = sorted({(r["sku"], r["node"]) for r in oh if r["node"] in nodes})
    onhand0 = {k: F(0) for k in series}
    for r in oh:
        if r["node"] in nodes and _d(r["date"]) == ANCHOR:
            onhand0[(r["sku"], r["node"])] = _num(r["qty"])

    # ---- 到货：live_events + arrival_dates(point) ----
    p50 = {r["lane_id"]: float(r["p50_days"]) for r in rows("lanes.csv")}
    floor, horizon_end = dates[0], dates[-1]
    arr = {k: [F(0)] * HORIZON for k in series}
    for e in rows("supply_events.csv"):
        tier = e["tier"].strip()
        if tier == "" or int(float(tier)) > MAX_TIER or e["status"] == DELIVERED:
            continue
        qty = _num(e["qty"])
        if qty is None or qty == 0 or e["dest_node"] not in nodes:
            continue
        eta = _d(e["eta_date"]) if e["eta_date"] else None
        if eta is not None and eta > horizon_end:
            continue
        if eta is None and e["ship_date"] and e["lane_id"] in p50:
            eta = _d(e["ship_date"]) + dt.timedelta(days=p50[e["lane_id"]])
        eta = max(eta or floor, floor)          # 缺失 → 锚点次日；逾期 → 夹到锚点次日
        col, key = (eta - dates[0]).days, (e["sku"], e["dest_node"])
        if 0 <= col < HORIZON and key in arr:
            arr[key][col] += qty

    # ---- 需求：overrides 半月摊分 + spread 修正（+AN） ----
    plan: dict[tuple[str, str], dict[dt.date, F]] = {}
    for r in rows("overrides.csv"):
        if r["kind"] == "absolute":
            plan.setdefault((r["sku"], r["node"]), {})[_d(r["period_start"]).replace(day=1)] = F(r["value"])
    realized: dict[tuple[str, str], F] = {}
    for r in rows("demand_history.csv"):
        if dt.date(2026, 8, 1) <= _d(r["date"]) <= ANCHOR:
            k = (r["sku"], r["node"])
            realized[k] = realized.get(k, F(0)) + F(r["units"])
    full = [dt.date(2026, 8, 1) + dt.timedelta(days=i)
            for i in range((horizon_end - dt.date(2026, 8, 1)).days + 1)]
    offset = (dates[0] - full[0]).days
    bucket_days = sum(1 for d in dates if d <= dt.date(2026, 8, 15))
    out = {}
    for k in series:
        months = plan.get(k)
        dem = [F(0)] * HORIZON
        if months:
            per_day: dict[dt.date, F] = {}
            for m, total in months.items():
                if halfmonth:
                    per_day.update(halfmonth_daily(total, m))
                else:                                   # 承重测试：按天均摊
                    n = len([d for d in full if d.replace(day=1) == m]) or 1
                    per_day.update({d: total / n for d in full if d.replace(day=1) == m})
            vals = [per_day.get(d, F(0)) for d in full]
            dem = vals[offset:offset + HORIZON]
            resid = sum(vals[:offset], F(0)) - realized.get(k, F(0))
            if anchor_correction and resid != 0:
                dem = [v + resid / bucket_days if i < bucket_days else v for i, v in enumerate(dem)]
                dem = [max(v, F(0)) for v in dem]
        L = run_ledger(onhand0[k], dates[0], dem, arr[k])
        out[k] = list(L.signed)
    return out


def excel_formula(r: dict) -> dict[str, F]:
    """运营表 BB..BG 的公式（兄弟仓 recon._EXCEL_FORMULAS），套它自己的缓存分量。"""
    g = lambda c: F(r[c])  # noqa: E731
    y = g("J_onhand") + g("N") + g("O") + g("P") + g("Q")
    ai = y + g("R")
    aj = ai + g("S")
    ak = aj + g("V")
    al = ak + g("W")
    am = al + g("X")
    an, aq, ar, as_ = g("AN_sold"), g("AQ_aug"), g("AR_sep"), g("AS_oct")
    return {"2026-08-15": y + an - aq / 2, "2026-08-31": ai + an - aq,
            "2026-09-15": aj + an - aq - ar / 2, "2026-09-30": ak + an - aq - ar,
            "2026-10-15": al + an - aq - ar - as_ / 2, "2026-10-31": am + an - aq - ar - as_}


def at(positions: dict, key: tuple[str, str], checkpoint: str) -> F:
    return positions[key][(dt.date.fromisoformat(checkpoint) - ANCHOR).days - 1]
```

- [ ] **Step 3: 写门禁测试**

`tests/test_recon_ledger.py`：

```python
"""★ 00 D-3 ①：兄弟仓 228 格对账门禁（台账）。必须一次绿 —— 红了就是搬错了，且能定位到格。"""
import pytest

from tests.recon_adapter import CHECKPOINTS, at, excel_formula, rows, signed_positions


@pytest.fixture(scope="module")
def positions():
    return signed_positions()


@pytest.fixture(scope="module")
def target():
    return {(r["sku"], r["node"]): excel_formula(r) for r in rows("recon_components.csv")}


def test_every_one_of_228_cells_matches_exactly(positions, target):
    bad = [(k, cp, float(at(positions, k, cp)), float(v))
           for k, cps in target.items() for cp, v in cps.items() if at(positions, k, cp) != v]
    assert sum(len(c) for c in target.values()) == 228 == 38 * len(CHECKPOINTS)
    assert bad == [], f"{len(bad)} 格不符：{bad[:10]}"


def test_negatives_survive(positions, target):
    """Excel 用负余额表达缺口 —— 带符号头寸必须保留负数（spec §4.5 · 14 B8）。"""
    assert any(v < 0 for cps in target.values() for v in cps.values())
    assert any(at(positions, k, cp) < 0 for k in target for cp in CHECKPOINTS)


def test_values_land_on_half_units(positions, target):
    """半月均分让检查点落在 0.5 的整数倍上。"""
    assert all((2 * at(positions, k, cp)).denominator == 1 for k in target for cp in CHECKPOINTS)


def test_workbook_has_exactly_two_stale_cells(target):
    """运营表里 2 格缓存与自身公式矛盾（兄弟仓 test_workbook_has_two_stale_cells）。"""
    from fractions import Fraction as F
    cached = {(r["sku"], r["node"], r["checkpoint"]): F(r["expected"])
              for r in rows("recon_expected.csv")}
    stale = [(k[0], cp, cached[(*k, cp)] - v) for k, cps in target.items()
             for cp, v in cps.items() if cached[(*k, cp)] != v]
    assert [(s, cp) for s, cp, _ in stale] == [("DCC1045014LG", "2026-08-31"),
                                               ("DCC1045014LG", "2026-09-15")]
    assert all(abs(d) == 1 for _, _, d in stale)


def test_model_matches_cache_wherever_cache_is_fresh(positions, target):
    from fractions import Fraction as F
    cached = {(r["sku"], r["node"], r["checkpoint"]): F(r["expected"])
              for r in rows("recon_expected.csv")}
    fresh = [(k, cp) for k, cps in target.items() for cp, v in cps.items()
             if cached[(*k, cp)] == v]
    assert len(fresh) == 226
    assert all(at(positions, k, cp) == cached[(*k, cp)] for k, cp in fresh)


def test_halfmonth_split_is_load_bearing(target):
    """换成按天均摊就必须对不上 —— 证明半月均分是被建模的约定，不是碰巧。"""
    flat = signed_positions(halfmonth=False)
    assert any(at(flat, k, cp) != v for k, cps in target.items() for cp, v in cps.items())


def test_anchor_correction_is_load_bearing(target):
    """关掉 +AN 就必须对不上 —— 证明适配器真的在复现 Excel 的锚点修正。"""
    raw = signed_positions(anchor_correction=False)
    assert any(at(raw, k, cp) != v for k, cps in target.items() for cp, v in cps.items())


def test_delivered_events_are_excluded():
    """`<>送达` 是支点：已送达的货在在库快照里，再算一次就双重计数。"""
    ev = rows("supply_events.csv")
    assert sum(1 for e in ev if e["status"] == "送达") == 4407
```

- [ ] **Step 4: 跑 —— 必须一次绿**

Run: `.venv/bin/python -m pytest -q tests/test_recon_ledger.py`
Expected: PASS（8）。★ **不许为了变绿改生产代码或 fixture**；红了就逐格对照兄弟仓 `recon.run` 的步骤找搬错的那一步（scratch 已验证本适配器 228/228 逐格相等）。

然后弄失败一次：临时把 `forecast/calendar.py::halfmonth_weight` 的 `Fraction(1, 30)` 改成 `Fraction(1, 31)` 再跑，`test_every_one_of_228_cells_matches_exactly` 与 `tests/test_forecast_calendar.py` 都必须红。改回。

- [ ] **Step 5: 提交**

```bash
git add tests/fixtures/recon_v1 tests/recon_adapter.py tests/test_recon_ledger.py
git commit -m "test(forecast): 搬兄弟仓 9 个 fixture + 228 格对账 —— 台账逐格一次绿（D-3 ①，OP-1）"
```

---

### Task 5: `forecast/allocate.py` —— 批次与两遍分配

spec §4.4（两遍法、Σ分到 ≡ 这批件数、不写表）· `14` §3.1–§3.2 · 原型 `docs/prototype/forecast_demo.py:343-455` · OP-11 · OP-12

**Files:**
- Create: `forecast/allocate.py`
- Test: `tests/test_forecast_allocate.py`（新建）

**Interfaces:**
- Consumes: `forecast.calendar.month_start`
- Produces:
  - `Key = tuple[str, str]` —— `(seller_sku, sid)`
  - `@dataclass(frozen=True) class Batch`: `sku: str, order_period: dt.date, units: int, placed: dt.date, made: dt.date, arrive: dt.date`
  - `@dataclass(frozen=True) class Give`: `key: Key, units: int, gap_part: int, weight_part: int, rule: str, pool: int`
  - `spread(amount: int, weights: dict[Key, int], order: list[Key]) -> dict[Key, int]`
  - `allocate(batches: list[Batch], keys: list[Key], baseline: dict[Key, dict[dt.date, int | None]], weights: dict[Key, dict[dt.date, int | None]]) -> tuple[dict[Batch, list[Give]], list[dict]]` —— `baseline[k][月] = 不含计划采购的月末带符号头寸`；返回 `(每批分给谁, 没分出去的批次)`，后者每项 `{"sku", "order_period": "YYYY-MM", "arrive": "YYYY-MM-DD", "units", "reason": "gap_unknown" | "no_fba_msku", "unknown": [[seller_sku, sid], ...]}`

- [ ] **Step 1: 写失败的测试**

`tests/test_forecast_allocate.py`：

```python
import datetime as dt

import pytest

from forecast.allocate import Batch, allocate, spread

D = dt.date
A, C = ("MSKU-A", "11072"), ("MSKU-C", "11094")
NOV = D(2026, 11, 1)


def batch(units, arrive=D(2026, 11, 14)):
    return Batch("DCC1800264G1", D(2026, 10, 1), units, D(2026, 10, 15), D(2026, 10, 25), arrive)


def given(res, b):
    return {g.key: g.units for g in res[0][b]}


def test_gap_first_then_rest_by_expected_sales():
    """14 §3.1 缺口 ≤ 这批：先补缺口（C 缺 77），余量 123 按 11 月期望 120:44 摊 → 90 / 33。"""
    b = batch(200)
    res = allocate([b], [A, C], {A: {NOV: 44}, C: {NOV: -77}}, {A: {NOV: 120}, C: {NOV: 44}})
    assert given(res, b) == {A: 90, C: 110}
    gc = next(g for g in res[0][b] if g.key == C)
    assert (gc.gap_part, gc.weight_part, gc.pool) == (77, 33, 200)
    assert res[1] == []


def test_nobody_short_still_ships_everything_by_expected_sales():
    """14 §3「货照发」：按缺口卡会让货凭空消失（第一版把 105 件吞了）。"""
    b = batch(100)
    res = allocate([b], [A, C], {A: {NOV: 10}, C: {NOV: 5}}, {A: {NOV: 2}, C: {NOV: 1}})
    assert given(res, b) == {A: 67, C: 33}      # 66/33 余 1 给权重大的


def test_short_more_than_the_batch_goes_by_gap_ratio_remainder_to_biggest_gap():
    b = batch(7)
    res = allocate([b], [A, C], {A: {NOV: -30}, C: {NOV: -10}}, {A: {NOV: 1}, C: {NOV: 1}})
    assert given(res, b) == {A: 6, C: 1}        # 5.25→5, 1.75→1, 余 1 给缺口大的 A


def test_cumulative_gap_is_not_filled_twice():
    """14 §3.1：G 是累计缺口，要减已累计分给 s 的量 —— 否则重复补同一个窟窿。"""
    b1, b2 = batch(50, D(2026, 11, 3)), batch(40, D(2026, 11, 20))
    res = allocate([b2, b1], [A, C], {A: {NOV: 44}, C: {NOV: -77}},
                   {A: {NOV: 120}, C: {NOV: 44}})
    assert given(res, b1) == {C: 50}
    assert given(res, b2) == {A: 10, C: 30}     # C 还缺 27，余 13 → 9/3 余 1 给 A


def test_every_batch_is_fully_given_out():
    """★ spec §4.4 断言：Σ分到的 ≡ 这批件数。"""
    for units in (1, 7, 99, 200, 1001):
        b = batch(units)
        res = allocate([b], [A, C], {A: {NOV: -3}, C: {NOV: 4}}, {A: {NOV: 5}, C: {NOV: 7}})
        assert sum(g.units for g in res[0][b]) == units


def test_batch_with_unknown_gap_is_not_given_to_anyone():
    """OP-12 · Review Focus 2：缺口未知 → 整批不分、点名。"""
    b = batch(200)
    res = allocate([b], [A, C], {A: {NOV: 44}, C: {NOV: None}}, {A: {NOV: 120}, C: {NOV: None}})
    assert res[0] == {}
    assert res[1] == [{"sku": "DCC1800264G1", "order_period": "2026-10", "arrive": "2026-11-14",
                       "units": 200, "reason": "gap_unknown", "unknown": [["MSKU-C", "11094"]]}]


def test_batch_with_no_fba_msku_is_listed():
    b = batch(200)
    res = allocate([b], [], {}, {})
    assert res[1][0]["reason"] == "no_fba_msku"


def test_zero_weights_and_no_gap_split_evenly():
    """OP-11：谁都不缺、期望全 0 → 按个数均分，余数按顺序给前面的。"""
    b = batch(5)
    res = allocate([b], [A, C], {A: {NOV: 1}, C: {NOV: 1}}, {A: {NOV: 0}, C: {NOV: 0}})
    assert given(res, b) == {A: 3, C: 2}


def test_spread_keeps_the_total():
    assert spread(123, {A: 120, C: 44}, [A, C]) == {A: 90, C: 33}
    with pytest.raises(ValueError):
        spread(-1, {A: 1}, [A])
```

- [ ] **Step 2: 跑，确认失败**

Run: `.venv/bin/python -m pytest -q tests/test_forecast_allocate.py`
Expected: FAIL —— `ModuleNotFoundError: No module named 'forecast.allocate'`

- [ ] **Step 3: 实现**

`forecast/allocate.py`：

```python
"""到货给哪个 msku —— 14 §3.1 两遍法（spec §4.4）。纯函数。

★ 这是**预测**不是排货：不写任何表、不产生动作，依据如实记录实际怎么分的（14 §3.2 · §3.3）。
★ 算法照原型 docs/prototype/forecast_demo.py::run_forecast 第二遍 + _spread。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from forecast.calendar import month_start

Key = tuple[str, str]


@dataclass(frozen=True)
class Batch:
    sku: str
    order_period: dt.date
    units: int
    placed: dt.date
    made: dt.date
    arrive: dt.date


@dataclass(frozen=True)
class Give:
    key: Key
    units: int
    gap_part: int
    weight_part: int
    rule: str
    pool: int


def spread(amount: int, weights: dict[Key, int], order: list[Key]) -> dict[Key, int]:
    """按权重摊整数，余数给权重最大的（并列按 order 先后）。★ 合计必须等于 amount。
    ★ 权重全 0 → 按个数均分（OP-11）。"""
    if amount < 0:
        raise ValueError(f"摊负数：{amount}")
    w = {k: weights[k] for k in order}
    if sum(w.values()) == 0:
        w = {k: 1 for k in order}
    tw = sum(w.values())
    out = {k: amount * w[k] // tw for k in order}
    rest = amount - sum(out.values())
    for k in sorted(order, key=lambda k: -w[k])[:rest]:   # sorted 稳定：并列按 order
        out[k] += 1
    assert sum(out.values()) == amount
    return out


def _unallocated(b: Batch, reason: str, unknown: list[Key]) -> dict:
    return {"sku": b.sku, "order_period": b.order_period.strftime("%Y-%m"),
            "arrive": b.arrive.isoformat(), "units": b.units, "reason": reason,
            "unknown": [list(k) for k in unknown]}


def allocate(batches: list[Batch], keys: list[Key],
             baseline: dict[Key, dict[dt.date, int | None]],
             weights: dict[Key, dict[dt.date, int | None]]
             ) -> tuple[dict[Batch, list[Give]], list[dict]]:
    gives: dict[Batch, list[Give]] = {}
    unallocated: list[dict] = []
    allocated = {k: 0 for k in keys}
    for b in sorted(batches, key=lambda x: (x.arrive, x.order_period)):
        if not keys:
            unallocated.append(_unallocated(b, "no_fba_msku", []))
            continue
        am = month_start(b.arrive)
        gap = {k: baseline[k].get(am) for k in keys}
        w = {k: weights[k].get(am) for k in keys}
        unknown = [k for k in keys if gap[k] is None or w[k] is None]
        if unknown:
            unallocated.append(_unallocated(b, "gap_unknown", unknown))
            continue
        need = {k: max(0, max(0, -gap[k]) - allocated[k]) for k in keys}
        total, pool = sum(need.values()), b.units
        tw = sum(w.values())
        if total == 0:
            give = spread(pool, w, keys)
            parts = {k: (0, give[k], f"无人缺货，按期望销量比例 {w[k]}/{tw}") for k in keys}
        elif total <= pool:
            extra = spread(pool - total, w, keys)
            give = {k: need[k] + extra[k] for k in keys}
            parts = {k: (need[k], extra[k], f"缺口 {need[k]} 补齐，余量按期望销量再摊 {extra[k]}")
                     for k in keys}
        else:
            give = {k: pool * need[k] // total for k in keys}
            for k in sorted(keys, key=lambda k: -need[k])[:pool - sum(give.values())]:
                give[k] += 1
            parts = {k: (give[k], 0, f"不够分，按缺口比例 {need[k]}/{total} × {pool}")
                     for k in keys}
        if sum(give.values()) != pool:   # ★ 14 §3.1：对不上就是管道漏货，宁可报错
            raise AssertionError(f"{b} 分配合计 {sum(give.values())} ≠ 这批 {pool}")
        for k in keys:
            allocated[k] += give[k]
        gives[b] = [Give(k, give[k], parts[k][0], parts[k][1], parts[k][2], pool)
                    for k in keys if give[k] > 0]
    return gives, unallocated
```

- [ ] **Step 4: 跑，确认通过 + 分层**

Run: `.venv/bin/python -m pytest -q tests/test_forecast_allocate.py tests/test_layering.py`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add forecast/allocate.py tests/test_forecast_allocate.py
git commit -m "feat(forecast): 两遍分配 —— 先补累计缺口、余量按期望销量、不够按缺口比例，Σ≡批件数"
```

---

### Task 6: `forecast/plan_forecast.py`（上）—— 单 msku 推演与按月读出

spec §4.2（期望销量逐日扣 · W-6 桥接 · 空 → 未知）· §4.5（期初/期末逐月相接、断货日）· §4.6（不适用）· OP-7 · OP-8 · OP-9 · OP-18

**Files:**
- Create: `forecast/plan_forecast.py`（本任务只写 `MskuInput` · `Arrival` · `MskuMonth` · `project_msku`）
- Test: `tests/test_forecast_project_msku.py`（新建）

**Interfaces:**
- Consumes: Task 2 的 `add_months, days_in_month, month_end, month_start, segment_daily, segment_units, span, units_for_month`；Task 3 的 `run_ledger`
- Produces:
  - `@dataclass(frozen=True) class MskuInput`: `seller_sku: str, sid: str, sku: str, applicable: bool, onhand: int | None, effective: dict[dt.date, int | None], system: dict[dt.date, int | None], base_rate: float | None`，属性 `key -> tuple[str, str]`
  - `@dataclass(frozen=True) class Arrival`: `date: dt.date, units: int | None, note: dict`
  - `@dataclass(frozen=True) class MskuMonth`: `period: dt.date, state: str, reason: str | None, unknown_since: dt.date | None, opening: int | None, demand: int | None, inbound: int | None, closing: int | None, stockout_date: dt.date | None, bridge: dict | None, arrivals: tuple[dict, ...]`
    - `state ∈ {"ok", "partial", "unknown", "not_applicable", "elapsed"}`；`reason ∈ {None, "as_of_inside_month", "unknown_onhand", "unknown_demand", "bridge_rate_unknown", "inbound_unknown", "not_applicable", "elapsed"}`
  - `project_msku(m: MskuInput, as_of: dt.date, periods: list[dt.date], arrivals: list[Arrival] = ()) -> list[MskuMonth]`

- [ ] **Step 1: 写失败的测试**

`tests/test_forecast_project_msku.py`：

```python
import datetime as dt

import pytest

from forecast.plan_forecast import Arrival, MskuInput, project_msku

D = dt.date
OCT, NOV, DEC = D(2026, 10, 1), D(2026, 11, 1), D(2026, 12, 1)


def msku(onhand=200, eff=None, rate=None, applicable=True):
    eff = eff or {OCT: 100, NOV: 50}
    return MskuInput("MSKU-A", "11072", "DCC1800264G1", applicable, onhand, eff,
                     {p: None for p in eff}, rate)


def chain(rows):
    return [(r.opening, r.demand, r.inbound, r.closing) for r in rows]


def test_owner_example_opening_200_expected_100():
    """spec §4.5 负责人 09-24：10 月期望 100，期初 200 → 期末 100 → 11 月期初 100。"""
    rows = project_msku(msku(), D(2026, 9, 30), [OCT, NOV])
    assert chain(rows) == [(200, 100, 0, 100), (100, 50, 0, 50)]
    assert [r.state for r in rows] == ["ok", "ok"]
    assert rows[1].opening == rows[0].closing          # ★ 恒等式④


def test_bridge_days_are_consumed_by_forecast():
    """W-6（OP-7）：as_of 9/21，9/22~9/30 按预测扣：9 月 = ceil(4×30) = 120，× 0.3 = 36。"""
    rows = project_msku(msku(onhand=300, rate=4.0), D(2026, 9, 21), [OCT, NOV])
    assert chain(rows) == [(264, 100, 0, 164), (164, 50, 0, 114)]
    assert rows[0].bridge == {"from": "2026-09-22", "to": "2026-09-30", "units": 36,
                              "by": "forecast", "rate": 4.0, "month_totals": {"2026-09": 120},
                              "onhand_as_of": 300}
    assert rows[1].bridge is None


def test_bridge_across_two_months():
    """as_of 8/20：8/21~8/31 = ceil(62 × 11/32) = 22；9 月整月 60 → 共 82。"""
    rows = project_msku(msku(onhand=100, eff={OCT: 10}, rate=2.0), D(2026, 8, 20), [OCT])
    assert rows[0].bridge["units"] == 82
    assert rows[0].opening == 18
    assert rows[0].bridge["month_totals"] == {"2026-08": 62, "2026-09": 60}


def test_no_rate_means_the_whole_msku_is_unknown_not_zero():
    """OP-8：扣不了桥接段 → 未知，不当 0。"""
    rows = project_msku(msku(onhand=300, rate=None), D(2026, 9, 21), [OCT, NOV])
    assert [(r.state, r.reason, r.closing) for r in rows] == [
        ("unknown", "bridge_rate_unknown", None), ("unknown", "bridge_rate_unknown", None)]
    assert rows[0].unknown_since == OCT


def test_unknown_demand_propagates_forward():
    """spec §4.2 · 14 B1/B2：11 月空 → 11 月起未知；10 月照常。"""
    rows = project_msku(msku(eff={OCT: 100, NOV: None, DEC: 10}), D(2026, 9, 30), [OCT, NOV, DEC])
    assert rows[0].closing == 100
    assert [(r.state, r.reason, r.unknown_since) for r in rows[1:]] == [
        ("unknown", "unknown_demand", NOV), ("unknown", "unknown_demand", NOV)]
    assert rows[1].opening == 100          # 期初是已知的上月期末


def test_not_applicable_and_unknown_onhand_are_different():
    """02 §3.1a · 14 B5：无 FBA = 不适用；有 FBA 没取到 = 未知。两者不许长得一样。"""
    na = project_msku(msku(onhand=None, applicable=False), D(2026, 9, 30), [OCT])
    un = project_msku(msku(onhand=None), D(2026, 9, 30), [OCT])
    assert (na[0].state, na[0].reason) == ("not_applicable", "not_applicable")
    assert (un[0].state, un[0].reason) == ("unknown", "unknown_onhand")


def test_arrivals_enter_on_their_day_and_the_month_identity_holds():
    """spec §4.5 ③：期末 = 期初 − 期望 + 入库；断货日来自截断台账。"""
    m = msku(onhand=7, eff={OCT: 40, NOV: 44, DEC: 44})
    arr = [Arrival(D(2026, 11, 14), 110, {"order_period": "2026-10"})]
    rows = project_msku(m, D(2026, 9, 30), [OCT, NOV, DEC], arr)
    assert chain(rows) == [(7, 40, 0, -33), (-33, 44, 110, 33), (33, 44, 0, -11)]
    assert [r.stockout_date for r in rows] == [D(2026, 10, 6), D(2026, 11, 1), None]
    assert rows[1].arrivals == ({"date": "2026-11-14", "units": 110, "order_period": "2026-10"},)


def test_unknown_arrival_makes_the_rest_unknown():
    m = msku(onhand=100, eff={OCT: 10, NOV: 10})
    rows = project_msku(m, D(2026, 9, 30), [OCT, NOV],
                        [Arrival(D(2026, 11, 14), None, {"reason": "gap_unknown"})])
    assert rows[0].closing == 90
    assert (rows[1].state, rows[1].reason, rows[1].inbound) == ("unknown", "inbound_unknown", None)


def test_as_of_inside_first_month_starts_the_day_after():
    """Review Focus 1 · OP-9：as_of 10/10 → 10 月从 10/11 起扣剩余份额 ceil(30×2/3)=20，不给期初。"""
    rows = project_msku(msku(onhand=100, eff={OCT: 30, NOV: 30}), D(2026, 10, 10), [OCT, NOV])
    assert (rows[0].state, rows[0].reason, rows[0].opening, rows[0].demand, rows[0].closing) == (
        "partial", "as_of_inside_month", None, 20, 80)
    assert (rows[1].opening, rows[1].closing) == (80, 50)


def test_elapsed_month_is_named_not_computed():
    """Review Focus 1：月末 ≤ as_of 的月 → 已过去。"""
    rows = project_msku(msku(onhand=100, eff={OCT: 30, NOV: 30}), D(2026, 10, 31), [OCT, NOV])
    assert (rows[0].state, rows[0].closing) == ("elapsed", None)
    assert (rows[1].opening, rows[1].closing) == (100, 70)


def test_arrival_outside_the_window_is_a_caller_bug():
    with pytest.raises(ValueError, match="beyond_horizon"):
        project_msku(msku(), D(2026, 9, 30), [OCT], [Arrival(D(2027, 1, 13), 5, {})])
```

- [ ] **Step 2: 跑，确认失败**

Run: `.venv/bin/python -m pytest -q tests/test_forecast_project_msku.py`
Expected: FAIL —— `ModuleNotFoundError: No module named 'forecast.plan_forecast'`

- [ ] **Step 3: 实现**

`forecast/plan_forecast.py`：

```python
"""计划整表的库存推演（spec §4）。纯函数：只吃值，不碰 PG / CH / 配置。

每个 msku 从 as_of 收盘起逐日记账（ledger.py），再按月读出期初 / 期末。
★ 月末头寸必为整数（OP-7：每一段都是 ceil 后的整件再摊回逐日），所以恒等式③④逐位成立。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from fractions import Fraction

from forecast.calendar import (add_months, days_in_month, month_end, month_start,
                               segment_daily, segment_units, span, units_for_month)
from forecast.ledger import run_ledger

ONE = dt.timedelta(days=1)


@dataclass(frozen=True)
class MskuInput:
    seller_sku: str
    sid: str
    sku: str
    applicable: bool                        # seller.has_fba（02 §3.1a）
    onhand: int | None                      # as_of 当天 FBA 可售；None + applicable → 未知在仓
    effective: dict[dt.date, int | None]    # 计划月（月初）→ 期望销量生效值
    system: dict[dt.date, int | None]       # 计划月 → 预测销量（只汇总，不进台账）
    base_rate: float | None                 # system_basis.base_rate；W-6 桥接段用

    @property
    def key(self) -> tuple[str, str]:
        return (self.seller_sku, self.sid)


@dataclass(frozen=True)
class Arrival:
    date: dt.date
    units: int | None                       # None = 这批分不出来（OP-12）
    note: dict


@dataclass(frozen=True)
class MskuMonth:
    period: dt.date
    state: str
    reason: str | None
    unknown_since: dt.date | None
    opening: int | None
    demand: int | None
    inbound: int | None
    closing: int | None
    stockout_date: dt.date | None
    bridge: dict | None
    arrivals: tuple[dict, ...]


def _int(x: Fraction) -> int:
    if x.denominator != 1:
        raise AssertionError(f"月末头寸必须是整数（OP-7 构造保证），得到 {x}")
    return int(x)


def _blank(p: dt.date, state: str, reason: str, since: dt.date | None = None) -> MskuMonth:
    return MskuMonth(p, state, reason, since, None, None, None, None, None, None, ())


def project_msku(m: MskuInput, as_of: dt.date, periods: list[dt.date],
                 arrivals: list[Arrival] = ()) -> list[MskuMonth]:
    if not m.applicable:
        return [_blank(p, "not_applicable", "not_applicable") for p in periods]
    if m.onhand is None:
        return [_blank(p, "unknown", "unknown_onhand", periods[0]) for p in periods]
    start, end = as_of + ONE, month_end(periods[-1])
    live = [p for p in periods if month_end(p) >= start]
    if not live:
        return [_blank(p, "elapsed", "elapsed") for p in periods]
    first_plan = periods[0]

    demand: dict[dt.date, Fraction | None] = {}
    bridge = None
    if start < first_plan:                                   # ★ W-6 桥接段：按预测扣
        if m.base_rate is None:
            demand.update({d: None for d in span(start, first_plan - ONE)})
        else:
            totals, units, mm = {}, 0, month_start(start)
            while mm < first_plan:
                a, b = max(start, mm), min(first_plan - ONE, month_end(mm))
                total = units_for_month(m.base_rate, days_in_month(mm))
                totals[mm.strftime("%Y-%m")] = total
                demand.update(segment_daily(total, a, b))
                units += segment_units(total, a, b)
                mm = add_months(mm, 1)
            bridge = {"from": start.isoformat(), "to": (first_plan - ONE).isoformat(),
                      "units": units, "by": "forecast", "rate": m.base_rate,
                      "month_totals": totals, "onhand_as_of": m.onhand}
    for p in live:                                           # ★ 计划月：扣期望销量
        a, eff = max(start, p), m.effective.get(p)
        if eff is None:
            demand.update({d: None for d in span(a, month_end(p))})
        else:
            demand.update(segment_daily(eff, a, month_end(p)))

    arr: dict[dt.date, Fraction | None] = {}
    notes: dict[dt.date, list[dict]] = {}
    for x in arrivals:
        if not start <= x.date <= end:
            raise ValueError(f"到货日 {x.date} 不在推演窗 [{start}, {end}] 内 —— "
                             "调用方应先把它列进 beyond_horizon")
        prev = arr.get(x.date, Fraction(0))
        arr[x.date] = None if (prev is None or x.units is None) else prev + x.units
        notes.setdefault(x.date, []).append({"date": x.date.isoformat(), "units": x.units,
                                             **x.note})

    days = span(start, end)
    L = run_ledger(m.onhand, start, [demand[d] for d in days],
                   [arr.get(d, Fraction(0)) for d in days])
    cause = since = None
    if L.unknown_from is not None:
        u = L.unknown_from
        cause = ("inbound_unknown" if demand[u] is not None
                 else "bridge_rate_unknown" if u < first_plan else "unknown_demand")
        since = max(month_start(u), live[0])

    rows: list[MskuMonth] = []
    for p in periods:
        if month_end(p) < start:
            rows.append(_blank(p, "elapsed", "elapsed"))
            continue
        a, b = max(start, p), month_end(p)
        partial = a != p
        eff = m.effective.get(p)
        dem = None if eff is None else segment_units(eff, a, b)
        opening, closing = L.signed_at(a - ONE), L.signed_at(b)
        inb = [arr.get(d, Fraction(0)) for d in span(a, b)]
        inbound = None if any(v is None for v in inb) else _int(sum(inb, Fraction(0)))
        month_notes = tuple(n for d in sorted(notes) if a <= d <= b for n in notes[d])
        br = bridge if (p == first_plan and not partial) else None
        shown_opening = None if (partial or opening is None) else _int(opening)
        if closing is None:
            rows.append(MskuMonth(p, "unknown", cause, since, shown_opening, dem, inbound, None,
                                  None, br, month_notes))
            continue
        if not partial and closing != opening - dem + inbound:   # ★ 恒等式③
            raise AssertionError(f"{m.key} {p}: {closing} ≠ {opening} − {dem} + {inbound}")
        rows.append(MskuMonth(p, "partial" if partial else "ok",
                              "as_of_inside_month" if partial else None, None, shown_opening,
                              dem, inbound, _int(closing), L.first_unmet(a, b), br, month_notes))
    return rows
```

- [ ] **Step 4: 跑，确认通过；再弄失败一次**

Run: `.venv/bin/python -m pytest -q tests/test_forecast_project_msku.py tests/test_layering.py`
Expected: PASS（12 + 分层）

临时把桥接段的 `segment_daily(total, a, b)` 换成 `{}`（不扣桥接）再跑：`test_bridge_days_are_consumed_by_forecast` 必须红（期初 300 而不是 264）。改回。

- [ ] **Step 5: 提交**

```bash
git add forecast/plan_forecast.py tests/test_forecast_project_msku.py
git commit -m "feat(forecast): 单 msku 推演 —— 期望销量逐日扣、W-6 桥接按预测、期初期末逐月相接、断货日"
```

---

### Task 7: `forecast/plan_forecast.py`（下）—— 整张计划：批次 · 分配 · 管道 · 三层汇总

spec §4.3（到货来源表 · 周期外逐笔列出）· §4.4 · §4.6（店铺 = Σ msku，货号 = Σ 店铺，在途）· OP-11~14

**Files:**
- Modify: `forecast/plan_forecast.py`（追加 `PlanForecastInput` · `StoreMonth` · `SkuMonth` · `PlanForecast` · `forecast_plan`）
- Test: `tests/test_forecast_plan.py`（新建）

**Interfaces:**
- Consumes: Task 5 `Batch, allocate`；Task 6 `MskuInput, Arrival, MskuMonth, project_msku`；Task 2 `order_arrival, month_end, month_start`
- Produces:
  - `@dataclass(frozen=True) class PlanForecastInput`: `as_of: dt.date, periods: list[dt.date], make_days: int, ship_days: int, mskus: list[MskuInput], purchase: dict[tuple[str, dt.date], int | None], ordered_open: dict[str, list[tuple[str, int, str]]]`（后者 = sku → [(期间 "YYYY-MM", 件数, 单号)]，来自 CH 已下单未到）
  - `@dataclass(frozen=True) class StoreMonth`: `sku, sid, period, state, reason, demand: int | None, system: int | None, opening, inbound, closing`
  - `@dataclass(frozen=True) class SkuMonth`: `sku, period, state, demand, system, opening, stock_demand, inbound, closing, excluded_not_applicable: int, making: int, shipping: int, ordered_open: int, ordered_sources: tuple[dict, ...], in_transit: int, unfilled_purchase: tuple[str, ...]`
  - `@dataclass(frozen=True) class PlanForecast`: `msku: dict[tuple[str, str], list[MskuMonth]], store: list[StoreMonth], sku: list[SkuMonth], beyond_horizon: list[dict], unallocated: list[dict]`
  - `forecast_plan(inp: PlanForecastInput) -> PlanForecast`
  - `beyond_horizon` 每项：`{"sku", "order_period": "YYYY-MM", "units", "placed", "made", "arrive", "reason": "arrives_after_horizon" | "arrives_before_as_of"}`

场景 S1（本任务与 Task 9 共用，数字手算过）：as_of 2026-09-21；计划 2026-10 起 3 个月；
货号 `DCC1800264G1` 下 MSKU-A@11072（在仓 300，rate 4，期望 100/120/90）与 MSKU-C@11094（在仓 25，rate 2，期望 40/44/44）；
货号 `A4P-TOY-002` 下 MSKU-D@11072（在仓 40，rate 1，期望 7/8/6）；计划采购 `DCC1800264G1` 10 月 200。

- 桥接：A 36（期初 264）· C 18（期初 7）· D 9（期初 31）
- 默认 30/60：到货 2027-01-13 → 周期外；A 期末 164/44/−46，C −33/−77/−121
- 改成 10/20：完工 10-25、到货 11-14；11 月基线 A 44、C −77 → C 补 77、余 123 按 120:44 → A 90、C 33 →
  A (264,100,0,164)(164,120,90,134)(134,90,0,44)；C (7,40,0,−33)(−33,44,110,33)(33,44,0,−11)，C 断货日 10-06 / 11-01 / 无；
  货号合计 期初 271/131/167、入库 0/200/0、期末 131/167/33；管道 月末 生产中 0/0/0、货运中 200/0/0

- [ ] **Step 1: 写失败的测试**

`tests/test_forecast_plan.py`：

```python
import datetime as dt
import time

from forecast.plan_forecast import MskuInput, PlanForecastInput, forecast_plan

D = dt.date
OCT, NOV, DEC = D(2026, 10, 1), D(2026, 11, 1), D(2026, 12, 1)
P = [OCT, NOV, DEC]
SKU_A, SKU_B = "DCC1800264G1", "A4P-TOY-002"
A, C, DD = ("MSKU-A", "11072"), ("MSKU-C", "11094"), ("MSKU-D", "11072")


def m(key, sku, onhand, eff, rate, applicable=True, system=None):
    return MskuInput(key[0], key[1], sku, applicable, onhand, dict(zip(P, eff)),
                     dict(zip(P, system or eff)), rate)


def s1(make=30, ship=60, c_eff=(40, 44, 44), extra=()):
    return PlanForecastInput(
        as_of=D(2026, 9, 21), periods=P, make_days=make, ship_days=ship,
        mskus=[m(A, SKU_A, 300, (100, 120, 90), 4.0), m(C, SKU_A, 25, c_eff, 2.0),
               m(DD, SKU_B, 40, (7, 8, 6), 1.0), *extra],
        purchase={(SKU_A, OCT): 200, (SKU_A, NOV): None, (SKU_A, DEC): 0,
                  (SKU_B, OCT): None, (SKU_B, NOV): None, (SKU_B, DEC): None},
        ordered_open={SKU_A: [("2026-10", 50, "PO-2026-0912"), ("2026-10", 30, "PO-2026-0915"),
                              ("2026-11", 120, "PO-2026-0920")]})


def chain(rows):
    return [(r.opening, r.demand, r.inbound, r.closing) for r in rows]


def sku_rows(f, sku):
    return [r for r in f.sku if r.sku == sku]


def test_default_lead_time_lands_the_october_order_outside_the_plan():
    """spec §7：10 月下单 + 30 + 60 → 2027-01，周期外 → 逐笔列出（14 B3）。"""
    f = forecast_plan(s1())
    assert [r.closing for r in f.msku[A]] == [164, 44, -46]
    assert [r.closing for r in f.msku[C]] == [-33, -77, -121]
    assert f.beyond_horizon == [{"sku": SKU_A, "order_period": "2026-10", "units": 200,
                                 "placed": "2026-10-15", "made": "2026-11-14",
                                 "arrive": "2027-01-13", "reason": "arrives_after_horizon"}]
    rows = sku_rows(f, SKU_A)
    assert [(r.making, r.shipping) for r in rows] == [(200, 0), (0, 200), (0, 200)]


def test_short_lead_time_allocates_into_november():
    f = forecast_plan(s1(make=10, ship=20))
    assert chain(f.msku[A]) == [(264, 100, 0, 164), (164, 120, 90, 134), (134, 90, 0, 44)]
    assert chain(f.msku[C]) == [(7, 40, 0, -33), (-33, 44, 110, 33), (33, 44, 0, -11)]
    assert [r.stockout_date for r in f.msku[C]] == [D(2026, 10, 6), D(2026, 11, 1), None]
    note = f.msku[C][1].arrivals[0]
    assert (note["units"], note["gap_part"], note["weight_part"], note["pool"]) == (110, 77, 33, 200)
    assert f.beyond_horizon == [] and f.unallocated == []


def test_store_is_sum_of_msku_and_sku_is_sum_of_store():
    """spec §4.6 · 14 §1.1 ①：合计行不许独立算一遍。"""
    f = forecast_plan(s1(make=10, ship=20))
    rows = sku_rows(f, SKU_A)
    assert [(r.opening, r.stock_demand, r.inbound, r.closing) for r in rows] == [
        (271, 140, 0, 131), (131, 164, 200, 167), (167, 134, 0, 33)]
    assert [r.demand for r in rows] == [140, 164, 134]
    store_a = [s for s in f.store if (s.sku, s.sid) == (SKU_A, "11072")]
    assert [s.closing for s in store_a] == [r.closing for r in f.msku[A]]


def test_pipeline_is_month_end_snapshot_and_ordered_open_never_arrives():
    """spec §4.3：已下单未到不定到货日，只进货号行在途（OQ-6）。"""
    rows = sku_rows(forecast_plan(s1(make=10, ship=20)), SKU_A)
    assert [(r.making, r.shipping, r.ordered_open, r.in_transit) for r in rows] == [
        (0, 200, 200, 400), (0, 0, 200, 200), (0, 0, 200, 200)]
    assert [x["ref"] for x in rows[0].ordered_sources] == ["PO-2026-0912", "PO-2026-0915",
                                                          "PO-2026-0920"]


def test_unfilled_purchase_is_named_not_silent():
    """OP-13：空采购不产生批次，但逐月列出。"""
    rows = sku_rows(forecast_plan(s1()), SKU_A)
    assert rows[0].unfilled_purchase == ("2026-11",)


def test_not_applicable_store_is_excluded_from_sku_stock_but_not_from_demand():
    """spec §4.6：无 FBA 店 → 不适用、不进库存求和；期望 Σ 仍含它（所以货号行分 demand 与 stock_demand 两个数）。"""
    w = m(("MSKU-W", "90001"), SKU_B, None, (60, 65, 70), None, applicable=False)
    f = forecast_plan(s1(extra=(w,)))
    rows = sku_rows(f, SKU_B)
    assert [r.excluded_not_applicable for r in rows] == [1, 1, 1]
    assert [r.closing for r in rows] == [24, 16, 10]            # 只有 D
    assert [r.demand for r in rows] == [67, 73, 76]             # 7+60 · 8+65 · 6+70
    assert [s.state for s in f.store if s.sid == "90001"] == ["not_applicable"] * 3


def test_batch_with_unknown_gap_makes_its_msku_unknown_from_arrival():
    """Review Focus 2 · OP-12。"""
    f = forecast_plan(s1(make=10, ship=20, c_eff=(40, None, 44)))
    assert [u["reason"] for u in f.unallocated] == ["gap_unknown"]
    assert (f.msku[A][1].state, f.msku[A][1].reason) == ("unknown", "inbound_unknown")
    assert f.msku[A][0].closing == 164


def test_batch_with_no_fba_msku_is_listed():
    """Review Focus 2：货号下只有无 FBA 的店。"""
    w = m(("MSKU-W", "90001"), "SKU-X", None, (1, 1, 1), None, applicable=False)
    inp = s1(make=10, ship=20, extra=(w,))
    inp.purchase[("SKU-X", OCT)] = 50
    f = forecast_plan(inp)
    assert [(u["sku"], u["reason"]) for u in f.unallocated] == [("SKU-X", "no_fba_msku")]


def test_zero_lead_time_before_as_of_is_listed():
    """Review Focus 3：0/0 且下单月在 as_of 之前 → 到货日早于 as_of，逐笔列出不丢。"""
    inp = s1(make=0, ship=0)
    inp = PlanForecastInput(D(2026, 9, 21), [D(2026, 9, 1), *P], 0, 0, inp.mskus,
                            {(SKU_A, D(2026, 9, 1)): 10}, {})
    f = forecast_plan(inp)
    assert [(b["arrive"], b["reason"]) for b in f.beyond_horizon] == [
        ("2026-09-15", "arrives_before_as_of")]


def test_hundred_mskus_twelve_months_is_fast_enough():
    """Review Focus 5：阈值 5 s 是 **设计取值 · 未实测**（只防数量级退化）。"""
    months = [D(2026 + (9 + i) // 12, (9 + i) % 12 + 1, 1) for i in range(12)]
    mskus = [MskuInput(f"M{i}", "11072", f"S{i % 30}", True, 500, {p: 30 for p in months},
                       {p: 30 for p in months}, 1.0) for i in range(100)]
    inp = PlanForecastInput(D(2026, 9, 21), months, 30, 60, mskus,
                            {(f"S{j}", months[0]): 100 for j in range(30)}, {})
    t0 = time.perf_counter()
    forecast_plan(inp)
    assert time.perf_counter() - t0 < 5.0
```

- [ ] **Step 2: 跑，确认失败**

Run: `.venv/bin/python -m pytest -q tests/test_forecast_plan.py`
Expected: FAIL —— `ImportError: cannot import name 'PlanForecastInput' from 'forecast.plan_forecast'`

- [ ] **Step 3: 实现（追加到 `forecast/plan_forecast.py`）**

在文件顶部 import 补上 `from forecast.allocate import Batch, allocate` 与 `order_arrival`（从 `forecast.calendar`），然后追加：

```python
@dataclass(frozen=True)
class PlanForecastInput:
    as_of: dt.date
    periods: list[dt.date]
    make_days: int
    ship_days: int
    mskus: list[MskuInput]
    purchase: dict[tuple[str, dt.date], int | None]
    ordered_open: dict[str, list[tuple[str, int, str]]]


@dataclass(frozen=True)
class StoreMonth:
    sku: str
    sid: str
    period: dt.date
    state: str
    reason: str | None
    demand: int | None
    system: int | None
    opening: int | None
    inbound: int | None
    closing: int | None


@dataclass(frozen=True)
class SkuMonth:
    sku: str
    period: dt.date
    state: str
    demand: int | None
    system: int | None
    opening: int | None
    stock_demand: int | None
    inbound: int | None
    closing: int | None
    excluded_not_applicable: int
    making: int
    shipping: int
    ordered_open: int
    ordered_sources: tuple[dict, ...]
    in_transit: int
    unfilled_purchase: tuple[str, ...]


@dataclass(frozen=True)
class PlanForecast:
    msku: dict[tuple[str, str], list[MskuMonth]]
    store: list[StoreMonth]
    sku: list[SkuMonth]
    beyond_horizon: list[dict]
    unallocated: list[dict]


def _sum(vals) -> int | None:
    vals = list(vals)
    return None if (not vals or any(v is None for v in vals)) else sum(vals)


def _beyond(b: Batch, reason: str) -> dict:
    return {"sku": b.sku, "order_period": b.order_period.strftime("%Y-%m"), "units": b.units,
            "placed": b.placed.isoformat(), "made": b.made.isoformat(),
            "arrive": b.arrive.isoformat(), "reason": reason}


def _combine_state(rows: list[MskuMonth]) -> tuple[str, str | None]:
    if all(r.state == "elapsed" for r in rows):
        return "elapsed", "elapsed"
    bad = next((r for r in rows if r.closing is None), None)
    if bad is not None:
        return "unknown", bad.reason
    if any(r.state == "partial" for r in rows):
        return "partial", "as_of_inside_month"
    return "ok", None


def forecast_plan(inp: PlanForecastInput) -> PlanForecast:
    start, end = inp.as_of + ONE, month_end(inp.periods[-1])
    by_sku: dict[str, list[MskuInput]] = {}
    for m in inp.mskus:
        by_sku.setdefault(m.sku, []).append(m)
    skus = sorted(set(by_sku) | {s for s, _ in inp.purchase})

    batches: dict[str, list[Batch]] = {s: [] for s in skus}
    unfilled: dict[str, list[str]] = {s: [] for s in skus}
    for (sku, p), units in sorted(inp.purchase.items()):
        if units is None:
            unfilled[sku].append(p.strftime("%Y-%m"))       # ★ OP-13：不静默
        elif units > 0:
            batches[sku].append(Batch(sku, p, units, *order_arrival(p, inp.make_days,
                                                                     inp.ship_days)))
    beyond: list[dict] = []
    placeable: dict[str, list[Batch]] = {}
    for sku, bs in batches.items():
        for b in bs:
            if b.arrive > end:
                beyond.append(_beyond(b, "arrives_after_horizon"))
            elif b.arrive < start:
                beyond.append(_beyond(b, "arrives_before_as_of"))
            else:
                placeable.setdefault(sku, []).append(b)

    # 第一遍 · 基线（不补货）
    base = {m.key: project_msku(m, inp.as_of, inp.periods) for m in inp.mskus}
    arrivals: dict[tuple[str, str], list[Arrival]] = {m.key: [] for m in inp.mskus}
    unallocated: list[dict] = []
    for sku in skus:
        keys = sorted((m.key for m in by_sku.get(sku, []) if m.applicable),
                      key=lambda k: (k[1], k[0]))
        ms = {m.key: m for m in by_sku.get(sku, [])}
        baseline = {k: {r.period: r.closing for r in base[k]} for k in keys}
        weights = {k: dict(ms[k].effective) for k in keys}
        gives, lost = allocate(placeable.get(sku, []), keys, baseline, weights)   # 第二遍
        unallocated += lost
        for b, gs in gives.items():
            for g in gs:
                arrivals[g.key].append(Arrival(b.arrive, g.units, {
                    "order_period": b.order_period.strftime("%Y-%m"), "rule": g.rule,
                    "gap_part": g.gap_part, "weight_part": g.weight_part, "pool": g.pool}))
        for u in lost:
            if u["reason"] == "gap_unknown":                 # ★ OP-12：整批分不出 → 从到货日起未知
                for k in keys:
                    arrivals[k].append(Arrival(dt.date.fromisoformat(u["arrive"]), None,
                                               {"order_period": u["order_period"],
                                                "reason": "gap_unknown", "pool": u["units"]}))

    final = {m.key: project_msku(m, inp.as_of, inp.periods, arrivals[m.key]) for m in inp.mskus}

    stores: list[StoreMonth] = []
    sku_rows: list[SkuMonth] = []
    for sku in skus:
        ms = by_sku.get(sku, [])
        sids = sorted({m.sid for m in ms})
        refs = tuple({"period": per, "units": u, "ref": ref}
                     for per, u, ref in inp.ordered_open.get(sku, []))
        ordered = sum(x["units"] for x in refs)
        for i, p in enumerate(inp.periods):
            month_stores: list[StoreMonth] = []
            for sid in sids:
                group = [m for m in ms if m.sid == sid]
                rows = [final[m.key][i] for m in group]
                dem = _sum(m.effective.get(p) for m in group)
                sysu = _sum(m.system.get(p) for m in group)
                if not group[0].applicable:
                    month_stores.append(StoreMonth(sku, sid, p, "not_applicable",
                                                   "not_applicable", dem, sysu, None, None, None))
                    continue
                state, reason = _combine_state(rows)
                month_stores.append(StoreMonth(
                    sku, sid, p, state, reason, dem, sysu,
                    _sum(r.opening for r in rows), _sum(r.inbound for r in rows),
                    _sum(r.closing for r in rows)))
            stores += month_stores
            live = [s for s in month_stores if s.state != "not_applicable"]
            me = month_end(p)
            making = sum(b.units for b in batches[sku] if b.placed <= me < b.made)
            shipping = sum(b.units for b in batches[sku] if b.made <= me < b.arrive)
            if not month_stores:
                state = "no_msku"
            elif not live:
                state = "not_applicable"
            else:
                state = _combine_state([r for m in ms if m.applicable
                                        for r in [final[m.key][i]]])[0]
            def stock(f, live=live):
                return _sum(f(s) for s in live) if live else None
            row = SkuMonth(
                sku, p, state, _sum(s.demand for s in month_stores) if month_stores else None,
                _sum(s.system for s in month_stores) if month_stores else None,
                stock(lambda s: s.opening),
                _sum(final[m.key][i].demand for m in ms if m.applicable) if live else None,
                stock(lambda s: s.inbound), stock(lambda s: s.closing),
                len(month_stores) - len(live), making, shipping, ordered, refs,
                making + shipping + ordered, tuple(unfilled[sku]))
            if (row.state == "ok" and row.closing
                    != row.opening - row.stock_demand + row.inbound):   # ★ 恒等式③ 货号层
                raise AssertionError(f"{sku} {p} 货号层恒等式不成立：{row}")
            sku_rows.append(row)
    return PlanForecast(final, stores, sku_rows, beyond, unallocated)
```

- [ ] **Step 4: 跑，确认通过；再弄失败一次**

Run: `.venv/bin/python -m pytest -q tests/test_forecast_plan.py tests/test_forecast_project_msku.py tests/test_layering.py`
Expected: PASS

临时把 `forecast_plan` 里第二遍的 `allocate(...)` 结果丢掉（`gives = {}`）再跑：`test_short_lead_time_allocates_into_november` 必须红。改回。

- [ ] **Step 5: 提交**

```bash
git add forecast/plan_forecast.py tests/test_forecast_plan.py
git commit -m "feat(forecast): 整张计划推演 —— 批次过管道、两遍分配、周期外逐笔列出、店铺/货号逐层求和"
```

---

### Task 8: `GET/PUT /v1/lead-time` + 启动检查

spec §4.4（用户变量、改完立即重算）· §6（改了写日志旧值 → 新值）· `14` B12 · CLAUDE.md「配置类错误往启动钩子放」· OP-4 · OP-5

**Files:**
- Create: `api/ui/lead_time.py`
- Modify: `api/__init__.py`（`include_router`；`_startup_check` 里加一段提前期检查）
- Modify: `tests/conftest.py`（加 `lead_time_reset` 夹具）
- Test: `tests/test_api_lead_time.py`（新建）

**Interfaces:**
- Consumes: `api.ui.deps.actor`, `actor_optional`, `declared`；`api.ui.errors.ApiError`；`shared.pg_client.pg_conn, timed`；`forecast.calendar.ANCHOR_DAY`
- Produces:
  - `ROUTE = "oversea_sea"`
  - `@dataclass(frozen=True) class LeadTime`: `route: str, make_days: int, ship_days: int, anchor_day: int, valid_from: dict[str, str]`
  - `read_lead_time(cur) -> LeadTime` —— 当前生效行不是恰好 making/shipping 各一行 → `raise ApiError(500, "lead_time_misconfigured", ...)`
  - `lead_time_json(lt: LeadTime) -> dict` —— `{"route", "make_days", "ship_days", "anchor_day", "valid_from": {"making", "shipping"}}`
  - `GET /v1/lead-time` → `lead_time_json`；`PUT /v1/lead-time` body `{"make_days"?: int, "ship_days"?: int}` → `{**lead_time_json, "changed": [{"segment", "from", "to"}]}`
  - 夹具 `lead_time_reset`（测试后把 `lead_time` 恢复成 008 的种子）

- [ ] **Step 1: 写失败的测试**

`tests/conftest.py` 末尾追加：

```python
@pytest.fixture
def lead_time_reset(business_db):
    """lead_time 是配置不是数据（不在 DATA_TABLES 里被清）—— 改了就必须还原成 008 的种子，
    否则后面的测试会读到上一个测试留下的提前期。★ 整表重灌（有的测试会删行）。"""
    yield
    from shared.pg_client import pg_conn
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("DELETE FROM lead_time")
        cur.execute("INSERT INTO lead_time (route, segment, days, valid_from) VALUES"
                    " ('oversea_sea', 'making', 30, DATE '2026-01-01'),"
                    " ('oversea_sea', 'shipping', 60, DATE '2026-01-01'),"
                    " ('air', 'shipping', 10, DATE '2026-01-01'),"
                    " ('domestic', 'shipping', 3, DATE '2026-01-01')")
```

`tests/test_api_lead_time.py`：

```python
import logging

import pytest

from shared.pg_client import pg_conn


def H(a):
    return {"x-actor": a}


def test_get_returns_the_seeded_default(client, seed, lead_time_reset):
    r = client.get("/v1/lead-time")
    assert r.status_code == 200
    assert r.json() == {"route": "oversea_sea", "make_days": 30, "ship_days": 60,
                        "anchor_day": 15,
                        "valid_from": {"making": "2026-01-01", "shipping": "2026-01-01"}}


def test_put_changes_only_what_is_given_and_logs_old_to_new(client, seed, lead_time_reset,
                                                            caplog):
    """spec §6：改了写日志（旧值 → 新值）。"""
    caplog.set_level(logging.INFO)
    r = client.put("/v1/lead-time", json={"make_days": 10}, headers=H(seed.actor))
    assert r.status_code == 200
    body = r.json()
    assert (body["make_days"], body["ship_days"]) == (10, 60)
    assert body["changed"] == [{"segment": "making", "from": 30, "to": 10}]
    assert "op=lead_time_update" in caplog.text and "segment=making from=30 to=10" in caplog.text
    assert client.get("/v1/lead-time").json()["make_days"] == 10


def test_put_keeps_history_and_same_day_edits_in_place(client, seed, lead_time_reset):
    """OP-4：旧行关掉留痕；同一天再改只改今天那行。"""
    client.put("/v1/lead-time", json={"make_days": 10}, headers=H(seed.actor))
    client.put("/v1/lead-time", json={"make_days": 12}, headers=H(seed.actor))
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT days, valid_to IS NULL FROM lead_time"
                    " WHERE route = 'oversea_sea' AND segment = 'making' ORDER BY valid_from")
        assert cur.fetchall() == [(30, False), (12, True)]


def test_put_same_value_changes_nothing(client, seed, lead_time_reset):
    r = client.put("/v1/lead-time", json={"ship_days": 60}, headers=H(seed.actor))
    assert r.json()["changed"] == []


@pytest.mark.parametrize("body", [{"make_days": -1}, {"ship_days": 721}, {"make_days": "30"},
                                  {"make_days": 3.5}, {"make_days": True}, {}])
def test_put_rejects_out_of_range(client, seed, lead_time_reset, body):
    """Review Focus 3：范围 0~720 天（03 §41 CHECK），类型在这里挡。"""
    r = client.put("/v1/lead-time", json=body, headers=H(seed.actor))
    assert r.status_code == 400
    assert r.json()["error"] == "validation_error"


def test_put_needs_an_actor(client, seed, lead_time_reset):
    assert client.put("/v1/lead-time", json={"make_days": 1}).status_code == 400


def test_misconfigured_table_is_a_named_500(client, seed, lead_time_reset):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE lead_time SET valid_to = DATE '2026-02-01'"
                    " WHERE route = 'oversea_sea' AND segment = 'making'")
    r = client.get("/v1/lead-time")
    assert r.status_code == 500
    assert r.json()["error"] == "lead_time_misconfigured"


def test_startup_names_a_misconfigured_lead_time(business_db, lead_time_reset, caplog):
    """CLAUDE.md：配置类错误在启动日志第一屏可见，不等第一个请求才炸。"""
    from api import _startup_check
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("DELETE FROM lead_time WHERE route = 'oversea_sea' AND segment = 'shipping'")
    caplog.set_level(logging.INFO)
    _startup_check()
    assert "stage=lead_time" in caplog.text and "outcome=fail" in caplog.text
```

- [ ] **Step 2: 跑，确认失败**

Run: `.venv/bin/python -m pytest -q tests/test_api_lead_time.py`
Expected: FAIL —— GET 返回 `404`（路由不存在）；startup 那条 `ImportError` 或断言失败

- [ ] **Step 3: 实现**

`api/ui/lead_time.py`：

```python
"""提前期（spec §4.4 · 03 §41）：用户自己改的全局配置。

★ 当前生效行 = valid_to IS NULL。改动 = 关旧行 + 插今天的新行（同一天再改就原地改今天那行）——
  「现在的规则」在表里，「当时用的规则」随 grid 的 params 一起出去（03 §7 那句话）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import APIRouter, Depends, Request

from api.ui.deps import actor, actor_optional, declared
from api.ui.errors import ApiError
from forecast.calendar import ANCHOR_DAY
from shared.pg_client import pg_conn, timed

log = logging.getLogger("scm.api")
router = APIRouter()

#: spec §4.4：货运 60 = 国际海运那一档。★ 预测只读这一条线路（OP-4）。
ROUTE = "oversea_sea"
_FIELDS = (("make_days", "making"), ("ship_days", "shipping"))
_MAX_DAYS = 720          # 03 §41 CHECK


@dataclass(frozen=True)
class LeadTime:
    route: str
    make_days: int
    ship_days: int
    anchor_day: int
    valid_from: dict[str, str]


def _current(cur, lock: bool = False) -> dict[str, tuple[int, object, bool]]:
    """当前生效行：{segment: (days, valid_from, valid_from 是不是今天)}。"""
    cur.execute("SELECT segment, days, valid_from, valid_from = CURRENT_DATE FROM lead_time"
                " WHERE route = %s AND valid_to IS NULL" + (" FOR UPDATE" if lock else ""),
                (ROUTE,))
    rows = cur.fetchall()
    got = {seg: (days, vf, today) for seg, days, vf, today in rows}
    # ★ 恰好 making / shipping 各一行；少一行或同一段两行都是配置坏了，不猜用哪行
    if sorted(seg for seg, *_ in rows) != ["making", "shipping"]:
        raise ApiError(500, "lead_time_misconfigured",
                       "提前期配置不是「生产 / 货运各一条当前行」—— 库存推演算不了",
                       {"route": ROUTE, "current_rows": [[s, d] for s, d, _, _ in rows]})
    return got


def read_lead_time(cur) -> LeadTime:
    got = _current(cur)
    return LeadTime(ROUTE, got["making"][0], got["shipping"][0], ANCHOR_DAY,
                    {s: got[s][1].isoformat() for s in ("making", "shipping")})


def lead_time_json(lt: LeadTime) -> dict:
    return {"route": lt.route, "make_days": lt.make_days, "ship_days": lt.ship_days,
            "anchor_day": lt.anchor_day, "valid_from": lt.valid_from}


@router.get("/lead-time")
def get_lead_time(request: Request, who: str | None = Depends(actor_optional)):
    declared(request)
    with timed("get_lead_time", actor=who), pg_conn() as c, c.cursor() as cur:
        return lead_time_json(read_lead_time(cur))


@router.put("/lead-time")
def put_lead_time(body: dict, request: Request, who: str = Depends(actor)):
    declared(request)
    wanted: dict[str, int] = {}
    for field, seg in _FIELDS:
        if field in body:
            v = body[field]
            if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= _MAX_DAYS:
                raise ApiError(400, "validation_error", f"{field} 必须是 0~{_MAX_DAYS} 的整数天",
                               {"fields": [field], "got": v})
            wanted[seg] = v
    if not wanted:
        raise ApiError(400, "validation_error", "至少给 make_days / ship_days 之一",
                       {"fields": [f for f, _ in _FIELDS]})
    changed: list[dict] = []
    with timed("put_lead_time", actor=who), pg_conn() as c, c.cursor() as cur:
        got = _current(cur, lock=True)
        for seg, days in wanted.items():
            old, vf, same_day = got[seg]
            if old == days:
                continue
            if same_day:
                cur.execute("UPDATE lead_time SET days = %s"
                            " WHERE route = %s AND segment = %s AND valid_from = %s",
                            (days, ROUTE, seg, vf))
            else:
                cur.execute("UPDATE lead_time SET valid_to = CURRENT_DATE"
                            " WHERE route = %s AND segment = %s AND valid_to IS NULL", (ROUTE, seg))
                cur.execute("INSERT INTO lead_time (route, segment, days, valid_from)"
                            " VALUES (%s, %s, %s, CURRENT_DATE)", (ROUTE, seg, days))
            changed.append({"segment": seg, "from": old, "to": days})
        lt = read_lead_time(cur)
    for ch in changed:
        log.info("op=lead_time_update actor=%s route=%s segment=%s from=%d to=%d",
                 who, ROUTE, ch["segment"], ch["from"], ch["to"])
    return {**lead_time_json(lt), "changed": changed}
```

`api/__init__.py`：在其它 `include_router` 旁加 `app.include_router(lead_time.router, prefix="/v1")`（import `from api.ui import lead_time`）。在 `_startup_check()` 的新鲜度检查**之前**加：

```python
    # ★ CLAUDE.md「配置类错误往启动钩子放」：提前期配错时第一屏就要看见，而不是等第一次 grid 500。
    #   同本函数其它段：只记日志，不许中断 lifespan。
    try:
        from api.ui.lead_time import read_lead_time
        with pg_conn() as c, c.cursor() as cur:
            lt = read_lead_time(cur)
        log.info("startup lead_time route=%s make_days=%d ship_days=%d",
                 lt.route, lt.make_days, lt.ship_days)
    except Exception as e:  # noqa: BLE001 - 见上
        log.warning("startup outcome=fail stage=lead_time err_type=%s err=%s",
                    type(e).__name__, getattr(e, "detail", e))
```

（`_startup_check` 已 import `pg_conn`；若没有则按文件现有 import 风格补上。）

- [ ] **Step 4: 跑，确认通过 + 相关回归**

Run: `.venv/bin/python -m pytest -q tests/test_api_lead_time.py tests/test_api_startup_check.py tests/test_ddl_008_plan_forecast.py`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add api/ui/lead_time.py api/__init__.py tests/conftest.py tests/test_api_lead_time.py
git commit -m "feat(api): GET/PUT /v1/lead-time —— 提前期用户可改、版本留痕、旧→新写日志、启动即查配置"
```

---

### Task 9: grid 返回 `params` · `forecast` · `forecast_store` · `forecast_sku` · `beyond_horizon` · `unallocated`

spec §6（grid 响应）· §4.3 · §4.6 · OP-6 · OP-16

**Files:**
- Create: `api/ui/grid_forecast.py`
- Modify: `api/ui/plans.py`（`_load_grid` 多取 `d.system_basis`；`grid()` 收集每个 msku 的在仓 / has_fba / base_rate，读提前期，合并预测块；★ 旧 `inventory[]` / `sku_pipeline[]` 原样保留，Task 21 再删）
- Test: `tests/test_api_grid_forecast.py`（新建）

**Interfaces:**
- Consumes: Task 7 `MskuInput, PlanForecastInput, forecast_plan`；Task 8 `read_lead_time, LeadTime`
- Produces（JSON，前端 Task 17 照这个写类型）：
  - `params`: `{"as_of": "YYYY-MM-DD", "route": "oversea_sea", "make_days", "ship_days", "anchor_day": 15, "embargo_days": null}`（Phase 2 Task 15 填 7）
  - `forecast[]`（msku × 月）: `{"seller_sku", "sid", "sku", "period": "YYYY-MM", "state", "reason", "unknown_since": "YYYY-MM"|null, "opening", "demand", "inbound_planned", "closing", "stockout_date": "YYYY-MM-DD"|null, "basis": {"bridge": {...}|null, "arrivals": [...]}}`
  - `forecast_store[]`: `{"sku", "sid", "period", "state", "reason", "demand", "system", "opening", "inbound_planned", "closing"}`
  - `forecast_sku[]`: `{"sku", "period", "state", "demand", "system", "opening", "stock_demand", "inbound_planned", "closing", "excluded_not_applicable", "pipeline": {"making", "shipping", "ordered_open", "ordered_open_sources": [{"period", "units", "ref"}], "fba_inbound": null, "fba_inbound_reason": "not_fetched"}, "in_transit", "in_transit_excludes": ["fba_inbound"], "unfilled_purchase": ["YYYY-MM"]}`
  - `beyond_horizon[]` · `unallocated[]`：同 Task 5/7
  - `forecast_payload(*, as_of, periods, demand_rows, purchase_rows, fba, onhand, base_rate, in_transit, lead, embargo_days) -> dict`（在 `api/ui/grid_forecast.py`）

- [ ] **Step 1: 写失败的测试**

`tests/test_api_grid_forecast.py`：

```python
"""spec §6：grid 的预测块。场景 S1 的数字见 Task 7 的推导。"""
import json

from shared.pg_client import pg_conn

P = ["2026-10", "2026-11", "2026-12"]


def H(a):
    return {"x-actor": a}


def s1(client, seed, extra=()):
    pid = client.post("/v1/plans", json={"title": "S1", "period_start": "2026-10-01", "months": 3},
                      headers=H(seed.actor)).json()["plan_id"]
    for ms in (seed.msku_a, seed.msku_c, seed.msku_d, *extra):
        client.post(f"/v1/plans/{pid}/claims", json={"seller_sku": ms[0], "sid": ms[1]},
                    headers=H(seed.actor))
    # ★ 人填期望销量：库存只扣生效值，与认领时种的预测销量解耦（Phase 2 会换掉预测算法）
    for ms, units in ((seed.msku_a, (100, 120, 90)), (seed.msku_c, (40, 44, 44)),
                      (seed.msku_d, (7, 8, 6))):
        for p, u in zip(P, units):
            client.put(f"/v1/plans/{pid}/demand/{ms[0]}/{ms[1]}/{p}",
                       json={"expected_units": u}, headers=H(seed.actor))
    with pg_conn() as c, c.cursor() as cur:
        for ms, rate in ((seed.msku_a, 4), (seed.msku_c, 2), (seed.msku_d, 1)):
            cur.execute("UPDATE plan_demand_cell SET system_basis = %s"
                        " WHERE plan_id = %s AND seller_sku = %s AND sid = %s",
                        (json.dumps({"base_rate": rate}), pid, *ms))
    client.put(f"/v1/plans/{pid}/purchase/{seed.sku_a}/2026-10", json={"planned_units": 200},
               headers=H(seed.actor))
    return pid


def grid(client, seed, pid):
    r = client.get(f"/v1/plans/{pid}/grid", headers=H(seed.actor))
    assert r.status_code == 200, r.text
    return r.json()


def rows(g, key, **match):
    return [r for r in g[key] if all(r[k] == v for k, v in match.items())]


def chain(rs):
    return [(r["opening"], r["demand"], r["inbound_planned"], r["closing"]) for r in rs]


def test_params_say_which_rules_were_used(client, seed, lead_time_reset):
    g = grid(client, seed, s1(client, seed))
    assert g["params"] == {"as_of": "2026-09-21", "route": "oversea_sea", "make_days": 30,
                           "ship_days": 60, "anchor_day": 15, "embargo_days": None}


def test_default_lead_time_msku_chain_and_beyond_horizon(client, seed, lead_time_reset):
    g = grid(client, seed, s1(client, seed))
    a = rows(g, "forecast", seller_sku="MSKU-A")
    assert chain(a) == [(264, 100, 0, 164), (164, 120, 0, 44), (44, 90, 0, -46)]
    assert a[0]["basis"]["bridge"]["units"] == 36
    assert [r["closing"] for r in rows(g, "forecast", seller_sku="MSKU-D")] == [24, 16, 10]
    assert g["beyond_horizon"] == [{"sku": seed.sku_a, "order_period": "2026-10", "units": 200,
                                    "placed": "2026-10-15", "made": "2026-11-14",
                                    "arrive": "2027-01-13", "reason": "arrives_after_horizon"}]


def test_changed_lead_time_recomputes_immediately(client, seed, lead_time_reset):
    """14 B12：改提前期立即重算 —— 下一次 grid 就是新结果。"""
    pid = s1(client, seed)
    client.put("/v1/lead-time", json={"make_days": 10, "ship_days": 20}, headers=H(seed.actor))
    g = grid(client, seed, pid)
    assert chain(rows(g, "forecast", seller_sku="MSKU-C")) == [
        (7, 40, 0, -33), (-33, 44, 110, 33), (33, 44, 0, -11)]
    assert [r["stockout_date"] for r in rows(g, "forecast", seller_sku="MSKU-C")] == [
        "2026-10-06", "2026-11-01", None]
    sku = rows(g, "forecast_sku", sku=seed.sku_a)
    assert [(r["opening"], r["inbound_planned"], r["closing"]) for r in sku] == [
        (271, 0, 131), (131, 200, 167), (167, 0, 33)]
    assert [(r["pipeline"]["making"], r["pipeline"]["shipping"], r["pipeline"]["ordered_open"],
             r["in_transit"]) for r in sku] == [(0, 200, 200, 400), (0, 0, 200, 200),
                                                (0, 0, 200, 200)]
    assert sku[0]["pipeline"]["fba_inbound"] is None
    assert sku[0]["pipeline"]["fba_inbound_reason"] == "not_fetched"
    assert g["params"]["make_days"] == 10


def test_store_rows_sum_mskus_and_no_fba_store_is_not_applicable(client, seed, lead_time_reset):
    g = grid(client, seed, s1(client, seed, extra=(seed.msku_nofba,)))
    w = rows(g, "forecast_store", sid=seed.seller_nofba)
    assert [r["state"] for r in w] == ["not_applicable"] * 3
    assert all(r["closing"] is None for r in w)
    b = rows(g, "forecast_sku", sku=seed.sku_b)
    assert [r["excluded_not_applicable"] for r in b] == [1, 1, 1]
    assert [r["closing"] for r in b] == [24, 16, 10]


def test_grid_unknown_demand_makes_the_batch_unallocated(client, seed, lead_time_reset):
    """Review Focus 2 · OP-12：C 的 11 月期望与预测都空 → 11 月起未知；这批货不分给任何人。"""
    pid = s1(client, seed)
    client.put("/v1/lead-time", json={"make_days": 10, "ship_days": 20}, headers=H(seed.actor))
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE plan_demand_cell SET expected_units = NULL, system_units = NULL,"
                    " system_extrapolated = FALSE WHERE plan_id = %s AND seller_sku = %s"
                    " AND period_start = DATE '2026-11-01'", (pid, seed.msku_c[0]))
    g = grid(client, seed, pid)
    assert [u["reason"] for u in g["unallocated"]] == ["gap_unknown"]
    c_rows = rows(g, "forecast", seller_sku="MSKU-C")
    assert [(r["state"], r["reason"], r["unknown_since"]) for r in c_rows[1:]] == [
        ("unknown", "unknown_demand", "2026-11")] * 2
    a_rows = rows(g, "forecast", seller_sku="MSKU-A")
    assert (a_rows[1]["state"], a_rows[1]["reason"]) == ("unknown", "inbound_unknown")
    assert rows(g, "forecast_sku", sku=seed.sku_a)[1]["closing"] is None


def test_missing_rate_and_missing_onhand_are_named(client, seed, lead_time_reset):
    pid = s1(client, seed, extra=(seed.msku_e,))
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE plan_demand_cell SET system_basis = NULL"
                    " WHERE plan_id = %s AND seller_sku = %s", (pid, seed.msku_d[0]))
    g = grid(client, seed, pid)
    assert {r["reason"] for r in rows(g, "forecast", seller_sku="MSKU-D")} == {"bridge_rate_unknown"}
    assert {r["reason"] for r in rows(g, "forecast", seller_sku="MSKU-E")} == {"unknown_onhand"}
```

- [ ] **Step 2: 跑，确认失败**

Run: `.venv/bin/python -m pytest -q tests/test_api_grid_forecast.py`
Expected: FAIL —— `KeyError: 'params'`

- [ ] **Step 3: 实现**

`api/ui/grid_forecast.py`：

```python
"""grid 的预测块（spec §6）：把 PG / 取数源的值装成 forecast_plan() 的入参，再序列化。
★ 这里不算任何一个数 —— 算都在 forecast/（纯函数）；这里只搬运与改名。"""
from __future__ import annotations

import datetime as dt
import logging
import time

from api.ui.lead_time import LeadTime
from forecast.plan_forecast import MskuInput, PlanForecastInput, forecast_plan

log = logging.getLogger("scm.api")


def _ym(d: dt.date | None) -> str | None:
    return d.strftime("%Y-%m") if d else None


def _p(s: str) -> dt.date:
    return dt.date.fromisoformat(s + "-01")


def forecast_payload(*, as_of: dt.date, periods: list[dt.date], demand_rows: list[dict],
                     purchase_rows: list[tuple], fba: dict, onhand: dict, base_rate: dict,
                     in_transit: dict, lead: LeadTime, embargo_days: int | None,
                     plan_id: int) -> dict:
    t0 = time.perf_counter()
    by_key: dict[tuple[str, str], dict] = {}
    for r in demand_rows:
        k = (r["seller_sku"], r["sid"])
        slot = by_key.setdefault(k, {"sku": r["sku"], "eff": {}, "sys": {}})
        slot["eff"][_p(r["period"])] = r["effective_units"]
        slot["sys"][_p(r["period"])] = r["system_units"]
    mskus = [MskuInput(k[0], k[1], v["sku"], bool(fba[k]), onhand.get(k), v["eff"], v["sys"],
                       base_rate.get(k)) for k, v in sorted(by_key.items())]
    inp = PlanForecastInput(
        as_of=as_of, periods=periods, make_days=lead.make_days, ship_days=lead.ship_days,
        mskus=mskus, purchase={(s, p): u for s, p, u in purchase_rows},
        ordered_open={sku: [(t.period, t.units, t.ref) for t in ts]
                      for sku, ts in in_transit.items()})
    f = forecast_plan(inp)
    out = {
        "params": {"as_of": as_of.isoformat(), "route": lead.route, "make_days": lead.make_days,
                   "ship_days": lead.ship_days, "anchor_day": lead.anchor_day,
                   "embargo_days": embargo_days},
        "forecast": [{"seller_sku": k[0], "sid": k[1], "sku": by_key[k]["sku"],
                      "period": _ym(r.period), "state": r.state, "reason": r.reason,
                      "unknown_since": _ym(r.unknown_since), "opening": r.opening,
                      "demand": r.demand, "inbound_planned": r.inbound, "closing": r.closing,
                      "stockout_date": r.stockout_date.isoformat() if r.stockout_date else None,
                      "basis": {"bridge": r.bridge, "arrivals": list(r.arrivals)}}
                     for k, rs in sorted(f.msku.items()) for r in rs],
        "forecast_store": [{"sku": s.sku, "sid": s.sid, "period": _ym(s.period),
                            "state": s.state, "reason": s.reason, "demand": s.demand,
                            "system": s.system, "opening": s.opening,
                            "inbound_planned": s.inbound, "closing": s.closing}
                           for s in f.store],
        "forecast_sku": [{"sku": s.sku, "period": _ym(s.period), "state": s.state,
                          "demand": s.demand, "system": s.system, "opening": s.opening,
                          "stock_demand": s.stock_demand, "inbound_planned": s.inbound,
                          "closing": s.closing,
                          "excluded_not_applicable": s.excluded_not_applicable,
                          "pipeline": {"making": s.making, "shipping": s.shipping,
                                       "ordered_open": s.ordered_open,
                                       "ordered_open_sources": list(s.ordered_sources),
                                       "fba_inbound": None, "fba_inbound_reason": "not_fetched"},
                          "in_transit": s.in_transit, "in_transit_excludes": ["fba_inbound"],
                          "unfilled_purchase": list(s.unfilled_purchase)} for s in f.sku],
        "beyond_horizon": f.beyond_horizon,
        "unallocated": f.unallocated,
    }
    log.info("op=grid_forecast plan_id=%s mskus=%d beyond=%d unallocated=%d make_days=%d"
             " ship_days=%d elapsed_ms=%d", plan_id, len(mskus), len(f.beyond_horizon),
             len(f.unallocated), lead.make_days, lead.ship_days,
             (time.perf_counter() - t0) * 1000)
    return out
```

`api/ui/plans.py` 改动（逐处）：

1. `_load_grid` 的 SELECT 在 `s.has_fba` 后加 `, d.system_basis`；`grid()` 的解包改成 `for seller_sku, sid, sku, period, sysu, extrap, expu, fba, sbasis in demand:`。
2. `grid()` 在循环前建三个 dict：`fba_by_msku: dict = {}`、`onhand_by_msku: dict = {}`、`rate_by_msku: dict = {}`；循环里：
   ```python
            k = (seller_sku, sid)
            fba_by_msku[k] = fba
            if sbasis and sbasis.get("base_rate") is not None:
                rate_by_msku[k] = float(sbasis["base_rate"])
   ```
   把原来 `got = source.onhand_available(seller_sku, sid)` 那一句后面补 `onhand_by_msku[k] = got`（★ 只在 `fba` 时取，与原逻辑同一个闸）。
3. `with timed("grid", ...)` 块里 `_load_grid` 之后加 `lead = read_lead_time(cur)`（import `from api.ui.lead_time import read_lead_time`）。
4. `return` 前：
   ```python
    payload = forecast_payload(
        as_of=as_of_date, periods=periods, demand_rows=out_demand, purchase_rows=purchase,
        fba=fba_by_msku, onhand=onhand_by_msku, base_rate=rate_by_msku, in_transit=in_transit,
        lead=lead, embargo_days=None, plan_id=plan_id)
   ```
   并把 `**payload` 合进返回的 dict（旧键一个不动）。

- [ ] **Step 4: 跑，确认通过 + grid 回归**

Run: `.venv/bin/python -m pytest -q tests/test_api_grid_forecast.py tests/test_api_grid.py tests/test_api_forecast_source.py`
Expected: `test_api_grid_forecast.py` PASS；`tests/test_api_grid.py` / `tests/test_grid_fixture.py` 里若有「键集合恰好等于」的断言（如 `test_grid_blocks_carry_exactly_the_ruled_keys` 只断言子块，不断言顶层键）保持绿；`tests/test_grid_fixture.py::test_grid_fixture_matches_the_live_api` 此时**会红**（顶层多了新键）—— 这是预期的，Task 10 重新固化。

- [ ] **Step 5: 提交**

```bash
git add api/ui/grid_forecast.py api/ui/plans.py tests/test_api_grid_forecast.py
git commit -m "feat(api): grid 返回预测块 —— params / msku 期初期末链 / 店铺·货号合计 / 管道在途 / 周期外与未分配逐笔"
```

---

### Task 10: 重新固化 grid fixture（后端 + 前端同源）

spec §6 · `00e` 阶段 A 判据⑥（mock 与真 API 同一份数据）

**Files:**
- Modify: `tests/fixtures/grid_response.json`（重新生成）
- Modify: `web/src/api/fixtures/grid-1.json`（逐字节拷贝）
- Modify: `tests/test_grid_fixture.py`（shape 覆盖断言补上新键）

**Interfaces:**
- Consumes: Task 9 的 grid 响应
- Produces: 前端 mock 从此带着 `params / forecast / forecast_store / forecast_sku / beyond_horizon / unallocated`

- [ ] **Step 1: 写失败的测试**

`tests/test_grid_fixture.py::test_the_fixture_covers_the_shapes_that_look_alike` 末尾追加：

```python
    # ★ spec §6 的预测块必须随 fixture 出去，否则前端永远在画一份没有它们的 mock
    for key in ("params", "forecast", "forecast_store", "forecast_sku", "beyond_horizon",
                "unallocated"):
        assert key in saved, f"fixture 缺 {key}"
    assert any(r["state"] == "not_applicable" for r in saved["forecast_store"]), "缺「不适用」"
    assert any(r["reason"] == "unknown_onhand" for r in saved["forecast"]), "缺「未知在仓」"
    assert saved["beyond_horizon"], "缺「周期外到货」—— 默认 30/60 下 10 月的 500 件必然在周期外"
```

- [ ] **Step 2: 跑，确认失败**

Run: `.venv/bin/python -m pytest -q tests/test_grid_fixture.py`
Expected: FAIL —— `fixture 缺 params` 与 `grid 的形状变了`

- [ ] **Step 3: 重新固化并拷到前端**

```bash
UPDATE_GRID_FIXTURE=1 .venv/bin/python -m pytest -q tests/test_grid_fixture.py
cp tests/fixtures/grid_response.json web/src/api/fixtures/grid-1.json
git diff --stat tests/fixtures/grid_response.json web/src/api/fixtures/grid-1.json
```

逐项读 diff：旧键（`demand` / `purchase` / `inventory` / `sku_pipeline` / `source_notes`）**一个字节不变**；只多出新键。此时 fixture 里 msku 的 `forecast` 多为 `bridge_rate_unknown`（Phase 1 认领不种 `base_rate`，OP-8）—— 这是如实的，Task 16 会重固化出真数。

- [ ] **Step 4: 前后端一起跑**

Run: `.venv/bin/python -m pytest -q tests/test_grid_fixture.py tests/test_layering_web.py && cd web && npx vitest run && npx tsc -b`
Expected: 全绿（前端还没读新键，JSON 多键不影响类型）

- [ ] **Step 5: 提交**

```bash
git add tests/fixtures/grid_response.json web/src/api/fixtures/grid-1.json tests/test_grid_fixture.py
git commit -m "test(fixture): grid fixture 带上预测块（前后端同源重固化）"
```

**Phase 1 收口：** `.venv/bin/python -m pytest -q && .venv/bin/ruff check . && (cd web && npx vitest run && npx tsc -b)` 全绿再进 Phase 2。


---

# Phase 2 · 预测销量（spec §3 · §9 第 2 步）

### Task 11: 移植 `baseline_rate`（D-3 ① 搬，金标一次绿）

spec §3.1（四步流水线、常数不另调）· `00` D-3 ① · OP-1 · OP-2 · OP-3

**Files:**
- Create: `tests/fixtures/baseline_golden/generate.py`（一次性脚本，用**兄弟仓的 venv** 跑）
- Create: `tests/fixtures/baseline_golden/{history.csv, censor.csv, expected.csv, README.md}`（脚本产物，提交）
- Create: `forecast/baseline.py`
- Test: `tests/test_forecast_baseline.py`（行为，从兄弟仓 `tests/test_baseline.py` 逐条搬）· `tests/test_forecast_baseline_golden.py`（金标门禁）

**Interfaces:**
- Consumes: 无
- Produces:
  - 常数 `HALFLIFE_DAYS = 14.0` · `WINDOW_DAYS = 56` · `SHRINK_PRIOR = 3.0` · `TARGET_WINDOW_DAYS = 90` · `WINSOR_Q = 0.95` · `WINSOR_MIN_NONZERO = 5`
  - `@dataclass(frozen=True) class BaselineResult`: `anchor: dt.date, rate: float, raw_rate: float, n_eff: float, target_rate: float, window_from: dt.date, target_from: dt.date, first_seen: dt.date | None, kept_days: int, censored_days: int, winsor_cap: float | None`
  - `baseline_rate(daily: Mapping[dt.date, int], censor: Collection[dt.date], anchor: dt.date, *, halflife=..., window=..., prior=..., target_window=..., winsor_q=...) -> BaselineResult | None`（窗口内一行都没有 → `None`，= 兄弟仓结果里没有这个键）
  - `@dataclass(frozen=True) class DataWindows`: `estimation_anchor, sales_from, censor_from, until`（均 `dt.date`）
  - `data_windows(as_of: dt.date) -> DataWindows`
  - `@dataclass(frozen=True) class Estimate`: `as_of: dt.date, embargo_days: int, estimation_anchor: dt.date, result: BaselineResult | None`
  - `estimate_rate(daily, censor, as_of: dt.date) -> Estimate` —— **生产入口**（claim 只调它）

- [ ] **Step 1: 生成金标（用兄弟仓 v1，只读兄弟仓）**

`tests/fixtures/baseline_golden/generate.py`：

```python
"""一次性：用兄弟仓 v1 `baseline_rate` 在**固定输入**上算期望值（00 D-3 ①「任意一组固定输入」）。

运行（worktree 根下）：
  /home/fido/work/2026/jxd_model_group/model_inventory_forecast/.venv/bin/python \
      tests/fixtures/baseline_golden/generate.py
★ 生成后不再改。要换基准就新建目录并写明理由（同兄弟仓 fixtures/README 的规矩）。
★ 每个 case 在两个锚点上各算一次：as_of 与 as_of − 7 —— 后者是 Task 12 embargo 之后的新基准。
"""
import csv
import datetime as dt
import random
import subprocess
import sys
from pathlib import Path

SIB = Path("/home/fido/work/2026/jxd_model_group/model_inventory_forecast")
sys.path.insert(0, str(SIB))
import pandas as pd  # noqa: E402
from models.inventory_forecast.demand import baseline_rate  # noqa: E402

OUT = Path(__file__).resolve().parent
AS_OF = dt.date(2026, 9, 21)
ANCHORS = (AS_OF, AS_OF - dt.timedelta(days=7))


def back(i):
    return AS_OF - dt.timedelta(days=i)


def named_cases():
    yield "dense_const_4_120d", {back(i): 4 for i in range(120)}, set()
    yield "const_4_56d", {back(i): 4 for i in range(56)}, set()
    yield "censor_last_14", {back(i): (0 if i < 14 else 5) for i in range(28)}, {back(i) for i in range(14)}
    yield "sparse_two_days", {back(0): 10, back(1): 10}, set()
    yield "spike", {**{back(i): 5 for i in range(28)}, back(3): 100}, set()
    yield "new_20d", {back(i): 6 for i in range(20)}, set()
    yield "zeros_only_in_window", {back(i): 0 for i in range(10)}, set()
    yield "only_older_than_window", {back(i): 3 for i in range(60, 89)}, set()
    yield "all_censored", {back(i): 2 for i in range(30)}, {back(i) for i in range(56)}
    yield "old_high_recent_low", {**{back(i): 10 for i in range(56, 90)}, **{back(i): 1 for i in range(56)}}, set()
    yield "empty", {}, set()


def random_cases(n=120, seed=20260924):
    rng = random.Random(seed)
    for k in range(n):
        days = rng.choice([10, 30, 56, 90, 150])
        lam = rng.choice([0.05, 0.3, 1, 4, 20])
        trials = int(lam * 3) + 1
        daily = {}
        for i in range(days):
            d = back(i + rng.choice([0, 0, 0, 40]))
            u = sum(1 for _ in range(trials) if rng.random() < lam / trials)
            if rng.random() < 0.05:
                u *= 10
            if u > 0 or rng.random() < 0.1:
                daily[d] = daily.get(d, 0) + u
        cen = {back(i) for i in range(60) if rng.random() < 0.1}
        yield f"rand_{k:03d}", daily, cen


def main():
    cases = list(named_cases()) + list(random_cases())
    hist, cens, exp = [], [], []
    for name, daily, cen in cases:
        hist += [(name, d.isoformat(), u) for d, u in sorted(daily.items())]
        cens += [(name, d.isoformat()) for d in sorted(cen)]
        h = pd.DataFrame([("S", "N", d, u) for d, u in daily.items()],
                         columns=["sku", "node", "date", "units"])
        c = pd.DataFrame([("S", "N", d) for d in cen], columns=["sku", "node", "date"])
        for a in ANCHORS:
            r = baseline_rate(h, a, censor=c).get(("S", "N"))
            exp.append((name, a.isoformat(), "" if r is None else repr(float(r))))
    for fname, header, rows in (("history.csv", ["case", "date", "units"], hist),
                                ("censor.csv", ["case", "date"], cens),
                                ("expected.csv", ["case", "anchor", "base_rate"], exp)):
        with open(OUT / fname, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(header)
            w.writerows(rows)
    rev = subprocess.run(["git", "-C", str(SIB), "rev-parse", "HEAD"], capture_output=True,
                         text=True, check=True).stdout.strip()
    (OUT / "README.md").write_text(
        "# baseline_rate 金标（冻结）\n\n"
        f"兄弟仓 `{SIB.name}` commit `{rev}` 的 v1 `baseline_rate` 在固定输入上的输出。\n"
        f"{len(cases)} 个 case × 锚点 {', '.join(a.isoformat() for a in ANCHORS)}。\n"
        "生成：见 generate.py 顶部。★ 不许手工编辑；要换基准就新建目录。\n", encoding="utf-8")


if __name__ == "__main__":
    main()
```

Run:
```bash
/home/fido/work/2026/jxd_model_group/model_inventory_forecast/.venv/bin/python tests/fixtures/baseline_golden/generate.py
wc -l tests/fixtures/baseline_golden/expected.csv    # 263（表头 + 131 × 2）
```

- [ ] **Step 2: 写失败的测试**

`tests/test_forecast_baseline_golden.py`：

```python
"""★ 00 D-3 ①：base_rate 移植的「一次绿」门禁 —— 兄弟仓 v1 在固定输入上的输出（OP-1）。
红了就是搬错了，且能定位到 case。"""
import csv
import datetime as dt
import math
from pathlib import Path

from forecast.baseline import baseline_rate, estimate_rate

GOLD = Path(__file__).parent / "fixtures" / "baseline_golden"
AS_OF = dt.date(2026, 9, 21)
EARLIER = AS_OF - dt.timedelta(days=7)


def _rows(name):
    with open(GOLD / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load():
    expected = {(r["case"], dt.date.fromisoformat(r["anchor"])):
                (None if r["base_rate"] == "" else float(r["base_rate"]))
                for r in _rows("expected.csv")}
    cases = {c: ({}, set()) for c, _ in expected}
    for r in _rows("history.csv"):
        cases[r["case"]][0][dt.date.fromisoformat(r["date"])] = int(r["units"])
    for r in _rows("censor.csv"):
        cases[r["case"]][1].add(dt.date.fromisoformat(r["date"]))
    return cases, expected


def close(a, b):
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12)


def test_golden_is_complete():
    cases, expected = load()
    assert len(cases) == 131 and len(expected) == 262
    assert {a for _, a in expected} == {AS_OF, EARLIER}
    assert any(not close(expected[(c, AS_OF)], expected[(c, EARLIER)]) for c in cases), \
        "两个锚点的期望值全一样 —— Task 12 的「变红」就不可能发生，这份金标测不到 embargo"


def test_port_matches_sibling_v1_at_every_anchor():
    cases, expected = load()
    bad = []
    for (case, anchor), want in sorted(expected.items()):
        r = baseline_rate(*cases[case], anchor)
        got = None if r is None else r.rate
        if not close(got, want):
            bad.append((case, anchor.isoformat(), got, want))
    assert bad == [], f"{len(bad)} 个 case 与兄弟仓不符：{bad[:10]}"


def test_production_entry_matches_sibling_v1_at_as_of():
    """生产入口（claim 调的就是它）在 v1 口径下 = 兄弟仓在 as_of 上的输出。"""
    cases, expected = load()
    bad = [c for c in cases
           if not close((lambda e: None if e.result is None else e.result.rate)(
               estimate_rate(*cases[c], AS_OF)), expected[(c, AS_OF)])]
    assert bad == []
```

`tests/test_forecast_baseline.py`（兄弟仓 `test_baseline.py` 的行为逐条搬，改成单 msku 签名）：

```python
"""base_rate 四步流水线各自被处理了（兄弟仓 tests/test_baseline.py 逐条搬）。"""
import datetime as dt

import pytest

from forecast.baseline import SHRINK_PRIOR, baseline_rate
from forecast.calendar import units_for_month

A = dt.date(2026, 8, 19)


def back(i):
    return A - dt.timedelta(days=i)


def test_absent_days_count_as_zero_not_missing():
    """densify：没出现的日子 = 卖了 0。"""
    days = [back(i) for i in range(56)]
    a = baseline_rate({days[0]: 10, days[1]: 10}, set(), A, prior=0.0)
    b = baseline_rate({d: (10 if d in days[:2] else 0) for d in days}, set(), A, prior=0.0)
    assert a.rate == pytest.approx(b.rate)


def test_stockout_days_are_censored_not_treated_as_zero_demand():
    """censor：断货日的 0 是供给为 0，不是需求为 0。"""
    sales = {back(i): (0 if i < 14 else 5) for i in range(28)}
    cen = {back(i) for i in range(14)}
    naive = baseline_rate(sales, set(), A, prior=0.0)
    fixed = baseline_rate(sales, cen, A, prior=0.0)
    assert fixed.rate > naive.rate * 2
    assert fixed.rate == pytest.approx(5.0, abs=0.01)
    assert fixed.censored_days == 14


def test_promo_spike_does_not_hijack_the_baseline():
    flat = {back(i): 5 for i in range(28)}
    spiked = {**flat, back(3): 100}
    lift = baseline_rate(spiked, set(), A, prior=0.0).rate / baseline_rate(flat, set(), A, prior=0.0).rate
    assert lift < 1.5
    assert baseline_rate(spiked, set(), A, prior=0.0).winsor_cap is not None


def test_shrinks_toward_the_mskus_own_long_window():
    h = {**{back(i): 10 for i in range(56, 90)}, **{back(i): 1 for i in range(56)}}
    raw = baseline_rate(h, set(), A, prior=0.0).rate
    shrunk = baseline_rate(h, set(), A, prior=SHRINK_PRIOR).rate
    assert raw < shrunk < 10


def test_new_msku_is_not_diluted_by_pre_launch_days():
    new = baseline_rate({back(i): 6 for i in range(20)}, set(), A, prior=0.0)
    old = baseline_rate({back(i): 6 for i in range(56)}, set(), A, prior=0.0)
    assert new.rate == pytest.approx(old.rate, rel=0.01)
    assert new.first_seen == back(19)


def test_monthly_forecast_scales_by_days():
    """spec §3.1：预测销量 = base_rate × 该月天数（季节指数停用）。"""
    r = baseline_rate({back(i): 10 for i in range(28)}, set(), A).rate
    assert units_for_month(r, 31) / units_for_month(r, 30) == pytest.approx(31 / 30, rel=0.02)


def test_no_rows_in_window_is_none_not_zero():
    """spec §3.3：没有历史 ≠ 0。"""
    assert baseline_rate({}, set(), A) is None
    assert baseline_rate({back(80): 3}, set(), A) is None


def test_known_sibling_defect_is_ported_verbatim():
    """★ OP-3：兄弟仓 first_seen 只在 56 天窗里找 → 90 天目标被除以 56。
    恒卖 4 件/天 120 天 → 4.325（高估 8.1%）。先搬不修：修的那一次这条**应当**变红。"""
    r = baseline_rate({back(i): 4 for i in range(120)}, set(), A)
    assert r.rate == pytest.approx(4.325139122267326, rel=1e-12)
```

- [ ] **Step 3: 跑，确认失败**

Run: `.venv/bin/python -m pytest -q tests/test_forecast_baseline.py tests/test_forecast_baseline_golden.py`
Expected: FAIL —— `ModuleNotFoundError: No module named 'forecast.baseline'`

- [ ] **Step 4: 实现（逐行对照兄弟仓 `demand.py::baseline_rate`）**

`forecast/baseline.py`：

```python
"""预测销量：兄弟仓 v1 `baseline_rate` 的移植（spec §3.1 · 00 D-3 ①）。纯函数，标准库（OP-2）。

    base_rate = 近 56 天日销 → densify（从首次有销量起，没出现 = 0）→ censor（剔 FBA 断货日）
                → 截到非零日 p95 → 指数加权算术均值（半衰期 14 天）
                → 向自身近 90 天算术日均收缩，权重 n/(n+3)

★ 先搬不修：兄弟仓已知缺陷原样保留（OP-3），由金标钉住。
★ 单 msku 签名：兄弟仓按 (sku, node) 各算各的，一次只算一个 msku 与它等价 ——
  而且从构造上就不可能被邻居污染（兄弟仓 2026-08-21 修掉的那个 bug）。
"""
from __future__ import annotations

import datetime as dt
import math
from collections.abc import Collection, Mapping
from dataclasses import dataclass

# ★ 全部照搬兄弟仓 models/inventory_forecast/demand.py:67-77，不另调（spec §3.1）
HALFLIFE_DAYS = 14.0
WINDOW_DAYS = 56
SHRINK_PRIOR = 3.0
TARGET_WINDOW_DAYS = 90
WINSOR_Q = 0.95
#: demand.py:266 `if len(nz) >= 5` —— 非零日少于 5 天不削峰
WINSOR_MIN_NONZERO = 5


@dataclass(frozen=True)
class BaselineResult:
    anchor: dt.date
    rate: float
    raw_rate: float
    n_eff: float
    target_rate: float
    window_from: dt.date
    target_from: dt.date
    first_seen: dt.date | None
    kept_days: int
    censored_days: int
    winsor_cap: float | None


@dataclass(frozen=True)
class DataWindows:
    estimation_anchor: dt.date
    sales_from: dt.date       # 90 天收缩目标窗的起点（日销要取到这里）
    censor_from: dt.date      # 56 天估计窗的起点（断货日要取到这里）
    until: dt.date


@dataclass(frozen=True)
class Estimate:
    as_of: dt.date
    embargo_days: int
    estimation_anchor: dt.date
    result: BaselineResult | None


def _quantile_linear(xs: list[float], q: float) -> float:
    """= numpy.quantile 默认的 'linear'。"""
    s = sorted(xs)
    pos = (len(s) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def baseline_rate(daily: Mapping[dt.date, int], censor: Collection[dt.date], anchor: dt.date, *,
                  halflife: float = HALFLIFE_DAYS, window: int = WINDOW_DAYS,
                  prior: float = SHRINK_PRIOR, target_window: int = TARGET_WINDOW_DAYS,
                  winsor_q: float | None = WINSOR_Q) -> BaselineResult | None:
    start = anchor - dt.timedelta(days=window - 1)
    in_win = {d: u for d, u in daily.items() if start <= d <= anchor}
    if not in_win:
        return None           # 兄弟仓：窗口里一行都没有 → 结果里没有这个键
    grid = [start + dt.timedelta(days=i) for i in range(window)]
    w_all = [math.exp(-math.log(2.0) * (window - 1 - i) / halflife) for i in range(window)]
    positive = [d for d, u in in_win.items() if u > 0]
    born = min(positive) if positive else None      # ★ densify 从首次有销量起（上市前不是「卖了 0」）
    keep = [(d not in censor) and (born is None or d >= born) for d in grid]
    cap = None
    if any(keep):
        v = [float(in_win.get(d, 0)) for d, k in zip(grid, keep) if k]
        w = [x for x, k in zip(w_all, keep) if k]
        if winsor_q is not None:
            nz = [x for x in v if x > 0]            # ★ 分位只在非零日上算
            if len(nz) >= WINSOR_MIN_NONZERO:
                cap = _quantile_linear(nz, winsor_q)
                v = [min(x, cap) for x in v]
        raw = sum(a * b for a, b in zip(w, v)) / sum(w)
        n = sum(w)
    else:
        raw, n = 0.0, 0.0
    t_start = anchor - dt.timedelta(days=target_window - 1)
    tot = float(sum(u for d, u in daily.items() if t_start <= d <= anchor))
    span_days = target_window if born is None else min(target_window, (anchor - born).days + 1)
    target = tot / span_days
    rate = (n * raw + prior * target) / (n + prior) if (n + prior) else 0.0
    censored = sum(1 for d in grid if d in censor and (born is None or d >= born))
    return BaselineResult(anchor, rate, raw, n, target, start, t_start, born, sum(keep),
                          censored, cap)


def data_windows(as_of: dt.date) -> DataWindows:
    a = as_of
    return DataWindows(a, a - dt.timedelta(days=TARGET_WINDOW_DAYS - 1),
                       a - dt.timedelta(days=WINDOW_DAYS - 1), a)


def estimate_rate(daily: Mapping[dt.date, int], censor: Collection[dt.date],
                  as_of: dt.date) -> Estimate:
    """生产入口。★ v1 口径：估计锚点 = as_of（没有滞后修正，embargo 0 天）。Task 12 改。"""
    return Estimate(as_of, 0, as_of, baseline_rate(daily, censor, as_of))
```

- [ ] **Step 5: 跑 —— 金标必须一次绿；再弄失败一次**

Run: `.venv/bin/python -m pytest -q tests/test_forecast_baseline.py tests/test_forecast_baseline_golden.py tests/test_layering.py`
Expected: PASS。★ 金标红了**不许改金标**，逐 case 对照兄弟仓代码找搬错的那一步。

临时把 `WINSOR_MIN_NONZERO` 改成 `1` 再跑：`test_port_matches_sibling_v1_at_every_anchor` 必须红（点名若干 case，含 `sparse_two_days`）。改回。

- [ ] **Step 6: 提交**

```bash
git add forecast/baseline.py tests/fixtures/baseline_golden tests/test_forecast_baseline.py tests/test_forecast_baseline_golden.py
git commit -m "feat(forecast): 移植兄弟仓 v1 baseline_rate（标准库）—— 金标 131 case × 2 锚点一次绿（D-3 ①）"
```

---

### Task 12: 滞后修正（D-3 ② 修 —— 门禁必然变红，再改指新基准）

spec §3.1 第 ② 步（估计锚点从 as_of 前挪 `embargo_days`，整窗后移不丢样本）· `00` W-5 · OP-19

**Files:**
- Modify: `forecast/baseline.py`（`EMBARGO_DAYS` · `estimation_anchor` · `data_windows` / `estimate_rate` 走估计锚点）
- Modify: `tests/test_forecast_baseline_golden.py`（生产入口改指 `as_of − 7` 的金标）
- Test: `tests/test_forecast_baseline.py`（追加 embargo 用例）
- Modify: `docs/00-待裁定清单.md`（W-5 行尾补一句）

**Interfaces:**
- Produces:
  - `EMBARGO_DAYS: int = 7`
  - `estimation_anchor(as_of: dt.date, embargo_days: int = EMBARGO_DAYS) -> dt.date`
  - `data_windows(as_of: dt.date, embargo_days: int = EMBARGO_DAYS) -> DataWindows`
  - `estimate_rate(daily, censor, as_of, embargo_days: int = EMBARGO_DAYS) -> Estimate`

- [ ] **Step 1: 写 embargo 的测试**

`tests/test_forecast_baseline.py` 追加：

```python
def test_estimation_anchor_moves_back_embargo_days():
    """spec §3.1 ②：估计锚点从 as_of 前挪 7 天（设计取值 · 未实测，W-5）。"""
    from forecast.baseline import EMBARGO_DAYS, data_windows, estimation_anchor
    assert EMBARGO_DAYS == 7
    assert estimation_anchor(dt.date(2026, 9, 21)) == dt.date(2026, 9, 14)
    w = data_windows(dt.date(2026, 9, 21))
    assert (w.estimation_anchor, w.censor_from, w.sales_from, w.until) == (
        dt.date(2026, 9, 14), dt.date(2026, 7, 21), dt.date(2026, 6, 17), dt.date(2026, 9, 14))


@pytest.mark.parametrize("bad", [-1, 1.5, True, None])
def test_embargo_must_be_a_non_negative_int(bad):
    from forecast.baseline import estimation_anchor
    with pytest.raises(ValueError):
        estimation_anchor(dt.date(2026, 9, 21), bad)


def test_the_incomplete_recent_days_no_longer_drag_the_rate_down():
    """06 §4：近几天订单没采全 → v1 系统性低估。挪开锚点后，最后 7 天的缺口不再进估计。"""
    from forecast.baseline import estimate_rate
    as_of = dt.date(2026, 9, 21)
    full = {as_of - dt.timedelta(days=i): 10 for i in range(120)}
    lagged = {**full, **{as_of - dt.timedelta(days=i): 3 for i in range(7)}}   # 近 7 天只采到 3 成
    e = estimate_rate(lagged, set(), as_of)
    assert (e.embargo_days, e.estimation_anchor) == (7, dt.date(2026, 9, 14))
    assert e.result.rate == pytest.approx(estimate_rate(full, set(), as_of).result.rate)
```

- [ ] **Step 2: 跑，确认失败**

Run: `.venv/bin/python -m pytest -q tests/test_forecast_baseline.py`
Expected: FAIL —— `ImportError: cannot import name 'EMBARGO_DAYS'`

- [ ] **Step 3: 实现**

`forecast/baseline.py`：在常数区追加

```python
#: spec §3.1 ② · 00 W-5：估计锚点从 as_of 前挪 7 天（整窗后移，不丢样本）。
#: ★ 设计取值 · 未实测 —— 兄弟仓 maturity.py:179 自己标的占位（成熟度曲线受 08-20 回填污染）。
EMBARGO_DAYS = 7
```

新增

```python
def estimation_anchor(as_of: dt.date, embargo_days: int = EMBARGO_DAYS) -> dt.date:
    """★ 两个锚点两个名字（兄弟仓 maturity.py:215）：推演从 as_of 的库存出发，
    速率用 as_of − embargo_days 那批成熟数据算。"""
    if not isinstance(embargo_days, int) or isinstance(embargo_days, bool) or embargo_days < 0:
        raise ValueError(f"embargo_days 必须是非负整数，收到 {embargo_days!r}")
    return as_of - dt.timedelta(days=embargo_days)
```

并把 `data_windows` / `estimate_rate` 改成：

```python
def data_windows(as_of: dt.date, embargo_days: int = EMBARGO_DAYS) -> DataWindows:
    a = estimation_anchor(as_of, embargo_days)
    return DataWindows(a, a - dt.timedelta(days=TARGET_WINDOW_DAYS - 1),
                       a - dt.timedelta(days=WINDOW_DAYS - 1), a)


def estimate_rate(daily: Mapping[dt.date, int], censor: Collection[dt.date], as_of: dt.date,
                  embargo_days: int = EMBARGO_DAYS) -> Estimate:
    """生产入口：速率在估计锚点上算（spec §3.1 ②）。"""
    a = estimation_anchor(as_of, embargo_days)
    return Estimate(as_of, embargo_days, a, baseline_rate(daily, censor, a))
```

- [ ] **Step 4: 跑金标 —— ★ 必然变红（D-3 ②），记下红了几个 case**

Run: `.venv/bin/python -m pytest -q tests/test_forecast_baseline_golden.py`
Expected: `test_production_entry_matches_sibling_v1_at_as_of` **FAIL**（列出若干 case —— 生产入口不再等于 v1@as_of）；`test_port_matches_sibling_v1_at_every_anchor` 仍 PASS（移植本身没变）。把失败的 case 数写进提交信息。

- [ ] **Step 5: 改指新基准**

`tests/test_forecast_baseline_golden.py`：删掉 `test_production_entry_matches_sibling_v1_at_as_of`，换成

```python
def test_production_entry_is_sibling_v1_at_the_estimation_anchor():
    """★ D-3 ② 之后的新基准：生产入口 = 兄弟仓 v1 在 as_of − 7 上的输出（W-5）。
    与运营对「当前数据下的新基准」是人的动作，不在这里（OP-19）。"""
    cases, expected = load()
    bad = [c for c in cases
           if not close((lambda e: None if e.result is None else e.result.rate)(
               estimate_rate(*cases[c], AS_OF)), expected[(c, EARLIER)])]
    assert bad == []
```

`docs/00-待裁定清单.md` 的 W-5 行（`| **W-5** | 预测销量的滞后修正…`）最后一格末尾追加：`；★ 09-24 D-3 ② 已做（金标改指 as_of − 7），新基准待与运营对`。

Run: `.venv/bin/python -m pytest -q tests/test_forecast_baseline.py tests/test_forecast_baseline_golden.py`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add forecast/baseline.py tests/test_forecast_baseline.py tests/test_forecast_baseline_golden.py docs/00-待裁定清单.md
git commit -m "feat(forecast): 滞后修正 embargo 7 天（设计取值·未实测）—— 门禁如期变红 N 个 case，改指 as_of−7 新基准（D-3 ②）"
```

（把 `N` 换成 Step 4 实测的失败 case 数。）

---

### Task 13: 取数协议加「日销」与「断货日」+ FixtureSource

spec §3.2（日销 msku × sid × 日；censor = FBA 可售 ≤ 0 的日子）· OP-15

**Files:**
- Modify: `dim/source.py`（`StockDays` + 协议两个方法）
- Modify: `dim/fixture_source.py`
- Create: `tests/fixtures/daily_sales.csv` · `tests/fixtures/fba_stockout_days.csv`
- Test: `tests/test_dim_fixture.py`（追加）

**Interfaces:**
- Produces:
  - `class StockDays(NamedTuple)`: `censored: frozenset[dt.date]`（被接受的采集日里该 msku 缺行或 ≤ 0）· `captured: frozenset[dt.date]`（被接受的采集日）· `rejected: tuple[dict, ...]`（没过掉档守卫的日子）
  - `Source.daily_sales(seller_sku: str, sid: str, start: dt.date, end: dt.date) -> dict[dt.date, int]`（两端含；没单的日子**不出现**）
  - `Source.fba_stock_days(seller_sku: str, sid: str, start: dt.date, end: dt.date) -> StockDays`
  - fixture 数据：2026-06-01 ~ 2026-09-21 每天 MSKU-A@11072 = 4、MSKU-B@11072 = 3、MSKU-C@11094 = 2（09-01~09-07 断货、无行）、MSKU-D@11072 = 1、MSKU-E@11072 = 1；MSKU-W 无

- [ ] **Step 1: 生成 fixture CSV**

```bash
.venv/bin/python - <<'EOF'
import csv, datetime as dt
d0, d1 = dt.date(2026, 6, 1), dt.date(2026, 9, 21)
out = {dt.date(2026, 9, 1) + dt.timedelta(days=i) for i in range(7)}
rows = []
for sku, sid, units in [("MSKU-A", "11072", 4), ("MSKU-B", "11072", 3), ("MSKU-C", "11094", 2),
                        ("MSKU-D", "11072", 1), ("MSKU-E", "11072", 1)]:
    d = d0
    while d <= d1:
        if not (sku == "MSKU-C" and d in out):
            rows.append((sku, sid, d.isoformat(), units))
        d += dt.timedelta(days=1)
with open("tests/fixtures/daily_sales.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f, lineterminator="\n"); w.writerow(["seller_sku", "sid", "date", "units"]); w.writerows(rows)
with open("tests/fixtures/fba_stockout_days.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f, lineterminator="\n"); w.writerow(["seller_sku", "sid", "date"])
    w.writerows(("MSKU-C", "11094", d.isoformat()) for d in sorted(out))
EOF
wc -l tests/fixtures/daily_sales.csv tests/fixtures/fba_stockout_days.csv   # 559 / 8
```

- [ ] **Step 2: 写失败的测试**

`tests/test_dim_fixture.py` 追加：

```python
def test_daily_sales_returns_only_days_with_orders(s):
    D = dt.date
    assert s.daily_sales("MSKU-A", "11072", D(2026, 9, 1), D(2026, 9, 3)) == {
        D(2026, 9, 1): 4, D(2026, 9, 2): 4, D(2026, 9, 3): 4}
    # ★ 断货那几天没有行 —— 补 0 是 baseline_rate densify 的事，不是取数层的
    assert s.daily_sales("MSKU-C", "11094", D(2026, 9, 6), D(2026, 9, 9)) == {
        D(2026, 9, 8): 2, D(2026, 9, 9): 2}
    assert s.daily_sales("MSKU-W", "90001", D(2026, 6, 1), D(2026, 9, 21)) == {}


def test_fba_stock_days_names_censored_and_captured_days(s):
    D = dt.date
    got = s.fba_stock_days("MSKU-C", "11094", D(2026, 8, 30), D(2026, 9, 2))
    assert got.censored == frozenset({D(2026, 9, 1), D(2026, 9, 2)})
    assert got.captured == frozenset(D(2026, 8, 30) + dt.timedelta(days=i) for i in range(4))
    assert got.rejected == ()
```

（用文件里已有的 `s` 夹具。）

- [ ] **Step 3: 跑，确认失败**

Run: `.venv/bin/python -m pytest -q tests/test_dim_fixture.py`
Expected: FAIL —— `AttributeError: 'FixtureSource' object has no attribute 'daily_sales'`

- [ ] **Step 4: 实现**

`dim/source.py`：`InTransit` 下面加

```python
class StockDays(NamedTuple):
    """censor 用的断货日（spec §3.2 · OP-15）。★ 三个集合分开：
    「那天断货」「那天采到了、没断」「那天没采到 / 没过掉档守卫」是三件事 ——
    第三种不 censor，也不许被当成第二种。"""
    censored: frozenset[dt.date]
    captured: frozenset[dt.date]
    rejected: tuple[dict, ...]
```

`Source` 协议里加：

```python
    def daily_sales(self, seller_sku: str, sid: str, start: dt.date, end: dt.date
                    ) -> dict[dt.date, int]:
        """[start, end] 每天的实际销量（按 order_item 去重、排除 Cancelled/Pending）。
        ★ 没单的日子**不出现** —— 补 0 是 baseline_rate densify 的事。"""

    def fba_stock_days(self, seller_sku: str, sid: str, start: dt.date, end: dt.date
                       ) -> StockDays:
        """[start, end] 里哪些天该 msku FBA 可售 ≤ 0（censor）。"""
```

`dim/fixture_source.py`：import `StockDays`，加

```python
    def daily_sales(self, seller_sku: str, sid: str, start: dt.date, end: dt.date
                    ) -> dict[dt.date, int]:
        out: dict[dt.date, int] = {}
        for r in self._rows("daily_sales.csv"):
            if r["seller_sku"] == seller_sku and r["sid"] == sid:
                d = dt.date.fromisoformat(r["date"])
                if start <= d <= end:
                    out[d] = out.get(d, 0) + int(r["units"])
        return out

    def fba_stock_days(self, seller_sku: str, sid: str, start: dt.date, end: dt.date
                       ) -> StockDays:
        """★ fixture 口径：窗口里每一天都算「采到了」—— 底料里只有断货日需要点名。"""
        days = frozenset(start + dt.timedelta(days=i) for i in range((end - start).days + 1))
        out = frozenset(dt.date.fromisoformat(r["date"]) for r in self._rows("fba_stockout_days.csv")
                        if r["seller_sku"] == seller_sku and r["sid"] == sid)
        return StockDays(out & days, days, ())
```

- [ ] **Step 5: 跑，确认通过；提交**

Run: `.venv/bin/python -m pytest -q tests/test_dim_fixture.py tests/test_layering.py`
Expected: PASS

```bash
git add dim/source.py dim/fixture_source.py tests/fixtures/daily_sales.csv tests/fixtures/fba_stockout_days.csv tests/test_dim_fixture.py
git commit -m "feat(dim): 取数协议加日销与断货日（censor 三分：断货/采到/没采到）+ fixture 底料"
```

---

### Task 14: `ChSource.daily_sales` · `ChSource.fba_stock_days`

spec §3.2（`amazon_sp_api_report_all_orders` 按 order_item 去重、改成按日；`lingxing_inventory_fba_detail` 各 `_captured_date` 的 `afn_fulfillable_quantity ≤ 0`）· OP-15 · CLAUDE.md 日志三问 · CH 只读

**Files:**
- Modify: `dim/ch_source.py`（`SQL_DAILY_SALES` · `SQL_FBA_STOCK_DAYS` · `parse_stock_days` · 两个方法）
- Test: `tests/test_dim_ch_source_daily.py`（新建）
- Modify: `tests/test_dim_fixture.py`（「ChSource 不许留占位」那条的方法清单 `("as_of", "monthly_sales_history", "onhand_available", "purchase_in_transit")` 补上 `"daily_sales", "fba_stock_days"` —— 先补、先红：`AttributeError: type object 'ChSource' has no attribute 'daily_sales'`）

**Interfaces:**
- Consumes: 既有 `ORDERS_TABLE` · `FBA_DETAIL_TABLE` · `order_store_map.store_for` · `ChUnavailable` · `UnknownShape` · `self._classify` · `self._min_rows` · `self._min_distinct_sid` · `self._history_windows`；Task 13 的 `StockDays`
- Produces:
  - `parse_stock_days(rows: list[tuple], start: dt.date, end: dt.date, *, min_rows: int, min_distinct_sid: int) -> StockDays` —— 行形 `(date, rows, uniq_sid, hit_rows, units)`
  - `ChSource.daily_sales(...)`、`ChSource.fba_stock_days(...)`（协议签名）
  - `ChSource.history_window(seller_sku, sid)` 在 `daily_sales` 之后返回 `{"store", "sales_channel", "from", "until", "days_with_orders", "lag_note"}`

- [ ] **Step 1: 写失败的测试**

`tests/test_dim_ch_source_daily.py`：

```python
import datetime as dt
import logging

import pytest

from dim import ch_source as cs
from dim import order_store_map

D = dt.date


class Q:
    def __init__(self, rows=None, exc=None):
        self.rows, self.exc, self.calls = rows or [], exc, []

    def __call__(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params))
        if self.exc:
            raise self.exc
        return list(self.rows)


def test_daily_sales_dedups_by_order_item_and_keeps_the_window():
    q = Q([(D(2026, 9, 1), 4), (D(2026, 9, 2), 0)])
    got = cs.ChSource(q).daily_sales("MSKU-A", "11072", D(2026, 6, 17), D(2026, 9, 14))
    assert got == {D(2026, 9, 1): 4, D(2026, 9, 2): 0}
    sql, params = q.calls[0]
    assert "GROUP BY amazon_order_id, order_item_id" in sql and "argMax(order_status" in sql
    store, channel = order_store_map.store_for("11072")
    assert params == {"store": store, "channel": channel, "seller_sku": "MSKU-A",
                      "start": "2026-06-17", "end": "2026-09-14"}


def test_daily_sales_outside_the_window_is_an_unknown_shape():
    q = Q([(D(2026, 9, 20), 4)])
    with pytest.raises(cs.UnknownShape):
        cs.ChSource(q).daily_sales("MSKU-A", "11072", D(2026, 6, 17), D(2026, 9, 14))


def test_daily_sales_failure_says_who_how_long_and_how(caplog):
    caplog.set_level(logging.INFO)
    q = Q(exc=TimeoutError("read timed out"))
    with pytest.raises(cs.ChUnavailable) as ei:
        cs.ChSource(q).daily_sales("MSKU-A", "11072", D(2026, 6, 17), D(2026, 9, 14))
    assert ei.value.target == cs.ORDERS_TABLE
    assert "op=ch_daily_sales outcome=fail" in caplog.text and "elapsed_ms=" in caplog.text


def test_daily_sales_leaves_a_window_mark_for_its_own_key():
    src = cs.ChSource(Q([(D(2026, 9, 1), 4)]))
    src.daily_sales("MSKU-A", "11072", D(2026, 6, 17), D(2026, 9, 14))
    w = src.history_window("MSKU-A", "11072")
    assert (w["from"], w["until"], w["days_with_orders"]) == ("2026-06-17", "2026-09-14", 1)
    assert src.history_window("MSKU-C", "11094") == {}


def test_parse_stock_days_three_ways():
    """OP-15：过了掉档守卫的日子里「缺行或 ≤ 0」= 断货；没过的日子不 censor、点名。"""
    rows = [(D(2026, 9, 1), 8000, 21, 1, 5),    # 采到了、有货
            (D(2026, 9, 2), 8000, 21, 0, 0),    # 采到了、缺行（L-1：0 库存不返回行）→ 断货
            (D(2026, 9, 3), 8000, 21, 2, 0),    # 采到了、可售 0 → 断货
            (D(2026, 9, 4), 100, 3, 0, 0)]      # 掉档 → 不 censor
    got = cs.parse_stock_days(rows, D(2026, 9, 1), D(2026, 9, 5), min_rows=7934,
                              min_distinct_sid=21)
    assert got.censored == frozenset({D(2026, 9, 2), D(2026, 9, 3)})
    assert got.captured == frozenset({D(2026, 9, 1), D(2026, 9, 2), D(2026, 9, 3)})
    assert got.rejected == ({"date": "2026-09-04", "rows": 100, "uniq_sid": 3},)


def test_fba_stock_days_queries_the_detail_table_read_only():
    q = Q([(D(2026, 9, 2), 8000, 21, 0, 0)])
    got = cs.ChSource(q).fba_stock_days("MSKU-C", "11094", D(2026, 7, 21), D(2026, 9, 14))
    assert got.censored == frozenset({D(2026, 9, 2)})
    sql, params = q.calls[0]
    assert cs.FBA_DETAIL_TABLE in sql and "afn_fulfillable_quantity" in sql
    assert params == {"sid": "11094", "seller_sku": "MSKU-C", "start": "2026-07-21",
                      "end": "2026-09-14"}


def test_fba_stock_days_failure_is_classified():
    with pytest.raises(cs.ChUnavailable) as ei:
        cs.ChSource(Q(exc=ConnectionRefusedError(111, "refused"))).fba_stock_days(
            "MSKU-C", "11094", D(2026, 7, 21), D(2026, 9, 14))
    assert ei.value.target == cs.FBA_DETAIL_TABLE
```

- [ ] **Step 2: 跑，确认失败**

Run: `.venv/bin/python -m pytest -q tests/test_dim_ch_source_daily.py`
Expected: FAIL —— `AttributeError: 'ChSource' object has no attribute 'daily_sales'`

- [ ] **Step 3: 实现**

`dim/ch_source.py`：在 `SQL_MONTHLY_SALES` 之后加

```python
#: spec §3.2：同 SQL_MONTHLY_SALES 的三个口径（按 order_item argMax 去重 · 排除
#: Cancelled/Pending · 只统计一个 (store, sales_channel)），只是按日分组。
SQL_DAILY_SALES = f"""
SELECT d, toInt64(sum(units)) AS units
  FROM (
    SELECT toDate(any(purchase_date)) AS d,
           if(argMax(order_status, _captured_date) NOT IN ('Cancelled', 'Pending'),
              argMax(quantity, _captured_date), 0) AS units
      FROM {ORDERS_TABLE}
     WHERE store = {{store:String}} AND sales_channel = {{channel:String}}
       AND sku = {{seller_sku:String}}
       AND toDate(purchase_date) >= {{start:Date}}
       AND toDate(purchase_date) <= {{end:Date}}
     GROUP BY amazon_order_id, order_item_id
  )
 GROUP BY d
 ORDER BY d
"""

#: spec §3.2 · OP-15：每个采集日一行 —— 当天整表的行数 / 店数（掉档守卫，同
#: pick_snapshot_date 的 min_rows / min_distinct_sid）+ 该 msku 的行数与可售合计。
#: ★ msku 缺行不在 SQL 里判：缺行在「被接受的采集日」上才等于断货（L-1），在掉档日上什么都不是。
SQL_FBA_STOCK_DAYS = f"""
SELECT _captured_date AS d, count() AS rows, uniq(sid) AS uniq_sid,
       countIf(toString(sid) = {{sid:String}} AND seller_sku = {{seller_sku:String}}) AS hit_rows,
       toInt64(sumIf(afn_fulfillable_quantity,
                     toString(sid) = {{sid:String}} AND seller_sku = {{seller_sku:String}})) AS units
  FROM {FBA_DETAIL_TABLE}
 WHERE _captured_date >= {{start:Date}} AND _captured_date <= {{end:Date}}
 GROUP BY _captured_date
 ORDER BY _captured_date
"""


def parse_stock_days(rows: list[tuple], start: dt.date, end: dt.date, *,
                     min_rows: int, min_distinct_sid: int) -> StockDays:
    censored: set[dt.date] = set()
    captured: set[dt.date] = set()
    rejected: list[dict] = []
    for d, n_rows, n_sid, hit, units in rows:
        if not start <= d <= end:
            raise UnknownShape(f"采集日 {d} 落在窗口 {start}~{end} 之外")
        if int(n_rows) < min_rows or int(n_sid) < min_distinct_sid:
            rejected.append({"date": d.isoformat(), "rows": int(n_rows), "uniq_sid": int(n_sid)})
            continue
        captured.add(d)
        if int(hit) == 0 or int(units) <= 0:
            censored.add(d)
    return StockDays(frozenset(censored), frozenset(captured), tuple(rejected))
```

`from dim.source import InTransit` 改成 `from dim.source import InTransit, StockDays`。`ChSource` 里加：

```python
    def daily_sales(self, seller_sku: str, sid: str, start: dt.date, end: dt.date
                    ) -> dict[dt.date, int]:
        store, channel = order_store_map.store_for(str(sid))
        params = {"store": store, "channel": channel, "seller_sku": seller_sku,
                  "start": start.isoformat(), "end": end.isoformat()}
        t0 = time.perf_counter()
        try:
            rows = self._q(SQL_DAILY_SALES, params)
        except Exception as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            cause = self._classify(e)
            log.warning("op=ch_daily_sales outcome=fail target=%s store=%s channel=%s msku=%s"
                        " start=%s end=%s elapsed_ms=%d cause=%s", ORDERS_TABLE, store, channel,
                        seller_sku, start, end, elapsed_ms, cause)
            raise ChUnavailable(target=ORDERS_TABLE, cause=cause) from e
        elapsed_ms = (time.perf_counter() - t0) * 1000
        out: dict[dt.date, int] = {}
        for d, units in rows:
            if not start <= d <= end:
                raise UnknownShape(f"日销落在窗口之外：{d}（窗口 {start}~{end}）")
            out[d] = int(units)
        self._history_windows[(seller_sku, str(sid))] = {
            "store": store, "sales_channel": channel, "from": start.isoformat(),
            "until": end.isoformat(), "days_with_orders": len(out),
            "lag_note": "orders_are_lagging_collected; deduped_by_order_item; "
                        "estimation_anchor_moved_back_by_embargo_days"}
        log.info("op=ch_daily_sales outcome=ok store=%s channel=%s msku=%s start=%s end=%s"
                 " days=%d units=%d elapsed_ms=%d", store, channel, seller_sku, start, end,
                 len(out), sum(out.values()), elapsed_ms)
        return out

    def fba_stock_days(self, seller_sku: str, sid: str, start: dt.date, end: dt.date
                       ) -> StockDays:
        params = {"sid": str(sid), "seller_sku": seller_sku,
                  "start": start.isoformat(), "end": end.isoformat()}
        t0 = time.perf_counter()
        try:
            rows = self._q(SQL_FBA_STOCK_DAYS, params)
        except Exception as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            cause = self._classify(e)
            log.warning("op=ch_stock_days outcome=fail target=%s msku=%s sid=%s start=%s end=%s"
                        " elapsed_ms=%d cause=%s", FBA_DETAIL_TABLE, seller_sku, sid, start, end,
                        elapsed_ms, cause)
            raise ChUnavailable(target=FBA_DETAIL_TABLE, cause=cause) from e
        elapsed_ms = (time.perf_counter() - t0) * 1000
        got = parse_stock_days(rows, start, end, min_rows=self._min_rows,
                               min_distinct_sid=self._min_distinct_sid)
        window_days = (end - start).days + 1
        for r in got.rejected:
            log.warning("op=ch_stock_days outcome=rejected_day msku=%s sid=%s %s —— 不 censor",
                        seller_sku, sid, r)
        log.info("op=ch_stock_days outcome=ok msku=%s sid=%s start=%s end=%s captured=%d"
                 " censored=%d rejected=%d uncaptured=%d elapsed_ms=%d", seller_sku, sid, start,
                 end, len(got.captured), len(got.censored), len(got.rejected),
                 window_days - len(got.captured) - len(got.rejected), elapsed_ms)
        return got
```

- [ ] **Step 4: 跑，确认通过 + CH 只读守卫**

Run: `.venv/bin/python -m pytest -q tests/test_dim_ch_source_daily.py tests/test_dim_ch_source_forecast.py tests/test_dim_fixture.py tests/test_layering.py`
Expected: PASS（`test_l7_no_writes_to_clickhouse` 仍绿）

- [ ] **Step 5: 提交**

```bash
git add dim/ch_source.py tests/test_dim_ch_source_daily.py tests/test_dim_fixture.py
git commit -m "feat(dim): ChSource 日销（order_item 去重按日）与断货日（过掉档守卫才 censor，拒收日点名）"
```

---

### Task 15: 认领时 seed 预测销量 + `system_basis`；demand 行带依据

spec §3.3（整数、向上取整、依据随数存、没历史 NULL 点名、`system_extrapolated` 如实 false）· §3.4（认领时算）· §6（`params.embargo_days`）

**Files:**
- Modify: `api/ui/plans.py`（`claim()` 换算法；`_demand_row` 加 `system_basis`；`grid()` 与 `put_demand()` 传它；grid `embargo_days=EMBARGO_DAYS`）
- Modify: `tests/test_api_claims.py` · `tests/test_api_forecast_source.py` · `tests/test_source_per_request.py` · `tests/test_api_grid.py`（假源 / 断言跟着换，逐处写理由）
- Test: `tests/test_api_claims_forecast.py`（新建）

**Interfaces:**
- Consumes: Task 11/12 `data_windows, estimate_rate, EMBARGO_DAYS, HALFLIFE_DAYS, WINDOW_DAYS, TARGET_WINDOW_DAYS, SHRINK_PRIOR, WINSOR_Q`；Task 2 `days_in_month, units_for_month`；Task 13/14 `daily_sales, fba_stock_days`
- Produces:
  - `plan_demand_cell.system_basis`（每格）：`{"method": "baseline_rate_v1", "as_of", "estimation_anchor", "embargo_days", "base_rate", "raw_rate", "n_eff", "target_rate", "winsor_cap", "first_seen", "window": {"from", "until"}, "target_window": {"from", "until"}, "censored_days", "stock_days_rejected", "stock_days_uncaptured", "reason": null | "no_sales_in_window", "constants": {...}, "days_in_month", "units_raw"}`
  - grid / put_demand 的 demand 行多一个键 `system_basis`（十键）

- [ ] **Step 1: 写失败的测试**

`tests/test_api_claims_forecast.py`：

```python
"""spec §3.3 / §3.4：认领时用 base_rate 种预测销量，依据随数一起存。
fixture：as_of 2026-09-21 → 估计锚点 09-14；MSKU-A 每天 4 件（OP-3：4.325/天），MSKU-C 每天 2 件、09-01~07 断货。"""
import pytest

from shared.pg_client import pg_conn


def H(a):
    return {"x-actor": a}


def claim(client, seed, ms):
    pid = client.post("/v1/plans", json={"title": "t", "period_start": "2026-10-01", "months": 3},
                      headers=H(seed.actor)).json()["plan_id"]
    r = client.post(f"/v1/plans/{pid}/claims", json={"seller_sku": ms[0], "sid": ms[1]},
                    headers=H(seed.actor))
    assert r.status_code == 200, r.text
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT system_units, system_extrapolated, system_basis FROM plan_demand_cell"
                    " WHERE plan_id = %s ORDER BY period_start", (pid,))
        return r.json(), cur.fetchall()


def test_claim_seeds_rate_times_days_rounded_up(client, seed):
    body, rows = claim(client, seed, seed.msku_a)
    assert [r[0] for r in rows] == [135, 130, 135]          # ceil(4.3251 × 31/30/31)
    assert [r[1] for r in rows] == [False, False, False]    # ★ spec §3.3：算法如实给 false
    b = rows[0][2]
    assert b["method"] == "baseline_rate_v1"
    assert (b["as_of"], b["estimation_anchor"], b["embargo_days"]) == ("2026-09-21", "2026-09-14", 7)
    assert b["base_rate"] == pytest.approx(4.325139122267326, rel=1e-12)
    assert b["window"] == {"from": "2026-07-21", "until": "2026-09-14"}
    assert b["target_window"] == {"from": "2026-06-17", "until": "2026-09-14"}
    assert (b["days_in_month"], b["censored_days"]) == (31, 0)
    assert b["units_raw"] == pytest.approx(4.325139122267326 * 31)
    assert rows[1][2]["days_in_month"] == 30
    assert body["no_history"] == []


def test_stockout_days_are_censored_and_counted(client, seed):
    _, rows = claim(client, seed, seed.msku_c)
    assert [r[0] for r in rows] == [67, 65, 67]
    assert rows[0][2]["censored_days"] == 7
    assert rows[0][2]["base_rate"] == pytest.approx(2.15964558760763, rel=1e-12)


def test_no_history_is_null_named_and_explained(client, seed, use_source):
    from api.ui import plans
    from dim.fixture_source import FixtureSource

    class NoSales(FixtureSource):
        def daily_sales(self, *a):
            return {}

    use_source(lambda: NoSales(plans.FIXTURES))
    body, rows = claim(client, seed, seed.msku_a)
    assert [r[0] for r in rows] == [None, None, None]
    assert rows[0][2]["reason"] == "no_sales_in_window" and rows[0][2]["base_rate"] is None
    assert body["no_history"] == [{"seller_sku": "MSKU-A", "sid": "11072",
                                   "reason": "no_sales_history"}]


def test_grid_demand_rows_carry_the_basis_and_params_carry_embargo(client, seed):
    claim(client, seed, seed.msku_a)
    pid = client.get("/v1/plans").json()["plans"][0]["plan_id"]
    g = client.get(f"/v1/plans/{pid}/grid").json()
    assert g["demand"][0]["system_basis"]["days_in_month"] == 31
    assert g["params"]["embargo_days"] == 7
    a = [r for r in g["forecast"] if r["seller_sku"] == "MSKU-A"]
    assert a[0]["basis"]["bridge"]["units"] == 39      # 9 月 ceil(4.3251×30)=130，× 0.3
    assert a[0]["opening"] == 261
```

- [ ] **Step 2: 跑，确认失败**

Run: `.venv/bin/python -m pytest -q tests/test_api_claims_forecast.py`
Expected: FAIL —— `assert [100, 120, 90] == [135, 130, 135]`

- [ ] **Step 3: 实现 `claim()`**

`api/ui/plans.py`：

1. import：删 `from forecast.estimate import InsufficientHistory, monthly_estimate`；加
   ```python
   import json
   from forecast.baseline import (EMBARGO_DAYS, HALFLIFE_DAYS, SHRINK_PRIOR, TARGET_WINDOW_DAYS,
                                  WINDOW_DAYS, WINSOR_Q, data_windows, estimate_rate)
   from forecast.calendar import days_in_month, units_for_month
   ```
2. 新增（放在 `claim()` 上面）：
   ```python
   def _system_basis(est, stock, win) -> dict:
       """spec §3.3：依据随数一起存 —— 界面点开能看到「预测 124 = 4.13 件/天 × 30 天」。"""
       r = est.result
       return {
           "method": "baseline_rate_v1", "as_of": est.as_of.isoformat(),
           "estimation_anchor": est.estimation_anchor.isoformat(),
           "embargo_days": est.embargo_days,
           "base_rate": r.rate if r else None, "raw_rate": r.raw_rate if r else None,
           "n_eff": r.n_eff if r else None, "target_rate": r.target_rate if r else None,
           "winsor_cap": r.winsor_cap if r else None,
           "first_seen": r.first_seen.isoformat() if (r and r.first_seen) else None,
           "window": {"from": win.censor_from.isoformat(), "until": win.until.isoformat()},
           "target_window": {"from": win.sales_from.isoformat(), "until": win.until.isoformat()},
           "censored_days": r.censored_days if r else len(stock.censored),
           "stock_days_rejected": len(stock.rejected),
           "stock_days_uncaptured": WINDOW_DAYS - len(stock.captured) - len(stock.rejected),
           "reason": None if r else "no_sales_in_window",
           "constants": {"halflife_days": HALFLIFE_DAYS, "window_days": WINDOW_DAYS,
                         "target_window_days": TARGET_WINDOW_DAYS, "shrink_prior": SHRINK_PRIOR,
                         "winsor_q": WINSOR_Q},
       }
   ```
3. `claim()`：`est = None` 改成 `rate = None; basis = None`；把 `else:` 分支（Amazon 店）的 try 块换成
   ```python
            try:
                as_of = source.as_of()
                win = data_windows(as_of)
                daily = source.daily_sales(seller_sku, sid, win.sales_from, win.until)
                stock = source.fba_stock_days(seller_sku, sid, win.censor_from, win.until)
            except UnknownStore as e:
                ...（原样保留整支）
            except (ChUnavailable, ChDataUnusable, UnknownShape) as e:
                raise _source_error(e) from e
            else:
                est = estimate_rate(daily, stock.censored, as_of)
                basis = _system_basis(est, stock, win)
                rate = est.result.rate if est.result else None
                history_window = _history_window(source, seller_sku, sid)
                if rate is None:
                    # ★ 没有历史 ≠ 预估 0：格子照建，system_units 留 NULL 并点名（spec §3.3）
                    no_history.append({"seller_sku": seller_sku, "sid": sid,
                                       "reason": "no_sales_history"})
   ```
   （原 `except InsufficientHistory` 支删除 —— 它的职责并进了 `rate is None`。）
4. 插格循环：
   ```python
        for period in periods:
            n = days_in_month(period)
            units = units_for_month(rate, n) if rate is not None else None
            cell_basis = (None if basis is None else
                          json.dumps({**basis, "days_in_month": n,
                                      "units_raw": rate * n if rate is not None else None}))
            cur.execute(
                "INSERT INTO plan_demand_cell (plan_id, seller_sku, sid, period_start,"
                " system_units, system_extrapolated, system_basis, updated_by)"
                " VALUES (%s, %s, %s, %s, %s, FALSE, %s, %s)"
                " ON CONFLICT (plan_id, seller_sku, sid, period_start) DO UPDATE"
                "    SET system_units = EXCLUDED.system_units,"
                "        system_extrapolated = EXCLUDED.system_extrapolated,"
                "        system_basis = EXCLUDED.system_basis",
                (plan_id, seller_sku, sid, period, units, cell_basis, who))
   ```
5. `_demand_row(...)` 加参数 `system_basis: dict | None`，返回 dict 加 `"system_basis": system_basis`；`grid()` 传 `sbasis`（Task 9 已取出）；`put_demand()` 的 `RETURNING` 加 `d.system_basis`，解包成 `sysu, extrap, sku, sbasis` 并传入。
6. `grid()` 里 `forecast_payload(..., embargo_days=EMBARGO_DAYS, ...)`。

- [ ] **Step 4: 让既有测试跟上（逐处写理由注释「spec §3.1：预测换成 base_rate × 天数」）**

- `tests/test_api_claims.py::test_claim_seeds_both_grids_and_names_mskus_without_history`：`[100, 120, 90]` → `[135, 130, 135]`，注释改成「fixture 日销 4 件/天 → base_rate 4.325（OP-3）× 天数向上取整」。
- `tests/test_api_claims.py::test_msku_without_history_is_seeded_null_and_named` 的 `NoHistory.monthly_sales_history` → `def daily_sales(self, *a): return {}`。
- `tests/test_api_forecast_source.py`：每个假源里的 `monthly_sales_history` 改名 `daily_sales`（同样的 raise / return；返回空历史的那个改成 `return {}`）；只实现了 `monthly_sales_history` 就走到 claim 的假源，若 `daily_sales` 正常返回，补 `def fba_stock_days(self, *a): return StockDays(frozenset(), frozenset(), ())`（`from dim.source import StockDays`）。
- `tests/test_source_per_request.py::FakeCh.__call__`：在最前面加两支（顺序要紧：`SQL_FBA_STOCK_DAYS` 也含 `GROUP BY _captured_date`，`SQL_DAILY_SALES` 也含 `ORDERS_TABLE`）：
  ```python
        if "countIf(" in one:                       # SQL_FBA_STOCK_DAYS
            self.calls.append("stock_days")
            return []
        if cs.ORDERS_TABLE in one and "GROUP BY d " in one + " ":   # SQL_DAILY_SALES
            self.calls.append("daily_sales")
            if self.gate is not None:
                self.gate()
            return []
  ```
  （并发 claim 的 `history_window` 断言照旧成立：`daily_sales` 按键记录 store/channel。）
- `tests/test_api_grid.py` 两处 demand 行键集合断言各加 `"system_basis"`（理由：spec §3.3 依据随数走）。

- [ ] **Step 5: 跑，确认通过 + 全量回归**

Run: `.venv/bin/python -m pytest -q tests/test_api_claims_forecast.py tests/test_api_claims.py tests/test_api_forecast_source.py tests/test_source_per_request.py tests/test_api_grid.py tests/test_api_grid_forecast.py`
Expected: PASS（`tests/test_grid_fixture.py` 此时仍红 —— Task 16 重固化）

- [ ] **Step 6: 提交**

```bash
git add api/ui/plans.py tests/test_api_claims_forecast.py tests/test_api_claims.py tests/test_api_forecast_source.py tests/test_source_per_request.py tests/test_api_grid.py
git commit -m "feat(api): 认领时按 base_rate×天数(向上取整)种预测销量，依据落 system_basis、随 demand 行出去"
```

---

### Task 16: 退役 `monthly_estimate` + 重固化 fixture（真数）+ 前端跟上

spec §3.3（不再产生外推）· `00e`:44 被 spec 取代 · OP-16

**Files:**
- Delete: `forecast/estimate.py` · `tests/test_forecast_estimate.py`
- Modify: `tests/test_dim_ch_source_forecast.py:800,812`、`dim/ch_source.py:359` 里指向 `forecast/estimate.py` 的注释（改指 `forecast/baseline.py`，或删掉那半句）
- Modify: `tests/test_grid_fixture.py`（外推形态断言翻转；加真数断言）
- Modify: `tests/fixtures/grid_response.json` · `web/src/api/fixtures/grid-1.json`（重固化）
- Modify: `web/src/pages/PlanGrid.test.tsx`（「真 fixture：外推标签在 MSKU-C 的 2026-12 上能找到」）· `web/src/api/mock.test.ts`（MSKU-A 的 `system_units` 100/120/90）以及 vitest 跑出来的其它因数值变化而红的断言

- [ ] **Step 1: 改 fixture 形态断言（先红）**

`tests/test_grid_fixture.py::test_the_fixture_covers_the_shapes_that_look_alike`：把 `assert any(r["system_extrapolated"] for r in saved["demand"]), "缺「外推」那一种"` 换成

```python
    # ★ spec §3.3：速率模型对任何月份都算得出 → 不再产生外推；标记列保留、由算法如实给 false
    assert not any(r["system_extrapolated"] for r in saved["demand"])
    a = [r for r in saved["forecast"] if r["seller_sku"] == "MSKU-A"]
    assert a[0]["basis"]["bridge"]["units"] == 39 and a[0]["opening"] == 261
    assert any(r["state"] == "ok" and r["closing"] is not None for r in saved["forecast"])
    assert any(r["reason"] == "unknown_demand" for r in saved["forecast"]), "缺「期望未知」"
```

Run: `.venv/bin/python -m pytest -q tests/test_grid_fixture.py`
Expected: FAIL（旧 fixture 里 MSKU-C 12 月是外推；bridge 为 null）

- [ ] **Step 2: 删 `monthly_estimate`**

```bash
git rm forecast/estimate.py tests/test_forecast_estimate.py
grep -rn "forecast.estimate\|monthly_estimate\|InsufficientHistory\|HistoryGap" --include=*.py . | grep -v "^./.venv"
```
Expected: 只剩注释里的引用 → 按上面 Files 列表改掉；再 grep 一次必须为空。

- [ ] **Step 3: 重固化并拷到前端**

```bash
UPDATE_GRID_FIXTURE=1 .venv/bin/python -m pytest -q tests/test_grid_fixture.py
.venv/bin/python -m pytest -q tests/test_grid_fixture.py
cp tests/fixtures/grid_response.json web/src/api/fixtures/grid-1.json
```

- [ ] **Step 4: 前端跟上**

Run: `cd web && npx vitest run`
逐条处理变红的用例，每处加一行注释写理由：
- `PlanGrid.test.tsx` 「★ 真 fixture：外推标签在 MSKU-C 的 2026-12 上能找到，10 月没有（F15）」→ 改成「★ 真 fixture：没有任何一格带外推标签（spec §3.3：速率模型不外推）」，断言 `screen.queryAllByLabelText('系统外推')` 长度为 0。（外推标签的渲染逻辑不删 —— 字段还在、合成数据的用例仍覆盖它。）
- `mock.test.ts` 里 MSKU-A 的 `system_units` `100/120/90` → `135/130/135`（fixture 日销 → base_rate × 天数）。
- 其它因 grid-1.json 数值变化而红的断言：数字照新 fixture 改，理由同上；**不许**为了变绿删断言。

Run: `cd web && npx vitest run && npx tsc -b`
Expected: 全绿

- [ ] **Step 5: 全量回归 + 提交**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check .`
Expected: 全绿

```bash
git add -A forecast tests dim web/src
git commit -m "refactor: 退役按位置沿用的 monthly_estimate；grid fixture 以 base_rate 真数重固化（前后端同源）"
```

**Phase 2 收口：** `.venv/bin/python -m pytest -q && .venv/bin/ruff check . && (cd web && npx vitest run && npx tsc -b)` 全绿再进 Phase 3。


---

# Phase 3 · 前端（spec §5 · §9 第 3 步）

> 前端约定（CLAUDE.md「写代码与写界面的铁律」）：界面文案只写当场发生的事；说明进 `<details>`，默认收起；数字旁边必须有**文字标签**，不只靠颜色。页面操作流程若改了按钮原文，要同步 `docs/10` §2.5（`test_doc_flow.js` 两边对核）—— 本 Phase 只加按钮不改既有按钮。

### Task 17: 类型 + 网格模型改从 `forecast*` 取库存 + mock 不装作重算

spec §4.6（店铺 = Σ msku，合计不独立算）· §6 · OP-16 · OP-17

**Files:**
- Modify: `web/src/api/types.ts`（新类型；`GridResponse` 加新键；`inventory` / `sku_pipeline` 改成可选，Task 21 删）
- Modify: `web/src/pages/planGridModel.ts`（`MskuRow.forecast` · `SkuBlock.stock` · `GridModel.totals/params/beyond/unallocated`；`inventoryAt` 更名 `stockAt` 并改读 `stock`；orphans 改从 `forecast*` 核对；在途改读 `forecast_sku`）
- Modify: `web/src/pages/planSheetModel.ts` · `web/src/pages/PlanSheet.tsx`（只改 import / 调用名：`inventoryAt` → `stockAt`；`block.inventory.find(...)` 取 `closing_reason` 的两处改读 `block.stock` 的 `reason`）
- Modify: `web/src/api/mock.ts`（`putDemand` / `putPurchase` 之后把该货号的 `forecast*` 行置为 `unknown / not_recomputed_in_mock`）
- Test: `web/src/pages/planGridModel.test.ts` · `web/src/pages/PlanGrid.test.tsx`（构造器从 `inventory` 换成 `forecast_store`，意图不变）· `web/src/api/mock.test.ts`

**Interfaces:**
- Consumes: Task 9/15 的 grid JSON（见 Task 9 Interfaces）
- Produces（`web/src/api/types.ts`）：

```ts
export type ForecastState = 'ok' | 'partial' | 'unknown' | 'not_applicable' | 'elapsed' | 'no_msku';
export type ForecastReason = null | 'as_of_inside_month' | 'unknown_onhand' | 'unknown_demand'
  | 'bridge_rate_unknown' | 'inbound_unknown' | 'not_applicable' | 'elapsed'
  | 'not_recomputed_in_mock';
export interface ForecastBridge {
  from: string; to: string; units: number; by: 'forecast'; rate: number;
  month_totals: Record<Period, number>; onhand_as_of: number;
}
export interface ForecastArrival {
  date: string; units: number | null; order_period: Period; pool: number;
  rule?: string; gap_part?: number; weight_part?: number; reason?: 'gap_unknown';
}
export interface MskuForecast {
  seller_sku: string; sid: Sid; sku: string; period: Period;
  state: ForecastState; reason: ForecastReason; unknown_since: Period | null;
  opening: number | null; demand: number | null; inbound_planned: number | null;
  closing: number | null; stockout_date: string | null;
  basis: { bridge: ForecastBridge | null; arrivals: ForecastArrival[] };
}
export interface StoreForecast {
  sku: string; sid: Sid; period: Period; state: ForecastState; reason: ForecastReason;
  demand: number | null; system: number | null;
  opening: number | null; inbound_planned: number | null; closing: number | null;
}
export interface SkuPipeline {
  making: number; shipping: number; ordered_open: number;
  ordered_open_sources: { period: Period; units: number; ref: string }[];
  fba_inbound: null; fba_inbound_reason: 'not_fetched';
}
export interface SkuForecast {
  sku: string; period: Period; state: ForecastState;
  demand: number | null; system: number | null;
  opening: number | null; stock_demand: number | null; inbound_planned: number | null;
  closing: number | null; excluded_not_applicable: number;
  pipeline: SkuPipeline; in_transit: number; in_transit_excludes: 'fba_inbound'[];
  unfilled_purchase: Period[];
}
export interface BeyondHorizon {
  sku: string; order_period: Period; units: number; placed: string; made: string; arrive: string;
  reason: 'arrives_after_horizon' | 'arrives_before_as_of';
}
export interface Unallocated {
  sku: string; order_period: Period; arrive: string; units: number;
  reason: 'gap_unknown' | 'no_fba_msku'; unknown: [string, Sid][];
}
export interface ForecastParams {
  as_of: string; route: 'oversea_sea'; make_days: number; ship_days: number;
  anchor_day: number; embargo_days: number | null;
}
export interface SystemBasis {
  method: 'baseline_rate_v1'; as_of: string; estimation_anchor: string; embargo_days: number;
  base_rate: number | null; days_in_month: number; units_raw: number | null;
  window: { from: string; until: string }; target_window: { from: string; until: string };
  censored_days: number; stock_days_rejected: number; stock_days_uncaptured: number;
  reason: null | 'no_sales_in_window';
}
// DemandCell 加：system_basis: SystemBasis | null;
// GridResponse 加：params, forecast, forecast_store, forecast_sku, beyond_horizon, unallocated
//   （类型如上）；inventory?: InventoryCell[]; sku_pipeline?: SkuPipelineRow[] 改为可选
```

- `planGridModel.ts` 新导出：`qtyOf(state: ForecastState, v: number | null): QtyValue` · `stockAt(block: SkuBlock, p: Period): QtyValue` · `mskuStockAt(row: MskuRow, p: Period): QtyValue` · `systemAt(block: SkuBlock, p: Period): QtyValue` · `totalsAt(model: GridModel, sku: string, p: Period): SkuForecast | undefined`；`Orphan.kind` 加 `'forecast_sum_disagrees'`

- [ ] **Step 1: 写失败的测试**

`web/src/pages/planGridModel.test.ts`：把 `inv()` 构造器换成下面的 `st()` / `fm()` / `fk()`，`makeGrid()` 用它们给 `forecast_store` / `forecast` / `forecast_sku` / `params` / `beyond_horizon: []` / `unallocated: []`（**数字沿用原来 inventory 行的**：11072 → `st('11072', P0, 420, 200)`、`st('11072', P1, 200, null)`、`st('11072', P2, null, null)`；90001 三格 `st(..., 'not_applicable')`；在途 10 月 80 → `fk(P0, 80)`、其余 `fk(p, 0)`）；原来断言 `m.blocks[0]!.inventory` 的改成 `.stock`，`inventoryAt` 改 `stockAt` —— **意图逐条保留**。再追加：

```ts
const st = (sid: string, period: string, opening: number | null, closing: number | null,
            state: ForecastState = closing === null ? 'unknown' : 'ok',
            demand: number | null = opening !== null && closing !== null ? opening - closing : null,
): StoreForecast => ({
  sku: 'SKU-1', sid, period, state,
  reason: state === 'ok' ? null : state === 'not_applicable' ? 'not_applicable' : 'unknown_demand',
  demand, system: null, opening, inbound_planned: 0, closing,
});
const fm = (seller_sku: string, sid: string, period: string, closing: number | null): MskuForecast => ({
  seller_sku, sid, sku: 'SKU-1', period, state: closing === null ? 'unknown' : 'ok',
  reason: closing === null ? 'unknown_demand' : null, unknown_since: null,
  opening: null, demand: null, inbound_planned: 0, closing, stockout_date: null,
  basis: { bridge: null, arrivals: [] },
});
const fk = (period: string, making: number, shipping = 0, ordered = 0): SkuForecast => ({
  sku: 'SKU-1', period, state: 'ok', demand: null, system: null, opening: null,
  stock_demand: null, inbound_planned: 0, closing: null, excluded_not_applicable: 1,
  pipeline: { making, shipping, ordered_open: ordered, ordered_open_sources: [],
              fba_inbound: null, fba_inbound_reason: 'not_fetched' },
  in_transit: making + shipping + ordered, in_transit_excludes: ['fba_inbound'],
  unfilled_purchase: [],
});

describe('库存改从 forecast* 取（spec §4.6）', () => {
  it('stockAt 读店铺行的期末；不适用是 na，不是 0 也不是未知', () => {
    const m = buildGridModel(makeGrid(), sellers);
    expect(stockAt(m.blocks[0]!, P[0]!)).toEqual({ kind: 'num', value: 200 });
    expect(stockAt(m.blocks[0]!, P[1]!)).toEqual({ kind: 'unknown' });
    expect(stockAt(m.blocks[1]!, P[0]!)).toEqual({ kind: 'na' });
  });

  it('msku 行有自己的期末', () => {
    const g = makeGrid();
    g.forecast = [fm('MSKU-A', '11072', P[0]!, 150), fm('MSKU-B', '11072', P[0]!, 50)];
    const m = buildGridModel(g, sellers);
    expect(mskuStockAt(m.blocks[0]!.mskus[0]!, P[0]!)).toEqual({ kind: 'num', value: 150 });
  });

  it('★ 店铺期末 ≠ Σ msku 期末 → 点名 forecast_sum_disagrees（两个真相）', () => {
    const g = makeGrid();
    g.forecast = [fm('MSKU-A', '11072', P[0]!, 150), fm('MSKU-B', '11072', P[0]!, 49)];
    expect(buildGridModel(g, sellers).orphans).toContainEqual(
      { kind: 'forecast_sum_disagrees', key: 'SKU-1/11072/2026-10' });
  });

  it('★ 期初[m+1] ≠ 期末[m] → chain_broken', () => {
    const g = makeGrid();
    g.forecast_store = [st('11072', P[0]!, 420, 200), st('11072', P[1]!, 199, 150),
                        st('11072', P[2]!, 150, 100), ...g.forecast_store.filter((r) => r.sid === '90001')];
    expect(buildGridModel(g, sellers).orphans).toContainEqual(
      { kind: 'chain_broken', key: 'SKU-1/11072/2026-11' });
  });

  it('在途读 forecast_sku.in_transit；组成对不上 → in_transit_disagrees', () => {
    const g = makeGrid();
    g.forecast_sku = [fk(P[0]!, 200, 0, 50), { ...fk(P[1]!, 0), in_transit: 7 }, fk(P[2]!, 0)];
    const m = buildGridModel(g, sellers);
    expect(m.transit[0]!.cells.map((c) => c.units)).toEqual([250, 7, 0]);
    expect(m.orphans).toContainEqual({ kind: 'in_transit_disagrees', key: 'SKU-1/2026-11' });
  });
});
```

`web/src/pages/PlanGrid.test.tsx`：同样把 `i()` 构造器换成 `forecast_store` 行（数字不变：`420→200`、`200→-30`、`-30→null(unknown_demand)`、90001 三格不适用），`sku_pipeline` 的 80 换成 `forecast_sku` 10 月 `in_transit: 80`；测试内 `buildMockApi.putDemand` 里重算的那段改成重算 `state.grid.forecast_store`（`closing = opening − Σ该店该货号 effective_units`，任一 null → `closing = null, state 'unknown'`）—— 于是「420 − (300 + 40) = 80」「Delete 清掉 → —」两条用例原样成立。

`web/src/api/mock.test.ts` 追加：

```ts
it('★ OP-17：mock 不重跑台账 —— 改了期望销量，该货号的预测库存变「未知」，不留旧数', async () => {
  const api = createMockApi();
  await api.putDemand(1, 'MSKU-A', '11072', '2026-10', 999);
  const g = await api.getGrid(1);
  const sku = g.demand.find((d) => d.seller_sku === 'MSKU-A')!.sku;
  for (const key of ['forecast', 'forecast_store', 'forecast_sku'] as const) {
    const rows = (g[key] as { sku: string; state: string; reason: string | null; closing: number | null }[])
      .filter((r) => r.sku === sku);
    expect(rows.length).toBeGreaterThan(0);
    expect(rows.every((r) => r.state === 'unknown' && r.reason === 'not_recomputed_in_mock' && r.closing === null)).toBe(true);
  }
  const other = g.forecast_store.filter((r) => r.sku !== sku);
  expect(other.some((r) => r.reason !== 'not_recomputed_in_mock')).toBe(true);
});
```

- [ ] **Step 2: 跑，确认失败**

Run: `cd web && npx vitest run src/pages/planGridModel.test.ts src/pages/PlanGrid.test.tsx src/api/mock.test.ts`
Expected: FAIL —— `stockAt is not exported` / 类型错误 / mock 用例 `state` 仍是 `ok`

- [ ] **Step 3: 实现**

`types.ts`：照 Interfaces 加类型；`DemandCell` 加 `system_basis: SystemBasis | null`；`GridResponse` 加六个键，`inventory` / `sku_pipeline` 改可选。

`planGridModel.ts` 关键改动：

```ts
export interface MskuRow { seller_sku: string; sid: Sid; cells: MskuCell[]; forecast: MskuForecast[] }
export interface SkuBlock {
  sid: Sid; seller_name: string; market: string; has_fba: boolean; sku: string;
  mskus: MskuRow[]; stock: StoreForecast[];
}
export interface SkuTotals { sku: string; cells: SkuForecast[] }
export interface GridModel {
  periods: Period[]; blocks: SkuBlock[]; purchase: PurchaseRow[]; transit: TransitRow[];
  totals: SkuTotals[]; params: ForecastParams; beyond: BeyondHorizon[]; unallocated: Unallocated[];
  orphans: Orphan[];
}

/** ★ 不适用 ≠ 未知 ≠ 0（02 §3.1a · 14 B5） */
export function qtyOf(state: ForecastState, v: number | null): QtyValue {
  if (state === 'not_applicable') return { kind: 'na' };
  return v === null ? { kind: 'unknown' } : { kind: 'num', value: v };
}
export function stockAt(block: SkuBlock, period: Period): QtyValue {
  const r = block.stock.find((s) => s.period === period);
  return r ? qtyOf(r.state, r.closing) : { kind: 'unknown' };
}
export function mskuStockAt(row: MskuRow, period: Period): QtyValue {
  const r = row.forecast.find((f) => f.period === period);
  return r ? qtyOf(r.state, r.closing) : { kind: 'unknown' };
}
export function systemAt(block: SkuBlock, period: Period): QtyValue {
  const r = block.stock.find((s) => s.period === period);
  return r?.system == null ? { kind: 'unknown' } : { kind: 'num', value: r.system };
}
export function totalsAt(model: GridModel, sku: string, period: Period): SkuForecast | undefined {
  return model.totals.find((t) => t.sku === sku)?.cells.find((c) => c.period === period);
}
```

`buildGridModel` 里：建 `MskuRow` 时带上 `forecast: grid.forecast.filter((f) => f.seller_sku === d.seller_sku && f.sid === d.sid)`；块的库存改从 `grid.forecast_store` 取（原来 `invByKey` 那一套换成 `stockByKey`，缺行仍报 `demand_without_inventory`，多出来的仍报 `inventory_without_demand`）；`na_disagrees_with_seller` 判 `(row.state === 'not_applicable') !== !block.has_fba`；`demand_disagrees` 比 `demandAt(block, p)` 与 `row.demand`；新增：

```ts
      // ★ spec §4.6：店铺 = Σ msku。后端算了一遍，这里再加一遍互为证人 —— 对不上就是两个真相
      if (row.state !== 'not_applicable') {
        const sum = sumUnits(block.mskus.map((r) => r.forecast.find((f) => f.period === period)?.closing ?? null));
        const theirs = row.closing === null ? { kind: 'unknown' } : { kind: 'num', value: row.closing };
        if (JSON.stringify(sum) !== JSON.stringify(theirs)) {
          orphans.push({ kind: 'forecast_sum_disagrees', key: k });
        }
      }
```

（`block.mskus[*].forecast` 为空 —— 旧构造器没给 msku 行 —— 时跳过这条核对：`if (block.mskus.every((r) => r.forecast.length === 0)) skip`，否则老用例全会报它。）`chain_broken` 改比 `block.stock` 相邻两月的 `closing` / `opening`。在途：

```ts
  const totals: SkuTotals[] = [...new Set(grid.forecast_sku.map((r) => r.sku))].map((sku) => ({
    sku, cells: grid.periods.map((p) => grid.forecast_sku.find((r) => r.sku === sku && r.period === p)!).filter(Boolean),
  }));
  const transit: TransitRow[] = totals.map((t) => ({
    sku: t.sku,
    cells: grid.periods.map((period) => {
      const c = t.cells.find((x) => x.period === period);
      if (c && c.in_transit !== c.pipeline.making + c.pipeline.shipping + c.pipeline.ordered_open) {
        orphans.push({ kind: 'in_transit_disagrees', key: `${t.sku}/${period}` });
      }
      return { period, units: c ? c.in_transit : null };
    }),
  }));
```

`outageCount` 与 `closingOfLast` 改用 `block.stock` / `stockAt`。删掉读 `inventory` / `sku_pipeline` 的全部代码（`GridModel.pipeline` 字段一起删）。

`planSheetModel.ts` / `PlanSheet.tsx`：`inventoryAt` → `stockAt`；`stockLine` / `stockTitle` 里 `block.inventory.find(...)` 改成 `block.stock.find(...)`，`closing_reason === 'unknown_onhand'` → `reason === 'unknown_onhand'`，`'unknown_demand'` 同理；`stockTitle` 的文案改成 `` `期初 ${inv.opening ?? '—'} · 数据截至 ${model.params.as_of}` ``（「不含本计划采购」这句删掉 —— 现在含了）。

`mock.ts`：加

```ts
  /** ★ OP-17：mock 不重跑逐日台账（那是后端 forecast/ 的活，照抄一份迟早分叉）。
   *  改了之后该货号的预测库存如实变「未知」，不留一个已经过时的数。 */
  function staleForecast(g: GridResponse, sku: string): void {
    for (const r of [...g.forecast, ...g.forecast_store, ...g.forecast_sku]) {
      if (r.sku !== sku) continue;
      Object.assign(r, { state: 'unknown', reason: 'not_recomputed_in_mock', opening: null, closing: null });
    }
  }
```

在 `putDemand`（取该 msku 的 `sku`）与 `putPurchase` 成功路径的末尾调 `staleForecast(g, sku)`。`createPlan` 新建空网格时补上 `params`（取 grid-1.json 的 `params`）与五个空数组。

- [ ] **Step 4: 跑，确认通过 + 类型**

Run: `cd web && npx vitest run && npx tsc -b`
Expected: 全绿（真 fixture 那条 `buildGridModel(realGrid, realSellers).orphans` 为空的用例也必须绿 —— 后端 Σ 与前端 Σ 一致）

- [ ] **Step 5: 提交**

```bash
git add web/src/api/types.ts web/src/pages/planGridModel.ts web/src/pages/planSheetModel.ts web/src/pages/PlanSheet.tsx web/src/api/mock.ts web/src/pages/planGridModel.test.ts web/src/pages/PlanGrid.test.tsx web/src/api/mock.test.ts
git commit -m "feat(web): 网格模型改从预测块取库存与在途（Σmsku 互证、链断点名）；mock 改后如实变未知"
```

---

### Task 18: 三种行的标签（spec §5）

spec §5（货号行：采购 + 预测 · 库存 · 在途；店铺行：期望 Σ + 预测 · 库存；msku 行：期望 + 预测 · 库存；文字标签不只靠颜色）· OP-14

**Files:**
- Modify: `web/src/pages/PlanSheet.tsx`（`skuRow` 的 `sub`、`storeRow` 的 `stockLine`、`mskuRow` 的 `sub`；货号行表头副标题）
- Test: `web/src/pages/PlanSheet.labels.test.tsx`（新建，自带一张小网格，直接渲染 `PlanSheet`）

**Interfaces:**
- Consumes: Task 17 `stockAt, mskuStockAt, systemAt, totalsAt, sumUnits`
- Produces（testid，Task 19 复用）：`sku-system-{sku}-{p}` · `sku-stock-{sku}-{p}` · `transit-{sku}-{p}`（既有）· `store-system-{sid}-{sku}-{p}` · `closing-sku`（既有，店铺行库存数）· `msku-system-{seller_sku}-{sid}-{p}` · `msku-stock-{seller_sku}-{sid}-{p}`

测试网格 `LABEL_GRID`（本任务与 Task 19 共用，放在 `web/src/pages/__fixtures__/labelGrid.ts`）：as_of 2026-09-21；货号 `SKU-1`；店铺 11072（MSKU-A、MSKU-B，多 msku → 店铺行只读）与 90001（MSKU-W，无 FBA）；月份 2026-10、2026-11。

| | 10 月 | 11 月 |
|---|---|---|
| MSKU-A 预测 / 期望 / 期初 / 到货 / 期末 | 135 / 100(人填) / 264 / 0 / 164 | 130 / 120 / 164 / 90 / 134 |
| MSKU-B 预测 / 期望 / 期初 / 到货 / 期末 | 10 / 5 / 10 / 0 / 5 | 10 / 5 / 5 / 0 / 0 |
| 店铺 11072：期望 Σ / 预测 Σ / 期初 / 到货 / 期末 | 105 / 145 / 274 / 0 / 169 | 125 / 140 / 169 / 90 / 134 |
| 店铺 90001：期望 / 预测 | 60 / null（不适用） | 65 / null |
| 货号：预测 Σ（含 null → 未知）/ 期末 / 生产中 / 货运中 / 已下单 / 在途 | — / 169 / 200 / 0 / 50 / 250 | — / 134 / 0 / 0 / 50 / 50 |

MSKU-A 10 月 `basis.bridge = {from: '2026-09-22', to: '2026-09-30', units: 36, by: 'forecast', rate: 4.0, month_totals: {'2026-09': 120}, onhand_as_of: 300}`；11 月 `basis.arrivals = [{date: '2026-11-14', units: 90, order_period: '2026-10', rule: '缺口 0 补齐，余量按期望销量再摊 90', gap_part: 0, weight_part: 90, pool: 200}]`；MSKU-A 10 月 demand 格 `system_basis = {method: 'baseline_rate_v1', base_rate: 4.325139122267326, days_in_month: 31, window: {from: '2026-07-21', until: '2026-09-14'}, embargo_days: 7, censored_days: 0, …}`。

- [ ] **Step 1: 写失败的测试**

`web/src/pages/__fixtures__/labelGrid.ts` 按上表写出一个完整的 `GridResponse`（`demand` 6 格、`purchase` 2 格、`forecast` 6 行、`forecast_store` 4 行、`forecast_sku` 2 行、`params`、`beyond_horizon: [{sku: 'SKU-1', order_period: '2026-11', units: 80, placed: '2026-11-15', made: '2026-12-15', arrive: '2027-02-13', reason: 'arrives_after_horizon'}]`、`unallocated: []`）并导出 `LABEL_SELLERS`：`{seller_id: '11072', name: 'A4Pet-US', market: 'US', has_fba: true, platform: 'amazon'}` 与 `{seller_id: '90001', name: 'A4Pet-WM', market: 'US', has_fba: false, platform: 'walmart'}`。

`web/src/pages/PlanSheet.labels.test.tsx`：

```tsx
import { render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { PlanSheet } from './PlanSheet';
import { buildGridModel } from './planGridModel';
import { LABEL_GRID, LABEL_SELLERS } from './__fixtures__/labelGrid';

function renderSheet() {
  const model = buildGridModel(LABEL_GRID, LABEL_SELLERS);
  expect(model.orphans).toEqual([]);            // ★ 构造的网格自身必须 Σ 一致
  render(<PlanSheet model={model} pending={new Set()} inputErr={{}} onDemand={vi.fn()}
                    onPurchase={vi.fn()} onRemoveStore={vi.fn()} onRemoveMsku={vi.fn()} />);
}

describe('spec §5：三种行各显示什么', () => {
  it('货号行：主数采购，副数 预测 · 库存 · 在途（带文字标签）', () => {
    renderSheet();
    const row = screen.getByTestId('purchase-row-SKU-1');
    expect(within(row).getByTestId('sku-system-SKU-1-2026-10')).toHaveTextContent('—');
    expect(within(row).getByTestId('sku-stock-SKU-1-2026-10')).toHaveTextContent('169');
    expect(within(row).getByTestId('transit-SKU-1-2026-10')).toHaveTextContent('250');
    const cell = within(row).getByTestId('sku-stock-SKU-1-2026-10').closest('.ps-c')!;
    expect(cell).toHaveTextContent(/预测.*库存.*在途/);
  });

  it('店铺行：期望 Σ + 预测 · 库存', () => {
    renderSheet();
    const block = screen.getByTestId('block-11072-SKU-1');
    const cell = within(block).getByTestId('sku-cell-2026-10');
    expect(within(cell).getByTestId('sum-expected-2026-10')).toHaveTextContent('105');
    expect(within(cell).getByTestId('store-system-11072-SKU-1-2026-10')).toHaveTextContent('145');
    expect(within(cell).getByTestId('closing-sku')).toHaveTextContent('169');
    expect(cell).toHaveTextContent(/预测.*库存/);
  });

  it('msku 行：期望 + 预测 · 库存', () => {
    renderSheet();
    expect(screen.getByTestId('msku-system-MSKU-A-11072-2026-10')).toHaveTextContent('135');
    expect(screen.getByTestId('msku-stock-MSKU-A-11072-2026-10')).toHaveTextContent('164');
    expect(screen.getByTestId('msku-stock-MSKU-B-11072-2026-11')).toHaveTextContent('0');
  });

  it('★ 无 FBA 店铺：库存写「无 FBA」，不是 0 也不是 —', () => {
    renderSheet();
    const block = screen.getByTestId('block-90001-SKU-1');
    expect(within(block).getAllByTestId('closing-na')[0]).toHaveTextContent('无 FBA');
  });

  it('OP-14：在途旁写明不含 FBA 货件（未取）', () => {
    renderSheet();
    expect(screen.getByTestId('purchase-row-SKU-1')).toHaveTextContent('不含 FBA 货件');
  });
});
```

（`block-{sid}-{sku}` 若不是现有 testid，用 `PlanGrid.test.tsx` 里已在用的块定位方式。）

- [ ] **Step 2: 跑，确认失败**

Run: `cd web && npx vitest run src/pages/PlanSheet.labels.test.tsx`
Expected: FAIL —— `Unable to find an element by: [data-testid="sku-system-SKU-1-2026-10"]`

- [ ] **Step 3: 实现**

`PlanSheet.tsx`：

- 货号行 `sub(p)` 改成：
  ```tsx
      const t = totalsAt(model, card.sku, p);
      return (
        <span className="ps-d">
          <span>预测 <b data-testid={`sku-system-${card.sku}-${p}`}>{fmt(t?.system ?? null)}</b></span>
          <span>库存 <b data-testid={`sku-stock-${card.sku}-${p}`}>{t && t.state !== 'not_applicable' ? fmt(t.closing) : '—'}</b></span>
          <span title="不含 FBA 货件（未取）">在途 <b data-testid={`transit-${card.sku}-${p}`}>{fmt(transitAt(p))}</b></span>
        </span>
      );
  ```
  货号行表头副标题 `计划采购／各店销量／在途` → `采购／预测·库存·在途`；在货号行表头下加一行 `<span className="ps-sub">在途不含 FBA 货件（未取）</span>`。
- 店铺行 `stockLine(inv, block, p)`：把 `word`（断货 / 偏低 / 库存）拆成「固定标签 `库存` + 状态词」：
  ```tsx
      <span className="ps-d" title={why}>
        <span>预测 <b data-testid={`store-system-${block.sid}-${block.sku}-${p}`}><Qty v={systemAt(block, p)} /></b></span>
        <span>库存 <b data-testid="closing-sku">{v.kind === 'num' ? fmt(v.value) : '—'}</b>{s !== 'ok' && <em className="ps-flag">{s === 'out' ? '断货' : '偏低'}</em>}</span>
      </span>
  ```
  （`s` 取 `stockState` 的返回；`stockState` 的「ok」值名以 `planSheetModel.ts` 为准。）
- msku 行：`sub: null` 改成
  ```tsx
            sub: (
              <span className="ps-d">
                <span>预测 <b data-testid={`msku-system-${r.seller_sku}-${r.sid}-${p}`}>{fmt(c?.system_units ?? null)}</b></span>
                <span>库存 <b data-testid={`msku-stock-${r.seller_sku}-${r.sid}-${p}`}><Qty v={mskuStockAt(r, p)} /></b></span>
              </span>
            ),
  ```
- 单 msku 的店铺行（`single && row.nav !== undefined` 那支）`sub` 同店铺行。

- [ ] **Step 4: 跑，确认通过 + 全量前端**

Run: `cd web && npx vitest run && npx tsc -b`
Expected: 全绿。既有用例里断言「销 N」「途 N」旧文案的，按新文案改（理由：spec §5 负责人 09-24 裁定的标签）。

- [ ] **Step 5: 提交**

```bash
git add web/src/pages/PlanSheet.tsx web/src/pages/PlanSheet.labels.test.tsx web/src/pages/__fixtures__/labelGrid.ts web/src/pages/PlanGrid.test.tsx
git commit -m "feat(web): 计划整表三种行按 spec §5 显示 预测·库存·在途 标签（文字标签，不只靠颜色）"
```

---

### Task 19: 点开一格看依据（期初 → 期末的链 + 预测的算式）

spec §5（点开看依据的样例）· §3.3（「预测 124 = 4.13 件/天 × 30 天」）· `14` §6（结论回显依据）· CLAUDE.md（说明进折叠区）

**Files:**
- Create: `web/src/pages/ForecastBasis.tsx`
- Modify: `web/src/pages/PlanSheet.tsx`（三种行的「库存」数字包成按钮，点开 `Modal` 里渲染 `ForecastBasis`）
- Test: `web/src/pages/ForecastBasis.test.tsx`（新建，用 Task 18 的 `LABEL_GRID`）

**Interfaces:**
- Consumes: `web/src/components/Modal.tsx`（既有）· Task 17 模型 · Task 18 的 `LABEL_GRID`
- Produces:
  - `ForecastBasis(props: { model: GridModel; target: BasisTarget })`，`type BasisTarget = { kind: 'msku'; block: SkuBlock; row: MskuRow; period: Period } | { kind: 'store'; block: SkuBlock; period: Period } | { kind: 'sku'; sku: string; period: Period }`
  - 按钮可访问名：`依据 {seller_sku} {p}` · `依据 {seller_name} {sku} {p}` · `依据 {sku} {p}`

- [ ] **Step 1: 写失败的测试**

`web/src/pages/ForecastBasis.test.tsx`：

```tsx
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { PlanSheet } from './PlanSheet';
import { buildGridModel } from './planGridModel';
import { LABEL_GRID, LABEL_SELLERS } from './__fixtures__/labelGrid';

function renderSheet() {
  render(<PlanSheet model={buildGridModel(LABEL_GRID, LABEL_SELLERS)} pending={new Set()}
                    inputErr={{}} onDemand={vi.fn()} onPurchase={vi.fn()}
                    onRemoveStore={vi.fn()} onRemoveMsku={vi.fn()} />);
}

describe('spec §5：点开一格看依据', () => {
  it('msku 首月：期初来自 as_of 可售 − 桥接段按预测扣，再 − 期望 + 到货 = 期末 → 下月期初', async () => {
    renderSheet();
    await userEvent.click(screen.getByRole('button', { name: '依据 MSKU-A 2026-10' }));
    const d = screen.getByRole('dialog');
    expect(d).toHaveTextContent('库存 · A4Pet-US · MSKU-A · 2026-10');
    expect(d).toHaveTextContent('期初 264');
    expect(d).toHaveTextContent('as_of 2026-09-21 可售 300 − 09-22–09-30 按预测扣 36');
    expect(d).toHaveTextContent('− 期望 100');
    expect(d).toHaveTextContent('+ 到货 0');
    expect(d).toHaveTextContent('= 期末 164');
    expect(d).toHaveTextContent('→ 2026-11 期初');
    expect(d).toHaveTextContent('预测 135 = 4.33 件/天 × 31 天（窗口 07-21 ~ 09-14，禁言 7 天，剔断货 0 天）');
  });

  it('到货逐笔说清怎么分的（14 §3.2 依据必须说实话）', async () => {
    renderSheet();
    await userEvent.click(screen.getByRole('button', { name: '依据 MSKU-A 2026-11' }));
    const d = screen.getByRole('dialog');
    expect(d).toHaveTextContent('期初 164（= 2026-10 期末）');
    expect(d).toHaveTextContent('+ 到货 90');
    expect(d).toHaveTextContent('11-14 到货 90（2026-10 下单 · 缺口 0 补齐，余量按期望销量再摊 90 · 这批 200）');
  });

  it('货号行：在途拆成 生产中 / 货运中 / 已下单未到，FBA 货件写「未取」；周期外逐笔列出', async () => {
    renderSheet();
    await userEvent.click(screen.getByRole('button', { name: '依据 SKU-1 2026-10' }));
    const d = screen.getByRole('dialog');
    expect(d).toHaveTextContent('生产中 200 · 货运中 0 · 已下单未到 50 · FBA 货件 未取');
    expect(d).toHaveTextContent('2026-11 下单 80 件 → 2027-02-13 到货（周期外）');
  });

  it('★ 说明进折叠区，默认收起', async () => {
    renderSheet();
    await userEvent.click(screen.getByRole('button', { name: '依据 MSKU-A 2026-10' }));
    const details = within(screen.getByRole('dialog')).getByText('怎么算的').closest('details')!;
    expect(details).not.toHaveAttribute('open');
  });

  it('未知给原因，不给数', async () => {
    const g = structuredClone(LABEL_GRID);
    const r = g.forecast.find((f) => f.seller_sku === 'MSKU-B' && f.period === '2026-11')!;
    Object.assign(r, { state: 'unknown', reason: 'unknown_demand', unknown_since: '2026-11', closing: null });
    render(<PlanSheet model={buildGridModel(g, LABEL_SELLERS)} pending={new Set()} inputErr={{}}
                      onDemand={vi.fn()} onPurchase={vi.fn()} onRemoveStore={vi.fn()} onRemoveMsku={vi.fn()} />);
    await userEvent.click(screen.getByRole('button', { name: '依据 MSKU-B 2026-11' }));
    expect(screen.getByRole('dialog')).toHaveTextContent('未知：2026-11 起期望销量待填');
  });
});
```

- [ ] **Step 2: 跑，确认失败**

Run: `cd web && npx vitest run src/pages/ForecastBasis.test.tsx`
Expected: FAIL —— `Unable to find role="button" and name "依据 MSKU-A 2026-10"`

- [ ] **Step 3: 实现**

`web/src/pages/ForecastBasis.tsx`（要点；文案逐字照测试）：

```tsx
import type { ReactNode } from 'react';
import type { ForecastReason, MskuForecast, Period } from '../api/types';
import type { GridModel, MskuRow, SkuBlock } from './planGridModel';

export type BasisTarget =
  | { kind: 'msku'; block: SkuBlock; row: MskuRow; period: Period }
  | { kind: 'store'; block: SkuBlock; period: Period }
  | { kind: 'sku'; sku: string; period: Period };

const md = (iso: string) => iso.slice(5);                       // "2026-09-22" → "09-22"
const WHY: Record<Exclude<ForecastReason, null>, string> = {
  as_of_inside_month: 'as_of 在本月中，从次日起扣', unknown_onhand: '没有在仓数',
  unknown_demand: '期望销量待填', bridge_rate_unknown: '没有预测速率，as_of 到首月之间扣不了',
  inbound_unknown: '到货分不出来（有店缺口未知）', not_applicable: '无 FBA',
  elapsed: '月份已过去', not_recomputed_in_mock: 'mock 不重算',
};

function openingLine(f: MskuForecast, asOf: string, prev: Period | undefined): string {
  const b = f.basis.bridge;
  if (f.state === 'partial') return '期初 —（as_of 在本月中，从次日起扣）';
  if (b) return `期初 ${f.opening}（as_of ${asOf} 可售 ${b.onhand_as_of} − ${md(b.from)}–${md(b.to)} 按预测扣 ${b.units}）`;
  if (prev) return `期初 ${f.opening}（= ${prev} 期末）`;
  return `期初 ${f.opening}（= as_of ${asOf} 可售）`;
}

function Chain({ f, periods, asOf }: { f: MskuForecast; periods: Period[]; asOf: string }): ReactNode {
  if (f.closing === null) {
    return <p>未知：{f.unknown_since ? `${f.unknown_since} 起` : ''}{f.reason ? WHY[f.reason] : ''}</p>;
  }
  const i = periods.indexOf(f.period);
  const nxt = periods[i + 1];
  return (
    <div className="fb-chain">
      <div>{openingLine(f, asOf, i > 0 ? periods[i - 1] : undefined)}</div>
      <div>− 期望 {f.demand}</div>
      <div>+ 到货 {f.inbound_planned}</div>
      {f.basis.arrivals.map((a) => (
        <div key={a.date + a.order_period} className="fb-arr">
          {md(a.date)} 到货 {a.units ?? '—'}（{a.order_period} 下单 · {a.rule ?? '分不出来'} · 这批 {a.pool}）
        </div>
      ))}
      <div>= 期末 {f.closing}{nxt ? `   → ${nxt} 期初` : ''}</div>
      {f.stockout_date && <div>预计断货 {f.stockout_date}</div>}
    </div>
  );
}
```

`ForecastBasis` 主体：
- `msku`：标题 `库存 · {block.seller_name} · {row.seller_sku} · {period}`；`<Chain f={该月 msku 预测行} periods={model.periods} asOf={model.params.as_of} />`；预测行取该 msku 该月 demand 格的 `system_basis`：`` `预测 ${system_units} = ${base_rate.toFixed(2)} 件/天 × ${days_in_month} 天（窗口 ${md(window.from)} ~ ${md(window.until)}，禁言 ${embargo_days} 天，剔断货 ${censored_days} 天）` ``；`system_basis` 为 null 或 `base_rate` 为 null → `预测 —（{reason === 'no_sales_in_window' ? '窗口内没有销量' : '没有预测'}）`。
- `store`：标题 `库存 · {seller_name} · {sku} · {period}`；`期初 {opening} − 期望 {Σ} + 到货 {inbound} = 期末 {closing}`；下面逐个 msku 一行 `{seller_sku}：期末 {closing}`。
- `sku`：标题 `货号 · {sku} · {period}`；`生产中 {making} · 货运中 {shipping} · 已下单未到 {ordered_open} · FBA 货件 未取`；`ordered_open_sources` 逐笔 `{ref} {units}`；`model.beyond` 里该货号的逐笔 `{order_period} 下单 {units} 件 → {arrive} 到货（周期外 | 早于 as_of）`；`model.unallocated` 里该货号的逐笔 `{arrive} 到货 {units} 件没分出去（{reason === 'gap_unknown' ? '有店缺口未知' : '没有 FBA 店'}）`；`unfilled_purchase` 非空 → `采购未填：{月份列表}`。
- 三种都在末尾放 `<details><summary>怎么算的</summary>…spec §4 的三句公式…</details>`（不加 `open`）。

`PlanSheet.tsx`：三处「库存」`<b>` 外面包 `<button type="button" className="ps-basis" aria-label={...} onClick={() => setBasis(target)}>`（`e.stopPropagation()`，别触发格子选中）；组件内 `const [basis, setBasis] = useState<BasisTarget | null>(null)`，`{basis && <Modal title="依据" onClose={() => setBasis(null)}><ForecastBasis model={model} target={basis} /></Modal>}`（`Modal` 的 props 以 `components/Modal.tsx` 为准）。

- [ ] **Step 4: 跑，确认通过 + 全量前端**

Run: `cd web && npx vitest run && npx tsc -b`
Expected: 全绿

- [ ] **Step 5: 提交**

```bash
git add web/src/pages/ForecastBasis.tsx web/src/pages/ForecastBasis.test.tsx web/src/pages/PlanSheet.tsx
git commit -m "feat(web): 点开库存看依据 —— 期初→期望→到货→期末的链、到货怎么分、预测算式；说明默认收起"
```

---

### Task 20: 提前期设置（API 客户端 + mock + 页面）

spec §4.4（用户变量、界面可改、改完立即重算）· `14` B12 · OP-5 · OP-17

**Files:**
- Modify: `web/src/api/types.ts`（`LeadTime` · `LeadTimeInput` · `LeadTimeResult`）· `web/src/api/client.ts`（接口加两个方法）· `web/src/api/http.ts` · `web/src/api/mock.ts`
- Create: `web/src/pages/LeadTimeBar.tsx`
- Modify: `web/src/pages/PlanGrid.tsx`（标题行放 `LeadTimeBar`，改完调 `load()`）
- Test: `web/src/api/http.test.ts` · `web/src/api/mock.test.ts` · `web/src/pages/LeadTimeBar.test.tsx`（新建）

**Interfaces:**
- Consumes: 后端 `GET/PUT /v1/lead-time`（Task 8）
- Produces:
  ```ts
  export interface LeadTime { route: 'oversea_sea'; make_days: number; ship_days: number;
                              anchor_day: number; valid_from: { making: string; shipping: string } }
  export interface LeadTimeInput { make_days?: number; ship_days?: number }
  export interface LeadTimeResult extends LeadTime { changed: { segment: 'making' | 'shipping'; from: number; to: number }[] }
  // SupplyChainApi 加：getLeadTime(): Promise<LeadTime>; putLeadTime(input: LeadTimeInput): Promise<LeadTimeResult>;
  ```
  - `LeadTimeBar(props: { onChanged: () => Promise<void> })`

- [ ] **Step 1: 写失败的测试**

`web/src/api/http.test.ts` 追加（照该文件既有的 fetch 桩写法）：`putLeadTime({ make_days: 10 })` 发 `PUT /v1/lead-time`、body `{"make_days":10}`、带 `x-actor` 头；`getLeadTime()` 发 `GET /v1/lead-time`。

`web/src/api/mock.test.ts` 追加：

```ts
it('mock 提前期：可读可改；改了之后所有预测库存如实变「未知」（OP-17）', async () => {
  const api = createMockApi();
  expect(await api.getLeadTime()).toMatchObject({ make_days: 30, ship_days: 60, anchor_day: 15 });
  const r = await api.putLeadTime({ make_days: 10 });
  expect(r.changed).toEqual([{ segment: 'making', from: 30, to: 10 }]);
  const g = await api.getGrid(1);
  expect(g.params.make_days).toBe(10);
  expect(g.forecast_store.every((x) => x.reason === 'not_recomputed_in_mock' || x.state === 'not_applicable')).toBe(true);
});
```

`web/src/pages/LeadTimeBar.test.tsx`：

```tsx
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api');
  return { ...actual, api: { getLeadTime: vi.fn(async () => ({ route: 'oversea_sea', make_days: 30, ship_days: 60, anchor_day: 15, valid_from: { making: '2026-01-01', shipping: '2026-01-01' } })),
                             putLeadTime: vi.fn(async (i: object) => ({ route: 'oversea_sea', make_days: 10, ship_days: 20, anchor_day: 15, valid_from: { making: '2026-09-24', shipping: '2026-09-24' }, changed: [], ...i })) } };
});
import { api } from '../api';
import { LeadTimeBar } from './LeadTimeBar';

describe('提前期设置（spec §4.4）', () => {
  it('显示当前值', async () => {
    render(<LeadTimeBar onChanged={vi.fn(async () => {})} />);
    expect(await screen.findByText('生产 30 天 · 货运 60 天 · 下单月 15 号')).toBeInTheDocument();
  });

  it('改完立即重算（14 B12）：PUT 之后调 onChanged 重拉网格', async () => {
    const onChanged = vi.fn(async () => {});
    render(<LeadTimeBar onChanged={onChanged} />);
    await userEvent.click(await screen.findByRole('button', { name: '改提前期' }));
    await userEvent.clear(screen.getByLabelText('生产天数'));
    await userEvent.type(screen.getByLabelText('生产天数'), '10');
    await userEvent.clear(screen.getByLabelText('货运天数'));
    await userEvent.type(screen.getByLabelText('货运天数'), '20');
    await userEvent.click(screen.getByRole('button', { name: '保存提前期' }));
    expect(api.putLeadTime).toHaveBeenCalledWith({ make_days: 10, ship_days: 20 });
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
  });

  it.each([['-1', '不是非负整数'], ['3.5', '不是非负整数'], ['721', '不能超过 720 天']])(
    '输入 %s 就地报错、不发请求', async (raw, why) => {
      render(<LeadTimeBar onChanged={vi.fn(async () => {})} />);
      await userEvent.click(await screen.findByRole('button', { name: '改提前期' }));
      await userEvent.clear(screen.getByLabelText('生产天数'));
      await userEvent.type(screen.getByLabelText('生产天数'), raw);
      await userEvent.click(screen.getByRole('button', { name: '保存提前期' }));
      expect(screen.getByRole('alert')).toHaveTextContent(why);
      expect(api.putLeadTime).not.toHaveBeenCalledWith(expect.objectContaining({ make_days: Number(raw) }));
    });
});
```

- [ ] **Step 2: 跑，确认失败**

Run: `cd web && npx vitest run src/api/http.test.ts src/api/mock.test.ts src/pages/LeadTimeBar.test.tsx`
Expected: FAIL —— `api.getLeadTime is not a function` / `Cannot find module './LeadTimeBar'`

- [ ] **Step 3: 实现**

- `client.ts`：`SupplyChainApi` 加两个方法签名。
- `http.ts`：`getLeadTime: () => get<LeadTime>('/v1/lead-time')`；`putLeadTime: (input) => put<LeadTimeResult>('/v1/lead-time', input)`（用文件里既有的请求助手与 `x-actor` 注入方式）。
- `mock.ts`：模块内 `let lead: LeadTime = { route: 'oversea_sea', make_days: 30, ship_days: 60, anchor_day: 15, valid_from: { making: '2026-01-01', shipping: '2026-01-01' } };`；`putLeadTime` 算 `changed`（只列真变了的段）、更新 `lead`，对每张网格 `g.params = { ...g.params, make_days, ship_days }` 并对网格里每个货号调 Task 17 的 `staleForecast`；`getLeadTime` 返回 `clone(lead)`。
- `LeadTimeBar.tsx`：

```tsx
import { useEffect, useState } from 'react';
import { api, type ApiError } from '../api';
import type { LeadTime } from '../api/types';
import { parseUnits } from './planGridModel';

/** 03 §41 CHECK：0~720 天 */
const MAX_DAYS = 720;

export function LeadTimeBar({ onChanged }: { onChanged: () => Promise<void> }) {
  const [lt, setLt] = useState<LeadTime | null>(null);
  const [edit, setEdit] = useState<{ make: string; ship: string } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { void api.getLeadTime().then(setLt).catch((e: ApiError) => setErr(e.hint)); }, []);
  if (!lt) return err ? <span role="alert">{err}</span> : null;

  async function save() {
    const out: { make_days?: number; ship_days?: number } = {};
    for (const [label, raw, key] of [['生产', edit!.make, 'make_days'], ['货运', edit!.ship, 'ship_days']] as const) {
      const p = parseUnits(raw);
      if (!p.ok || p.value === null) { setErr(`${label}：${p.ok ? '必填' : p.why}`); return; }
      if (p.value > MAX_DAYS) { setErr(`${label}：不能超过 ${MAX_DAYS} 天`); return; }
      out[key] = p.value;
    }
    try {
      setLt(await api.putLeadTime(out));
      setEdit(null); setErr(null);
      await onChanged();                      // ★ 14 B12：改完立即重算并回显
    } catch (e) { setErr((e as ApiError).hint); }
  }

  return (
    <div className="lt-bar">
      {edit === null ? (
        <>
          <span>生产 {lt.make_days} 天 · 货运 {lt.ship_days} 天 · 下单月 {lt.anchor_day} 号</span>
          <button type="button" aria-label="改提前期" onClick={() => setEdit({ make: String(lt.make_days), ship: String(lt.ship_days) })}>改</button>
        </>
      ) : (
        <>
          <label>生产 <input aria-label="生产天数" value={edit.make} onChange={(e) => setEdit({ ...edit, make: e.target.value })} /> 天</label>
          <label>货运 <input aria-label="货运天数" value={edit.ship} onChange={(e) => setEdit({ ...edit, ship: e.target.value })} /> 天</label>
          <button type="button" aria-label="保存提前期" onClick={() => void save()}>保存</button>
          <button type="button" onClick={() => { setEdit(null); setErr(null); }}>取消</button>
        </>
      )}
      {err && <span role="alert">{err}</span>}
      <details><summary>这是什么</summary>全局配置：所有计划共用。下单月 15 号 + 生产天数 = 完工，再 + 货运天数 = 到货可售。</details>
    </div>
  );
}
```

- `PlanGrid.tsx`：在渲染 `PlanSheet` 的那块上方放 `<LeadTimeBar onChanged={load} />`。

- [ ] **Step 4: 跑，确认通过 + 全量前端**

Run: `cd web && npx vitest run && npx tsc -b`
Expected: 全绿

- [ ] **Step 5: 提交**

```bash
git add web/src/api/types.ts web/src/api/client.ts web/src/api/http.ts web/src/api/mock.ts web/src/pages/LeadTimeBar.tsx web/src/pages/LeadTimeBar.test.tsx web/src/pages/PlanGrid.tsx web/src/api/http.test.ts web/src/api/mock.test.ts
git commit -m "feat(web): 计划页可改提前期（生产/货运天数），保存即重拉网格重算"
```

---

### Task 21: 退役阶段 A 的旧库存（`inventory[]` · `sku_pipeline[]` · `projection.py`）

spec §6（`00e`:44「库存预估 = 在仓 − 期望销量」被取代）· OP-16 · spec §4.6（合计不许有第二个真相）

**Files:**
- Delete: `forecast/projection.py` · `tests/test_forecast_projection.py`
- Modify: `api/ui/plans.py`（`grid()` 删 `inventory` / `sku_pipeline` 两个键与只为它们存在的中间量：`demand_by_store` / `onhand_by_store` / `applicable_by_store` / `onhand_added` / `pipeline` / `is_overdue` 的调用；`source_notes` 保留）
- Modify: `tests/test_api_grid.py` · `tests/test_grid_fixture.py` · `tests/test_api_forecast_source.py`（凡断言 `inventory` / `sku_pipeline` 的，改断言 `forecast_store` / `forecast_sku` 的等价事实；逐条写理由）
- Modify: `tests/fixtures/grid_response.json` · `web/src/api/fixtures/grid-1.json`（重固化）
- Modify: `web/src/api/types.ts`（删 `InventoryCell` · `InventoryBasis` · `InboundReason` · `ClosingReason` · `SkuPipelineRow` 与两个可选键）· `web/src/api/mock.ts`（删 `recompute()` 及调用；`createPlan` 空网格不再带这两个键）· `web/src/api/mock.test.ts` · `web/src/pages/*.test.tsx`（删构造器里残留的 `inventory` / `sku_pipeline`）

**Interfaces:**
- Produces: grid 响应顶层键 = `plan_id, periods, demand, purchase, source_notes, params, forecast, forecast_store, forecast_sku, beyond_horizon, unallocated`

- [ ] **Step 1: 先写红的契约断言**

`tests/test_api_grid.py::test_grid_blocks_carry_exactly_the_ruled_keys`：删掉对 `inventory` / `sku_pipeline` 的断言，加

```python
    # ★ spec §6：阶段 A 的「在仓 − 期望」口径已被取代；两套库存数并存就是两个真相（spec §4.6）
    assert set(g) == {"plan_id", "periods", "demand", "purchase", "source_notes", "params",
                      "forecast", "forecast_store", "forecast_sku", "beyond_horizon",
                      "unallocated"}
```

Run: `.venv/bin/python -m pytest -q tests/test_api_grid.py::test_grid_blocks_carry_exactly_the_ruled_keys`
Expected: FAIL（多出 `inventory`, `sku_pipeline`）

- [ ] **Step 2: 删后端**

按 Files 删；`grid()` 里 `in_transit` 仍要算（喂 `forecast_payload` 的 `ordered_open`），`purchase_as_of()` 仍要调（它是 OQ-5 陈旧闸，删了闸就没了 —— 结果写进 `source_notes.purchase_as_of`）。其余测试里断言旧键的逐条改成新键上的同一件事（例：「不适用 ≠ 未知在仓」改断言 `forecast_store[*].state == 'not_applicable'` 与 `forecast[*].reason == 'unknown_onhand'`；「在途无店铺归属」改断言 `forecast_sku[*].pipeline.ordered_open`）。

Run: `.venv/bin/python -m pytest -q tests/test_api_grid.py tests/test_api_forecast_source.py tests/test_api_grid_forecast.py`
Expected: PASS（`test_grid_fixture.py` 待 Step 3）

- [ ] **Step 3: 重固化 + 删前端残留**

```bash
UPDATE_GRID_FIXTURE=1 .venv/bin/python -m pytest -q tests/test_grid_fixture.py
.venv/bin/python -m pytest -q tests/test_grid_fixture.py tests/test_layering_web.py
cp tests/fixtures/grid_response.json web/src/api/fixtures/grid-1.json
grep -rn "InventoryCell\|sku_pipeline\|SkuPipelineRow\|closing_reason\|sku_level_in_transit\|\.inventory\b" web/src
```

逐处删掉（types / mock / 测试构造器）。`test_grid_fixture.py::test_the_fixture_covers_the_shapes_that_look_alike` 里原本从 `inventory` 取的四种形态改从 `forecast_store` / `forecast` 取（不适用 · 未知在仓 · 期望未知 · 在途）。

- [ ] **Step 4: 全量**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check . && (cd web && npx vitest run && npx tsc -b)`
Expected: 全绿；`grep` 为空

- [ ] **Step 5: 提交**

```bash
git add -A api forecast tests web/src
git commit -m "refactor: 退役阶段 A「在仓−期望」库存口径（inventory/sku_pipeline/projection.py）—— 只剩一套库存数"
```

**Phase 3 收口 / 全部完成：** `.venv/bin/python -m pytest -q && .venv/bin/ruff check . && (cd web && npx vitest run && npx tsc -b)` 全绿。★ 不要自己起服务验证界面 —— 需要看页面时，把命令交给使用者：在 worktree 下 `cd web && npm run dev`（前端 mock 模式）或按 `README.md` 起后端，由使用者在自己的终端执行。

---

## Self-review 记录

- **spec 覆盖**：§1/§2 → Task 6/7/18；§3.1 → 11/12；§3.2 → 13/14；§3.3 → 2/15；§3.4 → 15；§4.1 → 3；§4.2 → 2/6；§4.3 → 7/9；§4.4 → 5/7/8；§4.5 → 6（负责人例 200→100→100）；§4.6 → 7/9/17；§5 → 18/19；§6 → 1/8/9/21；§7 八条 → 4（228 格）· 12（变红）· 6（③④）· 3（恒等式）· 2/5（Σ）· 2（10 月→2027-01）· 6（空 / 不适用）· 7（周期外）；§8 W-5/6/7 → OP-19 · OP-7/8 · OP-8；§9 顺序 = Phase 1/2/3。
- **跨任务名字**：`forecast_plan` / `PlanForecastInput` / `MskuInput` / `project_msku` / `Arrival` / `allocate` / `Batch` / `read_lead_time` / `forecast_payload` / `estimate_rate` / `data_windows` / `EMBARGO_DAYS` / `StockDays` / `daily_sales` / `fba_stock_days` / `stockAt` / `mskuStockAt` / `systemAt` / `totalsAt` 在定义任务与使用任务里拼写一致。
- **已知需要实现者就地确认的三处**（不是占位，是「以现有文件为准」）：Task 17 `stockState` 的「正常」值名、Task 18 块定位 testid、Task 19 `Modal` 的 props —— 三处都已写明去哪个文件看。
