import json

import psycopg2
import pytest

from shared.pg_client import pg_conn
from tests.helpers import advance, mint


def _line(cur, seed, total, demand: dict):
    _, lid = mint(cur, seed.actor, seed.sku_a, total=total)
    cur.execute("UPDATE plan_line SET demand_by_seller=%s::jsonb WHERE line_id=%s",
                (json.dumps(demand), lid))
    return lid


def _draft(cur, seed, units):
    cur.execute("INSERT INTO supplier VALUES ('8085','01',now()) ON CONFLICT DO NOTHING")
    cur.execute("INSERT INTO po (supplier_id, purchaser_id, opt_uid, qc_type, created_by)"
                " VALUES ('8085',111,1,2,%s) RETURNING po_id", (seed.actor,))
    po_id = cur.fetchone()[0]
    cur.execute("INSERT INTO po_line (po_id, line_no, sku, wid, units, price)"
                " VALUES (%s,1,%s,%s,%s,1)", (po_id, seed.sku_a, seed.wid, units))
    return po_id


def test_seller_caps_largest_remainder(seed):
    # 24 × 12/23 = 12.52 · 24 × 11/23 = 11.48 → 13 / 11，合计恒等于 24
    with pg_conn() as c, c.cursor() as cur:
        lid = _line(cur, seed, 24, {"A": {"units": 12, "basis": "human"},
                                    "B": {"units": 11, "basis": "human"}})
        cur.execute("SELECT seller_id, cap FROM seller_caps(%s) ORDER BY 1", (lid,))
        assert cur.fetchall() == [("A", 13), ("B", 11)]


def test_seller_caps_empty_when_no_basis(seed):
    with pg_conn() as c, c.cursor() as cur:
        lid = _line(cur, seed, 24, {"A": {"units": 0, "basis": "human"}})
        cur.execute("SELECT count(*) FROM seller_caps(%s)", (lid,))
        assert cur.fetchone()[0] == 0
        lid2 = _line(cur, seed, 24, {"A": {"units": 5, "basis": "unknown"}})
        cur.execute("SELECT count(*) FROM seller_caps(%s)", (lid2,))
        assert cur.fetchone()[0] == 0


def test_over_seller_cap_is_rejected_at_commit(seed):
    with pytest.raises(psycopg2.Error) as e, pg_conn() as c, c.cursor() as cur:
        lid = _line(cur, seed, 24, {seed.seller_a: {"units": 12, "basis": "human"},
                                    "OTHER": {"units": 11, "basis": "human"}})
        po_id = _draft(cur, seed, 14)
        cur.execute("INSERT INTO po_source_map VALUES (%s,1,%s,%s,14)", (po_id, lid, seed.seller_a))
    assert e.value.diag.constraint_name == "po_over_seller_cap"


def test_no_seller_basis_is_rejected_at_commit(seed):
    with pytest.raises(psycopg2.Error) as e, pg_conn() as c, c.cursor() as cur:
        lid = _line(cur, seed, 24, {seed.seller_a: {"units": 0, "basis": "human"}})
        po_id = _draft(cur, seed, 5)
        cur.execute("INSERT INTO po_source_map VALUES (%s,1,%s,%s,5)", (po_id, lid, seed.seller_a))
    assert e.value.diag.constraint_name == "po_no_seller_basis"


def test_unplanned_units_need_a_reason(seed):
    with pytest.raises(psycopg2.Error) as e, pg_conn() as c, c.cursor() as cur:
        lid = _line(cur, seed, 24, {seed.seller_a: {"units": 10, "basis": "human"}})
        po_id = _draft(cur, seed, 30)       # 来源 24，超 6 件且没写理由
        cur.execute("INSERT INTO po_source_map VALUES (%s,1,%s,%s,24)", (po_id, lid, seed.seller_a))
    assert e.value.diag.constraint_name == "po_unplanned_reason_required"


def test_void_po_releases_quantity(seed):
    with pg_conn() as c, c.cursor() as cur:
        lid = _line(cur, seed, 24, {seed.seller_a: {"units": 10, "basis": "human"}})
        po_id = _draft(cur, seed, 24)
        cur.execute("INSERT INTO po_source_map VALUES (%s,1,%s,%s,24)", (po_id, lid, seed.seller_a))
        cur.execute("UPDATE po SET order_sn='FAKE-9', placed_mode='fake', placed_at=now() WHERE po_id=%s",
                    (po_id,))
        cur.execute("INSERT INTO po_event (po_id, from_state, to_state, actor, src)"
                    " VALUES (%s,'草稿','已下单',%s,'t')", (po_id, seed.actor))
        cur.execute("SELECT confirmed_units, ordered_units FROM v_plan_line_po_qty WHERE line_id=%s", (lid,))
        assert cur.fetchone() == (24, 24)
        cur.execute("INSERT INTO po_snapshot (order_sn, status, source, source_captured_at)"
                    " VALUES ('FAKE-9', -1, 'sim', now())")
        cur.execute("SELECT confirmed_units, ordered_units FROM v_plan_line_po_qty WHERE line_id=%s", (lid,))
        assert cur.fetchone() == (0, 0)


def test_check_line_state_reports_drift(seed):
    with pg_conn() as c, c.cursor() as cur:
        _, lid = mint(cur, seed.actor, seed.sku_a, total=10)
        advance(cur, lid, "已确认", actor=seed.actor)        # 已确认但 0 件组单
        cur.execute("SELECT line_id, state, derived FROM v_check_line_state")
        assert (lid, "已确认", "已提交") in cur.fetchall()


def test_check_po_landed_flags_quantity_drift(seed):
    with pg_conn() as c, c.cursor() as cur:
        lid = _line(cur, seed, 24, {seed.seller_a: {"units": 10, "basis": "human"}})
        po_id = _draft(cur, seed, 24)
        cur.execute("INSERT INTO po_source_map VALUES (%s,1,%s,%s,24)", (po_id, lid, seed.seller_a))
        cur.execute("UPDATE po SET order_sn='FAKE-7', placed_mode='fake', placed_at=now() WHERE po_id=%s",
                    (po_id,))
        cur.execute("INSERT INTO po_event (po_id, from_state, to_state, actor, src)"
                    " VALUES (%s,'草稿','已下单',%s,'t')", (po_id, seed.actor))
        cur.execute("SELECT problem FROM v_check_po_landed WHERE po_id=%s", (po_id,))
        assert cur.fetchall() == [("not_observed",)]
        cur.execute("INSERT INTO po_item_obs (order_sn, sku, wid, quantity_real, price, source,"
                    " source_captured_at) VALUES ('FAKE-7', %s, %s, 20, 1, 'sim', now())",
                    (seed.sku_a, seed.wid))
        cur.execute("SELECT problem, ours_units, lx_units FROM v_check_po_landed WHERE po_id=%s", (po_id,))
        assert cur.fetchall() == [("qty_or_price_differs", 24, 20)]
