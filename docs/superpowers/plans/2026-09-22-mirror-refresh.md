# 维度镜像刷新 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让四张 DIM 镜像能从 CH `jxd_raw` 按日自动刷新，刷新有留痕、掉档会拒批、漏登记会红。

**Architecture:** 一张登记表 `dim/registry.py` 是「有哪些镜像」的唯一真相，两道门禁把它钉在
`docs/03` 与 `v_mirror_freshness` 上。取数的 SQL 与纯变换在 `dim/`（客户端由调用方注入，
因为 `tests/test_layering.py:55` 明令 `dim/` 不许碰 `shared.ch_client`），落库与留痕在 `jobs/`。
三个入口（APScheduler 进程内调度 / CLI / 运维接口）靠一把 PG advisory lock 互斥。

**Tech Stack:** Python 3.11+ · FastAPI · psycopg2 · `apscheduler==3.11.3` · `clickhouse-connect==1.5.0` · pytest

**Spec:** `docs/superpowers/specs/2026-09-22-mirror-refresh-design.md`

## Global Constraints

- **绝不改已执行的迁移**（001~005）。新结构一律追加 `migrations/pg/006_*.sql`；迁移文件里
  **不写 `scm.` 前缀、不写 `BEGIN/COMMIT`**（事务边界由 `apply()` 持有）。
- **CH 只读**。`dim/` 里出现 `insert into` / `create table` 等词会让
  `test_l7_no_writes_to_clickhouse` 红 —— PG 的 upsert SQL 必须写在 `jobs/`。
- **`dim/` 不许 import `shared.ch_client` / `shared.pg_client` / `psycopg2` / `api`**
  （`tests/test_layering.py:55` 的 `NO_CONNECTIONS`）。客户端注入。
- **日志三问**：打的谁（host/db/表）· 多久（`elapsed_ms`）· 怎么失败的（状态码或 `e.cause` 的 code）。
  区分「超时」与「连不上」。**静默兜底是最坏的一种** —— 每条回退分支都要留声。
- **过滤/join 必须统计被丢掉的那一侧**，并对**认不出的形态硬失败**，不许落进 `else ''`。
- **测试写完先弄失败一次**，每个 Task 的 Step 2 就是这件事。
- **只 upsert，绝不 DELETE** 源里消失的行（spec §6 判据 6）。
- 配置只从 `config.toml` 的 `[freshness]` 读，不硬编码；`config.example.toml` 同步。
- 所有新数字要么指到 `docs/17` 的某个 n，要么标「设计取值 · 未实测」。

---

### Task 1: 登记表与门禁 (a)

**Files:**
- Create: `dim/registry.py`
- Test: `tests/test_mirror_registry.py`

**Interfaces:**
- Consumes: 无（本仓第一个任务）
- Produces:
  - `dim.registry.Mirror`（frozen dataclass，字段见下）
  - `dim.registry.MIRRORS: tuple[Mirror, ...]`
  - `dim.registry.by_name(name: str) -> Mirror`
  - `dim.registry.gate_503_names() -> set[str]`
  - `dim.registry.refresh_order() -> list[Mirror]` —— 按 `depends_on` 拓扑排，只含 `pending=False`

- [ ] **Step 1: 写失败的测试**

`tests/test_mirror_registry.py`：

```python
"""登记表是「有哪些镜像」的唯一真相。门禁 (a) 把它钉在 docs/03 §1 上。"""
import re
from pathlib import Path

from dim.registry import MIRRORS, by_name, gate_503_names, refresh_order

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "03-数据库表清单.md"

#: 03 §1 的行形如 `| 30 | \`sku_catalog\` | ① 维度 | DIM 镜像 | 刷新 | 外键完整性 |`
_ROW = re.compile(r"^\|\s*\d+\s*\|(?P<table>[^|]+)\|(?P<domain>[^|]+)\|(?P<owner>[^|]+)\|")


def _doc_rows() -> list[tuple[list[str], str]]:
    out = []
    for line in DOC.read_text("utf-8").splitlines():
        m = _ROW.match(line.strip())
        if m:
            out.append((re.findall(r"`([^`]+)`", m["table"]), m["owner"].strip()))
    return out


def test_doc_table_parser_actually_sees_the_table():
    """★ 扫 0 行的门禁永远是绿的。03:51 写的是 43 张（含 S-33 新增的 dim_refresh_run），
    少于它就是正则坏了。"""
    rows = _doc_rows()
    assert len(rows) >= 43, f"只解析到 {len(rows)} 行 —— 正则没匹配上，下面那条断言是空转的"


def test_registry_matches_the_doc_exactly():
    """★ 集合相等，不是包含：03 多一行 MIRROR → 红；登记表多一个名字 → 也红。"""
    doc_names, skipped = set(), 0
    for tables, owner in _doc_rows():
        if "MIRROR" in owner or "DIM" in owner:
            doc_names |= set(tables)
        else:
            skipped += 1          # ★ 同时统计被丢掉的那一侧
    assert skipped > 0, "一行都没被跳过 —— 归属列没解析对"
    registry_names = {m.name for m in MIRRORS} | {c for m in MIRRORS for c in m.companions}
    assert registry_names == doc_names, (
        f"只在 03 里：{sorted(doc_names - registry_names)}；"
        f"只在登记表里：{sorted(registry_names - doc_names)}")


def test_stage_a_entries_are_not_pending():
    a = {m.name for m in MIRRORS if m.stage == "A" and not m.pending}
    assert a == {"seller", "sku_catalog", "msku_bridge", "warehouse"}


def test_gate_503_is_exactly_the_four_dim_mirrors():
    assert gate_503_names() == {"seller", "sku_catalog", "msku_bridge", "warehouse"}


def test_refresh_order_puts_parents_before_children():
    """msku_bridge 的外键指着 seller 与 sku_catalog —— 先刷子表必然撞外键。"""
    order = [m.name for m in refresh_order()]
    assert order.index("seller") < order.index("msku_bridge")
    assert order.index("sku_catalog") < order.index("msku_bridge")
    assert set(order) == {m.name for m in MIRRORS if not m.pending}


def test_pending_entries_have_no_fetch():
    # ★ 只断「pending ⇒ 没有 fetch」。反向（非 pending 必须有 fetch）是 Task 2 门禁 (b) 的事，
    #   它从 Task 2 起红到 Task 4 接上真 fetch 才绿——这里若放占位 fetch 让它提前绿，那条红就是假的。
    for m in MIRRORS:
        if m.pending:
            assert m.fetch is None, f"{m.name}: pending 却带着 fetch"


def test_by_name_names_the_miss():
    try:
        by_name("no_such_mirror")
    except KeyError as e:
        assert "no_such_mirror" in str(e)
    else:
        raise AssertionError("查不到的名字必须硬失败，不许返回 None")
