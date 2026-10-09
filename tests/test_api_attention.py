import json

import pytest

from erp import gateway
from shared.pg_client import pg_conn
from tests.helpers import H, mint


@pytest.fixture
def stuck(client, seed, lingxing):
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
    lingxing.enqueue("create_purchase_order", "unknown_absent")
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
    assert (a["endpoint"], a["outcome"]) == ("create_purchase_order", "unknown") and "mode" not in a
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


# ---- fix round 1 ----

def _register(client, seed, po_id, sn="FAKE-PO000001"):
    return client.post(f"/v1/purchase-orders/{po_id}/register-existing", json={"order_sn": sn},
                       headers=H(seed.actor))


def test_last_lookup_ignores_register_lookups(client, seed, stuck):
    po_id, _ = stuck
    idem_lookup = client.get(f"/v1/purchase-orders/{po_id}/timeline").json()["attempts"][1]["call_id"]
    assert _register(client, seed, po_id, "NOPE").status_code == 409     # 留下一条窄查询
    t = client.get(f"/v1/purchase-orders/{po_id}/timeline").json()
    assert t["attempts"][-1]["request"].get("order_sns") == "NOPE"
    assert t["conclusion"]["last_lookup"]["call_id"] == idem_lookup
    assert t["conclusion"]["last_lookup"]["window"]["start_date"]


def test_confirm_absent_refused_while_lookup_in_flight(client, seed, stuck):
    po_id, _ = stuck
    from repo import purchase as repo
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT 1")
        repo.insert_call(cur, endpoint="purchase_order_list", side_effect="read", po_id=po_id,
                         idem_key=None, actor=seed.actor, request={"search_field_time": "create_time"})
    r = client.post(f"/v1/purchase-orders/{po_id}/confirm-absent", json={"reason": "x"}, headers=H(seed.actor))
    assert r.status_code == 409 and r.json()["error"] == "lookup_in_progress"
    assert len(client.get("/v1/attention").json()["items"]) == 1


def test_second_order_for_placed_po_opens_landed_mismatch(client, seed, stuck):
    po_id, _ = stuck
    from usecase import place_po
    _late_order(seed, po_id)
    assert _register(client, seed, po_id).status_code == 200
    _late_order(seed, po_id)                                              # FAKE-PO000002，同幂等键
    res = place_po.lookup_and_settle(po_id, seed.actor, None, "t")
    assert res["order_sn"] == "FAKE-PO000001" and res["landed_mismatch"] == "FAKE-PO000002"
    items = client.get("/v1/attention").json()["items"]
    assert [(i["po_id"], i["kind"]) for i in items] == [(po_id, "landed_mismatch")]
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT detail FROM po_attention WHERE po_id=%s AND kind='landed_mismatch'", (po_id,))
        d = cur.fetchone()[0]
        assert (d["recorded_order_sn"], d["also_found_order_sn"]) == ("FAKE-PO000001", "FAKE-PO000002")
        cur.execute("SELECT order_sn FROM po WHERE po_id=%s", (po_id,))
        assert cur.fetchone()[0] == "FAKE-PO000001"


def test_lookup_after_confirm_absent_does_not_reopen(client, seed, stuck):
    po_id, _ = stuck
    from usecase import place_po
    tl = client.get(f"/v1/purchase-orders/{po_id}/timeline").json()
    create_id = tl["attempts"][0]["call_id"]
    assert client.post(f"/v1/purchase-orders/{po_id}/confirm-absent", json={"reason": "x"},
                       headers=H(seed.actor)).status_code == 200
    place_po.lookup_and_settle(po_id, seed.actor, create_id, "t")        # 迟到的旧查询：没找到
    assert client.get("/v1/attention").json()["items"] == []


def test_register_compares_wid_and_price(client, seed, stuck, lingxing):
    po_id, _ = stuck
    _late_order(seed, po_id)
    lingxing.mutate_item("FAKE-PO000001", seed.sku_a, price="3")
    r = _register(client, seed, po_id)
    assert r.status_code == 409 and r.json()["error"] == "order_mismatch" and "items" in r.json()["mismatch"]
    lingxing.mutate_item("FAKE-PO000001", seed.sku_a, price="2")
    lingxing._orders["FAKE-PO000001"]["item_list"][0]["wid"] = "99999"
    r = _register(client, seed, po_id)
    assert r.status_code == 409 and "items" in r.json()["mismatch"]


def test_register_non_integer_quantity_is_mismatch_not_500(client, seed, stuck, lingxing):
    po_id, _ = stuck
    _late_order(seed, po_id)
    lingxing.mutate_item("FAKE-PO000001", seed.sku_a, quantity_real="abc")
    r = _register(client, seed, po_id)
    assert r.status_code == 409 and r.json()["error"] == "order_mismatch"


