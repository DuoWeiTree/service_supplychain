import json

import pytest

from shared.pg_client import pg_conn
from tests.helpers import H, advance, mint


def _line(seed, total, demand):
    with pg_conn() as c, c.cursor() as cur:
        _, lid = mint(cur, seed.actor, seed.sku_a, total=total)
        cur.execute("UPDATE plan_line SET demand_by_seller=%s::jsonb WHERE line_id=%s",
                    (json.dumps(demand), lid))
    return lid


def test_candidates_carry_expected_and_used_without_caps(client, seed):
    lid = _line(seed, 24, {"A": {"units": 12, "basis": "human"}, "B": {"units": 11, "basis": "human"}})
    row = next(r for r in client.get("/v1/plan-lines").json()["lines"] if r["line_id"] == lid)
    assert row["confirmed_units"] == 0 and row["remaining"] == 24
    assert row["sellers"] == {"A": {"expected": 12, "used": 0}, "B": {"expected": 11, "used": 0}}
    assert "blocked" not in row


def test_unknown_demand_line_is_orderable(client, seed):
    # PB-D22 起因：本月断货、期望为空的单店记录，照样可组单；期望回显 null 不是 0
    lid = _line(seed, 24, {"A": {"units": None, "basis": "unknown", "mskus": []}})
    body = client.get("/v1/plan-lines?orderable=1").json()
    row = next(r for r in body["lines"] if r["line_id"] == lid)
    assert row["sellers"] == {"A": {"expected": None, "used": 0}}


def test_orderable_filter_counts_what_it_drops(client, seed):
    _line(seed, 24, {"A": {"units": 1, "basis": "human"}})
    with pg_conn() as c, c.cursor() as cur:
        _, gone = mint(cur, seed.actor, seed.sku_a, total=5)
        advance(cur, gone, "已撤销", actor=seed.actor)
    body = client.get("/v1/plan-lines?orderable=1").json()
    assert all(r["state"] in ("已提交", "已确认") and r["remaining"] > 0 for r in body["lines"])
    assert body["excluded"]["state"] >= 1


@pytest.mark.parametrize("to", ["已确认", "已下单", "准备排货"])
def test_manual_forward_into_derived_state_is_refused(client, seed, to):
    lid = _line(seed, 24, {"A": {"units": 1, "basis": "human"}})
    r = client.post(f"/v1/plan-lines/{lid}/transition", json={"to_state": to}, headers=H(seed.actor))
    assert r.status_code == 422 and r.json()["error"] == "derived_state"


def test_derived_state_on_missing_line_is_404(client, seed):
    r = client.post("/v1/plan-lines/99999999/transition", json={"to_state": "已确认"},
                    headers=H(seed.actor))
    assert r.status_code == 404 and r.json()["error"] == "line_not_found"


def _draft(client, seed, lid, units):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO supplier VALUES ('8085','s',now()) ON CONFLICT DO NOTHING")
    b = {"supplier_id": "8085", "purchaser_id": 111, "opt_uid": 10925905, "qc_type": 2,
         "lines": [{"sku": seed.sku_a, "wid": seed.wid, "units": units, "price": "1.5",
                    "unplanned_reason": None,
                    "sources": [{"line_id": lid, "seller_id": seed.seller_a, "units": units}]}]}
    r = client.post("/v1/purchase-orders", json=b, headers=H(seed.actor))
    assert r.status_code == 201, r.text


def test_orderable_drops_fully_covered_line_and_counts_it(client, seed):
    full = _line(seed, 10, {seed.seller_a: {"units": 10, "basis": "human"}})
    part = _line(seed, 10, {seed.seller_a: {"units": 10, "basis": "human"}})
    _draft(client, seed, full, 10)
    _draft(client, seed, part, 4)
    rows = {r["line_id"]: r for r in client.get("/v1/plan-lines").json()["lines"]}
    assert rows[part]["sellers"][seed.seller_a]["used"] == 4
    assert rows[full]["sellers"][seed.seller_a]["used"] == 10 and rows[full]["remaining"] == 0
    body = client.get("/v1/plan-lines?orderable=1").json()
    ids = {r["line_id"] for r in body["lines"]}
    assert full not in ids and part in ids
    assert body["excluded"]["fully_confirmed"] == 1


def test_purchase_options_lists_suppliers(client, seed):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO supplier VALUES ('8085','01[吉信德]',now())")
    body = client.get("/v1/purchase-options").json()
    assert body["suppliers"] == [{"supplier_id": "8085", "name": "01[吉信德]"}]