```

- [ ] **Step 2: 跑测试确认它红**

Run: `JXD_SCM_CONFIG=/nonexistent uv run pytest -q tests/test_mirror_registry.py`
Expected: FAIL —— `ModuleNotFoundError: No module named 'dim.registry'`

- [ ] **Step 3: 写 `dim/registry.py`**

```python
"""镜像登记表 —— 「有哪些镜像」的唯一真相（设计 §3）。

★ 这里只登记「是什么」，不登记「怎么落库」：PG 的 upsert 文本一旦写进 dim/，
  test_l7_no_writes_to_clickhouse 的正则就会把 `insert into` 扫出来。落库在 jobs/。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Mirror:
    name: str                       # = PG 表名，也是 v_mirror_freshness 的 mirror 值
    stage: str                      # A | B | C | D
    kind: str                       # refresh | append | mutable
    staleness: str                  # gate_503 | label_only | none
    key_columns: tuple[str, ...]
    columns: tuple[str, ...] = ()   # fetch 返回的列序，jobs 据它拼 upsert
    source: str | None = None       # CH / PM / 领星 源；None = 源未定（设计 §10）
    fetch: Callable | None = None   # None ⟺ pending
    coverage: tuple[str, ...] = ()  # ("rows",) | ("rows", "distinct:sid")
    depends_on: tuple[str, ...] = ()
    companions: tuple[str, ...] = ()
    pending: bool = False
    note: str = ""


#: ★ 阶段 A 的 fetch 在 Task 4 接上。先留 None 的是 pending 条目，不是「还没写」——
#:   两者靠 pending 字段分开，test_pending_entries_have_no_fetch… 守着。
MIRRORS: tuple[Mirror, ...] = (
    Mirror(name="seller", stage="A", kind="refresh", staleness="gate_503",
           key_columns=("seller_id",),
           columns=("seller_id", "name", "market", "has_fba", "platform"),
           source="jxd_raw.lingxing_seller_list", coverage=("rows",),
           note="源已裁定（OQ-2，09-22）：jxd_raw.lingxing_seller_list；"
                "has_fba 派生自 lingxing_product_listing.fulfillment_channel_type='FBA'"
                "（OQ-3，同日裁定），不是源列"),
    Mirror(name="sku_catalog", stage="A", kind="refresh", staleness="gate_503",
           key_columns=("sku",), columns=("sku", "name"),
           source="jxd_raw.lingxing_product_local_products", coverage=("rows",)),
    Mirror(name="msku_bridge", stage="A", kind="refresh", staleness="gate_503",
           key_columns=("seller_sku", "sid"), columns=("seller_sku", "sid", "sku"),
           source="jxd_raw.lingxing_product_listing",
           coverage=("rows", "distinct:sid"),
           depends_on=("seller", "sku_catalog"),
           note="★ GROUP BY seller_sku, sid —— sid 绝不参与 argMax"),
    Mirror(name="warehouse", stage="A", kind="refresh", staleness="gate_503",
           key_columns=("wid",), columns=("wid", "name", "kind", "market"),
           source="jxd_raw.lingxing_inventory_warehouses", coverage=("rows",)),
    Mirror(name="sku_category", stage="A", kind="refresh", staleness="label_only",
           key_columns=("sku",), companions=("category_refresh",), pending=True,
           note="A-1。migrations/pg/*.sql 里一张都没有（OQ-9），PM 接入方式未落文档"),
    Mirror(name="po_snapshot", stage="B", kind="append", staleness="none",
           key_columns=("po_no", "observed_on"), pending=True),
    Mirror(name="po_receipt", stage="B", kind="append", staleness="none",
           key_columns=("receipt_no",), pending=True),
    Mirror(name="supplier", stage="B", kind="mutable", staleness="none",
           key_columns=("supplier_id",), pending=True,
           note="源是领星 /erp/sc/data/local_inventory/supplier（08:243），不是 CH"),
    Mirror(name="ext_stage_observation", stage="C", kind="append", staleness="none",
           key_columns=("ext_ref_id", "observed_on"), pending=True),
    Mirror(name="sales_actual", stage="D", kind="append", staleness="none",
           key_columns=("sku", "sid", "period"), pending=True),
)

_BY_NAME = {m.name: m for m in MIRRORS}


def by_name(name: str) -> Mirror:
    if name not in _BY_NAME:
        raise KeyError(f"登记表里没有镜像 {name!r}；已登记的：{sorted(_BY_NAME)}")
    return _BY_NAME[name]


def gate_503_names() -> set[str]:
    return {m.name for m in MIRRORS if m.staleness == "gate_503"}


def refresh_order() -> list[Mirror]:
    """按 depends_on 拓扑排。★ 环要硬失败 —— 静默丢掉一个条目等于那张表永远不刷。"""
    todo = [m for m in MIRRORS if not m.pending]
    out: list[Mirror] = []
    done: set[str] = set()
    while todo:
        ready = [m for m in todo if set(m.depends_on) <= done]
        if not ready:
            raise ValueError(f"depends_on 成环或指向 pending 条目：{[m.name for m in todo]}")
        ready.sort(key=lambda m: m.name)
        out += ready
        done |= {m.name for m in ready}
        todo = [m for m in todo if m.name not in done]
    return out
```

- [ ] **Step 4: 跑测试确认它绿**

Run: `JXD_SCM_CONFIG=/nonexistent uv run pytest -q tests/test_mirror_registry.py`
Expected: 7 passed（★ 这一组必须离线可跑 —— `dim/` 是纯层）

- [ ] **Step 5: 证明门禁真的会红**

临时在 `docs/03-数据库表清单.md` §1 加一行 `| 43 | \`fake_mirror\` | ⑨ | MIRROR | 刷新 | — |`，
重跑上面那条命令，确认 `test_registry_matches_the_doc_exactly` 红且错误信息点名 `fake_mirror`，
然后**把那一行删掉**。★ 不做这一步就分不清「守住了」与「没测到」。

- [ ] **Step 6: 跑分层测试**

Run: `JXD_SCM_CONFIG=/nonexistent uv run pytest -q tests/test_layering.py`
Expected: PASS（`dim/registry.py` 不碰连接、不含 `insert into`）

- [ ] **Step 7: Commit**

```bash
git add dim/registry.py tests/test_mirror_registry.py
git commit -m "feat(dim): 镜像登记表 + 门禁(a) —— 03 §1 新增 MIRROR 行而漏登记就红"
```

---

### Task 2: 迁移 006 · 刷新留痕表与门禁 (b)

**Files:**
- Create: `migrations/pg/006_dim_refresh_run.sql`
- Create: `tests/test_ddl_006_dim_refresh_run.py`
- Modify: `tests/test_mirror_registry.py`（追加门禁 (b)）
- Modify: `tests/conftest.py:20-24`（`DATA_TABLES` 加 `dim_refresh_run`）

**Interfaces:**
- Consumes: `dim.registry.MIRRORS` / `gate_503_names()`（Task 1）
- Produces: 表 `dim_refresh_run(run_id, mirror, trigger, actor, started_at, finished_at, source_max_captured, rows_in, rows_dropped, drop_reasons, ok, error)`；索引 `dim_refresh_run_by_mirror`

- [ ] **Step 1: 写失败的测试**

`tests/test_ddl_006_dim_refresh_run.py`：

```python
"""006 的结构守卫。★ 证据表可改就不再是证据（01 规则 8）。"""
import json

import psycopg2
import pytest

from shared.pg_client import pg_conn


def _insert(cur, **kw):
    kw.setdefault("mirror", "seller")
    kw.setdefault("trigger", "cli")
    cols = ", ".join(kw)
    cur.execute(f"INSERT INTO dim_refresh_run ({cols}) VALUES"
                f" ({', '.join(['%s'] * len(kw))}) RETURNING run_id", tuple(kw.values()))
    return cur.fetchone()[0]


def test_trigger_value_is_whitelisted(wipe):
    with pg_conn() as c, c.cursor() as cur:
        for ok in ("scheduler", "cli", "api"):
            _insert(cur, trigger=ok)
    with pytest.raises(psycopg2.errors.CheckViolation):
        with pg_conn() as c, c.cursor() as cur:
            _insert(cur, trigger="cron")


def test_actor_is_nullable_but_must_exist_when_given(seed):
    with pg_conn() as c, c.cursor() as cur:
        _insert(cur, trigger="scheduler", actor=None)     # ★ 调度没有人，不是「匿名」
        _insert(cur, trigger="api", actor=seed.actor)
    with pytest.raises(psycopg2.errors.ForeignKeyViolation):
        with pg_conn() as c, c.cursor() as cur:
            _insert(cur, trigger="api", actor="nobody")


def test_run_rows_cannot_be_updated_or_deleted(wipe):
    with pg_conn() as c, c.cursor() as cur:
        run_id = _insert(cur, ok=False)
    for sql in ("UPDATE dim_refresh_run SET ok = true WHERE run_id = %s",
                "DELETE FROM dim_refresh_run WHERE run_id = %s"):
        with pytest.raises(psycopg2.errors.RaiseException):
            with pg_conn() as c, c.cursor() as cur:
                cur.execute(sql, (run_id,))


def test_drop_reasons_is_queryable_jsonb(wipe):
    with pg_conn() as c, c.cursor() as cur:
        _insert(cur, rows_in=10, rows_dropped=3,
                drop_reasons=json.dumps({"empty_sku": 3}))
        cur.execute("SELECT drop_reasons->>'empty_sku' FROM dim_refresh_run")
        assert cur.fetchone()[0] == "3"


def test_ok_defaults_to_false(wipe):
    """★ 默认 false：一轮崩在半路、没人写结果，它必须看起来像失败而不是成功。"""
    with pg_conn() as c, c.cursor() as cur:
        run_id = _insert(cur)
        cur.execute("SELECT ok, finished_at FROM dim_refresh_run WHERE run_id = %s", (run_id,))
        assert cur.fetchone() == (False, None)
```

门禁 (b)，追加进 `tests/test_mirror_registry.py`：

```python
def test_every_non_pending_entry_is_backed_by_a_real_table(wipe):
    """★ 登记了却没有 refreshed_at 列 / 不在视图里 —— 两种都会让 503 闸形同虚设。"""
    from shared.pg_client import pg_conn
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT table_name FROM information_schema.columns"
                    " WHERE table_schema = current_schema() AND column_name = 'refreshed_at'")
        has_refreshed_at = {r[0] for r in cur.fetchall()}
        cur.execute("SELECT mirror FROM v_mirror_freshness")
        in_view = {r[0] for r in cur.fetchall()}
    assert in_view, "视图一行都没有 —— 下面的集合比较是空转的"
    for m in MIRRORS:
        if m.pending:
            continue
        assert callable(m.fetch), f"{m.name} 不是 pending，却没有 fetch"
        if m.staleness == "gate_503":
            assert m.name in has_refreshed_at, f"{m.name} 没有 refreshed_at 列"
    assert in_view == gate_503_names(), (
        f"只在视图里：{sorted(in_view - gate_503_names())}；"
        f"只在登记表里：{sorted(gate_503_names() - in_view)}")
```

- [ ] **Step 2: 跑测试确认它红**

Run: `uv run pytest -q tests/test_ddl_006_dim_refresh_run.py tests/test_mirror_registry.py`
Expected: FAIL —— `psycopg2.errors.UndefinedTable: relation "dim_refresh_run" does not exist`；
门禁 (b) 另因 `m.fetch` 仍为 `None` 而 FAIL。

★ **裁定的排期（preflight 09-22，不再有第二条路）**：`test_every_non_pending_entry_is_backed_by_a_real_table`
里 `assert callable(m.fetch)` 这一条从本 Task 起就加进测试文件，**红着一直到 Task 4**
（Task 1 的四条阶段 A 条目**不许**为了让它提前转绿而暂标 `pending=True`——那样会
把「还没接上 fetch」悄悄伪装成「阶段 A 本来就不实现」，跟 `pending` 字段的真实含义
撞车）。Task 3 全程也还是红（它只加 CH 客户端与探针，不碰 `dim/registry.py` 的
`fetch=` 字段）。直到 Task 4 Step 4 把四个 `fetch_*` 接进登记表，这条断言才转绿——
Task 4 那一步会显式确认。**不许把这条断言挪到 Task 4 才加，也不许中途 skip**：
它红着的这段时间，红的理由必须一直是「fetch 还没接上」，而不是别的什么。

- [ ] **Step 3: 写迁移**

`migrations/pg/006_dim_refresh_run.sql`：

```sql
-- 006 · 维度镜像刷新留痕
-- 出处：设计 docs/superpowers/specs/2026-09-22-mirror-refresh-design.md §6（依据 07:483-485）
--
-- ★ 不写 scm. 前缀、不写 BEGIN/COMMIT：同 001 的理由。

CREATE TABLE IF NOT EXISTS dim_refresh_run (
    run_id              bigserial PRIMARY KEY,
    mirror              text        NOT NULL,
    trigger             text        NOT NULL
        CONSTRAINT dim_refresh_run_trigger_known
        CHECK (trigger IN ('scheduler', 'cli', 'api')),
    -- ★ 可空：调度触发的那一轮背后没有人。写成 'system' 会让「机器跑的」
    --   和「某个叫 system 的人跑的」长得一模一样。
    actor               text        REFERENCES actor(actor_id),
    started_at          timestamptz NOT NULL DEFAULT now(),
    finished_at         timestamptz,
    source_max_captured date,          -- 该批 CH 数据的 max(_captured_date)（07:484）
    rows_in             integer,
    rows_dropped        integer,
    drop_reasons        jsonb,         -- 丢的那一侧按原因分列，含 no_baseline
    -- ★ 默认 false：崩在半路没人写结果时，它必须看起来像失败。
    ok                  boolean     NOT NULL DEFAULT false,
    error               text
);

CREATE INDEX IF NOT EXISTS dim_refresh_run_by_mirror
    ON dim_refresh_run (mirror, started_at DESC);

DROP TRIGGER IF EXISTS dim_refresh_run_append_only ON dim_refresh_run;
CREATE TRIGGER dim_refresh_run_append_only
    BEFORE UPDATE OR DELETE ON dim_refresh_run
    FOR EACH ROW EXECUTE FUNCTION forbid_update_delete();
```

- [ ] **Step 4: `conftest.py` 的 `DATA_TABLES` 加上它**

`tests/conftest.py:20-24`，在元组里 `plan_line_event` 之前加 `"dim_refresh_run",`。
★ 不加的话，`wipe` 之后上一条测试的 run 行还在，覆盖面基线会跨测试泄漏。
★ 同时注意 `wipe` 的 `TRUNCATE ... CASCADE` 不触发 BEFORE DELETE 触发器，只追加表清得掉。

- [ ] **Step 5: 应用迁移并跑测试**

Run: `uv run pytest -q tests/test_ddl_006_dim_refresh_run.py tests/test_migrations.py`
Expected: PASS（`business_db` 夹具会在 `scm_test` 上自动 `apply()`）

- [ ] **Step 6: 证明只追加触发器真的挡得住**

Run: `uv run pytest -q tests/test_ddl_006_dim_refresh_run.py::test_run_rows_cannot_be_updated_or_deleted -v`
Expected: PASS。再把 006 里的 `CREATE TRIGGER` 段临时注释掉、重建 `scm_test`
（`DROP SCHEMA scm_test CASCADE` 后重跑）确认它红，然后恢复。

- [ ] **Step 7: Commit**

```bash
git add migrations/pg/006_dim_refresh_run.sql tests/test_ddl_006_dim_refresh_run.py \
        tests/test_mirror_registry.py tests/conftest.py
git commit -m "feat(migrations): 006 刷新留痕表 + 门禁(b) 登记表⇄v_mirror_freshness 一致"
```

---

### Task 3: CH 客户端与源表探针

★ **控制器裁定（09-22）**：OQ-1（四张 CH 源表的真实列名）在本 Task 的探针跑通、
把列名回填进设计 §7 之前**保持开放** —— 其余九条开放问题已裁定（见设计 §10.1），
唯独这一条不许提前标成已解决。

**Files:**
- Create: `shared/ch_client.py`
- Create: `jobs/probe_ch.py`
- Test: `tests/test_ch_client.py`
- Modify: `pyproject.toml`（依赖 + `packages`）
- Modify: `docs/superpowers/specs/2026-09-22-mirror-refresh-design.md` §7（把探针打出来的真实列名填回去，并划掉 OQ-1）

**Interfaces:**
- Consumes: `shared.config.clickhouse()`
- Produces:
  - `shared.ch_client.ch_client()` → `clickhouse_connect` 客户端（免代理 PoolManager）
  - `shared.ch_client.Query = Callable[[str], list[tuple]]`
  - `shared.ch_client.ch_query(client) -> Query` —— 带日志三问的查询函数
  - `shared.ch_client.CH_CONNECT_TIMEOUT_S = 5`

- [ ] **Step 1: 加依赖**

```bash
uv add "clickhouse-connect==1.5.0" "apscheduler==3.11.3"
```

★ `clickhouse-connect` 钉 1.5.0 —— 免代理 `httputil.get_pool_manager(http_proxy=None,
https_proxy=None)` 这条是在它上面实测过的（邻居仓 `model_inventory_forecast`
正在跑这一版）。PyPI 上最新是 1.9.0，`get_pool_manager` 的签名没核过，不跟。
★ `apscheduler` Task 6 才用，这里一起装，省第二次 `uv sync`。

`pyproject.toml` 的 `[tool.setuptools] packages` 不用改（不新增子包）。

- [ ] **Step 2: 写失败的测试**

`tests/test_ch_client.py`：

```python
"""CH 客户端。★ 连不上时要说清「打的谁 · 多久 · 怎么失败的」。"""
import logging

import pytest

from shared import config as config_module
from shared.ch_client import CH_CONNECT_TIMEOUT_S, ch_query


class _Boom:
    def query(self, sql):
        raise TimeoutError("connect timed out")


class _Rows:
    def __init__(self):
        self.seen = []

    def query(self, sql):
        self.seen.append(sql)
        return type("R", (), {"result_rows": [(1, "a")]})()


def test_query_returns_plain_tuples(monkeypatch, tmp_path):
    q = ch_query(_Rows())
    assert q("SELECT 1") == [(1, "a")]


def test_failure_logs_target_elapsed_and_cause(caplog, monkeypatch):
    """★ 只打 str(e) 等于丢掉全部上下文 —— 下次得重新复现一遍。"""
    with caplog.at_level(logging.WARNING, logger="scm.ch"):
        with pytest.raises(TimeoutError):
            ch_query(_Boom())("SELECT 1")
    line = caplog.text
    assert "192.168.66.211" in line and "jxd_raw" in line, "没说打的谁"
    assert "elapsed_ms=" in line, "没说多久"
    assert "TimeoutError" in line, "没说怎么失败的"


def test_timeout_and_connect_failure_are_distinguishable():
    """★ 真超时与连不上签名互斥、处置相反：把后者读成前者会去调大超时，一行都不生效。"""
    from shared.ch_client import describe_failure
    assert describe_failure(TimeoutError("x"))["kind"] == "timeout"
    e = OSError("connect")
    e.errno = 110
    assert describe_failure(e)["kind"] == "connect"


def test_connect_timeout_is_explicit():
    assert CH_CONNECT_TIMEOUT_S == 5
```

- [ ] **Step 3: 跑测试确认它红**

Run: `JXD_SCM_CONFIG=/nonexistent uv run pytest -q tests/test_ch_client.py`
Expected: FAIL —— `ModuleNotFoundError: No module named 'shared.ch_client'`

- [ ] **Step 4: 写 `shared/ch_client.py`**

```python
"""ClickHouse 客户端 —— 只读 jxd_raw。

