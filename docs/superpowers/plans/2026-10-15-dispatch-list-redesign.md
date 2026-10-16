# 排货列表页 `/dispatch` 重排 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/dispatch` 首屏改成「计划抬头为主线、默认收起、展开是货号 × 月透视小表、排货表 chip 写下一步」，后端 `GET /v1/dispatch/plans` 补上只读 PG 的「下一步」事实。

**Architecture:** 后端只加 PG 查询（`repo/dispatch.py` 五个按表批量的读函数 + `usecase/sheet.py` 拼装，逐段 `timed` 并在失败时点名哪一段），不出站、不建表。前端把「下一步优先级」「透视」「搜索」做成纯函数（`dispatchHomeModel.ts`，Vitest 单测），`DispatchHome.tsx` 只负责渲染与交互；详情页只加一处「正在提交…」。

**Tech Stack:** Python 3.11 · FastAPI · psycopg2 · pytest（pure / integration 两层）· React 18 + Vite + TS（strict · `exactOptionalPropertyTypes` · `noUncheckedIndexedAccess`）· Vitest + Testing Library · 本仓「墨迹」纯 CSS。

**Spec:** `docs/superpowers/specs/2026-10-15-dispatch-list-redesign-design.md`（裁定 `docs/00-待裁定清单.md` DL-D1 ~ D10；评审 `.impeccable/critique/2026-10-15T11-50-29Z__web-src-pages-dispatch-dispatchhome-tsx.md`）

---

## ★ 开工前必须先过的两道闸（执行者不得自行跳过）

**闸 A · 工作区状态。** 本分支的排货功能**整体未提交**：`repo/dispatch.py`、`usecase/sheet.py`、`tests/test_api_sheets.py`、`web/src/pages/dispatch/`、`web/src/styles/dispatch.css`、大部分 `web/src/testing/fixtures/contract/*.json` 是 untracked；`web/src/api/types.ts`、`docs/08`、`docs/10` 有既有未提交改动。每个任务末尾的 `git add <file>` 会把这些**既有工作整份**带进本任务的提交。
→ Task 1 之前运行 `git status --porcelain -- repo/dispatch.py usecase/sheet.py tests/test_api_sheets.py web/src/api/types.ts web/src/pages/dispatch web/src/styles/dispatch.css "docs/08-后端接口清单.md" "docs/10-前端交互界面.md"`；**只要有一行输出，就停下问负责人**：先由负责人提交基线，或明确同意本计划的提交连带既有改动。没有答复就只做改动、不做 commit 步骤。

**闸 B · 待负责人裁定（spec 与代码对不上，本计划不替负责人定）。** 计划里的代码按「暂定」一栏写，裁定不同则只改标注处：

| # | 问题 | 暂定（本计划代码的写法） | 改哪 |
|---|---|---|---|
| Q1 | `to_submit` 的单位：spec §5 写「无领星单号的**排货单**数」，而 chip 复用的详情页原文「提交领星（N 张）」(`SheetPage.tsx:367`) 的 N = **排货单 × 来源仓**（= 一张领星单，CB-D5；`rules/ows.py:38`、`rules/fba_plan.py:34`） | 按排货单 × 来源仓数，与详情页同一个 N | `repo.sheets_submit_progress` 的 `count(*)` → `count(DISTINCT p.consignment_id)` |
| Q2 | FBA 表同时有「已建未锁」与「还没建」时：spec §4 序 4（补锁）先于序 5（提交），详情页按钮却先写「提交领星（N 张）」(`SheetPage.tsx:367`：`toCreate.length > 0 ? 提交 : 补锁`) | 照 spec 序（chip 写「补锁库存（N 张）+1」） | `sheetNextStep` 里序 4 / 序 5 两行顺序 |
| Q3 | 同一计划同一 货号 × 月 有两条活记录：spec §1 说「一条记录 = (plan, 货号, 月)」，但库层只有 `plan_line_one_per_cell UNIQUE (plan_id, rev, sku, period_start)`（`002_plan.sql:108`），不同 rev 的活记录没有唯一约束 | 两条都算进格子（还差 / 需求等相加），悬停文案尾部加「（N 条记录）」——★ 这几个字是新文案 | `pivotRecords` / `cellTitle` |
| Q4 | 已确认、但一张可提交的排货单都没有的表（理论上确认闸会挡，未实测是否可达）：spec §4 十二种情形都不命中，而 spec 又说「已确认」不再单独出现 | chip 写表状态原文、中性色，并 `console.warn('[dispatch] next step unmatched', …)`（不静默） | `sheetNextStep` 末尾兜底 |
| Q5 | 详情页「正在提交…」放哪、要不要同时禁用「提交领星」（后端本来就会 409 `submitting`，`api/ui/errors.py:58`）；另 spec §7 写「SheetPage 不动」与 DL-D8「详情页同步加」相抵 —— 本计划按 DL-D8（负责人裁定）做 | 只在底部常驻栏加一段状态字，不动按钮 | Task 4 |
| Q6 | `route = null`（旧版混合表）的 chip 没有「去向」可写（spec 格式 `#N 去向 下一步`） | 只写 `#N 旧版混合表：只能取消` | `SheetChip` |

★ 依 CLAUDE.md 实施纪律 ③，Task 5 把 Q1 ~ Q6 登记进 `docs/00` DL 组「待裁定」（负责人先裁了的写成已裁定）。

---

## Global Constraints

- 实施纪律：每个数字指到出处或标「设计取值 · 未实测」；每条约束指到 `01` 铁律 / `02` 关系 / `00` 裁定，否则进 `00` 待裁定。本计划**不引入任何新数字**；量级「未完成计划 10–50 个、一个计划约 100 msku」是负责人口述 · 未实测（spec §1）。
- DL-D7：列表的「下一步」**只用 PG 已存的事实**，`GET /v1/dispatch/plans` 不出站、不触发回执。
- spec §5：任一子查询失败 → 整体 5xx 并点名哪一段，**不把 chip 退回成「草稿」**（静默兜底是最坏的一种）。
- 日志三问：打的谁 · 多久 · 怎么失败的 —— PG 侧用 `shared.pg_client.timed`（自带 target / elapsed_ms / pgcode）。
- 分层由测试强制（`tests/test_layering.py`、`tests/test_layering_web.py`）：`repo/` 只收 cursor；`usecase/` 不 import psycopg2（L6）；`web/` 只打 `api/ui/` 已暴露的路径（L9）；`web/src` 不许出现 `lingxing`（L8）。
- 命名隔离：代码与界面不用 `inbound` · `shipment` · `shipment_plan` · `allocation`。
- chip 文案**逐字**照 spec §4 表（复用详情页原文）；新词只有「正在提交…」（DL-D8）。
- 界面文案只写当场发生的事；说明进折叠区（CLAUDE.md「屏幕给数据，文档给论证」）。代码注释只写「为什么」。
- 样式：只用 `web/src/styles/tokens.css` 已有的 token；复用 web 既有类（设计 artifact 的 `.sc-chip--danger/warn/ok/primary/outline` ↔ web 的 `.chip--bad/warn/ok/ours/dim`，见 `dispatch.css:3-4`；`.pt` 表在 `purchase.css:62-79`；`table.table` / `.r` 在 `shell.css:243-254`）；**不 import 设计 artifact 的 bundle**；新类一律 `dh-` 前缀写进 `dispatch.css`。
- 不建表、不写迁移（全部来自 `016 / 017 / 012 / 020 / 023 / 024 / 025` 已有的表与视图）。
- PB-D17：产品只有真路径；替身只在 `tests/`、`web/src/testing/`。
- 服务由负责人启动：本计划不起 / 不重启任何 dev server；只跑测试与 `tsc`。
- 契约样本改形状必须用 `SCM_WRITE_CONTRACT=1` 重生，再看 `git diff`，只收本计划预期变化的文件。

## Review Focus

1. **同一货号同一月两条活记录**（不同 rev 带进来的，库层无唯一约束）→ 格子不许静默只留一条：两条相加并在悬停里写出条数（Q3）。测试在 Task 2。
2. **点计划抬头里的 chip 链接 / 动作按钮** → 只做它自己的事（跳转 / 建表），不许顺带展开或收起计划。测试在 Task 3。
3. **搜索词带大小写差异或前后空格**（`  dcc1045 `）→ 照样命中货号；只有空格 = 不过滤；清空搜索后被搜索自动展开的计划收回去。测试在 Task 2（纯函数）与 Task 3（页面）。
4. **一张表同时命中多种情形**（在途 + 待核实 + 两轴不一致 + 待提交）→ chip 写优先级最高的那条（在途永远最先），其余计成「+N」且 N 准确。测试在 Task 2。
5. **一张表一种情形都不命中**（Q4）→ chip 不许空白、不许写「草稿」，写表状态原文并在控制台留痕。测试在 Task 2（纯函数）与 Task 3（`console.warn` 被调用）。

---

## File Structure

| 文件 | 动作 | 职责 |
|---|---|---|
| `repo/dispatch.py` | Modify | `dispatch_plans` 记录带 `confirmed / ordered`；新增 5 个按 `sheet_ids` 批量的读函数 |
| `usecase/sheet.py` | Modify | `dispatch_plans()` 逐段读、拼 `sheets[]` 事实、算 drift、失败点名；`load()` 带 `in_flight` |
| `tests/test_api_dispatch_plans.py` | Create | `/v1/dispatch/plans` 新字段与失败点名的集成测试 |
| `tests/test_api_sheets.py` | Modify `:143` `:521` | `sheets` 全等断言改成只比三列 |
| `web/src/testing/fixtures/contract/{dispatch-plans,sheet-detail*}.json` | Regenerate | 契约样本 |
| `web/src/api/types.ts` | Modify `~801-810`, `~840-850` | `DispatchPlanSheet` · 记录 `confirmed/ordered` · `SheetDetail.in_flight` |
| `docs/08-后端接口清单.md` | Modify `:165` `:167` | 接口形状 |
| `web/src/pages/dispatch/dispatchHomeModel.ts` | Create | 纯函数：`sheetNextStep` · `pivotRecords` · `cellTitle` · `filterPlans` · `skuHit` · `TONE_CLASS` |
| `web/src/pages/dispatch/dispatchHomeModel.test.ts` | Create | 纯函数单测 |
| `web/src/pages/dispatch/DispatchHome.tsx` | Rewrite | 计划抬头 · 透视小表 · chip · 搜索 · 全部排货表折叠 |
| `web/src/pages/dispatch/DispatchHome.test.tsx` | Rewrite | 页面测试 |
| `web/src/styles/dispatch.css` | Modify `:11-12`, 末尾 | 修孤立注释尾；加 `dh-*` 类，删不再用的 `.ds-plan-rec*` |
| `web/src/styles/tokens.test.ts` | Modify | 加「CSS 注释成对」检查 |
| `web/src/pages/dispatch/SheetPage.tsx` | Modify `~360` | 底部加「正在提交…」 |
| `web/src/pages/dispatch/SheetPage.test.tsx` | Modify | 在途测试 |
| `docs/10-前端交互界面.md` | Modify `:167` | §2.0b `/dispatch` 一行改成新结构 |
| `docs/00-待裁定清单.md` | Modify DL 组 | 登记 Q1 ~ Q6 |

---

### Task 1: 后端 —— `GET /v1/dispatch/plans` 带「下一步」事实，详情带 `in_flight`

**Files:**
- Modify: `repo/dispatch.py:33-62`（`dispatch_plans` 的记录查询）, 末尾追加 5 个函数
- Modify: `usecase/sheet.py:1-20`（import）, `:116-118`（`dispatch_plans`）, `:134-158`（`load`）
- Create: `tests/test_api_dispatch_plans.py`
- Modify: `tests/test_api_sheets.py:143`, `:521`
- Regenerate: `web/src/testing/fixtures/contract/dispatch-plans.json`, `sheet-detail.json`, `sheet-detail-oversea.json`, `sheet-detail-dispatched.json`, `sheet-detail-fba-dispatched.json`
- Modify: `web/src/api/types.ts`（`DispatchPlan` / `DispatchPlanRecord` / `SheetDetail`）
- Modify: `docs/08-后端接口清单.md:165`, `:167`

