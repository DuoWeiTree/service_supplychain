"""再提交（R2 · spec §2）：不先撤销，没变的记录原样保留，只替换变了的货号·月。

★ 每条都要同时看「这次动了什么」和「什么没动」—— 只看前者，
  多撤一条、少留一条都长得像「成功了」。
"""
import logging

import pytest
from helpers import H, advance, prepared

from shared.pg_client import pg_conn

OCT = "2026-10"


@pytest.fixture
def two_skus(client, seed):
    """sku_a（两店）+ sku_b（单店）各一条 10 月记录的计划，已提交 rev1。"""
    pid = prepared(client, seed)
    a = H(seed.actor)
    assert client.post(f"/v1/plans/{pid}/claims",
                       json={"seller_sku": seed.msku_d[0], "sid": seed.msku_d[1]},
                       headers=a).status_code == 200
    assert client.put(f"/v1/plans/{pid}/demand/{seed.msku_d[0]}/{seed.msku_d[1]}/{OCT}",
                      json={"expected_units": 50}, headers=a).status_code == 200
    assert client.put(f"/v1/plans/{pid}/purchase/{seed.sku_b}/{OCT}",
                      json={"planned_units": 300}, headers=a).status_code == 200
    r = client.post(f"/v1/plans/{pid}/submit", headers=a)
    assert r.status_code == 200 and r.json()["lines"] == 2, r.text
    return pid


def _lines(pid):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT sku, line_id, rev, state, total_units FROM plan_line"
                    " WHERE plan_id = %s ORDER BY line_id", (pid,))
        return cur.fetchall()


def _live(pid):
    return {r[0]: r for r in _lines(pid) if r[3] not in ("已撤销", "已完结")}


def _revs(pid):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT rev, in_flight FROM plan_rev WHERE plan_id = %s ORDER BY rev", (pid,))
        return cur.fetchall()


def _snapshot(pid):
    """整个再提交会碰到的每张表 —— 409 之后必须逐字节相同。"""
    with pg_conn() as c, c.cursor() as cur:
        out = {}
        for name, sql in {
            "plan_rev": "SELECT plan_id, rev, in_flight, content_digest FROM plan_rev"
                        " WHERE plan_id = %s ORDER BY rev",
            "plan_line": "SELECT line_id, rev, state, total_units FROM plan_line"
                         " WHERE plan_id = %s ORDER BY line_id",
            "plan_line_event": "SELECT e.seq FROM plan_line_event e JOIN plan_line l USING (line_id)"
                               " WHERE l.plan_id = %s ORDER BY e.seq",
            "plan_submit_skip": "SELECT skip_id FROM plan_submit_skip WHERE plan_id = %s"
                                " ORDER BY skip_id",
            "plan_line_carry": "SELECT carry_id FROM plan_line_carry WHERE plan_id = %s"
                               " ORDER BY carry_id",
            "po_source_map": "SELECT m.po_id, m.line_no, m.line_id, m.seller_id, m.units"
                             " FROM po_source_map m JOIN plan_line l USING (line_id)"
                             " WHERE l.plan_id = %s ORDER BY 1, 2, 3, 4",
        }.items():
            cur.execute(sql, (pid,))
            out[name] = cur.fetchall()
        return out


def _draft(client, seed, line_id, sku, units=100):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO supplier VALUES ('8085','01[吉信德]',now()) ON CONFLICT DO NOTHING")
    r = client.post("/v1/purchase-orders", headers=H(seed.actor), json={
        "supplier_id": "8085", "purchaser_id": 111, "opt_uid": 10925905, "qc_type": 2,
        "lines": [{"sku": sku, "wid": seed.wid, "units": units, "price": "1.5",
                   "sources": [{"line_id": line_id, "seller_id": seed.seller_a, "units": units}]}]})
    assert r.status_code == 201, r.text
    return r.json()["po_id"]


def _checks():
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT check_name, ref FROM v_check_purchase")
        return cur.fetchall()


