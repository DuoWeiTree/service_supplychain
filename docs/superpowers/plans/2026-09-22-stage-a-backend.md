# 阶段 A · 销售计划（后端）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `service_supplychain` 的销售计划链路（建计划 → 认领 msku → 填两种量 → 提交铸出 rev → 版本与撤销）实现成可跑、可测、约束全在库层的后端，并交出前端 mock 与真 API 同源的 `GET /grid` fixture。

**Architecture:** 三层纯度分离 —— `forecast/`（纯函数，只用标准库）· `rules/`（纯判据，只用标准库）· `dim/`（取数，阶段 A 只有 FixtureSource）· `api/ui/`（事务边界 + 状态码）· `migrations/pg/`（结构的唯一真源）。状态机做成数据（白名单表 + 外键），状态只能由事件表推进；独占、唯一在流转版本、非法迁移全部由库层裁决，接口只负责把库层的拒绝翻译成点名的错误码。

**Tech Stack:** Python 3.11+ · FastAPI ≥0.115 · uvicorn ≥0.30 · psycopg2-binary ≥2.9 · PostgreSQL（`192.168.66.210` · `jxd_ml` · schema `scm`，测试用 `scm_test`）· pytest ≥8 · ruff · uv

**Spec:**
- `docs/prototype/.stage-a-backend-spec.md`（规格抽取；它的 §5 缺口表已被下面的 S 组裁定取代）
- `docs/00-待裁定清单.md` **S 组「★ 全部已裁定（2026-09-22）」** —— 本计划每一处设计取值都指向它
- `docs/00e-分阶段实施计划.md` §1 阶段 A（六条校验判据）
- `docs/03-数据库表清单.md` §2 · §3 · §8.1 · `docs/04-状态流转.md` S1 · `docs/08-后端接口清单.md` §0 / §1.1 / §3 · `docs/01-架构设计.md` §3.1 · `docs/14-库存预测数学模型.md` §0~§5

---

## Global Constraints

逐字生效于每一个 Task，不在每个 Task 里重复：

- **日志三问**：打的谁（host/path/op）· 多久（`elapsed_ms`）· 怎么失败的（状态码或 cause code）。**catch 里绝不只取 `e.message`** —— PG 侧的等价物是 `e.pgcode` / `e.diag.constraint_name` / `e.diag.message_primary`，缺了它「唯一冲突」和「外键冲突」长得一模一样。
- **静默兜底是最坏的一种**：回退可以，但必须有声。**空 ≠ 0**；**「不适用」≠ 0**；**「没有」≠「没算进来」**。
- **状态只能由事件表推进**；只追加的表禁 UPDATE/DELETE（`forbid_update_delete()`）。
- **代码注释只写「为什么」**（尤其反直觉处），不复述代码在干什么；不写冗余功能描述。
- **测试写完先弄失败一次** —— 每个 Task 的步骤里显式体现：先写测试 → 跑红 → 实现 → 跑绿 → 提交。
- **操作人来自请求头 `x-actor`**，必须存在于 `actor` 表且 `active`，否则 400 `unknown_actor`。
  （★ 与 `08` §0「`x-actor` 只记录不校验」不冲突：校验的是**这个人存不存在、在不在职**，是 `plan.owner_actor REFERENCES actor(actor_id)` 这条外键的兑现；「不校验」说的是**权限**，权限仍在上层。不校验存在性，外键会以 500 的形态在半路炸，而不是 400 点名。）
- **检查任何过滤 / join / 分类，必须同时统计被丢掉的那一侧**，并对未预期的排除硬失败。
- **每个数字**要么指到文档里的实测 n，要么标「设计取值 · 未实测」。
- **迁移文件一次且仅一次**，已执行的不可改（checksum 守）；不许手工 `psql` 改结构。
- **不许出现** TBD / TODO / 「类似 Task N」/「加适当校验」。

### 全局命名与格式（`03` §0 · `08` §0）

| 项 | 约定 |
|---|---|
| 店铺号 | ★ 一律 `text`（18 位，JS 会精度丢失）。`sid` 与 `seller.seller_id` 是同一个店铺号 |
| 时间 | `timestamptz`；事件序用 `bigserial`，**不用时间戳**（时钟回拨会乱序） |
| 月份 | 对外一律 `"YYYY-MM"` 字符串；库里是 `date` 且必须是月初 |
| 命名 | ★ 禁用领星词汇 `inbound` / `shipment` / `shipment_plan` / `allocation`；`order` 只给采购单，`plan` 只给销售计划 |
| 错误形状 | `{"code": "...", "message": "...", "detail": {...}}`，★ `detail` 必须点名是哪几行（`08` §0） |
| 状态码 | 400 = 你写错了 · 409 = 冲突与闸（你没写错，但现在不行）· 422 = 语义不合法（非法迁移）· 503 = 镜像陈旧 |
| 未声明查询参数 | ★ 一律 400 `unknown_query_param` |

★ **错误形状为什么不照邻居**：`service_social` 用的是 `{"error","hint"}`，而 `08` §0 是**本服务自己的**约定且更严（`detail` 必须点名哪几行），前端与 `jxd_ai_ops` 都按它写。机制照邻居（`HTTPException` 式的一次抛出），形状按 `08`。

### ★ 阶段 A 明确不做（每条带出处 —— 写下来是为了让「没做」和「做漏了」长得不一样）

| 不做 | 出处 |
|---|---|
| `reevaluate_plan_lines()` 再评估函数 | `04` §2.4 的判据全部依赖承重墙①②（`po_source_map` / `dispatch_source_map`，属阶段 B/C）。阶段 A 没有任何调用者 —— 写了也不会被执行，而**不执行的东西不会失败** |
| `已确认 → 已提交` 退回边的**触发** | S-11：触发源「撤组单」属阶段 B。阶段 A 只验「白名单建对 + 非法迁移被外键拒 + 终态不可离开」（库层直插事件） |
| 四段管道 / 批次分配 / `make_days`·`ship_days` | `00e`:44 阶段 A 不做管道；`00e`:43 阶段 A 的库存预估 = 在仓 + 采购在途 − 期望销量，**全是 CH 现成事实** |
| 预测落库（`03` §7 七张表） | S-19 · `09`:279 · `08`:126 —— 不落库 |
| `x-internal-key` 鉴权与它的启动校验 | Q-5「认证暂不做，内网裸跑」；`08` §0 的鉴权属 `api/pub/`，阶段 A 不实现 `api/pub/` |
| `service.toml` | 同上：没有 `api/pub/` 就没有要注册的契约 |
| `GET /v1/plan-lines/{line_id}/trace` | 承重墙①②反查，两张映射表属阶段 B/C |
| `erp/` · `jobs/` 的内容 | 阶段 A 无出站写、无回执器。★ 目录按 `01` §7 先立空包，供分层测试扫到 |

---

## File Structure

```
service_supplychain/
├── pyproject.toml                      ★ uv · 依赖 · packages · pytest 配置
├── config.example.toml                 ★ 已存在，本计划追加 [api] 与 [freshness]
├── shared/
│   ├── config.py                       tomllib + JXD_SCM_CONFIG 覆盖 + reset_cache()
│   └── pg_client.py                    一次一连 · search_path · 错误取证 · timed()
├── migrations/pg/
│   ├── apply.py                        ★ schema_migration(version, checksum) + 核对
│   ├── 001_foundation.sql              actor + 四张镜像 + forbid_update_delete + v_mirror_freshness
│   ├── 002_plan.sql                    plan · 两张格子 · msku_claim · plan_rev · plan_line · skip
│   └── 003_plan_state_machine.sql      白名单（[*] 哨兵）· 事件表 · 五个函数 · 五个触发器
├── forecast/                           ★ 纯函数，只用标准库
│   ├── estimate.py                     monthly_estimate
│   └── projection.py                   inventory_projection
├── rules/                              ★ 纯判据，只用标准库
│   ├── effective.py                    effective_demand（human / system / unknown）
│   ├── submit.py                       select_submittable（Minted / Skipped）
│   └── digest.py                       content_digest（S-13）
├── dim/
│   ├── source.py                       Source 协议 + InTransit + 异常
│   ├── fixture_source.py               读 tests/fixtures/*.csv
│   └── ch_source.py                    ★ 显式 NotImplementedError，不是空实现
├── api/
│   ├── __init__.py                     create_app()
│   └── ui/
│       ├── errors.py                   ApiError + handler + PG 约束名 → 错误码
│       ├── deps.py                     actor · declared_params · fresh_mirrors
│       ├── catalog.py                  GET /v1/catalog/skus
│       ├── plans.py                    建 / 列 / 认领 / 网格 / 两种 PUT / archive
│       ├── submit.py                   提交 / revs / current / diff / 整版撤销
│       ├── lines.py                    GET /v1/plan-lines · 单条撤销
│       └── dashboard.py                /v1/dashboard/plans · /v1/dashboard/unsubmitted
├── erp/__init__.py                     ★ 空包（阶段 A 无出站写）
├── jobs/__init__.py                    ★ 空包（阶段 A 无回执器）
└── tests/
    ├── conftest.py                     scm_test 夹具 · 禁止 scm/inv · 每测试 TRUNCATE
    ├── test_layering.py                ★ L1~L7 + 反空转 + 新层目录守卫
    ├── test_migrations.py              checksum · 无事务控制 · 版本表
    ├── test_ddl_001_foundation.py      约束挡得住（逐条断言 psycopg2.errors.*）
    ├── test_ddl_002_plan.py            同上
    ├── test_ddl_003_state_machine.py   T1 / T2 / T3 / T6 / T21 + 铸出事件
    ├── test_forecast_estimate.py       ★ 原型 test_forecast.js §9 逐格移植
    ├── test_forecast_projection.py     ★ 原型 test_forecast.js §4 / §5 逐格移植
    ├── test_dim_fixture.py             空 ≠ 0 · CH 实现必须显式未做
    ├── test_rules_submit.py            跳过理由 · 生效值 basis · digest
    ├── test_api_plans.py / _claims.py / _grid.py / _submit.py / _lines.py / _dashboard.py
    ├── test_grid_fixture.py            ★ 固化 GET /grid 响应，供前端 mock 同源
    ├── test_stage_a_criteria.py        ★ 判据 ①~⑤ 各一条端到端
    └── fixtures/                       *.csv（dim 底料）+ grid_response.json
```

---

## Task 1: 脚手架与分层测试（先红）