**Interfaces:**
- Consumes: `repo.dispatch.insert_write_call(cur, *, endpoint, side_effect, consignment_id, idem_key, actor, request) -> int`（`repo/dispatch.py:511`，测试造在途调用用）；`rules.receipt.drift(our_stage, lx_stage) -> str | None`（`rules/receipt.py:40`）；`shared.pg_client.timed(op, **fields)`、`pg_error_fields(e)`；`usecase.sheet.Conflict(status, error, hint, **fields)`（`api/ui/sheets.py:_call` 把它翻成 `ApiError`）。
- Produces（后端，供测试与 Task 4）:
  - `repo.dispatch.sheets_in_flight(cur, sheet_ids: list[int]) -> dict[int, int]`
  - `repo.dispatch.sheets_open_attention(cur, sheet_ids: list[int]) -> dict[int, int]`
  - `repo.dispatch.sheets_ext_stages(cur, sheet_ids: list[int]) -> list[tuple[int, str, str | None]]`
  - `repo.dispatch.sheets_submit_progress(cur, sheet_ids: list[int]) -> dict[int, dict]`（键 `orders` · `to_submit` · `to_lock`）
  - `repo.dispatch.sheets_draft_gaps(cur, sheet_ids: list[int]) -> dict[int, dict]`（键 `consignments` · `packing_missing` · `logistics_missing`）
  - 响应 `plans[].sheets[]`: `{sheet_id, state, route, in_flight: int, attention: int, drift: list[str], to_lock: int, to_submit: int, packing_missing: int, logistics_missing: int, consignments: int, all_submitted: bool}`；`plans[].records[]` 多 `confirmed: int` · `ordered: int`；`GET /v1/sheets/{id}` 多 `in_flight: int`；失败 → 500 `{"error": "dispatch_plans_failed", "part": <段名>, ...}`，段名 ∈ `plans · in_flight · attention · drift · submit_progress · draft_gaps`。
- Produces（前端类型，供 Task 2 ~ 4）:
  - `export interface DispatchPlanSheet { sheet_id: number; state: SheetState; route: DispatchRoute | null; in_flight: number; attention: number; drift: string[]; to_lock: number; to_submit: number; packing_missing: number; logistics_missing: number; consignments: number; all_submitted: boolean }`
  - `DispatchPlan.sheets: DispatchPlanSheet[]`；`DispatchPlanRecord` 多 `confirmed: number; ordered: number`；`SheetDetail` 多 `in_flight: number`。

- [ ] **Step 1: 写失败的集成测试**

Create `tests/test_api_dispatch_plans.py`:

```python
"""`/v1/dispatch/plans` 的排货表「下一步」事实（spec 2026-10-15 §5 · DL-D7：只用 PG 已存的事实，不出站）。"""
from repo import dispatch as dispatch_repo
from shared.pg_client import pg_conn
from tests.helpers import H
from tests.test_api_sheets import BOX, OVERSEA, add_line, new_sheet, packing_url, put, stock, world  # noqa: F401
from tests.test_api_submit_fba import confirmed_fba  # noqa: F401
from tests.test_api_submit_ows import confirmed, submit  # noqa: F401

FACTS = ("in_flight", "attention", "drift", "to_lock", "to_submit", "packing_missing", "logistics_missing",
         "consignments", "all_submitted")
ZERO = {"in_flight": 0, "attention": 0, "drift": [], "to_lock": 0, "to_submit": 0, "packing_missing": 0,
        "logistics_missing": 0, "consignments": 0, "all_submitted": False}


def facts(client, sheet_id):
    sheets = [s for p in client.get("/v1/dispatch/plans").json()["plans"] for s in p["sheets"]]
    s = next(s for s in sheets if s["sheet_id"] == sheet_id)
    return {k: s[k] for k in FACTS}


def test_empty_draft_has_nothing(client, seed, world):
    assert facts(client, new_sheet(client, seed)) == ZERO


def test_fba_draft_counts_missing_packing_lines_until_filled(client, seed, world):
    sid = new_sheet(client, seed)
    put(client, seed, sid, line_id=world["line_id"], units=3)
    assert facts(client, sid) == {**ZERO, "consignments": 1, "packing_missing": 1}
    assert client.put(packing_url(sid, seed), json=BOX, headers=H(seed.actor)).status_code == 200
    assert facts(client, sid) == {**ZERO, "consignments": 1}


def test_oversea_draft_counts_consignments_without_logistics(client, seed, world):
    sid = new_sheet(client, seed, route="oversea")
    put(client, seed, sid, line_id=world["line_id"], units=3, channel="oversea", dest_wid=OVERSEA)
    assert facts(client, sid) == {**ZERO, "consignments": 1, "logistics_missing": 1}
    client.get("/v1/logistics-channels", headers=H(seed.actor))
    assert client.put(f"/v1/sheets/{sid}/logistics/{OVERSEA}", json={"logistics_id": 28762},
                      headers=H(seed.actor)).status_code == 200
    assert facts(client, sid) == {**ZERO, "consignments": 1}


def test_confirmed_oversea_counts_each_consignment_source_pair_until_submitted(client, seed, confirmed):
    """★ 闸 B · Q1 暂定：一张排货单从两个来源仓发 = 两张领星备货单（CB-D5），与详情页「提交领星（2 张）」同一个 N。"""
    assert facts(client, confirmed) == {**ZERO, "consignments": 1, "to_submit": 2}
    assert submit(client, seed, confirmed).status_code == 200
    assert facts(client, confirmed) == {**ZERO, "consignments": 1, "all_submitted": True}


def test_unknown_create_result_is_counted_as_attention(client, seed, confirmed, lingxing):
    lingxing.enqueue("create_ows_order", "unknown_absent")
    submit(client, seed, confirmed)
    f = facts(client, confirmed)
    assert (f["attention"], f["in_flight"], f["to_submit"]) == (1, 0, 2)


def test_drift_is_named_per_live_order(client, seed, confirmed, lingxing):
    submit(client, seed, confirmed)
    del lingxing._ows[sorted(lingxing._ows)[0]]
    assert client.post(f"/v1/sheets/{confirmed}/receipt", headers=H(seed.actor)).status_code == 200
    assert facts(client, confirmed)["drift"] == ["领星里没有这张单"]


def test_in_flight_write_call_shows_on_list_and_detail_read_calls_do_not(client, seed, confirmed):
    cid = client.get(f"/v1/sheets/{confirmed}").json()["consignments"][0]["consignment_id"]
    with pg_conn() as c, c.cursor() as cur:
        dispatch_repo.insert_write_call(cur, endpoint="create_ows_order", side_effect="irreversible",
                                        consignment_id=cid, idem_key=f"SCM-CN-{cid}-1", actor=seed.actor, request={})
        dispatch_repo.insert_write_call(cur, endpoint="ows_order_nos", side_effect="read",
                                        consignment_id=cid, idem_key=None, actor=seed.actor, request={})
    assert facts(client, confirmed)["in_flight"] == 1
    assert client.get(f"/v1/sheets/{confirmed}").json()["in_flight"] == 1


def test_fba_plan_built_but_not_locked_is_to_lock_not_to_submit(client, seed, confirmed_fba, lingxing):
    assert facts(client, confirmed_fba) == {**ZERO, "consignments": 1, "to_submit": 1}
    lingxing.enqueue("fba_plan_lock", "rejected")
    submit(client, seed, confirmed_fba)
    assert facts(client, confirmed_fba) == {**ZERO, "consignments": 1, "to_lock": 1}
    submit(client, seed, confirmed_fba)                      # 只补锁，不重建（CC-D3）
    assert facts(client, confirmed_fba) == {**ZERO, "consignments": 1, "all_submitted": True}


def test_records_carry_confirmed_and_ordered(client, seed, world):
    with pg_conn() as c, c.cursor() as cur:
        l2 = add_line(cur, world["plan_id"], seed.sku_a, 180, [seed.seller_a])
    [p] = client.get("/v1/dispatch/plans").json()["plans"]
    got = {r["line_id"]: (r["confirmed"], r["ordered"]) for r in p["records"]}
    assert got == {world["line_id"]: (100, 100), l2: (0, 0)}


def test_a_failing_part_is_named_not_silently_defaulted(client, seed, world, monkeypatch):
    new_sheet(client, seed)

    def boom(cur, sheet_ids):
        raise RuntimeError("boom")

    monkeypatch.setattr(dispatch_repo, "sheets_open_attention", boom)
    r = client.get("/v1/dispatch/plans")
    assert r.status_code == 500, r.text
    assert (r.json()["error"], r.json()["part"], r.json()["err_type"]) == ("dispatch_plans_failed", "attention",
                                                                           "RuntimeError")
```

- [ ] **Step 2: 跑，确认失败**

Run: `uv run pytest -q tests/test_api_dispatch_plans.py`
Expected: FAIL —— 多数用例 `KeyError: 'in_flight'`；`test_records_carry_confirmed_and_ordered` 为 `KeyError: 'confirmed'`；`test_a_failing_part…` 为 `AttributeError: <module 'repo.dispatch'…> has no attribute 'sheets_open_attention'`。

- [ ] **Step 3: repo —— 记录带 confirmed / ordered**

In `repo/dispatch.py`, replace the records query inside `dispatch_plans` (lines 50-61) with:

```python
    # 每个产品（活记录）一行：/dispatch 要回答「哪个产品还没排完」，不只是计划的合计
    cur.execute(
        "SELECT l.plan_id, l.line_id, l.sku, k.name, l.period_start, l.state, d.total_units, d.staged_units,"
        "       d.dispatched_units, d.pending_units, po.confirmed_units, po.ordered_units"
        "  FROM plan_line l JOIN v_plan_line_dispatch_qty d USING (line_id)"
        "  JOIN v_plan_line_po_qty po USING (line_id) LEFT JOIN sku_catalog k USING (sku)"
        f" WHERE l.plan_id = ANY(%s) AND {LIVE_LINE} ORDER BY l.plan_id, d.pending_units DESC, l.sku, l.line_id",
        ([p["plan_id"] for p in plans],))
    rcols = ("line_id", "sku", "sku_name", "period", "state", "total", "staged", "dispatched", "pending",
             "confirmed", "ordered")
```

（`records` 字典与 `return` 两行不变。）

- [ ] **Step 4: repo —— 五个批量读函数**

Append to `repo/dispatch.py`:

