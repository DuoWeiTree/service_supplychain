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
| 错误形状 | ★ `{"error": "...", "hint": "...", …点名字段}`（team-lead 2026-09-22 裁定，前端按它写）。`08` §0 写的是 `{code,message,detail}` —— 见「要交回文档」第 4 条 |
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
    ├── helpers.py                      ★ 共用造数（mint / H / prepared）—— 测试之间不互相 import
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
- Create: `tests/helpers.py` · `tests/test_ddl_003_state_machine.py`

**Interfaces:**
- Consumes: Task 4 的 `plan_line` / `plan_rev`
- Produces:
  - `plan_line_transition(from_state, to_state) PK, kind, needs_reason`，**12 行**（含哨兵 `('[*]','已提交')`）
  - `plan_line_event(seq bigserial PK, line_id, from_state, to_state, actor, reason, src, at)`
  - 函数 `sync_plan_line_state()` · `require_reason()` · `state_must_go_through_event()` · `assert_birth_event()` · `close_rev_if_settled(bigint, int) RETURNS void`
  - `tests/helpers.py`: `OCT: date` · `mint(cur, actor, sku, total=100, state="已提交", birth=True) -> tuple[int, int]`（Task 10 / 16 复用）
  - 常量（后续 Task 直接引用）：终态 = `('已完结','已撤销')`；铸出态 = `'已提交'`；哨兵 = `'[*]'`

- [ ] **Step 1: 写失败的状态机测试**

`tests/helpers.py` —— ★ 共用的造数放这里，测试文件之间**不互相 import**：
被 import 的那个文件里的 fixture 会被重复收集，而且哪天它改了名，另一头是在
**收集期**炸的，报错指向的行与真正的原因隔着一层。
（`tests/` 没有 `__init__.py`，pytest 默认把它放进 `sys.path[0]`，所以按模块名 `helpers` 导即可。）

```python
"""测试之间共用的造数。"""
import datetime as dt

OCT = dt.date(2026, 10, 1)


def mint(cur, actor, sku, total=100, state="已提交", birth=True):
    """铸出一条记录：行带 state 插入 + 追加铸出事件（S-2）。

    ★ birth=False 是给「插了行却不记事件」那条测试用的靶子，不是正常路径。
    """
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
```

`tests/test_ddl_003_state_machine.py`：

```python
"""状态机四件套。T1/T2/T3/T6/T21 出自 04 §9 的「必须先红一次」清单。"""
import psycopg2.errors
import pytest
from helpers import mint

from shared.pg_client import pg_conn

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
## Task 6: `forecast/` —— 从老原型移植的两个纯函数 + 逐格对账

**Files:**
- Create: `forecast/estimate.py` · `forecast/projection.py`
- Create: `tests/test_forecast_estimate.py` · `tests/test_forecast_projection.py`

**Interfaces:**
- Consumes: 无（纯函数，L2：只用标准库）
- Produces:
  - `forecast.estimate`: `MonthEstimate(units: int, extrapolated: bool)` · `monthly_estimate(history: list[tuple[date, int]], months: int) -> list[MonthEstimate]` · `InsufficientHistory` · `HistoryGap`
  - `forecast.projection`: `InboundSource(units: int, kind: str, ref: str)` · `inventory_projection(onhand: int | None, inbound_by_month: Mapping[str, Sequence[InboundSource]], expected_by_month: Mapping[str, int | None]) -> list[dict]` · `PeriodMismatch`

### ★ 移植范围：原型 `test_forecast.js` 的哪几节搬、哪几节不搬

> 统计被丢掉的那一侧 —— 不列出来，「没搬」和「搬漏了」长得一模一样。

| 原型节 | 内容 | 阶段 A | 出处 |
|---|---|---|---|
| §4 恒等式③④ | 期末 = 期初 − 期望 + 入库；期初[m] ≡ 期末[m−1] | ✅ 搬 → `test_forecast_projection.py` | `14` §1.1 |
| §5 恒等式⑥ | 推算入库 ≡ 其构成明细之和 | ✅ 搬（对象换成「采购在途的构成明细」） | `14` §1.1 ⑥ |
| §9 外推标记 | 超出预估范围的月份标 `extrapolated`，且**不许恒真** | ✅ 搬 → `test_forecast_estimate.py` | M-13 · `14` §5 |
| §1 提前期参数 | `make_days` / `ship_days` 校验 | ❌ 不搬 | `00e`:44 阶段 A 不做四段管道 |
| §2 天 → 月锚月中 | `monthAfterDays` | ❌ 不搬 | 同上：阶段 A 的入库量是 CH 现成事实，不需要把天数落回月 |
| §6 改采购量库存要动 | 计划采购量进管道 | ❌ 不搬 | `00e`:43 阶段 A 的库存预估**不含本计划的采购** |
| §7 切国内环境 | `ship_days=3` | ❌ 不搬 | 同 §1 |
| §8 分配合计 ≡ 这批货 | 两遍法逐批分配 | ❌ 不搬 | `14` §3 属管道；阶段 A 无批次 |

★ 因此阶段 A 的 `inventory_projection` 必须在每格的 `basis` 里带 `excludes_plan_purchase: true` ——
**「没算进来」不能和「算进来了但是 0」长得一样**（`00e`:43「标『未计入本计划的采购』」）。

- [ ] **Step 1: 写失败的 estimate 测试**

`tests/test_forecast_estimate.py`：

```python
"""monthly_estimate —— 系统预估销量的 seed 值。

★ 移植自老原型 store.js `rebuildCells`：预估序列**按位置**对齐到计划月份，
  用完了就沿用最后一个值并标 extrapolated（M-13 / 14 §5）。
"""
import datetime as dt

import pytest

from forecast.estimate import HistoryGap, InsufficientHistory, monthly_estimate


def h(*pairs):
    return [(dt.date(y, m, 1), u) for y, m, u in pairs]


def test_history_longer_than_plan_uses_the_most_recent_months():
    got = monthly_estimate(h((2026, 5, 10), (2026, 6, 20), (2026, 7, 30), (2026, 8, 40)), 3)
    assert [x.units for x in got] == [20, 30, 40]
    assert [x.extrapolated for x in got] == [False, False, False]


def test_beyond_the_series_carries_the_last_value_and_says_so():
    """★ 原型 test_forecast.js §9：第 7 个月必须标外推。"""
    got = monthly_estimate(h((2026, 7, 100), (2026, 8, 120), (2026, 9, 90)), 7)
    assert [x.units for x in got] == [100, 120, 90, 90, 90, 90, 90]
    assert [x.extrapolated for x in got] == [False] * 3 + [True] * 4


def test_the_flag_is_not_always_true():
    """★ 原型的原话：否则这个标记等于恒真，白标。"""
    got = monthly_estimate(h((2026, 7, 100), (2026, 8, 120), (2026, 9, 90)), 3)
    assert not any(x.extrapolated for x in got)


def test_empty_history_is_not_zero():
    """★ 没有历史 ≠ 预估 0。调用方要拿到一个硬失败，并把这个 msku 点名。"""
    with pytest.raises(InsufficientHistory):
        monthly_estimate([], 3)


def test_a_gap_in_history_is_refused_and_named():
    """★ 采集缺一天/缺一月是常态，把缺口当 0 会把预估压低而没有任何回声。"""
    with pytest.raises(HistoryGap) as ei:
        monthly_estimate(h((2026, 6, 10), (2026, 8, 20)), 3)
    assert "2026-07" in str(ei.value)


def test_negative_history_is_refused():
    with pytest.raises(ValueError):
        monthly_estimate(h((2026, 8, -1)), 1)


def test_months_must_be_a_positive_int():
    for bad in (0, -1, 3.5):
        with pytest.raises(ValueError):
            monthly_estimate(h((2026, 8, 10)), bad)
```

- [ ] **Step 2: 跑测试，确认它红**

Run: `python -m pytest tests/test_forecast_estimate.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'forecast.estimate'`

- [ ] **Step 3: 实现 `forecast/estimate.py`**

```python
"""系统预估销量：把历史月销量序列铺到计划月份上。

★ 算法照老原型 store.js `rebuildCells` 移植：序列**按位置**消费，用完沿用最后一个值
  并标 extrapolated。换统计口径（均值 / 同比）时换的是本文件这一个函数，
  上面那组测试的形态不变。
"""
from __future__ import annotations

import datetime as dt
from typing import NamedTuple


class MonthEstimate(NamedTuple):
    units: int
    #: ★ 外推 ≠ 预估。这个标记必须随结果一起返回，
    #  让界面不必回头读原始格子 —— 同一个数两个来源迟早分叉（14 §5）。
    extrapolated: bool


class InsufficientHistory(Exception):
    """一条历史都没有。★ 返回 0 会让『新品没数据』和『卖了 0 件』长得一样。"""


class HistoryGap(Exception):
    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__(f"历史销量中间缺月：{missing}。缺口不是 0，不许当 0 用。")


def monthly_estimate(history: list[tuple[dt.date, int]], months: int) -> list[MonthEstimate]:
    if not isinstance(months, int) or isinstance(months, bool) or months < 1:
        raise ValueError(f"months 必须是正整数，收到 {months!r}")
    if not history:
        raise InsufficientHistory("没有任何历史销量")

    rows = sorted(history)
    for d, u in rows:
        if d.day != 1:
            raise ValueError(f"历史的月份必须是月初，收到 {d}")
        if u < 0:
            raise ValueError(f"历史销量为负：{d} = {u}")

    missing = []
    for prev, cur in zip(rows, rows[1:]):
        step = (cur[0].year - prev[0].year) * 12 + cur[0].month - prev[0].month
        for k in range(1, step):
            m = prev[0].month + k
            missing.append(f"{prev[0].year + (m - 1) // 12}-{(m - 1) % 12 + 1:02d}")
    if missing:
        raise HistoryGap(missing)

    series = [u for _, u in rows][-months:]
    out = [MonthEstimate(u, False) for u in series]
    while len(out) < months:
        out.append(MonthEstimate(series[-1], True))
    return out
```

- [ ] **Step 4: 跑测试，确认它绿**

Run: `python -m pytest tests/test_forecast_estimate.py -q`
Expected: PASS（7 项）

- [ ] **Step 5: 写失败的 projection 测试**

`tests/test_forecast_projection.py`：

```python
"""inventory_projection —— 主公式 期末 = 期初 − 期望销量 + 当期入库（14 §0）。

★ 阶段 A 的入库量只有「已确认」这一种（CH 的采购在途），
  本计划的计划采购量**不进**这条公式，而这件事必须在每一格上说出来。
"""
import pytest

from forecast.projection import InboundSource, PeriodMismatch, inventory_projection

P = ["2026-10", "2026-11", "2026-12"]


def src(units, ref="PO-1"):
    return [InboundSource(units, "purchase_in_transit", ref)]


def test_main_formula_holds_in_every_cell():
    """★ 原型 test_forecast.js §4 第一条。"""
    rows = inventory_projection(300, {"2026-10": src(50)}, dict(zip(P, [120, 100, 80])))
    assert rows, "一行都没有 —— 下面的遍历是空转的"
    for r in rows:
        assert r["closing"] == r["opening"] - r["demand"] + r["inbound"]
    assert [r["closing"] for r in rows] == [230, 130, 50]


def test_opening_is_last_months_closing():
    """★ 原型 test_forecast.js §4 第二条（恒等式④）。"""
    rows = inventory_projection(300, {}, dict(zip(P, [120, 100, 80])))
    for prev, cur in zip(rows, rows[1:]):
        assert cur["opening"] == prev["closing"]


def test_inbound_equals_the_sum_of_its_sources():
    """★ 恒等式⑥：有结论必有依据（原型 §5）。合计由本函数求和，不靠调用方自觉。"""
    rows = inventory_projection(0, {"2026-10": [InboundSource(30, "purchase_in_transit", "PO-1"),
                                                InboundSource(20, "purchase_in_transit", "PO-2")]},
                                dict(zip(P, [0, 0, 0])))
    first = rows[0]
    assert first["inbound"] == sum(s["units"] for s in first["basis"]["sources"]) == 50


def test_every_cell_says_the_plan_purchase_is_not_counted():
    """★ 「没算进来」不能和「算进来了但是 0」长得一样（00e:43）。"""
    rows = inventory_projection(10, {}, dict(zip(P, [1, 1, 1])))
    assert all(r["basis"]["excludes_plan_purchase"] is True for r in rows)


def test_unknown_demand_propagates_forward_and_is_not_zero():
    """★ M-8：留空 = 未知，向后传染，不当 0。"""
    rows = inventory_projection(100, {}, {"2026-10": 30, "2026-11": None, "2026-12": 10})
    assert [r["closing"] for r in rows] == [70, None, None]
    assert [r["unknown"] for r in rows] == [False, True, True]
    assert rows[1]["basis"]["reason"] == "unknown_demand"


def test_not_applicable_is_a_third_state(  ):
    """★ 三种状态必须两两分得开：0（真的没货）· 未知（人没填）· 不适用（该平台无 FBA）。"""
    zero = inventory_projection(0, {}, {"2026-10": 0})
    unknown = inventory_projection(100, {}, {"2026-10": None})
    na = inventory_projection(None, {}, {"2026-10": 5})
    assert zero[0]["closing"] == 0 and not zero[0]["unknown"] and not zero[0]["not_applicable"]
    assert unknown[0]["closing"] is None and unknown[0]["unknown"]
    assert na[0]["closing"] is None and na[0]["not_applicable"]
    assert na[0]["basis"]["reason"] == "not_applicable"
    assert not na[0]["unknown"], "不适用不是未知 —— 处置完全不同"


def test_shortage_and_gap_are_reported():
    rows = inventory_projection(50, {}, {"2026-10": 80})
    assert rows[0]["closing"] == -30 and rows[0]["shortage"] and rows[0]["gap"] == 30


def test_inbound_in_a_period_nobody_planned_is_refused():
    """★ 被丢掉的那一侧要硬失败：入库落在期望销量没有的月份上，静默忽略就是漏货。"""
    with pytest.raises(PeriodMismatch) as ei:
        inventory_projection(0, {"2027-05": src(10)}, {"2026-10": 1})
    assert "2027-05" in str(ei.value)
```

- [ ] **Step 6: 跑测试，确认它红**

Run: `python -m pytest tests/test_forecast_projection.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'forecast.projection'`

- [ ] **Step 7: 实现 `forecast/projection.py`**

```python
"""库存推演。阶段 A 的口径（00e §1）：

    期末 = 期初 − 期望销量 + 当期入库（在仓 + 采购在途，全是 CH 现成事实）

★ 本计划的计划采购量**不在**加项里 —— 它要穿过四段管道才可售，而管道属阶段 B/C。
  所以每一格都带 excludes_plan_purchase，把「没算进来」说出来。
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import NamedTuple


class InboundSource(NamedTuple):
    units: int
    kind: str       # 阶段 A 只有 'purchase_in_transit'
    ref: str        # 单号等可回查的依据


class PeriodMismatch(Exception):
    def __init__(self, extra: list[str]):
        super().__init__(f"入库落在没有期望销量的月份上：{extra}。静默忽略就是漏货。")


def inventory_projection(
    onhand: int | None,
    inbound_by_month: Mapping[str, Sequence[InboundSource]],
    expected_by_month: Mapping[str, int | None],
) -> list[dict]:
    """periods 由 expected_by_month 的键给出（每个计划月都必须有键，值可以是 None）。

    ★ inbound 传的是**明细**不是合计：合计在这里求和，
      「有结论必有依据」就不依赖调用方自觉（恒等式⑥）。
    """
    periods = sorted(expected_by_month)
    extra = sorted(set(inbound_by_month) - set(periods))
    if extra:
        raise PeriodMismatch(extra)

    rows: list[dict] = []
    cur: int | None = onhand
    unknown = False
    for period in periods:
        sources = list(inbound_by_month.get(period, ()))
        inbound = sum(s.units for s in sources)
        demand = expected_by_month[period]
        basis = {
            "opening": cur,
            "demand": demand,
            "inbound": inbound,
            "sources": [{"units": s.units, "kind": s.kind, "ref": s.ref} for s in sources],
            # ★ 00e:43 要求标出来：这条曲线里没有本计划的采购
            "excludes_plan_purchase": True,
        }
        if onhand is None:
            # ★ 该店铺没有 FBA（02 §3.1a）—— 不适用，不是 0，也不是未知
            basis["reason"] = "not_applicable"
            rows.append({"period": period, "opening": None, "demand": demand,
                         "inbound": inbound, "closing": None, "shortage": False, "gap": 0,
                         "unknown": False, "not_applicable": True, "basis": basis})
            continue
        if demand is None:
            unknown = True
        if unknown:
            basis["reason"] = "unknown_demand"
            rows.append({"period": period, "opening": cur, "demand": demand,
                         "inbound": inbound, "closing": None, "shortage": False, "gap": 0,
                         "unknown": True, "not_applicable": False, "basis": basis})
            cur = None
            continue
        closing = cur - demand + inbound
        rows.append({"period": period, "opening": cur, "demand": demand,
                     "inbound": inbound, "closing": closing,
                     "shortage": closing < 0, "gap": -closing if closing < 0 else 0,
                     "unknown": False, "not_applicable": False, "basis": basis})
        cur = closing
    return rows
```

- [ ] **Step 8: 跑测试，确认它绿**

Run: `python -m pytest tests/test_forecast_projection.py tests/test_layering.py -q`
Expected: PASS（8 + 9 项；L2 仍绿 —— `forecast/` 只 import 了 `datetime` / `collections.abc` / `typing`）

- [ ] **Step 9: 提交**

```bash
git add forecast tests/test_forecast_estimate.py tests/test_forecast_projection.py
git commit -m "feat(forecast): 移植系统预估（含外推标记）与库存推演（主公式 + 依据回显）"
```

---

## Task 7: `dim/` —— 可替换的 Source 协议 · FixtureSource · 显式未做的 ChSource

**Files:**
- Create: `dim/source.py` · `dim/fixture_source.py` · `dim/ch_source.py`
- Create: `tests/fixtures/sales_history.csv` · `tests/fixtures/fba_onhand.csv` · `tests/fixtures/purchase_in_transit.csv`
- Create: `tests/test_dim_fixture.py`

**Interfaces:**
- Consumes: `forecast.projection.InboundSource`（转换时用）
- Produces:
  - `dim.source`: `Source`（Protocol）· `InTransit(period: str, units: int, ref: str)`
  - `Source.monthly_sales_history(seller_sku: str, sid: str, months: int) -> list[tuple[date, int]]`
  - `Source.onhand_available(seller_sku: str, sid: str) -> int | None`（★ `None` = 不适用）
  - `Source.purchase_in_transit(sku: str) -> list[InTransit]`
  - `dim.fixture_source.FixtureSource(root: Path)` · `dim.ch_source.ChSource`

- [ ] **Step 1: 写失败的测试与 fixture**

`tests/fixtures/sales_history.csv`：

```csv
seller_sku,sid,month,units
MSKU-A,11072,2026-07,100
MSKU-A,11072,2026-08,120
MSKU-A,11072,2026-09,90
MSKU-B,11072,2026-07,10
MSKU-B,11072,2026-08,12
MSKU-B,11072,2026-09,9
MSKU-C,11094,2026-08,40
MSKU-C,11094,2026-09,44
```

`tests/fixtures/fba_onhand.csv`（★ 末行的空 units 是「不适用」，不是 0）：

```csv
seller_sku,sid,units
MSKU-A,11072,300
MSKU-B,11072,0
MSKU-C,11094,25
MSKU-W,90001,
```

`tests/fixtures/purchase_in_transit.csv`：

```csv
sku,period,units,ref
DCC1800264G1,2026-10,50,PO-2026-0912
DCC1800264G1,2026-10,30,PO-2026-0915
DCC1800264G1,2026-11,120,PO-2026-0920
```

`tests/test_dim_fixture.py`：

```python
import datetime as dt
from pathlib import Path

import pytest

from dim.ch_source import ChSource
from dim.fixture_source import FixtureSource

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def s():
    return FixtureSource(FIX)


def test_history_is_month_first_dates_in_order(s):
    assert s.monthly_sales_history("MSKU-A", "11072", 3) == [
        (dt.date(2026, 7, 1), 100), (dt.date(2026, 8, 1), 120), (dt.date(2026, 9, 1), 90)]


def test_history_of_an_unknown_msku_is_empty_not_zero(s):
    """★ 没有行 ≠ 卖了 0 件。调用方要能区分，所以这里返回空列表而不是 [0,0,0]。"""
    assert s.monthly_sales_history("MSKU-NEW", "11072", 3) == []


def test_zero_onhand_and_not_applicable_are_different(s):
    """★ MSKU-B 真的是 0；MSKU-W 所在的店没有 FBA —— 空串必须读成 None。"""
    assert s.onhand_available("MSKU-B", "11072") == 0
    assert s.onhand_available("MSKU-W", "90001") is None


def test_in_transit_keeps_每笔的依据(s):
    rows = s.purchase_in_transit("DCC1800264G1")
    assert [(r.period, r.units, r.ref) for r in rows] == [
        ("2026-10", 50, "PO-2026-0912"),
        ("2026-10", 30, "PO-2026-0915"),
        ("2026-11", 120, "PO-2026-0920")]


def test_unknown_sku_has_no_in_transit_rows(s):
    assert s.purchase_in_transit("NO-SUCH") == []


def test_ch_source_is_explicitly_not_implemented():
    """★ 空实现会让「该做没做」和「本来就不用做」长得一模一样（规则五）。"""
    ch = ChSource()
    for call in (lambda: ch.monthly_sales_history("MSKU-A", "11072", 3),
                 lambda: ch.onhand_available("MSKU-A", "11072"),
                 lambda: ch.purchase_in_transit("DCC1800264G1")):
        with pytest.raises(NotImplementedError) as ei:
            call()
        assert "阶段" in str(ei.value)
```

- [ ] **Step 2: 跑测试，确认它红**

Run: `python -m pytest tests/test_dim_fixture.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'dim.ch_source'`

- [ ] **Step 3: 实现三个文件**

`dim/source.py`：

```python
"""取数口子。阶段 A 只有 FixtureSource 跑得起来，真 CH 实现显式未做。

