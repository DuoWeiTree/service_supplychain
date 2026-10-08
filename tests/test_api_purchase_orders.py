import json
import threading

import pytest

from shared.pg_client import pg_conn
from tests.helpers import H, mint


@pytest.fixture
def world(seed):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO supplier VALUES ('8085','01[吉信德]',now())")
        _, lid = mint(cur, seed.actor, seed.sku_a, total=24)
        cur.execute("UPDATE plan_line SET demand_by_seller=%s::jsonb WHERE line_id=%s",
                    (json.dumps({seed.seller_a: {"units": 12, "basis": "human"},
                                 "OTHER": {"units": 11, "basis": "human"}}), lid))
    return lid


def body(seed, lid, units=13, line_units=None, reason=None, version=None):
    b = {"supplier_id": "8085", "purchaser_id": 111, "opt_uid": 10925905, "qc_type": 2,
         "lines": [{"sku": seed.sku_a, "wid": seed.wid, "units": line_units or units, "price": "1.5",
                    "unplanned_reason": reason,
                    "sources": [{"line_id": lid, "seller_id": seed.seller_a, "units": units}]}]}
    if version is not None:
        b["draft_version"] = version
    return b


def line_state(lid):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT state FROM plan_line WHERE line_id=%s", (lid,))
        return cur.fetchone()[0]


def test_create_draft_and_read_back(client, seed, world):
    r = client.post("/v1/purchase-orders", json=body(seed, world), headers=H(seed.actor))
    assert r.status_code == 201, r.text
    po = client.get(f"/v1/purchase-orders/{r.json()['po_id']}").json()
    assert po["state"] == "草稿" and po["custom_order_sn"] == f"SCM-PO-{po['po_id']}"
    assert po["lines"][0]["sources"] == [{"line_id": world, "seller_id": seed.seller_a, "units": 13}]
    assert po["confirm_token"]
    assert line_state(world) == "已提交"      # 13 / 24，未满


def test_over_seller_cap_names_the_numbers(client, seed, world):
    r = client.post("/v1/purchase-orders", json=body(seed, world, units=14), headers=H(seed.actor))
    assert r.status_code == 409
    assert r.json()["error"] == "over_seller_cap"
    assert {k: r.json()[k] for k in ("line_id", "seller_id", "cap", "used", "requested")} == \
        {"line_id": world, "seller_id": seed.seller_a, "cap": 13, "used": 0, "requested": 14}


def test_no_seller_basis_is_named(client, seed, world):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE plan_line SET demand_by_seller='{\"X\":{\"units\":0,\"basis\":\"human\"}}'"
                    " WHERE line_id=%s", (world,))
    r = client.post("/v1/purchase-orders", json=body(seed, world), headers=H(seed.actor))
    assert r.status_code == 409 and r.json()["error"] == "no_seller_basis"
    assert r.json()["sellers"] == ["X"]


def test_unplanned_needs_reason(client, seed, world):
    r = client.post("/v1/purchase-orders", json=body(seed, world, units=13, line_units=20),
                    headers=H(seed.actor))
    assert r.status_code == 400 and r.json()["error"] == "unplanned_reason_required"
    r = client.post("/v1/purchase-orders", json=body(seed, world, units=13, line_units=20, reason="起订量"),
                    headers=H(seed.actor))
    assert r.status_code == 201


def test_two_drafts_filling_line_moves_it_to_confirmed_and_delete_moves_back(client, seed, world):
    a = client.post("/v1/purchase-orders", json=body(seed, world, units=13), headers=H(seed.actor))
    b2 = body(seed, world, units=11)
    b2["lines"][0]["sources"][0]["seller_id"] = "OTHER"
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO seller (seller_id, name, market, has_fba, platform, refreshed_at)"
                    " VALUES ('OTHER','o','US',true,'amazon',now()) ON CONFLICT DO NOTHING")
    b = client.post("/v1/purchase-orders", json=b2, headers=H(seed.actor))
    assert b.status_code == 201, b.text
    assert line_state(world) == "已确认"
    assert client.delete(f"/v1/purchase-orders/{b.json()['po_id']}", headers=H(seed.actor)).status_code == 204
    assert line_state(world) == "已提交"
    assert a.status_code == 201