```python
# ── /dispatch 排货表 chip 的「下一步」（spec 2026-10-15 §5 · DL-D7：只读本地事实）──────────

def sheets_in_flight(cur, sheet_ids: list[int]) -> dict[int, int]:
    """在途写调用数。判据同 020 erp_call_one_write_in_flight_per_consignment（side_effect <> 'read'）。"""
    cur.execute("SELECT c.sheet_id, count(*)::int FROM erp_call e JOIN consignment c USING (consignment_id)"
                " WHERE c.sheet_id = ANY(%s) AND e.outcome = 'in_flight' AND e.side_effect <> 'read'"
                " GROUP BY c.sheet_id", (sheet_ids,))
    return dict(cur.fetchall())


def sheets_open_attention(cur, sheet_ids: list[int]) -> dict[int, int]:
    cur.execute("SELECT c.sheet_id, count(*)::int FROM dispatch_attention a JOIN consignment c USING (consignment_id)"
                " WHERE c.sheet_id = ANY(%s) AND NOT EXISTS"
                "       (SELECT 1 FROM dispatch_attention_close x WHERE x.attention_id = a.attention_id)"
                " GROUP BY c.sheet_id", (sheet_ids,))
    return dict(cur.fetchall())


def sheets_ext_stages(cur, sheet_ids: list[int]) -> list[tuple[int, str, str | None]]:
    """未作废领星单的 (sheet_id, 我方进度, 领星当前进度)；两轴不一致由 rules.receipt.drift 判（同详情 _with_stage）。"""
    cur.execute("SELECT c.sheet_id, c.our_stage, s.stage FROM ext_ref e JOIN consignment c USING (consignment_id)"
                "  LEFT JOIN v_ext_stage s USING (ext_ref_id)"
                " WHERE c.sheet_id = ANY(%s)"
                "   AND NOT EXISTS (SELECT 1 FROM ext_ref_void v WHERE v.ext_ref_id = e.ext_ref_id)"
                " ORDER BY c.sheet_id, e.ext_ref_id", (sheet_ids,))
    return [tuple(r) for r in cur.fetchall()]


def sheets_submit_progress(cur, sheet_ids: list[int]) -> dict[int, dict]:
    """可提交的 排货单 × 来源仓（= 一张领星单，CB-D5；同 rules/ows · rules/fba_plan 的 orders 粒度与 SUBMITTABLE）：
    共几张 · 还没领星单号几张 · FBA 已建未锁几张。★ channel 按表去向取，旧版混合表同详情走海外仓那条（submit_ows._plan）。
    ★ 闸 B · Q1：若裁定按排货单数，to_submit 改 count(DISTINCT p.consignment_id) FILTER (...)。"""
    cur.execute(
        "WITH pairs AS ("
        "  SELECT DISTINCT c.sheet_id, l.consignment_id, l.from_wid"
        "    FROM consignment c JOIN sheet s USING (sheet_id) JOIN consignment_line l USING (consignment_id)"
        "   WHERE c.sheet_id = ANY(%s) AND c.our_stage IN ('已确认','已投送')"
        "     AND c.channel = coalesce(s.route, 'oversea')),"
        " live AS ("
        "  SELECT e.ext_ref_id, e.consignment_id, e.from_wid, e.kind, k.locked"
        "    FROM ext_ref e LEFT JOIN v_fba_plan_lock k USING (ext_ref_id)"
        "   WHERE NOT EXISTS (SELECT 1 FROM ext_ref_void v WHERE v.ext_ref_id = e.ext_ref_id))"
        " SELECT p.sheet_id, count(*)::int, count(*) FILTER (WHERE x.ext_ref_id IS NULL)::int,"
        "        count(*) FILTER (WHERE x.kind = 'fba_plan' AND x.locked IS NOT TRUE)::int"
        "   FROM pairs p LEFT JOIN live x ON x.consignment_id = p.consignment_id AND x.from_wid = p.from_wid"
        "  GROUP BY p.sheet_id", (sheet_ids,))
    return {r[0]: {"orders": r[1], "to_submit": r[2], "to_lock": r[3]} for r in cur.fetchall()}


def sheets_draft_gaps(cur, sheet_ids: list[int]) -> dict[int, dict]:
    """活的排货单数 · FBA 缺装箱行数（同前端 missingPacking：没有装箱行或六项有空）· 海外仓没选物流方式的单数（G7）。"""
    box_missing = " OR ".join(f"p.{k} IS NULL" for k in PACKING_COLS)
    cur.execute(
        "SELECT c.sheet_id, count(DISTINCT c.consignment_id)::int,"
        "       count(*) FILTER (WHERE c.channel = 'fba' AND l.consignment_id IS NOT NULL"
        f"                        AND (p.sheet_id IS NULL OR {box_missing}))::int,"
        "       count(DISTINCT c.consignment_id) FILTER (WHERE c.channel = 'oversea' AND g.sheet_id IS NULL)::int"
        "  FROM consignment c"
        "  LEFT JOIN consignment_line l ON l.consignment_id = c.consignment_id"
        "  LEFT JOIN consignment_packing p"
        "    ON (p.sheet_id, p.seller_sku, p.sid, p.from_wid) = (c.sheet_id, l.seller_sku, l.sid, l.from_wid)"
        "  LEFT JOIN consignment_logistics g ON (g.sheet_id, g.dest_wid) = (c.sheet_id, c.dest_wid)"
        " WHERE c.sheet_id = ANY(%s) AND c.our_stage NOT IN ('已放弃','已取消')"
        " GROUP BY c.sheet_id", (sheet_ids,))
    return {r[0]: {"consignments": r[1], "packing_missing": r[2], "logistics_missing": r[3]} for r in cur.fetchall()}
```

- [ ] **Step 5: usecase —— 逐段读、点名失败、拼事实**

In `usecase/sheet.py` add `import contextlib` to the imports and change the `shared.pg_client` import to:

```python
from shared.pg_client import pg_conn, pg_error_fields, timed
```

Replace `dispatch_plans()` (lines 116-118) with:

```python
@contextlib.contextmanager
def _part(name: str):
    """spec §5：任一段读不到整体 500 并点名哪一段 —— 不把 chip 退回成「草稿」。"""
    try:
        with timed(f"dispatch_plans.{name}"):
            yield
    except Conflict:
        raise
    except Exception as e:  # noqa: BLE001 —— 点名后照样以 500 冒出去
        raise Conflict(500, "dispatch_plans_failed", f"排货列表读不到「{name}」这一段", part=name,
                       err_type=type(e).__name__, **pg_error_fields(e)) from e


def dispatch_plans() -> list[dict]:
    t0 = time.perf_counter()
    with timed("dispatch_plans"), pg_conn() as c, c.cursor() as cur:
        with _part("plans"):
            plans = repo.dispatch_plans(cur)
        ids = [s["sheet_id"] for p in plans for s in p["sheets"]]
        with _part("in_flight"):
            in_flight = repo.sheets_in_flight(cur, ids)
        with _part("attention"):
            attention = repo.sheets_open_attention(cur, ids)
        with _part("drift"):
            stages = repo.sheets_ext_stages(cur, ids)
        with _part("submit_progress"):
            progress = repo.sheets_submit_progress(cur, ids)
        with _part("draft_gaps"):
            gaps = repo.sheets_draft_gaps(cur, ids)
    drift: dict[int, list[str]] = {}
    for sid, ours, lx in stages:
        d = receipt_rules.drift(ours, lx)
        if d:
            drift.setdefault(sid, []).append(d)
    for p in plans:
        p["sheets"] = [_sheet_facts(s, in_flight, attention, drift, progress, gaps) for s in p["sheets"]]
    log.info("op=dispatch_plans plans=%d sheets=%d records=%d in_flight=%d attention=%d drift=%d elapsed_ms=%d",
             len(plans), len(ids), sum(len(p["records"]) for p in plans), sum(in_flight.values()),
             sum(attention.values()), sum(len(v) for v in drift.values()), (time.perf_counter() - t0) * 1000)
    return plans


def _sheet_facts(s: dict, in_flight: dict, attention: dict, drift: dict, progress: dict, gaps: dict) -> dict:
    """查询按 sheet_ids 全量 GROUP BY，不在结果里 = 真没有（不是没算进来）。"""
    sid = s["sheet_id"]
    pg = progress.get(sid, {"orders": 0, "to_submit": 0, "to_lock": 0})
    return {**s, "in_flight": in_flight.get(sid, 0), "attention": attention.get(sid, 0), "drift": drift.get(sid, []),
            "to_lock": pg["to_lock"], "to_submit": pg["to_submit"],
            **gaps.get(sid, {"consignments": 0, "packing_missing": 0, "logistics_missing": 0}),
            "all_submitted": pg["orders"] > 0 and pg["to_submit"] == 0 and pg["to_lock"] == 0}
```

In `load()` add one line inside the `with` block after `events = repo.sheet_events(cur, sheet_id)`:

```python
        in_flight = repo.sheets_in_flight(cur, [sheet_id]).get(sheet_id, 0)
```

and one key in the returned dict after `"watermark": s["ledger_now"],`:

```python
        "in_flight": in_flight,
```

- [ ] **Step 6: 跑新测试，确认通过**

Run: `uv run pytest -q tests/test_api_dispatch_plans.py`
Expected: `10 passed`

- [ ] **Step 7: 改旧断言（新字段让 `sheets` 全等断言失效）**

Run first to see them fail: `uv run pytest -q tests/test_api_sheets.py -k "dispatch_plans or one_draft_per_plan_route"`
Expected: FAIL at `test_api_sheets.py:143` and `:521`（`assert p["sheets"] == [...]` 多出新键）。

In `tests/test_api_sheets.py` replace line 143:

```python
    assert [{k: s[k] for k in ("sheet_id", "state", "route")} for s in p["sheets"]] == \
        [{"sheet_id": sid, "state": "草稿", "route": "fba"}]
```

and line 521:

```python
    assert [{k: s[k] for k in ("sheet_id", "state", "route")} for s in p["sheets"]] == \
        [{"sheet_id": a, "state": "草稿", "route": "fba"}, {"sheet_id": b, "state": "草稿", "route": "oversea"}]
```

Run: `uv run pytest -q tests/test_api_sheets.py tests/test_e2e_c_dispatch_draft.py tests/test_api_submit_ows.py tests/test_api_submit_fba.py tests/test_api_receipt.py`
Expected: all passed（`test_e2e_c_dispatch_draft.py:36` 只比 `plan_id`，不受影响）。

- [ ] **Step 8: 重生契约样本，只收预期的文件**

Run: `uv run pytest -q tests/test_web_contract.py`
Expected: FAIL in `test_contract_dispatch_samples` / `test_contract_ows_samples` / `test_contract_fba_samples`（骨架多了 `in_flight` 等键）。

Run: `SCM_WRITE_CONTRACT=1 uv run pytest -q tests/test_web_contract.py && git status --porcelain web/src/testing/fixtures/contract/`
Expected: 测试 passed；变化的样本应只有 `dispatch-plans.json`、`sheet-detail.json`、`sheet-detail-oversea.json`、`sheet-detail-dispatched.json`、`sheet-detail-fba-dispatched.json`。用 `git diff --no-index` 不适用（它们 untracked）—— 打开 `dispatch-plans.json` 核对：`sheets[]` 每张带九个新键、`records[]` 带 `confirmed` / `ordered`。别的样本若也变了（如 `po-*.json` 是闸 A 提到的既有改动），**不收进本任务**。

Run: `uv run pytest -q tests/test_web_contract.py`
Expected: passed

- [ ] **Step 9: 前端类型**

In `web/src/api/types.ts` replace the `DispatchPlan` / `DispatchPlanRecord` block with:

```ts
/** CA-D11 入口：未完成的销售计划（需求侧的量，PG 内可得） */
export interface DispatchPlan {
  plan_id: PlanId; title: string; rev: number; lines: number; total: number; confirmed: number; ordered: number;
  staged: number; dispatched: number; pending: number; sheets: DispatchPlanSheet[];
  /** 每个产品（活记录）一行，还差多的在前 */
  records: DispatchPlanRecord[];
}
/** chip「下一步」的本地事实（DL-D7：只读 PG，列表不现查领星） */
export interface DispatchPlanSheet {
  sheet_id: number; state: SheetState; route: DispatchRoute | null;
  in_flight: number; attention: number; drift: string[]; to_lock: number; to_submit: number;
  packing_missing: number; logistics_missing: number; consignments: number; all_submitted: boolean;
}
export interface DispatchPlanRecord {
  line_id: number; sku: string; sku_name: string | null; period: string; state: string;
  total: number; confirmed: number; ordered: number; staged: number; dispatched: number; pending: number;
}
```

and in `SheetDetail` add after `watermark: number;`:

```ts
  /** 在途写调用数（DL-D8） */
  in_flight: number;
```

Run: `cd web && npx tsc --noEmit && npx vitest run src/api/contract.test.tsx src/pages/dispatch`
Expected: tsc 无输出；vitest passed（`DispatchHome.test.tsx` 的旧用例仍按 `plan-rec-*` 渲染，Task 3 才改）。

- [ ] **Step 10: docs/08**

In `docs/08-后端接口清单.md` replace row `:165` (the `GET /v1/dispatch/plans` row) with:

```markdown
| GET | `/v1/dispatch/plans` | UC-D1 | ★ CA-D11 入口：未归档、还有活记录的销售计划，每张带 记录数 · 需求 · 已组单 · 已下单 · 已排未发 · 已投送 · 还差 · 已有排货表（不含已取消）；`records[]` 每个活记录（产品）一行：`line_id · sku · sku_name · period · state · total · confirmed · ordered · staged · dispatched · pending`（`confirmed / ordered` 取 `v_plan_line_po_qty`）。★ `sheets[]` 每张带 chip「下一步」的本地事实（DL-D3 / D7，spec 2026-10-15 §5；只读 PG、不出站）：`in_flight`（在途写调用，`side_effect <> 'read'`）· `attention`（未关闭的待核实）· `drift[]`（未作废领星单的两轴不一致，`rules/receipt.drift`）· `to_lock`（FBA 已建未锁）· `to_submit`（还没领星单号的 排货单 × 来源仓 —— 待裁定 Q1）· `packing_missing`（FBA 缺装箱行）· `logistics_missing`（海外仓没选物流方式的排货单）· `consignments`（活的排货单）· `all_submitted`；任一段读不到 → 500 `dispatch_plans_failed` 点名 `part` |
```

and in row `:167` (`GET /v1/sheets?state=` · `/v1/sheets/{id}`) append to the last cell: ` · 详情带 `in_flight`（在途写调用数，DL-D8）`.

- [ ] **Step 11: 纯层与分层自检**

Run: `JXD_SCM_CONFIG=/nonexistent uv run pytest -q -m pure && uv run ruff check repo/dispatch.py usecase/sheet.py tests/test_api_dispatch_plans.py`
Expected: pure 全部 passed（含 `test_layering.py` 的 L6 / repo 只收 cursor）；ruff `All checks passed!`

- [ ] **Step 12: Commit（先过闸 A）**

