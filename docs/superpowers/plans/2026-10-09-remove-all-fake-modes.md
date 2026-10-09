# 全系统去掉假模式 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 产品只剩一条真路径 —— 前端只打真后端，后端预测只读真 ClickHouse，下单只打真领星。前端 `mock` 数据源与后端 `[forecast] source = fixture` 两个假模式（且默认都是假）整体删除；替身只活在测试目录，产品代码 import 不到。

**Architecture:** 前端 `api/index.ts` 只装配 `createHttpApi`；`mock.ts` 平移为测试专用的内存后端 `web/src/testing/memoryBackend.ts`，测试经 `vi.doMock('../api', …)` 注入。后端 `make_source()` 只造 `ChSource`；`FixtureSource` 平移到 `tests/`，测试经 FastAPI `dependency_overrides[source_dep]`（或现有 `use_source` 夹具）注入。两侧各加一条守门测试：产品源码出现假模式字样 / import 测试目录即红。

**Tech Stack:** Python 3.13 / FastAPI / pytest；React 18 + Vite + TS / vitest

**Spec:** 负责人裁定（2026-10-09，对话原文，见下）；前序 `docs/superpowers/plans/2026-10-08-remove-fake-mode.md`（PB-D14 只删了 `[erp] mode`）

## 负责人裁定（2026-10-09，对话原文）

> 「我们整个系统，不需要 live, fake 等假模式，这些模式只会带来复杂性！本身我们就是在开发阶段，并不会正式上线，要修改领星 ERP 的数据，也是小批量的测试！如果你加入了 live, fake 等模式，会导致开发阶段其实就出现了问题，但是我们发现不了！」

- **PB-D17**：全系统无假模式。推翻 Q-6（前端 `mock | api` 可切换）与 `[forecast] source = "fixture"`（含其「默认 fixture」）。
- 不在本计划内（不是假模式，原样保留并在文档注明）：`[auth] mode = dev`（M2 前唯一的身份来源，PB-D15）、`scheduler_enabled`（作业开关，不伪造任何数据）。

## Global Constraints

- 产品代码（后端 `api/ usecase/ repo/ rules/ erp/ jobs/ shared/ dim/ forecast/`；前端 `web/src/` 除 `*.test.*`、`web/src/testing/`、`web/src/e2e/`）不得出现：`mock` 数据源、`VITE_DATA_SOURCE`、`not_in_mock`、`FixtureSource`、`source = "fixture"`、`fake`、`simulate`。
- ★ 留着旧开关的配置必须**启动即拒**并点名（同 PB-D14 对 `[erp] mode` 的做法）：`config.toml` 里还写着 `[forecast] source` → 拒绝启动；`VITE_DATA_SOURCE` 仍被设置 → 前端启动抛错。理由：留着旧值的人以为自己在跑假数据。
- ★ 测试永远不碰真 CH / 真领星：后端沿用 autouse 替身注入；前端测试只经 `web/src/testing/` 的替身或 `stubBackend`。
- 测试 CSV 底料留在 `tests/fixtures/`（它是测试数据，不是模式）。
- 不新增迁移；提交只 add 具体路径；不碰 `web/package.json`；一次一个 pytest，前台跑；日志三问。
- 提交信息写到 `.superpowers/sdd/2026-10-09-remove-all-fake-modes/` 下的文件，不写 `/tmp`。

## Review Focus

1. 本机 `config.toml` 写着 `source = "ch"`（负责人机器上的真实值）→ 删键后必须照常启动；写着 `source = "fixture"` 或任何值 → 拒绝启动并点名。
2. 测试配置从真实 `config.toml` 生成（`tests/conftest.py` `_FORECAST_TEST_PINS`）—— 删掉钉子后，离线测试不得因此去连生产 CH（2026-09-28 出过 23 条）。
3. 前端 `npm run dev` 不设任何环境变量 → 直接打 `/v1`（经 vite 代理到 8091）；后端没起 → 页面显示「连不上」与 cause，不是空白、不是假数据。
4. 原先只在 mock 下成立的界面分支（`not_in_mock` 501、mock 专属文案）全部消失，不留死代码。
5. 守门测试先弄失败一次（往产品源码塞一个禁用词 → 红）。

