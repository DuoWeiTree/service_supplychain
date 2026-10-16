"""016 · 排货域表与状态机（spec 2026-10-10 §4 · `04` §4.2 / §7.6b · CA-D2/D3/D5/D6/D7）。"""
import json
import re
from pathlib import Path

import psycopg2
import pytest

from shared.pg_client import pg_conn
from tests.helpers import advance, mint

ROOT = Path(__file__).resolve().parents[1]
OVERSEA = 2


@pytest.fixture
def world(seed):
    """一条 已下单 的记录（店 seller_a 在 demand_by_seller 里）+ 一个海外仓。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO warehouse (wid, name, kind, market, refreshed_at)"
                    " VALUES (%s, '30-LA自有海外仓', 'oversea_self', 'US', now())", (OVERSEA,))
        _, lid = mint(cur, seed.actor, seed.sku_a, total=100)
        cur.execute("UPDATE plan_line SET demand_by_seller=%s::jsonb WHERE line_id=%s",
                    (json.dumps({seed.seller_a: {"units": 60, "basis": "human"}}), lid))
        advance(cur, lid, "已确认", "已下单", actor=seed.actor)
    return lid


def new_sheet(cur, actor, plan_id=None, route="fba"):
    """★ CA-D11：一张排货表对应一个销售计划。默认挂最早那张计划（world 的那张）。★ CA-D13：带去向。"""
    cur.execute("INSERT INTO sheet (created_by, plan_id, route)"
                " VALUES (%s, coalesce(%s, (SELECT min(plan_id) FROM plan)), %s) RETURNING sheet_id",
                (actor, plan_id, route))
    return cur.fetchone()[0]


def arrange(cur, seed, sheet, line_id, units, *, msku=None, from_wid=None, channel="fba",
            dest_wid=None, dest_sid=None, sku=None):
    seller_sku, sid = msku or seed.msku_a
    if channel == "fba" and dest_sid is None and dest_wid is None:
        dest_sid = sid
    cur.execute(
        "INSERT INTO dispatch_arrangement (sheet_id, line_id, sku, seller_sku, sid, from_wid,"
        " channel, dest_wid, dest_sid, units, updated_by)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING arrangement_id",
        (sheet, line_id, sku or seed.sku_a, seller_sku, sid, from_wid or seed.wid,
         channel, dest_wid, dest_sid, units, seed.actor))
    return cur.fetchone()[0]


def derive_one(cur, seed, sheet, arr_id):
    """测试里的最小派生：一条安排 → 它那张排货单的一行 + 一条映射（用例层的 Python 派生在 Task 6）。"""
    cur.execute("SELECT seller_sku, sid, from_wid, channel, dest_wid, dest_sid, units, sku"
                " FROM dispatch_arrangement WHERE arrangement_id=%s", (arr_id,))
    ssku, sid, fw, ch, dw, ds, units, sku = cur.fetchone()
    cur.execute("SELECT consignment_id FROM consignment WHERE sheet_id=%s AND channel=%s"
                " AND dest_wid IS NOT DISTINCT FROM %s AND dest_sid IS NOT DISTINCT FROM %s",
                (sheet, ch, dw, ds))
    row = cur.fetchone()
    if row is None:
        cur.execute("INSERT INTO consignment (sheet_id, channel, dest_wid, dest_sid, created_by)"
                    " VALUES (%s,%s,%s,%s,%s) RETURNING consignment_id", (sheet, ch, dw, ds, seed.actor))
        row = cur.fetchone()
    cid = row[0]
    cur.execute("INSERT INTO consignment_line (consignment_id, seller_sku, sid, from_wid, sku, units)"
                " VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (consignment_id, seller_sku, sid, from_wid)"
                " DO UPDATE SET units = consignment_line.units + EXCLUDED.units",
                (cid, ssku, sid, fw, sku, units))
    cur.execute("INSERT INTO dispatch_source_map (arrangement_id, consignment_id, seller_sku, sid,"
                " from_wid, units) VALUES (%s,%s,%s,%s,%s,%s)", (arr_id, cid, ssku, sid, fw, units))
    return cid


def sheet_event(cur, sheet, frm, to, actor, reason=None):
    cur.execute("INSERT INTO sheet_event (sheet_id, from_state, to_state, actor, reason, src)"
                " VALUES (%s,%s,%s,%s,%s,'test')", (sheet, frm, to, actor, reason))


def constraint_of(fn):
    with pytest.raises(psycopg2.Error) as e:
        fn()
    return e.value.diag.constraint_name


def run(sql_fn):
    def go():
        with pg_conn() as c, c.cursor() as cur:
            sql_fn(cur)
    return go


# ── 出生与状态推进 ───────────────────────────────────────────

def test_sheet_born_as_draft_with_birth_event(world, seed):
    with pg_conn() as c, c.cursor() as cur:
        sid = new_sheet(cur, seed.actor)
        cur.execute("SELECT state FROM sheet WHERE sheet_id=%s", (sid,))
        assert cur.fetchone()[0] == "草稿"
        cur.execute("SELECT from_state, to_state FROM sheet_event WHERE sheet_id=%s", (sid,))
        assert cur.fetchall() == [("[*]", "草稿")]


def test_sheet_state_only_moves_through_events(world, seed):
    with pg_conn() as c, c.cursor() as cur:
        sid = new_sheet(cur, seed.actor)
    assert constraint_of(run(lambda cur: cur.execute(
        "UPDATE sheet SET state='已确认' WHERE sheet_id=%s", (sid,)))) == "sheet_state_via_event"


def test_consignment_born_as_draft_with_birth_event(world, seed):
    with pg_conn() as c, c.cursor() as cur:
        sid = new_sheet(cur, seed.actor)
        cid = derive_one(cur, seed, sid, arrange(cur, seed, sid, world, 10))
        cur.execute("SELECT our_stage, idem_key FROM consignment WHERE consignment_id=%s", (cid,))
        stage, idem = cur.fetchone()
        cur.execute("SELECT from_state, to_state FROM consignment_event WHERE consignment_id=%s", (cid,))
        assert stage == "草稿" and idem == f"SCM-CN-{cid}"
        assert cur.fetchall() == [("[*]", "草稿")]


# ── 安排的前置条件（CA-D2 / D5 / D6）────────────────────────

def test_planned_and_unplanned_arrangements_both_write(world, seed):
    with pg_conn() as c, c.cursor() as cur:
        sid = new_sheet(cur, seed.actor)
        arrange(cur, seed, sid, world, 10)
        arrange(cur, seed, sid, None, 5)      # ★ CA-D2：无计划来源
        cur.execute("SELECT count(*), count(line_id) FROM dispatch_arrangement WHERE sheet_id=%s", (sid,))
        assert cur.fetchone() == (2, 1)


def test_same_cell_twice_is_rejected(world, seed):
    def go(cur):
        sid = new_sheet(cur, seed.actor)
        arrange(cur, seed, sid, world, 10)
        arrange(cur, seed, sid, world, 3)
    assert constraint_of(run(go)) == "arrangement_planned_cell"


def test_revoked_line_is_rejected(world, seed):
    def go(cur):
        advance(cur, world, "已撤销", actor=seed.actor)
        arrange(cur, seed, new_sheet(cur, seed.actor), world, 1)
    assert constraint_of(run(go)) == "arrangement_line_not_dispatchable"


def test_submitted_line_is_dispatchable_by_quantity(seed):
    """★ CA-D10：按量排 —— 只下单了一部分（停在 已提交）的记录也能排。"""
    with pg_conn() as c, c.cursor() as cur:
        _, lid = mint(cur, seed.actor, seed.sku_a, total=5)            # 已提交
        cur.execute("UPDATE plan_line SET demand_by_seller=%s::jsonb WHERE line_id=%s",
                    (json.dumps({seed.seller_a: {"units": 5, "basis": "human"}}), lid))
        sid = new_sheet(cur, seed.actor)
        derive_one(cur, seed, sid, arrange(cur, seed, sid, lid, 5))


def test_line_from_another_plan_is_rejected(world, seed):
    """★ CA-D11：排货表只排它那张计划的记录。"""
    def go(cur):
        _, lid = mint(cur, seed.actor, seed.sku_a, total=5)            # 另一张计划
        cur.execute("UPDATE plan_line SET demand_by_seller=%s::jsonb WHERE line_id=%s",
                    (json.dumps({seed.seller_a: {"units": 5, "basis": "human"}}), lid))
        arrange(cur, seed, new_sheet(cur, seed.actor), lid, 1)
    assert constraint_of(run(go)) == "arrangement_line_not_in_plan"


def test_sheet_needs_a_plan(world, seed):
    assert constraint_of(run(lambda cur: cur.execute(
        "INSERT INTO sheet (created_by) VALUES (%s)", (seed.actor,)))) == "sheet_needs_plan"


def test_seller_not_in_line_is_rejected(world, seed):
    assert constraint_of(run(lambda cur: arrange(
        cur, seed, new_sheet(cur, seed.actor), world, 1, msku=seed.msku_c))) == "arrangement_seller_not_in_line"


def test_unplanned_cell_has_no_seller_restriction(world, seed):
    with pg_conn() as c, c.cursor() as cur:
        arrange(cur, seed, new_sheet(cur, seed.actor), None, 1, msku=seed.msku_c)


def test_sku_must_match_the_plan_line(world, seed):
    assert constraint_of(run(lambda cur: arrange(
        cur, seed, new_sheet(cur, seed.actor), world, 1, msku=seed.msku_d, sku=seed.sku_b))) \
        == "arrangement_sku_mismatch"


def test_msku_must_belong_to_the_sku(world, seed):
    assert constraint_of(run(lambda cur: arrange(
        cur, seed, new_sheet(cur, seed.actor), None, 1, msku=seed.msku_d))) == "arrangement_msku_not_this_sku"


def test_from_warehouse_must_be_local(world, seed):
    assert constraint_of(run(lambda cur: arrange(
        cur, seed, new_sheet(cur, seed.actor), world, 1, from_wid=OVERSEA))) == "arrangement_from_not_local"


def test_oversea_destination_must_be_an_oversea_warehouse(world, seed):
    assert constraint_of(run(lambda cur: arrange(
        cur, seed, new_sheet(cur, seed.actor), world, 1, channel="oversea", dest_wid=seed.wid))) \
        == "arrangement_invalid_destination"


def test_fba_destination_must_be_the_cells_own_store(world, seed):
    assert constraint_of(run(lambda cur: arrange(
        cur, seed, new_sheet(cur, seed.actor), world, 1, dest_sid=seed.seller_b))) \
        == "arrangement_invalid_destination"


def test_oversea_destination_writes(world, seed):
    with pg_conn() as c, c.cursor() as cur:
        sid = new_sheet(cur, seed.actor, route="oversea")
        derive_one(cur, seed, sid, arrange(cur, seed, sid, world, 4, channel="oversea", dest_wid=OVERSEA))


# ── 量（延迟约束）───────────────────────────────────────────

def test_over_total_across_sheets_is_rejected_at_commit(world, seed):
    with pg_conn() as c, c.cursor() as cur:
        s1 = new_sheet(cur, seed.actor)
        derive_one(cur, seed, s1, arrange(cur, seed, s1, world, 70))

    def go(cur):                         # ★ CA-D13：总量跨去向共用一个数
        s2 = new_sheet(cur, seed.actor, route="oversea")
        derive_one(cur, seed, s2, arrange(cur, seed, s2, world, 31, channel="oversea", dest_wid=OVERSEA))
    assert constraint_of(run(go)) == "dispatch_over_total"


def test_unplanned_units_do_not_count_against_any_line(world, seed):
    with pg_conn() as c, c.cursor() as cur:
        s1 = new_sheet(cur, seed.actor)
        derive_one(cur, seed, s1, arrange(cur, seed, s1, world, 100))
        s2 = new_sheet(cur, seed.actor, route="oversea")
        derive_one(cur, seed, s2, arrange(cur, seed, s2, None, 500, from_wid=seed.wid,
                                          channel="oversea", dest_wid=OVERSEA))


# ── 冻结 · 取消 · 退回 ──────────────────────────────────────

def confirmed_sheet(cur, seed, line_id):
    sid = new_sheet(cur, seed.actor)
    arr = arrange(cur, seed, sid, line_id, 10)
    cid = derive_one(cur, seed, sid, arr)
    cur.execute("INSERT INTO consignment_packing (sheet_id, seller_sku, sid, from_wid, box_count,"
                " updated_by) VALUES (%s,%s,%s,%s,1,%s)", (sid, *seed.msku_a, seed.wid, seed.actor))
    sheet_event(cur, sid, "草稿", "已确认", seed.actor)
    return sid, arr, cid


def test_confirm_cascades_consignments_and_freezes_everything(world, seed):
    with pg_conn() as c, c.cursor() as cur:
        sid, arr, cid = confirmed_sheet(cur, seed, world)
        cur.execute("SELECT our_stage FROM consignment WHERE consignment_id=%s", (cid,))
        assert cur.fetchone()[0] == "已确认"
    for sql, params in [
        ("UPDATE dispatch_arrangement SET units=11 WHERE arrangement_id=%s", (arr,)),
        ("DELETE FROM dispatch_arrangement WHERE arrangement_id=%s", (arr,)),
        ("UPDATE consignment_packing SET box_count=2 WHERE sheet_id=%s", (sid,)),
        ("DELETE FROM consignment_line WHERE consignment_id=%s", (cid,)),
        ("DELETE FROM dispatch_source_map WHERE arrangement_id=%s", (arr,)),
        ("DELETE FROM consignment WHERE consignment_id=%s", (cid,)),
    ]:
        assert constraint_of(run(lambda cur: cur.execute(sql, params))) == "sheet_frozen", sql
    assert constraint_of(run(lambda cur: arrange(cur, seed, sid, None, 1))) == "sheet_frozen"


def test_cancel_needs_a_reason(world, seed):
    with pg_conn() as c, c.cursor() as cur:
        sid = new_sheet(cur, seed.actor)
    assert constraint_of(run(lambda cur: sheet_event(cur, sid, "草稿", "已取消", seed.actor))) \
        == "sheet_cancel_needs_reason"


def test_cancel_confirmed_releases_committed(world, seed):
    with pg_conn() as c, c.cursor() as cur:
        sid, _, cid = confirmed_sheet(cur, seed, world)
        sheet_event(cur, sid, "已确认", "已取消", seed.actor, reason="换方案")
        cur.execute("SELECT our_stage FROM consignment WHERE consignment_id=%s", (cid,))
        assert cur.fetchone()[0] == "已取消"
    # ★ 释放：同一条记录可以重新排满
    with pg_conn() as c, c.cursor() as cur:
        s2 = new_sheet(cur, seed.actor)
        derive_one(cur, seed, s2, arrange(cur, seed, s2, world, 100))


def test_cancel_draft_abandons_consignments(world, seed):
    with pg_conn() as c, c.cursor() as cur:
        sid = new_sheet(cur, seed.actor)
        cid = derive_one(cur, seed, sid, arrange(cur, seed, sid, world, 10))
        sheet_event(cur, sid, "草稿", "已取消", seed.actor, reason="不要了")
        cur.execute("SELECT our_stage FROM consignment WHERE consignment_id=%s", (cid,))
        assert cur.fetchone()[0] == "已放弃"


def test_consignment_cannot_go_back_to_draft_on_its_own(world, seed):
    """★ CA-D14（022）：单只能跟随表「退回修改」，不能自己退。"""
    with pg_conn() as c, c.cursor() as cur:
        _, _, cid = confirmed_sheet(cur, seed, world)

    def go(cur):
        cur.execute("INSERT INTO consignment_event (consignment_id, from_state, to_state, actor, src)"
                    " VALUES (%s,'已确认','草稿',%s,'test')", (cid, seed.actor))
    assert constraint_of(run(go)) == "consignment_back_follows_sheet"


def test_draft_consignment_can_be_deleted_when_emptied(world, seed):
    with pg_conn() as c, c.cursor() as cur:
        sid = new_sheet(cur, seed.actor)
        arr = arrange(cur, seed, sid, world, 10)
        cid = derive_one(cur, seed, sid, arr)
        cur.execute("DELETE FROM consignment WHERE consignment_id=%s", (cid,))
        cur.execute("SELECT count(*) FROM consignment_event WHERE consignment_id=%s", (cid,))
        assert cur.fetchone()[0] == 0


# ── 只追加 ─────────────────────────────────────────────────

def test_events_and_gate_runs_are_append_only(world, seed):
    with pg_conn() as c, c.cursor() as cur:
        sid = new_sheet(cur, seed.actor)
        cur.execute("INSERT INTO sheet_gate_run (sheet_id, actor, ok, ledger_at, gates)"
                    " VALUES (%s,%s,false,1,'[]') RETURNING run_id", (sid, seed.actor))
        run_id = cur.fetchone()[0]
    for sql, p in [("UPDATE sheet_event SET actor='x' WHERE sheet_id=%s", (sid,)),
                   ("DELETE FROM sheet_event WHERE sheet_id=%s", (sid,)),
                   ("UPDATE sheet_gate_run SET ok=true WHERE run_id=%s", (run_id,)),
                   ("DELETE FROM sheet_gate_run WHERE run_id=%s", (run_id,))]:
        with pytest.raises(psycopg2.Error), pg_conn() as c, c.cursor() as cur:
            cur.execute(sql, p)


# ── T21：白名单与 `04` §7.6b 一致 ───────────────────────────

def _doc_s4_rows() -> set[tuple[str, str, str]]:
    doc = (ROOT / "docs/04-状态流转.md").read_text("utf-8")
    sec = doc[doc.index("**S4 白名单全部内容**"):doc.index("★ **表里没有 `已投送 → 草稿`")]
    out = set()
    for ln in sec.splitlines():
        cells = [x.strip().strip("*") for x in ln.strip().strip("|").split("|")]
        if len(cells) >= 3 and cells[2] in ("forward", "back", "cancel"):
            out.add((cells[0], cells[1], cells[2]))
    return out


def _doc_s3_rows() -> set[tuple[str, str, str]]:
    doc = (ROOT / "docs/04-状态流转.md").read_text("utf-8")
    line = next(ln for ln in doc.splitlines() if ln.startswith("**S3 白名单**"))
    return {(a, b, k) for a, b, k in re.findall(r"`([^`]+?) → ([^`]+?)`\((forward|back|cancel)", line)}


def test_whitelists_match_doc_04(world):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT from_state, to_state, kind FROM consignment_transition")
        s4 = set(cur.fetchall())
        cur.execute("SELECT from_state, to_state, kind FROM sheet_transition")
        s3 = set(cur.fetchall())
    assert _doc_s4_rows() and _doc_s3_rows(), "文档表格解析出 0 行 —— 这条断言会空转"
    assert s4 == _doc_s4_rows()
    assert s3 == _doc_s3_rows()