```bash
git add repo/dispatch.py usecase/sheet.py tests/test_api_dispatch_plans.py tests/test_api_sheets.py \
  web/src/api/types.ts "docs/08-后端接口清单.md" \
  web/src/testing/fixtures/contract/dispatch-plans.json web/src/testing/fixtures/contract/sheet-detail.json \
  web/src/testing/fixtures/contract/sheet-detail-oversea.json web/src/testing/fixtures/contract/sheet-detail-dispatched.json \
  web/src/testing/fixtures/contract/sheet-detail-fba-dispatched.json
git commit -m "$(cat <<'EOF'
feat(dispatch): /v1/dispatch/plans 带排货表下一步的本地事实，记录带已组单 / 已下单

- sheets[] 加 in_flight · attention · drift · to_lock · to_submit · packing_missing · logistics_missing · consignments · all_submitted（DL-D7：只读 PG）
- 逐段 timed；任一段失败 500 dispatch_plans_failed 点名 part，不退回成「草稿」
- 详情带 in_flight（DL-D8）；契约样本重生；docs/08

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: 前端纯模型 —— 下一步优先级 · 透视 · 搜索

**Files:**
- Create: `web/src/pages/dispatch/dispatchHomeModel.ts`
- Create: `web/src/pages/dispatch/dispatchHomeModel.test.ts`

**Interfaces:**
- Consumes: Task 1 的 `DispatchPlan` · `DispatchPlanSheet` · `DispatchPlanRecord`（`web/src/api/types.ts`）。
- Produces:
  - `export type ChipTone = 'neutral' | 'warn' | 'danger' | 'primary'`
  - `export interface NextStep { text: string; tone: ChipTone; more: number; unmatched: boolean }`
  - `export const TONE_CLASS: Record<ChipTone, string>`
  - `export function sheetNextStep(s: DispatchPlanSheet): NextStep`
  - `export interface PivotCell { pending: number; total: number; confirmed: number; ordered: number; staged: number; dispatched: number; records: number }`
  - `export interface PivotRow { sku: string; sku_name: string | null; cells: Map<string, PivotCell>; pending_sum: number }`
  - `export interface Pivot { months: string[]; rows: PivotRow[] }`（月 = `YYYY-MM`）
  - `export function pivotRecords(records: DispatchPlanRecord[]): Pivot`
  - `export function cellTitle(sku: string, month: string, c: PivotCell): string`
  - `export function skuHit(sku: string, q: string): boolean`
  - `export function filterPlans(plans: DispatchPlan[], q: string): { shown: DispatchPlan[]; autoOpen: number[] }`

- [ ] **Step 1: 写失败的测试**

Create `web/src/pages/dispatch/dispatchHomeModel.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import type { DispatchPlan, DispatchPlanRecord, DispatchPlanSheet } from '../../api/types';
import { TONE_CLASS, cellTitle, filterPlans, pivotRecords, sheetNextStep, skuHit, type ChipTone } from './dispatchHomeModel';

const S: DispatchPlanSheet = {
  sheet_id: 11, state: '草稿', route: 'fba', in_flight: 0, attention: 0, drift: [], to_lock: 0, to_submit: 0,
  packing_missing: 0, logistics_missing: 0, consignments: 1, all_submitted: false,
};
const step = (o: Partial<DispatchPlanSheet>) => sheetNextStep({ ...S, ...o });

describe('sheetNextStep（spec §4 · DL-D7 / D8）', () => {
  const CASES: [string, Partial<DispatchPlanSheet>, string, ChipTone, number][] = [
    ['0 在途写调用排最前', { state: '已确认', in_flight: 1, to_submit: 1 }, '正在提交…', 'neutral', 1],
    ['1 旧版混合表', { route: null }, '旧版混合表：只能取消', 'neutral', 0],
    ['2 待核实', { state: '已确认', attention: 1, to_submit: 2 }, '提交结果未知，先核实再继续', 'warn', 1],
    ['3 两轴不一致', { state: '已确认', route: 'oversea', drift: ['领星已撤销'], all_submitted: true }, '⚠ 领星已撤销', 'danger', 1],
    ['4 FBA 已建未锁', { state: '已确认', to_lock: 1 }, '补锁库存（1 张）', 'warn', 0],
    ['5 还有没领星单号的', { state: '已确认', to_submit: 2 }, '提交领星（2 张）', 'primary', 0],
    ['6 草稿 FBA 缺装箱', { packing_missing: 3 }, '缺装箱 3', 'warn', 0],
    ['7 草稿 海外仓没选物流方式', { route: 'oversea', logistics_missing: 1 }, '还没选物流方式', 'warn', 0],
    ['8 草稿 没有排货单', { consignments: 0 }, '还没有排货单', 'neutral', 0],
    ['9 草稿 以上都没有', {}, '草稿', 'primary', 0],
    ['10 海外仓全部已提交', { state: '已确认', route: 'oversea', all_submitted: true }, '海外仓备货单已全部提交领星', 'neutral', 0],
    ['11 FBA 全部已提交', { state: '已确认', all_submitted: true }, '已提交领星 FBA 发货计划', 'neutral', 0],
  ];
  it.each(CASES)('%s', (_name, o, text, tone, more) => {
    expect(step(o)).toEqual({ text, tone, more, unmatched: false });
  });

  it('★ 多条命中：取优先级最高的，其余计成 +N；在途永远最先（Review Focus）', () => {
    expect(step({ state: '已确认', in_flight: 1, attention: 1, drift: ['领星已撤销'], to_submit: 2 }))
      .toEqual({ text: '正在提交…', tone: 'neutral', more: 3, unmatched: false });
  });

  it('★ 一条都不命中：写表状态原文、标 unmatched，不写「草稿」也不空着（Review Focus · 待裁定 Q4）', () => {
    expect(step({ state: '已确认' })).toEqual({ text: '已确认', tone: 'neutral', more: 0, unmatched: true });
  });

  it('「已确认」不再用绿色：四种色都映到既有 chip 类，没有 chip--ok', () => {
    expect(TONE_CLASS).toEqual({ neutral: 'chip', warn: 'chip chip--warn', danger: 'chip chip--bad', primary: 'chip chip--ours' });
  });
});

const R = (o: Partial<DispatchPlanRecord>): DispatchPlanRecord => ({
  line_id: 1, sku: 'A', sku_name: '甲', period: '2026-10-01', state: '已确认',
  total: 200, confirmed: 200, ordered: 150, staged: 5, dispatched: 0, pending: 195, ...o,
});

describe('pivotRecords（DL-D2 · DL-D6 · DL-D10）', () => {
  it('一个货号一行；月份只列有记录的月、按时间排；没有记录的格子不存在（≠ 0）', () => {
    const p = pivotRecords([
      R({ line_id: 1, period: '2027-02-01' }), R({ line_id: 2, period: '2026-11-01' }),
      R({ line_id: 3, sku: 'B', period: '2026-12-01', total: 50, pending: 0 }),
    ]);
    expect(p.months).toEqual(['2026-11', '2026-12', '2027-02']);
    expect(p.rows.map((r) => r.sku)).toEqual(['A', 'B']);
    expect(p.rows[0]!.cells.get('2026-12')).toBeUndefined();
    expect(p.rows[1]!.cells.get('2026-12')!.pending).toBe(0);
  });

  it('货号行按还差合计降序，同额按货号', () => {
    const p = pivotRecords([R({ sku: 'C', pending: 10 }), R({ sku: 'B', pending: 97 }), R({ sku: 'A', pending: 10 })]);
    expect(p.rows.map((r) => [r.sku, r.pending_sum])).toEqual([['B', 97], ['A', 10], ['C', 10]]);
  });

  it('★ 同一货号同一月两条活记录：两条都算进格子，不静默丢（Review Focus · 待裁定 Q3）', () => {
    const p = pivotRecords([R({ line_id: 1, total: 100, pending: 100 }), R({ line_id: 2, total: 40, pending: 30 })]);
    const c = p.rows[0]!.cells.get('2026-10')!;
    expect([c.pending, c.total, c.records]).toEqual([130, 140, 2]);
    expect(p.rows[0]!.pending_sum).toBe(130);
    expect(cellTitle('A', '2026-10', c)).toMatch(/（2 条记录）$/);
  });

  it('品名取第一个非空的', () => {
    expect(pivotRecords([R({ sku_name: null }), R({ line_id: 2, period: '2026-11-01' })]).rows[0]!.sku_name).toBe('甲');
  });

  it('悬停 / 读屏给全量：需求 · 已组单 · 已下单 · 已排未发 · 已投送', () => {
    const c = pivotRecords([R({})]).rows[0]!.cells.get('2026-10')!;
    expect(cellTitle('A', '2026-10', c)).toBe('A 2026-10：还差 195 · 需求 200 · 已组单 200 · 已下单 150 · 已排未发 5 · 已投送 0');
  });
});

const P = (o: Partial<DispatchPlan>): DispatchPlan => ({
  plan_id: 1, title: '测试1', rev: 1, lines: 1, total: 0, confirmed: 0, ordered: 0, staged: 0, dispatched: 0, pending: 0,
  sheets: [], records: [], ...o,
});

describe('filterPlans / skuHit（DL-D10：计划名 / 货号，纯前端）', () => {
  const plans = [P({ plan_id: 1, title: '测试1', records: [R({ sku: 'DCC1045014AI' })] }),
    P({ plan_id: 2, title: '测试销售计划2', records: [R({ sku: 'DCC9' })] })];

  it('空查询：全列、不自动展开', () => {
    expect(filterPlans(plans, '')).toEqual({ shown: plans, autoOpen: [] });
    expect(filterPlans(plans, '   ')).toEqual({ shown: plans, autoOpen: [] });
  });

  it('按计划名命中：列出但不自动展开', () => {
    expect(filterPlans(plans, '销售计划2')).toEqual({ shown: [plans[1]], autoOpen: [] });
  });

  it('★ 按货号命中：自动展开；大小写、前后空格不影响（Review Focus）', () => {
    expect(filterPlans(plans, '  dcc1045 ')).toEqual({ shown: [plans[0]], autoOpen: [1] });
    expect(skuHit('DCC1045014AI', '  dcc1045 ')).toBe(true);
    expect(skuHit('DCC1045014AI', '   ')).toBe(false);
  });

  it('都没命中：空', () => {
    expect(filterPlans(plans, 'zzz')).toEqual({ shown: [], autoOpen: [] });
  });
});
```

- [ ] **Step 2: 跑，确认失败**

Run: `cd web && npx vitest run src/pages/dispatch/dispatchHomeModel.test.ts`
Expected: FAIL —— `Failed to resolve import "./dispatchHomeModel"`

- [ ] **Step 3: 实现**

Create `web/src/pages/dispatch/dispatchHomeModel.ts`:

```ts
/** /dispatch 的纯模型（spec 2026-10-15）：排货表下一步 · 货号 × 月透视 · 搜索。 */
import type { DispatchPlan, DispatchPlanRecord, DispatchPlanSheet } from '../../api/types';

export type ChipTone = 'neutral' | 'warn' | 'danger' | 'primary';
export interface NextStep { text: string; tone: ChipTone; more: number; unmatched: boolean }

/** 设计系统 sc-chip 的四种 ↔ web 既有类（dispatch.css 头注）；色只给要人动手的 */
export const TONE_CLASS: Record<ChipTone, string> = {
  neutral: 'chip', warn: 'chip chip--warn', danger: 'chip chip--bad', primary: 'chip chip--ours',
};

/** spec §4：文案逐字复用详情页；按表中顺序取第一条，其余计「+N」 */
export function sheetNextStep(s: DispatchPlanSheet): NextStep {
  const hits: { text: string; tone: ChipTone }[] = [];
  if (s.in_flight > 0) hits.push({ text: '正在提交…', tone: 'neutral' });
  if (s.route === null) hits.push({ text: '旧版混合表：只能取消', tone: 'neutral' });
  if (s.attention > 0) hits.push({ text: '提交结果未知，先核实再继续', tone: 'warn' });
  const drift = s.drift[0];
  if (drift !== undefined) hits.push({ text: `⚠ ${drift}`, tone: 'danger' });
  // ★ 待裁定 Q2：详情页按钮在两者都有时先写「提交领星」；这里照 spec 序 4 在前
  if (s.route === 'fba' && s.to_lock > 0) hits.push({ text: `补锁库存（${s.to_lock} 张）`, tone: 'warn' });
  if (s.state === '已确认' && s.to_submit > 0) hits.push({ text: `提交领星（${s.to_submit} 张）`, tone: 'primary' });
  if (s.state === '草稿') {
    if (s.packing_missing > 0) hits.push({ text: `缺装箱 ${s.packing_missing}`, tone: 'warn' });
    if (s.logistics_missing > 0) hits.push({ text: '还没选物流方式', tone: 'warn' });
    if (s.consignments === 0) hits.push({ text: '还没有排货单', tone: 'neutral' });
    if (hits.length === 0) hits.push({ text: '草稿', tone: 'primary' });
  }
  if (s.state === '已确认' && s.all_submitted) {
    hits.push({ text: s.route === 'oversea' ? '海外仓备货单已全部提交领星' : '已提交领星 FBA 发货计划', tone: 'neutral' });
  }
  const first = hits[0];
  // ★ 待裁定 Q4：不命中时写表状态原文；调用方负责在控制台留痕
  if (first === undefined) return { text: s.state, tone: 'neutral', more: 0, unmatched: true };
  return { ...first, more: hits.length - 1, unmatched: false };
}

