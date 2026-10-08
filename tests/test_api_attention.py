import json

import pytest

from erp import gateway
from erp.drivers.fake import FAKE
from shared.pg_client import pg_conn
from tests.helpers import H, mint


@pytest.fixture(autouse=True)
def clean_fake():
    FAKE.reset()
    yield
    FAKE.reset()


@pytest.fixture
def stuck(client, seed):
    """一张进了「待人工核实」的单。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO supplier VALUES ('8085','01',now())")
        _, lid = mint(cur, seed.actor, seed.sku_a, total=10)
        cur.execute("UPDATE plan_line SET demand_by_seller=%s::jsonb WHERE line_id=%s",
                    (json.dumps({seed.seller_a: {"units": 5, "basis": "human"}}), lid))
    body = {"supplier_id": "8085", "purchaser_id": 111, "opt_uid": 1, "qc_type": 2,
            "lines": [{"sku": seed.sku_a, "wid": seed.wid, "units": 10, "price": "2",
                       "sources": [{"line_id": lid, "seller_id": seed.seller_a, "units": 10}]}]}
    po_id = client.post("/v1/purchase-orders", json=body, headers=H(seed.actor)).json()["po_id"]
    tok = client.get(f"/v1/purchase-orders/{po_id}").json()["confirm_token"]
    FAKE.enqueue("create_purchase_order", "unknown_absent")
    assert client.post(f"/v1/purchase-orders/{po_id}/place", json={"confirm_token": tok},
                       headers=H(seed.actor)).json()["outcome"] == "needs_verify"
    return po_id, lid


def _late_order(seed, po_id, custom=None):
    gateway.call("create_purchase_order", {"custom_order_sn": custom or f"SCM-PO-{po_id}", "supplier_id": 8085,
                                           "purchaser_id": 111, "opt_uid": 1, "qc_type": 2, "wid": seed.wid,
                                           "product_list": [{"sku": seed.sku_a, "wid": seed.wid,
                                                             "quantity_real": 10, "price": "2"}]})


def test_timeline_says_what_is_known_and_unknown(client, stuck):
    po_id, _ = stuck
    t = client.get(f"/v1/purchase-orders/{po_id}/timeline").json()
    cc = t["conclusion"]
    assert cc["known"] and cc["unknown"]
    assert "请求已发到领星" in cc["known"] and "TimeoutError" in cc["known"]
    assert cc["how_to_search"]["custom_order_sn"] == f"SCM-PO-{po_id}"
    assert cc["how_to_search"]["window"]["start_date"] < cc["how_to_search"]["window"]["end_date"]
    ll = cc["last_lookup"]
    assert ll["outcome"] == "ok" and ll["truncated"] is False and ll["call_id"] == t["attempts"][1]["call_id"]
    a = t["attempts"][0]
    assert (a["endpoint"], a["outcome"], a["mode"]) == ("create_purchase_order", "unknown", "fake")
    assert "TimeoutError" in a["cause"] and a["idem_key"] == f"SCM-PO-{po_id}"
    assert t["attempts"][1]["endpoint"] == "purchase_order_list"


def test_timeline_unknown_po_is_404(client):
    r = client.get("/v1/purchase-orders/999999/timeline")
    assert r.status_code == 404 and r.json()["error"] == "po_not_found"


def test_queue_lists_open_items(client, stuck):
    po_id, _ = stuck
    q = client.get("/v1/attention").json()["items"]
    assert [(i["po_id"], i["kind"]) for i in q] == [(po_id, "needs_verify")]


def test_register_existing_verifies_before_accepting(client, seed, stuck):
    po_id, _ = stuck
    _late_order(seed, po_id, custom="SOMEONE-ELSE")
    r = client.post(f"/v1/purchase-orders/{po_id}/register-existing", json={"order_sn": "FAKE-PO000001"},
                    headers=H(seed.actor))
    assert r.status_code == 409 and r.json()["error"] == "order_mismatch"
    assert r.json()["mismatch"]["custom_order_sn"] == {"ours": f"SCM-PO-{po_id}", "lingxing": "SOMEONE-ELSE"}
    assert len(client.get("/v1/attention").json()["items"]) == 1       # 未接受：仍待处理
    r = client.post(f"/v1/purchase-orders/{po_id}/register-existing", json={"order_sn": "NOPE"},
                    headers=H(seed.actor))
    assert r.status_code == 409 and r.json()["error"] == "order_not_found"


def test_register_existing_accepts_matching_order_atomically(client, seed, stuck):
    po_id, lid = stuck
    _late_order(seed, po_id)
    r = client.post(f"/v1/purchase-orders/{po_id}/register-existing", json={"order_sn": "FAKE-PO000001"},
                    headers=H(seed.actor))
    assert r.status_code == 200 and r.json()["outcome"] == "placed"
    assert client.get("/v1/attention").json()["items"] == []
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT state, order_sn FROM po WHERE po_id=%s", (po_id,))
        assert cur.fetchone() == ("已下单", "FAKE-PO000001")
        cur.execute("SELECT resolution, resolved_by, reason FROM po_attention WHERE po_id=%s", (po_id,))
        res, who, why = cur.fetchone()
        assert (res, who) == ("found_existing", seed.actor) and why
        cur.execute("SELECT count(*) FROM po_item_obs WHERE order_sn='FAKE-PO000001'")
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT state FROM plan_line WHERE line_id=%s", (lid,))
        assert cur.fetchone()[0] != "已提交"


def test_confirm_absent_needs_reason_then_unlocks_retry(client, seed, stuck):
    po_id, _ = stuck
    r = client.post(f"/v1/purchase-orders/{po_id}/confirm-absent", json={}, headers=H(seed.actor))
    assert r.status_code == 400 and r.json()["error"] == "reason_required"
    r = client.post(f"/v1/purchase-orders/{po_id}/confirm-absent", json={"reason": "已在领星后台按幂等键搜索，无此单"},
                    headers=H(seed.actor))
    assert r.status_code == 200
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT max(call_id) FROM erp_call WHERE po_id=%s AND endpoint='create_purchase_order'",
                    (po_id,))
        first = cur.fetchone()[0]
    tok = client.get(f"/v1/purchase-orders/{po_id}").json()["confirm_token"]
    again = client.post(f"/v1/purchase-orders/{po_id}/place", json={"confirm_token": tok}, headers=H(seed.actor))
    assert again.json()["outcome"] == "placed"
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT resolution, resolved_by, reason FROM po_attention WHERE po_id=%s", (po_id,))
        assert cur.fetchall() == [("confirmed_absent", seed.actor, "已在领星后台按幂等键搜索，无此单")]
        cur.execute("SELECT call_id, retry_of FROM erp_call WHERE po_id=%s AND endpoint='create_purchase_order'"
                    " AND call_id > %s", (po_id, first))
        rows = cur.fetchall()
        assert len(rows) == 1 and rows[0][1] == first       # 恰好一次新 create，链回上一次


def test_recheck_finds_order_created_late(client, seed, stuck):
    po_id, _ = stuck
    _late_order(seed, po_id)
    r = client.post(f"/v1/purchase-orders/{po_id}/recheck", headers=H(seed.actor))
    assert r.json()["outcome"] == "placed"
    assert client.get("/v1/attention").json()["items"] == []
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT resolution, resolved_by, reason FROM po_attention WHERE po_id=%s", (po_id,))
        res, who, why = cur.fetchone()
        assert (res, who) == ("rechecked_found", seed.actor) and why


def test_recheck_not_found_keeps_attention(client, seed, stuck):
    po_id, _ = stuck
    r = client.post(f"/v1/purchase-orders/{po_id}/recheck", headers=H(seed.actor))
    assert r.json()["outcome"] == "needs_verify"
    assert len(client.get("/v1/attention").json()["items"]) == 1


def test_resolutions_refuse_when_nothing_open(client, seed, stuck):
    po_id, _ = stuck
    client.post(f"/v1/purchase-orders/{po_id}/confirm-absent", json={"reason": "x"}, headers=H(seed.actor))
    for path, body in (("recheck", None), ("confirm-absent", {"reason": "x"}),
                       ("register-existing", {"order_sn": "A"})):
        r = client.post(f"/v1/purchase-orders/{po_id}/{path}", json=body, headers=H(seed.actor))
        assert r.status_code == 409 and r.json()["error"] == "nothing_to_resolve", path
