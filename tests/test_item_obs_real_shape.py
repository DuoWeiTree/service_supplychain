"""领星 purchaseOrderList 明细行号字段名：实测（刀0，2026-10-08）为 `id`，没有 `item_id`。"""
from repo import purchase as repo
from shared.pg_client import pg_conn

REAL_ITEM = {"id": 4242, "sku": "S1", "wid": 7, "quantity_real": 10, "price": "1.5", "is_delete": 0}


def test_item_obs_takes_lx_item_id_from_real_id_field(business_db, wipe):
    with pg_conn() as c, c.cursor() as cur:
        assert repo.insert_item_obs(cur, "PO-REAL", [REAL_ITEM], "sim", None) == 1
        cur.execute("SELECT lx_item_id FROM po_item_obs WHERE order_sn='PO-REAL'")
        assert cur.fetchone()[0] == "4242"
