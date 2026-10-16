"""023 · 领星备货单回执：只追加的观察与轮次 · 当前进度视图 · 归档前置（spec 2026-10-13 §3 / §4）。"""
import psycopg2
import pytest

from shared.pg_client import pg_conn
from tests.test_ddl_016_dispatch import OVERSEA, constraint_of, run, world  # noqa: F401
from tests.test_ddl_020_submit import WID2, confirmed, ext_ref, stage, to_dispatched  # noqa: F401


def new_run(cur, trigger="scheduler", actor=None):
    cur.execute("INSERT INTO receipt_run (trigger, actor) VALUES (%s,%s) RETURNING run_id", (trigger, actor))
    return cur.fetchone()[0]


def observe(cur, run_id, ext_ref_id, st, rank, at):
    cur.execute("INSERT INTO ext_stage_obs (ext_ref_id, run_id, observed_at, status, stage, stage_rank)"
                " VALUES (%s,%s,%s,'x',%s,%s)", (ext_ref_id, run_id, at, st, rank))


@pytest.fixture
def dispatched(confirmed, seed):  # noqa: F811
    _, cid = confirmed
    with pg_conn() as c, c.cursor() as cur:
        e1 = ext_ref(cur, cid, seed.wid, "OWS1", seed.actor)
        e2 = ext_ref(cur, cid, WID2, "OWS2", seed.actor)
        to_dispatched(cur, cid, seed.actor)
    return cid, e1, e2


def current(e):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT stage, stage_rank FROM v_ext_stage WHERE ext_ref_id=%s", (e,))
        return cur.fetchone()


def archive(cur, cid, actor):
    cur.execute("INSERT INTO consignment_event (consignment_id, from_state, to_state, actor, src)"
                " VALUES (%s,'已投送','已归档',%s,'test')", (cid, actor))


def test_current_stage_never_goes_backwards_and_bypass_wins(dispatched, seed):
    _, e1, e2 = dispatched
    with pg_conn() as c, c.cursor() as cur:
        r = new_run(cur)
        observe(cur, r, e1, "已完成", 5, "2026-10-13 01:00+08")
        observe(cur, r, e1, "待收货", 4, "2026-10-13 02:00+08")          # 读回倒退
        observe(cur, r, e2, "待发货", 3, "2026-10-13 01:00+08")
        observe(cur, r, e2, "已撤销", -1, "2026-10-13 02:00+08")
    assert current(e1) == ("已完成", 5) and current(e2) == ("已撤销", -1)


def test_archive_needs_every_live_order_complete(dispatched, seed):
    cid, e1, e2 = dispatched
    with pg_conn() as c, c.cursor() as cur:
        r = new_run(cur)
        observe(cur, r, e1, "已完成", 5, "2026-10-13 01:00+08")
        observe(cur, r, e2, "待收货", 4, "2026-10-13 01:00+08")
    assert constraint_of(run(lambda cur: archive(cur, cid, seed.actor))) == "consignment_archive_needs_complete"
    with pg_conn() as c, c.cursor() as cur:
        observe(cur, new_run(cur), e2, "已完成", 5, "2026-10-13 03:00+08")
        archive(cur, cid, seed.actor)
    assert stage(cid) == "已归档"


def test_observations_and_runs_are_append_only(dispatched, seed):
    _, e1, _ = dispatched
    with pg_conn() as c, c.cursor() as cur:
        r = new_run(cur, "manual", seed.actor)
        observe(cur, r, e1, "待发货", 3, "2026-10-13 01:00+08")
        cur.execute("INSERT INTO receipt_run_result (run_id, refs, ok, failed, skipped, failures)"
                    " VALUES (%s,1,1,0,0,'[]')", (r,))
    for sql in ("UPDATE ext_stage_obs SET stage='x'", "DELETE FROM ext_stage_obs", "UPDATE receipt_run SET trigger='manual'",
                "DELETE FROM receipt_run_result", "UPDATE receipt_run_result SET ok=0"):
        with pytest.raises(psycopg2.Error), pg_conn() as c, c.cursor() as cur:
            cur.execute(sql)


def test_manual_run_needs_an_actor_scheduled_does_not(dispatched, seed):
    assert constraint_of(run(lambda cur: new_run(cur, "manual", None))) == "receipt_run_manual_needs_actor"
