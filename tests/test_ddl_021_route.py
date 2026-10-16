"""021 · 排货表按去向分表（spec 2026-10-12-dispatch-route-split §3 R1~R5 · CA-D13）。"""
from shared.pg_client import pg_conn
from tests.test_ddl_016_dispatch import (  # noqa: F401
    OVERSEA, arrange, constraint_of, new_sheet, run, sheet_event, world,
)


def test_new_sheet_needs_a_route(world, seed):  # noqa: F811
    assert constraint_of(run(lambda cur: new_sheet(cur, seed.actor, route=None))) == "sheet_needs_route"


def test_route_is_fixed_after_birth(world, seed):  # noqa: F811
    with pg_conn() as c, c.cursor() as cur:
        sid = new_sheet(cur, seed.actor, route="fba")
    assert constraint_of(run(lambda cur: cur.execute("UPDATE sheet SET route='oversea' WHERE sheet_id=%s", (sid,)))) \
        == "sheet_route_fixed"


def test_cells_must_match_the_sheet_route(world, seed):  # noqa: F811
    with pg_conn() as c, c.cursor() as cur:
        fba = new_sheet(cur, seed.actor, route="fba")
        os_ = new_sheet(cur, seed.actor, route="oversea")
    assert constraint_of(run(lambda cur: arrange(cur, seed, fba, world, 1, channel="oversea", dest_wid=OVERSEA))) \
        == "arrangement_route_mismatch"
    assert constraint_of(run(lambda cur: arrange(cur, seed, os_, world, 1))) == "arrangement_route_mismatch"
    with pg_conn() as c, c.cursor() as cur:
        arrange(cur, seed, fba, world, 1)
        arrange(cur, seed, os_, world, 1, channel="oversea", dest_wid=OVERSEA)


def test_legacy_sheet_without_route_cannot_take_cells_but_can_withdraw_and_cancel(world, seed):  # noqa: F811
    with pg_conn() as c, c.cursor() as cur:
        sid = new_sheet(cur, seed.actor, route="fba")
        aid = arrange(cur, seed, sid, world, 2)
        cur.execute("ALTER TABLE sheet DISABLE TRIGGER t_sheet_guard")    # 模拟 021 回填出的旧版混合表
        cur.execute("UPDATE sheet SET route = NULL WHERE sheet_id=%s", (sid,))
        cur.execute("ALTER TABLE sheet ENABLE TRIGGER t_sheet_guard")
    assert constraint_of(run(lambda cur: arrange(cur, seed, sid, world, 1, msku=seed.msku_b))) == "sheet_route_missing"
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("DELETE FROM dispatch_arrangement WHERE arrangement_id=%s", (aid,))
        sheet_event(cur, sid, "草稿", "已取消", seed.actor, reason="t")


def test_one_draft_per_plan_and_route(world, seed):  # noqa: F811
    with pg_conn() as c, c.cursor() as cur:
        a = new_sheet(cur, seed.actor, route="fba")
        new_sheet(cur, seed.actor, route="oversea")                       # 不同去向并存
    assert constraint_of(run(lambda cur: new_sheet(cur, seed.actor, route="fba"))) == "sheet_one_draft_per_plan_route"
    with pg_conn() as c, c.cursor() as cur:
        sheet_event(cur, a, "草稿", "已取消", seed.actor, reason="t")
        new_sheet(cur, seed.actor, route="fba")                           # 不是草稿就不占名额
