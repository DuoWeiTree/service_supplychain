import json
import threading

import pytest

from shared.pg_client import pg_conn
from tests.helpers import H, mint


@pytest.fixture
def world(seed):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO supplier VALUES ('8085','01[吉信德]',now())")
        _, lid = mint(cur, seed.actor, seed.sku_a, total=24)
        cur.execute("UPDATE plan_line SET demand_by_seller=%s::jsonb WHERE line_id=%s",
                    (json.dumps({seed.seller_a: {"units": 12, "basis": "human"},
                                 "OTHER": {"units": 11, "basis": "human"}}), lid))
    return lid


def body(seed, lid, units=13, line_units=None, reason=None, version=None):
    b = {"supplier_id": "8085", "purchaser_id": 111, "opt_uid": 10925905, "qc_type": 2,
         "lines": [{"sku": seed.sku_a, "wid": seed.wid, "units": line_units or units, "price": "1.5",
                    "unplanned_reason": reason,
                    "sources": [{"line_id": lid, "seller_id": seed.seller_a, "units": units}]}]}
    if version is not None:
        b["draft_version"] = version
    return b


def line_state(lid):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT state FROM plan_line WHERE line_id=%s", (lid,))
        return cur.fetchone()[0]


def test_create_draft_and_read_back(client, seed, world):
    r = client.post("/v1/purchase-orders", json=body(seed, world), headers=H(seed.actor))
    assert r.status_code == 201, r.text
    po = client.get(f"/v1/purchase-orders/{r.json()['po_id']}").json()
    assert po["state"] == "草稿" and po["custom_order_sn"] == f"SCM-PO-{po['po_id']}"
    assert po["lines"][0]["sources"] == [{"line_id": world, "seller_id": seed.seller_a, "units": 13}]
    assert po["confirm_token"]
    assert line_state(world) == "已提交"      # 13 / 24，未满


def test_over_seller_cap_names_the_numbers(client, seed, world):
    r = client.post("/v1/purchase-orders", json=body(seed, world, units=14), headers=H(seed.actor))
    assert r.status_code == 409
    assert r.json()["error"] == "over_seller_cap"
    assert {k: r.json()[k] for k in ("line_id", "seller_id", "cap", "used", "requested")} == \
        {"line_id": world, "seller_id": seed.seller_a, "cap": 13, "used": 0, "requested": 14}


def test_no_seller_basis_is_named(client, seed, world):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE plan_line SET demand_by_seller='{\"X\":{\"units\":0,\"basis\":\"human\"}}'"
                    " WHERE line_id=%s", (world,))
    r = client.post("/v1/purchase-orders", json=body(seed, world), headers=H(seed.actor))
    assert r.status_code == 409 and r.json()["error"] == "no_seller_basis"
    assert r.json()["sellers"] == ["X"]


def test_unplanned_needs_reason(client, seed, world):
    r = client.post("/v1/purchase-orders", json=body(seed, world, units=13, line_units=20),
                    headers=H(seed.actor))
    assert r.status_code == 400 and r.json()["error"] == "unplanned_reason_required"
    r = client.post("/v1/purchase-orders", json=body(seed, world, units=13, line_units=20, reason="起订量"),
                    headers=H(seed.actor))
    assert r.status_code == 201


def test_two_drafts_filling_line_moves_it_to_confirmed_and_delete_moves_back(client, seed, world):
    a = client.post("/v1/purchase-orders", json=body(seed, world, units=13), headers=H(seed.actor))
    b2 = body(seed, world, units=11)
    b2["lines"][0]["sources"][0]["seller_id"] = "OTHER"
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO seller (seller_id, name, market, has_fba, platform, refreshed_at)"
                    " VALUES ('OTHER','o','US',true,'amazon',now()) ON CONFLICT DO NOTHING")
    b = client.post("/v1/purchase-orders", json=b2, headers=H(seed.actor))
    assert b.status_code == 201, b.text
    assert line_state(world) == "已确认"
    assert client.delete(f"/v1/purchase-orders/{b.json()['po_id']}", headers=H(seed.actor)).status_code == 204
    assert line_state(world) == "已提交"
    assert a.status_code == 201


def test_update_needs_current_draft_version(client, seed, world):
    po_id = client.post("/v1/purchase-orders", json=body(seed, world), headers=H(seed.actor)).json()["po_id"]
    r = client.put(f"/v1/purchase-orders/{po_id}", json=body(seed, world, units=10, version=1),
                   headers=H(seed.actor))
    assert r.status_code == 200 and r.json()["draft_version"] == 2
    stale = client.put(f"/v1/purchase-orders/{po_id}", json=body(seed, world, units=9, version=1),
                       headers=H(seed.actor))
    assert stale.status_code == 409 and stale.json()["error"] == "stale_draft"
    assert stale.json()["current_version"] == 2


def test_duplicate_sku_wid_lines_rejected(client, seed, world):
    b = body(seed, world, units=5)
    b["lines"].append(dict(b["lines"][0], sources=[]))
    r = client.post("/v1/purchase-orders", json=b, headers=H(seed.actor))
    assert r.status_code == 400 and r.json()["error"] == "duplicate_line"


def test_concurrent_drafts_cannot_both_take_the_same_quantity(client, seed, world):
    """Review Focus #1：两个人同时组同一条记录，只能一个成功。"""
    results = []

    def go():
        results.append(client.post("/v1/purchase-orders", json=body(seed, world, units=13),
                                   headers=H(seed.actor)).status_code)

    ts = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(results) == [201, 409]
