"""C-a / C-b 验收（spec 2026-10-10 §11 · 2026-10-12）：计划 → 采购单下单 → 排货表（计划格 + 无计划格）→ 确认
→ 海外仓单提交领星备货单（已投送）→ 取消（领星单被删、作废留痕），每一跳之后 v_check_dispatch 全空；
采购单只建过一次。"""
from shared.pg_client import pg_conn
from tests.helpers import H, prepared
from tests.test_api_sheets import BOX, OVERSEA, FakeStock, packing_url, put, stock, sync_live  # noqa: F401


def _ok(client):
    body = client.get("/v1/checks/dispatch").json()
    assert body["ok"], body["violations"]


def test_plan_to_dispatch_draft_chain(client, seed, lingxing, stock):  # noqa: F811
    plan_id = prepared(client, seed)
    assert client.post(f"/v1/plans/{plan_id}/submit", headers=H(seed.actor)).status_code == 200
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO supplier VALUES ('8085','01',now()) ON CONFLICT DO NOTHING")
        cur.execute("INSERT INTO warehouse (wid, name, kind, market, refreshed_at)"
                    " VALUES (%s, '30-LA自有海外仓', 'oversea_self', 'US', now())", (OVERSEA,))
    line = client.get(f"/v1/plan-lines?plan_ids={plan_id}&orderable=1").json()["lines"][0]
    body = {"supplier_id": "8085", "purchaser_id": 111, "opt_uid": 10925905, "qc_type": 2,
            "lines": [{"sku": line["sku"], "wid": seed.wid, "units": line["remaining"], "price": "3.2",
                       "sources": [{"line_id": line["line_id"], "seller_id": seed.seller_a,
                                    "units": line["remaining"]}]}]}
    po_id = client.post("/v1/purchase-orders", json=body, headers=H(seed.actor)).json()["po_id"]
    tok = client.get(f"/v1/purchase-orders/{po_id}").json()["confirm_token"]
    assert client.post(f"/v1/purchase-orders/{po_id}/place", json={"confirm_token": tok},
                       headers=H(seed.actor)).json()["outcome"] == "placed"
    assert client.get(f"/v1/plan-lines/{line['line_id']}/trace").json()["state"] == "已下单"
    stock["s"] = FakeStock(buckets={(seed.sku_a, seed.wid): {"shared": 1000},
                                    (seed.sku_e, seed.wid): {"shared": 20}})
    sync_live(lingxing, stock["s"])            # CA-D12：确认前现读领星，替身看到同一份库存
    orders_before = len(lingxing._orders)

    plans = client.get("/v1/dispatch/plans").json()["plans"]
    assert [p["plan_id"] for p in plans] == [plan_id]          # CA-D11 入口：先选未完成的计划
    # ★ CA-D13：同一计划，平台仓 · FBA 表与海外仓表各一张，各自确认、各自提交，还差共用
    sid = client.post("/v1/sheets", json={"plan_id": plan_id, "route": "fba"}, headers=H(seed.actor)).json()["sheet_id"]
    osid = client.post("/v1/sheets", json={"plan_id": plan_id, "route": "oversea"},
                       headers=H(seed.actor)).json()["sheet_id"]
    g = client.get(f"/v1/sheets/{sid}/grid").json()
    row = next(r for r in g["demand"] if r["line_id"] == line["line_id"])
    assert row["purchase_orders"][0]["po_id"] == po_id
    assert [s["sid"] for s in row["stores"]] == sorted([seed.seller_a, seed.seller_b])   # 计划里的两家店
    _ok(client)

    assert put(client, seed, sid, line_id=line["line_id"], units=40).status_code == 200
    assert put(client, seed, osid, line_id=line["line_id"], units=10, channel="oversea",
               dest_wid=OVERSEA).status_code == 200
    assert put(client, seed, osid, seller_sku=seed.msku_e[0], sid=seed.msku_e[1], units=5,
               channel="oversea", dest_wid=OVERSEA).status_code == 200
    orow = next(r for r in client.get(f"/v1/sheets/{osid}/grid").json()["demand"] if r["line_id"] == line["line_id"])
    assert (orow["staged_here"], orow["staged_other"], orow["pending"]) == (10, 40, line["total_units"] - 50)
    _ok(client)
    assert client.put(packing_url(sid, seed), json=BOX, headers=H(seed.actor)).status_code == 200
    client.get("/v1/logistics-channels", headers=H(seed.actor))         # CB-D3：海外仓排货单要选物流方式（G7）
    assert client.put(f"/v1/sheets/{osid}/logistics/{OVERSEA}", json={"logistics_id": 28762},
                      headers=H(seed.actor)).status_code == 200
    for s_ in (sid, osid):
        seen = client.get(f"/v1/sheets/{s_}").json()["watermark"]
        r = client.post(f"/v1/sheets/{s_}/confirm", json={"seen_watermark": seen}, headers=H(seed.actor))
        assert r.status_code == 200, r.text
    _ok(client)
    s = client.get(f"/v1/sheets/{osid}").json()
    lines = {(ln["seller_sku"], ln["planned"], ln["unplanned"]) for c in s["consignments"] for ln in c["lines"]}
    assert lines == {("MSKU-A", 10, 0), ("MSKU-E", 0, 5)}
    fsub = client.get(f"/v1/sheets/{sid}/submission").json()
    # ★ C-c1：FBA 表也走领星（FBA 发货计划批次，参考号 SCM-FBA-…）
    assert [o["idem_key"][:8] for o in fsub["orders"]] == ["SCM-FBA-"]

    with pg_conn() as c, c.cursor() as cur:                              # C-b：备货单要领星产品 ID
        cur.execute("UPDATE sku_catalog SET product_id = 741806 WHERE sku=%s", (seed.sku_e,))
        cur.execute("UPDATE sku_catalog SET product_id = 741805 WHERE sku=%s", (seed.sku_a,))
    sub = client.get(f"/v1/sheets/{osid}/submission").json()
    assert sub["blockers"] == [] and [o["r_wid"] for o in sub["orders"]] == [OVERSEA]
    r = client.post(f"/v1/sheets/{osid}/submit", json={"confirm_token": sub["confirm_token"]}, headers=H(seed.actor))
    assert [x["outcome"] for x in r.json()["results"]] == ["created"], r.text
    assert [c["our_stage"] for c in client.get(f"/v1/sheets/{osid}").json()["consignments"]] == ["已投送"]
    assert [o["status"] for o in lingxing._ows.values()] == ["40"]
    _ok(client)

    r = client.post(f"/v1/sheets/{osid}/cancel", json={"reason": "验收"}, headers=H(seed.actor))
    assert r.status_code == 200 and len(r.json()["deleted_ows"]) == 1, r.text
    assert lingxing._ows == {}
    s = client.get(f"/v1/sheets/{osid}").json()
    assert {c["our_stage"] for c in s["consignments"]} == {"已取消"} and all(e["voided_at"] for e in s["ext_refs"])
    assert client.post(f"/v1/sheets/{sid}/cancel", json={"reason": "验收"}, headers=H(seed.actor)).status_code == 200
    _ok(client)
    s2 = client.post("/v1/sheets", json={"plan_id": plan_id, "route": "oversea"}, headers=H(seed.actor)).json()["sheet_id"]
    g2 = client.get(f"/v1/sheets/{s2}/grid").json()
    assert next(r for r in g2["demand"] if r["line_id"] == line["line_id"])["pending"] == line["total_units"]
    assert len(lingxing._orders) == orders_before, "排货不建采购单（现读只读库存）"
