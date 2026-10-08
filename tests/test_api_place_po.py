import json

import pytest

from erp.drivers.fake import FAKE
from shared.pg_client import pg_conn
from tests.helpers import H, mint


@pytest.fixture(autouse=True)
def clean_fake():
    FAKE.reset()
    yield
    FAKE.reset()


@pytest.fixture
def draft(client, seed):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO supplier VALUES ('8085','01',now())")
        _, lid = mint(cur, seed.actor, seed.sku_a, total=10)
        cur.execute("UPDATE plan_line SET demand_by_seller=%s::jsonb WHERE line_id=%s",
                    (json.dumps({seed.seller_a: {"units": 5, "basis": "human"}}), lid))
    body = {"supplier_id": "8085", "purchaser_id": 111, "opt_uid": 10925905, "qc_type": 2,
            "lines": [{"sku": seed.sku_a, "wid": seed.wid, "units": 10, "price": "2",
                       "sources": [{"line_id": lid, "seller_id": seed.seller_a, "units": 10}]}]}
    po_id = client.post("/v1/purchase-orders", json=body, headers=H(seed.actor)).json()["po_id"]
    return po_id, lid


def _place(client, seed, po_id, token=None):
    token = token or client.get(f"/v1/purchase-orders/{po_id}").json()["confirm_token"]
    return client.post(f"/v1/purchase-orders/{po_id}/place", json={"confirm_token": token},
                       headers=H(seed.actor))


def _line_state(lid):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT state FROM plan_line WHERE line_id=%s", (lid,))
        return cur.fetchone()[0]


def _calls(po_id):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT endpoint, outcome, retry_of IS NOT NULL FROM erp_call WHERE po_id=%s ORDER BY call_id",
                    (po_id,))
        return cur.fetchall()


def test_ok_freezes_po_and_moves_line_to_ordered(client, seed, draft):
    po_id, lid = draft
    r = _place(client, seed, po_id)
    assert r.status_code == 200 and r.json()["outcome"] == "placed"
    po = client.get(f"/v1/purchase-orders/{po_id}").json()
    assert po["state"] == "已下单" and po["order_sn"].startswith("FAKE-PO") and po["placed_mode"] == "fake"
    assert po["lines"][0]["lx_item_id"]                     # 读回首检回填了行号
    assert _line_state(lid) == "已下单"
    assert _calls(po_id) == [("create_purchase_order", "ok", False), ("purchase_order_list", "ok", False)]


def test_stale_token_refused(client, seed, draft):
    po_id, _ = draft
    r = _place(client, seed, po_id, token="nope")
    assert r.status_code == 409 and r.json()["error"] == "stale_confirm_token"


def test_rejected_keeps_draft_and_echoes_lingxing(client, seed, draft):
    po_id, lid = draft
    FAKE.enqueue("create_purchase_order", "rejected")
    r = _place(client, seed, po_id)
    assert r.json() | {"erp_call_id": None} == {"outcome": "rejected", "po_id": po_id, "lx_code": "1001",
                                                "lx_msg": "fake: 供应商不存在", "erp_call_id": None}
    assert client.get(f"/v1/purchase-orders/{po_id}").json()["state"] == "草稿"
    assert _line_state(lid) == "已确认"


def test_delete_after_attempt_discards_and_keeps_evidence(client, seed, draft):
    po_id, lid = draft
    FAKE.enqueue("create_purchase_order", "rejected")
    _place(client, seed, po_id)
    assert client.delete(f"/v1/purchase-orders/{po_id}", headers=H(seed.actor)).status_code == 204
    po = client.get(f"/v1/purchase-orders/{po_id}").json()
    assert po["discarded_at"] and po["lines"] == []
    assert _line_state(lid) == "已提交"                    # 额度释放
    assert _calls(po_id) == [("create_purchase_order", "rejected", False)]   # 证据还在


