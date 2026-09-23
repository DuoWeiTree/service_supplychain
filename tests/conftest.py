"""测试夹具。照邻居 service_social/tests/conftest.py 的形状。

★ 测试 schema 是 scm_test。禁止 scm（本服务生产）与 inv（同库另一个在跑的系统）——
  往它们任何一个里写一行都是事故，而这类事故没有任何回声。
"""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONFIG_TOML = ROOT / "config.toml"

TEST_SCHEMA = "scm_test"
FORBIDDEN_SCHEMAS = {"scm", "inv"}

#: 每个测试之间要清空的表。★ plan_line_transition 不在其中 ——
#: 它是白名单（迁移灌的数据），清掉它状态机就没了。
DATA_TABLES = (
    "dim_refresh_run",
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
    """★ TRUNCATE 不触发 BEFORE DELETE 触发器，所以只追加表也清得掉。

    ★ DATA_TABLES 是阶段 A 终态的全表单，各表随 Task 3~5 的迁移逐个落地 ——
      过滤到「当前 schema 里已存在」的那些，否则在只应用了部分迁移的阶段，
      TRUNCATE 会先炸在还没创建的表上，而不是炸在测试真正要验的地方。
    """
    assert_not_production_schema()
    from migrations.pg.apply import applied_versions
    from shared.pg_client import pg_conn
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()")
        existing = {r[0] for r in cur.fetchall()}
        # ★ 过滤本身会哑掉「表名拼错/改名」这类真事故：一旦阶段 A 最后一个迁移
        #   （004，落地全部表）已应用，DATA_TABLES 就该与 existing 相等 ——
        #   不相等就点名硬失败，不许再靠「还没到它的迁移」这个借口继续漏下去。
        if "004_plan_overall_state.sql" in applied_versions(business_db):
            missing = set(DATA_TABLES) - existing
            assert not missing, f"DATA_TABLES 里这些表不存在：{sorted(missing)}"
        tables = [t for t in DATA_TABLES if t in existing]
        if tables:
            cur.execute(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE")
    return business_db


@pytest.fixture
def seed(wipe):
    """维度与操作人的最小一组。

    ★ 刻意包含两种「长得像 0 其实不是」的形态：
      · actor `bob` 停用 —— 与「不存在」分得开
      · seller `WM-1` has_fba=false —— 它的 FBA 在仓是「不适用」，不是 0（02 §3.1a）

    ★ Task 10 补：`warehouse` 是 `v_mirror_freshness` 的四个镜像之一，
      之前这里没种它，第一个挂 `require_fresh_mirrors` 的路由（`/v1/plans`）
      上线前没人发现 —— `/v1/readiness` 只报告新鲜度、不拒绝服务，盖不住这个缺口。
      不种它，任何挂了这个依赖的端点在 `seed` 下永远 503。
    """
    from shared.pg_client import pg_conn
    ns = SimpleNamespace(
        actor="alice", actor_inactive="bob",
        seller_a="11072", seller_b="11094", seller_nofba="90001",
        sku_a="DCC1800264G1", sku_b="A4P-TOY-002",
        msku_a=("MSKU-A", "11072"), msku_b=("MSKU-B", "11072"),
        msku_c=("MSKU-C", "11094"),          # ★ 与 msku_a 同货号、不同店 → 在途无归属那一形态
        msku_d=("MSKU-D", "11072"),          # ★ sku_b 在有 FBA 的店 → closing 有真数那一形态
        msku_nofba=("MSKU-W", "90001"),      # ★ 无 FBA → 不适用那一形态
        wid=1,
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
                         (*ns.msku_c, ns.sku_a), (*ns.msku_d, ns.sku_b),
                         (*ns.msku_nofba, ns.sku_b)])
        cur.execute(
            "INSERT INTO warehouse (wid, name, kind, market, refreshed_at)"
            " VALUES (%s, %s, %s, %s, now())", (ns.wid, "测试仓", "local", "US"))
    return ns


@pytest.fixture
def client(wipe):
    from starlette.testclient import TestClient

    from api import create_app
    return TestClient(create_app(), raise_server_exceptions=False)