★ 内网强制无代理：HTTP(S)_PROXY 会把 192.168.66.211 劫持掉，表现是 503 而不是
  「连不上」，于是没人会想到去看代理。这条是邻居仓 model_inventory_forecast
  实测出来的，照搬它的 PoolManager 写法。
★ dim/ 不许 import 本模块（tests/test_layering.py:55）—— 客户端由 jobs/ 注入。
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable

import clickhouse_connect
from clickhouse_connect.driver import httputil

from shared.config import clickhouse

log = logging.getLogger("scm.ch")

#: 内网直连。5 秒连不上就是网络/主机出问题了，不是慢。
CH_CONNECT_TIMEOUT_S = 5

Query = Callable[[str], list[tuple]]


def describe_failure(e: BaseException) -> dict:
    """★ 「超时」与「连不上」必须分得开：前者调大超时有用，后者一行都不生效。"""
    cause = getattr(e, "cause", None) or e.__cause__
    code = getattr(cause, "errno", None) or getattr(e, "errno", None)
    kind = "timeout" if isinstance(e, TimeoutError) else ("connect" if code else "other")
    return {"kind": kind, "type": type(e).__name__, "code": code, "msg": str(e)}


def ch_client():
    c = clickhouse()
    pm = httputil.get_pool_manager(http_proxy=None, https_proxy=None)   # ★ 无代理直连
    return clickhouse_connect.get_client(
        host=c["host"], port=int(c.get("port", 8123)),
        username=c.get("user", "default"), password=c.get("password", ""),
        database=c.get("database", "jxd_raw"), secure=c.get("secure", False),
        connect_timeout=CH_CONNECT_TIMEOUT_S, pool_mgr=pm,
    )


def ch_query(client) -> Query:
    """把查询包上日志三问：打的谁（host/db）· 多久 · 怎么失败的（kind + errno）。"""
    c = clickhouse()
    where = f"{c['host']}:{c.get('port', 8123)}/{c.get('database', 'jxd_raw')}"

    def run(sql: str) -> list[tuple]:
        head = " ".join(sql.split())[:120]
        t0 = time.perf_counter()
        try:
            rows = list(client.query(sql).result_rows)
        except BaseException as e:
            log.warning("op=ch_query target=%s elapsed_ms=%d outcome=fail %s sql=%s",
                        where, (time.perf_counter() - t0) * 1000, describe_failure(e), head)
            raise
        log.info("op=ch_query target=%s elapsed_ms=%d outcome=ok rows=%d sql=%s",
                 where, (time.perf_counter() - t0) * 1000, len(rows), head)
        return rows

    return run
```

- [ ] **Step 5: 跑测试确认它绿**

Run: `JXD_SCM_CONFIG=/nonexistent uv run pytest -q tests/test_ch_client.py`
Expected: 4 passed。★ `test_failure_logs_target_elapsed_and_cause` 读的是
`shared.config.clickhouse()`，`JXD_SCM_CONFIG=/nonexistent` 下会 `FileNotFoundError`
—— 改用 `monkeypatch.setattr(config_module, "_CFG", {...})` 注入一份假配置，
或去掉 `JXD_SCM_CONFIG` 直接用本机 `config.toml`。二选一，别让这条测试静默跳过。

- [ ] **Step 6: 写探针 `jobs/probe_ch.py`**

```python
"""只读探针：把四张源表的真实列名与量级打出来。

★ 存在的理由：设计 §7 的 SQL 草案全部标着「未实测」—— 本仓文档 17:84 / 17:202
  只给了表名与行数，一个列名都没写。先探再写，别对着猜出来的列名写 SQL。
用法：uv run python -m jobs.probe_ch
"""
from __future__ import annotations

import sys

from dim.registry import MIRRORS
from shared.ch_client import ch_client, ch_query, describe_failure

TABLES = [m.source for m in MIRRORS
          if m.source and m.source.startswith("jxd_raw.") and not m.pending]