**Files:**
- Create: `pyproject.toml`
- Create: `tests/test_layering.py`
- Create: `shared/__init__.py` · `rules/__init__.py` · `forecast/__init__.py` · `dim/__init__.py` · `api/__init__.py` · `api/ui/__init__.py` · `erp/__init__.py` · `jobs/__init__.py` · `migrations/__init__.py` · `migrations/pg/__init__.py`

**Interfaces:**
- Consumes: 无（第一个 Task）
- Produces: 包布局 `api` `api.ui` `shared` `rules` `forecast` `dim` `erp` `jobs` `migrations` `migrations.pg`；`tests/test_layering.py`，此后每个 Task 都在它的约束下写代码

- [ ] **Step 1: 写失败的分层测试**

`tests/test_layering.py`：

```python
"""分层由测试强制（01 §3.1 L1~L7）。

★ 每条规则都要断言「扫到的文件不是 0 个」——
  扫 0 个文件的规则永远是绿的，而它看起来和真的守住了一模一样。
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: 按 01 §7 的目录结构。api/ui 是子包，随 api 一起扫到。
PACKAGES = ["api", "dim", "erp", "forecast", "jobs", "migrations", "rules", "shared"]

DB_CLIENTS = {"psycopg2", "clickhouse_connect", "clickhouse_driver"}
HTTP_CLIENTS = {"httpx", "requests", "urllib3", "aiohttp"}


def sources(*rel: str) -> list[Path]:
    out: list[Path] = []
    for r in rel:
        out += sorted((ROOT / r).rglob("*.py"))
    assert out, f"{rel} 下一个 .py 都没扫到 —— 这条规则是空转的"
    return out


def imports(path: Path) -> set[str]:
    """只收绝对 import 的顶层模块名。"""
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text("utf-8"))):
        if isinstance(node, ast.Import):
            out |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module.split(".")[0])
    return out


def test_no_relative_imports():
    """相对 import 会从上面那个 imports() 里漏出去 —— 漏出去的那条不会报错。"""
    bad = []
    for p in sources(*PACKAGES):
        for node in ast.walk(ast.parse(p.read_text("utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.level:
                bad.append(f"{p.relative_to(ROOT)}:{node.lineno}")
    assert not bad, f"相对 import 会绕过分层扫描：{bad}"


def test_l1_rules_is_pure():
    forbidden = DB_CLIENTS | HTTP_CLIENTS | {"api", "dim", "erp", "jobs", "fastapi", "starlette"}
    bad = {p.name: sorted(imports(p) & forbidden)
           for p in sources("rules") if imports(p) & forbidden}
    assert not bad, f"L1 违规（判据必须离线可测）：{bad}"


def test_l2_forecast_only_stdlib_and_rules():
    allowed = set(sys.stdlib_module_names) | {"rules"}
    bad = {p.name: sorted(imports(p) - allowed)
           for p in sources("forecast") if imports(p) - allowed}
    assert not bad, f"L2 违规（移植来的算法必须可独立回归）：{bad}"


def test_l3_dim_knows_nothing_about_api_or_erp():
    bad = {p.name: sorted(imports(p) & {"api", "erp", "jobs"})
           for p in sources("dim") if imports(p) & {"api", "erp", "jobs"}}
    assert not bad, f"L3 违规：{bad}"


def test_l4_l5_outbound_http_only_in_erp():
    others = [x for x in PACKAGES if x != "erp"]
    bad = {str(p.relative_to(ROOT)): sorted(imports(p) & HTTP_CLIENTS)
           for p in sources(*others) if imports(p) & HTTP_CLIENTS}
    assert not bad, f"L5 违规（出站写只能在 erp/）：{bad}"


def test_l6_psycopg2_only_where_transactions_live():
    """★ L6 的原文是「psycopg 连接只在 api/ 与 jobs/」。

    这里放宽到「连接工厂本身 + 事务边界所在层 + 迁移」：连接工厂必须有一个地方实现
    （shared/pg_client.py），把它一起禁掉等于禁掉整条链路。真正要防的是
    rules/ forecast/ dim/ erp/ 里出现连接 —— 那几层一旦连库就不再离线可测。
    """
    allowed = ("api/", "jobs/", "migrations/", "shared/pg_client.py")
    bad = [str(p.relative_to(ROOT)) for p in sources(*PACKAGES)
           if "psycopg2" in imports(p) and not str(p.relative_to(ROOT)).startswith(allowed)]
    assert not bad, f"L6 违规：{bad}"


def test_l7_no_writes_to_clickhouse():
    write = re.compile(r"(?i)\b(insert\s+into|alter\s+table|drop\s+table|create\s+table)\b")
    bad = [str(p.relative_to(ROOT)) for p in sources("dim") if write.search(p.read_text("utf-8"))]
    assert not bad, f"L7 违规（CH 只读）：{bad}"


def test_new_layer_dirs_bring_their_own_rules():
    """★ web/ 与 api/pub/ 一出现，L8~L10 就必须有人写。

    它们属「前端并入本仓」（00d Q 组）那份计划。这里不替它们写规则，
    只保证「新层悄悄出现而没有任何规则守着」这件事会红。
    """
    if (ROOT / "web").exists() or (ROOT / "api" / "pub").exists():
        assert (ROOT / "tests" / "test_layering_web.py").exists(), (
            "出现了 web/ 或 api/pub/，但 L8~L10 的测试还没写")


def test_every_declared_package_exists():
    missing = [p for p in PACKAGES if not (ROOT / p / "__init__.py").exists()]
    assert not missing, f"01 §7 声明了但不存在的包：{missing}"
```

- [ ] **Step 2: 跑测试，确认它红**

Run: `cd /home/fido/work/2026/jxd_service_group/service_supplychain && python -m pytest tests/test_layering.py -q`
Expected: FAIL —— `sources()` 里 `assert out` 失败（`rules` 下一个 `.py` 都没有），`test_every_declared_package_exists` 列出全部 8 个包。

- [ ] **Step 3: 建包与 pyproject**

十个 `__init__.py`，其中六个带一行为什么：

```python
# rules/__init__.py
"""纯判据。★ 只用标准库 —— 判据必须离线可测，否则测一条判据要起一套环境（01 §3.2）。"""
```
```python
# forecast/__init__.py
"""预测算法。★ 只用标准库 + rules —— 从老原型 store.js 移植，要能独立回归（L2）。"""
```
```python
# dim/__init__.py
"""取数。CH 只读；阶段 A 只有 FixtureSource，真 CH 实现显式未做。"""
```
```python
# erp/__init__.py
"""领星网关：唯一出站写入口。★ 阶段 A 无出站写，本包刻意为空 ——
目录先立着，是为了让分层测试从第一天就扫得到它。"""
```
```python
# jobs/__init__.py
"""回执器与定时。★ 阶段 A 无回执器（判据依赖承重墙①②，属阶段 B/C），本包刻意为空。"""
```
```python
# shared/__init__.py
"""配置与公共类型。"""
```
其余四个（`api/__init__.py` 先留空，Task 9 填 `create_app`；`api/ui/__init__.py`、`migrations/__init__.py`、`migrations/pg/__init__.py` 空文件）。

`pyproject.toml`：

```toml
[project]
name = "jxd-scm"
version = "0.1.0"
description = "供应链后端服务 —— 销售计划 → 采购单 → 排货 → 投送 → 回执"
requires-python = ">=3.11"          # tomllib
dependencies = [
    "fastapi>=0.115", "uvicorn>=0.30", "psycopg2-binary>=2.9",
]

[dependency-groups]
# httpx 是 starlette.testclient 的运行期依赖，只测试用。
dev = ["pytest>=8", "httpx>=0.27", "ruff>=0.6"]

[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools]
packages = ["api", "api.ui", "dim", "erp", "forecast", "jobs",
            "migrations", "migrations.pg", "rules", "shared"]

[tool.ruff]
line-length = 100
```

- [ ] **Step 4: 跑测试，确认它绿**

Run: `python -m pytest tests/test_layering.py -q`
Expected: PASS（9 项）

- [ ] **Step 5: 提交**

```bash
git add pyproject.toml tests/test_layering.py api dim erp forecast jobs migrations rules shared
git commit -m "test(layering): 分层规则 L1~L7 由测试强制，先于一切代码写出"
```

---

## Task 2: 配置 · PG 连接 · 带 checksum 的迁移执行器

**Files:**
- Create: `shared/config.py` · `shared/pg_client.py` · `migrations/pg/apply.py`
- Create: `tests/conftest.py` · `tests/test_migrations.py`
- Modify: `config.example.toml`（追加 `[api]` 与 `[freshness]`）

**Interfaces:**
- Consumes: Task 1 的包布局
- Produces:
  - `shared.config`: `business_pg() -> dict` · `clickhouse() -> dict` · `api() -> dict` · `freshness() -> dict` · `reset_cache() -> None`
  - `shared.pg_client`: `business_schema() -> str` · `pg_conn(autocommit: bool = False)`（contextmanager，产出 psycopg2 connection，**已 `SET search_path`**）· `check_schema(name: str) -> str` · `BadSchemaName` · `pg_error_fields(e) -> dict` · `timed(op: str, **fields)`（contextmanager）
  - `migrations.pg.apply`: `apply(schema: str) -> list[str]` · `applied_versions(schema: str) -> list[str]` · `migrations() -> list[tuple[str, str]]` · `MigrationChanged`
  - `tests/conftest.py`: fixture `business_db`（session）· `wipe`（function）

- [ ] **Step 1: 写失败的测试**

`tests/test_migrations.py`：

