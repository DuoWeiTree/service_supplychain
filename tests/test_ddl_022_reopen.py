"""022 · 退回修改：已确认 → 草稿，只要这张表从没发到领星（CA-D14 · 负责人 10-12）。"""
from shared.pg_client import pg_conn
from tests.test_ddl_016_dispatch import OVERSEA, constraint_of, run, sheet_event, world  # noqa: F401
from tests.test_ddl_020_submit import WID2, confirmed, ext_ref, stage  # noqa: F401


def state(sid):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT state, watermark_at_confirm FROM sheet WHERE sheet_id=%s", (sid,))
        return cur.fetchone()


def test_reopen_brings_sheet_and_consignments_back_to_draft_and_editable(confirmed, seed):  # noqa: F811
    sid, cid = confirmed
    with pg_conn() as c, c.cursor() as cur:
        sheet_event(cur, sid, "已确认", "草稿", seed.actor, reason="选错仓库")
    assert state(sid) == ("草稿", None) and stage(cid) == "草稿"
    with pg_conn() as c, c.cursor() as cur:                            # 解冻：物流方式又能改
        cur.execute("UPDATE consignment_logistics SET logistics_id=28762 WHERE sheet_id=%s", (sid,))
        sheet_event(cur, sid, "草稿", "已确认", seed.actor)               # 能再确认
    assert state(sid)[0] == "已确认" and stage(cid) == "已确认"


def test_reopen_needs_a_reason(confirmed, seed):  # noqa: F811
    sid, _ = confirmed
    assert constraint_of(run(lambda cur: sheet_event(cur, sid, "已确认", "草稿", seed.actor))) == "sheet_reopen_needs_reason"


def test_reopen_refused_once_anything_reached_lingxing(confirmed, seed):  # noqa: F811
    sid, cid = confirmed
    with pg_conn() as c, c.cursor() as cur:
        e = ext_ref(cur, cid, seed.wid, "OWS1", seed.actor)
        cur.execute("INSERT INTO ext_ref_void (ext_ref_id, actor, reason) VALUES (%s,%s,'t')", (e, seed.actor))
    # ★ 删掉了也算发过（CA-D14：发到领星就锁死，只能取消）
    assert constraint_of(run(lambda cur: sheet_event(cur, sid, "已确认", "草稿", seed.actor, reason="x"))) \
        == "sheet_reopen_after_submit"


def test_reopen_refused_while_a_submission_is_unresolved(confirmed, seed):  # noqa: F811
    sid, cid = confirmed
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO dispatch_attention (consignment_id, from_wid, idem_key, detail) VALUES (%s,%s,'k','{}')",
                    (cid, seed.wid))
    assert constraint_of(run(lambda cur: sheet_event(cur, sid, "已确认", "草稿", seed.actor, reason="x"))) \
        == "sheet_reopen_after_submit"


def test_reopen_refused_while_a_write_call_is_in_flight(confirmed, seed):  # noqa: F811
    sid, cid = confirmed
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO erp_call (endpoint, side_effect, consignment_id, actor, request)"
                    " VALUES ('create_ows_order','reversible',%s,%s,'{}')", (cid, seed.actor))
    assert constraint_of(run(lambda cur: sheet_event(cur, sid, "已确认", "草稿", seed.actor, reason="x"))) \
        == "sheet_reopen_after_submit"


def test_consignment_cannot_go_back_on_its_own(confirmed, seed):  # noqa: F811
    _, cid = confirmed
    assert constraint_of(run(lambda cur: cur.execute(
        "INSERT INTO consignment_event (consignment_id, from_state, to_state, actor, src)"
        " VALUES (%s,'已确认','草稿',%s,'test')", (cid, seed.actor)))) == "consignment_back_follows_sheet"