def test_register_refuses_voided_order(client, seed, stuck, lingxing):
    po_id, _ = stuck
    _late_order(seed, po_id)
    lingxing._orders["FAKE-PO000001"]["status"] = -1
    r = _register(client, seed, po_id)
    assert r.status_code == 409 and r.json()["error"] == "order_voided"
    assert len(client.get("/v1/attention").json()["items"]) == 1


def test_register_lookup_failure_is_not_order_not_found(client, seed, stuck, lingxing):
    po_id, _ = stuck
    lingxing.enqueue("purchase_order_list", "not_sent")
    r = _register(client, seed, po_id)
    assert r.status_code == 502 and r.json()["error"] == "lookup_failed"
    assert r.json()["lookup_outcome"] == "not_sent" and "ConnectionRefusedError" in r.json()["cause"]


def test_resolutions_on_missing_po_are_404(client, seed):
    for path, body in (("recheck", None), ("confirm-absent", {"reason": "x"}),
                       ("register-existing", {"order_sn": "A"})):
        r = client.post(f"/v1/purchase-orders/999999/{path}", json=body, headers=H(seed.actor))
        assert r.status_code == 404 and r.json()["error"] == "po_not_found", path


# ---- 终审修复波 ----

def test_confirm_absent_not_blocked_by_stale_read_call(client, seed, stuck):
    """#2：卡了超过 V5 的读调用不算「查询进行中」。"""
    po_id, _ = stuck
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO erp_call (endpoint, side_effect, po_id, actor, request, started_at)"
                    " VALUES ('purchase_order_list','read',%s,%s,'{}', now() - interval '1 hour')",
                    (po_id, seed.actor))
    r = client.post(f"/v1/purchase-orders/{po_id}/confirm-absent", json={"reason": "x"}, headers=H(seed.actor))
    assert r.status_code == 200, r.text


@pytest.fixture
def mismatched(client, seed, lingxing):
    """一张已下单、领星侧被改了数量、落地核对开了 landed_mismatch 的单。"""
    from usecase import landed
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
    sn = client.post(f"/v1/purchase-orders/{po_id}/place", json={"confirm_token": tok},
                     headers=H(seed.actor)).json()["order_sn"]
    lingxing.mutate_item(sn, seed.sku_a, quantity_real=7)
    assert landed.verify(po_id, seed.actor)["problems"]
    return po_id


def test_landed_mismatch_has_a_conclusion_and_can_be_acknowledged(client, seed, mismatched):
    """#3：landed_mismatch 原来没有任何出口 —— 退回边必须真退得回去。"""
    po_id = mismatched
    cc = client.get(f"/v1/purchase-orders/{po_id}/timeline").json()["conclusion"]
    assert cc["kind"] == "landed_mismatch" and cc["known"] and cc["unknown"]
    assert cc["facts"]["problems"] and cc["facts"]["read_outcome"] == "ok"
    url = f"/v1/purchase-orders/{po_id}/acknowledge-mismatch"
    r = client.post(url, json={"reason": "  "}, headers=H(seed.actor))
    assert r.status_code == 400 and r.json()["error"] == "reason_required"
    r = client.post(url, json={"reason": "供应商电话确认改为 7 件，已知悉"}, headers=H(seed.actor))
    assert r.status_code == 200, r.text
    assert client.get("/v1/attention").json()["items"] == []
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT resolution, resolved_by, reason FROM po_attention WHERE po_id=%s", (po_id,))
        assert cur.fetchall() == [("mismatch_acknowledged", seed.actor, "供应商电话确认改为 7 件，已知悉")]
    r = client.post(url, json={"reason": "again"}, headers=H(seed.actor))
    assert r.status_code == 409 and r.json()["error"] == "nothing_to_resolve"


def test_acknowledge_mismatch_on_missing_po_is_404(client, seed):
    r = client.post("/v1/purchase-orders/999999/acknowledge-mismatch", json={"reason": "x"}, headers=H(seed.actor))
    assert r.status_code == 404 and r.json()["error"] == "po_not_found"


def test_acknowledge_mismatch_does_not_touch_needs_verify(client, seed, stuck):
    po_id, _ = stuck
    r = client.post(f"/v1/purchase-orders/{po_id}/acknowledge-mismatch", json={"reason": "x"},
                    headers=H(seed.actor))
    assert r.status_code == 409 and r.json()["error"] == "nothing_to_resolve"
    assert len(client.get("/v1/attention").json()["items"]) == 1
