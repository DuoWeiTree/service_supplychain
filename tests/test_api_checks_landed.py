"""G1 落地核对：领星落下的 ≡ 我们发的。领星侧的改动由测试替身直接造（PB-D14：产品里没有模拟入口）。"""
import json

import pytest

from shared.pg_client import pg_conn
from tests.helpers import H, mint
from usecase import landed


def _draft(client, seed):
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
    return po_id, tok


@pytest.fixture
def placed(client, seed):
    po_id, tok = _draft(client, seed)
    r = client.post(f"/v1/purchase-orders/{po_id}/place", json={"confirm_token": tok}, headers=H(seed.actor))
    assert r.json()["outcome"] == "placed", r.json()
    return po_id


def _order_sn(po_id):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT order_sn FROM po WHERE po_id=%s", (po_id,))
        return cur.fetchone()[0]


def test_clean_chain_has_no_violations(client, placed):
    body = client.get("/v1/checks/purchase").json()
    assert body["ok"] is True and body["violations"] == []


def test_lingxing_side_change_is_caught(client, seed, placed, lingxing):
    """Review Focus #5：领星落下的数被改了 —— 核对红、开待处理，不是「拿到单号就算对」。"""
    lingxing.mutate_item(_order_sn(placed), seed.sku_a, quantity_real=8)
    res = landed.verify(placed, seed.actor)
    assert res["checked"] is True and res["problems"][0]["problem"] == "qty_or_price_differs"
    checks = client.get("/v1/checks/purchase").json()
    assert checks["ok"] is False and checks["counts"]["po_landed"] == 1
    kinds = [i["kind"] for i in client.get("/v1/attention").json()["items"]]
    assert kinds == ["landed_mismatch"]


def test_landed_check_failure_never_breaks_placed(client, seed, monkeypatch):
    """Task 11 I3：落地核对炸了，已下单的单仍然是 placed，不是 500。"""

    def boom(po_id, actor):
        raise RuntimeError("verify boom")
    monkeypatch.setattr(landed, "verify", boom)
    from usecase import place_po
    place_po._first_check(1, "X", "a")   # 不抛即通过


def test_failed_read_is_not_reported_as_clean(client, seed, placed, lingxing):
    """查不了 ≠ 没有：读回失败 → checked=False、不开 landed_mismatch，旧的干净观测不冒充核对通过。"""
    lingxing.enqueue("purchase_order_list", "unknown_absent")
    lingxing.mutate_item(_order_sn(placed), seed.sku_a, quantity_real=8)
    b = landed.verify(placed, seed.actor)
    assert b["checked"] is False and b["problems"] == []
    assert b["read_outcome"] == "unknown" and b["read_cause"] and b["read_back_call"]
    assert client.get("/v1/attention").json()["items"] == []


def test_failed_first_check_keeps_placed_and_stays_not_observed(client, seed, lingxing):
    po_id, tok = _draft(client, seed)
    lingxing.enqueue("purchase_order_list", "unknown_absent")
    r = client.post(f"/v1/purchase-orders/{po_id}/place", json={"confirm_token": tok}, headers=H(seed.actor))
    assert r.status_code == 200 and r.json()["outcome"] == "placed"
    assert client.get("/v1/attention").json()["items"] == []
    checks = client.get("/v1/checks/purchase").json()
    assert checks["ok"] is False and checks["counts"]["po_landed"] == 1