export interface PivotCell { pending: number; total: number; confirmed: number; ordered: number; staged: number; dispatched: number; records: number }
export interface PivotRow { sku: string; sku_name: string | null; cells: Map<string, PivotCell>; pending_sum: number }
export interface Pivot { months: string[]; rows: PivotRow[] }

const EMPTY: PivotCell = { pending: 0, total: 0, confirmed: 0, ordered: 0, staged: 0, dispatched: 0, records: 0 };

/** DL-D2 货号 × 月；DL-D10 只列有记录的月。★ 同格多条记录相加并计条数（待裁定 Q3），不覆盖 */
export function pivotRecords(records: DispatchPlanRecord[]): Pivot {
  const months = [...new Set(records.map((r) => r.period.slice(0, 7)))].sort();
  const bySku = new Map<string, PivotRow>();
  for (const r of records) {
    const row = bySku.get(r.sku) ?? { sku: r.sku, sku_name: null, cells: new Map<string, PivotCell>(), pending_sum: 0 };
    bySku.set(r.sku, row);
    if (row.sku_name === null && r.sku_name) row.sku_name = r.sku_name;
    const m = r.period.slice(0, 7);
    const c = row.cells.get(m) ?? EMPTY;
    row.cells.set(m, {
      pending: c.pending + r.pending, total: c.total + r.total, confirmed: c.confirmed + r.confirmed,
      ordered: c.ordered + r.ordered, staged: c.staged + r.staged, dispatched: c.dispatched + r.dispatched,
      records: c.records + 1,
    });
    row.pending_sum += r.pending;
  }
  const rows = [...bySku.values()].sort((a, b) => b.pending_sum - a.pending_sum || a.sku.localeCompare(b.sku));
  return { months, rows };
}

export function cellTitle(sku: string, month: string, c: PivotCell): string {
  return `${sku} ${month}：还差 ${c.pending} · 需求 ${c.total} · 已组单 ${c.confirmed} · 已下单 ${c.ordered}`
    + ` · 已排未发 ${c.staged} · 已投送 ${c.dispatched}${c.records > 1 ? `（${c.records} 条记录）` : ''}`;
}

const norm = (q: string) => q.trim().toLowerCase();

export function skuHit(sku: string, q: string): boolean {
  const k = norm(q);
  return k !== '' && sku.toLowerCase().includes(k);
}

/** 计划名或货号命中即列出；★ 只有货号命中的才自动展开 */
export function filterPlans(plans: DispatchPlan[], q: string): { shown: DispatchPlan[]; autoOpen: number[] } {
  const k = norm(q);
  if (k === '') return { shown: plans, autoOpen: [] };
  const shown: DispatchPlan[] = [];
  const autoOpen: number[] = [];
  for (const p of plans) {
    const bySku = p.records.some((r) => r.sku.toLowerCase().includes(k));
    if (bySku) autoOpen.push(p.plan_id);
    if (bySku || p.title.toLowerCase().includes(k)) shown.push(p);
  }
  return { shown, autoOpen };
}
```

- [ ] **Step 4: 跑，确认通过**

Run: `cd web && npx vitest run src/pages/dispatch/dispatchHomeModel.test.ts && npx tsc --noEmit`
Expected: `Tests  24 passed`（12 情形 + 3 · 透视 5 · 搜索 4）；tsc 无输出。

- [ ] **Step 5: 先弄失败一次（CLAUDE.md「测试写完先弄失败一次」）**

临时把 `sheetNextStep` 里 `if (s.in_flight > 0)` 那一行挪到 `if (s.state === '已确认' && s.all_submitted)` 之前，再跑 Step 4 的命令 —— Expected: `0 在途写调用排最前` 与 `★ 多条命中` 两条 FAIL。改回原样，再跑一遍确认全绿。

- [ ] **Step 6: Commit（先过闸 A）**

```bash
git add web/src/pages/dispatch/dispatchHomeModel.ts web/src/pages/dispatch/dispatchHomeModel.test.ts
git commit -m "$(cat <<'EOF'
feat(dispatch): /dispatch 纯模型 —— 排货表下一步优先级 · 货号×月透视 · 搜索

- sheetNextStep 照 spec §4 词表（正在提交… 排最前，+N 计其余；不命中写表状态并标 unmatched）
- pivotRecords 只列有记录的月（DL-D10），同格多条相加计条数，不覆盖
- filterPlans：计划名 / 货号，货号命中才自动展开

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `DispatchHome` 重写 —— 抬头主线 · 透视小表 · chip · 搜索 · 全部排货表折叠

**Files:**
- Rewrite: `web/src/pages/dispatch/DispatchHome.tsx`
- Rewrite: `web/src/pages/dispatch/DispatchHome.test.tsx`
- Modify: `web/src/styles/dispatch.css`（删 `:124-126` 的 `.ds-plan-rec*`，末尾加 `dh-*`）

**Interfaces:**
- Consumes: Task 2 的 `TONE_CLASS` · `sheetNextStep` · `pivotRecords` · `cellTitle` · `filterPlans` · `skuHit` · `Pivot`；Task 1 的 `DispatchPlan` · `DispatchPlanSheet`；既有 `ROUTE_SHORT`（`dispatchGridModel.ts:60`）、`Fold`（`components/Fold.tsx`，`<details>` 默认收起）、`ErrorDetail`、`fmtAt`、`api.listDispatchPlans / listSheets / createSheet`。
- Produces（测试定位用的 `data-testid`）：`plan-<plan_id>`（计划块）· `plan-left-<plan_id>`（还差）· `pivot-<plan_id>`（透视表）· `pivot-row-<plan_id>-<sku>` · `cell-<plan_id>-<sku>-<YYYY-MM>` · `sheet-chip-<sheet_id>` · `dispatch-plans`（计划区）· `all-sheets`（折叠区）· `sheet-list`（全部排货表的表）。

- [ ] **Step 1: 写失败的页面测试**

Replace `web/src/pages/dispatch/DispatchHome.test.tsx` with:

