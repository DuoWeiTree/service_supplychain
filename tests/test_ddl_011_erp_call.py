import psycopg2
import pytest

from shared.pg_client import pg_conn


@pytest.fixture
def draft(seed):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO supplier VALUES ('8085','01',now())")
        cur.execute("INSERT INTO po (supplier_id, purchaser_id, opt_uid, qc_type, created_by)"
                    " VALUES ('8085',111,1,2,%s) RETURNING po_id", (seed.actor,))
        return cur.fetchone()[0]


def _call(cur, po_id, actor, outcome="in_flight"):
    cur.execute(
        "INSERT INTO erp_call (endpoint, side_effect, mode, po_id, idem_key, actor, request, outcome)"
        " VALUES ('create_purchase_order','irreversible','fake',%s,'SCM-PO-1',%s,'{}',%s)"
        " RETURNING call_id", (po_id, actor, outcome))
    return cur.fetchone()[0]


def test_only_one_irreversible_call_in_flight_per_po(draft, seed):
    with pytest.raises(psycopg2.Error) as e, pg_conn() as c, c.cursor() as cur:
        _call(cur, draft, seed.actor)
        _call(cur, draft, seed.actor)
    assert e.value.diag.constraint_name == "erp_call_one_in_flight_per_po"


def test_erp_call_closes_once(draft, seed):
    with pg_conn() as c, c.cursor() as cur:
        cid = _call(cur, draft, seed.actor)
        cur.execute("UPDATE erp_call SET outcome='ok', finished_at=now() WHERE call_id=%s", (cid,))
    with pytest.raises(psycopg2.Error, match="只能收尾一次"), pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE erp_call SET outcome='rejected' WHERE call_id=%s", (cid,))


def test_erp_call_request_is_immutable(draft, seed):
    with pg_conn() as c, c.cursor() as cur:
        cid = _call(cur, draft, seed.actor)
    with pytest.raises(psycopg2.Error, match="只能收尾"), pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE erp_call SET request='{\"x\":1}' WHERE call_id=%s", (cid,))


def test_observations_are_append_only(business_db, wipe):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO po_item_obs (order_sn, lx_item_id, sku, wid, quantity_real, price,"
                    " source, source_captured_at) VALUES ('PO1','1','S',1,10,1,'sim',now())"
                    " RETURNING obs_id")
        oid = cur.fetchone()[0]
    with pytest.raises(psycopg2.Error, match="只追加"), pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE po_item_obs SET quantity_real=11 WHERE obs_id=%s", (oid,))


def test_one_open_attention_per_kind(draft, seed):
    with pytest.raises(psycopg2.Error) as e, pg_conn() as c, c.cursor() as cur:
        for _ in (1, 2):
            cur.execute("INSERT INTO po_attention (po_id, kind) VALUES (%s,'needs_verify')", (draft,))
    assert e.value.diag.constraint_name == "po_attention_one_open"