---

### Task 1: 后端 —— 删 `[forecast] source` 与产品侧 `FixtureSource`

**Files:** Modify `api/ui/source_factory.py`（只造 `ChSource`；删 `KINDS` / `_kind` / fixture 分支；`log_source_config` 只打 ch 配置）· `api/__init__.py`（启动闸：`[forecast]` 里有 `source` 键 → `RuntimeError` 点名 PB-D17）· `shared/config.py` 默认值 · `config.example.toml`（删 `source` 行与说明）· Move `dim/fixture_source.py` → `tests/fixture_source.py` · `tests/conftest.py`（删 `_FORECAST_TEST_PINS` 的 source 钉子；改为 autouse 注入 `FixtureSource` 到 `source_dep`，live 测试显式换回真源）· 所有 import `dim.fixture_source` 的测试 · 产品代码里提到 fixture 档的注释 · 扩展 PB-D14 的守门测试（产品包源码禁 `FixtureSource` / `fixture_source` / `source = "fixture"`）

- [ ] Step 1: 守门测试 + 启动闸测试（留 `source` 键 → 拒绝；无键 → 起得来）先跑红
- [ ] Step 2: 平移 `FixtureSource` 到 `tests/`，conftest 注入；此时全量应仍绿
- [ ] Step 3: 删产品侧分支、配置键、注释
- [ ] Step 4: 全量 pytest（前台，单进程；仅已知 `test_every_amazon_pair_in_ch_is_declared` 失败）；ruff
- [ ] Step 5: 提交

### Task 2: 前端 —— 删 `VITE_DATA_SOURCE` 与产品侧 mock

**Files:** Modify `web/src/api/index.ts`（只 `createHttpApi`；`VITE_DATA_SOURCE` 仍被设置 → 启动抛错点名 PB-D17）· Move `web/src/api/mock.ts` → `web/src/testing/memoryBackend.ts`（及其 fixtures 引用；`mock.test.ts` 随迁或删其中只测 mock 行为的用例）· 所有用 `createMockApi` 的测试改为从 `web/src/testing/` 取 · 删产品代码中 mock 专属分支（`poStatus.ts` 的 `not_in_mock` / 501 例外、`PlaceDialog` 的 mock 文案及其测试）· `web/.env.example` · `web/vite.config.ts`（若有 mock 相关）· 产品代码里提到 mock 的注释 · 新增守门测试 `tests/test_layering_web.py` 一条：非测试源码出现禁用词或 import `src/testing/` 即红

- [ ] Step 1: 守门测试先跑红
- [ ] Step 2: 平移 mock → `testing/memoryBackend.ts`，测试改 import；此时 vitest 应仍绿
- [ ] Step 3: `index.ts` 只留真路径 + 旧变量拒绝；删 mock 专属分支
- [ ] Step 4: `npx tsc --noEmit`、全量 vitest、`pytest tests/test_layering_web.py`
- [ ] Step 5: 提交

### Task 3: 文档与裁定登记

**Files:** `docs/00-待裁定清单.md`（PB 组加 PB-D17，标注推翻 Q-6 与 `[forecast] source` 默认 fixture；原话）· 项目 `CLAUDE.md`（「前端 `mock | api` 可切换」一行改为 PB-D17；「原型里人工输入两种」一行只属于已冻结原型，保留但注明）· `docs/00d-前端并入本仓改造计划.md`（Q-6 段）· `web/README.md`（删 mock 档，起服务只剩一种）· `docs/16-系统边界与外部模拟.md`（产品不模拟外部；模拟只在测试）· 其余文档里提到 `source = "fixture"` / `VITE_DATA_SOURCE=mock` 的地方（grep）

- [ ] Step 1: 改文档 · Step 2: 文档门禁（`test_doc_flow` `test_retired_claims` `test_mermaid` `test_doc_index`）全绿 · Step 3: 提交