```tsx
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useParams } from 'react-router-dom';
import list from '../../testing/fixtures/contract/sheet-list.json';
import plans from '../../testing/fixtures/contract/dispatch-plans.json';
import { fail, ok, stubBackend, type Handler } from '../../testing/backendStub';

beforeEach(() => { vi.resetModules(); });
afterEach(() => { vi.doUnmock('../../api'); vi.restoreAllMocks(); });

function Sheet() { return <div>SHEET {useParams().sheetId}</div>; }

const SH = { sheet_id: 0, state: '草稿', route: 'fba', in_flight: 0, attention: 0, drift: [], to_lock: 0, to_submit: 0,
  packing_missing: 0, logistics_missing: 0, consignments: 1, all_submitted: false };
const sh = (o: Record<string, unknown>) => ({ ...SH, ...o });
const rec = (o: Record<string, unknown>) => ({ line_id: 1, sku: 'DCC1045014AI', sku_name: '围栏', period: '2026-10-01',
  state: '已确认', total: 200, confirmed: 0, ordered: 0, staged: 5, dispatched: 0, pending: 195, ...o });
const PLAN = { plan_id: 5, title: '10 月计划', rev: 7, lines: 2, total: 380, confirmed: 140, ordered: 10,
  staged: 0, dispatched: 0, pending: 380, sheets: [], records: [] };

async function mount(r: Record<string, Handler>) {
  const calls = stubBackend({ 'GET /v1/attention': () => ok({ count: 0, items: [] }),
    'GET /v1/sheets': () => ok({ sheets: [] }), 'GET /v1/dispatch/plans': () => ok({ plans: [] }), ...r });
  const { ApiError } = await import('../../api/client');
  const { createHttpApi } = await import('../../api/http');
  vi.doMock('../../api', () => ({ api: createHttpApi({ base: '/v1', timeoutMs: 1000 }), ApiError }));
  const { DispatchHome } = await import('./DispatchHome');
  render(
    <MemoryRouter initialEntries={['/dispatch']}>
      <Routes>
        <Route path="/dispatch" element={<DispatchHome />} />
        <Route path="/dispatch/sheets/:sheetId" element={<Sheet />} />
      </Routes>
    </MemoryRouter>,
  );
  await act(async () => {});
  return calls;
}
const plansOf = (...ps: unknown[]) => ({ 'GET /v1/dispatch/plans': () => ok({ plans: ps }) });

describe('DispatchHome · 计划抬头（DL-D1 · DL-D5）', () => {
  it('抬头：计划 rev · N 个货号 · M 条记录 · 需求 · 已组单 · 已下单 · 已排未发 · 已投送 · 还差；区块标题带个数', async () => {
    await mount(plansOf({ ...PLAN, records: [rec({ line_id: 1 }), rec({ line_id: 2, period: '2026-11-01' })] }));
    const head = screen.getByTestId('plan-5');
    for (const t of ['10 月计划', 'rev 7', '1 个货号 · 2 条记录', '需求380', '已组单140', '已下单10', '已排未发0', '已投送0']) {
      expect(head.textContent!.replace(/\s/g, '')).toContain(t.replace(/\s/g, ''));
    }
    expect(screen.getByTestId('plan-left-5')).toHaveTextContent('380');
    expect(screen.getByRole('heading', { name: /未完成的销售计划/ })).toHaveTextContent('· 1 个');
  });

  it('默认全部收起；点标题展开，键盘 Enter 再收起', async () => {
    await mount(plansOf({ ...PLAN, records: [rec({})] }));
    const t = within(screen.getByTestId('plan-5')).getByRole('button', { name: /10 月计划/ });
    expect(t).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByTestId('pivot-5')).toBeNull();
    await userEvent.click(t);
    expect(t).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByTestId('pivot-5')).toBeInTheDocument();
    t.focus();
    await userEvent.keyboard('{Enter}');
    expect(screen.queryByTestId('pivot-5')).toBeNull();
  });

  it('★ 点抬头空白处也展开；点抬头里的动作按钮只做它的事，不展开（Review Focus）', async () => {
    await mount({ ...plansOf({ ...PLAN, records: [rec({})] }),
      'POST /v1/sheets': () => fail(409, 'plan_archived', '计划已归档') });
    await userEvent.click(within(screen.getByTestId('plan-5')).getByText('1 个货号 · 2 条记录'));
    expect(screen.getByTestId('pivot-5')).toBeInTheDocument();
    await userEvent.click(within(screen.getByTestId('plan-5')).getByText('1 个货号 · 2 条记录'));
    expect(screen.queryByTestId('pivot-5')).toBeNull();
    await userEvent.click(within(screen.getByTestId('plan-5')).getByRole('button', { name: '海外仓备货' }));
    expect(screen.getByText('计划已归档')).toBeInTheDocument();
    expect(screen.queryByTestId('pivot-5')).toBeNull();
  });

  it('还差 0 写「已满足」，不给动作按钮', async () => {
    await mount(plansOf({ ...PLAN, pending: 0, sheets: [sh({ sheet_id: 3 })] }));
    expect(screen.getByTestId('plan-left-5')).toHaveTextContent('已满足');
    expect(within(screen.getByTestId('plan-5')).queryByRole('button', { name: /备货|接着/ })).toBeNull();
  });
});

describe('DispatchHome · 动作（CA-D13 · 评审 P1）', () => {
  it('两个入口各带自己的去向建表', async () => {
    const calls = await mount({ ...plansOf(PLAN), 'POST /v1/sheets': () => ({ status: 201, json: { sheet_id: 7 } }) });
    await userEvent.click(within(screen.getByTestId('plan-5')).getByRole('button', { name: '海外仓备货' }));
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ plan_id: 5, route: 'oversea' });
    expect(screen.getByText('SHEET 7')).toBeInTheDocument();
  });

  it('已有草稿：「接着…#N」是主按钮且不建表；没有草稿的去向是次按钮；两者横排', async () => {
    const calls = await mount(plansOf({ ...PLAN, sheets: [sh({ sheet_id: 3, route: 'fba' })] }));
    const row = screen.getByTestId('plan-5');
    const cont = within(row).getByRole('button', { name: '接着平台仓 · FBA #3' });
    const neu = within(row).getByRole('button', { name: '海外仓备货' });
    expect(cont).toHaveClass('btn--primary');
    expect(neu).not.toHaveClass('btn--primary');
    expect(cont.parentElement).toBe(neu.parentElement);
    await userEvent.click(cont);
    expect(calls.some((c) => c.method === 'POST')).toBe(false);
    expect(screen.getByText('SHEET 3')).toBeInTheDocument();
  });
});

describe('DispatchHome · 排货表 chip（DL-D3 · D7 · D8）', () => {
  it('chip 写「#N 去向 下一步」，色按词表；「已确认」不单独出现', async () => {
    await mount(plansOf({ ...PLAN, sheets: [sh({ sheet_id: 11, state: '已确认', to_submit: 1 }),
      sh({ sheet_id: 12, route: 'oversea', logistics_missing: 1 }), sh({ sheet_id: 13, route: null })] }));
    expect(screen.getByTestId('sheet-chip-11')).toHaveTextContent('#11 平台仓 · FBA 提交领星（1 张）');
    expect(screen.getByTestId('sheet-chip-11')).toHaveClass('chip--ours');
    expect(screen.getByTestId('sheet-chip-12')).toHaveTextContent('#12 海外仓 还没选物流方式');
    expect(screen.getByTestId('sheet-chip-12')).toHaveClass('chip--warn');
    expect(screen.getByTestId('sheet-chip-13')).toHaveTextContent('#13 旧版混合表：只能取消');
    expect(screen.getByTestId('plan-5')).not.toHaveTextContent('已确认');
  });

  it('多条命中写 +N', async () => {
    await mount(plansOf({ ...PLAN, sheets: [sh({ sheet_id: 11, state: '已确认', in_flight: 1, to_submit: 1 })] }));
    expect(screen.getByTestId('sheet-chip-11')).toHaveTextContent('#11 平台仓 · FBA 正在提交…+1');
  });

  it('★ 一条都不命中：写表状态并在控制台留痕（Review Focus · 待裁定 Q4）', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    await mount(plansOf({ ...PLAN, sheets: [sh({ sheet_id: 14, state: '已确认' })] }));
    expect(screen.getByTestId('sheet-chip-14')).toHaveTextContent('#14 平台仓 · FBA 已确认');
    expect(warn).toHaveBeenCalledWith('[dispatch] next step unmatched', expect.stringContaining('"sheet_id":14'));
  });
});

describe('DispatchHome · 透视小表（DL-D2 · D6 · D10）', () => {
  it('月份只列有记录的月；格子「还差 /需求」，0 写已满足，没有记录写「—」；悬停给全量；行按还差合计降序', async () => {
    await mount(plansOf({ ...PLAN, records: [
      rec({ line_id: 1, sku: 'DCC1045014AI', period: '2026-10-01', total: 200, pending: 195 }),
      rec({ line_id: 2, sku: 'DCC1041294BW', sku_name: '软窝', period: '2026-11-01', total: 180, pending: 97 }),
      rec({ line_id: 3, sku: 'DCC1041294BW', sku_name: '软窝', period: '2026-10-01', total: 50, pending: 0 })] }));
    await userEvent.click(within(screen.getByTestId('plan-5')).getByRole('button', { name: /10 月计划/ }));
    const t = screen.getByTestId('pivot-5');
    expect(within(t).getAllByRole('columnheader').map((h) => h.textContent)).toEqual(['货号', '品名', '2026-10', '2026-11', '还差']);
    expect(within(t).getAllByRole('row').slice(1).map((r) => r.getAttribute('data-testid')))
      .toEqual(['pivot-row-5-DCC1045014AI', 'pivot-row-5-DCC1041294BW']);
    const c = screen.getByTestId('cell-5-DCC1045014AI-2026-10');
    expect(c).toHaveTextContent('195/200');
    expect(c).toHaveClass('r');
    expect(c).toHaveAttribute('title', 'DCC1045014AI 2026-10：还差 195 · 需求 200 · 已组单 0 · 已下单 0 · 已排未发 5 · 已投送 0');
    expect(screen.getByTestId('cell-5-DCC1045014AI-2026-11')).toHaveTextContent('—');
    expect(screen.getByTestId('cell-5-DCC1041294BW-2026-10')).toHaveTextContent('已满足');
  });

  it('契约样本：后端真响应里的记录能渲染成格子', async () => {
    await mount({ 'GET /v1/dispatch/plans': () => ok(plans) });
    const p = plans.plans[0]!;
    await userEvent.click(within(screen.getByTestId(`plan-${p.plan_id}`)).getByRole('button', { expanded: false }));
    const r = p.records[0]!;
    expect(screen.getByTestId(`cell-${p.plan_id}-${r.sku}-${r.period.slice(0, 7)}`)).toHaveTextContent(`${r.pending}/${r.total}`);
  });
});

describe('DispatchHome · 搜索（DL-D10）', () => {
  const two = plansOf({ ...PLAN, records: [rec({ sku: 'DCC1045014AI' })] },
    { ...PLAN, plan_id: 6, title: '测试销售计划2', records: [rec({ sku: 'DCC9' })] });

  it('★ 货号命中：只留命中的计划、自动展开、命中行高亮；大小写不影响；清空后收回（Review Focus）', async () => {
    await mount(two);
    const box = screen.getByRole('searchbox', { name: '搜计划名或货号' });
    await userEvent.type(box, 'dcc1045');
    expect(screen.queryByTestId('plan-6')).toBeNull();
    expect(screen.getByTestId('pivot-row-5-DCC1045014AI')).toHaveClass('is-on');
    await userEvent.clear(box);
    expect(screen.getByTestId('plan-6')).toBeInTheDocument();
    expect(screen.queryByTestId('pivot-5')).toBeNull();
  });

  it('计划名命中：列出但不展开；都没命中写「没有匹配的计划或货号」', async () => {
    await mount(two);
    const box = screen.getByRole('searchbox', { name: '搜计划名或货号' });
    await userEvent.type(box, '测试销售计划2');
    expect(screen.queryByTestId('plan-5')).toBeNull();
    expect(screen.getByTestId('plan-6')).toBeInTheDocument();
    expect(screen.queryByTestId('pivot-6')).toBeNull();
    await userEvent.clear(box);
    await userEvent.type(box, 'zzz');
    expect(screen.getByText('没有匹配的计划或货号')).toBeInTheDocument();
  });
});

describe('DispatchHome · 全部排货表（DL-D9）', () => {
  it('进折叠区、默认收起，标题带张数与已取消张数；表有 table 基类、数字右对齐', async () => {
    const s0 = list.sheets[0]!;
    await mount({ 'GET /v1/sheets': () => ok({ sheets: [s0, { ...s0, sheet_id: 9, state: '已取消' }] }) });
    const fold = screen.getByTestId('all-sheets');
    expect(fold).not.toHaveAttribute('open');
    expect(fold).toHaveTextContent('全部排货表（2 张，含已取消 1）');
    const t = screen.getByTestId('sheet-list');
    expect(t).toHaveClass('table');
    expect(within(t).getByRole('columnheader', { name: '件数' })).toHaveClass('r');
    expect(t).toHaveTextContent('#1');
  });

  it('没有未完成的计划', async () => {
    await mount({});
    expect(screen.getByText('没有未完成的销售计划')).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 跑，确认失败**

Run: `cd web && npx vitest run src/pages/dispatch/DispatchHome.test.tsx`
Expected: FAIL —— 例如 `Unable to find an accessible element with the role "button" and name /10 月计划/`、`Unable to find an element by: [data-testid="sheet-chip-11"]`、`all-sheets` 找不到。

- [ ] **Step 3: 重写页面**

Replace `web/src/pages/dispatch/DispatchHome.tsx` with:

```tsx
import { useEffect, useMemo, useState, type MouseEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { AppShell } from '../../shell/AppShell';
import { ErrorDetail } from '../../components/ErrorDetail';
import { Fold } from '../../components/Fold';
import { api, ApiError } from '../../api';
import { asApiError } from '../../api/client';
import type { DispatchPlan, DispatchPlanSheet, DispatchPlans, DispatchRoute, SheetList } from '../../api/types';
import { ROUTE_SHORT } from './dispatchGridModel';
import { TONE_CLASS, cellTitle, filterPlans, pivotRecords, sheetNextStep, skuHit, type Pivot } from './dispatchHomeModel';
import { fmtAt } from '../purchase/fmt';

const ROUTES = ['fba', 'oversea'] as const;

/** 排货员首页（CA-D11 · DL-D1）：计划抬头为主线，默认收起；展开是货号 × 月小表 */
export function DispatchHome() {
  const navigate = useNavigate();
  const [plans, setPlans] = useState<DispatchPlans | null>(null);
  const [plansErr, setPlansErr] = useState<ApiError | null>(null);
  const [list, setList] = useState<SheetList | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  const [createErr, setCreateErr] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [q, setQ] = useState('');
  /** 人点过的展开 / 收起；没点过的跟着搜索走（货号命中自动展开） */
  const [override, setOverride] = useState<Map<number, boolean>>(() => new Map());

  useEffect(() => {
    api.listDispatchPlans().then(setPlans, (e: unknown) => setPlansErr(asApiError(e)));
    api.listSheets().then(setList, (e: unknown) => setErr(asApiError(e)));
  }, []);

  const { shown, autoOpen } = useMemo(() => filterPlans(plans?.plans ?? [], q), [plans, q]);
  const auto = useMemo(() => new Set(autoOpen), [autoOpen]);
  const isOpen = (id: number) => override.get(id) ?? auto.has(id);
  const toggle = (id: number) => setOverride((prev) => new Map(prev).set(id, !isOpen(id)));
  const search = (v: string) => {
    setQ(v);
    // 新命中的计划让搜索说了算，不被之前的手动收起挡住
    const hit = filterPlans(plans?.plans ?? [], v).autoOpen;
    if (hit.length) setOverride((prev) => { const n = new Map(prev); hit.forEach((id) => n.delete(id)); return n; });
  };

  /** ★ CA-D13：按去向开表；这个去向已有草稿就接着排那张 */
  const start = async (p: DispatchPlan, route: DispatchRoute) => {
    const draft = p.sheets.find((s) => s.state === '草稿' && s.route === route);
    if (draft) { navigate(`/dispatch/sheets/${draft.sheet_id}`); return; }
    setBusy(`${p.plan_id}/${route}`);
    try {
      const { sheet_id } = await api.createSheet(p.plan_id, route);
      navigate(`/dispatch/sheets/${sheet_id}`);
    } catch (e) {
      setCreateErr(asApiError(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <AppShell crumb="排货">
      <div className="head">
        <div className="head__main"><h1>排货</h1></div>
        <div className="head__act">
          <span className="pm-search-wrap">
            <input className="pm-search" type="search" aria-label="搜计划名或货号" placeholder="搜 计划 / 货号"
              value={q} onChange={(e) => search(e.target.value)} />
          </span>
        </div>
      </div>
      {createErr && <ErrorDetail err={createErr} onDismiss={() => setCreateErr(null)} />}

      <section className="sec">
        <h2 className="sec__title">未完成的销售计划{plans && plans.plans.length > 0 && <span className="muted">· {plans.plans.length} 个</span>}</h2>
        {plansErr ? <ErrorDetail err={plansErr} /> : plans === null ? <div className="empty">正在取销售计划…</div>
          : plans.plans.length === 0 ? <div className="empty">没有未完成的销售计划</div>
          : shown.length === 0 ? <div className="empty">没有匹配的计划或货号</div> : (
            <div className="dh-plans" data-testid="dispatch-plans">
              {shown.map((p) => (
                <PlanBlock key={p.plan_id} p={p} open={isOpen(p.plan_id)} q={q} busy={busy}
                  onToggle={() => toggle(p.plan_id)} onStart={(route) => void start(p, route)} />
              ))}
            </div>
          )}
      </section>

      <AllSheets list={list} err={err} />
    </AppShell>
  );
}

function PlanBlock({ p, open, q, busy, onToggle, onStart }: {
  p: DispatchPlan; open: boolean; q: string; busy: string | null; onToggle: () => void; onStart: (r: DispatchRoute) => void;
}) {
  const pivot = useMemo(() => pivotRecords(p.records), [p.records]);
  const bodyId = `plan-body-${p.plan_id}`;
  const onHead = (e: MouseEvent<HTMLDivElement>) => {
    // 抬头里的链接 / 按钮各做各的事；标题按钮自己会切换
    if ((e.target as HTMLElement).closest('a, button, input, select')) return;
    onToggle();
  };
  return (
    <article className="dh-plan" data-testid={`plan-${p.plan_id}`}>
      <div className="dh-plan__head" onClick={onHead}>
        <div className="dh-plan__id">
          <button type="button" className="dh-plan__title" aria-expanded={open} aria-controls={open ? bodyId : undefined}
            onClick={onToggle}>
            {p.title} <span className="pt-rev">rev {p.rev}</span>
          </button>
          <span className="dh-plan__count">{pivot.rows.length} 个货号 · {p.lines} 条记录</span>
        </div>
        <div className="dh-plan__kpis">
          {([['需求', p.total], ['已组单', p.confirmed], ['已下单', p.ordered], ['已排未发', p.staged], ['已投送', p.dispatched]] as const).map(([l, v]) => (
            <div key={l} className="pm-kpi"><span className="pm-kpi__l">{l}</span><span className="pm-kpi__v">{v}</span></div>
          ))}
          <div className="pm-kpi ds-kpi--left"><span className="pm-kpi__l">还差</span>
            <span className={`pm-kpi__v${p.pending === 0 ? ' ds-done' : ''}`} data-testid={`plan-left-${p.plan_id}`}>
              {p.pending === 0 ? '已满足' : p.pending}
            </span>
          </div>
        </div>
        <div className="dh-plan__row2">
          {p.sheets.map((sh) => <SheetChip key={sh.sheet_id} sh={sh} />)}
          {p.pending > 0 && (
            <div className="dh-plan__acts">
              {ROUTES.map((route) => {
                const draft = p.sheets.find((s) => s.state === '草稿' && s.route === route);
                return (
                  <button key={route} type="button" className={`btn btn--sm${draft ? ' btn--primary' : ''}`}
                    disabled={busy === `${p.plan_id}/${route}`} onClick={() => onStart(route)}>
                    {draft ? `接着${ROUTE_SHORT[route]} #${draft.sheet_id}` : route === 'fba' ? '平台仓备货 · FBA' : '海外仓备货'}
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </div>
      {open && <div className="dh-plan__body" id={bodyId}><PivotTable planId={p.plan_id} pivot={pivot} q={q} /></div>}
    </article>
  );
}

function SheetChip({ sh }: { sh: DispatchPlanSheet }) {
  const step = sheetNextStep(sh);
  useEffect(() => {
    if (step.unmatched) console.warn('[dispatch] next step unmatched', JSON.stringify(sh));
  }, [step.unmatched, sh]);
  return (
    <Link to={`/dispatch/sheets/${sh.sheet_id}`} className={TONE_CLASS[step.tone]} data-testid={`sheet-chip-${sh.sheet_id}`}>
      #{sh.sheet_id}{sh.route ? ` ${ROUTE_SHORT[sh.route]}` : ''} {step.text}
      {step.more > 0 && <span className="dh-more">+{step.more}</span>}
    </Link>
  );
}

function PivotTable({ planId, pivot, q }: { planId: number; pivot: Pivot; q: string }) {
  return (
    <div className="pt-wrap">
      <table className="pt" data-testid={`pivot-${planId}`}>
        <thead>
          <tr><th>货号</th><th>品名</th>{pivot.months.map((m) => <th key={m} className="r">{m}</th>)}<th className="r">还差</th></tr>
        </thead>
        <tbody>
          {pivot.rows.map((r) => (
            <tr key={r.sku} className={skuHit(r.sku, q) ? 'is-on' : undefined} data-testid={`pivot-row-${planId}-${r.sku}`}>
              <td className="pt-sku">{r.sku}</td>
              <td className="dh-name" title={r.sku_name ?? undefined}>{r.sku_name ?? ''}</td>
              {pivot.months.map((m) => {
                const c = r.cells.get(m);
                return (
                  <td key={m} className="r dh-cell" data-testid={`cell-${planId}-${r.sku}-${m}`}
                    title={c ? cellTitle(r.sku, m, c) : undefined}>
                    {!c ? <span className="pt-zero">—</span> : (
                      <>
                        <span aria-hidden="true">
                          {c.pending === 0 ? <span className="ds-done">已满足</span> : <><b>{c.pending}</b><span className="pt-rev">/{c.total}</span></>}
                        </span>
                        <span className="dh-sr">{cellTitle(r.sku, m, c)}</span>
                      </>
                    )}
                  </td>
                );
              })}
              <td className="r"><b className={r.pending_sum === 0 ? 'ds-done' : 'ds-pending'}>{r.pending_sum === 0 ? '已满足' : r.pending_sum}</b></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AllSheets({ list, err }: { list: SheetList | null; err: ApiError | null }) {
  if (err) return <section className="sec"><h2 className="sec__title">全部排货表</h2><ErrorDetail err={err} /></section>;
  const n = list?.sheets.length ?? 0;
  const cancelled = list?.sheets.filter((s) => s.state === '已取消').length ?? 0;
  return (
    <section className="sec">
      <Fold summary={list === null ? '全部排货表（正在取…）' : `全部排货表（${n} 张，含已取消 ${cancelled}）`} testId="all-sheets">
        {list === null ? <div className="empty">正在取排货表…</div> : n === 0 ? <div className="empty">还没有排货表</div> : (
          <div className="table-scroll">
            <table className="table table--dense" data-testid="sheet-list">
              <thead><tr><th>排货表</th><th>销售计划</th><th>去向</th><th>状态</th><th className="r">排货单</th><th className="r">件数</th><th>建表人</th><th>建于</th></tr></thead>
              <tbody>
                {list.sheets.map((s) => (
                  <tr key={s.sheet_id}>
                    <td><Link to={`/dispatch/sheets/${s.sheet_id}`}>#{s.sheet_id}</Link></td>
                    <td>{s.plan_title ?? <span className="muted">未关联计划</span>}</td>
                    <td>{s.route ? ROUTE_SHORT[s.route] : <span className="muted">旧版混合</span>}</td>
                    <td><span className={`chip ${s.state === '草稿' ? 'chip--ours' : s.state === '已确认' ? 'chip--ok' : 'chip--dim'}`}>{s.state}</span></td>
                    <td className="r num">{s.consignments}</td>
                    <td className="r num">{s.units}</td>
                    <td>{s.created_by}</td>
                    <td>{fmtAt(s.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Fold>
    </section>
  );
}
```

- [ ] **Step 4: 样式**

In `web/src/styles/dispatch.css` delete the block

```css
/* /dispatch：计划下的产品行 */
.ds-plan-rec td { color: var(--text-2); border-top: none; }
.ds-plan-rec__sku { padding-left: var(--space-3); }
```

and append at the end:

```css
/* /dispatch：计划抬头为主线（DL-D1 / D5）+ 每个计划自带的货号 × 月小表（DL-D2） */
.dh-plans { display: grid; gap: var(--space-2); }
.dh-plan { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-lg); box-shadow: var(--shadow-card); overflow: hidden; }
.dh-plan__head { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: var(--space-2) var(--space-4); align-items: center; padding: var(--space-3) var(--space-4); cursor: pointer; }
.dh-plan__head:hover { background: var(--surface-2); }
.dh-plan__id { display: flex; flex-wrap: wrap; align-items: baseline; gap: var(--space-1) var(--space-3); min-width: 0; }
.dh-plan__title { appearance: none; border: 0; background: transparent; padding: 0; display: inline-flex; align-items: baseline; gap: var(--space-2); font: 650 15px/20px var(--font-sans); color: var(--text); cursor: pointer; text-align: left; }
.dh-plan__title::before { content: "›"; display: inline-block; color: var(--text-3); transition: transform .15s; }
.dh-plan__title[aria-expanded="true"]::before { transform: rotate(90deg); }
.dh-plan__title:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; border-radius: var(--radius-sm); }
.dh-plan__count { font-size: 12px; color: var(--text-3); }
.dh-plan__kpis { display: flex; flex-wrap: wrap; justify-content: flex-end; align-items: flex-end; gap: var(--space-4); }
.dh-plan__row2 { grid-column: 1 / -1; display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-2); }
.dh-plan__acts { margin-left: auto; display: flex; gap: var(--space-2); }
.dh-plan__body { border-top: 1px solid var(--border); }
.dh-plan__body .pt-wrap { border: 0; border-radius: 0; box-shadow: none; }
.dh-name { max-width: 0; width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text-2); }
.dh-cell { white-space: nowrap; font-variant-numeric: tabular-nums; }
.dh-more { margin-left: var(--space-1); font-weight: 600; }
.dh-sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
```

- [ ] **Step 5: 跑，确认通过**

Run: `cd web && npx vitest run src/pages/dispatch && npx tsc --noEmit`
Expected: `DispatchHome.test.tsx` 全部 passed（15 条）、`dispatchHomeModel.test.ts` 与 `SheetPage.test.tsx` / `dispatchGridModel.test.ts` 不受影响；tsc 无输出。

- [ ] **Step 6: 先弄失败一次**

临时把 `onHead` 里 `if ((e.target as HTMLElement).closest(...)) return;` 删掉再跑 `npx vitest run src/pages/dispatch/DispatchHome.test.tsx` —— Expected: `★ 点抬头空白处也展开…` FAIL（点「海外仓备货」后 `pivot-5` 出现了）。恢复原样再跑一遍全绿。

- [ ] **Step 7: 全量前端测试 + 分层**

Run: `cd web && npx vitest run && cd .. && JXD_SCM_CONFIG=/nonexistent uv run pytest -q tests/test_layering_web.py`
Expected: 全部 passed（L8：`web/src` 里没出现 `lingxing`；L9：没新增后端路径）。

- [ ] **Step 8: Commit（先过闸 A）**

```bash
git add web/src/pages/dispatch/DispatchHome.tsx web/src/pages/dispatch/DispatchHome.test.tsx web/src/styles/dispatch.css
git commit -m "$(cat <<'EOF'
feat(dispatch): /dispatch 计划抬头为主线、默认收起，展开是货号×月小表

- DL-D1/D5：抬头 计划 rev · N 个货号 · M 条记录 · 需求…还差；标题按钮 aria-expanded，点抬头空白处也切换
- DL-D2/D6/D10：每计划一张小表，格子「还差 /需求」、已满足、没有记录写「—」，悬停 / 读屏给全量
- DL-D3/D7/D8：chip 写下一步；接着…#N 为主按钮，新建为次按钮横排（评审 P1）
- DL-D9：全部排货表进折叠区、带张数与已取消张数；补 table 基类与 .r
- 搜索 计划名 / 货号，命中货号自动展开、行高亮

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: 详情页加「正在提交…」（DL-D8）

**Files:**
- Modify: `web/src/pages/dispatch/SheetPage.tsx`（底部常驻栏 `data-testid="dock"`，约 `:369` 「已确认，整表冻结」那一行之前）
- Modify: `web/src/pages/dispatch/SheetPage.test.tsx`（文件末尾加一个 `describe`）

**Interfaces:**
- Consumes: Task 1 的 `SheetDetail.in_flight: number`（`GET /v1/sheets/{id}` 已带；契约样本 `sheet-detail*.json` 已重生，值为 0）。
- Produces: 底部栏里 `data-testid="in-flight"` 的状态字「正在提交…」。

- [ ] **Step 1: 写失败的测试**

Append to `web/src/pages/dispatch/SheetPage.test.tsx`:

```tsx
describe('排货工作台 · 在途写调用（DL-D8）', () => {
  it('有在途写调用：底部写「正在提交…」', async () => {
    await mount(routes({ 'GET /v1/sheets/1': () => ok({ ...D, in_flight: 1 }) }));
    expect(within(screen.getByTestId('dock')).getByTestId('in-flight')).toHaveTextContent('正在提交…');
  });

  it('没有在途写调用：不写', async () => {
    await mount(routes());
    expect(screen.queryByTestId('in-flight')).toBeNull();
  });
});
```

- [ ] **Step 2: 跑，确认失败**

Run: `cd web && npx vitest run src/pages/dispatch/SheetPage.test.tsx -t "在途写调用"`
Expected: FAIL —— `Unable to find an element by: [data-testid="in-flight"]`（第二条 PASS）。

- [ ] **Step 3: 实现**

In `web/src/pages/dispatch/SheetPage.tsx`, in the dock, immediately before

```tsx
          {detail.state === '已确认' && <span className="pe-bar__state">已确认，整表冻结</span>}
```

insert:

```tsx
          {detail.in_flight > 0 && <span className="pe-bar__state" data-testid="in-flight">正在提交…</span>}
```

（★ 待裁定 Q5：按钮不动，后端在途时本来就 409 `submitting`。）

- [ ] **Step 4: 跑，确认通过**

Run: `cd web && npx vitest run src/pages/dispatch/SheetPage.test.tsx && npx tsc --noEmit`
Expected: SheetPage 全部 passed；tsc 无输出。

- [ ] **Step 5: Commit（先过闸 A）**

```bash
git add web/src/pages/dispatch/SheetPage.tsx web/src/pages/dispatch/SheetPage.test.tsx
git commit -m "$(cat <<'EOF'
feat(dispatch): 排货表详情底部写「正在提交…」（DL-D8，与列表 chip 同词）

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: 收尾 —— 修 `dispatch.css` 孤立注释 · `docs/10` §2.0b · `docs/00` 登记待裁定

**Files:**
- Modify: `web/src/styles/dispatch.css:11-12`
- Modify: `web/src/styles/tokens.test.ts`（加一个 `describe`）
- Modify: `docs/10-前端交互界面.md:167`（§2.0b `/dispatch` 行）
- Modify: `docs/00-待裁定清单.md`（DL 组表后）

**Interfaces:**
- Consumes: Task 1 ~ 4 落地后的行为（文档照实写）。
- Produces: 无代码接口。

- [ ] **Step 1: 写失败的测试 —— CSS 注释必须成对**

`dispatch.css:11-12` 是一段没有 `/*` 开头的注释尾（`   排货` / `   ====… */`），浏览器把它连同后面的 `.gl-list--one` 当成一个非法选择器整条丢掉（评审 Minor）。In `web/src/styles/tokens.test.ts` append:

```ts
describe('样式表的注释成对', () => {
  // 少一个 /* 时，注释尾会把紧跟的那条规则吞成非法选择器，整条静默失效，不会报任何错
  it.each(['tokens.css', 'shell.css', 'app.css', 'purchase.css', 'dispatch.css'])('%s', (file) => {
    const text = readFileSync(resolve(webRoot(), 'src/styles', file), 'utf8');
    expect((text.match(/\/\*/g) ?? []).length).toBe((text.match(/\*\//g) ?? []).length);
  });
});
```

Run: `cd web && npx vitest run src/styles/tokens.test.ts`
Expected: FAIL —— `dispatch.css`：`expected 5 to be 6`

- [ ] **Step 2: 修**

In `web/src/styles/dispatch.css` replace lines 11-12

```css
   排货
   ============================================================ */
```

with

```css
/* ============================================================
   排货
   ============================================================ */
```

Run: `cd web && npx vitest run src/styles/tokens.test.ts`
Expected: passed

- [ ] **Step 3: docs/10 §2.0b `/dispatch` 一行**

In `docs/10-前端交互界面.md` replace the whole `| \`/dispatch\` | \`DispatchHome\` | … | ①⑥ |` row (line 167) with:

```markdown
| `/dispatch` | `DispatchHome` | ★ 第一屏 = **未完成的销售计划**（CA-D11 · DL-D1），`plan_id` 倒序（DL-D4），★ 默认全部收起（DL-D5）。抬头：计划 rev · 「N 个货号 · M 条记录」· 需求 · 已组单 · 已下单 · 已排未发 · 已投送 · **还差**（最大字号，0 写「已满足」）· 排货表 chip · 动作；点抬头任意处 / 键盘 Enter 展开（抬头里的链接与按钮只做自己的事）。★ 展开体每个计划自带一张小表（DL-D2，各计划月份不同，不共用列头）：货号 · 品名（单行省略）· 每月一列（★ 只列有记录的月，DL-D10）· 还差合计，货号行按还差合计降序；格子大字还差、小字 `/需求`（DL-D6），0 写「已满足」，★ 该货号该月没有记录写「—」（≠ 0，≠ 已满足），悬停 / 读屏给 需求 · 已组单 · 已下单 · 已排未发 · 已投送 —— 取消排货表后还差立即回来。★ 排货表 chip「#N 去向 下一步」（DL-D3 / D7 / D8：只用本地事实，列表不现查领星；词表与优先级见 spec `superpowers/specs/2026-10-15-dispatch-list-redesign-design.md` §4，「正在提交…」排最前，多条命中写「+N」；不再单独显示「已确认」、不显示闸未过数）。★ 动作（CA-D13）：已有草稿 **接着平台仓 · FBA #N** / **接着海外仓 #N** 为主按钮，没有草稿的去向 **平台仓备货 · FBA** / **海外仓备货** 为次按钮，横排；已满足的计划不给。★ 搜索 计划名 / 货号（DL-D10，纯前端）：命中货号的计划自动展开、命中行高亮；没命中写「没有匹配的计划或货号」。「全部排货表」进折叠区、默认收起（DL-D9），标题带张数与已取消张数，展开后带对应计划与「去向」列（旧版混合表写「旧版混合」）。两块各自读、各自点名错误 | ①⑥ |
```

（★ 该节开头的 blockquote「C-a 只到…FBA 投送在 C-c 才出现」已陈旧，但 spec 没要求改 —— 本计划不动，范围外。）

- [ ] **Step 4: docs/00 登记待裁定（CLAUDE.md 实施纪律 ③）**

In `docs/00-待裁定清单.md`, directly after the DL 组「已由负责人裁定」table (the row `★ DL-D10`), insert（负责人已裁了的条目改写成「已裁定」并删去「暂定」列）:

```markdown

### 待裁定（实施 spec 2026-10-15 时发现与代码对不上；plan `superpowers/plans/2026-10-15-dispatch-list-redesign.md` 闸 B）

| # | 问题 | 现在代码里的暂定 | 出处 |
|---|---|---|---|
| DL-Q5 | chip「提交领星（N 张）」的 N：spec §5 写「无领星单号的排货单数」，详情页同一句的 N 是 排货单 × 来源仓（= 一张领星单） | 排货单 × 来源仓，与详情页同一个 N | `SheetPage.tsx:367` · `rules/ows.py:38` · CB-D5 |
| DL-Q6 | FBA 表同时「已建未锁」与「还没建」：spec §4 序 4（补锁）在序 5（提交）前，详情页按钮却先写「提交领星」 | 照 spec 序 | `SheetPage.tsx:367` |
| DL-Q7 | 同一计划同一 货号 × 月 两条活记录（库层只按 rev 唯一）：透视格子怎么写 | 相加，悬停尾部写「（N 条记录）」 | `002_plan.sql:108` · spec §1 |
| DL-Q8 | 已确认、但没有可提交排货单的表：spec §4 都不命中 | 写表状态原文 + 控制台留痕 | spec §4「已确认不再单独出现」 |
| DL-Q9 | 详情页「正在提交…」只写状态，还是同时禁用「提交领星」；spec §7「SheetPage 不动」与 DL-D8 相抵（按 DL-D8 做了） | 只写状态，按钮不动 | DL-D8 · `api/ui/errors.py:58` |
| DL-Q10 | 旧版混合表（`route = null`）chip 没有「去向」可写 | 只写 `#N 旧版混合表：只能取消` | spec §4 序 1 |
```

（编号接 DL 组已用的 Q1 ~ Q4；执行前先 `grep -n "DL-Q" docs/00-待裁定清单.md` 确认 Q5 起未被占用，被占用就顺延。）

- [ ] **Step 5: 文档自检**

Run: `cd docs/prototype && node test_doc_flow.js && node test_retired_claims.js && node test_doc_index.js`
Expected: 三条都以 `✓` 结尾、退出码 0（§2.0b 不在 §2.5 流程表里；若 `test_retired_claims` 报新违规，看是不是新行里写了 `00c` §5 登记过的作废说法，按它改写）。

- [ ] **Step 6: 全量回归**

Run: `JXD_SCM_CONFIG=/nonexistent uv run pytest -q -m pure && uv run pytest -q tests/test_api_dispatch_plans.py tests/test_api_sheets.py tests/test_web_contract.py && cd web && npx vitest run && npx tsc --noEmit`
Expected: 全部 passed；tsc 无输出。

- [ ] **Step 7: Commit（先过闸 A）**

```bash
git add web/src/styles/dispatch.css web/src/styles/tokens.test.ts "docs/10-前端交互界面.md" "docs/00-待裁定清单.md"
git commit -m "$(cat <<'EOF'
docs(dispatch): §2.0b /dispatch 改成计划抬头 + 透视小表；登记实施时发现的待裁定；修 dispatch.css 孤立注释

- dispatch.css:11-12 注释尾少 /*，.gl-list--one 整条失效（评审 Minor）；tokens.test 加注释成对检查
- docs/10 §2.0b 照 DL-D1~D10 重写 /dispatch 一行
- docs/00 DL 组登记 Q5~Q10（N 的单位 · 补锁/提交先后 · 同格多记录 · 不命中兜底 · 详情在途 · 旧版混合 chip）

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review（写完后对着 spec 过一遍）

**Spec 覆盖：**

| spec | 任务 |
|---|---|
| §2 抬头字段 · 还差最大字号 · 已满足 | Task 3（`PlanBlock`，`ds-kpi--left`） |
| §2 排序 `plan_id DESC` 不变（DL-D4） | 不改 `repo.dispatch_plans` 的 ORDER BY；Task 3 按响应顺序渲染 |
| §2 默认收起 · 点抬头 / Enter（DL-D5） | Task 3（标题 `<button aria-expanded>` 原生 Enter / Space；`onHead`） |
| §2 每计划一张小表 · 货号行按还差合计降序（DL-D2） | Task 2 `pivotRecords` · Task 3 `PivotTable` |
| §2 格子 · 「—」· 悬停 / 读屏全量（DL-D6） | Task 2 `cellTitle` · Task 3（`title` + `.dh-sr`） |
| §2 月份只列有记录的月（DL-D10） | Task 2 |
| §2 动作主次 · 横排 · 已满足不给（CA-D13 · P1） | Task 3 |
| §2 全部排货表折叠 · 张数 · 已取消（DL-D9） | Task 3 `AllSheets` |
| §2 搜索 · 自动展开 · 高亮 · 无命中文案（DL-D10） | Task 2 `filterPlans/skuHit` · Task 3 |
| §3 品名单行省略 · `.wrap` 1320 保留 | Task 3 `.dh-name`；`.wrap` 不动 |
| §4 十二种情形 · 优先级 · +N · 色 | Task 2 `sheetNextStep` / `TONE_CLASS` |
| §4 不显示闸未过 N（不读 `last_gate_run`） | Task 1 不查 `sheet_gate_run` |
| §4「正在提交…」详情页同步（DL-D8） | Task 1（`load` 带 `in_flight`）· Task 4 |
| §5 九个 sheet 字段 · 记录 confirmed/ordered · 只读 PG | Task 1 |
| §5 耗时日志 · 失败点名不静默 | Task 1 `_part` / `timed` / `log.info op=dispatch_plans` |
| §5 受影响测试 · 契约样本 · docs/08 | Task 1 Step 7 ~ 10 |
| §6 读取中 · 读失败各自点名 · 无计划 · 全部已满足 | Task 3（沿用 `ErrorDetail`、两块独立加载） |
| §7 `DispatchHome.test.tsx:85-98` 改按 货号 × 月格子定位 | Task 3（`cell-<plan>-<sku>-<月>`） |
| 评审 Minor `dispatch.css:11-12` · 漏 `table` 基类 · `.r` · 行内 `marginRight` | Task 5 · Task 3 |
| `docs/10` §2.0b | Task 5 |

**占位扫描：** 无 TBD / TODO；每个代码步骤给了完整代码。

**类型一致：** `DispatchPlanSheet` 九字段在 Task 1（Python 键名 / TS 类型）、Task 2（`S` 夹具）、Task 3（`SH` 夹具）三处同名同型；`sheets_open_attention` 在 Task 1 实现与失败测试的 monkeypatch 目标一致；`NextStep.unmatched` 为必填 boolean（`exactOptionalPropertyTypes`）。

**Review Focus：** 五条各有测试 —— ①同格多记录（Task 2）②抬头内按钮不切换（Task 3，并在 Step 6 证伪）③搜索大小写 / 空格 / 清空收回（Task 2 + Task 3）④多命中 +N 与在途最先（Task 2，Step 5 证伪）⑤不命中有声（Task 2 + Task 3 `console.warn`）。
