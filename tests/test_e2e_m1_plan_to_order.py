"""M1 验收（spec §11.4）：计划 → 候选 → 组单 → 下单（四种结果）→ 读回核对 → 反查，
每一跳之后 v_check_* 全空。"""
import pytest

from erp.drivers.fake import FAKE
from shared.pg_client import pg_conn
from tests.helpers import H, prepared


@pytest.fixture(autouse=True)
def clean_fake():
    FAKE.reset()
    yield
    FAKE.reset()


def _ok(client):
    body = client.get("/v1/checks/purchase").json()
    assert body["ok"], body["violations"]


def test_plan_to_order_chain(client, seed):
    plan_id = prepared(client, seed)                    # 阶段 A：建计划 → 填两种量
    assert client.post(f"/v1/plans/{plan_id}/submit", headers=H(seed.actor)).status_code == 200
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO supplier VALUES ('8085','01',now()) ON CONFLICT DO NOTHING")
    cands = client.get(f"/v1/plan-lines?plan_ids={plan_id}&orderable=1").json()["lines"]
    assert cands, "提交后应有可组单的记录"
    _ok(client)

    line = next(c for c in cands if c["blocked"] is None)
    sources = [{"line_id": line["line_id"], "seller_id": sid, "units": v["remaining"]}
               for sid, v in line["sellers"].items() if v["remaining"]]
    units = sum(s["units"] for s in sources)
    body = {"supplier_id": "8085", "purchaser_id": 111, "opt_uid": 10925905, "qc_type": 2,
            "lines": [{"sku": line["sku"], "wid": seed.wid, "units": units, "price": "3.2",
                       "sources": sources}]}
    po_id = client.post("/v1/purchase-orders", json=body, headers=H(seed.actor)).json()["po_id"]
    _ok(client)
    assert client.get(f"/v1/plan-lines/{line['line_id']}/trace").json()["state"] == "已确认"

    # 先业务拒绝，再结果未知且领星实际已建 —— 最终只有一张领星单
    FAKE.enqueue("create_purchase_order", "rejected")
    FAKE.enqueue("create_purchase_order", "unknown_created")
    tok = client.get(f"/v1/purchase-orders/{po_id}").json()["confirm_token"]
    assert client.post(f"/v1/purchase-orders/{po_id}/place", json={"confirm_token": tok},
                       headers=H(seed.actor)).json()["outcome"] == "rejected"
    r = client.post(f"/v1/purchase-orders/{po_id}/place", json={"confirm_token": tok}, headers=H(seed.actor))
    assert r.json()["outcome"] == "placed" and r.json()["found_by"] == "lookup"
    _ok(client)

    t = client.get(f"/v1/plan-lines/{line['line_id']}/trace").json()
    assert t["state"] == "已下单" and t["ordered_units"] == t["total_units"]
    assert t["purchase_orders"][0]["order_sn"].startswith("FAKE-PO")
    tl = client.get(f"/v1/purchase-orders/{po_id}/timeline").json()
    assert [a["outcome"] for a in tl["attempts"] if a["endpoint"] == "create_purchase_order"] == \
        ["rejected", "unknown"]
    assert client.get("/v1/attention").json()["count"] == 0