```python
"""迁移执行器自己的守卫。

★ 「已执行的迁移不可改」这条铁律靠 checksum 核对兑现 ——
  没有它，改一行旧迁移在开发机上悄悄生效，而生产上根本不会重跑。
"""
import pytest

from migrations.pg import apply as mig
from shared.pg_client import BadSchemaName, pg_conn


def test_migration_files_carry_no_transaction_control():
    """★ 迁移文件自带 BEGIN/COMMIT，外层 rollback 就兜不住 —— 预演会真落库。"""
    bad = []
    for name, sql in mig.migrations():
        for kw in ("begin;", "commit;", "rollback;"):
            if kw in sql.lower():
                bad.append(f"{name} 含 {kw}")
    assert not bad, f"事务边界只能由 apply() 持有：{bad}"


def test_schema_name_whitelist():
    for bad in ["scm; drop schema public", "SCM", "s" * 64, "", "public.scm"]:
        with pytest.raises(BadSchemaName):
            mig.apply(bad)


def test_applied_migrations_are_recorded_with_checksum(business_db):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT version, checksum FROM schema_migration ORDER BY version")
        rows = cur.fetchall()
    assert [r[0] for r in rows] == [n for n, _ in mig.migrations()]
    assert rows, "一条迁移都没记 —— 下面两条断言就是空转的"
    assert all(len(r[1]) == 64 for r in rows), "checksum 不是 sha256"


def test_rerun_is_a_noop(business_db):
    assert mig.apply(business_db) == [], "已应用的迁移被重复执行了"


def test_changed_migration_is_refused(business_db, monkeypatch):
    """改掉一份已执行迁移的内容 → 必须点名那份文件硬失败。"""
    orig = mig.migrations()
    monkeypatch.setattr(mig, "migrations", lambda: [(orig[0][0], orig[0][1] + "\n-- 偷改一行\n")])
    with pytest.raises(mig.MigrationChanged) as ei:
        mig.apply(business_db)
    assert orig[0][0] in str(ei.value)


def test_pg_conn_sets_search_path(business_db):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SHOW search_path")
        assert cur.fetchone()[0].strip() == business_db
```

`tests/conftest.py`：

```python
"""测试夹具。照邻居 service_social/tests/conftest.py 的形状。

★ 测试 schema 是 scm_test。禁止 scm（本服务生产）与 inv（同库另一个在跑的系统）——
  往它们任何一个里写一行都是事故，而这类事故没有任何回声。
"""
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONFIG_TOML = ROOT / "config.toml"

TEST_SCHEMA = "scm_test"
FORBIDDEN_SCHEMAS = {"scm", "inv"}

#: 每个测试之间要清空的表。★ plan_line_transition 不在其中 ——
#: 它是白名单（迁移灌的数据），清掉它状态机就没了。
DATA_TABLES = (
    "plan_line_event", "plan_submit_skip", "plan_line", "plan_rev",
    "plan_demand_cell", "plan_purchase_cell", "msku_claim", "plan",
    "msku_bridge", "sku_catalog", "warehouse", "seller", "actor",
)


def assert_not_production_schema():
    from shared.pg_client import business_schema
    s = business_schema()
    assert s not in FORBIDDEN_SCHEMAS, (
        f"测试正指向 {s!r}。多半是某个夹具漏了对 business_db 的依赖，"
        "shared.config 读的是未切换的 config.toml。")


def _switch_schema(text: str) -> str:
    out, in_pg = [], False
    for line in text.splitlines(keepends=True):
        s = line.strip()
        if s.startswith("["):
            in_pg = s.startswith("[business_pg]")
        if in_pg and s.startswith("schema"):
            out.append(f'schema = "{TEST_SCHEMA}"\n')
            continue
        out.append(line)
    return "".join(out)


@pytest.fixture(scope="session")
def business_db(tmp_path_factory):
    from shared import config as config_module

    cfg = tmp_path_factory.mktemp("jxd_scm") / "config.toml"
    cfg.write_text(_switch_schema(CONFIG_TOML.read_text("utf-8")), encoding="utf-8")
    prev = os.environ.get("JXD_SCM_CONFIG")
    os.environ["JXD_SCM_CONFIG"] = str(cfg)
    config_module.reset_cache()

    assert_not_production_schema()
    from migrations.pg.apply import apply
    apply(TEST_SCHEMA)
    try:
        yield TEST_SCHEMA
    finally:
        from shared.pg_client import pg_conn
        with pg_conn(autocommit=True) as c, c.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE")
        if prev is None:
            os.environ.pop("JXD_SCM_CONFIG", None)
        else:
            os.environ["JXD_SCM_CONFIG"] = prev
        config_module.reset_cache()


@pytest.fixture
def wipe(business_db):
    """★ TRUNCATE 不触发 BEFORE DELETE 触发器，所以只追加表也清得掉。"""
    assert_not_production_schema()
    from shared.pg_client import pg_conn
    with pg_conn() as c, c.cursor() as cur:
        cur.execute(f"TRUNCATE {', '.join(DATA_TABLES)} RESTART IDENTITY CASCADE")
    return business_db
```

- [ ] **Step 2: 跑测试，确认它红**

Run: `python -m pytest tests/test_migrations.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'migrations.pg.apply'`

- [ ] **Step 3: 实现配置与连接**

`config.example.toml` 追加（同时手工改本机 `config.toml`，它不入库）：

```toml
[api]
# ★ 阶段 A 按 Q-5 内网裸跑，不做鉴权；这一段留给 api/pub/ 上线时用。
bind = "0.0.0.0"
port = 8090

[freshness]
# ★ 维度镜像陈旧超过它 → 拒绝服务（E-4）。
# ★ 设计取值 · 未实测 —— 文档没给过阈值，刷新频率定下来后按实测改。
max_age_hours = 24
```

`shared/config.py`：

```python
"""配置读取。路径可由 JXD_SCM_CONFIG 覆盖，便于测试指向 scm_test。"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

_CFG: dict | None = None


def _cfg() -> dict:
    global _CFG
    if _CFG is None:
        default = Path(__file__).resolve().parents[1] / "config.toml"
        path = Path(os.environ.get("JXD_SCM_CONFIG", default))
        with open(path, "rb") as f:
            _CFG = tomllib.load(f)
    return _CFG


def reset_cache() -> None:
    """测试用：改了 JXD_SCM_CONFIG 之后强制重读。"""
    global _CFG
    _CFG = None


def business_pg() -> dict:
    """业务库：本仓全部业务数据，独占 schema scm（E-1）。"""
    return dict(_cfg()["business_pg"])


def clickhouse() -> dict:
    """★ 只读。绝不写任何表。"""
    return dict(_cfg()["clickhouse"])


def api() -> dict:
    return dict(_cfg().get("api", {}))


def freshness() -> dict:
    return dict(_cfg().get("freshness", {"max_age_hours": 24}))
```

`shared/pg_client.py`：

```python
"""业务库（PG）连接。一次一连，用完即关。"""
from __future__ import annotations

import contextlib
import logging
import re
import time

import psycopg2

from shared.config import business_pg

log = logging.getLogger("scm.pg")

#: 内网直连，3 秒连不上就是网络/主机出问题了。
CONNECT_TIMEOUT_S = 3

#: 光秃秃的小写标识符。PG 会折叠大小写，允许大写会让 "Scm" 和 "scm"
#: 看起来是两个 schema 实际是一个。
_SCHEMA_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class BadSchemaName(Exception):
    def __init__(self, schema):
        super().__init__(f"schema 名必须匹配 {_SCHEMA_RE.pattern}，收到 {schema!r}")


def check_schema(schema: str) -> str:
    """PG 没有 {db:Identifier} 这种参数，schema 名只能拼进 SQL 文本 —— 先过白名单。"""
    if not isinstance(schema, str) or not _SCHEMA_RE.match(schema):
        raise BadSchemaName(schema)
    return schema


def business_schema() -> str:
    return check_schema(business_pg().get("schema", "scm"))


def pg_error_fields(e: psycopg2.Error) -> dict:
    """★ 只取 str(e) 等于丢掉全部上下文：唯一冲突与外键冲突的 message 都长得像一句话，
    真凶在 pgcode 与 constraint_name 里。日志与错误码映射都靠它。"""
    d = getattr(e, "diag", None)
    return {
        "pgcode": getattr(e, "pgcode", None),
        "constraint": getattr(d, "constraint_name", None),
        "table": getattr(d, "table_name", None),
        "primary": getattr(d, "message_primary", None),
        "detail": getattr(d, "message_detail", None),
    }


@contextlib.contextmanager
def timed(op: str, **fields):
    """日志三问：打的谁（op + host/db）· 多久（elapsed_ms）· 怎么失败的（pgcode / constraint）。"""
    c = business_pg()
    where = f"{c['host']}:{c.get('port', 5432)}/{c['dbname']}.{business_schema()}"
    t0 = time.perf_counter()
    try:
        yield
    except psycopg2.Error as e:
        log.warning("op=%s target=%s elapsed_ms=%d outcome=pg_error %s %s",
                    op, where, (time.perf_counter() - t0) * 1000, pg_error_fields(e), fields)
        raise
    except BaseException as e:
        log.warning("op=%s target=%s elapsed_ms=%d outcome=%s msg=%s %s",
                    op, where, (time.perf_counter() - t0) * 1000, type(e).__name__, e, fields)
        raise
    else:
        log.info("op=%s target=%s elapsed_ms=%d outcome=ok %s",
                 op, where, (time.perf_counter() - t0) * 1000, fields)


@contextlib.contextmanager
def pg_conn(autocommit: bool = False):
    """一次一连。不做连接池：写入是低频的人工操作，而池化会引入
    「连接跨请求复用时 search_path / 事务状态残留」这类难查的问题。

    ★ 必须设 search_path：迁移里的 plpgsql 函数体用的是不带 schema 前缀的表名，
      同一份 SQL 才能同时跑在 scm 与 scm_test 上。
    """
    c = business_pg()
    schema = business_schema()
    conn = psycopg2.connect(
        host=c["host"], port=int(c.get("port", 5432)),
        user=c["user"], password=c.get("password", ""),
        dbname=c["dbname"], connect_timeout=CONNECT_TIMEOUT_S,
    )
    conn.autocommit = autocommit
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {schema}")
        yield conn
        if not autocommit:
            conn.commit()
    except BaseException:
        if not autocommit:
            conn.rollback()
        raise
    finally:
        conn.close()
```

`migrations/pg/apply.py`：

