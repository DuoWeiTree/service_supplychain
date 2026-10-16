"""领星 purchaseOrderList 明细行号字段名：实测（刀0，2026-10-08）为 `id`；CH 来源观测（M3）用 `item_id`。"""
import logging

import pytest

from repo import purchase as repo
from shared.pg_client import pg_conn


def real_item(seed):
    return {"id": 4242, "sku": seed.sku_a, "wid": seed.wid, "quantity_real": 10, "price": "1.5", "is_delete": 0}


@pytest.fixture
def po_line(seed):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO supplier VALUES ('8085','01',now())")
        cur.execute("INSERT INTO po (supplier_id, purchaser_id, opt_uid, qc_type, created_by)"
                    " VALUES ('8085',111,1,2,%s) RETURNING po_id", (seed.actor,))
        po_id = cur.fetchone()[0]
        cur.execute("INSERT INTO po_line (po_id, line_no, sku, wid, units, price, unplanned_reason)"
                    " VALUES (%s, 1, %s, %s, 10, 1, 't')", (po_id, seed.sku_a, seed.wid))
    return po_id


def _line_id(po_id):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT lx_item_id FROM po_line WHERE po_id=%s", (po_id,))
        return cur.fetchone()[0]


def test_item_obs_takes_lx_item_id_from_real_id_field(business_db, wipe, seed):
    with pg_conn() as c, c.cursor() as cur:
        assert repo.insert_item_obs(cur, "PO-REAL", [real_item(seed)], "api", None) == 1
        cur.execute("SELECT lx_item_id FROM po_item_obs WHERE order_sn='PO-REAL'")
        assert cur.fetchone()[0] == "4242"


def test_backfill_real_shape_id(po_line, seed):
    with pg_conn() as c, c.cursor() as cur:
        assert repo.backfill_item_ids(cur, po_line, [real_item(seed)]) == 1
    assert _line_id(po_line) == "4242"


def test_backfill_ch_shape_item_id(po_line, seed):
    it = {k: v for k, v in real_item(seed).items() if k != "id"} | {"item_id": 99}
    with pg_conn() as c, c.cursor() as cur:
        assert repo.backfill_item_ids(cur, po_line, [it]) == 1
    assert _line_id(po_line) == "99"


def test_missing_id_neither_raises_nor_writes_none_string(po_line, seed, caplog):
    it = {k: v for k, v in real_item(seed).items() if k != "id"}
    with caplog.at_level(logging.WARNING), pg_conn() as c, c.cursor() as cur:
        assert repo.backfill_item_ids(cur, po_line, [it]) == 0
        assert repo.insert_item_obs(cur, "PO-NOID", [it], "api", None) == 1
        cur.execute("SELECT lx_item_id FROM po_item_obs WHERE order_sn='PO-NOID'")
        assert cur.fetchone()[0] is None
    assert _line_id(po_line) is None
    assert any(f"po_id={po_line}" in m and "skipped_no_id=1" in m for m in caplog.messages)


def test_item_obs_keeps_per_item_entered_and_pending(business_db, wipe, seed):
    """CA-D12：领星 item_list 行级有 quantity_entry（已入库）/ quantity_receive（待到货，CA-D9 口径）。
    以前只存 quantity_real，排货页拿不到采购页已经读回的到货 —— 10-12 PO261012001 实测。"""
    it = {**real_item(seed), "quantity_entry": 7, "quantity_receive": 3}
    old = {k: v for k, v in real_item(seed).items()}            # 老形状：没有这两个键 → NULL（未知），不是 0
    with pg_conn() as c, c.cursor() as cur:
        repo.insert_item_obs(cur, "PO-E", [it], "api", None)
        repo.insert_item_obs(cur, "PO-OLD", [old], "api", None)
        cur.execute("SELECT order_sn, quantity_entry, quantity_receive FROM po_item_obs ORDER BY order_sn")
        assert cur.fetchall() == [("PO-E", 7, 3), ("PO-OLD", None, None)]