★ 做成协议是为了让接口层**不知道**数据从哪来 —— 阶段 B 换成 ChSource 时
  改的只有装配那一行。
"""
from __future__ import annotations

import datetime as dt
from typing import NamedTuple, Protocol


class InTransit(NamedTuple):
    period: str     # "YYYY-MM"
    units: int
    ref: str        # 可回查的单号


class Source(Protocol):
    def monthly_sales_history(self, seller_sku: str, sid: str, months: int
                              ) -> list[tuple[dt.date, int]]:
        """最近若干个完整自然月的实际销量，按月升序。★ 没有行就返回空列表 —— 不补 0。"""

    def onhand_available(self, seller_sku: str, sid: str) -> int | None:
        """可售在仓。★ None = 不适用（该店铺没有 FBA），与 0 是两回事。"""

    def purchase_in_transit(self, sku: str) -> list[InTransit]:
        """采购在途（CH `quantity_receive`）。★ 返回逐笔明细，合计由调用方求和。"""
```

`dim/fixture_source.py`：

```python
"""CSV 底料。阶段 A 的唯一数据源，也是前端 mock 与真 API 同源的那一份。

★ 空串读成 None，不读成 0 —— 旧项目冻结 fixture 时踩过：
  空串被读成 0 之后，「不适用」和「真的没有」再也分不开。
"""
from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

from dim.source import InTransit


def _int_or_none(s: str) -> int | None:
    s = (s or "").strip()
    return int(s) if s else None


class FixtureSource:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _rows(self, name: str) -> list[dict]:
        with open(self.root / name, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def monthly_sales_history(self, seller_sku: str, sid: str, months: int
                              ) -> list[tuple[dt.date, int]]:
        hit = [r for r in self._rows("sales_history.csv")
               if r["seller_sku"] == seller_sku and r["sid"] == sid]
        out = [(dt.date.fromisoformat(r["month"] + "-01"), int(r["units"])) for r in hit]
        return sorted(out)[-months:]

    def onhand_available(self, seller_sku: str, sid: str) -> int | None:
        for r in self._rows("fba_onhand.csv"):
            if r["seller_sku"] == seller_sku and r["sid"] == sid:
                return _int_or_none(r["units"])
        return None

    def purchase_in_transit(self, sku: str) -> list[InTransit]:
        return [InTransit(r["period"], int(r["units"]), r["ref"])
                for r in self._rows("purchase_in_transit.csv") if r["sku"] == sku]
```

★ `onhand_available` 对「表里没有这一行」也返回 `None`，与「该店无 FBA」同一个值 ——
这是刻意的：两者的处置相同（都不能当 0 参与推演），而**接口层会把它标成 `not_applicable`
并把 msku 点名**（Task 12 的 `GET /grid`）。要区分成因，靠的是镜像里的 `seller.has_fba`，
不是这里的返回值。

`dim/ch_source.py`：

```python
"""真 CH 取数。★ 阶段 A 没有实现 —— 刻意抛，不给空实现。

空实现会让「该做没做」和「本来就不用做」长得一模一样（01 规则五）。
阶段 B 接上时，这三个方法各自的取数口径在 08 §2.3 与 CLAUDE.md「取数的四条铁律」里。
"""
from __future__ import annotations

import datetime as dt

from dim.source import InTransit

_MSG = ("CH 取数属阶段 B：请按 08 §2.3 实现（商品目录 LEFT JOIN 快照、"
        "msku→货号 按 as_of argMax 但 sid 不参与、日报先按 _captured_date 去重并比对覆盖面）")


class ChSource:
    def monthly_sales_history(self, seller_sku: str, sid: str, months: int
                              ) -> list[tuple[dt.date, int]]:
        raise NotImplementedError(_MSG)

    def onhand_available(self, seller_sku: str, sid: str) -> int | None:
        raise NotImplementedError(_MSG)

    def purchase_in_transit(self, sku: str) -> list[InTransit]:
        raise NotImplementedError(_MSG)
```

- [ ] **Step 4: 跑测试，确认它绿**

Run: `python -m pytest tests/test_dim_fixture.py tests/test_layering.py -q`
Expected: PASS（6 + 9 项）

- [ ] **Step 5: 提交**

```bash
git add dim tests/fixtures tests/test_dim_fixture.py
git commit -m "feat(dim): Source 协议 + FixtureSource（空串读成 None）+ 显式未做的 ChSource"
```

---

## Task 8: `rules/` —— 生效值 · 提交闸 · content_digest

**Files:**
- Create: `rules/effective.py` · `rules/submit.py` · `rules/digest.py`
- Create: `tests/test_rules_submit.py`

**Interfaces:**
- Consumes: 无（纯判据，L1：只用标准库）
- Produces:
  - `rules.effective`: `Effective(units: int | None, basis: str)`（`basis ∈ {"human","system","unknown"}`）· `effective_demand(system_units, expected_units) -> Effective`
  - `rules.submit`: `PurchaseCell(sku, period, planned_units)` · `DemandCell(seller_sku, sid, sku, period, system_units, expected_units)` · `Minted(sku, period, total_units, demand_by_seller, demand_at_submit)` · `Skipped(sku, period, reason)` · `select_submittable(purchase_cells, demand_cells, claimed) -> tuple[list[Minted], list[Skipped]]`
  - `rules.digest`: `content_digest(purchase_cells, demand_cells) -> str`

- [ ] **Step 1: 写失败的测试**

`tests/test_rules_submit.py`：

```python
"""提交闸：铸出哪些、跳过哪些、冻结成什么。

★ 判据② 要证的不只是「有留痕」，还要证「该跳的都跳了、不该跳的没跳」。
"""
import pytest

from rules.digest import content_digest
from rules.effective import effective_demand
from rules.submit import DemandCell, PurchaseCell, select_submittable

SKU = "DCC1800264G1"
OCT, NOV = "2026-10", "2026-11"
A, B = ("MSKU-A", "11072"), ("MSKU-C", "11094")


def dcell(m, period, system, expected):
    return DemandCell(m[0], m[1], SKU, period, system, expected)


def test_effective_value_keeps_both_the_number_and_where_it_came_from():
    """★ S-15：生效值取 COALESCE(人填, 系统)，但 basis 必须记下来 ——
    不记的话，冻结之后再也分不清这 300 件是人写的还是采用了预估。"""
    assert effective_demand(120, 130) == (130, "human")
    assert effective_demand(120, None) == (120, "system")
    assert effective_demand(None, None) == (None, "unknown")
    assert effective_demand(None, 0) == (0, "human"), "★ 人填 0 是明确表态，不是未填"


def test_mints_one_line_per_purchase_cell_with_units():
    minted, skipped = select_submittable(
        [PurchaseCell(SKU, OCT, 500)],
        [dcell(A, OCT, 100, 120), dcell(B, OCT, 40, None)],
        claimed={A, B})
    assert skipped == []
    assert len(minted) == 1
    m = minted[0]
    assert (m.sku, m.period, m.total_units) == (SKU, OCT, 500)
    assert m.demand_by_seller["11072"]["units"] == 120
    assert m.demand_by_seller["11072"]["basis"] == "human"
    assert m.demand_by_seller["11094"]["units"] == 40
    assert m.demand_by_seller["11094"]["basis"] == "system"
    assert m.demand_at_submit == 160


def test_zero_or_empty_purchase_is_skipped_with_its_reason():
    """★ 坑①：总量 0 的记录 0 = 0 恒真，一诞生就会自动跳到「已确认」。"""
    minted, skipped = select_submittable(
        [PurchaseCell(SKU, OCT, 0), PurchaseCell(SKU, NOV, None)],
        [dcell(A, OCT, 100, 120), dcell(A, NOV, 100, 120)], claimed={A})
    assert minted == []
    assert [(s.period, s.reason) for s in skipped] == [
        (OCT, "zero_purchase"), (NOV, "zero_purchase")]


def test_purchase_cell_without_any_claimed_msku_is_skipped():
    """释放了认领之后，货号级的采购格子还在 —— 它铸不出记录，必须点名。"""
    minted, skipped = select_submittable(
        [PurchaseCell(SKU, OCT, 500)], [], claimed=set())
    assert minted == []
    assert [(s.period, s.reason) for s in skipped] == [(OCT, "no_claimed_msku")]


def test_structural_reason_wins_over_zero():
    """两个理由同时成立时取哪个是定死的：没有落点是结构问题，先报它。
    不定死，同一份数据两次提交会给出两种理由，而 skipped[] 是要给人看的。"""
    _, skipped = select_submittable([PurchaseCell(SKU, OCT, 0)], [], claimed=set())
    assert skipped[0].reason == "no_claimed_msku"


def test_all_unknown_demand_is_frozen_as_null_not_zero():
    """★ 空 ≠ 0：一个都没填时，冻结值必须是 null + unknown，而不是 0。"""
    minted, _ = select_submittable(
        [PurchaseCell(SKU, OCT, 500)], [dcell(A, OCT, None, None)], claimed={A})
    entry = minted[0].demand_by_seller["11072"]
    assert entry["units"] is None and entry["basis"] == "unknown"
    assert minted[0].demand_at_submit == 0, "合计只加得起来的那部分"
    assert entry["mskus"] == [{"seller_sku": "MSKU-A", "units": None, "basis": "unknown"}]


def test_a_sellers_basis_is_the_weakest_of_its_mskus():
    """同一店铺下一个 msku 人填、一个没填 —— basis 取最弱的那档。
    取最强的那档，会把「有一半是猜的」说成「人填的」。"""
    minted, _ = select_submittable(
        [PurchaseCell(SKU, OCT, 500)],
        [dcell(A, OCT, 10, 20), dcell(("MSKU-B", "11072"), OCT, None, None)],
        claimed={A, ("MSKU-B", "11072")})
    entry = minted[0].demand_by_seller["11072"]
    assert entry["basis"] == "unknown" and entry["units"] == 20


def test_demand_of_an_unclaimed_msku_is_refused_not_silently_dropped():
    """★ 丢东西必须有声：格子里有个 msku 不在认领集合里 —— 硬失败并点名。"""
    with pytest.raises(ValueError) as ei:
        select_submittable([PurchaseCell(SKU, OCT, 500)], [dcell(B, OCT, 40, 40)], claimed={A})
    assert "MSKU-C" in str(ei.value)


def test_digest_is_stable_under_reordering_and_moves_when_a_value_changes():
    """★ S-13：sha256(规范化 JSON：purchase 行 + demand 行含 basis，键排序，NULL 保留为 null)。"""
    p = [PurchaseCell(SKU, OCT, 500), PurchaseCell(SKU, NOV, None)]
    d = [dcell(A, OCT, 100, 120), dcell(B, OCT, 40, None)]
    base = content_digest(p, d)
    assert base == content_digest(list(reversed(p)), list(reversed(d)))
    assert len(base) == 64
    assert base != content_digest(p, [dcell(A, OCT, 100, 121), dcell(B, OCT, 40, None)])


def test_clearing_a_value_changes_the_digest():
    """★ NULL 保留为 null，所以「把 120 删成空」是一次真实变更 ——
    digest 不动的话，dashboard 的 changed_since_submit[] 就会漏掉这个人。"""
    p = [PurchaseCell(SKU, OCT, 500)]
    assert content_digest(p, [dcell(A, OCT, 100, 120)]) != \
           content_digest(p, [dcell(A, OCT, 100, None)])
```

- [ ] **Step 2: 跑测试，确认它红**

Run: `python -m pytest tests/test_rules_submit.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'rules.digest'`

- [ ] **Step 3: 实现三个文件**

`rules/effective.py`：

```python
"""生效值：人填优先，没填就用系统预估，两者都没有就是未知。

★ S-15 的两半缺一不可：COALESCE 给数，basis 给出处。
  只给数，冻结之后「这 300 件是谁给的」永远答不出来（02 §3.1b / M-8）。
"""
from __future__ import annotations

from typing import NamedTuple


class Effective(NamedTuple):
    units: int | None
    basis: str      # human | system | unknown


def effective_demand(system_units: int | None, expected_units: int | None) -> Effective:
    if expected_units is not None:
        return Effective(expected_units, "human")      # ★ 0 也是人的表态
    if system_units is not None:
        return Effective(system_units, "system")
    return Effective(None, "unknown")
```

`rules/submit.py`：

```python
"""提交闸：哪些格子铸成记录、哪些被跳过、各店的期望销量冻结成什么。"""
from __future__ import annotations

from typing import NamedTuple

from rules.effective import effective_demand

#: basis 的强弱序。★ 一个店铺下多个 msku 时取**最弱**的那档 ——
#  取最强的会把「有一半是猜的」说成「人填的」。
_BASIS_RANK = {"human": 0, "system": 1, "unknown": 2}


class PurchaseCell(NamedTuple):
    sku: str
    period: str
    planned_units: int | None


class DemandCell(NamedTuple):
    seller_sku: str
    sid: str
    sku: str
    period: str
    system_units: int | None
    expected_units: int | None


class Minted(NamedTuple):
    sku: str
    period: str
    total_units: int
    demand_by_seller: dict      # {sid: {"units": int|None, "basis": str, "mskus": [...]}}
    demand_at_submit: int


class Skipped(NamedTuple):
    sku: str
    period: str
    reason: str                 # zero_purchase | no_claimed_msku


def select_submittable(
    purchase_cells: list[PurchaseCell],
    demand_cells: list[DemandCell],
    claimed: set[tuple[str, str]],
) -> tuple[list[Minted], list[Skipped]]:
    """`claimed` = 该计划当前仍有效的 (seller_sku, sid) 集合。"""
    stray = sorted({(c.seller_sku, c.sid) for c in demand_cells} - claimed)
    if stray:
        # ★ 过滤掉就是静默丢失：格子存在而认领没了，是数据坏了，不是「少算一点」
        raise ValueError(f"期望销量格子指向未认领的 msku：{stray}")

    skus_with_claim = {c.sku for c in demand_cells}
    minted: list[Minted] = []
    skipped: list[Skipped] = []

    for pc in sorted(purchase_cells):
        if pc.sku not in skus_with_claim:
            # ★ 先判结构：没有落点比「填了 0」更早一步，两个都成立时报这个
            skipped.append(Skipped(pc.sku, pc.period, "no_claimed_msku"))
            continue
        if not pc.planned_units:            # None 或 0
            skipped.append(Skipped(pc.sku, pc.period, "zero_purchase"))
            continue

        by_sid: dict[str, dict] = {}
        for dc in sorted(demand_cells):
            if dc.sku != pc.sku or dc.period != pc.period:
                continue
            eff = effective_demand(dc.system_units, dc.expected_units)
            slot = by_sid.setdefault(dc.sid, {"units": 0, "basis": "human", "mskus": []})
            slot["mskus"].append({"seller_sku": dc.seller_sku,
                                  "units": eff.units, "basis": eff.basis})
            if eff.units is not None:
                slot["units"] += eff.units
            if _BASIS_RANK[eff.basis] > _BASIS_RANK[slot["basis"]]:
                slot["basis"] = eff.basis
        for slot in by_sid.values():
            if all(m["units"] is None for m in slot["mskus"]):
                slot["units"] = None        # ★ 一个都没有 → null，不是 0
        total_demand = sum(s["units"] or 0 for s in by_sid.values())
        minted.append(Minted(pc.sku, pc.period, pc.planned_units, by_sid, total_demand))

    return minted, skipped
```

`rules/digest.py`：

```python
"""content_digest（S-13）：sha256(规范化 JSON)。

★ 算法一变，历史 rev 的 digest 全部失配、dashboard 会把所有人都列进
  changed_since_submit[] —— 所以这里的规范化规则改动等同于一次迁移。