```python
"""在指定 schema 上幂等应用迁移。SQL 文件是表结构的唯一真源。

★ 与邻居 service_social 的唯一差别：多一列 checksum 并在 apply 时核对 ——
  「已执行的迁移不可改」这条铁律，不核对就只是一句口号。
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from shared.config import business_pg
from shared.pg_client import check_schema, pg_conn, timed

MIGRATION_DIR = Path(__file__).resolve().parent


class MigrationChanged(Exception):
    def __init__(self, name: str, applied: str, current: str):
        super().__init__(
            f"迁移 {name} 的内容变了（已执行 {applied[:12]}… / 现在 {current[:12]}…）。"
            "已执行的迁移不可改 —— 要改结构就追加一个新文件。")


def _checksum(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def migrations() -> list[tuple[str, str]]:
    return [(p.name, p.read_text(encoding="utf-8")) for p in sorted(MIGRATION_DIR.glob("*.sql"))]


def apply(schema: str) -> list[str]:
    """幂等建 schema + 应用全部迁移，返回本次**新应用**的文件名。"""
    check_schema(schema)
    newly: list[str] = []
    with timed("migrate", schema=schema), pg_conn() as conn, conn.cursor() as cur:
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        cur.execute(f"SET search_path TO {schema}")
        cur.execute(
            "CREATE TABLE IF NOT EXISTS schema_migration ("
            "  version text PRIMARY KEY,"
            "  checksum text NOT NULL,"
            "  applied_at timestamptz NOT NULL DEFAULT now())"
        )
        cur.execute("SELECT version, checksum FROM schema_migration")
        done = dict(cur.fetchall())
        for name, sql in migrations():
            digest = _checksum(sql)
            if name in done:
                if done[name] != digest:
                    raise MigrationChanged(name, done[name], digest)
                continue
            cur.execute(sql)
            cur.execute("INSERT INTO schema_migration (version, checksum) VALUES (%s, %s)",
                        (name, digest))
            newly.append(name)
    return newly


def applied_versions(schema: str) -> list[str]:
    check_schema(schema)
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(f"SET search_path TO {schema}")
        cur.execute("SELECT version FROM schema_migration ORDER BY version")
        return [r[0] for r in cur.fetchall()]


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else business_pg().get("schema", "scm")
    print(f"applied {apply(target)} to schema {target}")
```

★ `pg_conn()` 与 `apply()` 里各有一句 `SET search_path`，重复是刻意的：
`apply()` 可以被要求作用在与配置不同的 schema 上（`python -m migrations.pg.apply scm_test`），
那时连接上的 search_path 指的是配置里的那个。

- [ ] **Step 4: 跑测试，确认它绿**

Run: `python -m pytest tests/test_migrations.py -q`
Expected: 4 项 PASS、2 项 FAIL（`test_applied_migrations_are_recorded_with_checksum` 的 `assert rows` 与 `test_changed_migration_is_refused` 的 `orig[0]` 索引越界）——
★ 这两项**现在就该红**：一条 `.sql` 都还没有。Task 3 加上 `001_foundation.sql` 后它们转绿，
而它们红的形态（`IndexError` / 空列表）正是「扫到 0 个东西」的样子。

- [ ] **Step 5: 提交**

```bash
git add shared migrations config.example.toml tests/conftest.py tests/test_migrations.py
git commit -m "feat(shared): 配置 · PG 连接（search_path 与错误取证）· 带 checksum 的迁移执行器"
```

---
## Task 3: 001_foundation.sql —— 操作人 + 四张镜像 + 只追加守卫 + 新鲜度视图

**Files:**
- Create: `migrations/pg/001_foundation.sql`
- Create: `tests/test_ddl_001_foundation.py`
- Modify: `tests/conftest.py`（追加 `seed` 夹具）

**Interfaces:**
- Consumes: `migrations.pg.apply.apply()`（Task 2）· fixture `business_db` / `wipe`
- Produces:
  - 表 `actor(actor_id text PK, name, active bool, created_at)` · `seller(seller_id text PK, name, market, has_fba bool, platform, refreshed_at)` · `sku_catalog(sku text PK, name, refreshed_at)` · `msku_bridge(seller_sku, sid) PK, sku, refreshed_at` · `warehouse(wid int PK, name, kind, market, refreshed_at)`
  - 函数 `forbid_update_delete()` · 视图 `v_mirror_freshness(mirror, refreshed_at)`
  - fixture `seed`：插入 2 个 actor（1 停用）· 3 个 seller（含 1 个 `has_fba=false`）· 2 个 sku · 4 条 bridge，返回 `SimpleNamespace`

- [ ] **Step 1: 写失败的约束测试**

`tests/test_ddl_001_foundation.py`：

```python
"""001 的约束真的挡得住吗 —— 每条都插一行违反的，断言具体是哪个错。

★ 「约束在库层」这条铁律靠这些测试兑现。只写 DDL 不写这些，
  等于把「我以为它挡得住」当成「它挡得住」。
"""
import psycopg2.errors
import pytest

from shared.pg_client import pg_conn


def _exec(sql, args=()):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute(sql, args)


def test_mirror_freshness_lists_exactly_four_mirrors(wipe):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT mirror FROM v_mirror_freshness ORDER BY mirror")
        assert [r[0] for r in cur.fetchall()] == [
            "msku_bridge", "seller", "sku_catalog", "warehouse"]


def test_empty_mirror_reports_null_not_fresh(wipe):
    """★ 空镜像的 max(refreshed_at) 是 NULL —— 必须能与「刚刷过」分得开。
    把 NULL 当成新鲜，就是拿一张空表当可用数据在服务。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT refreshed_at FROM v_mirror_freshness WHERE mirror = 'seller'")
        assert cur.fetchone()[0] is None


def test_msku_bridge_primary_key_includes_sid(wipe, seed):
    """★ 同一 msku 字符串跨店是不同 listing：只按 seller_sku 建键会撞键并静默覆盖。"""
    _exec("INSERT INTO msku_bridge VALUES (%s, %s, %s, now())",
          (seed.msku_a[0], seed.seller_b, seed.sku_a))   # 同 seller_sku、不同 sid → 必须能插
    with pytest.raises(psycopg2.errors.UniqueViolation):
        _exec("INSERT INTO msku_bridge VALUES (%s, %s, %s, now())",
              (seed.msku_a[0], seed.msku_a[1], seed.sku_a))  # 同 (seller_sku, sid) → 撞主键


def test_msku_bridge_requires_known_seller_and_sku(wipe, seed):
    with pytest.raises(psycopg2.errors.ForeignKeyViolation) as ei:
        _exec("INSERT INTO msku_bridge VALUES ('MSKU-X', '9999999999', %s, now())", (seed.sku_a,))
    assert "seller" in (ei.value.diag.constraint_name or "")
    with pytest.raises(psycopg2.errors.ForeignKeyViolation) as ei:
        _exec("INSERT INTO msku_bridge VALUES ('MSKU-X', %s, 'NO-SUCH-SKU', now())",
              (seed.seller_a,))
    assert "sku" in (ei.value.diag.constraint_name or "")


def test_warehouse_kind_is_a_closed_set(wipe):
    with pytest.raises(psycopg2.errors.CheckViolation) as ei:
        _exec("INSERT INTO warehouse VALUES (1, '某仓', 'fba_like', 'US', now())")
    assert ei.value.diag.constraint_name == "warehouse_kind_known"


def test_forbid_update_delete_names_the_table_and_the_op(wipe, seed):
    """守卫函数本身要能被证伪：挂一张临时的只追加表，改它必须炸且点名。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("CREATE TEMP TABLE probe_append_only (x int)")
        cur.execute("CREATE TRIGGER t BEFORE UPDATE OR DELETE ON probe_append_only "
                    "FOR EACH ROW EXECUTE FUNCTION forbid_update_delete()")
        cur.execute("INSERT INTO probe_append_only VALUES (1)")
        with pytest.raises(psycopg2.errors.RaiseException) as ei:
            cur.execute("UPDATE probe_append_only SET x = 2")
        assert "probe_append_only" in str(ei.value) and "UPDATE" in str(ei.value)
```

`tests/conftest.py` 追加：

```python
from types import SimpleNamespace


@pytest.fixture
def seed(wipe):
    """维度与操作人的最小一组。

    ★ 刻意包含两种「长得像 0 其实不是」的形态：
      · actor `bob` 停用 —— 与「不存在」分得开
      · seller `WM-1` has_fba=false —— 它的 FBA 在仓是「不适用」，不是 0（02 §3.1a）
    """
    from shared.pg_client import pg_conn
    ns = SimpleNamespace(
        actor="alice", actor_inactive="bob",
        seller_a="11072", seller_b="11094", seller_nofba="90001",
        sku_a="DCC1800264G1", sku_b="A4P-TOY-002",
        msku_a=("MSKU-A", "11072"), msku_b=("MSKU-B", "11072"),
        msku_c=("MSKU-C", "11094"), msku_nofba=("MSKU-W", "90001"),
    )
    with pg_conn() as c, c.cursor() as cur:
        cur.executemany("INSERT INTO actor (actor_id, name, active) VALUES (%s, %s, %s)",
                        [(ns.actor, "爱丽丝", True), (ns.actor_inactive, "鲍勃", False)])
        cur.executemany(
            "INSERT INTO seller (seller_id, name, market, has_fba, platform, refreshed_at)"
            " VALUES (%s, %s, %s, %s, %s, now())",
            [(ns.seller_a, "A4Pet-US", "US", True, "amazon"),
             (ns.seller_b, "A4Pet-BS-UK", "UK", True, "amazon"),
             (ns.seller_nofba, "A4Pet-WM", "US", False, "walmart")])
        cur.executemany("INSERT INTO sku_catalog (sku, name, refreshed_at) VALUES (%s, %s, now())",
                        [(ns.sku_a, "猫爬架"), (ns.sku_b, "逗猫棒")])
        cur.executemany("INSERT INTO msku_bridge VALUES (%s, %s, %s, now())",
                        [(*ns.msku_a, ns.sku_a), (*ns.msku_b, ns.sku_a),
                         (*ns.msku_c, ns.sku_a), (*ns.msku_nofba, ns.sku_b)])
    return ns
```

- [ ] **Step 2: 跑测试，确认它红**

Run: `python -m pytest tests/test_ddl_001_foundation.py -q`
Expected: FAIL —— `psycopg2.errors.UndefinedTable: relation "v_mirror_freshness" does not exist`
（★ 若先前跑过 `business_db`，`scm_test` 会残留 —— 夹具每轮 DROP SCHEMA，不会假绿。）

- [ ] **Step 3: 写迁移**

`migrations/pg/001_foundation.sql`：