def test_update_needs_current_draft_version(client, seed, world):
    po_id = client.post("/v1/purchase-orders", json=body(seed, world), headers=H(seed.actor)).json()["po_id"]
    r = client.put(f"/v1/purchase-orders/{po_id}", json=body(seed, world, units=10, version=1),
                   headers=H(seed.actor))
    assert r.status_code == 200 and r.json()["draft_version"] == 2
    stale = client.put(f"/v1/purchase-orders/{po_id}", json=body(seed, world, units=9, version=1),
                       headers=H(seed.actor))
    assert stale.status_code == 409 and stale.json()["error"] == "stale_draft"
    assert stale.json()["current_version"] == 2


def test_duplicate_sku_wid_lines_rejected(client, seed, world):
    b = body(seed, world, units=5)
    b["lines"].append(dict(b["lines"][0], sources=[]))
    r = client.post("/v1/purchase-orders", json=b, headers=H(seed.actor))
    assert r.status_code == 400 and r.json()["error"] == "duplicate_line"


def test_concurrent_drafts_cannot_both_take_the_same_quantity(client, seed, world):
    """Review Focus #1：两个人同时组同一条记录，只能一个成功。

    ★ 断言输家的 body：应用层的行锁让它读到赢家已提交的 used=13 并点名 over_total；
      没有锁时两边都读到 used=0，只能被库层兜底拦下（hint 带「并发组单」，numbers 不同）。
    """
    results = []

    def go():
        r = client.post("/v1/purchase-orders", json=body(seed, world, units=13), headers=H(seed.actor))
        results.append((r.status_code, r.json()))

    ts = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(st for st, _ in results) == [201, 409], results
    loser = next(j for st, j in results if st == 409)
    assert loser["error"] == "over_total"
    assert (loser["used"], loser["requested"], loser["total"]) == (13, 13, 24)
    assert "并发组单" not in loser["hint"]
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM po WHERE discarded_at IS NULL")
        assert cur.fetchone()[0] == 1
    assert line_state(world) == "已提交"


# ---- 以下守护既有行为 ----

def mk_other():
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO seller (seller_id, name, market, has_fba, platform, refreshed_at)"
                    " VALUES ('OTHER','o','US',true,'amazon',now()) ON CONFLICT DO NOTHING")


def full_body(seed, lid, sku=None, version=None):
    """A 店 13 + OTHER 11 = 24，正好填满。"""
    b = body(seed, lid, units=13)
    b["lines"][0]["units"] = 24
    b["lines"][0]["sources"].append({"line_id": lid, "seller_id": "OTHER", "units": 11})
    if sku:
        b["lines"][0]["sku"] = sku
    if version is not None:
        b["draft_version"] = version
    return b


def mk_draft(client, seed, b):
    r = client.post("/v1/purchase-orders", json=b, headers=H(seed.actor))
    assert r.status_code == 201, r.text
    return r.json()["po_id"]


def sql(q, *a):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute(q, a)
        return cur.fetchall() if cur.description else None


def place(po_id):
    sql("UPDATE po SET order_sn='FAKE-1', placed_mode='fake', placed_at=now() WHERE po_id=%s", po_id)
    sql("INSERT INTO po_event (po_id, from_state, to_state, actor, src)"
        " VALUES (%s,'草稿','已下单','alice','test')", po_id)


def mk_call(po_id, outcome, side="irreversible"):
    sql("INSERT INTO erp_call (endpoint, side_effect, mode, po_id, actor, request, outcome)"
        " VALUES ('x', %s, 'fake', %s, 'alice', '{}'::jsonb, %s)", side, po_id, outcome)


def test_delete_after_erp_call_is_soft_discard(client, seed, world):
    mk_other()
    po_id = mk_draft(client, seed, full_body(seed, world))
    assert line_state(world) == "已确认"
    mk_call(po_id, "ok")
    assert client.delete(f"/v1/purchase-orders/{po_id}", headers=H(seed.actor)).status_code == 204
    assert sql("SELECT discarded_at IS NOT NULL FROM po WHERE po_id=%s", po_id) == [(True,)]
    assert client.get(f"/v1/purchase-orders/{po_id}").json()["lines"] == []
    assert line_state(world) == "已提交"
    assert sql("SELECT count(*) FROM erp_call WHERE po_id=%s", po_id) == [(1,)]
    r = client.delete(f"/v1/purchase-orders/{po_id}", headers=H(seed.actor))
    assert r.status_code == 409 and r.json()["error"] == "po_discarded"
    ids = [p["po_id"] for p in client.get("/v1/purchase-orders").json()["purchase_orders"]]
    assert po_id not in ids