规则：purchase 行 + demand 行（含 basis），键排序，★ NULL 保留为 null。
"""
from __future__ import annotations

import hashlib
import json

from rules.effective import effective_demand


def content_digest(purchase_cells, demand_cells) -> str:
    payload = {
        "purchase": sorted(
            [{"sku": c.sku, "period": c.period, "planned_units": c.planned_units}
             for c in purchase_cells],
            key=lambda r: (r["sku"], r["period"])),
        "demand": sorted(
            [{"seller_sku": c.seller_sku, "sid": c.sid, "sku": c.sku, "period": c.period,
              "units": effective_demand(c.system_units, c.expected_units).units,
              "basis": effective_demand(c.system_units, c.expected_units).basis}
             for c in demand_cells],
            key=lambda r: (r["sku"], r["period"], r["sid"], r["seller_sku"])),
    }
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: 跑测试，确认它绿**

Run: `python -m pytest tests/test_rules_submit.py tests/test_layering.py -q`
Expected: PASS（11 + 9 项）

- [ ] **Step 5: 提交**

```bash
git add rules tests/test_rules_submit.py
git commit -m "feat(rules): 生效值带 basis · 提交闸的两种跳过理由 · content_digest"
```

---
## Task 9: `api/` 骨架 —— 错误形状 · x-actor · 未声明参数 · 镜像陈旧 503 · /health

**Files:**
- Create: `api/__init__.py`（`create_app`）· `api/ui/errors.py` · `api/ui/deps.py` · `api/ui/system.py`
- Create: `tests/test_api_system.py`
- Modify: `tests/conftest.py`（追加 `client` 夹具）

**Interfaces:**
- Consumes: `shared.pg_client.pg_conn` / `timed` / `pg_error_fields`；`shared.config.freshness`
- Produces:
  - `api.create_app() -> FastAPI`
  - `api.ui.errors`: `ApiError(status, code, message, detail=None)` · `translate(e: psycopg2.Error) -> ApiError | None` · `CONSTRAINT_ERRORS: dict[str, tuple[str, int, str]]`
  - `api.ui.deps`: `actor(request) -> str` · `declared(request, *names) -> None` · `require_fresh_mirrors() -> None`
  - 路由：`GET /health`（★ 不加前缀）· `GET /v1/readiness`
  - fixture `client`：`TestClient(create_app(), raise_server_exceptions=False)`

- [ ] **Step 1: 写失败的测试**

`tests/test_api_system.py`：

```python
"""接口层的地基：错误形状、操作人、未声明参数、镜像陈旧。"""
import datetime as dt

from shared.pg_client import pg_conn


def test_health_has_no_prefix(client):
    """★ 探活目标 = base_url + health.path，不加 path_prefix（08 §0）。"""
    assert client.get("/health").status_code == 200
    assert client.get("/v1/health").status_code == 404


def test_error_shape_is_code_message_detail(client, seed):
    r = client.get("/v1/plans", headers={"x-actor": seed.actor}, params={"stat": "x"})
    assert r.status_code == 400
    body = r.json()
    assert set(body) == {"code", "message", "detail"}
    assert body["code"] == "unknown_query_param"
    # ★ detail 必须点名是哪几个 —— 只说「参数有问题」，前端只能猜
    assert body["detail"]["unknown"] == ["stat"]
    assert "state" in body["detail"]["declared"]


def test_missing_actor_header_is_400(client):
    r = client.get("/v1/plans")
    assert r.status_code == 400 and r.json()["error"] == "unknown_actor"
    assert r.json()["header"] == "x-actor"


def test_unknown_actor_is_400_and_names_it(client, seed):
    r = client.get("/v1/plans", headers={"x-actor": "ghost"})
    assert r.status_code == 400 and r.json()["actor"] == "ghost"


def test_inactive_actor_is_400_and_says_it_is_inactive(client, seed):
    """★ 「查无此人」与「这个人停用了」共用一个 code，但 detail 必须分得开 ——
    不分开，停用的人会以为自己打错了名字。"""
    r = client.get("/v1/plans", headers={"x-actor": seed.actor_inactive})
    assert r.status_code == 400
    assert r.json()["detail"] == {"actor": seed.actor_inactive, "active": False}


def test_stale_mirror_refuses_service_with_503(client, seed):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE seller SET refreshed_at = now() - interval '40 hours'")
    r = client.get("/v1/plans", headers={"x-actor": seed.actor})
    assert r.status_code == 503 and r.json()["error"] == "mirror_stale"
    assert r.json()["stale"][0]["mirror"] == "seller"


def test_empty_mirror_is_stale_not_fresh(client, wipe):
    """★ 空表的 max(refreshed_at) 是 NULL —— 当成新鲜就是拿空表在服务。"""
    r = client.get("/v1/plans", headers={"x-actor": "anyone"})
    assert r.status_code == 503
    assert {m["mirror"] for m in r.json()["stale"]} == {
        "sku_catalog", "msku_bridge", "seller", "warehouse"}


def test_readiness_says_erp_is_not_implemented(client, seed):
    """★ 空实现会让「该做没做」和「本来就不用做」长得一样（规则五）。"""
    body = client.get("/v1/readiness", headers={"x-actor": seed.actor}).json()
    assert body["erp"] == "not_implemented"
    assert body["migrations"][-1].startswith("00")
    assert len(body["mirrors"]) == 4
```

`tests/conftest.py` 追加：

```python
@pytest.fixture
def client(wipe):
    from starlette.testclient import TestClient

    from api import create_app
    return TestClient(create_app(), raise_server_exceptions=False)
```

- [ ] **Step 2: 跑测试，确认它红**

Run: `python -m pytest tests/test_api_system.py -q`
Expected: FAIL —— `ImportError: cannot import name 'create_app' from 'api'`

- [ ] **Step 3: 实现错误与依赖**

`api/ui/errors.py`：

```python
"""错误形状与「库层拒绝 → 错误码」的翻译。

★ 形状按 08 §0：{"code","message","detail"}，detail 必须点名是哪几行。
★ 翻译靠 constraint_name 而不是 str(e)：两种冲突的 message 都长得像一句话，
  真凶只在 pgcode / constraint_name 里。
"""
from __future__ import annotations

import logging

import psycopg2

from shared.pg_client import pg_error_fields

log = logging.getLogger("scm.api")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, detail: dict | None = None):
        self.status, self.code, self.message, self.detail = status, code, message, detail or {}
        super().__init__(f"{status} {code}: {message}")


#: constraint / index 名 → (code, status, message)
CONSTRAINT_ERRORS: dict[str, tuple[str, int, str]] = {
    "msku_claim_one_active_idx": ("msku_already_claimed", 409,
                                  "该 msku 已被另一张尚未下单的计划占用"),
    "plan_rev_one_in_flight_idx": ("rev_in_flight", 409, "该计划已有一版在流转"),
    "plan_rev_pkey": ("rev_in_flight", 409, "同一版号已被并发提交占用"),
    "plan_line_event_transition_fk": ("illegal_transition", 422, "这条状态迁移不在白名单里"),
    "plan_months_1_24": ("bad_months", 400, "计划跨月数必须在 1~24 之间"),
    "plan_period_start_is_month_start": ("bad_period_start", 400, "起始月必须是月初"),
    "plan_owner_fk": ("unknown_actor", 400, "负责人不在 actor 表里"),
    "plan_demand_cell_claim_fk": ("msku_not_claimed", 409, "没认领就没有格子"),
    "plan_demand_cell_expected_nonneg": ("bad_units", 400, "期望销量不能为负"),
    "plan_purchase_cell_nonneg": ("bad_units", 400, "计划采购量不能为负"),
}


def translate(e: psycopg2.Error) -> ApiError | None:
    """认得出的库层拒绝翻成错误码；认不出的返回 None 让它以 500 冒出来。

    ★ 不许有 else 兜底成某个笼统的 409 —— 那会让一条没人预料到的约束
      看起来像一次正常的业务冲突。
    """
    f = pg_error_fields(e)
    hit = CONSTRAINT_ERRORS.get(f["constraint"] or "")
    if hit is None:
        log.warning("untranslated pg error %s", f)
        return None
    code, status, msg = hit
    return ApiError(status, code, msg, {"constraint": f["constraint"], "pg_detail": f["detail"]})
```

`api/ui/deps.py`：

```python
"""三样每个业务端点都要过的东西：操作人 · 未声明参数 · 镜像新鲜度。"""
from __future__ import annotations

import datetime as dt

from fastapi import Request

from api.ui.errors import ApiError
from shared.config import freshness
from shared.pg_client import pg_conn


def declared(request: Request, *names: str) -> None:
    """★ 未声明查询参数一律 400：拼错的参数被静默忽略时，
    返回的是「全量」而不是报错 —— 而全量看起来完全正常。"""
    extra = sorted(set(request.query_params) - set(names))
    if extra:
        raise ApiError(400, "unknown_query_param", "有未声明的查询参数",
                       {"unknown": extra, "declared": sorted(names)})


def require_fresh_mirrors() -> None:
    """E-4：任一维度镜像陈旧（或为空）→ 拒绝服务。"""
    max_age = dt.timedelta(hours=float(freshness().get("max_age_hours", 24)))
    now = dt.datetime.now(dt.timezone.utc)
    stale = []
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT mirror, refreshed_at FROM v_mirror_freshness ORDER BY mirror")
        for mirror, at in cur.fetchall():
            # ★ NULL 是「一行都没有」，不是「刚刷过」
            if at is None or now - at > max_age:
                stale.append({"mirror": mirror,
                              "refreshed_at": at.isoformat() if at else None})
    if stale:
        raise ApiError(503, "mirror_stale", "维度镜像陈旧，拒绝服务",
                       {"stale": stale, "max_age_hours": max_age.total_seconds() / 3600})


def actor(request: Request) -> str:
    """操作人来自 x-actor，必须存在且在职。

    ★ 校验的是「这个人存不存在」，不是权限（权限在上层，08 §0）——
      不校验的话，plan.owner_actor 的外键会以 500 的形态在半路炸。
    """
    who = (request.headers.get("x-actor") or "").strip()
    if not who:
        raise ApiError(400, "unknown_actor", "缺少 x-actor 头", {"header": "x-actor"})
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT active FROM actor WHERE actor_id = %s", (who,))
        row = cur.fetchone()
    if row is None:
        raise ApiError(400, "unknown_actor", "x-actor 不在 actor 表里", {"actor": who})
    if not row[0]:
        raise ApiError(400, "unknown_actor", "该操作人已停用", {"actor": who, "active": False})
    return who
```

`api/ui/system.py`：

```python
"""探活与就绪。★ /health 不加前缀（08 §0）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from api.ui.deps import actor, declared
from shared.pg_client import business_schema, pg_conn

health_router = APIRouter()
system_router = APIRouter()


@health_router.get("/health")
def health():
    return {"status": "ok"}


@system_router.get("/readiness")
def readiness(request: Request, who: str = Depends(actor)):
    declared(request)
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT mirror, refreshed_at FROM v_mirror_freshness ORDER BY mirror")
        mirrors = [{"mirror": m, "refreshed_at": a.isoformat() if a else None}
                   for m, a in cur.fetchall()]
        cur.execute("SELECT version FROM schema_migration ORDER BY version")
        versions = [r[0] for r in cur.fetchall()]
    return {
        "schema": business_schema(),
        "migrations": versions,
        "mirrors": mirrors,
        # ★ 阶段 A 没有领星网关。写成 "ok" 或干脆不返回，都会让
        #   「该做没做」和「本来就不用做」长得一模一样（01 规则五）。
        "erp": "not_implemented",
    }
```

`api/__init__.py`：

```python
"""api/ui —— 给本仓前端的 BFF。★ 随界面一起变，不进契约（01 §1.1b）。"""
from __future__ import annotations

import logging

import psycopg2
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from api.ui import system
from api.ui.errors import ApiError, translate
from shared.pg_client import business_schema, pg_conn

log = logging.getLogger("scm.api")


def _log_startup() -> None:
    """★ 配置类问题往启动钩子放，别等第一个请求才炸 ——
    在启动日志第一屏可见，胜过淹没在访问日志里的一片 500。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT mirror, refreshed_at FROM v_mirror_freshness ORDER BY mirror")
        for mirror, at in cur.fetchall():
            log.info("startup mirror=%s refreshed_at=%s", mirror, at)
        cur.execute("SELECT count(*) FROM schema_migration")
        log.info("startup schema=%s migrations=%d", business_schema(), cur.fetchone()[0])


def create_app() -> FastAPI:
    app = FastAPI(title="service_supplychain · api/ui")

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status,
                            content={"code": exc.code, "message": exc.message,
                                     "detail": exc.detail})

    @app.exception_handler(psycopg2.Error)
    async def _pg_error(request: Request, exc: psycopg2.Error):
        translated = translate(exc)
        if translated is None:
            raise exc     # ★ 认不出的约束不许被兜成业务错误，让它以 500 冒出来
        return JSONResponse(status_code=translated.status,
                            content={"code": translated.code, "message": translated.message,
                                     "detail": translated.detail})

    app.include_router(system.health_router)
    app.include_router(system.system_router, prefix="/v1")
    app.add_event_handler("startup", _log_startup)
    return app
```

- [ ] **Step 4: 跑测试，确认它绿（除仍未实现的 /v1/plans 三项）**

Run: `python -m pytest tests/test_api_system.py -q`
Expected: `test_health_has_no_prefix` 与 `test_readiness_says_erp_is_not_implemented` PASS；
其余 6 项 FAIL（`/v1/plans` 还不存在，返回 404）。★ Task 10 之后全绿。

- [ ] **Step 5: 提交**

```bash
git add api tests/test_api_system.py tests/conftest.py
git commit -m "feat(api): 骨架 —— 错误形状按 08 §0 · x-actor 校验 · 未声明参数 400 · 镜像陈旧 503"
```

---

## Task 10: `POST /v1/plans` · `GET /v1/plans` —— 含派生的整体状态

**Files:**
- Create: `migrations/pg/004_plan_overall_state.sql`
- Create: `api/ui/plans.py`
- Create: `tests/test_api_plans.py` · `tests/test_ddl_004_overall_state.py`
- Modify: `api/__init__.py`（挂 `plans` 路由）

**Interfaces:**
- Consumes: Task 9 的 `actor` / `declared` / `require_fresh_mirrors` / `ApiError`
- Produces:
  - 表 `plan_line_state_rank(state text PK, rank int UNIQUE)`（6 行，★ 不含 `已撤销`）
  - 视图 `v_plan_overall_state(plan_id, state_rev, overall)`
  - `POST /v1/plans` body `{"title","period_start","months"}` → `201 {"plan_id"}`
  - `GET /v1/plans?state=&owner=&archived=` → `{"plans":[{plan_id,title,period_start,months,owner_actor,archived_at,overall,state_rev}]}`
  - `api.ui.plans.router`（供后续 Task 往上挂端点）

- [ ] **Step 1: 写失败的测试**

`tests/test_ddl_004_overall_state.py`：

```python
"""整体状态是**派生**的（04 §3），不是字段。两个边界必须显式定义。"""
from helpers import mint

from shared.pg_client import pg_conn


def test_cancelled_is_not_in_the_rank_table(wipe):
    """★ 已撤销是旁路终态，不参与 rank 比较（04:814-819）。
    把它放进 rank 表，木桶会把一张「撤了一条、其余在跑」的计划算成已撤销。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT state FROM plan_line_state_rank ORDER BY rank")
        assert [r[0] for r in cur.fetchall()] == [
            "已提交", "已确认", "已下单", "准备排货", "已排货", "已完结"]


def test_never_submitted_plan_has_no_overall_state(wipe, seed):
    """★ 「从未提交」不是「已撤销」—— 两者在看板上的处置相反。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO plan (title, period_start, months, owner_actor, created_by)"
                    " VALUES ('空的', '2026-10-01', 3, %s, %s) RETURNING plan_id",
                    (seed.actor, seed.actor))
        pid = cur.fetchone()[0]
        cur.execute("SELECT overall FROM v_plan_overall_state WHERE plan_id = %s", (pid,))
        assert cur.fetchone()[0] is None


def test_all_lines_cancelled_makes_the_plan_cancelled(wipe, seed):
    with pg_conn() as c, c.cursor() as cur:
        plan, line = mint(cur, seed.actor, seed.sku_a)
        cur.execute("INSERT INTO plan_line_event (line_id, from_state, to_state, actor, reason)"
                    " VALUES (%s, '已提交', '已撤销', %s, '不做了')", (line, seed.actor))
        cur.execute("SELECT overall FROM v_plan_overall_state WHERE plan_id = %s", (plan,))
        assert cur.fetchone()[0] == "已撤销"
```

`tests/test_api_plans.py`：

```python
import datetime as dt


def mk(client, seed, **kw):
    body = {"title": "10 月计划", "period_start": "2026-10-01", "months": 3} | kw
    return client.post("/v1/plans", json=body, headers={"x-actor": seed.actor})


def test_create_returns_the_new_plan_id(client, seed):
    r = mk(client, seed)
    assert r.status_code == 201 and isinstance(r.json()["plan_id"], int)


def test_months_defaults_to_three(client, seed):
    r = client.post("/v1/plans", json={"title": "t", "period_start": "2026-10-01"},
                    headers={"x-actor": seed.actor})
    pid = r.json()["plan_id"]
    got = client.get("/v1/plans", headers={"x-actor": seed.actor}).json()["plans"]
    assert [p for p in got if p["plan_id"] == pid][0]["months"] == 3


def test_bad_months_and_bad_start_are_400_with_their_own_codes(client, seed):
    assert mk(client, seed, months=25).json()["error"] == "bad_months"
    assert mk(client, seed, period_start="2026-10-15").json()["error"] == "bad_period_start"


def test_list_reports_derived_overall_state_not_a_column(client, seed):
    mk(client, seed)
    rows = client.get("/v1/plans", headers={"x-actor": seed.actor}).json()["plans"]
    assert rows[0]["overall"] is None, "★ 从未提交 → 没有状态，不是 0、不是已撤销"


def test_filters_are_declared_and_typos_are_400(client, seed):
    mk(client, seed)
    ok = client.get("/v1/plans", params={"owner": seed.actor, "archived": "false"},
                    headers={"x-actor": seed.actor})
    assert ok.status_code == 200
    bad = client.get("/v1/plans", params={"owener": seed.actor}, headers={"x-actor": seed.actor})
    assert bad.status_code == 400 and bad.json()["unknown"] == ["owener"]


def test_archived_filter_splits_the_two_sides(client, seed):
    """★ 过滤必须同时统计被丢掉的那一侧：默认不返回已归档的，
    但接口要告诉你被挡掉了几张，而不是让人以为计划凭空少了。"""
    pid = mk(client, seed).json()["plan_id"]
    client.post(f"/v1/plans/{pid}/archive", headers={"x-actor": seed.actor})
    body = client.get("/v1/plans", headers={"x-actor": seed.actor}).json()
    assert body["plans"] == [] and body["excluded"] == {"archived": 1}
```

- [ ] **Step 2: 跑测试，确认它红**

Run: `python -m pytest tests/test_api_plans.py tests/test_ddl_004_overall_state.py -q`
Expected: FAIL —— `UndefinedTable: relation "plan_line_state_rank" does not exist` / `/v1/plans` 404

- [ ] **Step 3: 写迁移 004**

`migrations/pg/004_plan_overall_state.sql`：

```sql
-- 004 · S2 整体状态：派生，不存储（04 §3）
-- ★ rank 做成表而不是代码里的 CASE：木桶口径有三个调用者（列表 / 看板 / 前端），
--   写三遍必然分叉，而分叉的形态是「同一张计划在两个屏幕上状态不同」。

CREATE TABLE IF NOT EXISTS plan_line_state_rank (
    state text PRIMARY KEY,
    rank  int  NOT NULL UNIQUE
);

INSERT INTO plan_line_state_rank VALUES
 ('已提交',1), ('已确认',2), ('已下单',3), ('准备排货',4), ('已排货',5), ('已完结',6)
ON CONFLICT DO NOTHING;
-- ★ 已撤销刻意不在表里：它是旁路终态，不参与 rank 比较（04:814-819）。
--   放进来的话，撤掉一条记录会把整张计划的木桶拉到「已撤销」。

CREATE OR REPLACE VIEW v_plan_overall_state AS
WITH pick AS (
    -- 一张计划最多 1 版在流转（S-4）；没有在流转的就看最近一版
    SELECT DISTINCT ON (plan_id) plan_id, rev
      FROM plan_rev ORDER BY plan_id, in_flight DESC, rev DESC
)
SELECT p.plan_id,
       pick.rev AS state_rev,
       CASE
         -- ★ 从未提交 → NULL。它不是「已撤销」，两者在看板上的处置相反
         WHEN pick.rev IS NULL THEN NULL
         -- ★ 全部撤销、以及一条记录都没有的空计划单 → 已撤销（04 §3.1）
         --   不显式定义，MIN() 会返回 NULL，而 NULL 在下游每一处表现都不一样
         WHEN agg.live = 0 THEN '已撤销'
         ELSE agg.min_state
       END AS overall
  FROM plan p
  LEFT JOIN pick ON pick.plan_id = p.plan_id
  LEFT JOIN LATERAL (
      SELECT count(*) FILTER (WHERE l.state <> '已撤销') AS live,
             (SELECT l2.state
                FROM plan_line l2 JOIN plan_line_state_rank r ON r.state = l2.state
               WHERE l2.plan_id = p.plan_id AND l2.rev = pick.rev
               ORDER BY r.rank LIMIT 1) AS min_state
        FROM plan_line l
       WHERE l.plan_id = p.plan_id AND l.rev = pick.rev
  ) agg ON true;
```

- [ ] **Step 4: 实现 `api/ui/plans.py` 的建与列**

```python
"""计划：建 · 列 · 认领 · 网格 · 两种量 · 归档。"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from api.ui.deps import actor, declared, require_fresh_mirrors
from api.ui.errors import ApiError
from shared.pg_client import pg_conn, timed