def test_resubmit_without_cancel_keeps_unchanged_and_replaces_changed(client, seed, two_skus):
    """★ ① 不撤销直接再提交成功 · ② 没变的 line_id、状态不变 · ③ 变了的旧记录已撤销且有 actor/理由/src。"""
    pid = two_skus
    before = _live(pid)
    client.put(f"/v1/plans/{pid}/purchase/{seed.sku_a}/{OCT}",
               json={"planned_units": 600}, headers=H(seed.actor))
    r = client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rev"] == 2 and body["in_flight"] is True and body["lines"] == 2
    assert {k: body[k]["count"] for k in ("kept", "replaced", "added", "removed")} == \
        {"kept": 1, "replaced": 1, "added": 0, "removed": 0}
    assert body["kept"]["lines"] == [{"sku": seed.sku_b, "period": OCT,
                                      "line_id": before[seed.sku_b][1], "state": "已提交",
                                      "total_units": 300}]
    rep = body["replaced"]["lines"][0]
    assert (rep["sku"], rep["period"], rep["old_line_id"]) == (seed.sku_a, OCT, before[seed.sku_a][1])
    assert rep["total_units"] == {"from": 500, "to": 600}

    after = _live(pid)
    assert after[seed.sku_b][1:4] == (before[seed.sku_b][1], 2, "已提交"), "没变的：同一条、归入新版、状态不动"
    assert after[seed.sku_a][1] != before[seed.sku_a][1] and after[seed.sku_a][4] == 600
    assert rep["new_line_id"] == after[seed.sku_a][1]
    assert _revs(pid) == [(1, False), (2, True)], "★ S-4：任一时刻只 1 版在流转"

    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT from_state, to_state, actor, reason, src FROM plan_line_event"
                    " WHERE line_id = %s ORDER BY seq", (before[seed.sku_a][1],))
        assert cur.fetchall()[-1] == ("已提交", "已撤销", seed.actor, "被 rev 2 替换",
                                      f"resubmit:{pid}#2")
        cur.execute("SELECT line_id, from_rev, to_rev, actor, src FROM plan_line_carry"
                    " WHERE plan_id = %s", (pid,))
        assert cur.fetchall() == [(before[seed.sku_b][1], 1, 2, seed.actor, f"carry:{pid}#2")]
        # 保留的那条没有多出任何状态事件
        cur.execute("SELECT count(*) FROM plan_line_event WHERE line_id = %s",
                    (before[seed.sku_b][1],))
        assert cur.fetchone()[0] == 1


def test_a_cell_no_longer_minted_is_removed_with_its_reason(client, seed, two_skus):
    pid = two_skus
    before = _live(pid)
    client.put(f"/v1/plans/{pid}/purchase/{seed.sku_b}/{OCT}",
               json={"planned_units": None}, headers=H(seed.actor))
    body = client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor)).json()
    assert {k: body[k]["count"] for k in ("kept", "replaced", "added", "removed")} == \
        {"kept": 1, "replaced": 0, "added": 0, "removed": 1}
    assert body["removed"]["lines"] == [{"sku": seed.sku_b, "period": OCT,
                                         "line_id": before[seed.sku_b][1], "total_units": 300}]
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT to_state, reason, src FROM plan_line_event WHERE line_id = %s"
                    " ORDER BY seq DESC LIMIT 1", (before[seed.sku_b][1],))
        assert cur.fetchone() == ("已撤销", "rev 2 不再包含", f"resubmit:{pid}#2")
    assert set(_live(pid)) == {seed.sku_a}


def test_a_new_cell_is_added(client, seed, two_skus):
    pid = two_skus
    client.put(f"/v1/plans/{pid}/purchase/{seed.sku_a}/2026-11",
               json={"planned_units": 70}, headers=H(seed.actor))
    body = client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor)).json()
    assert {k: body[k]["count"] for k in ("kept", "replaced", "added", "removed")} == \
        {"kept": 2, "replaced": 0, "added": 1, "removed": 0}
    assert [(x["sku"], x["period"], x["total_units"]) for x in body["added"]["lines"]] == \
        [(seed.sku_a, "2026-11", 70)]