```sql
-- 001 · 地基：只追加守卫 · 操作人 · 四张维度镜像 · 镜像新鲜度视图
-- 出处：03 §0.1（forbid_update_delete）· 03 §8.1（2026-09-22 补，S-9）· 03 §7.1（E-4）
--
-- ★ 迁移里一律不写 scm. 前缀（连函数、触发器名）：靠 search_path 落到目标 schema，
--   同一份 SQL 才能既跑在 scm 上、也跑在测试的 scm_test 上。
-- ★ 本文件不含 BEGIN/COMMIT：事务边界由 apply() 持有。自带边界的 SQL 会让
--   外层的 rollback 兜不住，"预演"就变成真落库。

-- ★ CREATE OR REPLACE：PG 没有 CREATE FUNCTION IF NOT EXISTS，而 apply() 可能
--   被指到一个已有同名函数的 schema 上。表用 IF NOT EXISTS 就够。
CREATE OR REPLACE FUNCTION forbid_update_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% 是只追加表，不允许 %（证据一旦可改，就不再是证据）',
        TG_TABLE_NAME, TG_OP;
END $$;

CREATE TABLE IF NOT EXISTS actor (
    actor_id   text PRIMARY KEY,           -- ★ 阶段 A 内网裸跑：前端下拉选（Q-5，留痕可伪造）
    name       text NOT NULL,
    active     boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS seller (        -- 店铺（= 领星 sid，全字段字符串）
    seller_id    text PRIMARY KEY,
    name         text NOT NULL,
    market       text NOT NULL,            -- US / UK / DE …
    has_fba      boolean NOT NULL,         -- ★ 无 FBA 的平台显示「不适用」，不是 0（02 §3.1a）
    platform     text NOT NULL,            -- amazon / walmart / …
    refreshed_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS sku_catalog (   -- 货号：物理产品
    sku          text PRIMARY KEY,
    name         text NOT NULL,
    refreshed_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS msku_bridge (   -- msku（listing）→ 货号
    seller_sku   text NOT NULL,
    sid          text NOT NULL,
    sku          text NOT NULL,
    refreshed_at timestamptz NOT NULL,
    -- ★ 主键必须含 sid：同一 msku 字符串跨店是不同 listing，
    --   不含它会撞键并静默覆盖店铺归属。
    CONSTRAINT msku_bridge_pkey PRIMARY KEY (seller_sku, sid),
    CONSTRAINT msku_bridge_seller_fk FOREIGN KEY (sid) REFERENCES seller(seller_id),
    CONSTRAINT msku_bridge_sku_fk    FOREIGN KEY (sku) REFERENCES sku_catalog(sku)
);

CREATE TABLE IF NOT EXISTS warehouse (     -- 仓（★ 不是库位，实测没有实体库位）
    wid          int  PRIMARY KEY,
    name         text NOT NULL,
    kind         text NOT NULL CONSTRAINT warehouse_kind_known
                      CHECK (kind IN ('local','oversea_self','oversea_3pl','fba')),
    market       text,
    refreshed_at timestamptz NOT NULL
);

-- 启动钩子与就绪探针共用：任一维度镜像陈旧超阈值 → 拒绝服务（E-4）。
-- ★ 空表时 max() 返回 NULL —— 调用方必须把 NULL 判成「不可用」，
--   把它当新鲜就是拿一张空表在服务。
CREATE OR REPLACE VIEW v_mirror_freshness AS
         SELECT 'sku_catalog' AS mirror, max(refreshed_at) AS refreshed_at FROM sku_catalog
UNION ALL SELECT 'msku_bridge', max(refreshed_at) FROM msku_bridge
UNION ALL SELECT 'seller',      max(refreshed_at) FROM seller
UNION ALL SELECT 'warehouse',   max(refreshed_at) FROM warehouse;
```

- [ ] **Step 4: 跑测试，确认它绿**

Run: `python -m pytest tests/test_ddl_001_foundation.py tests/test_migrations.py -q`
Expected: PASS（6 + 6 项；Task 2 里那两项红的现在转绿）

- [ ] **Step 5: 提交**

```bash
git add migrations/pg/001_foundation.sql tests/test_ddl_001_foundation.py tests/conftest.py
git commit -m "feat(db): 001 地基迁移 —— 操作人 · 四张镜像 · 只追加守卫 · 新鲜度视图"
```

---

## Task 4: 002_plan.sql —— 计划 · 两张格子 · 认领 · 版本 · 记录 · 跳过留痕

**Files:**
- Create: `migrations/pg/002_plan.sql`
- Create: `tests/test_ddl_002_plan.py`

**Interfaces:**
- Consumes: Task 3 的 `actor` / `seller` / `sku_catalog` / `msku_bridge`；fixture `seed`
- Produces（列名是后续每个 Task 写 SQL 的依据）：
  - `plan(plan_id bigserial PK, title, period_start date, months int, owner_actor, archived_at, created_by, created_at)`
  - `plan_demand_cell(plan_id, seller_sku, sid, period_start) PK, system_units int NULL, system_extrapolated bool, expected_units int NULL, updated_by, updated_at`
  - `plan_purchase_cell(plan_id, sku, period_start) PK, planned_units int NULL, updated_by, updated_at`
  - `msku_claim(plan_id, seller_sku, sid) PK, claimed_by, claimed_at, released_by, released_at, plan_ordered bool`
  - `plan_rev(plan_id, rev) PK, content_digest, is_current bool, in_flight bool, submitted_by, submitted_at`
  - `plan_line(line_id bigserial PK, plan_id, rev, sku, period_start, total_units, demand_by_seller jsonb, demand_at_submit int, state text)`
  - `plan_submit_skip(skip_id bigserial PK, plan_id, rev, sku, period_start, reason, at)`

- [ ] **Step 1: 写失败的约束测试**

`tests/test_ddl_002_plan.py`：

```python
"""002 的约束挡得住吗。每条都插一行违反的，断言具体是哪个约束名。"""
import datetime as dt

import psycopg2.errors
import pytest

from shared.pg_client import pg_conn

OCT = dt.date(2026, 10, 1)


def mk_plan(cur, owner, months=3, start=OCT, title="10 月计划"):
    cur.execute("INSERT INTO plan (title, period_start, months, owner_actor, created_by)"
                " VALUES (%s, %s, %s, %s, %s) RETURNING plan_id", (title, start, months, owner, owner))
    return cur.fetchone()[0]


def test_months_range_is_1_to_24(wipe, seed):
    """★ S-7：03 §2.1 曾写 1..12，而 M-10 裁定 1..24 —— 统一按 24。"""
    with pg_conn() as c, c.cursor() as cur:
        mk_plan(cur, seed.actor, months=24)
    for bad in (0, 25):
        with pytest.raises(psycopg2.errors.CheckViolation) as ei, pg_conn() as c, c.cursor() as cur:
            mk_plan(cur, seed.actor, months=bad)
        assert ei.value.diag.constraint_name == "plan_months_1_24"


def test_period_start_must_be_first_of_month(wipe, seed):
    with pytest.raises(psycopg2.errors.CheckViolation) as ei, pg_conn() as c, c.cursor() as cur:
        mk_plan(cur, seed.actor, start=dt.date(2026, 10, 15))
    assert ei.value.diag.constraint_name == "plan_period_start_is_month_start"


def test_owner_must_be_a_known_actor(wipe, seed):
    with pytest.raises(psycopg2.errors.ForeignKeyViolation) as ei, pg_conn() as c, c.cursor() as cur:
        mk_plan(cur, "nobody")
    assert ei.value.diag.constraint_name == "plan_owner_fk"


def test_one_active_claim_per_msku_across_plans(wipe, seed):
    """★ 判据③ 的库层那一半：独占由部分唯一索引裁决，不靠先查后写。"""
    with pg_conn() as c, c.cursor() as cur:
        p1, p2 = mk_plan(cur, seed.actor), mk_plan(cur, seed.actor, title="另一张")
        cur.execute("INSERT INTO msku_claim (plan_id, seller_sku, sid, claimed_by)"
                    " VALUES (%s, %s, %s, %s)", (p1, *seed.msku_a, seed.actor))
        with pytest.raises(psycopg2.errors.UniqueViolation) as ei:
            cur.execute("INSERT INTO msku_claim (plan_id, seller_sku, sid, claimed_by)"
                        " VALUES (%s, %s, %s, %s)", (p2, *seed.msku_a, seed.actor))
        assert ei.value.diag.constraint_name == "msku_claim_one_active_idx"


def test_released_claim_frees_the_msku_but_keeps_the_row(wipe, seed):
    with pg_conn() as c, c.cursor() as cur:
        p1, p2 = mk_plan(cur, seed.actor), mk_plan(cur, seed.actor, title="另一张")
        cur.execute("INSERT INTO msku_claim (plan_id, seller_sku, sid, claimed_by)"
                    " VALUES (%s, %s, %s, %s)", (p1, *seed.msku_a, seed.actor))
        cur.execute("UPDATE msku_claim SET released_at = now(), released_by = %s"
                    " WHERE plan_id = %s", (seed.actor, p1))
        cur.execute("INSERT INTO msku_claim (plan_id, seller_sku, sid, claimed_by)"
                    " VALUES (%s, %s, %s, %s)", (p2, *seed.msku_a, seed.actor))
        cur.execute("SELECT count(*) FROM msku_claim")
        assert cur.fetchone()[0] == 2, "★ 释放不删行 —— 留痕没了就查不到谁占过"


def test_ordered_claim_no_longer_blocks(wipe, seed):
    """★ 独占窗口只到「已下单」为止（03:171 的 WHERE NOT plan_ordered）。
    阶段 A 里 plan_ordered 恒 false（S-12），这里直接置位验证索引的另一半。"""
    with pg_conn() as c, c.cursor() as cur:
        p1, p2 = mk_plan(cur, seed.actor), mk_plan(cur, seed.actor, title="另一张")
        cur.execute("INSERT INTO msku_claim (plan_id, seller_sku, sid, claimed_by, plan_ordered)"
                    " VALUES (%s, %s, %s, %s, true)", (p1, *seed.msku_a, seed.actor))
        cur.execute("INSERT INTO msku_claim (plan_id, seller_sku, sid, claimed_by)"
                    " VALUES (%s, %s, %s, %s)", (p2, *seed.msku_a, seed.actor))


def test_demand_cell_requires_a_claim_and_a_bridge_row(wipe, seed):
    """★ 没认领就没格子。"""
    with pg_conn() as c, c.cursor() as cur:
        p = mk_plan(cur, seed.actor)
        with pytest.raises(psycopg2.errors.ForeignKeyViolation) as ei:
            cur.execute("INSERT INTO plan_demand_cell (plan_id, seller_sku, sid, period_start)"
                        " VALUES (%s, %s, %s, %s)", (p, *seed.msku_a, OCT))
        assert ei.value.diag.constraint_name == "plan_demand_cell_claim_fk"


def test_expected_units_null_is_allowed_but_negative_is_not(wipe, seed):
    """★ NULL = 未知（M-8），不是 0；负数才是错。两者必须分得开。"""
    with pg_conn() as c, c.cursor() as cur:
        p = mk_plan(cur, seed.actor)
        cur.execute("INSERT INTO msku_claim (plan_id, seller_sku, sid, claimed_by)"
                    " VALUES (%s, %s, %s, %s)", (p, *seed.msku_a, seed.actor))
        cur.execute("INSERT INTO plan_demand_cell (plan_id, seller_sku, sid, period_start)"
                    " VALUES (%s, %s, %s, %s)", (p, *seed.msku_a, OCT))
        cur.execute("SELECT expected_units, system_units FROM plan_demand_cell")
        assert cur.fetchone() == (None, None)
        with pytest.raises(psycopg2.errors.CheckViolation) as ei:
            cur.execute("UPDATE plan_demand_cell SET expected_units = -1")
        assert ei.value.diag.constraint_name == "plan_demand_cell_expected_nonneg"


def test_one_current_and_one_in_flight_rev_per_plan(wipe, seed):
    """★ S-4：同一计划最多 1 版在流转 —— 由库层裁决，不靠接口先查后写。"""
    with pg_conn() as c, c.cursor() as cur:
        p = mk_plan(cur, seed.actor)
        cur.execute("INSERT INTO plan_rev (plan_id, rev, content_digest, is_current, submitted_by)"
                    " VALUES (%s, 1, 'd1', true, %s)", (p, seed.actor))
        with pytest.raises(psycopg2.errors.UniqueViolation) as ei:
            cur.execute("INSERT INTO plan_rev (plan_id, rev, content_digest, submitted_by)"
                        " VALUES (%s, 2, 'd2', %s)", (p, seed.actor))
        assert ei.value.diag.constraint_name == "plan_rev_one_in_flight_idx"


def test_plan_line_total_units_must_be_positive(wipe, seed):
    """★ 坑①：总量 0 会因为 0 = 0 恒真，一诞生就自动跳到「已确认」。"""
    with pg_conn() as c, c.cursor() as cur:
        p = mk_plan(cur, seed.actor)
        cur.execute("INSERT INTO plan_rev (plan_id, rev, content_digest, submitted_by)"
                    " VALUES (%s, 1, 'd1', %s)", (p, seed.actor))
        with pytest.raises(psycopg2.errors.CheckViolation) as ei:
            cur.execute(
                "INSERT INTO plan_line (plan_id, rev, sku, period_start, total_units,"
                " demand_by_seller, demand_at_submit, state)"
                " VALUES (%s, 1, %s, %s, 0, '{}'::jsonb, 0, '已提交')", (p, seed.sku_a, OCT))
        assert ei.value.diag.constraint_name == "plan_line_total_positive"


def test_plan_line_has_no_seller_id(wipe):
    """★ P2 / S-3：记录是货号级，店铺归属在组单时由采购员决定（po_source_map）。
    列还在的话，组单时会出现两个真相。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT column_name FROM information_schema.columns"
                    " WHERE table_schema = current_schema() AND table_name = 'plan_line'")
        cols = {r[0] for r in cur.fetchall()}
    assert "seller_id" not in cols
    assert {"demand_by_seller", "demand_at_submit"} <= cols, "S-6：冻结的两列必须在"


def test_skip_reason_is_a_closed_set_and_append_only(wipe, seed):
    with pg_conn() as c, c.cursor() as cur:
        p = mk_plan(cur, seed.actor)
        cur.execute("INSERT INTO plan_rev (plan_id, rev, content_digest, submitted_by)"
                    " VALUES (%s, 1, 'd1', %s)", (p, seed.actor))
        with pytest.raises(psycopg2.errors.CheckViolation) as ei:
            cur.execute("INSERT INTO plan_submit_skip (plan_id, rev, sku, period_start, reason)"
                        " VALUES (%s, 1, %s, %s, 'because')", (p, seed.sku_a, OCT))
        assert ei.value.diag.constraint_name == "plan_submit_skip_reason_known"
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO plan_submit_skip (plan_id, rev, sku, period_start, reason)"
                    " VALUES (%s, 1, %s, %s, 'zero_purchase')", (p, seed.sku_a, OCT))
        with pytest.raises(psycopg2.errors.RaiseException):     # ★ S-17：只追加
            cur.execute("UPDATE plan_submit_skip SET reason = 'no_claimed_msku'")
```

