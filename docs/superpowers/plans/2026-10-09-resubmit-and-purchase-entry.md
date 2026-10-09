# 计划再提交 · 提交结果显示 · 采购入口 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 改了计划直接再提交（没变的记录保留）；提交结果不再把「没填采购量」显示成红色错误；采购首页第一屏列待采购记录，勾选即建采购单，来源一路可见。

**Architecture:** 再提交在一个事务里先处置上一版的活记录（保留 / 撤销）再立新版，S-4「只 1 版在流转」不变；跳过原因拆 `empty_purchase` / `zero_purchase`（迁移 013）。前端提交结果页按 R1 重排；采购首页加待采购清单，编辑器接受 `?lines=` 预选并显示来源链。

**Tech Stack:** Python 3.13 / FastAPI / PostgreSQL（迁移只追加）；React 18 + Vite + TS / vitest

**Spec:** `docs/superpowers/specs/2026-10-09-resubmit-and-purchase-entry-design.md`（R1–R3 负责人裁定；D1–D5 我的裁定）

## Global Constraints

- 迁移只追加新文件 `013_*.sql`，已执行的 001–012 不改；不手工 psql 改结构；`scm_test` 由测试夹具建。
- S-4 不变量：任一时刻一个计划只有一组活记录、最多 1 版在流转；承重墙求和不得翻倍。
- 只追加表（`plan_line_event`、`plan_submit_skip`）禁 UPDATE/DELETE。
- 产品无假模式（PB-D17）；日志三问；空 ≠ 0；界面只给数据，说明进折叠区。
- 提交只 add 具体路径；不碰 `web/package.json`、`config.toml`；一次一个 pytest、前台；提交信息写到 `.superpowers/sdd/2026-10-09-resubmit-and-purchase-entry/` 下的文件。

## Review Focus

1. 再提交时上一版有一条记录已在采购草稿里、且这次它的量变了 → 409 `lines_in_purchase` 点名采购单号，**什么都没改**（事务整体回滚）。
2. 再提交内容与上一版完全相同 → 全部保留、0 替换 0 新增；新版仍立起来（或按 D5 明确给出「无变化」），不产生重复活记录。
3. 承重墙核对视图（`v_check_*`）在再提交前后都是 0 违规。
4. 采购首页待采购清单为空（没有已提交记录）→ 写「没有待采购的记录」并给去销售计划的链接，不是空白。
5. `?lines=` 带了一条已不可组单的记录 → 编辑器点名它，不静默丢掉。

---

### Task 1: 后端 —— 再提交保留未变记录 + 跳过原因拆分

**Files:** Create `migrations/pg/013_resubmit_and_skip_reason.sql`（`plan_submit_skip.reason` CHECK 加 `empty_purchase`；再提交需要的任何约束 / 函数调整，如记录换版所需）· Modify `rules/submit.py`（`None` → `empty_purchase`，`0` → `zero_purchase`；新增纯函数对比上一版活记录与本次可铸记录 → keep / replace / add / remove）· `api/ui/submit.py`（`submit()`：先锁上一版活记录 `FOR UPDATE`，按 D1 守卫，处置后再立新版；响应加 `kept / replaced / added / removed` 计数与明细）· tests

- [ ] Step 1: 规则层纯函数测试（四种情形 + D2 分店拆分变化算「变」）先红
- [ ] Step 2: API 测试先红：① 不撤销直接再提交成功；② 没变的记录 `line_id` 不变、状态不变；③ 变了的旧记录已撤销且事件有 actor/理由/src；④ D1：被采购草稿引用的记录要变 → 409 点名、库里无任何变化；⑤ 已下单的同理；⑥ 内容完全相同的再提交；⑦ 承重墙 `v_check_*` 0 违规；⑧ 空采购量记 `empty_purchase`、0 记 `zero_purchase`
- [ ] Step 3: 迁移 013 + 实现；`./replay.sh` 或现有迁移测试覆盖 013
- [ ] Step 4: 全量 pytest（前台单进程；仅已知 `test_every_amazon_pair_in_ch_is_declared` 失败）；ruff
- [ ] Step 5: 提交

### Task 2: 前端 —— 提交结果页（R1 + D5）

**Files:** `web/src/pages/` 下提交结果所在页面（grep「铸出」「跳过」）· `web/src/api/types.ts`（submit 响应新字段、新 reason）· `web/src/testing/` 替身同步 · tests

- 「采购量为空 N 个」「采购量为 0 N 个」各一行灰字，明细进 `<Fold>` 默认收起；红色只给 `no_claimed_msku`。
- 显示「保留 N · 替换 N · 新增 N · 去掉 N」（不用 ` · ` 拼接文案的话按 copyDiscipline 改成分列）；替换 / 去掉明细进折叠。
- 409 `lines_in_purchase`：逐条点名 货号 · 月 · 状态 · 采购单号（链接到 `/purchase/{po_id}`）。
- 结果页加链接「去采购（N 条待采购）」→ `/purchase`。
- 「提交」按钮在已有一版在流转时不再要求先撤销（删除对应前端拦截 / 提示）。

- [ ] Step 1: 测试先红 · Step 2: 实现 · Step 3: tsc + 全量 vitest · Step 4: 提交

### Task 3: 前端 —— 采购首页待采购清单 + 编辑器预选与来源链（R3）

**Files:** `web/src/pages/purchase/PurchaseHome.tsx`（顶部待采购清单：计划号 · rev · 货号 · 月 · 量 · 还可组 · 各店；无分店依据置灰点名；勾选 +「用选中的 N 条建采购单」）· `PoEditor.tsx` / `poEditorModel.ts`（读 `?lines=`：左栏只列这些并展开，顶部「来自 销售计划 X · revN 的 N 条记录」；不可组单的点名；右栏来源链 `计划X·revN·货号·月 → 店 量`）· 后端若 `/v1/plan-lines` 缺 rev / 计划名字段则在 `api/ui/lines.py` 补（带测试与契约快照 `tests/test_web_contract.py` 更新）· tests

- [ ] Step 1: 测试先红（含 Review Focus 4、5）· Step 2: 实现 · Step 3: tsc + 全量 vitest + 契约 pytest · Step 4: 提交

### Task 4: 文档与裁定登记

**Files:** `docs/00-待裁定清单.md`（新增裁定行：R1–R3 原文 + D1–D5，标注 S-4 不变量保留、S-14 加值）· `docs/03`（`plan_submit_skip.reason` 值域）· `docs/04`（再提交的状态处置；D3 说明）· `docs/06` §1.2（跳过显示）· `docs/08`（submit 响应、409 `lines_in_purchase`）· `docs/10`（提交结果页、采购首页第一屏）

- [ ] Step 1: 改文档 · Step 2: 四项文档门禁全绿 · Step 3: 提交