def test_retry_after_not_sent_is_chained(client, seed, draft):
    po_id, _ = draft
    FAKE.enqueue("create_purchase_order", "not_sent")
    _place(client, seed, po_id)
    _place(client, seed, po_id)
    creates = [c for c in _calls(po_id) if c[0] == "create_purchase_order"]
    assert creates == [("create_purchase_order", "not_sent", False), ("create_purchase_order", "ok", True)]


def test_not_sent_is_safe_to_retry(client, seed, draft):
    po_id, _ = draft
    FAKE.enqueue("create_purchase_order", "not_sent")
    assert _place(client, seed, po_id).json()["outcome"] == "not_sent"
    assert _place(client, seed, po_id).json()["outcome"] == "placed"


def test_unknown_but_created_is_found_by_lookup(client, seed, draft):
    """Review Focus #2：发出去了没回音，但领星建了单 —— 查重找回，不重下。"""
    po_id, lid = draft
    FAKE.enqueue("create_purchase_order", "unknown_created")
    r = _place(client, seed, po_id)
    assert r.json()["outcome"] == "placed" and r.json()["found_by"] == "lookup"
    assert _line_state(lid) == "已下单"
    assert [c[0] for c in _calls(po_id)].count("create_purchase_order") == 1


def test_unknown_absent_goes_to_needs_verify_and_blocks_everything(client, seed, draft):
    """Review Focus #2 / #4：查不到 → 转人工；此后改 / 删 / 再下单一律 409 needs_verify。"""
    po_id, _ = draft
    FAKE.enqueue("create_purchase_order", "unknown_absent")
    r = _place(client, seed, po_id)
    assert r.json()["outcome"] == "needs_verify"
    po = client.get(f"/v1/purchase-orders/{po_id}").json()
    assert po["state"] == "草稿" and [a["kind"] for a in po["attention"]] == ["needs_verify"]
    again = _place(client, seed, po_id)
    assert again.status_code == 409 and again.json()["error"] == "needs_verify"
    assert client.delete(f"/v1/purchase-orders/{po_id}", headers=H(seed.actor)).json()["error"] == "needs_verify"
    assert [c[0] for c in _calls(po_id)].count("create_purchase_order") == 1


def test_stuck_in_flight_is_recovered_by_lookup(client, seed, draft):
    """Review Focus #3：领星成功、本地还没记账就被杀 —— 启动恢复按幂等键找回。"""
    from usecase import place_po
    po_id, lid = draft
    # 造现场：领星侧已有单，本地只有一条 in_flight
    FAKE.enqueue("create_purchase_order", "unknown_created")
    from erp import gateway
    gateway.call("create_purchase_order", {"custom_order_sn": f"SCM-PO-{po_id}", "supplier_id": 8085,
                                           "purchaser_id": 111, "opt_uid": 1, "qc_type": 2, "wid": seed.wid,
                                           "product_list": [{"sku": seed.sku_a, "wid": seed.wid,
                                                             "quantity_real": 10, "price": "2"}]})
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO erp_call (endpoint, side_effect, mode, po_id, idem_key, actor, request,"
                    " started_at) VALUES ('create_purchase_order','irreversible','fake',%s,%s,%s,'{}',"
                    " now() - interval '1 hour')", (po_id, f"SCM-PO-{po_id}", seed.actor))
    out = place_po.recover_stuck()
    assert [o["po_id"] for o in out] == [po_id]
    assert client.get(f"/v1/purchase-orders/{po_id}").json()["state"] == "已下单"
    assert _line_state(lid) == "已下单"


def test_placing_a_discarded_draft_is_po_discarded(client, seed, draft):
    """控制者裁定 1：作废草稿（明细已清）不能落到 preflight_failed 之类的次生错误上。"""
    po_id, _ = draft
    FAKE.enqueue("create_purchase_order", "rejected")
    _place(client, seed, po_id)
    client.delete(f"/v1/purchase-orders/{po_id}", headers=H(seed.actor))
    r = _place(client, seed, po_id)
    assert r.status_code == 409 and r.json()["error"] == "po_discarded"
    assert [c[0] for c in _calls(po_id)].count("create_purchase_order") == 1