def test_changing_a_line_that_a_draft_po_sources_is_409_and_changes_nothing(client, seed, two_skus):
    """★ Review Focus 1 / D1：草稿里的来源被静默撤销，要到下单前检查才炸 —— 晚且远。"""
    pid = two_skus
    lid = _live(pid)[seed.sku_a][1]
    po_id = _draft(client, seed, lid, seed.sku_a)
    client.put(f"/v1/plans/{pid}/purchase/{seed.sku_a}/{OCT}",
               json={"planned_units": 600}, headers=H(seed.actor))
    # sku_b 也改了：没被引用的那条照样不许动 —— 整次拒绝，不是部分成功
    client.put(f"/v1/plans/{pid}/purchase/{seed.sku_b}/{OCT}",
               json={"planned_units": 301}, headers=H(seed.actor))
    snap = _snapshot(pid)
    r = client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    assert r.status_code == 409 and r.json()["error"] == "lines_in_purchase", r.text
    assert r.json()["lines"] == [{"sku": seed.sku_a, "period": OCT, "line_id": lid,
                                  "state": "已提交", "change": "replace", "po_ids": [po_id]}]
    assert _snapshot(pid) == snap


def test_removing_a_line_that_a_draft_po_sources_is_409(client, seed, two_skus):
    pid = two_skus
    lid = _live(pid)[seed.sku_b][1]
    po_id = _draft(client, seed, lid, seed.sku_b, units=50)
    client.put(f"/v1/plans/{pid}/purchase/{seed.sku_b}/{OCT}",
               json={"planned_units": 0}, headers=H(seed.actor))
    snap = _snapshot(pid)
    r = client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    assert r.status_code == 409 and r.json()["lines"][0]["change"] == "remove"
    assert r.json()["lines"][0]["po_ids"] == [po_id]
    assert _snapshot(pid) == snap


def test_changing_a_line_past_submitted_is_409_even_without_po_sources(client, seed, two_skus):
    """★ ⑤ 已下单的同理：判状态这一支单独靶 —— 不靠采购单来源那一支托着。"""
    pid = two_skus
    lid = _live(pid)[seed.sku_a][1]
    with pg_conn() as c, c.cursor() as cur:
        advance(cur, lid, "已确认", "已下单", actor=seed.actor)
    client.put(f"/v1/plans/{pid}/purchase/{seed.sku_a}/{OCT}",
               json={"planned_units": 600}, headers=H(seed.actor))
    snap = _snapshot(pid)
    r = client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    assert r.status_code == 409
    assert r.json()["lines"] == [{"sku": seed.sku_a, "period": OCT, "line_id": lid,
                                  "state": "已下单", "change": "replace", "po_ids": []}]
    assert _snapshot(pid) == snap


def test_unchanged_line_in_a_draft_is_kept_and_the_load_bearing_wall_stays_clean(
        client, seed, two_skus):
    """★ Review Focus 3：v_check_* 再提交前后都 0 违规；被草稿引用、没变的那条照样保留，来源不断。"""
    pid = two_skus
    lid_b = _live(pid)[seed.sku_b][1]
    po_id = _draft(client, seed, lid_b, seed.sku_b, units=300)    # 组满 → 已确认
    assert _live(pid)[seed.sku_b][3] == "已确认"
    assert _checks() == []
    client.put(f"/v1/plans/{pid}/purchase/{seed.sku_a}/{OCT}",
               json={"planned_units": 600}, headers=H(seed.actor))
    r = client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    assert r.status_code == 200, r.text
    assert r.json()["kept"]["lines"][0]["state"] == "已确认"
    assert _live(pid)[seed.sku_b][1:4] == (lid_b, 2, "已确认")
    assert _checks() == []
    po = client.get(f"/v1/purchase-orders/{po_id}").json()
    assert po["source_line_states"] == [{"line_id": lid_b, "state": "已确认"}]
    # 新版里仍只有一组活记录：承重墙按记录求和不翻倍
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT sku, count(*) FROM plan_line WHERE plan_id = %s"
                    " AND state NOT IN ('已撤销','已完结') GROUP BY sku", (pid,))
        assert dict(cur.fetchall()) == {seed.sku_a: 1, seed.sku_b: 1}


