import psycopg2
import pytest

from shared.pg_client import pg_conn
from tests.helpers import advance, mint


@pytest.fixture
def po_world(seed):
    """一个供应商、一个仓、一条已提交记录（seed 已灌 actor/sku/seller/warehouse）。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO supplier VALUES ('8085', '01[吉信德]', now())")
        _, line_id = mint(cur, seed.actor, seed.sku_a, total=100)
    return line_id


def _po(cur, actor, wid):
    cur.execute("INSERT INTO po (supplier_id, purchaser_id, opt_uid, qc_type, created_by)"
                " VALUES ('8085', 111, 10925905, 2, %s) RETURNING po_id, custom_order_sn",
                (actor,))
    return cur.fetchone()


def test_custom_order_sn_is_generated_from_po_id(po_world, seed):
    with pg_conn() as c, c.cursor() as cur:
        po_id, sn = _po(cur, seed.actor, seed.wid)
    assert sn == f"SCM-PO-{po_id}"


def test_new_po_starts_as_draft_with_birth_event(po_world, seed):
    with pg_conn() as c, c.cursor() as cur:
        po_id, _ = _po(cur, seed.actor, seed.wid)
        cur.execute("SELECT state FROM po WHERE po_id=%s", (po_id,))
        assert cur.fetchone()[0] == "草稿"
        cur.execute("SELECT from_state, to_state FROM po_event WHERE po_id=%s", (po_id,))
        assert cur.fetchall() == [("[*]", "草稿")]


def test_same_sku_same_wid_twice_in_one_po_is_rejected(po_world, seed):
    with pytest.raises(psycopg2.Error) as e, pg_conn() as c, c.cursor() as cur:
        po_id, _ = _po(cur, seed.actor, seed.wid)
        for n in (1, 2):
            cur.execute("INSERT INTO po_line (po_id, line_no, sku, wid, units, price)"
                        " VALUES (%s, %s, %s, %s, 10, 1)", (po_id, n, seed.sku_a, seed.wid))
    assert e.value.diag.constraint_name == "po_line_one_per_sku_wid"


def test_placing_requires_order_sn(po_world, seed):
    with pytest.raises(psycopg2.Error) as e, pg_conn() as c, c.cursor() as cur:
        po_id, _ = _po(cur, seed.actor, seed.wid)
        cur.execute("INSERT INTO po_event (po_id, from_state, to_state, actor, src)"
                    " VALUES (%s, '草稿', '已下单', %s, 't')", (po_id, seed.actor))
    assert e.value.diag.constraint_name == "po_placed_needs_order_sn"


def test_placed_po_is_frozen(po_world, seed):
    with pg_conn() as c, c.cursor() as cur:
        po_id, _ = _po(cur, seed.actor, seed.wid)
        cur.execute("INSERT INTO po_line (po_id, line_no, sku, wid, units, price)"
                    " VALUES (%s, 1, %s, %s, 10, 1)", (po_id, seed.sku_a, seed.wid))
        cur.execute("UPDATE po SET order_sn='FAKE-1', placed_mode='fake', placed_at=now()"
                    " WHERE po_id=%s", (po_id,))
        cur.execute("INSERT INTO po_event (po_id, from_state, to_state, actor, src)"
                    " VALUES (%s, '草稿', '已下单', %s, 't')", (po_id, seed.actor))
    with pytest.raises(psycopg2.Error) as e, pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE po_line SET units = 11 WHERE po_id=%s", (po_id,))
    assert e.value.diag.constraint_name == "po_frozen"
    # ★ 唯一例外：下单后回填领星行号
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE po_line SET lx_item_id='9001' WHERE po_id=%s", (po_id,))


def test_mapping_only_onto_orderable_lines(po_world, seed):
    with pg_conn() as c, c.cursor() as cur:
        _, done_line = mint(cur, seed.actor, seed.sku_a, total=5)
        advance(cur, done_line, "已撤销", actor=seed.actor)
    with pytest.raises(psycopg2.Error) as e, pg_conn() as c, c.cursor() as cur:
        po_id, _ = _po(cur, seed.actor, seed.wid)
        cur.execute("INSERT INTO po_line (po_id, line_no, sku, wid, units, price)"
                    " VALUES (%s, 1, %s, %s, 5, 1)", (po_id, seed.sku_a, seed.wid))
        cur.execute("INSERT INTO po_source_map VALUES (%s, 1, %s, %s, 5)",
                    (po_id, done_line, seed.seller_a))
    assert e.value.diag.constraint_name == "po_source_line_not_orderable"


def test_plan_line_has_new_back_edges(business_db):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT from_state, to_state FROM plan_line_transition WHERE from_state='已下单'"
                    " AND kind='back' ORDER BY to_state")
        assert cur.fetchall() == [("已下单", "已提交"), ("已下单", "已确认")]