def main() -> int:
    try:
        q = ch_query(ch_client())
    except BaseException as e:
        print(f"连不上 CH：{describe_failure(e)}", file=sys.stderr)
        return 2
    for table in TABLES:
        db, name = table.split(".", 1)
        print(f"\n=== {table} ===")
        for col, typ in q(f"SELECT name, type FROM system.columns"
                          f" WHERE database = '{db}' AND table = '{name}' ORDER BY position"):
            print(f"  {col:40s} {typ}")
        for rows, days, lo, hi in q(
                f"SELECT count(), uniq(_captured_date), min(_captured_date),"
                f" max(_captured_date) FROM {table}"):
            print(f"  -- {rows} 行 / {days} 个采集日 / {lo} ~ {hi}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 7: 跑探针，把结果填回设计文档**

Run: `uv run python -m jobs.probe_ch`（需内网）
把每张表的真实列名写进 `docs/superpowers/specs/2026-09-22-mirror-refresh-design.md` §7 的
SQL 草案，去掉「未实测」标注，并在 §10 把 OQ-1 标为已解决、列出实测日期。
★ 连不上时**不要跳过这一步**继续往下写 —— 猜出来的列名会一路带到 Task 4 的 SQL 里。

★ **预期状态（preflight 09-22 裁定的排期，见 Task 2 Step 2）**：本 Task 结束时，
`test_mirror_registry.py::test_every_non_pending_entry_is_backed_by_a_real_table`
里 `assert callable(m.fetch)` 那条断言**仍然是红的**——本 Task 只加了 CH 客户端
与探针，没有碰 `dim/registry.py` 的 `fetch=` 字段。这是预期状态，不是本 Task
遗留的缺陷，Task 4 Step 4 接上四个 `fetch_*` 之后才会转绿。

- [ ] **Step 8: Commit**

```bash
git add shared/ch_client.py jobs/probe_ch.py tests/test_ch_client.py \
        pyproject.toml uv.lock docs/superpowers/specs/2026-09-22-mirror-refresh-design.md
git commit -m "feat(shared): CH 只读客户端（免代理）+ 源表列名探针，回填设计 §7"
```

---

### Task 4: 四张镜像的取数与纯变换

**Files:**
- Modify: `dim/ch_source.py`（追加四个 `fetch_*`，**保留**原有 `ChSource` 的四个 `NotImplementedError`）
- Modify: `dim/registry.py`（把四个 `fetch` 接上）
- Create: `tests/fixtures/ch_rows.py`
- Test: `tests/test_dim_ch_source.py`

**Interfaces:**
- Consumes: `shared.ch_client.Query`（类型别名，Task 3）；`dim.registry.Mirror`（Task 1）
- Produces：
  - `dim.ch_source.Fetched = NamedTuple("Fetched", rows: list[tuple], dropped: int, drop_reasons: dict[str, int], source_max_captured: datetime.date | None)`
  - `dim.ch_source.UnknownShape(Exception)` —— 认不出的形态，硬失败
  - `fetch_seller(query) -> Fetched` · `fetch_sku_catalog(query) -> Fetched`
    · `fetch_msku_bridge(query) -> Fetched` · `fetch_warehouse(query) -> Fetched`
  - 每个 `Fetched.rows` 的列序 = 对应 `Mirror.columns`

- [ ] **Step 1: 写失败的测试**

`tests/fixtures/ch_rows.py`：

```python
"""假 CH 返回。★ 每组都带一个「长得像 0 其实不是」的形态。"""
import datetime as dt

D1, D2 = dt.date(2026, 9, 20), dt.date(2026, 9, 21)

SKU_CATALOG = [("DCC1800264G1", "猫爬架", D2), ("", "无名氏", D2)]          # 空货号要被丢掉
#: ★ OQ-3 裁定（09-22）：SQL_SELLER 已 join lingxing_product_listing 派生 has_fba_flag，
#:   所以假行是 (seller_id, name, market, platform, captured, has_fba_flag) 六元组 ——
#:   11072 名下有条 FBA listing、11094 没有。
SELLER = [("11072", "A4Pet-US", "US", "amazon", D2, 1),
          ("11094", "A4Pet-BS-UK", "UK", "amazon", D1, 0)]
MSKU_BRIDGE = [("MSKU-A", "11072", "DCC1800264G1", D2),
               ("MSKU-A", "11094", "DCC1800264G1", D2),   # ★ 同串跨店 = 两个 listing
               ("MSKU-Z", "11072", "", D2)]               # 未绑货号要被丢掉
WAREHOUSE = [(1, "11-北美亚马逊东孚仓", 1, 0, D2),
             (2, "30-LA自有海外仓", 3, 1, D2),
             (3, "A4pet英国仓", 3, 2, D2)]
WAREHOUSE_UNKNOWN = WAREHOUSE + [(9, "说不清是什么仓", 7, 7, D2)]


def replay(rows):
    """把一组固定行喂给 fetch_*，无视它发的 SQL。"""
    return lambda sql: list(rows)
```

`tests/test_dim_ch_source.py`：

```python
"""取数的纯变换。★ 离线可跑 —— dim/ 是纯层，客户端是注入的。"""
import datetime as dt

import pytest

from dim import ch_source as cs
from dim.registry import by_name
from tests.fixtures import ch_rows as R


def test_sku_catalog_drops_empty_and_counts_what_it_dropped():
    got = cs.fetch_sku_catalog(R.replay(R.SKU_CATALOG))
    assert got.rows == [("DCC1800264G1", "猫爬架")]
    assert got.dropped == 1 and got.drop_reasons == {"empty_sku": 1}
    assert got.source_max_captured == R.D2


def test_msku_bridge_keeps_the_same_string_in_two_stores():
    """★ 同一 msku 字符串跨店是不同 listing —— 合并成一行就丢了一个店的库存。"""
    got = cs.fetch_msku_bridge(R.replay(R.MSKU_BRIDGE))
    assert sorted(got.rows) == [("MSKU-A", "11072", "DCC1800264G1"),
                                ("MSKU-A", "11094", "DCC1800264G1")]
    assert got.drop_reasons == {"unbound_sku": 1}


def test_msku_bridge_sql_never_argmaxes_sid():
    """★ 实测教训：把 sid 也 argMax 掉，美国两店的 msku 数在 735~1445 之间摆动。"""
    sql = cs.SQL_MSKU_BRIDGE.lower()
    assert "group by seller_sku, sid" in " ".join(sql.split())
    assert "argmax(sid" not in " ".join(sql.split()).replace(" ", "")


def test_warehouse_kind_is_mapped_from_type_and_subtype():
    got = cs.fetch_warehouse(R.replay(R.WAREHOUSE))
    assert [r[2] for r in got.rows] == ["local", "oversea_self", "oversea_3pl"]
    assert [r[3] for r in got.rows] == [None, None, None], "market 无源，留 NULL 不写 ''"
    assert got.drop_reasons.get("market_null") == 3


def test_unknown_warehouse_kind_fails_loudly():
    """★ 认不出的形态硬失败 —— 落进 else '' 再被下游过滤掉，就是静默少一批仓。"""
    with pytest.raises(cs.UnknownShape) as ei:
        cs.fetch_warehouse(R.replay(R.WAREHOUSE_UNKNOWN))
    assert "9" in str(ei.value) and "说不清是什么仓" in str(ei.value)


def test_seller_has_fba_is_derived_from_listing_fulfillment_channel():
    """OQ-3 裁定（控制器 09-22）：has_fba 是派生列 —— 该 sid 名下只要有一条
    listing 的 fulfillment_channel_type = 'FBA' 就算有 FBA，不是猜的默认值。"""
    got = cs.fetch_seller(R.replay(R.SELLER))
    assert {r[0]: r[3] for r in got.rows} == {"11072": True, "11094": False}


def test_row_width_matches_the_registry():
    for name, fn, rows in [("sku_catalog", cs.fetch_sku_catalog, R.SKU_CATALOG),
                           ("msku_bridge", cs.fetch_msku_bridge, R.MSKU_BRIDGE),
                           ("warehouse", cs.fetch_warehouse, R.WAREHOUSE),
                           ("seller", cs.fetch_seller, R.SELLER)]:      # OQ-3 已裁定，seller 不再抛
        got = fn(R.replay(rows))
        assert all(len(r) == len(by_name(name).columns) for r in got.rows), name


def test_empty_source_is_not_silently_ok():
    """★ 一行都没取到不是「刷新成功、只是没数据」。"""
    with pytest.raises(cs.UnknownShape):
        cs.fetch_sku_catalog(R.replay([]))
```

- [ ] **Step 2: 跑测试确认它红**

Run: `JXD_SCM_CONFIG=/nonexistent uv run pytest -q tests/test_dim_ch_source.py`
Expected: FAIL —— `AttributeError: module 'dim.ch_source' has no attribute 'fetch_sku_catalog'`

- [ ] **Step 3: 写取数与变换**

在 `dim/ch_source.py` 末尾追加（★ 不动文件里原有的 `ChSource`，它是 `Source` 协议的
阶段 B 占位，与本次的镜像刷新是两件事）：

```python
from collections.abc import Callable
from typing import NamedTuple


class Fetched(NamedTuple):
    rows: list[tuple]
    dropped: int
    drop_reasons: dict[str, int]
    source_max_captured: dt.date | None


class UnknownShape(Exception):
    """认不出的形态 / 空结果。★ 硬失败，不许落进 else '' 再被下游过滤掉。"""


Query = Callable[[str], list[tuple]]

# ★ 列名已由 jobs/probe_ch.py 实测（Task 3 Step 7）。未跑探针就照搬草案 = 在猜。
SQL_SKU_CATALOG = """
SELECT local_sku AS sku,
       argMax(product_name, _captured_date) AS name,
       max(_captured_date)                  AS captured
  FROM jxd_raw.lingxing_product_local_products
 GROUP BY local_sku
"""

SQL_MSKU_BRIDGE = """
SELECT seller_sku,
       toString(sid)                     AS sid,
       argMax(local_sku, _captured_date) AS sku,
       max(_captured_date)               AS captured
  FROM jxd_raw.lingxing_product_listing
 WHERE _captured_date >= today() - 30
 GROUP BY seller_sku, sid
"""

SQL_WAREHOUSE = """
SELECT wid,
       argMax(name, _captured_date)     AS name,
       argMax(type, _captured_date)     AS type,
       argMax(sub_type, _captured_date) AS sub_type,
       max(_captured_date)              AS captured
  FROM jxd_raw.lingxing_inventory_warehouses
 GROUP BY wid
"""

#: ★ OQ-3 裁定（控制器 09-22）：has_fba 是派生列，不是 lingxing_seller_list 自带的
#:   列 —— join lingxing_product_listing，该 sid 名下只要有一条 listing 的
#:   fulfillment_channel_type = 'FBA' 就算有 FBA（依据：兄弟仓
#:   027_fbm_ships_from_overseas_pool.sql:30「一律用
#:   lingxing_product_listing.fulfillment_channel_type」）。platform 仍取自
#:   lingxing_seller_list 本身（OQ-2 裁定同日）。
SQL_SELLER = """
SELECT toString(s.sid)                       AS seller_id,
       argMax(s.name, s._captured_date)      AS name,
       argMax(s.marketplace, s._captured_date) AS market,
       argMax(s.platform, s._captured_date)  AS platform,
       max(s._captured_date)                 AS captured,
       maxIf(1, l.fulfillment_channel_type = 'FBA') AS has_fba_flag
  FROM jxd_raw.lingxing_seller_list s
  LEFT JOIN jxd_raw.lingxing_product_listing l ON l.sid = s.sid
 GROUP BY s.sid
"""

#: 001:54 的 CHECK 只认四个值。fba 这一档在这张源表里没有（OQ-4 裁定：阶段 A 不产出，
#:   CHECK 保留该值给阶段 C）。
_WAREHOUSE_KIND = {(1, 0): "local", (1, 1): "local",
                   (3, 1): "oversea_self", (3, 2): "oversea_3pl"}


def _nonempty(rows: list[tuple], table: str) -> None:
    if not rows:
        raise UnknownShape(
            f"{table} 取回 0 行。★ 空不是「刷新成功、只是没数据」—— "
            "把采集缺口读成空会让整张镜像被清成不存在")


def fetch_sku_catalog(query: Query) -> Fetched:
    raw = query(SQL_SKU_CATALOG)
    _nonempty(raw, "lingxing_product_local_products")
    rows, reasons = [], {}
    for sku, name, _cap in raw:
        if not (sku or "").strip():
            reasons["empty_sku"] = reasons.get("empty_sku", 0) + 1
            continue
        rows.append((sku, name or ""))
    return Fetched(rows, sum(reasons.values()), reasons, max(r[2] for r in raw))


def fetch_msku_bridge(query: Query) -> Fetched:
    raw = query(SQL_MSKU_BRIDGE)
    _nonempty(raw, "lingxing_product_listing")
    rows, reasons = [], {}
    for seller_sku, sid, sku, _cap in raw:
        if not (sku or "").strip():
            reasons["unbound_sku"] = reasons.get("unbound_sku", 0) + 1
            continue
        rows.append((seller_sku, str(sid), sku))
    return Fetched(rows, sum(reasons.values()), reasons, max(r[3] for r in raw))


def fetch_warehouse(query: Query) -> Fetched:
    raw = query(SQL_WAREHOUSE)
    _nonempty(raw, "lingxing_inventory_warehouses")
    rows, reasons = [], {}
    for wid, name, typ, sub, _cap in raw:
        kind = _WAREHOUSE_KIND.get((int(typ), int(sub or 0)))
        if kind is None:
            raise UnknownShape(
                f"仓 {wid}「{name}」的 type/sub_type = {typ}/{sub} 认不出。"
                "★ 仓名改了要的是报错，不是悄悄少一批货")
        # ★ market 对国内仓无源（OQ-5 裁定：阶段 A 一律留 NULL，不在这里解析中文
        #   仓名 —— 解析只能在一个地方，是兄弟仓 gen_locations.py 的活，不是这里）。
        #   留 NULL 而不是 ''：空是「还没到」，'' 会被当成「查过了、就是没有」。
        reasons["market_null"] = reasons.get("market_null", 0) + 1
        # ★ OQ-4 裁定：按 kind 记一条计数日志，方便一眼看出这轮刷了多少哪一档的仓。
        reasons[f"kind_{kind}"] = reasons.get(f"kind_{kind}", 0) + 1
        rows.append((int(wid), name, kind, None))
    return Fetched(rows, 0, reasons, max(r[4] for r in raw))


def fetch_seller(query: Query) -> Fetched:
    """★ OQ-3 裁定（控制器 09-22）：has_fba 不再是「无源刻意抛」，是派生列 ——
    SQL_SELLER 已经 join 出 has_fba_flag，这里只做类型收敛。不再抛异常，
    seller 这一路从此和其它三张镜像一样能正常刷出来。"""
    raw = query(SQL_SELLER)
    _nonempty(raw, "lingxing_seller_list")
    rows = []
    for seller_id, name, market, platform, _cap, has_fba_flag in raw:
        rows.append((seller_id, name or "", market or "", bool(has_fba_flag), platform or ""))
    return Fetched(rows, 0, {}, max(r[4] for r in raw))
```

★ **OQ-3 已裁定（控制器 09-22）**：`fetch_seller` 不再刻意抛异常 —— `has_fba`
是从 `lingxing_product_listing.fulfillment_channel_type` 派生出来的列，
`SQL_SELLER` 的 join 已经算好 `has_fba_flag`，`fetch_seller` 只做布尔收敛。
`seller` 这一路从此和其余三张镜像一样能正常刷出来，Task 5/8 里「seller 会以
`ok=false` 出现」的说明已按此删除。

- [ ] **Step 4: 把 fetch 接进登记表**

`dim/registry.py` 顶部加 `from dim import ch_source`，四条阶段 A 条目分别加
`fetch=ch_source.fetch_seller` / `fetch_sku_catalog` / `fetch_msku_bridge` / `fetch_warehouse`。

- [ ] **Step 5: 跑测试确认它绿**

Run: `JXD_SCM_CONFIG=/nonexistent uv run pytest -q tests/test_dim_ch_source.py tests/test_mirror_registry.py tests/test_layering.py`
Expected: PASS（门禁 (b) 的 `callable(m.fetch)` 这一条此时转绿）

- [ ] **Step 6: 证明「认不出就硬失败」真的会红**

把 `_WAREHOUSE_KIND` 临时改成带 `else "local"` 的写法，重跑
`test_unknown_warehouse_kind_fails_loudly`，确认它红，然后改回去。

- [ ] **Step 7: Commit**

```bash
git add dim/ch_source.py dim/registry.py tests/test_dim_ch_source.py tests/fixtures/ch_rows.py
git commit -m "feat(dim): 四张镜像的取数 SQL 与纯变换 —— 丢弃分类计数，认不出的形态硬失败"
```

---

### Task 5: 刷新作业 · 互斥锁 · 覆盖面 · CLI

**Files:**
- Create: `jobs/lock.py`
- Create: `jobs/refresh_dims.py`
- Test: `tests/test_jobs_refresh_dims.py`

**Interfaces:**
- Consumes: `dim.registry.refresh_order()` / `by_name()`（Task 1）· `dim.ch_source.Fetched` / `UnknownShape`（Task 4）· `shared.ch_client.ch_client/ch_query`（Task 3）· 表 `dim_refresh_run`（Task 2）
- Produces:
  - `jobs.lock.advisory_lock()` —— contextmanager；拿不到抛 `jobs.lock.RefreshInFlight`
  - `jobs.lock.ADVISORY_LOCK_KEY = 20260922`
  - `jobs.refresh_dims.CoverageDrop(Exception)`
  - `jobs.refresh_dims.RunRow = NamedTuple(mirror: str, run_id: int, ok: bool, rows_in: int, rows_dropped: int, error: str | None)`
  - `jobs.refresh_dims.refresh_one(cur, mirror, query, trigger, actor) -> RunRow`
  - `jobs.refresh_dims.refresh_all(trigger: str, actor: str | None = None, only: str | None = None, query=None) -> list[RunRow]`
  - `python -m jobs.refresh_dims [--only NAME]`，退出码 `0` 全成 / `1` 有失败 / `3` 锁被占

- [ ] **Step 1: 写失败的测试**

`tests/test_jobs_refresh_dims.py`：

```python
"""刷新作业。★ 掉档拒批、旧镜像不动、留痕写全。"""
import pytest

from dim.registry import by_name
from jobs import refresh_dims as rd
from jobs.lock import RefreshInFlight, advisory_lock
from shared.pg_client import pg_conn
from tests.fixtures import ch_rows as R


def _rows(cur, sql, *a):
    cur.execute(sql, a)
    return cur.fetchall()


def test_second_holder_is_refused_not_queued(wipe):
    """★ 三个入口同时刷 = 同一张表两个写入者。排队或静默返回成功都不行。"""
    with advisory_lock():
        with pytest.raises(RefreshInFlight):
            with advisory_lock():
                pass
    with advisory_lock():          # ★ 前一次释放了才拿得到 —— 证明锁真的解开了
        pass


def test_refresh_writes_rows_and_bumps_refreshed_at(seed):
    got = rd.refresh_all("cli", actor=seed.actor, only="sku_catalog",
                         query=R.replay(R.SKU_CATALOG))
    assert [r.ok for r in got] == [True]
    with pg_conn() as c, c.cursor() as cur:
        assert _rows(cur, "SELECT count(*) FROM sku_catalog WHERE sku = 'DCC1800264G1'") == [(1,)]
        run = _rows(cur, "SELECT mirror, trigger, actor, rows_in, rows_dropped,"
                         " drop_reasons, ok, finished_at IS NOT NULL, source_max_captured"
                         " FROM dim_refresh_run")[0]
    assert run[:5] == ("sku_catalog", "cli", seed.actor, 1, 1)
    assert run[5] == {"empty_sku": 1, "no_baseline": 1}      # ★ 首轮无基线要留声
    assert run[6] is True and run[7] is True
    assert run[8] == R.D2


def test_coverage_drop_refuses_the_batch_and_keeps_the_old_mirror(seed):
    big = [(f"SKU{i}", f"名{i}", R.D2) for i in range(100)]
    rd.refresh_all("cli", only="sku_catalog", query=R.replay(big))
    with pg_conn() as c, c.cursor() as cur:
        before = _rows(cur, "SELECT count(*), max(refreshed_at) FROM sku_catalog")[0]

    got = rd.refresh_all("cli", only="sku_catalog", query=R.replay(big[:50]))
    assert [r.ok for r in got] == [False]
    assert "coverage_drop" in (got[0].error or "")
    with pg_conn() as c, c.cursor() as cur:
        assert _rows(cur, "SELECT count(*), max(refreshed_at) FROM sku_catalog")[0] == before, (
            "拒批了却动了镜像 —— 旧数据必须原封不动")
        assert _rows(cur, "SELECT ok, error FROM dim_refresh_run ORDER BY run_id DESC LIMIT 1"
                     )[0][0] is False


def test_msku_bridge_coverage_also_counts_distinct_sid(seed):
    """★ 整店 0 行是采集缺口的形状，总行数看不出来 —— 已踩第六次。"""
    two = R.MSKU_BRIDGE
    one = [r for r in two if r[1] == "11072"] * 2      # 行数不掉，少一个店
    rd.refresh_all("cli", only="msku_bridge", query=R.replay(two))
    got = rd.refresh_all("cli", only="msku_bridge", query=R.replay(one))
    assert [r.ok for r in got] == [False]
    assert "distinct:sid" in (got[0].error or "")


def test_a_failed_mirror_does_not_stop_the_others(seed):
    """`07:486` 的同一条：一批失败不影响其它批。
    ★ OQ-3 裁定后 seller 不再天然失败（has_fba 已可派生），改用「混进一行认不出
    的仓形态」来制造这一条要验证的失败——warehouse 挂，其余三张照常成功。"""
    got = rd.refresh_all("cli", query=_mixed_query())
    names = {r.mirror: r.ok for r in got}
    assert names["warehouse"] is False
    assert names["seller"] is True
    assert names["sku_catalog"] is True
    assert set(names) == {m.name for m in rd.registry.refresh_order()}


def test_unknown_mirror_name_is_refused(seed):
    with pytest.raises(KeyError):
        rd.refresh_all("cli", only="no_such_mirror", query=R.replay([]))


def _mixed_query():
    """按 SQL 里出现的表名派发到对应的假行。"""
    from dim import ch_source as cs
    table = {cs.SQL_SKU_CATALOG: R.SKU_CATALOG, cs.SQL_MSKU_BRIDGE: R.MSKU_BRIDGE,
             cs.SQL_WAREHOUSE: R.WAREHOUSE_UNKNOWN, cs.SQL_SELLER: R.SELLER}
    return lambda sql: list(table[sql])
```

- [ ] **Step 2: 跑测试确认它红**

Run: `uv run pytest -q tests/test_jobs_refresh_dims.py`
Expected: FAIL —— `ModuleNotFoundError: No module named 'jobs.lock'`

- [ ] **Step 3: 写 `jobs/lock.py`**

```python
"""三个入口共用的互斥锁。

★ 用 PG 的**会话级** advisory lock：它随连接断开自动释放，所以进程被 kill -9
  也不会留一把没人解的锁。代价是这个连接必须在整轮刷新期间一直开着。
"""
from __future__ import annotations

import contextlib
import logging

from shared.pg_client import pg_conn

log = logging.getLogger("scm.jobs")

#: 任取的常量，全仓唯一即可。取设计定稿日，方便在 pg_locks 里认出它是谁。
ADVISORY_LOCK_KEY = 20260922


class RefreshInFlight(Exception):
    def __init__(self):
        super().__init__(
            f"另一轮镜像刷新正在跑（advisory lock {ADVISORY_LOCK_KEY}）。"
            "★ 不排队也不返回成功 —— 排队会让两轮写同一张表，"
            "返回成功会让调用方以为刷过了")


@contextlib.contextmanager
def advisory_lock():
    with pg_conn(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
        if not cur.fetchone()[0]:
            log.warning("op=advisory_lock key=%s outcome=busy", ADVISORY_LOCK_KEY)
            raise RefreshInFlight()
        log.info("op=advisory_lock key=%s outcome=acquired", ADVISORY_LOCK_KEY)
        try:
            yield
        finally:
            cur.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))
            log.info("op=advisory_lock key=%s outcome=released", ADVISORY_LOCK_KEY)
```

- [ ] **Step 4: 写 `jobs/refresh_dims.py`**

```python
"""维度镜像刷新。取数在 dim/，落库与留痕在这里（设计 §7.1）。

★ upsert 的 SQL 只能写在本层：test_l7_no_writes_to_clickhouse 按正则扫 dim/ 里的
  `insert into`，一段 upsert 字符串就会让它红。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from typing import NamedTuple

from psycopg2.extras import execute_values

from dim import ch_source, registry
from jobs.lock import RefreshInFlight, advisory_lock
from shared.ch_client import ch_client, ch_query
from shared.config import freshness
from shared.pg_client import pg_conn, timed

log = logging.getLogger("scm.jobs")


class CoverageDrop(Exception):
    pass


class RunRow(NamedTuple):
    mirror: str
    run_id: int
    ok: bool
    rows_in: int
    rows_dropped: int
    error: str | None


def _threshold() -> float:
    # ★ 设计取值 · 未实测（OQ-6 裁定 09-22 = 0.30）。兄弟仓实测过库存日报单日掉 40%，
    #   取比它更松的阈值，免得把 30% 内的正常波动也当掉档拒了。
    return float(freshness().get("coverage_drop_threshold", 0.30))


def _baseline(cur, mirror: str) -> tuple[int, dict] | None:
    cur.execute("SELECT rows_in, drop_reasons FROM dim_refresh_run"
                " WHERE mirror = %s AND ok ORDER BY run_id DESC LIMIT 1", (mirror,))
    row = cur.fetchone()
    return (row[0], row[1] or {}) if row else None


def _check_coverage(cur, m: registry.Mirror, fetched: ch_source.Fetched,
                    reasons: dict) -> None:
    """`07:485`：掉档超阈值 → 整批不可用、不写。★ 首轮没基线要留声，不是静默放行。"""
    base = _baseline(cur, m.name)
    if base is None:
        reasons["no_baseline"] = 1
        log.warning("op=coverage mirror=%s outcome=no_baseline rows_in=%d"
                    " —— 首轮无基线，本轮不做掉档比较", m.name, len(fetched.rows))
        return
    limit = 1.0 - _threshold()
    prev_rows, prev_reasons = base
    if prev_rows and len(fetched.rows) < prev_rows * limit:
        raise CoverageDrop(f"coverage_drop rows {len(fetched.rows)} < {prev_rows} × {limit:.2f}")
    # ★ 一趟循环做两件事：算出这一轮的 distinct 计数（不管比不比得过都要记，
    #   下一轮要拿它当基线）、再拿它跟上一轮比。原来分两个循环各扫一遍 m.coverage，
    #   合并成一趟。
    for rule in m.coverage:
        if not rule.startswith("distinct:"):
            continue
        col = rule.split(":", 1)[1]
        idx = m.columns.index(col)
        now = len({r[idx] for r in fetched.rows})
        reasons[f"distinct_{col}"] = now
        prev = prev_reasons.get(f"distinct_{col}")
        if prev and now < prev * limit:
            raise CoverageDrop(
                f"coverage_drop {rule} {now} < {prev} × {limit:.2f} —— "
                "整店 0 行是采集缺口的形状，总行数看不出来")


def _upsert(cur, m: registry.Mirror, rows: list[tuple], at: dt.datetime) -> None:
    """★ 只 upsert，绝不 DELETE 源里消失的行（设计 §6 判据 6）。"""
    cols = ", ".join([*m.columns, "refreshed_at"])
    keys = ", ".join(m.key_columns)
    sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in [*m.columns, "refreshed_at"]
                     if c not in m.key_columns)
    execute_values(
        cur,
        f"INSERT INTO {m.name} ({cols}) VALUES %s"
        f" ON CONFLICT ({keys}) DO UPDATE SET {sets}",
        [(*r, at) for r in rows])


def refresh_one(cur, m: registry.Mirror, query, trigger: str, actor: str | None) -> RunRow:
    """★ `dim_refresh_run` 只追加（006 的 `BEFORE UPDATE OR DELETE` 触发器挡住了
    `UPDATE`）—— 整轮跑完只在收尾时写**一次完整的行**，不分「先 INSERT 占位、
    再 UPDATE 补结果」两次。`started_at` 在 Python 侧先取好时间戳，跟收尾那次
    INSERT 一起落库，而不是靠数据库的 `DEFAULT now()`（那样会记成收尾时刻）。"""
    started_at = dt.datetime.now(dt.UTC)
    rows_in = rows_dropped = 0
    reasons: dict = {}
    source_max_captured = None
    ok = False
    error: str | None = None
    try:
        fetched = m.fetch(query)
        rows_in, rows_dropped = len(fetched.rows), fetched.dropped
        reasons = dict(fetched.drop_reasons)
        _check_coverage(cur, m, fetched, reasons)
        _upsert(cur, m, fetched.rows, dt.datetime.now(dt.UTC))
        source_max_captured = fetched.source_max_captured
        ok = True
    except BaseException as e:
        # ★ 失败也要写完整的一行：只打日志的话，明天没人知道今天刷过、更不知道为什么没成
        error = f"{type(e).__name__}: {e}"[:2000]
        log.warning("op=refresh mirror=%s trigger=%s outcome=fail err=%s",
                    m.name, trigger, e)
    cur.execute(
        "INSERT INTO dim_refresh_run (mirror, trigger, actor, started_at, finished_at,"
        " source_max_captured, rows_in, rows_dropped, drop_reasons, ok, error)"
        " VALUES (%s, %s, %s, %s, now(), %s, %s, %s, %s, %s, %s) RETURNING run_id",
        (m.name, trigger, actor, started_at, source_max_captured, rows_in, rows_dropped,
         json.dumps(reasons, ensure_ascii=False), ok, error))
    run_id = cur.fetchone()[0]
    if ok:
        log.info("op=refresh mirror=%s trigger=%s outcome=ok rows_in=%d dropped=%d %s",
                 m.name, trigger, rows_in, rows_dropped, reasons)
    return RunRow(m.name, run_id, ok, rows_in, rows_dropped, error)
```

★ **已裁定（preflight 09-22）**：Task 2 Step 1 的 `test_run_rows_cannot_be_updated_or_deleted`
就是钉住「只追加」这一条的门禁；上面的实现是它唯一能通过的写法 ——
`refresh_one` 全程只有这一条 `INSERT`，没有任何 `UPDATE ... WHERE run_id`。
旧版「先 INSERT 占位、成败后再 UPDATE 补结果」的写法在 006 迁移之下**根本跑不动**：
第一次调用就会撞上 `forbid_update_delete()` 触发器，抛
`psycopg2.errors.RaiseException`，这不是「二选一待定」，是已经选定并改完的实现。

续写：

```python
def refresh_all(trigger: str, actor: str | None = None, only: str | None = None,
                query=None) -> list[RunRow]:
    targets = [registry.by_name(only)] if only else registry.refresh_order()
    if only and targets[0].pending:
        raise KeyError(f"{only} 是 pending 条目，本阶段没有取数实现")
    own_query = query is None
    if own_query:
        query = ch_query(ch_client())
    out: list[RunRow] = []
    with advisory_lock(), timed("refresh_dims", trigger=trigger, only=only or "all"):
        for m in targets:
            # ★ 一个镜像一个短事务：一批失败不影响其它批（07:486）
            with pg_conn() as conn, conn.cursor() as cur:
                out.append(refresh_one(cur, m, query, trigger, actor))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="刷新维度镜像")
    ap.add_argument("--only", help="只刷这一个镜像")
    args = ap.parse_args(argv)
    try:
        rows = refresh_all("cli", only=args.only)
    except RefreshInFlight as e:
        print(e, file=sys.stderr)
        return 3
    for r in rows:
        print(f"{r.mirror:22s} ok={r.ok} rows_in={r.rows_in} dropped={r.rows_dropped}"
              f"{' ' + r.error if r.error else ''}")
    return 0 if all(r.ok for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: 跑测试确认它绿**

Run: `uv run pytest -q tests/test_jobs_refresh_dims.py`
Expected: PASS。★ 这一步顺带验证了 `refresh_one` 只有一条 `INSERT`、没有任何
`UPDATE ... WHERE run_id`——006 的 `forbid_update_delete()` 触发器不会被撞到
（preflight 09-22 发现的头号缺陷：旧写法在这一步会以
`psycopg2.errors.RaiseException` 整体炸掉，不是断言失败）。

- [ ] **Step 6: 证明掉档闸不是摆设**

把 `_check_coverage` 里的 `raise CoverageDrop` 临时换成 `log.warning(...)`，
重跑 `test_coverage_drop_refuses_the_batch_and_keeps_the_old_mirror` 与
`test_msku_bridge_coverage_also_counts_distinct_sid`，确认两条都红，然后改回去。

- [ ] **Step 7: Commit**

```bash
git add jobs/lock.py jobs/refresh_dims.py tests/test_jobs_refresh_dims.py
git commit -m "feat(jobs): 镜像刷新作业 —— advisory lock 互斥、掉档拒批、逐轮留痕、CLI"
```

---

### Task 6: 调度器 · 配置 · 运维触发接口

**Files:**
- Create: `jobs/scheduler.py`
- Create: `api/ui/ops.py`
- Modify: `api/__init__.py:65-100`（lifespan 起停调度器 + 注册路由）
- Modify: `shared/config.py:41-42`（`freshness()` 合并默认值）
- Modify: `config.example.toml:24-28`
- Test: `tests/test_jobs_scheduler.py` · `tests/test_api_ops.py`

**Interfaces:**
- Consumes: `jobs.refresh_dims.refresh_all` / `RunRow`（Task 5）· `jobs.lock.RefreshInFlight`
- Produces:
  - `jobs.scheduler.build_scheduler() -> AsyncIOScheduler | None`（`scheduler_enabled=false` 时返回 `None` 并打 WARNING）
  - `jobs.scheduler.start(app) -> None` · `jobs.scheduler.shutdown() -> None`
  - `jobs.scheduler.REFRESH_JOB_ID = "refresh_dims"`
  - `POST /v1/jobs/refresh-dims` —— 必须带 `x-actor`；体可选 `{"only": "..."}`；
    200 `{"runs": [{mirror, run_id, ok, rows_in, rows_dropped, error}]}`；
    锁被占 → 409 `refresh_in_flight`；未知镜像 → 404 `unknown_mirror`
- `shared.config.freshness()` 保证返回全部五个键

- [ ] **Step 1: 写失败的测试**

`tests/test_jobs_scheduler.py`：

```python
"""调度器。★ 关着的调度器要吵一声 —— 不执行的东西不会失败。"""
import logging

from apscheduler.triggers.cron import CronTrigger

from jobs import scheduler as S
from shared import config as config_module


def _with_freshness(monkeypatch, **kw):
    base = {"max_age_hours": 24, "refresh_at": "06:30", "scheduler_enabled": True,
            "coverage_drop_threshold": 0.30, "startup_gate": True,   # OQ-6 裁定 09-22
            "refresh_timezone": "Asia/Shanghai"}
    monkeypatch.setattr(config_module, "_CFG", {"freshness": {**base, **kw}}, raising=False)
    config_module._CFG = {"freshness": {**base, **kw}}


def test_cron_uses_the_configured_time_and_timezone(monkeypatch):
    _with_freshness(monkeypatch, refresh_at="07:15")
    sch = S.build_scheduler()
    job = sch.get_job(S.REFRESH_JOB_ID)
    assert isinstance(job.trigger, CronTrigger)
    assert (str(job.trigger.fields[job.trigger.FIELD_NAMES.index("hour")]) == "7"
            and str(job.trigger.fields[job.trigger.FIELD_NAMES.index("minute")]) == "15")
    assert "Shanghai" in str(job.trigger.timezone)


def test_misfire_and_coalesce_are_set(monkeypatch):
    _with_freshness(monkeypatch)
    job = S.build_scheduler().get_job(S.REFRESH_JOB_ID)
    assert job.coalesce is True and job.misfire_grace_time == 3600


def test_disabled_scheduler_says_so_out_loud(monkeypatch, caplog):
    _with_freshness(monkeypatch, scheduler_enabled=False)
    with caplog.at_level(logging.WARNING, logger="scm.jobs"):
        assert S.build_scheduler() is None
    assert "scheduler_enabled=false" in caplog.text


def test_bad_refresh_at_fails_at_build_not_at_0630(monkeypatch):
    """★ 配置类错误往启动钩子放 —— 等到第二天早上才炸是查不回来的。"""
    _with_freshness(monkeypatch, refresh_at="半夜")
    try:
        S.build_scheduler()
    except ValueError as e:
        assert "refresh_at" in str(e)
    else:
        raise AssertionError("配置写错了却起得来")


def test_freshness_config_has_every_key_even_when_toml_omits_them(monkeypatch):
    monkeypatch.setattr(config_module, "_CFG", {}, raising=False)
    config_module._CFG = {}
    got = config_module.freshness()
    assert set(got) >= {"max_age_hours", "refresh_at", "scheduler_enabled",
                        "coverage_drop_threshold", "startup_gate", "refresh_timezone"}
```

`tests/test_api_ops.py`：

```python
"""运维触发接口。★ 仅运维可用的手动触发（07:482）。"""
import pytest

from jobs.lock import advisory_lock


def test_trigger_requires_actor(client, seed):
    assert client.post("/v1/jobs/refresh-dims", json={}).status_code == 400


def test_trigger_reports_每个镜像的结果(client, seed, monkeypatch):
    from jobs import refresh_dims as rd
    monkeypatch.setattr(rd, "refresh_all",
                        lambda *a, **k: [rd.RunRow("sku_catalog", 1, True, 3, 0, None)])
    r = client.post("/v1/jobs/refresh-dims", json={}, headers={"x-actor": seed.actor})
    assert r.status_code == 200
    assert r.json()["runs"][0] == {"mirror": "sku_catalog", "run_id": 1, "ok": True,
                                   "rows_in": 3, "rows_dropped": 0, "error": None}


def test_trigger_while_another_run_holds_the_lock_is_409(client, seed):
    with advisory_lock():
        r = client.post("/v1/jobs/refresh-dims", json={}, headers={"x-actor": seed.actor})
    assert r.status_code == 409 and r.json()["error"] == "refresh_in_flight"


def test_unknown_mirror_is_404_not_silently_all(client, seed):
    """★ 打错名字却照常刷了全部，比不让刷更坏。"""
    r = client.post("/v1/jobs/refresh-dims", json={"only": "nope"},
                    headers={"x-actor": seed.actor})
    assert r.status_code == 404 and r.json()["error"] == "unknown_mirror"


def test_undeclared_query_param_is_400(client, seed):
    r = client.post("/v1/jobs/refresh-dims?force=1", json={},
                    headers={"x-actor": seed.actor})
    assert r.status_code == 400 and r.json()["error"] == "unknown_query_param"
```

- [ ] **Step 2: 跑测试确认它红**

Run: `uv run pytest -q tests/test_jobs_scheduler.py tests/test_api_ops.py`
Expected: FAIL —— `ModuleNotFoundError: No module named 'jobs.scheduler'`

- [ ] **Step 3: 配置默认值**

`shared/config.py` 替换 `freshness()`：

```python
#: ★ 设计取值 · 未实测（设计 §10 OQ-6/7/10）。config.toml 缺键时用它们，
#:   但缺键与「明确写了这个值」在日志里要分得开 —— 见 jobs/scheduler.py 的启动行。
_FRESHNESS_DEFAULTS = {
    "max_age_hours": 24,
    "refresh_at": "06:30",              # ★ 07:481「在 CH 采集窗口之后」，具体时刻未实测
    "refresh_timezone": "Asia/Shanghai",
    "scheduler_enabled": True,
    "coverage_drop_threshold": 0.30,    # OQ-6 裁定 09-22
    "startup_gate": True,
}


def freshness() -> dict:
    return {**_FRESHNESS_DEFAULTS, **_cfg().get("freshness", {})}
```

`config.example.toml` 的 `[freshness]` 段替换为：

```toml
[freshness]
# ★ 维度镜像陈旧超过它 → 拒绝服务（E-4）。
# ★ 设计取值 · 未实测 —— 文档没给过阈值（OQ-7）。
max_age_hours = 24

# ★ 进程内 APScheduler 每日刷一次的时刻（负责人裁定 S-33）。
#   07:481「在 CH 采集窗口之后」——「之后」是文档原话，06:30 是设计取值 · 未实测（OQ-7）。
refresh_at       = "06:30"
refresh_timezone = "Asia/Shanghai"   # ★ 容器里的本地时区不可靠，必须显式写（OQ-10）

# ★ false = 不起调度器（测试、只跑 CLI 的部署）。关着时启动日志里有一条 WARNING。
scheduler_enabled = true

# ★ 本轮行数较上一次成功轮跌幅超过它 → 拒绝整批、保留旧镜像（07:485）。
#   设计取值 · 未实测（OQ-6 裁定 09-22 = 0.30；邻仓实测单日掉 40% 是坏批）。
coverage_drop_threshold = 0.30

# ★ 启动钩子只记录 + 触发刷新，不拦启动、不拦 /health · /v1/readiness；
#   真正的拒绝服务只在业务路由按请求判（OQ-8 裁定 09-22，见 03:1622）。
#   ⚠️ 本 Task 之下的 Task 7 仍按旧写法（抛异常拒绝启动）实现，执行前需按
#   本裁定重写 —— 见设计 §10.1 OQ-8 与 mirror-refresh 文档增量提案第 7 节。
#   测试夹具置 false —— 它 TRUNCATE 掉四张镜像。
startup_gate = true
```

- [ ] **Step 4: 写 `jobs/scheduler.py`**

```python
"""进程内定时（负责人裁定：APScheduler，不用外部 cron，不用 pg_cron）。

★ 频率与时刻的理由同 07:481：CH 延迟 ≥ 1 天，跑更密没有新数据，所以每日一次、
  落在采集窗口之后。
"""
from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from apscheduler.events import EVENT_JOB_ERROR
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from jobs.refresh_dims import refresh_all
from shared.config import freshness

log = logging.getLogger("scm.jobs")

REFRESH_JOB_ID = "refresh_dims"
#: 睡醒/重启后补跑一次（1 小时内），不补跑一串。
MISFIRE_GRACE_S = 3600

_SCHEDULER: AsyncIOScheduler | None = None


def _hour_minute(raw: str) -> tuple[int, int]:
    """★ 配置类错误往启动钩子放：写错了要现在炸，不是明天 06:30 悄悄不跑。"""
    try:
        h, m = raw.split(":")
        return int(h), int(m)
    except Exception as e:
        raise ValueError(f"[freshness] refresh_at 必须是 \"HH:MM\"，收到 {raw!r}") from e


def _on_error(event) -> None:
    # ★ APScheduler 默认把 job 异常吞进它自己的 logger —— 不挂这个监听器，
    #   刷新崩了在 scm.* 的日志里一个字都看不到。
    log.warning("op=scheduled_job job=%s outcome=fail", event.job_id,
                exc_info=event.exception)


def build_scheduler() -> AsyncIOScheduler | None:
    cfg = freshness()
    if not cfg.get("scheduler_enabled", True):
        log.warning("op=scheduler outcome=disabled scheduler_enabled=false"
                    " —— 镜像不会自动刷新，只能靠 CLI 或 /v1/jobs/refresh-dims")
        return None
    hour, minute = _hour_minute(str(cfg["refresh_at"]))
    tz = ZoneInfo(str(cfg["refresh_timezone"]))
    sch = AsyncIOScheduler(timezone=tz)
    sch.add_job(lambda: refresh_all("scheduler"), id=REFRESH_JOB_ID,
                trigger=CronTrigger(hour=hour, minute=minute, timezone=tz),
                coalesce=True, misfire_grace_time=MISFIRE_GRACE_S,
                max_instances=1, replace_existing=True)
    sch.add_listener(_on_error, EVENT_JOB_ERROR)
    log.info("op=scheduler outcome=built at=%02d:%02d tz=%s", hour, minute, tz)
    return sch


def start() -> None:
    global _SCHEDULER
    _SCHEDULER = build_scheduler()
    if _SCHEDULER is not None:
        _SCHEDULER.start()
        log.info("op=scheduler outcome=started jobs=%d", len(_SCHEDULER.get_jobs()))


def shutdown() -> None:
    global _SCHEDULER
    if _SCHEDULER is not None:
        _SCHEDULER.shutdown(wait=False)
        log.info("op=scheduler outcome=stopped")
        _SCHEDULER = None
```

- [ ] **Step 5: 写 `api/ui/ops.py`**

```python
"""运维触发：手动重跑镜像刷新（07:482「另留一个仅运维可用的手动触发」）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from api.ui.deps import actor, declared
from api.ui.errors import ApiError
from jobs import refresh_dims
from jobs.lock import RefreshInFlight

router = APIRouter()


class RefreshBody(BaseModel):
    only: str | None = None


@router.post("/jobs/refresh-dims")
def refresh_dims_now(request: Request, body: RefreshBody, who: str = Depends(actor)):
    declared(request)
    try:
        runs = refresh_dims.refresh_all("api", actor=who, only=body.only)
    except RefreshInFlight as e:
        raise ApiError(409, "refresh_in_flight", str(e), {}) from e
    except KeyError as e:
        raise ApiError(404, "unknown_mirror", "登记表里没有这个镜像",
                       {"only": body.only}) from e
    return {"runs": [r._asdict() for r in runs]}
```

★ 本端点**不挂** `require_fresh_mirrors` —— 镜像陈旧时它恰恰是用来修的那个口子，
挂上去就变成「陈旧 → 503 → 无法刷新 → 永远陈旧」。

- [ ] **Step 6: 接进 lifespan**

`api/__init__.py`：`from jobs import scheduler as job_scheduler`、`from api.ui import ops`；
`_lifespan` 改成

```python
@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI):
    job_scheduler.start()          # ★ 先起调度器：启动检查可能要立刻触发一轮刷新
    try:
        _log_startup()
        yield
    finally:
        job_scheduler.shutdown()
```

并在 `create_app()` 里加 `app.include_router(ops.router, prefix="/v1")`。

- [ ] **Step 7: 跑测试确认它绿**

Run: `uv run pytest -q tests/test_jobs_scheduler.py tests/test_api_ops.py tests/test_api_system.py`
Expected: PASS。★ 测试配置里 `scheduler_enabled` 要置 `false`
（在 `tests/conftest.py` 的 `_switch_schema` 旁边补一段改写 `[freshness]` 的逻辑），
否则每个 `client` 夹具都会起一个真调度器。

- [ ] **Step 8: Commit**

```bash
git add jobs/scheduler.py api/ui/ops.py api/__init__.py shared/config.py \
        config.example.toml tests/test_jobs_scheduler.py tests/test_api_ops.py tests/conftest.py
git commit -m "feat(jobs): APScheduler 进程内日调度 + 运维触发接口 + [freshness] 五个键"
```

---

### Task 7: 启动检查 —— 记录 + 触发刷新（OQ-8 裁定 09-22，不拦启动）

★ **本 Task 已按控制器 OQ-8 裁定重写**：启动检查**从不抛异常、从不拦 `lifespan`**，
`/health` 与 `/v1/readiness` 永远可达。它只做两件事——① 把每张镜像的新鲜度打进
启动日志；② 若有 `gate_503` 镜像陈旧且 `[freshness] startup_gate = true`，就通过
与调度器/CLI 共用的同一个刷新入口（`jobs.refresh_dims.refresh_all`，同一把 PG
advisory lock）触发**一次**立即刷新——不管这次刷新成不成功都不影响应用启动。
真正的拒绝服务只在业务路由按请求判（`api/ui/deps.py::require_fresh_mirrors`，
已经在，不用本 Task 改）。旧版「`MirrorStale` 拦 `lifespan`、抛异常拒绝启动」的
设计已作废（对应设计 §8 的旧文本、`03:1622` 的旧措辞）。

**Files:**
- Modify: `api/__init__.py`（`_log_startup` → `_startup_check`；顶部加
  `from jobs import refresh_dims`、`from dim.registry import gate_503_names`——
  若 Task 6 Step 6 尚未加）
- Test: `tests/test_api_startup_check.py`

**Interfaces:**
- Consumes: `dim.registry.gate_503_names()`（Task 1）· `jobs.refresh_dims.refresh_all`（Task 5）· `shared.config.freshness()["startup_gate"]`（Task 6）· `api.ui.deps.require_fresh_mirrors`（既有，业务路由的逐请求 503）
- Produces: `api._startup_check()`——★ 不再产出任何会中断 `lifespan` 的异常；
  没有 `MirrorStale` 类了

- [ ] **Step 1: 写失败的测试**

`tests/test_api_startup_check.py`：

```python
"""启动检查。★ OQ-8 裁定（控制器 09-22）：不拦启动、不拦 /health/readiness——
只记录 + 在需要时触发一次立即刷新；拒绝服务只在业务路由按请求判。"""
import logging

from starlette.testclient import TestClient

import api
from jobs import refresh_dims
from shared import config as config_module


def _cfg(monkeypatch, **kw):
    cfg = dict(config_module.freshness())
    cfg.update(scheduler_enabled=False, **kw)
    monkeypatch.setattr(config_module, "freshness", lambda: cfg)


def test_boot_with_all_mirrors_stale_still_serves_health_and_gates_business_per_request(
        wipe, monkeypatch):
    """★ 判据：/health 200、/v1/readiness 能看到陈旧、/v1/plans 503，
    且启动只触发一次刷新——不是零次（没做该做的事），也不是一次以上（重复刷）。"""
    _cfg(monkeypatch, startup_gate=True)
    calls = []
    monkeypatch.setattr(refresh_dims, "refresh_all",
                        lambda *a, **k: (calls.append((a, k)), [])[1])
    with TestClient(api.create_app()) as c:
        assert c.get("/health").status_code == 200
        r = c.get("/v1/readiness")
        assert r.status_code == 200
        never_refreshed = {m["mirror"] for m in r.json()["mirrors"] if m["refreshed_at"] is None}
        assert {"sku_catalog", "msku_bridge", "seller", "warehouse"} <= never_refreshed
        pr = c.get("/v1/plans")
        assert pr.status_code == 503 and pr.json()["error"] == "mirror_stale"
    assert len(calls) == 1, f"启动应恰好触发一次刷新，实际 {len(calls)} 次：{calls}"
    assert calls[0][0] == ("scheduler",), "启动触发的刷新必须走 trigger='scheduler' 那条口子"


def test_startup_gate_false_never_triggers_a_refresh(wipe, monkeypatch):
    """★ startup_gate 现在的语义是「是否在启动时触发刷新」，不是「是否拒绝启动」。"""
    _cfg(monkeypatch, startup_gate=False)
    calls = []
    monkeypatch.setattr(refresh_dims, "refresh_all",
                        lambda *a, **k: (calls.append((a, k)), [])[1])
    with TestClient(api.create_app()) as c:
        assert c.get("/health").status_code == 200
    assert calls == [], "startup_gate=false 时一次都不许触发"


def test_a_failing_startup_refresh_is_logged_with_cause_and_does_not_stop_the_app(
        wipe, monkeypatch, caplog):
    """★ 静默兜底是最坏的一种：刷新崩了要连原因一起留声，且绝不能让应用起不来。"""
    _cfg(monkeypatch, startup_gate=True)

    def _boom(*a, **k):
        raise RuntimeError("CH 连不上：模拟 UND_ERR_CONNECT_TIMEOUT")

    monkeypatch.setattr(refresh_dims, "refresh_all", _boom)
    with caplog.at_level(logging.WARNING, logger="scm.api"):
        with TestClient(api.create_app()) as c:
            assert c.get("/health").status_code == 200
    assert "CH 连不上" in caplog.text and "UND_ERR_CONNECT_TIMEOUT" in caplog.text


def test_check_only_looks_at_gate_503_mirrors(wipe, monkeypatch, seed):
    """label_only 的镜像（sku_category）陈旧不触发这条路径，只标注（01:275）。"""
    from dim.registry import gate_503_names
    assert "sku_category" not in gate_503_names()
```

- [ ] **Step 2: 跑测试确认它红**

Run: `uv run pytest -q tests/test_api_startup_check.py`
Expected: FAIL —— `_startup_check` 还不存在（当前是 `_log_startup`，从不调用
`refresh_dims.refresh_all`），第一条与第三条测试会因为 `calls`/`caplog` 断言落空而红。

- [ ] **Step 3: 改 `api/__init__.py`**

```python
def _stale_mirrors() -> list[dict]:
    """★ 打全部镜像的新鲜度日志（不管新不新鲜），只把陈旧的 gate_503 镜像挑进返回值。"""
    max_age = dt.timedelta(hours=float(freshness()["max_age_hours"]))
    now = dt.datetime.now(dt.UTC)
    gated = gate_503_names()
    out = []
    with timed("startup"), pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT mirror, refreshed_at FROM v_mirror_freshness ORDER BY mirror")
        for mirror, at in cur.fetchall():
            log.info("startup mirror=%s refreshed_at=%s", mirror, at)
            # ★ NULL 是「一行都没有」，不是「刚刷过」
            if mirror in gated and (at is None or now - at > max_age):
                out.append({"mirror": mirror,
                            "refreshed_at": at.isoformat() if at else None})
        cur.execute("SELECT count(*) FROM schema_migration")
        log.info("startup schema=%s migrations=%d", business_schema(), cur.fetchone()[0])
    return out


def _startup_check() -> None:
    """★ OQ-8 裁定（控制器 09-22）：不拦启动、不拦 /health · /v1/readiness——
    只记录 + 在需要时触发一次立即刷新。真正的拒绝服务只在业务路由按请求判
    （api/ui/deps.py::require_fresh_mirrors）。

    ★ 全程不许抛出会中断 lifespan 的异常：触发的这次刷新本身失败也只记日志——
    静默兜底是最坏的一种，但「刷新失败」和「应用起不来」是两件不同的事，
    把二者绑在一起就是把一次 CH 抖动变成一次人工到场（设计 §10.1 OQ-8）。
    """
    stale = _stale_mirrors()
    if not stale:
        return
    if not freshness()["startup_gate"]:
        log.warning("startup stale=%s startup_gate=false —— 只记录，不触发刷新；"
                    "业务端点仍会逐请求 503", [s["mirror"] for s in stale])
        return
    log.warning("startup stale=%s startup_gate=true —— 触发一次立即刷新",
               [s["mirror"] for s in stale])
    try:
        runs = refresh_dims.refresh_all("scheduler")
        log.info("startup refresh runs=%s", [(r.mirror, r.ok) for r in runs])
    except BaseException as e:
        log.warning("startup refresh failed err=%s", e)
```

`_lifespan` 里把 `_log_startup()` 换成 `_startup_check()`；顶部若 Task 6 Step 6
还没加，补上 `from jobs import refresh_dims`、`from dim.registry import gate_503_names`。
★ 不再有 `class MirrorStale`——它连同「抛异常拒绝启动」的设计一起被本次裁定作废。

- [ ] **Step 4: 跑测试确认它绿**

Run: `uv run pytest -q tests/test_api_startup_check.py tests/test_api_system.py`
Expected: PASS

- [ ] **Step 5: 跑全量，确认没有把别的端点拖下水**

Run: `uv run pytest -q`
Expected: 全绿。★ 与旧版不同，这里不需要测试配置把 `startup_gate` 特意置
`false` 才能让 `client` 夹具跑起来——`_startup_check` 从不拦 `lifespan`，
`startup_gate` 只决定测试夹具的 `wipe`（TRUNCATE 四张镜像后）要不要在每次
建应用时都空跑一次刷新触发；为了不让每个用例都去碰 PG advisory lock，
仍建议 Task 6 Step 7 的测试配置把 `startup_gate` 置 `false`。

- [ ] **Step 6: Commit**

```bash
git add api/__init__.py tests/test_api_startup_check.py
git commit -m "fix(api): 启动检查按 OQ-8 裁定改写——记录 + 触发刷新，不再拦 /health"
```

---

### Task 8: 文档增量与端到端证明

**Files:**
- Modify: `README.md`（「跑起来」与「测试」两节）
- Create: `docs/superpowers/specs/2026-09-22-mirror-refresh-doc-deltas.md` 已列的五处编辑，**由主控执行**，本任务只核对是否已落
- Test: `tests/test_stage_a_criteria.py`（追加一条端到端）

**Interfaces:**
- Consumes: 前七个 Task 的全部产物
- Produces: `tests/test_stage_a_criteria.py::test_mirror_refresh_end_to_end`

- [ ] **Step 1: 写端到端测试**

追加进 `tests/test_stage_a_criteria.py`：

```python
def test_mirror_refresh_end_to_end(wipe, seed):
    """CLI 刷一轮 → 镜像有数 → 留痕可查 → 陈旧闸放行。

    ★ 用注入的假 CH 行，不连内网 —— 连 CH 的那条在
      test_jobs_refresh_dims_live.py 里，CH 不可达时**吵着跳过**。
    """
    from jobs import refresh_dims as rd
    from shared.pg_client import pg_conn
    from tests.fixtures import ch_rows as R

    runs = rd.refresh_all("cli", actor=seed.actor, only="warehouse",
                          query=R.replay(R.WAREHOUSE))
    assert [r.ok for r in runs] == [True]
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT kind FROM warehouse ORDER BY wid")
        assert [r[0] for r in cur.fetchall()] == ["local", "local", "oversea_self",
                                                  "oversea_3pl"]   # seed 里已有 wid=1
        cur.execute("SELECT refreshed_at FROM v_mirror_freshness WHERE mirror = 'warehouse'")
        assert cur.fetchone()[0] is not None
        cur.execute("SELECT ok, drop_reasons FROM dim_refresh_run WHERE mirror = 'warehouse'")
        ok, reasons = cur.fetchone()
    assert ok and reasons["no_baseline"] == 1
```

- [ ] **Step 2: 写连活 CH 的冒烟测试**

`tests/test_jobs_refresh_dims_live.py`：

```python
"""连真 CH 的冒烟。★ 跳过必须吵 —— 静默跳过的测试与没写这个测试一样。"""
import pytest

from dim import ch_source as cs
from shared.ch_client import ch_client, ch_query, describe_failure


@pytest.fixture(scope="module")
def live_query():
    try:
        return ch_query(ch_client())
    except BaseException as e:
        pytest.skip(f"CH 连不上，跳过实盘冒烟：{describe_failure(e)}"
                    f" —— 这不是「通过」，是没测", allow_module_level=True)


@pytest.mark.parametrize("fetch", [cs.fetch_sku_catalog, cs.fetch_msku_bridge,
                                   cs.fetch_warehouse])
def test_real_ch_shape_matches_the_transform(live_query, fetch):
    got = fetch(live_query)
    assert got.rows, "取回 0 行"
    assert got.source_max_captured is not None
    print(f"\n{fetch.__name__}: {len(got.rows)} 行 / 丢 {got.dropped} "
          f"{got.drop_reasons} / captured={got.source_max_captured}")
```

★ `pytest.skip` 的理由里要带 `describe_failure` 的结果 —— 「跳过了」和
「因为代理劫持导致 503 而跳过」是两件事。

- [ ] **Step 3: 跑两条**

Run: `uv run pytest -q tests/test_stage_a_criteria.py::test_mirror_refresh_end_to_end`
Expected: PASS
Run: `uv run pytest -q tests/test_jobs_refresh_dims_live.py -s`
Expected: PASS（内网）或 SKIP **并在输出里看得见理由**

- [ ] **Step 4: 改 README**

「跑起来」一节末尾追加：

```
    uv run python -m jobs.probe_ch              # 探源表列名（只读，需内网）
    uv run python -m jobs.refresh_dims          # 手动刷一轮四张维度镜像
    uv run python -m jobs.refresh_dims --only warehouse

镜像刷新平时由进程内的 APScheduler 每日跑一次（`[freshness] refresh_at`，默认
06:30 Asia/Shanghai，落在 CH 采集窗口之后）。三个入口——调度、上面那条 CLI、
`POST /v1/jobs/refresh-dims`——共用一把 PG advisory lock，第二个来的会拿到
409 / 退出码 3，不排队也不假装成功。

刷新失败或掉档超 `coverage_drop_threshold` 时**整批拒绝**、旧镜像原封不动，
`dim_refresh_run` 里会有一行 `ok=false` 说明是哪一步、丢了多少行、为什么丢。

启动只做记录 + 触发：`[freshness] startup_gate = true` 时，若有维度镜像仍陈旧，
启动检查会记一条日志并触发一次立即刷新，**不会**阻断进程启动——`/health` 与
`/v1/readiness` 永远可达（OQ-8 裁定 09-22）。`startup_gate = false` 时只记录，
不触发刷新。不管哪种配置，业务端点的拒绝服务都只发生在按请求判的
`require_fresh_mirrors`（`docs/03` §7.1 E-4）。
```

「离线可跑的那部分」的命令追加 `tests/test_mirror_registry.py tests/test_dim_ch_source.py tests/test_ch_client.py`。

- [ ] **Step 5: 核对文档增量是否已落**

对照 `docs/superpowers/specs/2026-09-22-mirror-refresh-doc-deltas.md` 逐条确认
`00` / `01` / `00e` / `03` / `config.example.toml` 五处已由主控改完。
★ 没落的**不要自己改** —— 裁定层的文档不是实施者能动的（`CLAUDE.md` 纪律 ③）。

- [ ] **Step 6: 全量回归**

Run: `uv run pytest -q`
Expected: 全绿，含 `seller`（OQ-3 已由控制器 09-22 裁定为派生列，`fetch_seller`
不再抛异常，四张阶段 A 镜像在 CLI 与调度里都应以 `ok=true` 出现）。

- [ ] **Step 7: Commit**

```bash
git add README.md tests/test_stage_a_criteria.py tests/test_jobs_refresh_dims_live.py
git commit -m "docs+test: 镜像刷新端到端证明 + 活 CH 冒烟（跳过必须吵）+ README 运行说明"
```

---

## Self-Review

**1. Spec coverage**

| 设计节 | 落在 | |
|---|---|---|
| §3 登记表 | Task 1 | ✅ |
| §4 门禁 (a)(b) | Task 1 / Task 2 | ✅ |
| §5 调度（APScheduler · 三入口 · 互斥） | Task 5（锁 + CLI）· Task 6（调度 + 接口） | ✅ |
| §6 留痕与覆盖面（006 · drop_reasons · 首轮无基线 · 只 upsert） | Task 2 / Task 5 | ✅ |
| §7 四张 SQL | Task 3（探针定稿）· Task 4（变换） | ✅ |
| §7.1 取数分层（F-9） | Global Constraints + Task 3/4/5 的文件划分 | ✅ |
| §8 启动检查 | Task 7 | ✅ |
| §9 阶段 B/C/D 加条目 | Task 1 的 `pending` 字段 + 门禁 (a)；**无代码任务**（本阶段不实现） | ✅ 刻意 |
| §10 开放问题 | 控制器 09-22 已裁定 OQ-2~OQ-10（见设计 §10.1）；OQ-1 保持开放，待 Task 3 Step 7 探针跑通才算解决 | ✅ 除 OQ-1 外 |

**Gap（已由控制器 09-22 裁定关闭，第一批）**：OQ-3（`seller.has_fba` 无源）曾落
到「刻意抛」，本轮已按裁定改写 `SQL_SELLER` + `fetch_seller`（Task 4 Step 3）：
`has_fba` 派生自 `lingxing_product_listing.fulfillment_channel_type='FBA'`，
`seller` 不再天然失败。Task 4 的混合失败测试、Task 8 Step 6 的「seller 会以
`ok=false` 出现」两处说明已同步删除或改写。

**Gap（已由控制器 09-22 裁定关闭，第二批）**：OQ-8 裁定 `startup_gate`
**不拦启动、不拦 `/health`/`/v1/readiness`**，只记录 + 在需要时触发一次立即
刷新，真正的拒绝服务只在业务路由按请求判。Task 7 与设计 §8 已同批重写：
`_startup_check()` 不再抛 `MirrorStale`、不再拦 `lifespan`，改成
「记录全部镜像新鲜度 → 若 `gate_503` 陈旧且 `startup_gate=true` 就触发一次
`refresh_dims.refresh_all("scheduler")`（同一把 advisory lock）→ 无论成败都
`yield`」；`/v1/plans` 等业务路由的 503 仍由既有的 `require_fresh_mirrors`
逐请求承担，不受影响。测试文件同步改名 `tests/test_api_startup_check.py`。

**Gap（preflight 09-22 扫描发现，已裁定并改完，第三批）**：

1. **Task 5 `refresh_one` 撞 006 的只追加触发器**（preflight 头号缺陷）：原写法
   先 `INSERT` 占位、成败后再 `UPDATE ... WHERE run_id`，而 006 挂着
   `BEFORE UPDATE OR DELETE` 触发器——第一次调用就会被
   `forbid_update_delete()` 拦成 `psycopg2.errors.RaiseException`，Step 1 自己
   的测试全过不了。已改成 `started_at` 在 Python 侧先取时间戳、整轮跑完只写
   **一条终态 INSERT**（成功/失败都在这条里落齐 `finished_at/ok/error/rows_in/
   rows_dropped/source_max_captured`），不再有任何 `UPDATE`。`_check_coverage`
   原本两个循环各扫一遍 `m.coverage`，顺带合并成一趟。
2. **Task 8 README 文案未跟 OQ-8 重写同步**：仍写着「进程起不来」「带病起来要
   显式置 false」，与 Task 7/设计 §8 的新行为（只记录 + 触发一次刷新，永不阻断）
   矛盾。已改写为「记录 + 触发，不阻断；`startup_gate=false` 只记录」。
3. **`03:51` 引用数字过期**：`docs/03-数据库表清单.md:51` 因本轮新增
   `dim_refresh_run`（S-33）已经是「43 张」，而计划 `test_doc_table_parser_actually_sees_the_table`
   的文档字符串与门槛断言、设计 §4 的同一处引用都还写着「42 张」/`>= 42`。
   已同步改成 43。
4. **门禁 (b) `callable(m.fetch)` 的预期红绿排期未钉死**：原文本同时给出「留红到
   Task 4」与「挪到 Task 4 再加」两条互斥路径，容易被执行者顺手选错、还看不出
   跟 Task 5 的问题会叠加。已裁定为唯一路径：断言从 Task 2 起就在测试文件里，
   Task 2/3 期间预期红，Task 4 Step 4 接上 `fetch=` 后才转绿——Task 2 Step 2、
   Task 3 Step 7 之后、Task 4 Step 5 三处都显式写明了这一排期，不许中途 skip。
5. **设计 §7 `SQL_MSKU_BRIDGE` 与计划实现不一致**：设计里这条 SQL 带
   `HAVING sku != ''`，计划 Task 4 的同名 SQL 没有——未绑货号的丢弃改在 Python
   侧的 `fetch_msku_bridge` 里做，因为要计进 `drop_reasons.unbound_sku`（丢的
   一侧必须统计，铁律）。已让设计 §7 去掉 `HAVING`，改成与计划一致的说明。

**2. Placeholder scan**：无 TBD / 「适当处理错误」/「照 Task N 写」。每个代码步都有可运行的代码块。
Task 5 `refresh_one` 原有一处「先 INSERT 占位再 UPDATE / 一次性 INSERT」的二选一，
preflight 09-22 发现它写成了两条 `UPDATE`，与 006 的只追加触发器自相矛盾——已裁定
并改完为「只有一条终态 INSERT」，不再是待选项（见下方 Gap 第三批）。

**3. Type consistency**

- `Fetched(rows, dropped, drop_reasons, source_max_captured)` 在 Task 4 定义，Task 5 `refresh_one` 按同名字段用。✅
- `RunRow(mirror, run_id, ok, rows_in, rows_dropped, error)` 在 Task 5 定义，Task 6 的
  `_asdict()` 与 Task 8 的 `[r.ok for r in runs]` 一致。✅
- `Mirror.columns` 在 Task 1 定义、Task 4 的 `test_row_width_matches_the_registry` 校验、
  Task 5 的 `_upsert` 与 `_check_coverage` 的 `m.columns.index(col)` 消费。✅
- `gate_503_names()` 三处用法一致（Task 1 定义 · Task 2 门禁 (b) · Task 7 启动检查）。✅
- `advisory_lock()` / `RefreshInFlight` 在 Task 5 定义，Task 6 的接口与测试按同名 import。✅
- ⚠️ Task 5 的 `refresh_all` 签名里 `query=None` 是**为测试注入留的口子**。生产路径
  （CLI / 调度 / 接口）都不传它 —— Task 6 与 Task 7 的调用全部只给 `trigger`/`actor`/`only`。
