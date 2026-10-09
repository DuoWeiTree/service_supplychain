"""M1 验收（spec §11.4）：计划 → 候选 → 组单 → 下单（四种结果）→ 读回核对 → 反查，
每一跳之后 v_check_* 全空。"""

from shared.pg_client import pg_conn
from tests.helpers import H, prepared


def _ok(client):
    body = client.get("/v1/checks/purchase").json()
    assert body["ok"], body["violations"]


def test_plan_to_order_chain(client, seed, lingxing):
    plan_id = prepared(client, seed)                    # 阶段 A：建计划 → 填两种量
    assert client.post(f"/v1/plans/{plan_id}/submit", headers=H(seed.actor)).status_code == 200
    _ok(client)
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO supplier VALUES ('8085','01',now()) ON CONFLICT DO NOTHING")
    cands = client.get(f"/v1/plan-lines?plan_ids={plan_id}&orderable=1").json()["lines"]
    assert cands, "提交后应有可组单的记录"
    _ok(client)

    line = next(cand for cand in cands if cand["blocked"] is None)
    sources = [{"line_id": line["line_id"], "seller_id": sid, "units": v["remaining"]}
               for sid, v in line["sellers"].items() if v["remaining"]]
    assert len(sources) >= 2, sources                   # 两个店铺，按店余量真的被用到
    units = sum(s["units"] for s in sources)
    assert units > 0
    body = {"supplier_id": "8085", "purchaser_id": 111, "opt_uid": 10925905, "qc_type": 2,
            "lines": [{"sku": line["sku"], "wid": seed.wid, "units": units, "price": "3.2",
                       "sources": sources}]}
    po_id = client.post("/v1/purchase-orders", json=body, headers=H(seed.actor)).json()["po_id"]
    _ok(client)
    assert client.get(f"/v1/plan-lines/{line['line_id']}/trace").json()["state"] == "已确认"

    # 先业务拒绝，再结果未知且领星实际已建 —— 最终只有一张领星单
    lingxing.enqueue("create_purchase_order", "rejected")
    lingxing.enqueue("create_purchase_order", "unknown_created")
    tok = client.get(f"/v1/purchase-orders/{po_id}").json()["confirm_token"]
    r0 = client.post(f"/v1/purchase-orders/{po_id}/place", json={"confirm_token": tok},
                     headers=H(seed.actor))
    assert r0.status_code == 200 and r0.json()["outcome"] == "rejected"
    po = client.get(f"/v1/purchase-orders/{po_id}").json()
    assert po["state"] == "草稿" and po["order_sn"] is None
    assert client.get(f"/v1/plan-lines/{line['line_id']}/trace").json()["state"] == "已确认"
    _ok(client)
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
    assert len(lingxing._orders) == 1                       # 领星侧只有一张，不看我们自己的日志
    assert next(iter(lingxing._orders)) == t["purchase_orders"][0]["order_sn"]
    _ok(client)