- [ ] **Step 2: 跑测试，确认它红**

Run: `python -m pytest tests/test_ddl_002_plan.py -q`
Expected: FAIL —— `UndefinedTable: relation "plan" does not exist`（12 项全红）

- [ ] **Step 3: 写迁移**

`migrations/pg/002_plan.sql`：

```sql
-- 002 · 需求域：计划 · 两张格子 · 认领 · 版本 · 记录 · 跳过留痕
-- 出处：03 §2.1 / §2.2 / §2.3（P1 与 P2 已于 2026-09-22 落地）· S-1/S-3/S-4/S-6/S-7/S-14/S-17
-- ★ 建表顺序按外键依赖：plan → msku_claim → 两张格子 → plan_rev → plan_line → skip。

CREATE TABLE IF NOT EXISTS plan (
    plan_id      bigserial PRIMARY KEY,
    title        text        NOT NULL,
    period_start date        NOT NULL,
    -- ★ M-10 裁定 1..24（03 §2.1 早期写过 1..12，按 S-7 统一到这里）
    months       int         NOT NULL DEFAULT 3
                             CONSTRAINT plan_months_1_24 CHECK (months BETWEEN 1 AND 24),
    owner_actor  text        NOT NULL CONSTRAINT plan_owner_fk REFERENCES actor(actor_id),
    archived_at  timestamptz,
    created_by   text        NOT NULL CONSTRAINT plan_created_by_fk REFERENCES actor(actor_id),
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT plan_period_start_is_month_start
        CHECK (date_trunc('month', period_start) = period_start)
);

CREATE TABLE IF NOT EXISTS msku_claim (
    plan_id      bigint NOT NULL REFERENCES plan(plan_id),
    seller_sku   text   NOT NULL,
    sid          text   NOT NULL,            -- ★ 同一字符串跨店是不同 listing
    claimed_by   text   NOT NULL,
    claimed_at   timestamptz NOT NULL DEFAULT now(),
    released_by  text,
    released_at  timestamptz,                -- ★ 释放不删行，保留留痕
    plan_ordered boolean NOT NULL DEFAULT false,  -- 物化：该计划是否已进入已下单段（阶段 B 写）
    CONSTRAINT msku_claim_pkey PRIMARY KEY (plan_id, seller_sku, sid)
);

-- ★ 独占由库层裁决，不用先查后写（判据③）。窗口到「已下单」为止。
CREATE UNIQUE INDEX IF NOT EXISTS msku_claim_one_active_idx
    ON msku_claim (seller_sku, sid)
    WHERE released_at IS NULL AND NOT plan_ordered;

-- ① 期望销量：msku 级。★ 行本身就是「构成」，没有第二张构成表，
--    所以原先守「格子 = 构成之和」的约束触发器随拆表取消（S-16）。
CREATE TABLE IF NOT EXISTS plan_demand_cell (
    plan_id        bigint NOT NULL REFERENCES plan(plan_id) ON DELETE CASCADE,
    seller_sku     text   NOT NULL,
    sid            text   NOT NULL,
    period_start   date   NOT NULL,
    system_units   int    CONSTRAINT plan_demand_cell_system_nonneg CHECK (system_units >= 0),
    -- ★ 外推出来的预估不是预估（M-13）：标记必须跟着数一起存，
    --   否则界面只能回头读别处，同一个数两个来源迟早分叉。
    system_extrapolated boolean NOT NULL DEFAULT false,
    expected_units int    CONSTRAINT plan_demand_cell_expected_nonneg CHECK (expected_units >= 0),
    updated_by     text,
    updated_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT plan_demand_cell_pkey PRIMARY KEY (plan_id, seller_sku, sid, period_start),
    CONSTRAINT plan_demand_cell_bridge_fk FOREIGN KEY (seller_sku, sid)
        REFERENCES msku_bridge(seller_sku, sid),
    -- ★ 没认领就没格子（释放不删行，所以这条外键长期成立）
    CONSTRAINT plan_demand_cell_claim_fk FOREIGN KEY (plan_id, seller_sku, sid)
        REFERENCES msku_claim(plan_id, seller_sku, sid) ON DELETE CASCADE
);

-- ② 计划采购量：货号级，★ 不带店铺 —— 供应链备的是总量
CREATE TABLE IF NOT EXISTS plan_purchase_cell (
    plan_id       bigint NOT NULL REFERENCES plan(plan_id) ON DELETE CASCADE,
    sku           text   NOT NULL REFERENCES sku_catalog(sku),
    period_start  date   NOT NULL,
    planned_units int    CONSTRAINT plan_purchase_cell_nonneg CHECK (planned_units >= 0),
    updated_by    text,
    updated_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT plan_purchase_cell_pkey PRIMARY KEY (plan_id, sku, period_start)
);

CREATE TABLE IF NOT EXISTS plan_rev (
    plan_id        bigint  NOT NULL REFERENCES plan(plan_id),
    rev            int     NOT NULL,
    content_digest text    NOT NULL,      -- ★ A-2 / S-13：提交时全部生效值的哈希
    is_current     boolean NOT NULL DEFAULT false,
    in_flight      boolean NOT NULL DEFAULT true,  -- ★ 全部记录进终态后由触发器置 false
    submitted_by   text    NOT NULL,
    submitted_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT plan_rev_pkey PRIMARY KEY (plan_id, rev)   -- ★ 并发提交撞 rev 由主键裁决
);

CREATE UNIQUE INDEX IF NOT EXISTS plan_rev_one_current_idx
    ON plan_rev (plan_id) WHERE is_current;

-- ★ S-4：同一计划最多 1 版在流转。提交新版撞上在流转的旧版 → 唯一索引冲突
--   → 409 rev_in_flight 点名旧版。
CREATE UNIQUE INDEX IF NOT EXISTS plan_rev_one_in_flight_idx
    ON plan_rev (plan_id) WHERE in_flight;

CREATE TABLE IF NOT EXISTS plan_line (
    line_id      bigserial PRIMARY KEY,   -- 代理键：承重墙要引用它，业务键太宽
    plan_id      bigint NOT NULL,
    rev          int    NOT NULL,
    -- ★ 没有 seller_id（P2 / S-3）：记录是货号级，店铺归属在组单时由采购员决定
    sku          text   NOT NULL REFERENCES sku_catalog(sku),
    period_start date   NOT NULL,
    total_units  int    NOT NULL CONSTRAINT plan_line_total_positive CHECK (total_units > 0),
    -- ★ 冻结「提交那一刻各店的期望销量」（02:958 / S-6）：
    --   {sid: {units, basis, mskus[]}}，basis ∈ human|system|unknown
    demand_by_seller jsonb NOT NULL,
    demand_at_submit int   NOT NULL
        CONSTRAINT plan_line_demand_nonneg CHECK (demand_at_submit >= 0),
    -- ★ 物化列；插入时写 '已提交'，此后只能由事件触发器写（003 挂拦截器）
    state        text   NOT NULL,
    CONSTRAINT plan_line_one_per_cell UNIQUE (plan_id, rev, sku, period_start),
    CONSTRAINT plan_line_rev_fk FOREIGN KEY (plan_id, rev) REFERENCES plan_rev(plan_id, rev)
);

-- ★ 提交时被跳过的格子，逐条留痕（判据②，不静默丢）
CREATE TABLE IF NOT EXISTS plan_submit_skip (
    skip_id      bigserial PRIMARY KEY,
    plan_id      bigint NOT NULL,
    rev          int    NOT NULL,
    sku          text   NOT NULL,
    period_start date   NOT NULL,
    reason       text   NOT NULL CONSTRAINT plan_submit_skip_reason_known
                        CHECK (reason IN ('zero_purchase','no_claimed_msku')),   -- S-14
    at           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT plan_submit_skip_rev_fk FOREIGN KEY (plan_id, rev) REFERENCES plan_rev(plan_id, rev)
);

-- ★ S-17：清单把它归为只追加，这里兑现 —— 留痕可改就不再是留痕。
DROP TRIGGER IF EXISTS t_skip_append_only ON plan_submit_skip;
CREATE TRIGGER t_skip_append_only BEFORE UPDATE OR DELETE ON plan_submit_skip
    FOR EACH ROW EXECUTE FUNCTION forbid_update_delete();
```

