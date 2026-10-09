# 去掉 fake 模式 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 产品代码只剩一条真路径（草稿 → ⛔ 下单打真领星 → 已下单）；fake 模式、模拟外部、所有按模式分叉的逻辑全部删除；领星替身只存在于 `tests/`。

**Architecture:** `erp/gateway` 永远用 `LiveDriver`；测试由 `tests/conftest.py` 的 autouse 夹具把网关的 driver 换成 `tests/lingxing_double.py`，并让任何测试里实例化真 driver 直接失败。迁移 010/011 原地改（`scm` 只应用到 009，已只读核实）。

**Spec:** `docs/superpowers/specs/2026-10-07-purchase-order-design.md`（本计划推翻其 D4 与 §12.2 硬闸，见下方裁定）

## 负责人裁定（2026-10-08，对话原文）

> 「我们的系统不需要 fake 模式，只要有草稿模式即可。我们需要的是纯净且高效的系统，不是 fake 模式只会让逻辑变复杂，之后你调整更容易出错。」
> 问「M2 接好 RBAC 之前下单怎么处理」→ 选「现在就允许真下单」。

- **PB-D14**：删除 fake 模式（推翻 PB-D4）。
- **PB-D15**：下单接口现在就打真领星，身份沿用开发下拉（推翻 spec §12.2「live + dev 拒绝启动」）。★ 已知风险由负责人接受：RBAC（M2）之前，内网任何人可以任意身份真下单。

## Global Constraints

- 迁移只在 010/011 原地改（未上 `scm`）；不新增 013。
- 产品代码（`api/ usecase/ repo/ rules/ erp/ jobs/ shared/ dim/ migrations/`）里不得再出现 fake / simulate / `placed_mode` / `erp_call.mode` / `source='sim'`。
- ★ 测试永远打不到真领星：autouse 夹具注入替身 + 测试内实例化 `LiveDriver` 即失败。
- 启动闸：`app_id` / `app_secret` / `proxy_host` 缺失或 `confirm_salt` 为空 / 开发默认值 → 启动失败；`[auth] mode = rbac` 仍拒绝（M2 未实现）；不再有 live+dev 拒绝。
- 其余沿用 M1 计划的 Global Constraints（提交只 add 具体路径、不碰 `web/package.json`、一次一个 pytest、日志三问）。

---

### Task 1: 删除 fake 模式（代码 + 迁移 + 测试，一次绿）

**Files:**
- Modify: `migrations/pg/010_po.sql`（删 `placed_mode` 列与 CHECK；`sync_po_state` 转已下单只要求 `order_sn`）· `migrations/pg/011_erp_call_obs.sql`（删 `erp_call.mode`；`po_snapshot` / `po_item_obs` 的 `source` CHECK 改为 `('ch','api')`）
- Modify: `shared/config.py`（删 `[erp] mode`）· `api/__init__.py`（启动闸按上面 Global Constraints；删模拟路由挂载）· `erp/gateway.py`（只用 `LiveDriver`，删 `mode()`）· `repo/purchase.py`（`insert_call` / `mark_placed` 去掉 mode）· `usecase/place_po.py` `usecase/landed.py` `usecase/attention.py`（去掉 `gateway.mode()`、`_obs_source`，观察来源固定 `api`）· `config.example.toml`（删 `mode` 行）
- Delete: `erp/drivers/fake.py` · `api/ui/simulate.py` · `tests/test_api_checks_simulate.py` 中仅测模拟接口的用例
- Create: `tests/lingxing_double.py`（由原 FakeDriver 平移、改名 `LingxingDouble`，接口不变：`enqueue / reset / mutate_item / call`）
- Modify: `tests/conftest.py`（autouse 夹具：`monkeypatch` 网关 driver 工厂为替身；`LiveDriver.__init__` 在测试中抛 `RuntimeError("测试禁止实例化真领星 driver")`；删 `[erp] mode` 钉子）· 所有 `from erp.drivers.fake import FAKE` 的测试改为从替身夹具取 · `tests/test_harness_pins.py` `tests/test_outbound_config.py` 同步
- Create: 一条守门测试：扫产品包源码，出现 `fake` / `simulate` / `placed_mode` 字样即红（允许清单为空）

- [ ] Step 1: 先写守门测试与「测试内实例化 LiveDriver 即失败」测试，跑红
- [ ] Step 2: 建 `tests/lingxing_double.py` + conftest 注入，现有测试改用替身（此时产品代码仍含 fake，测试应仍绿）
- [ ] Step 3: 删产品代码中的 fake / 模拟 / mode，改迁移 010/011
- [ ] Step 4: 原「模拟改量 → 落地核对」用例改为：测试里直接 `double.mutate_item(...)` 再调 `landed.verify(...)`；保留其断言意图
- [ ] Step 5: 启动闸测试：缺凭证 / 缺代理 / 盐为默认值 → 拒绝；rbac → 拒绝；齐全 + dev → 起得来
- [ ] Step 6: 全量（前台，仅已知 live 失败）；ruff；按逻辑分 2~3 个提交

### Task 2: 文档与裁定登记

**Files:** `docs/00-待裁定清单.md`（PB 组追加 PB-D14 / PB-D15，标注推翻 PB-D4 与 §12.2；风险原文）· spec（§6.4 删、§11 G2 模拟外部删、§12.2 硬闸改、§13 加两条）· `docs/03`（删 `placed_mode` / `erp_call.mode` / `sim`）· `docs/07` `docs/08`（删模拟接口、fake 说明）· `docs/16`（采购段「模拟外部」不再适用的说明，一行）

- [ ] Step 1: 改文档 · Step 2: 文档门禁四项全绿 · Step 3: 提交
