"""`GET /v1/purchase/plans`（UX-D1 / D2 / D3，spec 2026-10-16 §4）：待采购按销售计划分组。

★ 主要防的是「被组满的记录悄悄消失」：组进草稿的计划仍要列出并挂草稿；
  全部下单的计划不列，但必须在 excluded.nothing_to_order 里点名。
"""
import datetime as dt
import json

from shared.pg_client import pg_conn
from tests.helpers import H, advance, mint

NOV = dt.date(2026, 11, 1)


def _demand(cur, lid, seller):
    cur.execute("UPDATE plan_line SET demand_by_seller=%s::jsonb WHERE line_id=%s",
                (json.dumps({seller: {"units": 5, "basis": "human"}}), lid))


def _plan(seed, total=10, second_total=None, sku=None):
    """一个计划；second_total 给了就在同一计划下再铸一条（sku_b，11 月）。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO supplier VALUES ('8085','01[吉信德]',now()) ON CONFLICT DO NOTHING")
        pid, lid = mint(cur, seed.actor, sku or seed.sku_a, total=total)
        _demand(cur, lid, seed.seller_a)
        out = [lid]
        if second_total is not None:
            cur.execute("INSERT INTO plan_line (plan_id, rev, sku, period_start, total_units, demand_by_seller,"
                        " demand_at_submit, state) VALUES (%s, 1, %s, %s, %s, '{}'::jsonb, 0, '已提交')"
                        " RETURNING line_id", (pid, seed.sku_b, NOV, second_total))
            lid2 = cur.fetchone()[0]
            cur.execute("INSERT INTO plan_line_event (line_id, from_state, to_state, actor, src)"
                        " VALUES (%s, '[*]', '已提交', %s, 'test')", (lid2, seed.actor))
            _demand(cur, lid2, seed.seller_a)
            out.append(lid2)
    return pid, out


def _draft(client, seed, parts):
    """parts = [(sku, line_id, units)]，每个货号一行。"""
    lines = [{"sku": sku, "wid": seed.wid, "units": u, "price": "1.5",
              "sources": [{"line_id": lid, "seller_id": seed.seller_a, "units": u}]} for sku, lid, u in parts]
    r = client.post("/v1/purchase-orders", json={"supplier_id": "8085", "purchaser_id": 111, "opt_uid": 10925905,
                                                 "qc_type": 2, "lines": lines}, headers=H(seed.actor))
    assert r.status_code == 201, r.text
    return r.json()["po_id"]


def _place(client, seed, po_id):
    tok = client.get(f"/v1/purchase-orders/{po_id}").json()["confirm_token"]
    r = client.post(f"/v1/purchase-orders/{po_id}/place", json={"confirm_token": tok}, headers=H(seed.actor))
    assert r.json()["outcome"] == "placed", r.text


def _body(client):
    r = client.get("/v1/purchase/plans")
    assert r.status_code == 200, r.text
    return r.json()


def _by_id(body):
    return {p["plan_id"]: p for p in body["plans"]}


def test_plan_header_splits_confirmed_and_ordered_and_lists_records(client, seed):
    pid, (a, b) = _plan(seed, total=10, second_total=8)
    _draft(client, seed, [(seed.sku_a, a, 4)])
    p = _by_id(_body(client))[pid]
    assert (p["lines"], p["total"], p["confirmed"], p["ordered"]) == (2, 18, 4, 0)
    assert (p["orderable_units"], p["orderable_lines"]) == (14, 2)
    recs = {r["line_id"]: r for r in p["records"]}
    assert recs[a]["remaining"] == 6 and recs[a]["orderable"] is True and recs[a]["sku_name"] == "猫爬架"
    assert recs[b]["period"] == "2026-11"


def test_plan_fully_confirmed_by_draft_is_still_listed_with_the_draft(client, seed):
    """★ UX-D2：草稿把计划组满后，计划不能从待采购里消失 —— 草稿挂在抬头上。"""
    pid, (a,) = _plan(seed, total=10)
    po = _draft(client, seed, [(seed.sku_a, a, 10)])
    p = _by_id(_body(client))[pid]
    assert p["orderable_units"] == 0 and p["orderable_lines"] == 0
    assert p["records"][0]["orderable"] is False and p["records"][0]["remaining"] == 0
    assert [(d["po_id"], d["units"], d["open_attention"]) for d in p["drafts"]] == [(po, 10, 0)]
    assert p["drafts"][0]["custom_order_sn"].startswith("SCM-PO-")


def test_plan_fully_placed_is_not_listed_but_named(client, seed, lingxing):
    pid, (a,) = _plan(seed, total=10)
    _place(client, seed, _draft(client, seed, [(seed.sku_a, a, 10)]))
    body = _body(client)
    assert pid not in _by_id(body)
    assert pid in [x["plan_id"] for x in body["excluded"]["nothing_to_order"]]


def test_cross_plan_draft_counts_its_units_per_plan(client, seed):
    p1, (a,) = _plan(seed, total=10)
    p2, (b,) = _plan(seed, total=10, sku=seed.sku_b)
    po = _draft(client, seed, [(seed.sku_a, a, 3), (seed.sku_b, b, 7)])
    plans = _by_id(_body(client))
    assert [(d["po_id"], d["units"]) for d in plans[p1]["drafts"]] == [(po, 3)]
    assert [(d["po_id"], d["units"]) for d in plans[p2]["drafts"]] == [(po, 7)]


def test_draft_without_plan_source_is_reported_separately(client, seed):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO supplier VALUES ('8085','01[吉信德]',now()) ON CONFLICT DO NOTHING")
    r = client.post("/v1/purchase-orders", json={
        "supplier_id": "8085", "purchaser_id": 111, "opt_uid": 10925905, "qc_type": 2,
        "lines": [{"sku": seed.sku_a, "wid": seed.wid, "units": 5, "price": "1", "unplanned_reason": "补货",
                   "sources": []}]}, headers=H(seed.actor))
    assert r.status_code == 201, r.text
    po = r.json()["po_id"]
    assert [(d["po_id"], d["units"]) for d in _body(client)["unplanned_drafts"]] == [(po, 5)]


def test_orderable_flag_agrees_with_plan_lines_orderable(client, seed):
    """同一判据只写一处：/plan-lines?orderable=1 列出的，正好是这里 orderable 的记录。"""
    _, (a, b) = _plan(seed, total=10, second_total=8)
    _, (c,) = _plan(seed, total=5)
    _draft(client, seed, [(seed.sku_a, c, 5)])
    with pg_conn() as conn, conn.cursor() as cur:
        advance(cur, b, "已撤销", actor=seed.actor)
    here = {r["line_id"] for p in _body(client)["plans"] for r in p["records"] if r["orderable"]}
    there = {r["line_id"] for r in client.get("/v1/plan-lines?orderable=1").json()["lines"]}
    assert here == there and a in here and c not in here
    # 撤销的记录不是活记录：不进 records
    assert b not in {r["line_id"] for p in _body(client)["plans"] for r in p["records"]}