★ 为什么触发器用 `DROP TRIGGER IF EXISTS` + `CREATE` 而不是 `CREATE OR REPLACE TRIGGER`：
后者要 PG 14+，而这台实例的版本**未实测**；前者在任何版本上都成立。

- [ ] **Step 4: 跑测试，确认它绿**

Run: `python -m pytest tests/test_ddl_002_plan.py -q`
Expected: PASS（12 项）

- [ ] **Step 5: 提交**

```bash
git add migrations/pg/002_plan.sql tests/test_ddl_002_plan.py
git commit -m "feat(db): 002 需求域迁移 —— 两张格子（msku 级期望 / 货号级采购）· 认领独占 · 唯一在流转版"
```

---

## Task 5: 003_plan_state_machine.sql —— 白名单 · 事件表 · 五个函数 · 五个触发器

**Files:**
- Create: `migrations/pg/003_plan_state_machine.sql`
- Create: `tests/test_ddl_003_state_machine.py`

**Interfaces:**
- Consumes: Task 4 的 `plan_line` / `plan_rev`
- Produces:
  - `plan_line_transition(from_state, to_state) PK, kind, needs_reason`，**12 行**（含哨兵 `('[*]','已提交')`）
  - `plan_line_event(seq bigserial PK, line_id, from_state, to_state, actor, reason, src, at)`
  - 函数 `sync_plan_line_state()` · `require_reason()` · `state_must_go_through_event()` · `assert_birth_event()` · `close_rev_if_settled(bigint, int) RETURNS void`
  - 常量（后续 Task 直接引用）：终态 = `('已完结','已撤销')`；铸出态 = `'已提交'`；哨兵 = `'[*]'`

- [ ] **Step 1: 写失败的状态机测试**

`tests/test_ddl_003_state_machine.py`：

```python
"""状态机四件套。T1/T2/T3/T6/T21 出自 04 §9 的「必须先红一次」清单。"""
import datetime as dt

import psycopg2.errors
import pytest

from shared.pg_client import pg_conn

OCT = dt.date(2026, 10, 1)

#: ★ T21 的靶子：把 04 §7.1 / 03 §3 的行表**逐字**抄在这里当断言。
#: 没有它，「状态机是数据」只兑现一半 —— 库里是数据，文档里还是另一份手抄副本，
#: 而两份副本一定会分叉。
WHITELIST = [
    ("[*]", "已提交", "forward", False),
    ("已提交", "已确认", "forward", False),
    ("已确认", "已下单", "forward", False),
    ("已下单", "准备排货", "forward", False),
    ("准备排货", "已排货", "forward", False),
    ("已排货", "已完结", "forward", False),
    ("已确认", "已提交", "back", False),
    ("已提交", "已撤销", "cancel", True),
    ("已确认", "已撤销", "cancel", True),
    ("已下单", "已撤销", "cancel", True),
    ("准备排货", "已撤销", "cancel", True),
    ("已排货", "已撤销", "cancel", True),
]


def mint(cur, actor, sku, total=100, state="已提交", birth=True):
    """铸出一条记录：行带 state 插入 + 追加铸出事件（S-2）。"""
    cur.execute("INSERT INTO plan (title, period_start, months, owner_actor, created_by)"
                " VALUES ('t', %s, 3, %s, %s) RETURNING plan_id", (OCT, actor, actor))
    plan_id = cur.fetchone()[0]
    cur.execute("INSERT INTO plan_rev (plan_id, rev, content_digest, submitted_by)"
                " VALUES (%s, 1, 'd', %s)", (plan_id, actor))
    cur.execute(
        "INSERT INTO plan_line (plan_id, rev, sku, period_start, total_units,"
        " demand_by_seller, demand_at_submit, state)"
        " VALUES (%s, 1, %s, %s, %s, '{}'::jsonb, 0, %s) RETURNING line_id",
        (plan_id, sku, OCT, total, state))
    line_id = cur.fetchone()[0]
    if birth:
        cur.execute("INSERT INTO plan_line_event (line_id, from_state, to_state, actor, src)"
                    " VALUES (%s, '[*]', %s, %s, 'test')", (line_id, state, actor))
    return plan_id, line_id


def test_t21_whitelist_matches_the_document_row_by_row(wipe):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT from_state, to_state, kind, needs_reason FROM plan_line_transition"
                    " ORDER BY from_state, to_state")
        got = cur.fetchall()
    assert sorted(got) == sorted(WHITELIST)


def test_only_one_back_edge_exists(wipe):
    """★ 04:800：back 只有一行。多一行就要重新论证「状态只前进」还成不成立。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT from_state, to_state FROM plan_line_transition WHERE kind = 'back'")
        assert cur.fetchall() == [("已确认", "已提交")]


def test_terminal_states_never_appear_as_from_state(wipe):
    """★ 终态不可离开，由外键裁决 —— 表里没有以它们作 from_state 的行。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM plan_line_transition"
                    " WHERE from_state IN ('已完结','已撤销')")
        assert cur.fetchone()[0] == 0


def test_t1_illegal_transition_is_a_foreign_key_violation(wipe, seed):
    with pg_conn() as c, c.cursor() as cur:
        _, line = mint(cur, seed.actor, seed.sku_a)
        with pytest.raises(psycopg2.errors.ForeignKeyViolation) as ei:
            cur.execute("INSERT INTO plan_line_event (line_id, from_state, to_state, actor)"
                        " VALUES (%s, '已提交', '已排货', %s)", (line, seed.actor))
        assert ei.value.diag.constraint_name == "plan_line_event_transition_fk"


def test_t2_terminal_state_cannot_be_left(wipe, seed):
    with pg_conn() as c, c.cursor() as cur:
        _, line = mint(cur, seed.actor, seed.sku_a)
        cur.execute("INSERT INTO plan_line_event (line_id, from_state, to_state, actor, reason)"
                    " VALUES (%s, '已提交', '已撤销', %s, '不做了')", (line, seed.actor))
        cur.execute("SELECT state FROM plan_line WHERE line_id = %s", (line,))
        assert cur.fetchone()[0] == "已撤销"
        with pytest.raises(psycopg2.errors.ForeignKeyViolation):
            cur.execute("INSERT INTO plan_line_event (line_id, from_state, to_state, actor)"
                        " VALUES (%s, '已撤销', '已确认', %s)", (line, seed.actor))


def test_t3_direct_update_of_state_is_refused(wipe, seed):
    with pg_conn() as c, c.cursor() as cur:
        _, line = mint(cur, seed.actor, seed.sku_a)
        with pytest.raises(psycopg2.errors.RaiseException) as ei:
            cur.execute("UPDATE plan_line SET state = '已确认' WHERE line_id = %s", (line,))
        assert "只能由事件表推进" in str(ei.value)


def test_t6_total_zero_never_exists_so_it_cannot_auto_advance(wipe, seed):
    """★ 坑①：量为 0 的记录 0 = 0 恒真，一诞生就该是「已确认」——
    所以它根本不许存在（002 的 CHECK），这里从状态机这一侧再确认一次。"""
    with pg_conn() as c, c.cursor() as cur:
        with pytest.raises(psycopg2.errors.CheckViolation):
            mint(cur, seed.actor, seed.sku_a, total=0)


def test_event_table_is_append_only(wipe, seed):
    with pg_conn() as c, c.cursor() as cur:
        _, line = mint(cur, seed.actor, seed.sku_a)
        with pytest.raises(psycopg2.errors.RaiseException):
            cur.execute("UPDATE plan_line_event SET actor = 'x' WHERE line_id = %s", (line,))
    with pg_conn() as c, c.cursor() as cur:
        with pytest.raises(psycopg2.errors.RaiseException):
            cur.execute("DELETE FROM plan_line_event WHERE line_id = %s", (line,))


def test_line_without_birth_event_is_refused_at_commit(wipe, seed):
    """★ S-2：插了行却不记事件 = 静默绕过审计链。延迟约束在 COMMIT 时抓它。"""
    with pytest.raises(psycopg2.errors.RaiseException) as ei:
        with pg_conn() as c, c.cursor() as cur:
            mint(cur, seed.actor, seed.sku_a, birth=False)
    assert "没有铸出事件" in str(ei.value)


def test_birth_event_must_agree_with_the_row_it_mints(wipe, seed):
    """行带 '已确认' 插入 + 铸出事件 → 必须炸：铸出只能落在「已提交」。"""
    with pytest.raises(psycopg2.errors.RaiseException) as ei:
        with pg_conn() as c, c.cursor() as cur:
            mint(cur, seed.actor, seed.sku_a, state="已确认")
    assert "铸出事件要求行处于 已提交" in str(ei.value)


def test_from_state_mismatch_is_refused(wipe, seed):
    """乐观校验：事件声称的 from_state 与行的当前状态不符 → 硬失败。"""
    with pg_conn() as c, c.cursor() as cur:
        _, line = mint(cur, seed.actor, seed.sku_a)
        with pytest.raises(psycopg2.errors.RaiseException) as ei:
            cur.execute("INSERT INTO plan_line_event (line_id, from_state, to_state, actor)"
                        " VALUES (%s, '已确认', '已下单', %s)", (line, seed.actor))
        assert "from_state 不匹配" in str(ei.value)


def test_cancel_without_reason_is_refused(wipe, seed):
    with pg_conn() as c, c.cursor() as cur:
        _, line = mint(cur, seed.actor, seed.sku_a)
        with pytest.raises(psycopg2.errors.RaiseException) as ei:
            cur.execute("INSERT INTO plan_line_event (line_id, from_state, to_state, actor)"
                        " VALUES (%s, '已提交', '已撤销', %s)", (line, seed.actor))
        assert "必须写理由" in str(ei.value)


def test_rev_leaves_in_flight_when_every_line_is_terminal(wipe, seed):
    with pg_conn() as c, c.cursor() as cur:
        plan, line = mint(cur, seed.actor, seed.sku_a)
        cur.execute("SELECT in_flight FROM plan_rev WHERE plan_id = %s", (plan,))
        assert cur.fetchone()[0] is True
        cur.execute("INSERT INTO plan_line_event (line_id, from_state, to_state, actor, reason)"
                    " VALUES (%s, '已提交', '已撤销', %s, '不做了')", (line, seed.actor))
        cur.execute("SELECT in_flight FROM plan_rev WHERE plan_id = %s", (plan,))
        assert cur.fetchone()[0] is False, "★ 全部记录进终态了，这一版还占着在流转位"
```

