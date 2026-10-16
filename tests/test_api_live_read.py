"""CA-D12：排货页数据源取较新 —— CH 作底 · 采购页领星读回 · 「现查领星」。★ 领星走替身，CH 走 stock 夹具。"""
from shared.pg_client import pg_conn
from tests.helpers import H
from tests.test_api_sheets import (  # noqa: F401
    BOX, FakeStock, new_sheet, packing_url, put, stock, world,
)


def placed_world(client, seed, lingxing):
    """把 world 的采购单换成经替身真下过单的形状（领星有它，读回能拿到行级到货）。"""
    from erp import gateway
    gateway.call("create_purchase_order", {"custom_order_sn": "SCM-PO-1", "sys_supplier_id": "8085", "sys_wid": seed.wid,
                                           "purchaser_id": 111, "opt_uid": 1, "qc_type": 2,
                                           "product_list": [{"sku": seed.sku_a, "quantity_real": 100, "price": "1"}]})
    sn = next(iter(lingxing._orders))
    order = lingxing._orders.pop(sn)
    order["order_sn"] = "PO-1"                     # world 里那张已下单采购单的领星单号
    lingxing._orders["PO-1"] = order
    return "PO-1"


def test_live_read_stock_is_newer_than_ch_and_wins(client, seed, world, stock, lingxing):
    sid = new_sheet(client, seed)
    lingxing.set_stock([(seed.wid, seed.sku_a, "0", 7), (seed.wid, seed.sku_a, "11072", 1), (2, seed.sku_a, "0", 50)])
    r = client.post(f"/v1/sheets/{sid}/live-read", json={"sku": seed.sku_a}, headers=H(seed.actor))
    assert r.status_code == 200, r.text
    assert r.json()["stock"]["buckets"] == [{"wid": seed.wid, "buckets": {"shared": 7, seed.seller_a: 1}}]
    # ★ 只按本地仓 wid 查，海外仓（wid 2）那行根本不会回来 —— 不进桶
    row = next(s for s in client.get(f"/v1/sheets/{sid}/grid").json()["stock"] if s["sku"] == seed.sku_a)
    assert row["buckets"] == {"shared": 7, seed.seller_a: 1} and row["source"] == "api"
    assert row["available"][seed.seller_a] == 8
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT endpoint, outcome FROM erp_call WHERE endpoint='local_inventory_details'")
        assert cur.fetchall() == [("local_inventory_details", "ok")]


def test_ch_row_is_kept_and_labelled_when_no_live_read(client, seed, world, stock):
    g = client.get(f"/v1/sheets/{new_sheet(client, seed)}/grid").json()
    row = next(s for s in g["stock"] if s["sku"] == seed.sku_a)
    assert row["source"] == "ch" and row["as_of"] == "2026-10-10T06:33:29+00:00"     # CH 的 DateTime 是 UTC


def test_live_read_failure_is_named_and_nothing_is_written(client, seed, world, stock, lingxing):
    sid = new_sheet(client, seed)
    lingxing.enqueue("local_inventory_details", "not_sent")
    r = client.post(f"/v1/sheets/{sid}/live-read", json={"sku": seed.sku_a}, headers=H(seed.actor))
    assert r.status_code == 200
    assert r.json()["stock"]["error"]["outcome"] == "not_sent"
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM stock_read")
        assert cur.fetchone()[0] == 0


def test_po_arrival_from_purchase_read_back_beats_older_ch(client, seed, world, stock, lingxing):
    """10-12 实测形状：采购页读回（领星）已全部入库，CH 还是昨天的 —— 排货页要用读回的。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO po_item_obs (order_sn, lx_item_id, sku, wid, quantity_real, source, source_captured_at,"
                    " quantity_entry, quantity_receive) VALUES ('PO-1','1',%s,%s,100,'api',now(),100,0)", (seed.sku_a, seed.wid))
    po = client.get(f"/v1/sheets/{new_sheet(client, seed)}/grid").json()["demand"][0]["purchase_orders"][0]
    assert (po["entered"], po["pending"], po["source"]) == (100, 0, "api")


def test_ch_progress_wins_when_newer_than_read_back(client, seed, world, stock):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO po_item_obs (order_sn, lx_item_id, sku, wid, quantity_real, source, source_captured_at,"
                    " quantity_entry, quantity_receive) VALUES ('PO-1','1',%s,%s,100,'api','2026-10-01',1,99)", (seed.sku_a, seed.wid))
    po = client.get(f"/v1/sheets/{new_sheet(client, seed)}/grid").json()["demand"][0]["purchase_orders"][0]
    assert (po["entered"], po["source"]) == (85, "ch")


def test_live_read_refreshes_linked_placed_pos(client, seed, world, stock, lingxing):
    sn = placed_world(client, seed, lingxing)
    lingxing.set_arrival(sn, seed.sku_a, 60)
    lingxing.set_stock([(seed.wid, seed.sku_a, "0", 60)])
    sid = new_sheet(client, seed)
    r = client.post(f"/v1/sheets/{sid}/live-read", json={"sku": seed.sku_a}, headers=H(seed.actor)).json()
    assert [(p["order_sn"], p["outcome"]) for p in r["purchase_orders"]] == [(sn, "ok")]
    po = client.get(f"/v1/sheets/{sid}/grid").json()["demand"][0]["purchase_orders"][0]
    assert (po["entered"], po["pending"], po["source"]) == (60, 40, "api")


def test_confirm_rereads_live_stock_first(client, seed, world, stock, lingxing):
    """CH 说够（85），领星现读只剩 3 件 → G3 按现读判，不过。"""
    sid = new_sheet(client, seed)
    put(client, seed, sid, line_id=world["line_id"], units=30)
    client.put(packing_url(sid, seed), json=BOX, headers=H(seed.actor))
    lingxing.set_stock([(seed.wid, seed.sku_a, "0", 3)])
    seen = client.get(f"/v1/sheets/{sid}").json()["watermark"]
    r = client.post(f"/v1/sheets/{sid}/confirm", json={"seen_watermark": seen}, headers=H(seed.actor))
    assert r.status_code == 409, r.text
    g3 = next(g for g in r.json()["gates"] if g["gate"] == "G3")
    assert g3["ok"] is False and g3["detail"][0]["short"] == 27


def test_confirm_live_read_failure_is_503_not_silent_fallback(client, seed, world, stock, lingxing):
    sid = new_sheet(client, seed)
    put(client, seed, sid, line_id=world["line_id"], units=30)
    client.put(packing_url(sid, seed), json=BOX, headers=H(seed.actor))
    lingxing.enqueue("local_inventory_details", "not_sent")
    seen = client.get(f"/v1/sheets/{sid}").json()["watermark"]
    r = client.post(f"/v1/sheets/{sid}/confirm", json={"seen_watermark": seen}, headers=H(seed.actor))
    assert r.status_code == 503 and r.json()["error"] == "stock_unavailable"
    assert r.json()["target"].endswith("inventoryDetails")
