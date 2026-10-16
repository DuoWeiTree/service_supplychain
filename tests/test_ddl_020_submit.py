"""020 · C-b 提交领星：外部单引用 · 作废 · 待核实 · 物流方式 · 投送 / 取消的库层前置（spec 2026-10-12 §4）。"""
import psycopg2
import pytest

from shared.pg_client import pg_conn
from tests.test_ddl_016_dispatch import (  # noqa: F401
    OVERSEA, arrange, constraint_of, derive_one, new_sheet, run, sheet_event, world,
)

WID2 = 3          # 第二个本地仓


@pytest.fixture
def confirmed(world, seed):  # noqa: F811
    """一张已确认的表：一张海外仓排货单，两个来源仓（seed.wid 与 WID2）。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO warehouse (wid, name, kind, market, refreshed_at) VALUES (%s,'二号仓','local',NULL,now())",
                    (WID2,))
        cur.execute("INSERT INTO logistics_channel (logistics_id, channel_name, enabled, refreshed_at)"
                    " VALUES (28762, '美森快船', true, now())")
        sid = new_sheet(cur, seed.actor, route="oversea")
        cid = derive_one(cur, seed, sid, arrange(cur, seed, sid, world, 5, channel="oversea", dest_wid=OVERSEA))
        derive_one(cur, seed, sid, arrange(cur, seed, sid, world, 3, channel="oversea", dest_wid=OVERSEA, from_wid=WID2))
        cur.execute("INSERT INTO consignment_logistics (sheet_id, dest_wid, logistics_id, updated_by) VALUES (%s,%s,28762,%s)",
                    (sid, OVERSEA, seed.actor))
        sheet_event(cur, sid, "草稿", "已确认", seed.actor)
    return sid, cid


def ext_ref(cur, cid, from_wid, no, actor):
    cur.execute("INSERT INTO ext_ref (consignment_id, from_wid, kind, idem_key, ext_no, found_by, actor)"
                " VALUES (%s,%s,'ows',%s,%s,'create',%s) RETURNING ext_ref_id",
                (cid, from_wid, f"SCM-CN-{cid}-{from_wid}", no, actor))
    return cur.fetchone()[0]


def to_dispatched(cur, cid, actor):
    cur.execute("INSERT INTO consignment_event (consignment_id, from_state, to_state, actor, src)"
                " VALUES (%s,'已确认','已投送',%s,'test')", (cid, actor))


def stage(cid):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT our_stage FROM consignment WHERE consignment_id=%s", (cid,))
        return cur.fetchone()[0]


def test_dispatched_needs_a_live_ext_ref_for_every_from_warehouse(confirmed, seed):
    _, cid = confirmed

    def half(cur):
        ext_ref(cur, cid, seed.wid, "OWS1", seed.actor)
        to_dispatched(cur, cid, seed.actor)
    assert constraint_of(run(half)) == "consignment_dispatch_incomplete"
    with pg_conn() as c, c.cursor() as cur:
        ext_ref(cur, cid, seed.wid, "OWS1", seed.actor)
        ext_ref(cur, cid, WID2, "OWS2", seed.actor)
        to_dispatched(cur, cid, seed.actor)
    assert stage(cid) == "已投送"


def test_cancel_needs_every_ext_ref_voided_then_cascades_from_dispatched(confirmed, seed):
    sid, cid = confirmed
    with pg_conn() as c, c.cursor() as cur:
        e1 = ext_ref(cur, cid, seed.wid, "OWS1", seed.actor)
        e2 = ext_ref(cur, cid, WID2, "OWS2", seed.actor)
        to_dispatched(cur, cid, seed.actor)
    assert constraint_of(run(lambda cur: sheet_event(cur, sid, "已确认", "已取消", seed.actor, reason="t"))) \
        == "consignment_cancel_needs_void"
    with pg_conn() as c, c.cursor() as cur:
        for e in (e1, e2):
            cur.execute("INSERT INTO ext_ref_void (ext_ref_id, actor, reason) VALUES (%s,%s,'t')", (e, seed.actor))
        sheet_event(cur, sid, "已确认", "已取消", seed.actor, reason="t")
    assert stage(cid) == "已取消"


def test_partially_submitted_consignment_also_needs_void_before_cancel(confirmed, seed):
    sid, cid = confirmed
    with pg_conn() as c, c.cursor() as cur:
        ext_ref(cur, cid, seed.wid, "OWS1", seed.actor)          # 已确认、只建了一张
    assert constraint_of(run(lambda cur: sheet_event(cur, sid, "已确认", "已取消", seed.actor, reason="t"))) \
        == "consignment_cancel_needs_void"


def test_ext_refs_voids_and_attention_are_append_only(confirmed, seed):
    _, cid = confirmed
    with pg_conn() as c, c.cursor() as cur:
        e = ext_ref(cur, cid, seed.wid, "OWS1", seed.actor)
        cur.execute("INSERT INTO ext_ref_void (ext_ref_id, actor, reason) VALUES (%s,%s,'t')", (e, seed.actor))
        cur.execute("INSERT INTO dispatch_attention (consignment_id, from_wid, idem_key, detail)"
                    " VALUES (%s,%s,'k','{}') RETURNING attention_id", (cid, WID2))
        a = cur.fetchone()[0]
        cur.execute("INSERT INTO dispatch_attention_close (attention_id, how, actor) VALUES (%s,'recheck',%s)", (a, seed.actor))
    for sql, p in [("UPDATE ext_ref SET ext_no='X' WHERE ext_ref_id=%s", (e,)), ("DELETE FROM ext_ref WHERE ext_ref_id=%s", (e,)),
                   ("DELETE FROM ext_ref_void WHERE ext_ref_id=%s", (e,)),
                   ("UPDATE dispatch_attention SET idem_key='x' WHERE attention_id=%s", (a,)),
                   ("DELETE FROM dispatch_attention_close WHERE attention_id=%s", (a,))]:
        with pytest.raises(psycopg2.Error), pg_conn() as c, c.cursor() as cur:
            cur.execute(sql, p)


def test_same_idem_key_twice_is_refused(confirmed, seed):
    _, cid = confirmed

    def twice(cur):
        ext_ref(cur, cid, seed.wid, "OWS1", seed.actor)
        ext_ref(cur, cid, seed.wid, "OWS9", seed.actor)
    assert constraint_of(run(twice)) == "ext_ref_idem_key_key"


def test_logistics_choice_freezes_with_the_sheet(confirmed, seed):
    sid, _ = confirmed
    assert constraint_of(run(lambda cur: cur.execute(
        "UPDATE consignment_logistics SET logistics_id=28762 WHERE sheet_id=%s", (sid,)))) == "sheet_frozen"


def test_one_write_in_flight_per_consignment(confirmed, seed):
    _, cid = confirmed

    def two(cur):
        for _ in range(2):
            cur.execute("INSERT INTO erp_call (endpoint, side_effect, consignment_id, actor, request)"
                        " VALUES ('create_ows_order','reversible',%s,%s,'{}')", (cid, seed.actor))
    assert constraint_of(run(two)) == "erp_call_one_write_in_flight_per_consignment"
