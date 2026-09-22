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
