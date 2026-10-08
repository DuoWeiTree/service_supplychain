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
    with pytest.raises(psycopg2.Error, match="请求侧不可改"), pg_conn() as c, c.cursor() as cur:
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


def _open_attention(cur, po_id):
    cur.execute("INSERT INTO po_attention (po_id, kind) VALUES (%s,'needs_verify') RETURNING attention_id",
                (po_id,))
    return cur.fetchone()[0]


def test_erp_call_delete_is_rejected(draft, seed):
    with pg_conn() as c, c.cursor() as cur:
        cid = _call(cur, draft, seed.actor)
    with pytest.raises(psycopg2.Error, match="不许删除"), pg_conn() as c, c.cursor() as cur:
        cur.execute("DELETE FROM erp_call WHERE call_id=%s", (cid,))


def test_erp_call_valid_close_persists_result_columns(draft, seed):
    with pg_conn() as c, c.cursor() as cur:
        cid = _call(cur, draft, seed.actor)
        cur.execute(
            "UPDATE erp_call SET outcome='rejected', http_status=200, lx_code='4001', lx_msg='bad',"
            " cause='ECONNRESET', response='{\"r\":1}', host='h.example', path='/erp/x',"
            " elapsed_ms=42, finished_at=now() WHERE call_id=%s", (cid,))
        cur.execute("SELECT outcome, http_status, lx_code, lx_msg, cause, response, host, path,"
                    " elapsed_ms, finished_at IS NOT NULL FROM erp_call WHERE call_id=%s", (cid,))
        assert cur.fetchone() == ("rejected", 200, "4001", "bad", "ECONNRESET", {"r": 1},
                                  "h.example", "/erp/x", 42, True)


def test_attention_full_resolve_succeeds(draft, seed):
    with pg_conn() as c, c.cursor() as cur:
        aid = _open_attention(cur, draft)
        cur.execute("UPDATE po_attention SET resolved_at=now(), resolution='confirmed_absent',"
                    " resolved_by=%s, reason='r' WHERE attention_id=%s", (seed.actor, aid))
        cur.execute("SELECT resolution, resolved_by FROM po_attention WHERE attention_id=%s", (aid,))
        assert cur.fetchone() == ("confirmed_absent", seed.actor)


@pytest.mark.parametrize("set_clause", [
    "resolved_at=now(), resolved_by='alice'",
    "resolved_at=now(), resolution='confirmed_absent'",
    "resolution='confirmed_absent', resolved_by='alice'",
])
def test_attention_partial_resolve_rejected(draft, seed, set_clause):
    with pg_conn() as c, c.cursor() as cur:
        aid = _open_attention(cur, draft)
    with pytest.raises(psycopg2.Error, match="必须同时给出"), pg_conn() as c, c.cursor() as cur:
        cur.execute(f"UPDATE po_attention SET {set_clause} WHERE attention_id=%s", (aid,))


def test_attention_double_resolve_rejected(draft, seed):
    with pg_conn() as c, c.cursor() as cur:
        aid = _open_attention(cur, draft)
        cur.execute("UPDATE po_attention SET resolved_at=now(), resolution='confirmed_absent',"
                    " resolved_by=%s WHERE attention_id=%s", (seed.actor, aid))
    with pytest.raises(psycopg2.Error, match="已处理，不可再改"), pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE po_attention SET reason='x' WHERE attention_id=%s", (aid,))


@pytest.mark.parametrize("col_set", [
    "kind='landed_mismatch'",
    "opened_at=now() - interval '1 day'",
    "detail='{\"x\":1}'",
    "po_id=(SELECT po_id FROM po_other)",
    "opened_by_call=(SELECT call_id FROM erp_call LIMIT 1)",
])
def test_attention_resolve_freezes_identity_columns(draft, seed, col_set):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO po (supplier_id, purchaser_id, opt_uid, qc_type, created_by)"
                    " VALUES ('8085',111,1,2,%s) RETURNING po_id", (seed.actor,))
        other = cur.fetchone()[0]
        _call(cur, draft, seed.actor)
        aid = _open_attention(cur, draft)
    col_set = col_set.replace("(SELECT po_id FROM po_other)", str(other))
    with pytest.raises(psycopg2.Error, match="身份列不可改"), pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE po_attention SET resolved_at=now(), resolution='confirmed_absent',"
                    f" resolved_by=%s, {col_set} WHERE attention_id=%s", (seed.actor, aid))


def test_po_snapshot_is_append_only(business_db, wipe):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO po_snapshot (order_sn, status, source, source_captured_at)"
                    " VALUES ('PO1',2,'sim',now()) RETURNING snap_id")
        sid = cur.fetchone()[0]
    with pytest.raises(psycopg2.Error, match="只追加"), pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE po_snapshot SET status=9 WHERE snap_id=%s", (sid,))
    with pytest.raises(psycopg2.Error, match="只追加"), pg_conn() as c, c.cursor() as cur:
        cur.execute("DELETE FROM po_snapshot WHERE snap_id=%s", (sid,))


def test_observation_source_check(business_db, wipe):
    with pytest.raises(psycopg2.Error) as e, pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO po_snapshot (order_sn, status, source, source_captured_at)"
                    " VALUES ('PO1',2,'bogus',now())")
    assert e.value.diag.constraint_name == "po_snapshot_source_known"
    with pytest.raises(psycopg2.Error) as e, pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO po_item_obs (order_sn, sku, quantity_real, source, source_captured_at)"
                    " VALUES ('PO1','S',1,'bogus',now())")
    assert e.value.diag.constraint_name == "po_item_obs_source_known"


# ---- 终审修复波 #6 / #3 ----

def test_erp_call_close_requires_finished_at(draft, seed):
    with pg_conn() as c, c.cursor() as cur:
        cid = _call(cur, draft, seed.actor)
    with pytest.raises(psycopg2.Error, match="finished_at"), pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE erp_call SET outcome='ok' WHERE call_id=%s", (cid,))


def test_erp_call_cannot_be_updated_while_staying_in_flight(draft, seed):
    with pg_conn() as c, c.cursor() as cur:
        cid = _call(cur, draft, seed.actor)
    with pytest.raises(psycopg2.Error, match="in_flight"), pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE erp_call SET http_status=200, response='{}' WHERE call_id=%s", (cid,))


@pytest.mark.parametrize("kind,resolution", [
    ("needs_verify", "mismatch_acknowledged"),
    ("landed_mismatch", "confirmed_absent"),
    ("landed_mismatch", "rechecked_found"),
])
def test_attention_resolution_must_fit_kind(draft, seed, kind, resolution):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO po_attention (po_id, kind) VALUES (%s,%s) RETURNING attention_id", (draft, kind))
        aid = cur.fetchone()[0]
    with pytest.raises(psycopg2.Error) as e, pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE po_attention SET resolved_at=now(), resolution=%s, resolved_by=%s"
                    " WHERE attention_id=%s", (resolution, seed.actor, aid))
    assert e.value.diag.constraint_name == "po_attention_resolution_fits_kind"


def test_open_attention_cannot_be_deleted(draft, seed):
    with pg_conn() as c, c.cursor() as cur:
        aid = _open_attention(cur, draft)
    with pytest.raises(psycopg2.Error, match="不许删除"), pg_conn() as c, c.cursor() as cur:
        cur.execute("DELETE FROM po_attention WHERE attention_id=%s", (aid,))