router = APIRouter(dependencies=[Depends(require_fresh_mirrors)])


def _date(s: str, field: str) -> dt.date:
    try:
        return dt.date.fromisoformat(s)
    except (TypeError, ValueError):
        raise ApiError(400, "bad_date", f"{field} 不是 YYYY-MM-DD", {"field": field,
                                                                     "got": s}) from None


@router.post("/plans")
def create_plan(request: Request, body: dict, who: str = Depends(actor)):
    title = (body.get("title") or "").strip()
    if not title:
        raise ApiError(400, "title_required", "标题必填", {"field": "title"})
    start = _date(body.get("period_start"), "period_start")
    months = body.get("months", 3)          # ★ M-10：默认 3，范围 1~24 由库层 CHECK 裁决
    with timed("create_plan", actor=who), pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO plan (title, period_start, months, owner_actor, created_by)"
                    " VALUES (%s, %s, %s, %s, %s) RETURNING plan_id",
                    (title, start, months, body.get("owner_actor", who), who))
        plan_id = cur.fetchone()[0]
    return JSONResponse(status_code=201, content={"plan_id": plan_id})


@router.get("/plans")
def list_plans(request: Request, who: str = Depends(actor)):
    declared(request, "state", "owner", "archived")
    q = request.query_params
    want_archived = q.get("archived", "false").lower() == "true"
    where, args = ["(%s OR p.archived_at IS NULL)"], [want_archived]
    if q.get("owner"):
        where.append("p.owner_actor = %s")
        args.append(q["owner"])
    if q.get("state"):
        where.append("v.overall = %s")
        args.append(q["state"])
    sql = ("SELECT p.plan_id, p.title, p.period_start, p.months, p.owner_actor,"
           " p.archived_at, v.overall, v.state_rev"
           " FROM plan p JOIN v_plan_overall_state v USING (plan_id)"
           f" WHERE {' AND '.join(where)} ORDER BY p.plan_id DESC")
    with pg_conn() as c, c.cursor() as cur:
        cur.execute(sql, args)
        plans = [{"plan_id": r[0], "title": r[1], "period_start": r[2].isoformat(),
                  "months": r[3], "owner_actor": r[4],
                  "archived_at": r[5].isoformat() if r[5] else None,
                  "overall": r[6], "state_rev": r[7]} for r in cur.fetchall()]
        # ★ 统计被丢掉的那一侧：不说「挡掉了几张」，人只会觉得计划凭空少了
        cur.execute("SELECT count(*) FROM plan WHERE archived_at IS NOT NULL")
        archived = 0 if want_archived else cur.fetchone()[0]
    return {"plans": plans, "excluded": {"archived": archived}}
```

`api/__init__.py` 里追加：

```python
from api.ui import plans
...
    app.include_router(plans.router, prefix="/v1")
```

★ `POST /v1/plans/{plan_id}/archive` 在 Task 13 与「同一事务释放全部占用」一起实现；
`test_archived_filter_splits_the_two_sides` 在那之前会红 —— 这是刻意的，它是 Task 13 的靶子。

- [ ] **Step 5: 跑测试**

Run: `python -m pytest tests/test_api_plans.py tests/test_ddl_004_overall_state.py tests/test_api_system.py -q`
Expected: `test_archived_filter_splits_the_two_sides` 以外全绿（6 + 3 + 8 − 1 = 16 项 PASS，1 项 FAIL 等 Task 13）

- [ ] **Step 6: 提交**

```bash
git add migrations/pg/004_plan_overall_state.sql api/ui/plans.py api/__init__.py tests/test_api_plans.py tests/test_ddl_004_overall_state.py
git commit -m "feat(api): 建计划与计划列表；整体状态由 rank 表 + 视图派生，从未提交与已撤销分得开"
```

---

## Task 11: `GET /v1/catalog/skus` · 认领与释放（判据③）

**Files:**
- Create: `api/ui/catalog.py`
- Modify: `api/ui/plans.py`（认领 / 释放）· `api/__init__.py`
- Create: `tests/test_api_claims.py`

**Interfaces:**
- Consumes: Task 10 的 `router`；`forecast.estimate.monthly_estimate`；`dim.fixture_source.FixtureSource`
- Produces:
  - `GET /v1/catalog/skus?q=&limit=` → `{"need_query":bool,"matched":int,"truncated":bool,"rows":[{sku,name,mskus:[{seller_sku,sid,seller_name,selectable,claimed_by:{plan_id,actor}|null}]}]}`
  - `POST /v1/plans/{plan_id}/claims` body `{"seller_sku","sid"}` → `{"claimed":…,"seeded":{"demand_cells":n,"purchase_cells":m},"no_history":[…]}`；409 `msku_already_claimed` 点名占用方
  - `DELETE /v1/plans/{plan_id}/claims/{seller_sku}/{sid}` → `{"released":…,"dropped_cells":[…]}`
  - `api.ui.plans.SOURCE`：模块级 `Source` 实例（阶段 A = `FixtureSource(tests/fixtures)`），Task 12 复用

- [ ] **Step 1: 写失败的测试**

`tests/test_api_claims.py`：

```python
import threading

import psycopg2.errors
import pytest

from shared.pg_client import pg_conn


def H(a):
    return {"x-actor": a}


def mk(client, seed, title="10 月计划"):
    return client.post("/v1/plans", json={"title": title, "period_start": "2026-10-01",
                                          "months": 3}, headers=H(seed.actor)).json()["plan_id"]


def test_catalog_without_a_query_deliberately_returns_nothing(client, seed):
    """★ P11：不给条件 → 故意不返回，且必须与「查不到」长得不一样。"""
    r = client.get("/v1/catalog/skus", headers=H(seed.actor)).json()
    assert r["need_query"] is True and r["rows"] == [] and r["matched"] == 0
    miss = client.get("/v1/catalog/skus", params={"q": "ZZZ"}, headers=H(seed.actor)).json()
    assert miss["need_query"] is False and miss["rows"] == [] and miss["matched"] == 0


