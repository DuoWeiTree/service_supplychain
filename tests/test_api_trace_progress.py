import json

from shared.pg_client import pg_conn
from tests.helpers import H, mint


def _po(client, seed, lid, units):
    body = {"supplier_id": "8085", "purchaser_id": 111, "opt_uid": 1, "qc_type": 2,
            "lines": [{"sku": seed.sku_a, "wid": seed.wid, "units": units, "price": "2",
                       "sources": [{"line_id": lid, "seller_id": seed.seller_a, "units": units}]}]}
    return client.post("/v1/purchase-orders", json=body, headers=H(seed.actor)).json()["po_id"]


def _setup(seed, total=10):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO supplier VALUES ('8085','01',now()) ON CONFLICT DO NOTHING")
        plan_id, lid = mint(cur, seed.actor, seed.sku_a, total=total)
        cur.execute("UPDATE plan_line SET demand_by_seller=%s::jsonb WHERE line_id=%s",
                    (json.dumps({seed.seller_a: {"units": 5, "basis": "human"}}), lid))
    return plan_id, lid


def test_trace_lists_every_po_and_store_split(client, seed):
    _, lid = _setup(seed)
    a, b = _po(client, seed, lid, 4), _po(client, seed, lid, 6)
    t = client.get(f"/v1/plan-lines/{lid}/trace").json()
    assert t["confirmed_units"] == 10 and t["state"] == "已确认"
    assert [(p["po_id"], p["sources"]) for p in t["purchase_orders"]] == \
        [(a, [{"seller_id": seed.seller_a, "units": 4}]), (b, [{"seller_id": seed.seller_a, "units": 6}])]


def test_trace_unknown_line_is_404(client, seed):
    assert client.get("/v1/plan-lines/999999999/trace").status_code == 404


def test_plan_progress_shows_four_columns_and_null_good_in(client, seed):
    plan_id, lid = _setup(seed)
    _po(client, seed, lid, 4)
    p = client.get(f"/v1/plans/{plan_id}/purchase-progress").json()
    row = p["rows"][0]
    assert (row["planned"], row["confirmed"], row["ordered"], row["good_in"]) == (10, 4, 0, None)
    assert p["basis"]["good_in"]


def test_preflight_endpoint_lists_all_gates(client, seed):
    _, lid = _setup(seed)
    po_id = _po(client, seed, lid, 4)
    r = client.get(f"/v1/purchase-orders/{po_id}/preflight").json()
    assert r["ok"] is True and {g["gate"] for g in r["gates"]} >= {"supplier", "opt_uid", "line_price"}


def test_preflight_unknown_po_is_404(client, seed):
    assert client.get("/v1/purchase-orders/999999999/preflight").status_code == 404
