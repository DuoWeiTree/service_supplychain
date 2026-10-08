import json

from shared.pg_client import pg_conn
from tests.helpers import H, advance, mint


def _line(seed, total, demand):
    with pg_conn() as c, c.cursor() as cur:
        _, lid = mint(cur, seed.actor, seed.sku_a, total=total)
        cur.execute("UPDATE plan_line SET demand_by_seller=%s::jsonb WHERE line_id=%s",
                    (json.dumps(demand), lid))
    return lid


def test_candidates_carry_caps_and_remaining(client, seed):
    lid = _line(seed, 24, {"A": {"units": 12, "basis": "human"}, "B": {"units": 11, "basis": "human"}})
    row = next(r for r in client.get("/v1/plan-lines").json()["lines"] if r["line_id"] == lid)
    assert row["confirmed_units"] == 0 and row["remaining"] == 24
    assert row["sellers"] == {"A": {"expected": 12, "cap": 13, "used": 0, "remaining": 13},
                              "B": {"expected": 11, "cap": 11, "used": 0, "remaining": 11}}
    assert row["blocked"] is None


def test_blocked_line_is_named_up_front(client, seed):
    lid = _line(seed, 24, {"A": {"units": 0, "basis": "human"}})
    row = next(r for r in client.get("/v1/plan-lines").json()["lines"] if r["line_id"] == lid)
    assert row["blocked"] == {"reason": "no_seller_basis", "sellers": ["A"]}


def test_orderable_filter_counts_what_it_drops(client, seed):
    _line(seed, 24, {"A": {"units": 1, "basis": "human"}})
    with pg_conn() as c, c.cursor() as cur:
        _, gone = mint(cur, seed.actor, seed.sku_a, total=5)
        advance(cur, gone, "已撤销", actor=seed.actor)
    body = client.get("/v1/plan-lines?orderable=1").json()
    assert all(r["state"] in ("已提交", "已确认") and r["remaining"] > 0 for r in body["lines"])
    assert body["excluded"]["state"] >= 1


def test_manual_forward_into_derived_state_is_refused(client, seed):
    lid = _line(seed, 24, {"A": {"units": 1, "basis": "human"}})
    r = client.post(f"/v1/plan-lines/{lid}/transition", json={"to_state": "已确认"}, headers=H(seed.actor))
    assert r.status_code == 422 and r.json()["error"] == "derived_state"


def test_purchase_options_lists_suppliers(client, seed):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO supplier VALUES ('8085','01[吉信德]',now())")
    body = client.get("/v1/purchase-options").json()
    assert body["suppliers"] == [{"supplier_id": "8085", "name": "01[吉信德]"}]