def test_claimed_rows_stay_in_the_table_marked(client, seed):
    """★ P11：被占用的行留在表里标出来，不过滤 ——
    过滤掉，人永远不知道「我要的那个为什么没出现」。"""
    p1 = mk(client, seed)
    client.post(f"/v1/plans/{p1}/claims",
                json={"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]}, headers=H(seed.actor))
    rows = client.get("/v1/catalog/skus", params={"q": seed.sku_a},
                      headers=H(seed.actor)).json()["rows"]
    m = [x for x in rows[0]["mskus"] if x["seller_sku"] == seed.msku_a[0]][0]
    assert m["selectable"] is False and m["claimed_by"]["plan_id"] == p1


def test_truncation_is_reported(client, seed):
    r = client.get("/v1/catalog/skus", params={"q": "", "limit": 1}, headers=H(seed.actor))
    assert r.status_code == 200
    body = client.get("/v1/catalog/skus", params={"q": "MSKU", "limit": 1},
                      headers=H(seed.actor)).json()
    assert body["truncated"] is True and len(body["rows"]) == 1


def test_claim_seeds_both_grids_and_names_mskus_without_history(client, seed):
    p = mk(client, seed)
    r = client.post(f"/v1/plans/{p}/claims",
                    json={"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]},
                    headers=H(seed.actor)).json()
    assert r["seeded"] == {"demand_cells": 3, "purchase_cells": 3}
    assert r["no_history"] == []
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT period_start, system_units, system_extrapolated, expected_units"
                    " FROM plan_demand_cell WHERE plan_id = %s ORDER BY period_start", (p,))
        rows = cur.fetchall()
    # fixture 里 MSKU-A 的历史是 100/120/90，计划 3 个月 → 不外推
    assert [r[1] for r in rows] == [100, 120, 90]
    assert [r[2] for r in rows] == [False, False, False]
    assert [r[3] for r in rows] == [None, None, None], "★ 人填列留空 = 未知，不预填"


def test_msku_without_history_is_seeded_null_and_named(client, seed):
    """★ 没有历史 ≠ 预估 0。格子照建，system_units 留 NULL，并在返回里点名。"""
    p = mk(client, seed)
    r = client.post(f"/v1/plans/{p}/claims",
                    json={"seller_sku": seed.msku_nofba[0], "sid": seed.msku_nofba[1]},
                    headers=H(seed.actor)).json()
    assert r["no_history"] == [{"seller_sku": seed.msku_nofba[0], "sid": seed.msku_nofba[1],
                                "reason": "no_sales_history"}]
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT DISTINCT system_units FROM plan_demand_cell WHERE plan_id = %s", (p,))
        assert cur.fetchall() == [(None,)]


def test_second_plan_claiming_the_same_msku_is_409_and_names_the_holder(client, seed):
    """★ 判据③ 的接口那一半。"""
    p1, p2 = mk(client, seed), mk(client, seed, title="另一张")
    body = {"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]}
    assert client.post(f"/v1/plans/{p1}/claims", json=body, headers=H(seed.actor)).status_code == 200
    r = client.post(f"/v1/plans/{p2}/claims", json=body, headers=H(seed.actor))
    assert r.status_code == 409 and r.json()["error"] == "msku_already_claimed"
    assert r.json()["claimed_by"] == {"plan_id": p1, "actor": seed.actor}


def test_two_connections_racing_for_the_same_msku(client, seed):
    """★ 判据③ 的库层那一半：先查后写挡不住并发，部分唯一索引能。

    B 在 A 未提交时插同一把键 → 必须**阻塞**；A 提交后 B 收到唯一冲突。
    只跑「A 提交完 B 再插」的话，证明的是「重复插入被拒」，不是竞态。
    """
    p1, p2 = mk(client, seed), mk(client, seed, title="另一张")
    err, started = [], threading.Event()

    def other():
        started.set()
        try:
            with pg_conn() as c, c.cursor() as cur:
                cur.execute("INSERT INTO msku_claim (plan_id, seller_sku, sid, claimed_by)"
                            " VALUES (%s, %s, %s, %s)", (p2, *seed.msku_a, seed.actor))
        except psycopg2.errors.UniqueViolation as e:
            err.append(e)

    conn = psycopg2.connect  # noqa: F841  （用 pg_conn 拿一条独立连接）
    with pg_conn() as a, a.cursor() as cur:
        cur.execute("INSERT INTO msku_claim (plan_id, seller_sku, sid, claimed_by)"
                    " VALUES (%s, %s, %s, %s)", (p1, *seed.msku_a, seed.actor))
        t = threading.Thread(target=other)
        t.start()
        started.wait(1)
        t.join(timeout=0.5)
        assert t.is_alive(), "★ B 没有被挡住 —— 唯一索引没生效，或它根本没走到插入"
        # 退出 with → A 提交 → B 被唤醒并撞上唯一索引
    t.join(timeout=5)
    assert not t.is_alive() and len(err) == 1


def test_release_keeps_the_row_and_names_the_cells_it_drops(client, seed):
    """★ 释放不删行；而被一起删掉的期望销量格子必须逐条点名 ——
    「少了几个数」在界面上是看不出来的。"""
    p = mk(client, seed)
    body = {"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]}
    client.post(f"/v1/plans/{p}/claims", json=body, headers=H(seed.actor))
    client.put(f"/v1/plans/{p}/demand/{seed.msku_a[0]}/{seed.msku_a[1]}/2026-10",
               json={"expected_units": 130}, headers=H(seed.actor))
    r = client.delete(f"/v1/plans/{p}/claims/{seed.msku_a[0]}/{seed.msku_a[1]}",
                      headers=H(seed.actor)).json()
    assert {"period": "2026-10", "expected_units": 130} in r["dropped_cells"]
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT released_at IS NOT NULL FROM msku_claim WHERE plan_id = %s", (p,))
        assert cur.fetchone()[0] is True


def test_reclaiming_in_the_same_plan_revives_the_row(client, seed):
    p = mk(client, seed)
    body = {"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]}
    client.post(f"/v1/plans/{p}/claims", json=body, headers=H(seed.actor))
    client.delete(f"/v1/plans/{p}/claims/{seed.msku_a[0]}/{seed.msku_a[1]}", headers=H(seed.actor))
    assert client.post(f"/v1/plans/{p}/claims", json=body, headers=H(seed.actor)).status_code == 200
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM msku_claim WHERE plan_id = %s", (p,))
        assert cur.fetchone()[0] == 1, "★ 复认领是复活那一行，不是新插一行（主键就在那）"
```

- [ ] **Step 2: 跑测试，确认它红**

Run: `python -m pytest tests/test_api_claims.py -q`
Expected: FAIL —— `/v1/catalog/skus` 404（10 项全红）

- [ ] **Step 3: 实现目录**

`api/ui/catalog.py`：

```python
"""选货号这一步（UC-O2 / P11）。

★ 真实库成百上千个货号，全列出来让人挑从第一天就是错的 ——
  所以不给条件时**故意不返回**，而且这件事要与「查不到」长得不一样。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from api.ui.deps import actor, declared, require_fresh_mirrors
from api.ui.errors import ApiError
from shared.pg_client import pg_conn

router = APIRouter(dependencies=[Depends(require_fresh_mirrors)])

#: ★ 设计取值 · 未实测。文档没给过上限；超出就报 truncated 让人细化条件。
DEFAULT_LIMIT, MAX_LIMIT = 200, 500


@router.get("/catalog/skus")
def catalog_skus(request: Request, who: str = Depends(actor)):
    declared(request, "q", "limit")
    q = (request.query_params.get("q") or "").strip()
    try:
        limit = min(int(request.query_params.get("limit", DEFAULT_LIMIT)), MAX_LIMIT)
    except ValueError:
        raise ApiError(400, "bad_limit", "limit 不是整数",
                       {"got": request.query_params.get("limit")}) from None
    if not q:
        # ★ 与「查不到」分得开：need_query=true，前端据此提示输入条件
        return {"need_query": False if q else True, "matched": 0, "truncated": False, "rows": []}

    like = f"%{q}%"
    with pg_conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT b.sku, s.name, b.seller_sku, b.sid, se.name,"
            "       cl.plan_id, cl.claimed_by"
            "  FROM msku_bridge b"
            "  JOIN sku_catalog s ON s.sku = b.sku"
            "  JOIN seller se ON se.seller_id = b.sid"
            "  LEFT JOIN msku_claim cl"
            "    ON cl.seller_sku = b.seller_sku AND cl.sid = b.sid"
            "   AND cl.released_at IS NULL AND NOT cl.plan_ordered"
            " WHERE b.sku ILIKE %s OR s.name ILIKE %s OR b.seller_sku ILIKE %s"
            " ORDER BY b.sku, b.seller_sku, b.sid", (like, like, like))
        rows = cur.fetchall()

    by_sku: dict[str, dict] = {}
    for sku, sku_name, seller_sku, sid, seller_name, plan_id, claimed_by in rows:
        entry = by_sku.setdefault(sku, {"sku": sku, "name": sku_name, "mskus": []})
        entry["mskus"].append({
            "seller_sku": seller_sku, "sid": sid, "seller_name": seller_name,
            # ★ 被占用的行留在表里标出来，不过滤
            "selectable": plan_id is None,
            "claimed_by": None if plan_id is None
            else {"plan_id": plan_id, "actor": claimed_by},
        })
    out = list(by_sku.values())
    return {"need_query": False, "matched": len(out), "truncated": len(out) > limit,
            "rows": out[:limit]}
```

- [ ] **Step 4: 实现认领与释放**（追加进 `api/ui/plans.py`）

```python
from dim.fixture_source import FixtureSource
from forecast.estimate import InsufficientHistory, monthly_estimate

#: 阶段 A 的取数源。★ 阶段 B 换成 ChSource 时改的只有这一行（dim/source.py 的协议不变）。
SOURCE = FixtureSource(Path(__file__).resolve().parents[2] / "tests" / "fixtures")


def _periods(cur, plan_id: int) -> list[dt.date]:
    cur.execute("SELECT period_start, months FROM plan WHERE plan_id = %s", (plan_id,))
    row = cur.fetchone()
    if row is None:
        raise ApiError(404, "plan_not_found", "计划不存在", {"plan_id": plan_id})
    start, months = row
    return [dt.date(start.year + (start.month - 1 + i) // 12,
                    (start.month - 1 + i) % 12 + 1, 1) for i in range(months)]


@router.post("/plans/{plan_id}/claims")
def claim(plan_id: int, body: dict, who: str = Depends(actor)):
    seller_sku, sid = body.get("seller_sku"), body.get("sid")
    if not seller_sku or not sid:
        raise ApiError(400, "bad_request", "seller_sku 与 sid 必填",
                       {"got": {"seller_sku": seller_sku, "sid": sid}})
    with timed("claim", actor=who, plan_id=plan_id), pg_conn() as c, c.cursor() as cur:
        periods = _periods(cur, plan_id)
        cur.execute("SELECT sku FROM msku_bridge WHERE seller_sku = %s AND sid = %s",
                    (seller_sku, sid))
        row = cur.fetchone()
        if row is None:
            raise ApiError(404, "unknown_msku", "msku 不在桥表里",
                           {"seller_sku": seller_sku, "sid": sid})
        sku = row[0]

        # ★ 先查后写挡不住并发，所以这里不查 —— 直接插，让部分唯一索引裁决；
        #   撞上了再回头查是谁占的，只为把 409 的 detail 点到名。
        try:
            cur.execute(
                "INSERT INTO msku_claim (plan_id, seller_sku, sid, claimed_by)"
                " VALUES (%s, %s, %s, %s)"
                " ON CONFLICT (plan_id, seller_sku, sid) DO UPDATE"
                "    SET released_at = NULL, released_by = NULL,"
                "        claimed_by = EXCLUDED.claimed_by, claimed_at = now()",
                (plan_id, seller_sku, sid, who))
        except psycopg2.errors.UniqueViolation:
            c.rollback()
            with pg_conn() as c2, c2.cursor() as cur2:
                cur2.execute("SELECT plan_id, claimed_by FROM msku_claim"
                             " WHERE seller_sku = %s AND sid = %s"
                             "   AND released_at IS NULL AND NOT plan_ordered",
                             (seller_sku, sid))
                holder = cur2.fetchone()
            raise ApiError(409, "msku_already_claimed", "该 msku 已被另一张尚未下单的计划占用",
                           {"seller_sku": seller_sku, "sid": sid,
                            "claimed_by": {"plan_id": holder[0], "actor": holder[1]}
                            if holder else None}) from None

        no_history = []
        try:
            est = monthly_estimate(SOURCE.monthly_sales_history(seller_sku, sid, len(periods)),
                                   len(periods))
        except InsufficientHistory:
            # ★ 没有历史 ≠ 预估 0：格子照建（人还要在上面填），system_units 留 NULL 并点名
            est = None
            no_history.append({"seller_sku": seller_sku, "sid": sid,
                               "reason": "no_sales_history"})
        for i, period in enumerate(periods):
            cur.execute(
                "INSERT INTO plan_demand_cell (plan_id, seller_sku, sid, period_start,"
                " system_units, system_extrapolated, updated_by)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (plan_id, seller_sku, sid, period_start) DO UPDATE"
                "    SET system_units = EXCLUDED.system_units,"
                "        system_extrapolated = EXCLUDED.system_extrapolated",
                (plan_id, seller_sku, sid, period,
                 est[i].units if est else None, bool(est and est[i].extrapolated), who))
        for period in periods:
            # ★ 货号级采购格子必须先长出来：没有行和填了 0 不能长得一样，
            #   否则提交时它连一条 skipped 都留不下（判据②）
            cur.execute("INSERT INTO plan_purchase_cell (plan_id, sku, period_start, updated_by)"
                        " VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                        (plan_id, sku, period, who))
    return {"claimed": {"seller_sku": seller_sku, "sid": sid, "sku": sku},
            "seeded": {"demand_cells": len(periods), "purchase_cells": len(periods)},
            "no_history": no_history}


@router.delete("/plans/{plan_id}/claims/{seller_sku}/{sid}")
def release(plan_id: int, seller_sku: str, sid: str, who: str = Depends(actor)):
    with timed("release_claim", actor=who, plan_id=plan_id), pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT period_start, expected_units FROM plan_demand_cell"
                    " WHERE plan_id = %s AND seller_sku = %s AND sid = %s ORDER BY period_start",
                    (plan_id, seller_sku, sid))
        dropped = [{"period": p.strftime("%Y-%m"), "expected_units": u} for p, u in cur.fetchall()]
        cur.execute("DELETE FROM plan_demand_cell"
                    " WHERE plan_id = %s AND seller_sku = %s AND sid = %s",
                    (plan_id, seller_sku, sid))
        cur.execute("UPDATE msku_claim SET released_at = now(), released_by = %s"
                    " WHERE plan_id = %s AND seller_sku = %s AND sid = %s AND released_at IS NULL"
                    " RETURNING plan_id", (who, plan_id, seller_sku, sid))
        if cur.fetchone() is None:
            raise ApiError(404, "claim_not_found", "没有这条在占用中的认领",
                           {"plan_id": plan_id, "seller_sku": seller_sku, "sid": sid})
    # ★ 丢东西必须有声：删掉的格子逐条回给界面，包括人填过的数
    return {"released": {"seller_sku": seller_sku, "sid": sid}, "dropped_cells": dropped}
```

`api/__init__.py` 追加 `app.include_router(catalog.router, prefix="/v1")`。

- [ ] **Step 5: 跑测试，确认它绿**

Run: `python -m pytest tests/test_api_claims.py -q`
Expected: 9 项 PASS，`test_release_keeps_the_row_and_names_the_cells_it_drops` 依赖 Task 12 的
`PUT /demand/...` → 仍红。★ Task 12 之后全绿。

- [ ] **Step 6: 提交**

```bash
git add api/ui/catalog.py api/ui/plans.py api/__init__.py tests/test_api_claims.py
git commit -m "feat(api): 目录搜索（需条件/截断/占用标记）· 认领由部分唯一索引裁决并点名占用方"
```

---
## Task 12: `GET /grid` 与两种量的 PUT

**Files:**
- Modify: `api/ui/plans.py`
- Create: `tests/test_api_grid.py`

**Interfaces:**
- Consumes: `forecast.projection.inventory_projection` / `InboundSource`；`api.ui.plans.SOURCE`
- Produces:
  - `GET /v1/plans/{plan_id}/grid` → 见下方形状（Task 15 会把它原样固化成前端 mock 的 fixture）
  - `PUT /v1/plans/{plan_id}/demand/{seller_sku}/{sid}/{period}` body `{"expected_units": int|null}`
  - `PUT /v1/plans/{plan_id}/purchase/{sku}/{period}` body `{"planned_units": int|null}`

### ★ 一处必须说清的层级问题（影响 `grid` 的形状）

`00e`:43 写「库存预估 = 在仓 + **采购在途** − 期望销量」，但两者**层级不同**：

```
在仓（FBA）      msku × 月     ← 有店铺
采购在途          货号 × 月     ← ★ 无店铺（14 §1：排货这条线以下没有店铺）
```

把货号级的在途加进每一个 msku 的期末，等于**每个店都以为这批货是自己的** ——
三个 msku 就凭空多出两份货。所以 `grid` 分两块返回，并在每一格标出**没算进来的是什么**：

| 块 | 粒度 | 内容 |
|---|---|---|
| `inventory[]` | msku × 月 | 期初 / 期望销量 / 期末 + `basis`（含 `excludes_plan_purchase` 与 `excludes_sku_level_in_transit`） |
| `sku_pipeline[]` | 货号 × 月 | 采购在途逐笔，★ `no_seller_attribution: true` |

★ 阶段 A 的 msku 级入库恒为空（「货运中 / 已发未到」属阶段 C，没有数据源）——
这件事写在 `basis.msku_inbound` 里说出来，而不是让它长成一个 0。

- [ ] **Step 1: 写失败的测试**

`tests/test_api_grid.py`：

```python
from shared.pg_client import pg_conn


def H(a):
    return {"x-actor": a}


def setup_plan(client, seed, mskus=(("MSKU-A", "11072"),)):
    pid = client.post("/v1/plans", json={"title": "10 月计划", "period_start": "2026-10-01",
                                         "months": 3}, headers=H(seed.actor)).json()["plan_id"]
    for ms in mskus:
        client.post(f"/v1/plans/{pid}/claims", json={"seller_sku": ms[0], "sid": ms[1]},
                    headers=H(seed.actor))
    return pid


def test_grid_returns_three_blocks_with_their_own_granularity(client, seed):
    pid = setup_plan(client, seed)
    g = client.get(f"/v1/plans/{pid}/grid", headers=H(seed.actor)).json()
    assert g["periods"] == ["2026-10", "2026-11", "2026-12"]
    assert {r["seller_sku"] for r in g["demand"]} == {"MSKU-A"}
    assert {r["sku"] for r in g["purchase"]} == {seed.sku_a}
    assert all("sid" in r for r in g["inventory"])
    assert all(r["no_seller_attribution"] is True for r in g["sku_pipeline"])


def test_inventory_follows_the_main_formula_and_says_what_is_excluded(client, seed):
    pid = setup_plan(client, seed)
    client.put(f"/v1/plans/{pid}/demand/MSKU-A/11072/2026-10",
               json={"expected_units": 100}, headers=H(seed.actor))
    g = client.get(f"/v1/plans/{pid}/grid", headers=H(seed.actor)).json()
    oct_row = [r for r in g["inventory"] if r["period"] == "2026-10"][0]
    assert oct_row["opening"] == 300 and oct_row["demand"] == 100 and oct_row["closing"] == 200
    assert oct_row["basis"]["excludes_plan_purchase"] is True
    assert oct_row["basis"]["excludes_sku_level_in_transit"] is True
    assert oct_row["basis"]["msku_inbound"] == "阶段 C 才有数据源（货运中 / 已发未到）"


def test_in_transit_is_not_added_into_each_msku(client, seed):
    """★ 货号级的在途加进每个 msku，三个 msku 就凭空多出两份货（14 §1）。"""
    pid = setup_plan(client, seed, mskus=(("MSKU-A", "11072"), ("MSKU-C", "11094")))
    g = client.get(f"/v1/plans/{pid}/grid", headers=H(seed.actor)).json()
    assert all(r["inbound"] == 0 for r in g["inventory"])
    oct_pipe = [r for r in g["sku_pipeline"] if r["period"] == "2026-10"][0]
    assert oct_pipe["units"] == 80 and len(oct_pipe["sources"]) == 2


def test_demand_cell_keeps_system_and_human_apart(client, seed):
    pid = setup_plan(client, seed)
    g = client.get(f"/v1/plans/{pid}/grid", headers=H(seed.actor)).json()
    cell = [r for r in g["demand"] if r["period"] == "2026-10"][0]
    assert cell == {"seller_sku": "MSKU-A", "sid": "11072", "sku": seed.sku_a,
                    "period": "2026-10", "system_units": 100, "system_extrapolated": False,
                    "expected_units": None, "effective_units": 100, "basis": "system"}


def test_extrapolated_flag_travels_with_the_number(client, seed):
    """★ 14 §5：标记必须随结果一起返回，不能让界面回头读原始格子。"""
    pid = client.post("/v1/plans", json={"title": "长周期", "period_start": "2026-10-01",
                                         "months": 7}, headers=H(seed.actor)).json()["plan_id"]
    client.post(f"/v1/plans/{pid}/claims", json={"seller_sku": "MSKU-A", "sid": "11072"},
                headers=H(seed.actor))
    g = client.get(f"/v1/plans/{pid}/grid", headers=H(seed.actor)).json()
    flags = [r["system_extrapolated"] for r in sorted(g["demand"], key=lambda r: r["period"])]
    assert flags == [False, False, False, True, True, True, True]
    assert not all(flags), "★ 恒真的标记等于白标"


def test_no_fba_seller_is_not_applicable_not_zero(client, seed):
    """★ 02 §3.1a：无 FBA 的平台显示「不适用」，不是 0。"""
    pid = setup_plan(client, seed, mskus=(("MSKU-W", "90001"),))
    g = client.get(f"/v1/plans/{pid}/grid", headers=H(seed.actor)).json()
    row = g["inventory"][0]
    assert row["not_applicable"] is True and row["closing"] is None
    assert row["basis"]["reason"] == "not_applicable"


def test_unknown_demand_propagates(client, seed):
    pid = setup_plan(client, seed)
    client.put(f"/v1/plans/{pid}/demand/MSKU-A/11072/2026-10",
               json={"expected_units": None}, headers=H(seed.actor))
    with pg_conn() as c, c.cursor() as cur:   # 把系统预估也清掉 → 这一格真的是未知
        cur.execute("UPDATE plan_demand_cell SET system_units = NULL"
                    " WHERE plan_id = %s AND period_start = '2026-10-01'", (pid,))
    g = client.get(f"/v1/plans/{pid}/grid", headers=H(seed.actor)).json()
    closings = [r["closing"] for r in sorted(g["inventory"], key=lambda r: r["period"])]
    assert closings == [None, None, None]


def test_put_demand_and_purchase_are_one_cell_one_transaction(client, seed):
    pid = setup_plan(client, seed)
    r1 = client.put(f"/v1/plans/{pid}/demand/MSKU-A/11072/2026-11",
                    json={"expected_units": 130}, headers=H(seed.actor))
    assert r1.status_code == 200 and r1.json()["cell"]["effective_units"] == 130
    assert r1.json()["cell"]["basis"] == "human"
    r2 = client.put(f"/v1/plans/{pid}/purchase/{seed.sku_a}/2026-11",
                    json={"planned_units": 500}, headers=H(seed.actor))
    assert r2.status_code == 200 and r2.json()["cell"]["planned_units"] == 500


def test_put_on_a_cell_that_was_never_seeded_is_404(client, seed):
    """★ 只能改已经长出来的格子 —— 凭空插一行会绕过「没认领就没格子」。"""
    pid = setup_plan(client, seed)
    r = client.put(f"/v1/plans/{pid}/demand/MSKU-A/11072/2027-05",
                   json={"expected_units": 1}, headers=H(seed.actor))
    assert r.status_code == 404 and r.json()["error"] == "cell_not_found"


def test_negative_units_are_400(client, seed):
    pid = setup_plan(client, seed)
    r = client.put(f"/v1/plans/{pid}/purchase/{seed.sku_a}/2026-10",
                   json={"planned_units": -1}, headers=H(seed.actor))
    assert r.status_code == 400 and r.json()["error"] == "bad_units"
```

- [ ] **Step 2: 跑测试，确认它红**

Run: `python -m pytest tests/test_api_grid.py -q`
Expected: FAIL —— `/v1/plans/{id}/grid` 404（10 项全红）

- [ ] **Step 3: 实现**（追加进 `api/ui/plans.py`）

```python
from forecast.projection import InboundSource, inventory_projection
from rules.effective import effective_demand


def _ym(d: dt.date) -> str:
    return d.strftime("%Y-%m")


def _load_grid(cur, plan_id: int):
    periods = _periods(cur, plan_id)
    cur.execute(
        "SELECT d.seller_sku, d.sid, b.sku, d.period_start, d.system_units,"
        "       d.system_extrapolated, d.expected_units, s.has_fba"
        "  FROM plan_demand_cell d"
        "  JOIN msku_bridge b ON b.seller_sku = d.seller_sku AND b.sid = d.sid"
        "  JOIN seller s ON s.seller_id = d.sid"
        " WHERE d.plan_id = %s ORDER BY b.sku, d.seller_sku, d.sid, d.period_start", (plan_id,))
    demand = cur.fetchall()
    cur.execute("SELECT sku, period_start, planned_units FROM plan_purchase_cell"
                " WHERE plan_id = %s ORDER BY sku, period_start", (plan_id,))
    purchase = cur.fetchall()
    return periods, demand, purchase


@router.get("/plans/{plan_id}/grid")
def grid(plan_id: int, request: Request, who: str = Depends(actor)):
    declared(request)
    with pg_conn() as c, c.cursor() as cur:
        periods, demand, purchase = _load_grid(cur, plan_id)

    out_demand, expected_by_msku, has_fba, sku_of = [], {}, {}, {}
    for seller_sku, sid, sku, period, sysu, extrap, expu, fba in demand:
        eff = effective_demand(sysu, expu)
        out_demand.append({"seller_sku": seller_sku, "sid": sid, "sku": sku,
                           "period": _ym(period), "system_units": sysu,
                           "system_extrapolated": extrap, "expected_units": expu,
                           "effective_units": eff.units, "basis": eff.basis})
        expected_by_msku.setdefault((seller_sku, sid), {})[_ym(period)] = eff.units
        has_fba[(seller_sku, sid)] = fba
        sku_of[(seller_sku, sid)] = sku

    inventory = []
    for key, by_month in expected_by_msku.items():
        seller_sku, sid = key
        # ★ 该店铺没有 FBA → onhand 是「不适用」，不是 0（02 §3.1a）
        onhand = SOURCE.onhand_available(seller_sku, sid) if has_fba[key] else None
        for row in inventory_projection(onhand, {}, by_month):
            row["basis"]["excludes_sku_level_in_transit"] = True
            row["basis"]["msku_inbound"] = "阶段 C 才有数据源（货运中 / 已发未到）"
            inventory.append({"seller_sku": seller_sku, "sid": sid,
                              "sku": sku_of[key], **row})

    pipeline: dict[tuple[str, str], dict] = {}
    for sku in sorted({r[0] for r in purchase}):
        for t in SOURCE.purchase_in_transit(sku):
            slot = pipeline.setdefault((sku, t.period),
                                       {"sku": sku, "period": t.period, "units": 0,
                                        "sources": [], "no_seller_attribution": True})
            slot["units"] += t.units
            slot["sources"].append({"units": t.units, "kind": "purchase_in_transit", "ref": t.ref})

    return {
        "plan_id": plan_id,
        "periods": [_ym(p) for p in periods],
        "demand": out_demand,
        "purchase": [{"sku": s, "period": _ym(p), "planned_units": u} for s, p, u in purchase],
        "inventory": inventory,
        "sku_pipeline": [pipeline[k] for k in sorted(pipeline)],
    }


def _units(body: dict, field: str) -> int | None:
    v = body.get(field, None)
    if v is None:
        return None                       # ★ 留空 = 未知（M-8），是合法输入
    if not isinstance(v, int) or isinstance(v, bool) or v < 0:
        raise ApiError(400, "bad_units", f"{field} 必须是 ≥0 的整数或 null",
                       {"field": field, "got": v})
    return v


@router.put("/plans/{plan_id}/demand/{seller_sku}/{sid}/{period}")
def put_demand(plan_id: int, seller_sku: str, sid: str, period: str, body: dict,
               who: str = Depends(actor)):
    units = _units(body, "expected_units")
    # ★ 一格一事务：重算（预测）放在事务外 —— 预测慢，不该把行锁攥着（01 §5）
    with timed("put_demand", actor=who, plan_id=plan_id), pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE plan_demand_cell SET expected_units = %s, updated_by = %s,"
                    " updated_at = now()"
                    " WHERE plan_id = %s AND seller_sku = %s AND sid = %s AND period_start = %s"
                    " RETURNING system_units, system_extrapolated",
                    (units, who, plan_id, seller_sku, sid, _date(period + "-01", "period")))
        row = cur.fetchone()
    if row is None:
        raise ApiError(404, "cell_not_found", "这一格还没长出来（先认领这个 msku）",
                       {"plan_id": plan_id, "seller_sku": seller_sku, "sid": sid,
                        "period": period})
    eff = effective_demand(row[0], units)
    return {"cell": {"seller_sku": seller_sku, "sid": sid, "period": period,
                     "system_units": row[0], "system_extrapolated": row[1],
                     "expected_units": units,
                     "effective_units": eff.units, "basis": eff.basis}}


@router.put("/plans/{plan_id}/purchase/{sku}/{period}")
def put_purchase(plan_id: int, sku: str, period: str, body: dict, who: str = Depends(actor)):
    units = _units(body, "planned_units")
    with timed("put_purchase", actor=who, plan_id=plan_id), pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE plan_purchase_cell SET planned_units = %s, updated_by = %s,"
                    " updated_at = now()"
                    " WHERE plan_id = %s AND sku = %s AND period_start = %s RETURNING 1",
                    (units, who, plan_id, sku, _date(period + "-01", "period")))
        hit = cur.fetchone()
    if hit is None:
        raise ApiError(404, "cell_not_found", "这一格还没长出来（先认领该货号下的 msku）",
                       {"plan_id": plan_id, "sku": sku, "period": period})
    return {"cell": {"sku": sku, "period": period, "planned_units": units}}
```

- [ ] **Step 4: 跑测试，确认它绿**

Run: `python -m pytest tests/test_api_grid.py tests/test_api_claims.py -q`
Expected: PASS（10 + 10 项 —— Task 11 那条等 `PUT /demand` 的也转绿了）

- [ ] **Step 5: 提交**

```bash
git add api/ui/plans.py tests/test_api_grid.py
git commit -m "feat(api): 网格三块（msku 级期望/库存 · 货号级采购 · 货号级在途不并入 msku）与两种量的 PUT"
```

---

## Task 13: 提交 · 版本 · 当前使用 · 差异 · 归档 · 整版撤销（判据①②④）

**Files:**
- Create: `api/ui/submit.py`
- Modify: `api/ui/plans.py`（archive）· `api/__init__.py` · `tests/helpers.py`（追加 `H` / `prepared`）
- Create: `tests/test_api_submit.py`

**Interfaces:**
- Consumes: `rules.submit.select_submittable` / `PurchaseCell` / `DemandCell`；`rules.digest.content_digest`
- Produces:
  - `POST /v1/plans/{plan_id}/submit` → `{"rev":int,"minted":int,"skipped":[{sku,period,reason}],"in_flight":bool,"content_digest":str}`；409 `rev_in_flight` 点名旧版
  - `GET /v1/plans/{plan_id}/revs` → `{"revs":[…],"in_flight_rev":int|None,"current_rev":int|None}`
  - `POST /v1/plans/{plan_id}/revs/{rev}/current` → `{"current_rev":int}`
  - `GET /v1/plans/{plan_id}/diff?from=&to=` → `{"added":[],"removed":[],"changed":[]}`
  - `POST /v1/plans/{plan_id}/revs/{rev}/cancel` body `{"reason"}` → `{"cancelled":[line_id…]}`
  - `POST /v1/plans/{plan_id}/archive` → `{"archived_at":str,"released":int}`

- [ ] **Step 1: 写失败的测试**

`tests/helpers.py` 追加（Task 14 与 16 都要用；测试文件之间仍然不互相 import）：

```python
def H(actor: str) -> dict:
    return {"x-actor": actor}


def prepared(client, seed, purchase=500, expected=120):
    """一张填好两种量的计划：MSKU-A（11072）· MSKU-C（11094）同属 sku_a。

    ★ 两个店铺是刻意的：单店的话 demand_by_seller 只有一把键，
      「按店冻结」这件事等于没被测到。
    """
    pid = client.post("/v1/plans", json={"title": "10 月计划", "period_start": "2026-10-01",
                                         "months": 3}, headers=H(seed.actor)).json()["plan_id"]
    for ms in (seed.msku_a, seed.msku_c):
        client.post(f"/v1/plans/{pid}/claims", json={"seller_sku": ms[0], "sid": ms[1]},
                    headers=H(seed.actor))
        if expected is not None:
            client.put(f"/v1/plans/{pid}/demand/{ms[0]}/{ms[1]}/2026-10",
                       json={"expected_units": expected}, headers=H(seed.actor))
    if purchase is not None:
        client.put(f"/v1/plans/{pid}/purchase/{seed.sku_a}/2026-10",
                   json={"planned_units": purchase}, headers=H(seed.actor))
    return pid
```

`tests/test_api_submit.py`：

```python
from helpers import H, prepared

from shared.pg_client import pg_conn


def test_submit_mints_lines_and_lists_every_skipped_cell(client, seed):
    """★ 判据①②：铸出 rev，且被跳过的两个月逐条列出。"""
    pid = prepared(client, seed)
    r = client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor)).json()
    assert r["rev"] == 1 and r["minted"] == 1
    assert [(s["period"], s["reason"]) for s in r["skipped"]] == [
        ("2026-11", "zero_purchase"), ("2026-12", "zero_purchase")]
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT sku, total_units, demand_at_submit, demand_by_seller, state"
                    " FROM plan_line WHERE plan_id = %s", (pid,))
        sku, total, dsum, by_seller, state = cur.fetchone()
    assert (total, dsum, state) == (500, 240, "已提交")
    assert set(by_seller) == {"11072", "11094"}
    assert by_seller["11072"]["basis"] == "human"


def test_every_minted_line_has_its_birth_event(client, seed):
    """★ S-2：事件链的第一行不许缺，否则事件表不是完整履历。"""
    pid = prepared(client, seed)
    client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM plan_line l"
                    " WHERE l.plan_id = %s AND NOT EXISTS (SELECT 1 FROM plan_line_event e"
                    "   WHERE e.line_id = l.line_id AND e.from_state = '[*]')", (pid,))
        assert cur.fetchone()[0] == 0


def test_second_submit_while_one_is_in_flight_is_409_naming_the_old_rev(client, seed):
    """★ 判据④ / S-4：改了只能出新 rev，而旧版还在流转就拒 —— 点名旧版。"""
    pid = prepared(client, seed)
    client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    r = client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    assert r.status_code == 409 and r.json()["error"] == "rev_in_flight"
    assert r.json()["in_flight_rev"] == 1


def test_an_empty_rev_does_not_hold_the_in_flight_slot(client, seed):
    """★ T20：全部格子被跳过 → 空计划单 → 整体已撤销，且**不许占着在流转位**。
    占着的话，这张计划从此再也提交不了，而错误信息会说「有一版在流转」——
    人去找那一版，找到的是一张空的。"""
    pid = prepared(client, seed, purchase=None)
    r = client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor)).json()
    assert r["minted"] == 0 and r["in_flight"] is False and len(r["skipped"]) == 3
    rows = client.get("/v1/plans", headers=H(seed.actor)).json()["plans"]
    assert rows[0]["overall"] == "已撤销"
    assert client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor)).status_code == 200


def test_skip_rows_are_persisted_and_append_only(client, seed):
    pid = prepared(client, seed)
    client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM plan_submit_skip WHERE plan_id = %s", (pid,))
        assert cur.fetchone()[0] == 2


def test_revs_report_in_flight_and_current(client, seed):
    pid = prepared(client, seed)
    client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    body = client.get(f"/v1/plans/{pid}/revs", headers=H(seed.actor)).json()
    assert body["in_flight_rev"] == 1 and body["current_rev"] is None
    client.post(f"/v1/plans/{pid}/revs/1/current", headers=H(seed.actor))
    body = client.get(f"/v1/plans/{pid}/revs", headers=H(seed.actor)).json()
    assert body["current_rev"] == 1 and body["revs"][0]["lines"] == 1


def test_cancelling_a_rev_cancels_every_live_line_with_one_reason(client, seed):
    """★ A-8：一个理由记在每条上。理由不填 → 400。"""
    pid = prepared(client, seed)
    client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    bad = client.post(f"/v1/plans/{pid}/revs/1/cancel", json={}, headers=H(seed.actor))
    assert bad.status_code == 400 and bad.json()["error"] == "reason_required"
    ok = client.post(f"/v1/plans/{pid}/revs/1/cancel", json={"reason": "供应商断供"},
                     headers=H(seed.actor)).json()
    assert len(ok["cancelled"]) == 1
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT reason FROM plan_line_event WHERE to_state = '已撤销'")
        assert [r[0] for r in cur.fetchall()] == ["供应商断供"]


def test_a_new_rev_is_allowed_after_the_old_one_settles(client, seed):
    """★ 判据④ 的另一半：内容不可变 —— 要改就出新 rev。"""
    pid = prepared(client, seed)
    client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    client.post(f"/v1/plans/{pid}/revs/1/cancel", json={"reason": "重来"}, headers=H(seed.actor))
    client.put(f"/v1/plans/{pid}/purchase/{seed.sku_a}/2026-10",
               json={"planned_units": 600}, headers=H(seed.actor))
    r2 = client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor)).json()
    assert r2["rev"] == 2
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT rev, total_units FROM plan_line WHERE plan_id = %s ORDER BY rev",
                    (pid,))
        assert cur.fetchall() == [(1, 500), (2, 600)], "★ 旧版的数一个字节都没变"


def test_digest_changes_only_when_content_changes(client, seed):
    pid = prepared(client, seed)
    d1 = client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor)).json()["content_digest"]
    client.post(f"/v1/plans/{pid}/revs/1/cancel", json={"reason": "重来"}, headers=H(seed.actor))
    d2 = client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor)).json()["content_digest"]
    assert d1 == d2
    client.put(f"/v1/plans/{pid}/purchase/{seed.sku_a}/2026-10",
               json={"planned_units": 600}, headers=H(seed.actor))
    client.post(f"/v1/plans/{pid}/revs/2/cancel", json={"reason": "再来"}, headers=H(seed.actor))
    d3 = client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor)).json()["content_digest"]
    assert d3 != d1


def test_diff_between_revs_names_what_moved(client, seed):
    pid = prepared(client, seed)
    client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    client.post(f"/v1/plans/{pid}/revs/1/cancel", json={"reason": "重来"}, headers=H(seed.actor))
    client.put(f"/v1/plans/{pid}/purchase/{seed.sku_a}/2026-10",
               json={"planned_units": 600}, headers=H(seed.actor))
    client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    d = client.get(f"/v1/plans/{pid}/diff", params={"from": 1, "to": 2},
                   headers=H(seed.actor)).json()
    assert d["changed"] == [{"sku": seed.sku_a, "period": "2026-10",
                             "total_units": {"from": 500, "to": 600},
                             "demand_at_submit": {"from": 240, "to": 240}}]
    assert d["added"] == [] and d["removed"] == []


def test_archive_releases_every_claim_in_the_same_transaction(client, seed):
    pid = prepared(client, seed)
    r = client.post(f"/v1/plans/{pid}/archive", headers=H(seed.actor)).json()
    assert r["released"] == 2
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM msku_claim"
                    " WHERE plan_id = %s AND released_at IS NULL", (pid,))
        assert cur.fetchone()[0] == 0
    # ★ 归档后这两个 msku 必须能被别的计划认领，否则「归档」等于永久扣着不放
    p2 = client.post("/v1/plans", json={"title": "下一张", "period_start": "2026-11-01"},
                     headers=H(seed.actor)).json()["plan_id"]
    assert client.post(f"/v1/plans/{p2}/claims",
                       json={"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]},
                       headers=H(seed.actor)).status_code == 200
```

- [ ] **Step 2: 跑测试，确认它红**

Run: `python -m pytest tests/test_api_submit.py -q`
Expected: FAIL —— `/v1/plans/{id}/submit` 404（11 项全红）

- [ ] **Step 3: 实现 `api/ui/submit.py`**

```python
"""提交与版本。★ 提交是**一个事务**铸出全部记录 —— 半铸的计划单无法解释（01 §5）。"""
from __future__ import annotations

import datetime as dt
import json

import psycopg2
from fastapi import APIRouter, Depends, Request

from api.ui.deps import actor, declared, require_fresh_mirrors
from api.ui.errors import ApiError
from rules.digest import content_digest
from rules.submit import DemandCell, PurchaseCell, select_submittable
from shared.pg_client import pg_conn, timed

router = APIRouter(dependencies=[Depends(require_fresh_mirrors)])

TERMINAL = ("已完结", "已撤销")


def _cells(cur, plan_id: int):
    cur.execute("SELECT sku, period_start, planned_units FROM plan_purchase_cell"
                " WHERE plan_id = %s", (plan_id,))
    purchase = [PurchaseCell(s, p.strftime("%Y-%m"), u) for s, p, u in cur.fetchall()]
    cur.execute(
        "SELECT d.seller_sku, d.sid, b.sku, d.period_start, d.system_units, d.expected_units"
        "  FROM plan_demand_cell d"
        "  JOIN msku_bridge b ON b.seller_sku = d.seller_sku AND b.sid = d.sid"
        "  JOIN msku_claim cl ON cl.plan_id = d.plan_id AND cl.seller_sku = d.seller_sku"
        "   AND cl.sid = d.sid AND cl.released_at IS NULL"
        " WHERE d.plan_id = %s", (plan_id,))
    demand = [DemandCell(ss, sid, sku, p.strftime("%Y-%m"), sy, ex)
              for ss, sid, sku, p, sy, ex in cur.fetchall()]
    cur.execute("SELECT seller_sku, sid FROM msku_claim"
                " WHERE plan_id = %s AND released_at IS NULL", (plan_id,))
    claimed = {(r[0], r[1]) for r in cur.fetchall()}
    return purchase, demand, claimed


@router.post("/plans/{plan_id}/submit")
def submit(plan_id: int, request: Request, who: str = Depends(actor)):
    declared(request)
    with timed("submit", actor=who, plan_id=plan_id), pg_conn() as c, c.cursor() as cur:
        purchase, demand, claimed = _cells(cur, plan_id)
        minted, skipped = select_submittable(purchase, demand, claimed)
        digest = content_digest(purchase, demand)

        cur.execute("SELECT coalesce(max(rev), 0) + 1 FROM plan_rev WHERE plan_id = %s",
                    (plan_id,))
        rev = cur.fetchone()[0]
        try:
            cur.execute("INSERT INTO plan_rev (plan_id, rev, content_digest, submitted_by)"
                        " VALUES (%s, %s, %s, %s)", (plan_id, rev, digest, who))
        except psycopg2.errors.UniqueViolation:
            # ★ 撞的是「同一计划只许 1 版在流转」那条部分唯一索引（S-4）。
            #   回滚后另起一条连接去查是哪一版 —— 只说「有一版在流转」，人得自己去翻。
            c.rollback()
            with pg_conn() as c2, c2.cursor() as cur2:
                cur2.execute("SELECT rev, submitted_by, submitted_at FROM plan_rev"
                             " WHERE plan_id = %s AND in_flight", (plan_id,))
                old = cur2.fetchone()
            raise ApiError(409, "rev_in_flight", "该计划已有一版在流转",
                           {"plan_id": plan_id,
                            "in_flight_rev": old[0] if old else None,
                            "submitted_by": old[1] if old else None,
                            "submitted_at": old[2].isoformat() if old else None}) from None

        for m in minted:
            period = dt.date.fromisoformat(m.period + "-01")
            cur.execute(
                "INSERT INTO plan_line (plan_id, rev, sku, period_start, total_units,"
                " demand_by_seller, demand_at_submit, state)"
                " VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, '已提交') RETURNING line_id",
                (plan_id, rev, m.sku, period, m.total_units,
                 json.dumps(m.demand_by_seller, ensure_ascii=False), m.demand_at_submit))
            line_id = cur.fetchone()[0]
            # ★ 铸出也留痕：没有这一行，事件表就不是完整履历（S-2；库层还有延迟约束兜底）
            cur.execute("INSERT INTO plan_line_event (line_id, from_state, to_state, actor, src)"
                        " VALUES (%s, '[*]', '已提交', %s, %s)",
                        (line_id, who, f"submit:{plan_id}#{rev}"))
        for s in skipped:
            cur.execute("INSERT INTO plan_submit_skip (plan_id, rev, sku, period_start, reason)"
                        " VALUES (%s, %s, %s, %s, %s)",
                        (plan_id, rev, s.sku, dt.date.fromisoformat(s.period + "-01"), s.reason))

        # ★ 铸出 0 条记录的空版本没有任何行能触发 t_rev_in_flight，
        #   不在这里收一次，它会永远占着「唯一在流转」那个位子（04 §3.1 的空计划单）
        cur.execute("SELECT close_rev_if_settled(%s, %s)", (plan_id, rev))
        cur.execute("SELECT in_flight FROM plan_rev WHERE plan_id = %s AND rev = %s",
                    (plan_id, rev))
        in_flight = cur.fetchone()[0]

    return {"rev": rev, "minted": len(minted), "in_flight": in_flight,
            "content_digest": digest,
            "skipped": [{"sku": s.sku, "period": s.period, "reason": s.reason}
                        for s in sorted(skipped)]}


@router.get("/plans/{plan_id}/revs")
def revs(plan_id: int, request: Request, who: str = Depends(actor)):
    declared(request)
    with pg_conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT r.rev, r.content_digest, r.is_current, r.in_flight, r.submitted_by,"
            "       r.submitted_at, count(l.line_id)"
            "  FROM plan_rev r LEFT JOIN plan_line l ON l.plan_id = r.plan_id AND l.rev = r.rev"
            " WHERE r.plan_id = %s GROUP BY r.rev, r.content_digest, r.is_current, r.in_flight,"
            "       r.submitted_by, r.submitted_at ORDER BY r.rev DESC", (plan_id,))
        rows = cur.fetchall()
    revs_out = [{"rev": r[0], "content_digest": r[1], "is_current": r[2], "in_flight": r[3],
                 "submitted_by": r[4], "submitted_at": r[5].isoformat(), "lines": r[6]}
                for r in rows]
    return {"revs": revs_out,
            "in_flight_rev": next((r["rev"] for r in revs_out if r["in_flight"]), None),
            "current_rev": next((r["rev"] for r in revs_out if r["is_current"]), None)}


@router.post("/plans/{plan_id}/revs/{rev}/current")
def mark_current(plan_id: int, rev: int, who: str = Depends(actor)):
    with pg_conn() as c, c.cursor() as cur:
        # 先清后立：部分唯一索引不可延迟，同一语句里换不过来
        cur.execute("UPDATE plan_rev SET is_current = false WHERE plan_id = %s AND is_current",
                    (plan_id,))
        cur.execute("UPDATE plan_rev SET is_current = true WHERE plan_id = %s AND rev = %s"
                    " RETURNING rev", (plan_id, rev))
        if cur.fetchone() is None:
            raise ApiError(404, "rev_not_found", "没有这一版",
                           {"plan_id": plan_id, "rev": rev})
    return {"current_rev": rev}


@router.get("/plans/{plan_id}/diff")
def diff(plan_id: int, request: Request, who: str = Depends(actor)):
    declared(request, "from", "to")
    try:
        a, b = int(request.query_params["from"]), int(request.query_params["to"])
    except (KeyError, ValueError):
        raise ApiError(400, "bad_request", "from 与 to 必须是版本号",
                       {"got": dict(request.query_params)}) from None
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT rev, sku, period_start, total_units, demand_at_submit"
                    " FROM plan_line WHERE plan_id = %s AND rev IN (%s, %s)", (plan_id, a, b))
        rows = cur.fetchall()
    left = {(r[1], r[2]): r for r in rows if r[0] == a}
    right = {(r[1], r[2]): r for r in rows if r[0] == b}
    changed = [{"sku": k[0], "period": k[1].strftime("%Y-%m"),
                "total_units": {"from": left[k][3], "to": right[k][3]},
                "demand_at_submit": {"from": left[k][4], "to": right[k][4]}}
               for k in sorted(left.keys() & right.keys())
               if left[k][3:] != right[k][3:]]
    fmt = (lambda k, m: {"sku": k[0], "period": k[1].strftime("%Y-%m"),
                         "total_units": m[k][3]})
    return {"added": [fmt(k, right) for k in sorted(right.keys() - left.keys())],
            "removed": [fmt(k, left) for k in sorted(left.keys() - right.keys())],
            "changed": changed}


@router.post("/plans/{plan_id}/revs/{rev}/cancel")
def cancel_rev(plan_id: int, rev: int, body: dict, who: str = Depends(actor)):
    reason = (body.get("reason") or "").strip()
    if not reason:
        # ★ 库层的 require_reason() 也会拦，但那会以 500 的形态出去；
        #   这里给的是能让人改表单的 400（08 §0.1：400 = 你写错了）
        raise ApiError(400, "reason_required", "撤销必须填理由", {"field": "reason"})
    with timed("cancel_rev", actor=who, plan_id=plan_id), pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT line_id, state FROM plan_line"
                    " WHERE plan_id = %s AND rev = %s AND state NOT IN %s FOR UPDATE",
                    (plan_id, rev, TERMINAL))
        lines = cur.fetchall()
        for line_id, state in lines:
            # ★ A-8：一个理由记在每条上 —— 记在版本上，逐条查历史时就看不到它
            cur.execute("INSERT INTO plan_line_event"
                        " (line_id, from_state, to_state, actor, reason, src)"
                        " VALUES (%s, %s, '已撤销', %s, %s, %s)",
                        (line_id, state, who, reason, f"cancel_rev:{plan_id}#{rev}"))
    return {"cancelled": [line_id for line_id, _ in lines], "reason": reason}
```

★ `demand_by_seller` 用 `json.dumps(..., ensure_ascii=False)` + SQL 里的 `%s::jsonb` 明写，
不用 `psycopg2.extras.Json`：这一列的内容是**冻结证据**，序列化形态直接决定它长什么样，
藏在适配器里将来没人知道是谁写成那样的。

- [ ] **Step 4: 实现归档**（追加进 `api/ui/plans.py`）

```python
@router.post("/plans/{plan_id}/archive")
def archive(plan_id: int, who: str = Depends(actor)):
    """★ 同一事务释放全部占用：分两次做，中间挂掉就会留下一张归档了却还扣着货的计划。"""
    with timed("archive", actor=who, plan_id=plan_id), pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE msku_claim SET released_at = now(), released_by = %s"
                    " WHERE plan_id = %s AND released_at IS NULL RETURNING seller_sku, sid",
                    (who, plan_id))
        released = cur.fetchall()
        cur.execute("UPDATE plan SET archived_at = now() WHERE plan_id = %s"
                    " RETURNING archived_at", (plan_id,))
        row = cur.fetchone()
        if row is None:
            raise ApiError(404, "plan_not_found", "计划不存在", {"plan_id": plan_id})
    return {"archived_at": row[0].isoformat(), "released": len(released),
            "released_mskus": [{"seller_sku": s, "sid": i} for s, i in released]}
```

`api/__init__.py` 追加 `app.include_router(submit.router, prefix="/v1")`。

- [ ] **Step 5: 跑测试，确认它绿**

Run: `python -m pytest tests/test_api_submit.py tests/test_api_plans.py -q`
Expected: PASS（11 + 6 项 —— Task 10 那条等 `archive` 的也转绿了）

- [ ] **Step 6: 提交**

```bash
git add api/ui/submit.py api/ui/plans.py api/__init__.py tests/test_api_submit.py
git commit -m "feat(api): 提交铸出 rev（含铸出事件与 skipped 留痕）· 版本 · 差异 · 整版撤销 · 归档同事务释放占用"
```

---
## Task 14: `GET /v1/plan-lines` · 单条撤销 · 两个看板

**Files:**
- Create: `api/ui/lines.py` · `api/ui/dashboard.py`
- Modify: `api/__init__.py`
- Create: `tests/test_api_lines.py` · `tests/test_api_dashboard.py`

**Interfaces:**
- Consumes: Task 13 的 `submit` 端点（测试要先造出记录）；`v_plan_overall_state`
- Produces:
  - `GET /v1/plan-lines?plan_ids=&state=&sku=&category=&period=&group_by=` → `{"lines":[…],"groups":{…},"excluded":{…}}`
  - `POST /v1/plan-lines/{line_id}/cancel` body `{"reason"}` → `{"line_id","state":"已撤销"}`；400 `reason_required` · 422 `illegal_transition`（回显 `allowed[]`）· 422 `terminal_state`
  - `GET /v1/dashboard/plans` → `{"counts":{…},"scope_note":…}`
  - `GET /v1/dashboard/unsubmitted` → `{"never_submitted":[…],"changed_since_submit":[…]}`

- [ ] **Step 1: 写失败的测试**

`tests/test_api_lines.py`：

```python
from helpers import H, prepared


def submitted(client, seed):
    pid = prepared(client, seed)
    client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    line = client.get("/v1/plan-lines", params={"plan_ids": pid},
                      headers=H(seed.actor)).json()["lines"][0]
    return pid, line["line_id"]


def test_lines_can_be_viewed_across_several_plans(client, seed):
    """★ G3：勾选多份联合查看。"""
    p1, _ = submitted(client, seed)
    p2, _ = submitted(client, seed)
    body = client.get("/v1/plan-lines", params={"plan_ids": f"{p1},{p2}"},
                      headers=H(seed.actor)).json()
    assert {r["plan_id"] for r in body["lines"]} == {p1, p2}


def test_group_by_sku_and_period(client, seed):
    p1, _ = submitted(client, seed)
    body = client.get("/v1/plan-lines", params={"plan_ids": p1, "group_by": "sku"},
                      headers=H(seed.actor)).json()
    assert body["groups"][seed.sku_a]["total_units"] == 500


def test_category_is_refused_loudly_not_ignored(client, seed):
    """★ 阶段 A 没有品类镜像（A-1 属另一条线）。静默忽略这个参数，
    返回的是「全量」而不是报错 —— 而全量看起来完全正常。"""
    p1, _ = submitted(client, seed)
    r = client.get("/v1/plan-lines", params={"plan_ids": p1, "group_by": "category"},
                   headers=H(seed.actor))
    assert r.status_code == 400 and r.json()["error"] == "category_not_available"


def test_cancel_one_line_requires_a_reason(client, seed):
    _, line = submitted(client, seed)
    r = client.post(f"/v1/plan-lines/{line}/cancel", json={}, headers=H(seed.actor))
    assert r.status_code == 400 and r.json()["error"] == "reason_required"


def test_cancel_one_line_records_the_reason_on_it(client, seed):
    _, line = submitted(client, seed)
    r = client.post(f"/v1/plan-lines/{line}/cancel", json={"reason": "改方案"},
                    headers=H(seed.actor))
    assert r.status_code == 200 and r.json()["state"] == "已撤销"


def test_cancelling_a_terminal_line_is_422_terminal_state(client, seed):
    _, line = submitted(client, seed)
    client.post(f"/v1/plan-lines/{line}/cancel", json={"reason": "改方案"}, headers=H(seed.actor))
    r = client.post(f"/v1/plan-lines/{line}/cancel", json={"reason": "再撤一次"},
                    headers=H(seed.actor))
    assert r.status_code == 422 and r.json()["error"] == "terminal_state"
    assert r.json()["state"] == "已撤销"


def test_illegal_transition_echoes_where_it_could_go(client, seed):
    """★ 只说「不许」而不说「那能去哪」，前端只能猜（04 §1.4）。"""
    _, line = submitted(client, seed)
    r = client.post(f"/v1/plan-lines/{line}/transition",
                    json={"to_state": "已排货"}, headers=H(seed.actor))
    assert r.status_code == 422 and r.json()["error"] == "illegal_transition"
    assert r.json()["allowed"] == ["已确认", "已撤销"]


def test_unknown_state_value_is_400(client, seed):
    _, line = submitted(client, seed)
    r = client.post(f"/v1/plan-lines/{line}/transition", json={"to_state": "飞了"},
                    headers=H(seed.actor))
    assert r.status_code == 400 and r.json()["error"] == "unknown_state"
```

`tests/test_api_dashboard.py`：

```python
from helpers import H, prepared


def test_plan_counts_return_the_full_set_even_where_stage_a_cannot_reach(client, seed):
    """★ S-20：接口返回全集（诚实），前端按阶段不渲染够不着的态。
    接口自己把它们抹成 0，「没有」和「还没做」就长得一样了。"""
    pid = prepared(client, seed)
    client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    body = client.get("/v1/dashboard/plans", headers=H(seed.actor)).json()
    assert set(body["counts"]) == {"进行中", "已提交", "已提交未确认", "已下单",
                                   "准备排货", "已排货"}
    assert body["counts"]["已提交"] == 1 and body["counts"]["已排货"] == 0
    assert body["scope_note"]["unreachable_in_stage_a"] == ["已下单", "准备排货", "已排货"]


def test_unsubmitted_has_two_columns(client, seed):
    """★ 两栏：从未提交 vs 提交后又改过 —— 催的是两种人，合成一栏就催错人。"""
    never = client.post("/v1/plans", json={"title": "没提交过", "period_start": "2026-10-01"},
                        headers=H(seed.actor)).json()["plan_id"]
    changed = prepared(client, seed)
    client.post(f"/v1/plans/{changed}/submit", headers=H(seed.actor))
    client.put(f"/v1/plans/{changed}/purchase/{seed.sku_a}/2026-11",
               json={"planned_units": 90}, headers=H(seed.actor))
    body = client.get("/v1/dashboard/unsubmitted", headers=H(seed.actor)).json()
    assert [p["plan_id"] for p in body["never_submitted"]] == [never]
    assert [p["plan_id"] for p in body["changed_since_submit"]] == [changed]


def test_a_plan_that_did_not_change_is_in_neither_column(client, seed):
    pid = prepared(client, seed)
    client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    body = client.get("/v1/dashboard/unsubmitted", headers=H(seed.actor)).json()
    assert body["never_submitted"] == [] and body["changed_since_submit"] == []
```

- [ ] **Step 2: 跑测试，确认它红**

Run: `python -m pytest tests/test_api_lines.py tests/test_api_dashboard.py -q`
Expected: FAIL —— `/v1/plan-lines` 404（11 项全红）

- [ ] **Step 3: 实现 `api/ui/lines.py`**

```python
"""计划单记录的查询与状态动作。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from api.ui.deps import actor, declared, require_fresh_mirrors
from api.ui.errors import ApiError
from shared.pg_client import pg_conn, timed

router = APIRouter(dependencies=[Depends(require_fresh_mirrors)])

TERMINAL = ("已完结", "已撤销")


def _allowed_next(cur, state: str) -> list[str]:
    cur.execute("SELECT to_state FROM plan_line_transition WHERE from_state = %s ORDER BY to_state",
                (state,))
    return [r[0] for r in cur.fetchall()]


@router.get("/plan-lines")
def list_lines(request: Request, who: str = Depends(actor)):
    declared(request, "plan_ids", "state", "sku", "category", "period", "group_by")
    q = request.query_params
    if q.get("category") or q.get("group_by") == "category":
        # ★ 品类镜像（sku_category / A-1）不在阶段 A 的表里。默默忽略这个参数，
        #   返回的会是「全量」而不是报错 —— 而全量看起来完全正常。
        raise ApiError(400, "category_not_available", "阶段 A 还没有品类镜像",
                       {"depends_on": "sku_category（A-1）"})
    group_by = q.get("group_by")
    if group_by and group_by not in ("sku", "period"):
        raise ApiError(400, "bad_group_by", "group_by 只支持 sku | period",
                       {"got": group_by, "allowed": ["sku", "period"]})

    where, args = ["true"], []
    if q.get("plan_ids"):
        try:
            ids = [int(x) for x in q["plan_ids"].split(",") if x]
        except ValueError:
            raise ApiError(400, "bad_plan_ids", "plan_ids 必须是逗号分隔的整数",
                           {"got": q["plan_ids"]}) from None
        where.append("l.plan_id = ANY(%s)")
        args.append(ids)
    for col, key in (("l.state", "state"), ("l.sku", "sku")):
        if q.get(key):
            where.append(f"{col} = %s")
            args.append(q[key])
    if q.get("period"):
        where.append("l.period_start = %s")
        args.append(q["period"] + "-01")

    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT l.line_id, l.plan_id, l.rev, l.sku, l.period_start, l.total_units,"
                    " l.demand_at_submit, l.demand_by_seller, l.state"
                    f" FROM plan_line l WHERE {' AND '.join(where)}"
                    " ORDER BY l.plan_id, l.rev, l.sku, l.period_start", args)
        lines = [{"line_id": r[0], "plan_id": r[1], "rev": r[2], "sku": r[3],
                  "period": r[4].strftime("%Y-%m"), "total_units": r[5],
                  "demand_at_submit": r[6], "demand_by_seller": r[7], "state": r[8]}
                 for r in cur.fetchall()]
        cur.execute("SELECT count(*) FROM plan_line")
        total = cur.fetchone()[0]

    groups: dict[str, dict] = {}
    if group_by:
        key = "sku" if group_by == "sku" else "period"
        for row in lines:
            slot = groups.setdefault(row[key], {"lines": 0, "total_units": 0,
                                                "demand_at_submit": 0})
            slot["lines"] += 1
            slot["total_units"] += row["total_units"]
            slot["demand_at_submit"] += row["demand_at_submit"]
    # ★ 过滤必须同时统计被丢掉的那一侧
    return {"lines": lines, "groups": groups,
            "excluded": {"filtered_out": total - len(lines)}}


def _transition(line_id: int, to_state: str, reason: str, who: str, src: str):
    with timed("plan_line_transition", actor=who, line_id=line_id), \
            pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT state FROM plan_line WHERE line_id = %s FOR UPDATE", (line_id,))
        row = cur.fetchone()
        if row is None:
            raise ApiError(404, "line_not_found", "没有这条记录", {"line_id": line_id})
        state = row[0]
        cur.execute("SELECT count(*) FROM plan_line_transition WHERE to_state = %s", (to_state,))
        if cur.fetchone()[0] == 0:
            raise ApiError(400, "unknown_state", "没有这个状态值",
                           {"got": to_state,
                            "known": _allowed_next(cur, state)})
        if state in TERMINAL:
            raise ApiError(422, "terminal_state", "这条记录已在终态，离不开",
                           {"line_id": line_id, "state": state})
        allowed = _allowed_next(cur, state)
        if to_state not in allowed:
            # ★ allowed[] 必须回显（04 §1.4）
            raise ApiError(422, "illegal_transition", "这条状态迁移不在白名单里",
                           {"line_id": line_id, "from": state, "to": to_state,
                            "allowed": allowed})
        cur.execute("INSERT INTO plan_line_event"
                    " (line_id, from_state, to_state, actor, reason, src)"
                    " VALUES (%s, %s, %s, %s, %s, %s)",
                    (line_id, state, to_state, who, reason or None, src))
    return {"line_id": line_id, "state": to_state}


@router.post("/plan-lines/{line_id}/cancel")
def cancel_line(line_id: int, body: dict, who: str = Depends(actor)):
    reason = (body.get("reason") or "").strip()
    if not reason:
        raise ApiError(400, "reason_required", "撤销必须填理由", {"field": "reason"})
    return _transition(line_id, "已撤销", reason, who, f"cancel_line:{line_id}")


@router.post("/plan-lines/{line_id}/transition")
def move_line(line_id: int, body: dict, who: str = Depends(actor)):
    """★ 阶段 A 里唯一走得通的目标是「已撤销」（C 类，人做的）。

    其余目标态全是 A 类 —— 由承重墙①②的量推出来，属阶段 B/C。
    这个端点**不替它们提前开路**：非法就 422 并回显 allowed[]，
    让「阶段 A 还没有这条路」看起来就是「现在不行」，而不是一个默默成功的动作。
    """
    to_state = (body.get("to_state") or "").strip()
    return _transition(line_id, to_state, (body.get("reason") or "").strip(), who,
                       f"transition:{line_id}")
```

- [ ] **Step 4: 实现 `api/ui/dashboard.py`**

```python
"""两个看板。★ 计划的状态是记录状态的木桶派生，不是独立字段（08 §1.1）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from api.ui.deps import actor, declared, require_fresh_mirrors
from rules.digest import content_digest
from rules.submit import DemandCell, PurchaseCell
from shared.pg_client import pg_conn

router = APIRouter(dependencies=[Depends(require_fresh_mirrors)])

COUNTED = ["进行中", "已提交", "已提交未确认", "已下单", "准备排货", "已排货"]
#: 阶段 A 铸不出这几个态（要承重墙①②）。★ 接口照样返回它们（诚实），
#  由前端按阶段不渲染 —— 接口自己抹成 0，「没有」和「还没做」就长得一样了（S-20）。
UNREACHABLE_IN_STAGE_A = ["已下单", "准备排货", "已排货"]


@router.get("/dashboard/plans")
def dashboard_plans(request: Request, who: str = Depends(actor)):
    declared(request)
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT v.overall, count(*) FROM v_plan_overall_state v"
                    " JOIN plan p USING (plan_id) WHERE p.archived_at IS NULL"
                    " GROUP BY v.overall")
        by_state = {k: n for k, n in cur.fetchall()}
    counts = {k: 0 for k in COUNTED}
    for state, n in by_state.items():
        if state in counts:
            counts[state] += n
        if state not in (None, "已完结", "已撤销"):
            counts["进行中"] += n
        if state == "已提交":
            counts["已提交未确认"] += n
    return {"counts": counts,
            "scope_note": {"unreachable_in_stage_a": UNREACHABLE_IN_STAGE_A,
                           "never_submitted_excluded": by_state.get(None, 0)}}


@router.get("/dashboard/unsubmitted")
def dashboard_unsubmitted(request: Request, who: str = Depends(actor)):
    declared(request)
    never, changed = [], []
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT p.plan_id, p.title, r.rev, r.content_digest"
                    "  FROM plan p"
                    "  LEFT JOIN LATERAL (SELECT rev, content_digest FROM plan_rev"
                    "     WHERE plan_id = p.plan_id ORDER BY rev DESC LIMIT 1) r ON true"
                    " WHERE p.archived_at IS NULL ORDER BY p.plan_id")
        plans = cur.fetchall()
        for plan_id, title, rev, digest in plans:
            if rev is None:
                never.append({"plan_id": plan_id, "title": title})
                continue
            cur.execute("SELECT sku, period_start, planned_units FROM plan_purchase_cell"
                        " WHERE plan_id = %s", (plan_id,))
            purchase = [PurchaseCell(s, p.strftime("%Y-%m"), u) for s, p, u in cur.fetchall()]
            cur.execute(
                "SELECT d.seller_sku, d.sid, b.sku, d.period_start, d.system_units,"
                "       d.expected_units"
                "  FROM plan_demand_cell d"
                "  JOIN msku_bridge b ON b.seller_sku = d.seller_sku AND b.sid = d.sid"
                "  JOIN msku_claim cl ON cl.plan_id = d.plan_id"
                "   AND cl.seller_sku = d.seller_sku AND cl.sid = d.sid"
                "   AND cl.released_at IS NULL"
                " WHERE d.plan_id = %s", (plan_id,))
            demand = [DemandCell(ss, sid, sku, p.strftime("%Y-%m"), sy, ex)
                      for ss, sid, sku, p, sy, ex in cur.fetchall()]
            if content_digest(purchase, demand) != digest:
                changed.append({"plan_id": plan_id, "title": title, "since_rev": rev})
    # ★ 两栏分开：从未提交要人去提交，改过要人去重新提交 —— 催的是两种动作
    return {"never_submitted": never, "changed_since_submit": changed}
```

`api/__init__.py` 追加两行 `include_router(..., prefix="/v1")`。

- [ ] **Step 5: 跑测试，确认它绿**

Run: `python -m pytest tests/test_api_lines.py tests/test_api_dashboard.py -q`
Expected: PASS（8 + 3 项）

- [ ] **Step 6: 提交**

```bash
git add api/ui/lines.py api/ui/dashboard.py api/__init__.py tests/test_api_lines.py tests/test_api_dashboard.py
git commit -m "feat(api): 记录联合查看与撤销（回显 allowed[]）· 看板返回全集并标出阶段 A 够不着的态"
```

---

## Task 15: 把 `GET /grid` 的响应固化成前端 mock 的同源 fixture（判据⑥ 的后端一半）

**Files:**
- Create: `tests/fixtures/grid_response.json`（由测试生成）
- Create: `tests/test_grid_fixture.py`

**Interfaces:**
- Consumes: Task 12 的 `GET /v1/plans/{plan_id}/grid`
- Produces: `tests/fixtures/grid_response.json` —— 前端 `web/src/api/mock` 直接读这一份；
  以及重生成命令 `UPDATE_GRID_FIXTURE=1 python -m pytest tests/test_grid_fixture.py`

- [ ] **Step 1: 写失败的测试**

`tests/test_grid_fixture.py`：

```python
"""★ 判据⑥ 的后端一半：mock 与真 API 必须是**同一份数据**，不是两份长得像的。

两份各自维护的话，前端跑 mock 全绿、切 api 才发现字段名不一样 ——
而那时候界面已经照着 mock 写完了。
"""
import json
import os
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "grid_response.json"


def _build(client, seed):
    pid = client.post("/v1/plans", json={"title": "10 月计划", "period_start": "2026-10-01",
                                         "months": 3},
                      headers={"x-actor": seed.actor}).json()["plan_id"]
    # ★ 刻意覆盖三种形态：人填 / 采用系统预估 / 该平台无 FBA
    for ms in (seed.msku_a, seed.msku_c, seed.msku_nofba):
        client.post(f"/v1/plans/{pid}/claims", json={"seller_sku": ms[0], "sid": ms[1]},
                    headers={"x-actor": seed.actor})
    client.put(f"/v1/plans/{pid}/demand/{seed.msku_a[0]}/{seed.msku_a[1]}/2026-10",
               json={"expected_units": 130}, headers={"x-actor": seed.actor})
    client.put(f"/v1/plans/{pid}/purchase/{seed.sku_a}/2026-10",
               json={"planned_units": 500}, headers={"x-actor": seed.actor})
    body = client.get(f"/v1/plans/{pid}/grid", headers={"x-actor": seed.actor}).json()
    body["plan_id"] = 1        # ★ 固化前抹掉自增主键，否则 fixture 每轮都在变
    return body


def test_grid_fixture_matches_the_live_api(client, seed):
    live = _build(client, seed)
    if os.environ.get("UPDATE_GRID_FIXTURE"):
        # ★ 整份重写，不合并 —— 合并会把删掉的字段永远留在 fixture 里
        FIXTURE.write_text(json.dumps(live, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    saved = json.loads(FIXTURE.read_text("utf-8"))
    assert saved == live, ("grid 的形状变了。确认是有意改动后，用 "
                           "UPDATE_GRID_FIXTURE=1 重跑本测试重新固化，"
                           "并告诉前端这一版的差异。")


def test_the_fixture_covers_the_three_shapes_that_look_alike(client, seed):
    """★ fixture 若只有「正常」那一种形态，前端就永远不会画出另外两种。"""
    saved = json.loads(FIXTURE.read_text("utf-8"))
    bases = {r["basis"] for r in saved["demand"]}
    assert {"human", "system"} <= bases
    assert any(r["not_applicable"] for r in saved["inventory"]), "缺「不适用」那一种"
    assert any(r["no_seller_attribution"] for r in saved["sku_pipeline"])
```

- [ ] **Step 2: 跑测试，确认它红**

Run: `python -m pytest tests/test_grid_fixture.py -q`
Expected: FAIL —— `FileNotFoundError: tests/fixtures/grid_response.json`

- [ ] **Step 3: 生成 fixture**

Run: `UPDATE_GRID_FIXTURE=1 python -m pytest tests/test_grid_fixture.py -q`
（生成后**人工读一遍** `tests/fixtures/grid_response.json`：三种形态各在不在，字段名与 `08` §1.1 对不对得上。）

- [ ] **Step 4: 不带环境变量再跑一次，确认它绿且稳定**

Run: `python -m pytest tests/test_grid_fixture.py -q`
Expected: PASS（2 项）；再跑一次仍 PASS（★ 若两次不同，说明响应里有时间戳或自增 id 漏抹）

- [ ] **Step 5: 提交**

```bash
git add tests/fixtures/grid_response.json tests/test_grid_fixture.py
git commit -m "test(api): 固化 GET /grid 响应为前端 mock 的同源 fixture（含不适用/外推/无归属三种形态）"
```

---

## Task 16: 六条校验判据的端到端证明

**Files:**
- Create: `tests/test_stage_a_criteria.py`
- Create: `README.md`（只写怎么跑，不复述设计 —— 论证在 `docs/`）

**Interfaces:**
- Consumes: 前面全部 Task
- Produces: 判据 ①~⑤ 各一条可指认的测试；判据⑥ 的后端一半由 Task 15 兑现

- [ ] **Step 1: 写测试**

`tests/test_stage_a_criteria.py`：

```python
"""00e §1 阶段 A 的六条校验判据，每条一个可指认的测试。

★ 判据⑥（前端 mock 与真 API 跑出同一屏）的后端一半在 tests/test_grid_fixture.py；
  前端那一半在 web/ 的计划里。这里不假装它已经全过。
"""
import psycopg2.errors
import pytest

from shared.pg_client import pg_conn
from helpers import H, prepared


def test_criterion_1_build_claim_fill_submit_mint(client, seed):
    """① 建 → 认领 → 填两种量 → 提交 → 铸出 rev，全程可复现。"""
    pid = client.post("/v1/plans", json={"title": "判据一", "period_start": "2026-10-01",
                                         "months": 3}, headers=H(seed.actor)).json()["plan_id"]
    assert client.post(f"/v1/plans/{pid}/claims",
                       json={"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]},
                       headers=H(seed.actor)).status_code == 200
    assert client.put(f"/v1/plans/{pid}/demand/{seed.msku_a[0]}/{seed.msku_a[1]}/2026-10",
                      json={"expected_units": 120}, headers=H(seed.actor)).status_code == 200
    assert client.put(f"/v1/plans/{pid}/purchase/{seed.sku_a}/2026-10",
                      json={"planned_units": 400}, headers=H(seed.actor)).status_code == 200
    r = client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor)).json()
    assert r["rev"] == 1 and r["minted"] == 1
    lines = client.get("/v1/plan-lines", params={"plan_ids": pid},
                       headers=H(seed.actor)).json()["lines"]
    assert lines[0]["total_units"] == 400 and lines[0]["demand_at_submit"] == 120
    assert lines[0]["state"] == "已提交"


def test_criterion_2_every_skipped_cell_is_listed_with_a_reason(client, seed):
    """② 提交时被跳过的格子逐条列出，不静默丢。"""
    pid = prepared(client, seed, purchase=None)
    r = client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor)).json()
    assert {s["reason"] for s in r["skipped"]} == {"zero_purchase"}
    assert len(r["skipped"]) == 3
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM plan_submit_skip WHERE plan_id = %s", (pid,))
        assert cur.fetchone()[0] == 3, "★ 返回体说了，库里也要有 —— 它是留痕不是提示"


def test_criterion_3_one_msku_cannot_belong_to_two_unordered_plans(client, seed):
    """③ 同一 msku 不能同时归两个「未下单」计划 —— 库层裁决 + 接口点名。"""
    p1 = prepared(client, seed)
    p2 = client.post("/v1/plans", json={"title": "另一张", "period_start": "2026-10-01"},
                     headers=H(seed.actor)).json()["plan_id"]
    r = client.post(f"/v1/plans/{p2}/claims",
                    json={"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]},
                    headers=H(seed.actor))
    assert r.status_code == 409 and r.json()["claimed_by"]["plan_id"] == p1


def test_criterion_4_content_is_immutable_changes_become_a_new_rev(client, seed):
    """④ 计划单记录内容不可变：改了只能出新 rev，旧版一个字节不动。"""
    pid = prepared(client, seed)
    client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT line_id FROM plan_line WHERE plan_id = %s", (pid,))
        line = cur.fetchone()[0]
        with pytest.raises(psycopg2.errors.RaiseException):
            cur.execute("UPDATE plan_line SET state = '已确认' WHERE line_id = %s", (line,))
    client.post(f"/v1/plans/{pid}/revs/1/cancel", json={"reason": "重来"}, headers=H(seed.actor))
    client.put(f"/v1/plans/{pid}/purchase/{seed.sku_a}/2026-10",
               json={"planned_units": 999}, headers=H(seed.actor))
    assert client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor)).json()["rev"] == 2
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT rev, total_units FROM plan_line WHERE plan_id = %s ORDER BY rev",
                    (pid,))
        assert cur.fetchall() == [(1, 500), (2, 999)]


def test_criterion_5_the_back_edge_exists_and_terminal_states_are_sealed(client, seed):
    """⑤ 阶段 A 的形态（S-11）：白名单建对 + 非法迁移被外键拒 + 终态不可离开。

    ★ 退回边的**触发源**（撤组单）属阶段 B —— 这里证的是这条边存在且走得通，
      不是「阶段 A 能自己走一遍」。两者不要混。
    """
    pid = prepared(client, seed)
    client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT line_id FROM plan_line WHERE plan_id = %s", (pid,))
        line = cur.fetchone()[0]
        # 库层直插：已提交 → 已确认 → 已提交（唯一的一条退回边）
        cur.execute("INSERT INTO plan_line_event (line_id, from_state, to_state, actor)"
                    " VALUES (%s, '已提交', '已确认', %s)", (line, seed.actor))
        cur.execute("INSERT INTO plan_line_event (line_id, from_state, to_state, actor)"
                    " VALUES (%s, '已确认', '已提交', %s)", (line, seed.actor))
        cur.execute("SELECT state FROM plan_line WHERE line_id = %s", (line,))
        assert cur.fetchone()[0] == "已提交"
    r = client.post(f"/v1/plan-lines/{line}/transition", json={"to_state": "已完结"},
                    headers=H(seed.actor))
    assert r.status_code == 422 and r.json()["allowed"] == ["已确认", "已撤销"]
```

`README.md`：

```markdown
# service_supplychain

供应链后端服务。设计与论证在 `docs/`，这里只写怎么跑。

## 跑起来

    uv sync
    cp config.example.toml config.toml     # 填 PG / CH 口令
    python -m migrations.pg.apply          # 应用迁移到 config.toml 里的 schema
    uvicorn --factory api:create_app --port 8090

## 测试

    python -m pytest -q                    # 全部（需要能连到 PG 192.168.66.210）
    python -m pytest tests/test_layering.py tests/test_forecast_*.py tests/test_rules_*.py -q
                                           # 离线可跑的那部分（纯层）

测试跑在 schema `scm_test` 上，跑完整个 DROP；`scm` 与 `inv` 被夹具硬拦。
```

- [ ] **Step 2: 跑全量**

Run: `python -m pytest -q`
Expected: PASS —— 合计 **126 项**
（9 分层 + 6 迁移 + 6 + 12 + 14 DDL + 3 整体状态 + 7 + 8 预测 + 6 dim + 11 rules
+ 8 系统 + 6 计划 + 10 认领 + 10 网格 + 11 提交 + 8 记录 + 3 看板 + 2 fixture + 5 判据）

- [ ] **Step 3: 确认纯层真的离线可跑**

Run: `JXD_SCM_CONFIG=/nonexistent python -m pytest tests/test_layering.py tests/test_forecast_estimate.py tests/test_forecast_projection.py tests/test_rules_submit.py tests/test_dim_fixture.py -q`
Expected: PASS（41 项）★ 配置文件都读不到还能全绿，才叫「判据离线可测」。

- [ ] **Step 4: 提交**

```bash
git add tests/test_stage_a_criteria.py README.md
git commit -m "test(stage-a): 六条校验判据各一条端到端，判据⑤ 按 S-11 的阶段 A 形态证明"
```

---

## 附：阶段 A 交付后要交回文档的三件事

> 实现过程中发现的文档问题，按 CLAUDE.md「指不到出处的记进 00」处理，**不在代码里自己定**。

| # | 发现 | 建议落点 |
|---|---|---|
| 1 | `08` §3 错误码总表写 `another_rev_in_flight`，而 §1.1 与 S-4 写 `rev_in_flight` | 统一成 `rev_in_flight`（本计划按 §1.1 实现），改 `08` §3 |
| 2 | `00e`:43「库存预估 = 在仓 + 采购在途 − 期望销量」两项层级不同（`14` §1：在途在「排货」那条横线以下、无店铺），合并会让每个 msku 都以为这批货是自己的 | `00e` §1 阶段 A 那一格补一句「两层分开渲染」；实现见 Task 12 |
| 3 | `04` §1.4 写非法迁移 409，`08`:280 与 S-18 写 422 | 按 S-18 统一 422，改 `04` §1.4 |

---

## Self-Review

### 1. 规格覆盖

| 规格项 | 落点 |
|---|---|
| `00e` 阶段 A 九张表 + 地基 | Task 3 / 4 / 5（`plan_cell` / `plan_cell_msku` 按 S-1 拆成 `plan_demand_cell` / `plan_purchase_cell`） |
| 判据 ①②③④⑤ | Task 16 各一条；③ 的竞态在 Task 11、④ 的不可变在 Task 13 |
| 判据⑥ | Task 15（后端一半：同源 fixture）；前端一半属 `web/` 的计划 |
| `08` §1.1 阶段 A 端点 | catalog Task 11 · plans Task 10 · claims Task 11 · grid + 两 PUT Task 12 · submit/revs/current/diff/archive Task 13 · plan-lines/cancel/dashboard Task 14 · health/readiness Task 9 |
| `08` §1.1 未实现的一项 | `GET /plan-lines/{id}/trace` —— 已在「阶段 A 明确不做」表里列出并给出处 |
| `03` §3 状态机四件套 | Task 5（含 S-2 哨兵与铸出事件、S-17 只追加触发器） |
| `04` S1 / S2 | Task 5（S1）· Task 10（S2 派生视图，两个边界显式定义） |
| `01` §3.1 L1~L10 | Task 1：L1~L7 落地；L8~L10 属 `web/`，由守卫测试逼它们不被忘掉 |
| `14` §0/§1/§5 | Task 6（主公式、恒等式③④⑥、外推标记） |
| S 组 20 条裁定 | S-1..S-20 逐条在对应 Task 的注释与测试里点名（S-5 = S-1；S-11 见 Task 16 判据⑤） |

**gap**：`14` §2 四段管道、`03` §7 预测落库、`erp/` 出站写 —— 三项都不属阶段 A，已在「明确不做」表里带出处列出。

### 2. 占位符扫描

扫 TBD / TODO / 「类似 Task N」/「加适当校验」/ 无代码的步骤 —— **修掉 1 处**：
Task 13 的 `plan_line` 插入一度写成 `psycopg2.extras.Json(...) if False else __import__("json")...` 的三元式（是占位噪声，不是能抄的代码），已改成 `import json` + `json.dumps(..., ensure_ascii=False)` 并补上「为什么不用适配器」的理由。

### 3. 类型一致性

逐个核对过跨 Task 引用的名字与类型，**修掉 3 处**：

1. `msku_claim` 与 `plan_demand_cell` 的建表顺序 —— 外键 `plan_demand_cell_claim_fk` 指向 `msku_claim`，而 `03` §2 的行文里格子在前。迁移 002 里改成 `plan → msku_claim → 两张格子`，否则第一条迁移就跑不过。
2. `close_rev_if_settled(plan_id, rev)` 的**第二个调用者** —— 只挂触发器的话，铸出 0 条记录的空版本没有任何行能触发它，`plan_rev_one_in_flight_idx` 会把这张计划的后续提交永久挡住。Task 13 的提交路径显式调一次，Task 13 的 `test_an_empty_rev_does_not_hold_the_in_flight_slot` 是它的靶子。
3. 错误码 `rev_in_flight`（`08` §1.1 / S-4）与 `another_rev_in_flight`（`08` §3）不一致 —— 全计划统一用 `rev_in_flight`，并记进上面「要交回文档的三件事」。
4. 四处测试原本写成 `from tests.test_xxx import …` —— `tests/` 没有 `__init__.py`，这条 import 在收集期就会炸，而报错指向的行与真正的原因隔着一层。共用造数改放 `tests/helpers.py`（Task 5 建、Task 13 追加），四处统一 `from helpers import …`。

另两处刻意的偏离，一并记在这里：

- `monthly_estimate` 的返回类型是 `list[MonthEstimate]` 而不是 `list[int]`：外推标记必须随数一起走（`14` §5 / M-13），返回裸 `int` 会把它丢在函数里。
- `inventory_projection` 的 `inbound_by_month` 收的是**逐笔明细**而不是合计：合计在函数内部求和，恒等式⑥「推算入库 ≡ 其构成明细之和」才不依赖调用方自觉。