@pytest.mark.parametrize("setup, error", [
    (place, "po_frozen"),
    (lambda po: mk_call(po, "in_flight"), "po_placing"),
    (lambda po: sql("INSERT INTO po_attention (po_id, kind) VALUES (%s,'needs_verify')", po), "needs_verify"),
])
def test_put_and_delete_refused_when_not_editable(client, seed, world, setup, error):
    po_id = mk_draft(client, seed, body(seed, world))
    setup(po_id)
    r = client.put(f"/v1/purchase-orders/{po_id}", json=body(seed, world, units=5, version=1),
                   headers=H(seed.actor))
    assert (r.status_code, r.json()["error"]) == (409, error)
    r = client.delete(f"/v1/purchase-orders/{po_id}", headers=H(seed.actor))
    assert (r.status_code, r.json()["error"]) == (409, error)


def test_unknown_po_and_unknown_line_are_404(client, seed, world):
    h = H(seed.actor)
    for r in (client.get("/v1/purchase-orders/999999"),
              client.put("/v1/purchase-orders/999999", json=body(seed, world, version=1), headers=h),
              client.delete("/v1/purchase-orders/999999", headers=h)):
        assert r.status_code == 404 and r.json()["error"] == "po_not_found"
    r = client.post("/v1/purchase-orders", json=body(seed, 999999), headers=h)
    assert r.status_code == 404 and r.json()["error"] == "line_not_found"


def test_withdrawn_line_is_not_orderable(client, seed, world):
    from tests.helpers import advance
    with pg_conn() as c, c.cursor() as cur:
        advance(cur, world, "已撤销", actor=seed.actor)
    r = client.post("/v1/purchase-orders", json=body(seed, world), headers=H(seed.actor))
    assert r.status_code == 409 and r.json()["error"] == "line_not_orderable"
    assert r.json()["state"] == "已撤销"


def test_over_total_names_total_used_requested(client, seed, world):
    mk_other()
    mk_draft(client, seed, full_body(seed, world))
    r = client.post("/v1/purchase-orders", json=body(seed, world, units=1), headers=H(seed.actor))
    assert r.status_code == 409 and r.json()["error"] == "over_total"
    assert (r.json()["total"], r.json()["used"], r.json()["requested"]) == (24, 24, 1)


def test_missing_header_field_is_named(client, seed, world):
    b = body(seed, world)
    del b["purchaser_id"]
    r = client.post("/v1/purchase-orders", json=b, headers=H(seed.actor))
    assert r.status_code == 400 and r.json()["error"] == "missing_required"
    assert r.json()["missing"] == ["purchaser_id"]


def test_confirm_token_changes_after_put(client, seed, world):
    po_id = mk_draft(client, seed, body(seed, world))
    t1 = client.get(f"/v1/purchase-orders/{po_id}").json()["confirm_token"]
    assert client.put(f"/v1/purchase-orders/{po_id}", json=body(seed, world, units=10, version=1),
                      headers=H(seed.actor)).status_code == 200
    assert client.get(f"/v1/purchase-orders/{po_id}").json()["confirm_token"] != t1


def test_put_moving_source_between_lines_rederives_both(client, seed, world):
    mk_other()
    with pg_conn() as c, c.cursor() as cur:
        _, lid_b = mint(cur, seed.actor, seed.sku_b, total=24)
        cur.execute("UPDATE plan_line SET demand_by_seller=%s::jsonb WHERE line_id=%s",
                    (json.dumps({seed.seller_a: {"units": 12, "basis": "human"},
                                 "OTHER": {"units": 11, "basis": "human"}}), lid_b))
    po_id = mk_draft(client, seed, full_body(seed, world))
    assert line_state(world) == "已确认"
    moved = full_body(seed, lid_b, sku=seed.sku_b, version=1)
    r = client.put(f"/v1/purchase-orders/{po_id}", json=moved, headers=H(seed.actor))
    assert r.status_code == 200, r.text
    assert line_state(world) == "已提交"
    assert line_state(lid_b) == "已确认"


def test_unknown_supplier_is_400_not_500(client, seed, world):
    b = body(seed, world)
    b["supplier_id"] = "NO-SUCH-SUPPLIER"
    r = client.post("/v1/purchase-orders", json=b, headers=H(seed.actor))
    assert r.status_code == 400 and r.json()["error"] == "unknown_supplier"
    assert r.json()["supplier_id"] == "NO-SUCH-SUPPLIER"
    po_id = client.post("/v1/purchase-orders", json=body(seed, world), headers=H(seed.actor)).json()["po_id"]
    b["draft_version"] = 1
    r = client.put(f"/v1/purchase-orders/{po_id}", json=b, headers=H(seed.actor))
    assert r.status_code == 400 and r.json()["error"] == "unknown_supplier"
