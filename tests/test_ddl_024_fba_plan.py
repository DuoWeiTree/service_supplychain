"""024 · FBA 发货计划（CC-D1/D3）：ext_ref kind fba_plan · 计划号 · 锁 / 释放事件只追加 · 已投送前置「全部已锁」。"""
import psycopg2
import pytest

from shared.pg_client import pg_conn
from tests.test_ddl_016_dispatch import confirmed_sheet, constraint_of, run, world  # noqa: F401


@pytest.fixture
def fba(world, seed):  # noqa: F811
    with pg_conn() as c, c.cursor() as cur:
        sid, _, cid = confirmed_sheet(cur, seed, world)
        cur.execute("INSERT INTO ext_ref (consignment_id, from_wid, kind, idem_key, ext_no, found_by, actor)"
                    " VALUES (%s,%s,'fba_plan',%s,'RP1','create',%s) RETURNING ext_ref_id",
                    (cid, seed.wid, f"SCM-FBA-{cid}-{seed.wid}", seed.actor))
        e = cur.fetchone()[0]
        cur.execute("INSERT INTO fba_plan_order (ext_ref_id, order_sns) VALUES (%s, ARRAY['R1'])", (e,))
    return cid, e


def stock_event(cur, e, action, actor):
    cur.execute("INSERT INTO fba_plan_stock (ext_ref_id, action, actor) VALUES (%s,%s,%s)", (e, action, actor))


def dispatch(cur, cid, actor):
    cur.execute("INSERT INTO consignment_event (consignment_id, from_state, to_state, actor, src)"
                " VALUES (%s,'已确认','已投送',%s,'test')", (cid, actor))


def test_dispatched_needs_every_plan_locked(fba, seed):
    cid, e = fba
    assert constraint_of(run(lambda cur: dispatch(cur, cid, seed.actor))) == "consignment_dispatch_needs_lock"
    with pg_conn() as c, c.cursor() as cur:
        stock_event(cur, e, "lock", seed.actor)
        stock_event(cur, e, "unlock", seed.actor)
    assert constraint_of(run(lambda cur: dispatch(cur, cid, seed.actor))) == "consignment_dispatch_needs_lock"
    with pg_conn() as c, c.cursor() as cur:
        stock_event(cur, e, "lock", seed.actor)
        dispatch(cur, cid, seed.actor)
        cur.execute("SELECT locked FROM v_fba_plan_lock WHERE ext_ref_id=%s", (e,))
        assert cur.fetchone()[0] is True


def test_plan_orders_and_stock_events_are_append_only(fba, seed):
    _, e = fba
    with pg_conn() as c, c.cursor() as cur:
        stock_event(cur, e, "lock", seed.actor)
    for sql in ("UPDATE fba_plan_order SET order_sns='{}'", "DELETE FROM fba_plan_order",
                "UPDATE fba_plan_stock SET action='unlock'", "DELETE FROM fba_plan_stock"):
        with pytest.raises(psycopg2.Error), pg_conn() as c, c.cursor() as cur:
            cur.execute(sql)