- [ ] **Step 2: 跑测试，确认它红**

Run: `python -m pytest tests/test_ddl_003_state_machine.py -q`
Expected: FAIL —— `UndefinedTable: relation "plan_line_transition" does not exist`（14 项全红）

- [ ] **Step 3: 写迁移**

`migrations/pg/003_plan_state_machine.sql`：

```sql
-- 003 · S1 状态机四件套：白名单 · 事件表 · 触发器
-- 出处：03 §3 / §3.2 · 04 §1 / §2 · S-2（哨兵与铸出事件）· S-4（in_flight）

-- ① 白名单：状态机本身是数据
CREATE TABLE IF NOT EXISTS plan_line_transition (
    from_state   text    NOT NULL,
    to_state     text    NOT NULL,
    kind         text    NOT NULL CONSTRAINT plan_line_transition_kind_known
                         CHECK (kind IN ('forward','back','cancel')),
    needs_reason boolean NOT NULL DEFAULT false,
    CONSTRAINT plan_line_transition_pkey PRIMARY KEY (from_state, to_state)
);

INSERT INTO plan_line_transition VALUES
 ('[*]','已提交','forward',false),          -- ★ S-2 哨兵：铸出这一步（04:67 S1-1）也留痕，事件链不断
 ('已提交','已确认','forward',false),
 ('已确认','已下单','forward',false),
 ('已下单','准备排货','forward',false),
 ('准备排货','已排货','forward',false),
 ('已排货','已完结','forward',false),
 ('已确认','已提交','back',   false),       -- ★ 全服务唯一一条退回
 ('已提交','已撤销','cancel', true),
 ('已确认','已撤销','cancel', true),
 ('已下单','已撤销','cancel', true),
 ('准备排货','已撤销','cancel', true),
 ('已排货','已撤销','cancel', true)
ON CONFLICT DO NOTHING;
-- ★ 表里没有以 已完结/已撤销 作 from_state 的行 → 终态不可离开，由外键裁决。

-- ② 事件表：唯一能改状态的入口
CREATE TABLE IF NOT EXISTS plan_line_event (
    seq        bigserial PRIMARY KEY,       -- ★ 序列，不用时间戳（时钟回拨会乱序）
    line_id    bigint NOT NULL REFERENCES plan_line(line_id),
    from_state text   NOT NULL,
    to_state   text   NOT NULL,
    actor      text   NOT NULL,
    reason     text,
    src        text,                        -- 触发来源：哪张采购单/排货单/哪轮回执
    at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT plan_line_event_transition_fk FOREIGN KEY (from_state, to_state)
        REFERENCES plan_line_transition(from_state, to_state)   -- ★ 非法迁移 = 外键违反
);

-- ⓐ 乐观校验 + 写物化列（同时解决并发）
CREATE OR REPLACE FUNCTION sync_plan_line_state() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE cur text;
BEGIN
    SELECT state INTO cur FROM plan_line WHERE line_id = NEW.line_id FOR UPDATE;
    -- ★ 铸出事件：行已带 state='已提交' 插入，这里只核对、不再 UPDATE。
    --   （拦截器只挡 UPDATE OF state，所以 INSERT 那一刻的值必须在这里被验一次，
    --     否则「状态只能由事件推进」在铸记录这一刻就是假的。）
    IF NEW.from_state = '[*]' THEN
        IF cur IS DISTINCT FROM '已提交' THEN
            RAISE EXCEPTION '铸出事件要求行处于 已提交，当前 %', cur;
        END IF;
        RETURN NEW;
    END IF;
    IF cur IS DISTINCT FROM NEW.from_state THEN
        RAISE EXCEPTION 'from_state 不匹配：当前 %，事件称 %', cur, NEW.from_state;
    END IF;
    PERFORM set_config('app.in_state_sync','on',true);
    UPDATE plan_line SET state = NEW.to_state WHERE line_id = NEW.line_id;
    PERFORM set_config('app.in_state_sync','off',true);
    RETURN NEW;
END $$;

-- ⓑ 理由必填
CREATE OR REPLACE FUNCTION require_reason() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE need boolean;
BEGIN
    SELECT needs_reason INTO need FROM plan_line_transition
     WHERE from_state = NEW.from_state AND to_state = NEW.to_state;
    IF need AND coalesce(btrim(NEW.reason),'') = '' THEN
        RAISE EXCEPTION '这条迁移必须写理由：% → %', NEW.from_state, NEW.to_state;
    END IF;
    RETURN NEW;
END $$;

-- ⓒ 禁止直改 state
CREATE OR REPLACE FUNCTION state_must_go_through_event() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF current_setting('app.in_state_sync', true) IS DISTINCT FROM 'on' THEN
        RAISE EXCEPTION '状态只能由事件表推进，不许直接 UPDATE state';
    END IF;
    RETURN NEW;
END $$;

-- ⓓ 每条记录在事务提交前必须有铸出事件
CREATE OR REPLACE FUNCTION assert_birth_event() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM plan_line_event
                    WHERE line_id = NEW.line_id AND from_state = '[*]') THEN
        RAISE EXCEPTION '记录 % 没有铸出事件（[*]→已提交）', NEW.line_id;
    END IF;
    RETURN NULL;
END $$;

-- ⓔ 一版什么时候算「不在流转」：它的记录全进了终态。
-- ★ 一个实现，两个调用者（触发器 + 提交路径）—— 两份实现必然分叉（04:329）。
--   提交路径必须自己调一次，因为**铸出 0 条记录的空版本没有任何行能触发触发器**，
--   而它会永远占着「唯一在流转」那个位子，把这张计划的后续提交全挡住。
CREATE OR REPLACE FUNCTION close_rev_if_settled(p_plan_id bigint, p_rev int)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    UPDATE plan_rev SET in_flight = false
     WHERE plan_id = p_plan_id AND rev = p_rev AND in_flight
       AND NOT EXISTS (SELECT 1 FROM plan_line
                        WHERE plan_id = p_plan_id AND rev = p_rev
                          AND state NOT IN ('已完结','已撤销'));
END $$;

CREATE OR REPLACE FUNCTION sync_rev_in_flight() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM close_rev_if_settled(NEW.plan_id, NEW.rev);
    RETURN NULL;
END $$;

-- ★ 触发器用 DROP IF EXISTS + CREATE：CREATE OR REPLACE TRIGGER 要 PG 14+，
--   而这台实例的版本未实测。
DROP TRIGGER IF EXISTS t_append_only ON plan_line_event;
CREATE TRIGGER t_append_only BEFORE UPDATE OR DELETE ON plan_line_event
    FOR EACH ROW EXECUTE FUNCTION forbid_update_delete();

DROP TRIGGER IF EXISTS t_require_reason ON plan_line_event;
CREATE TRIGGER t_require_reason BEFORE INSERT ON plan_line_event
    FOR EACH ROW EXECUTE FUNCTION require_reason();

DROP TRIGGER IF EXISTS t_sync_state ON plan_line_event;
CREATE TRIGGER t_sync_state AFTER INSERT ON plan_line_event
    FOR EACH ROW EXECUTE FUNCTION sync_plan_line_state();

DROP TRIGGER IF EXISTS t_state_is_readonly ON plan_line;
CREATE TRIGGER t_state_is_readonly BEFORE UPDATE OF state ON plan_line
    FOR EACH ROW EXECUTE FUNCTION state_must_go_through_event();

DROP TRIGGER IF EXISTS c_line_birth_event ON plan_line;
CREATE CONSTRAINT TRIGGER c_line_birth_event AFTER INSERT ON plan_line
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION assert_birth_event();

DROP TRIGGER IF EXISTS t_rev_in_flight ON plan_line;
CREATE TRIGGER t_rev_in_flight AFTER INSERT OR UPDATE OF state ON plan_line
    FOR EACH ROW EXECUTE FUNCTION sync_rev_in_flight();
```

- [ ] **Step 4: 跑测试，确认它绿**

Run: `python -m pytest tests/test_ddl_003_state_machine.py -q`
Expected: PASS（14 项）

- [ ] **Step 5: 全量跑一次迁移相关测试**

Run: `python -m pytest tests/test_migrations.py tests/test_ddl_001_foundation.py tests/test_ddl_002_plan.py tests/test_ddl_003_state_machine.py -q`
Expected: PASS（6 + 6 + 12 + 14 = 38 项）

- [ ] **Step 6: 提交**

```bash
git add migrations/pg/003_plan_state_machine.sql tests/test_ddl_003_state_machine.py
git commit -m "feat(db): 003 状态机四件套 —— 白名单含 [*] 哨兵 · 铸出事件延迟约束 · in_flight 单一实现"
```

---