def test_identical_resubmit_keeps_everything_and_still_stands_up_a_rev(client, seed, two_skus):
    """★ Review Focus 2：全部保留、0 替换 0 新增 0 去掉；新版立起来，旧版让出在流转位，不出重复活记录。"""
    pid = two_skus
    before = _live(pid)
    r = client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    assert r.status_code == 200, r.text
    body = r.json()
    assert {k: body[k]["count"] for k in ("kept", "replaced", "added", "removed")} == \
        {"kept": 2, "replaced": 0, "added": 0, "removed": 0}
    after = _live(pid)
    assert {k: v[1] for k, v in after.items()} == {k: v[1] for k, v in before.items()}
    assert len(_lines(pid)) == 2
    assert _revs(pid) == [(1, False), (2, True)]
    assert _checks() == []


def test_revs_and_diff_still_see_what_each_rev_contained(client, seed, two_skus):
    """保留的记录归入新版之后，旧版的「当时包含什么」不能跟着消失。"""
    pid = two_skus
    client.put(f"/v1/plans/{pid}/purchase/{seed.sku_a}/{OCT}",
               json={"planned_units": 600}, headers=H(seed.actor))
    client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    revs = client.get(f"/v1/plans/{pid}/revs", headers=H(seed.actor)).json()
    assert [(x["rev"], x["lines"]) for x in revs["revs"]] == [(2, 2), (1, 2)]
    assert revs["in_flight_rev"] == 2
    d = client.get(f"/v1/plans/{pid}/diff", params={"from": 1, "to": 2},
                   headers=H(seed.actor)).json()
    assert d["added"] == [] and d["removed"] == []
    assert [(x["sku"], x["total_units"]) for x in d["changed"]] == \
        [(seed.sku_a, {"from": 500, "to": 600})]
    lines = client.get("/v1/plan-lines", params={"plan_ids": pid},
                       headers=H(seed.actor)).json()["lines"]
    assert {(x["sku"], x["rev"]) for x in lines if x["state"] == "已提交"} == \
        {(seed.sku_a, 2), (seed.sku_b, 2)}


def test_empty_and_zero_purchase_are_skipped_with_different_reasons(client, seed):
    """★ ⑧ R1：空 ≠ 0 —— 没填记 empty_purchase，填 0 记 zero_purchase。"""
    pid = prepared(client, seed)
    client.put(f"/v1/plans/{pid}/purchase/{seed.sku_a}/2026-12",
               json={"planned_units": 0}, headers=H(seed.actor))
    r = client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor)).json()
    assert [(s["period"], s["reason"]) for s in r["skipped"]] == [
        ("2026-11", "empty_purchase"), ("2026-12", "zero_purchase")]
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT to_char(period_start, 'YYYY-MM'), reason FROM plan_submit_skip"
                    " WHERE plan_id = %s ORDER BY period_start", (pid,))
        assert cur.fetchall() == [("2026-11", "empty_purchase"), ("2026-12", "zero_purchase")]


def test_submit_logs_the_four_counts_and_refusals_name_the_offenders(
        client, seed, two_skus, scm_log):
    pid = two_skus
    client.put(f"/v1/plans/{pid}/purchase/{seed.sku_a}/{OCT}",
               json={"planned_units": 600}, headers=H(seed.actor))
    client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    assert f"op=submit plan_id={pid} rev=2 kept=1 replaced=1 added=0 removed=0" in scm_log.text
    lid = _live(pid)[seed.sku_a][1]
    po_id = _draft(client, seed, lid, seed.sku_a)
    client.put(f"/v1/plans/{pid}/purchase/{seed.sku_a}/{OCT}",
               json={"planned_units": 700}, headers=H(seed.actor))
    scm_log.clear()
    with scm_log.at_level(logging.INFO, logger="scm"):
        client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    assert "op=submit outcome=lines_in_purchase" in scm_log.text
    assert f"line_id={lid}" in scm_log.text and f"po_ids=[{po_id}]" in scm_log.text
