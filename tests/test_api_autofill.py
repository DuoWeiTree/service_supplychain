"""新建 FBA 排货表时自动填一次默认排货量（UX-D7 ~ D10 · spec 2026-10-16 §1）。"""
import json
import logging

import psycopg2
import pytest

from shared.pg_client import pg_conn
from tests.helpers import H
from tests.test_api_sheets import FakeStock, put, stock, sync_live, world  # noqa: F401


def claim(seed, lid, stores):
    """stores: {sid: (店期望, [(msku, msku 期望), …])}"""
    dbs = {sid: {"units": u, "basis": "human", "mskus": [{"seller_sku": m, "units": mu, "basis": "human"} for m, mu in ms]}
           for sid, (u, ms) in stores.items()}
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE plan_line SET demand_by_seller=%s::jsonb WHERE line_id=%s", (json.dumps(dbs), lid))


def create(client, seed, plan_id, route="fba"):
    r = client.post("/v1/sheets", json={"plan_id": plan_id, "route": route}, headers=H(seed.actor))
    assert r.status_code == 201, r.text
    return r.json()


def events(sheet_id):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT outcome, filled, skipped, error, rule FROM dispatch_autofill WHERE sheet_id=%s", (sheet_id,))
        return cur.fetchall()


@pytest.fixture
def two_stores(seed, world):  # noqa: F811
    claim(seed, world["line_id"], {seed.seller_a: (60, [(seed.msku_a[0], 60)]),
                                   seed.seller_b: (20, [(seed.msku_c[0], 20)])})
    return world


def test_fba_sheet_is_filled_once_into_human_cells_with_an_event(client, seed, two_stores, caplog):
    """还差 100 按期望 60:20 拆成 75 / 25；店 A 本店桶 5 + 共享 80 → 75；店 B 只剩共享 10 → 10；差 15 点名。"""
    caplog.set_level(logging.INFO, logger="scm.usecase")
    body = create(client, seed, two_stores["plan_id"])
    sid = body["sheet_id"]
    assert body["autofill"]["outcome"] == "done"
    assert (body["autofill"]["filled"], body["autofill"]["units"]) == (2, 85)

    cells = client.get(f"/v1/sheets/{sid}/grid").json()["cells"]
    got = sorted((c["seller_sku"], c["sid"], c["from_wid"], c["dest_sid"], c["units"], c["line_id"]) for c in cells)
    assert got == [(seed.msku_a[0], seed.seller_a, seed.wid, seed.seller_a, 75, two_stores["line_id"]),
                   (seed.msku_c[0], seed.seller_b, seed.wid, seed.seller_b, 10, two_stores["line_id"])]

    d = client.get(f"/v1/sheets/{sid}").json()
    assert len([c for c in d["consignments"] if c["our_stage"] == "草稿"]) == 2      # 派生照常
    af = d["autofill"]
    assert af["outcome"] == "done" and af["rule"] == "UX-D8/D9 v1"
    assert sorted(f["arrangement_id"] for f in af["filled"]) == sorted(c["arrangement_id"] for c in cells)
    assert [(s["reason"], s["wanted"], s["filled"]) for s in af["skipped"]] == [("stock_short", 100, 85)]
    assert af["inputs"]["lines"][0]["pending"] == 100

    line = next(r.getMessage() for r in caplog.records if "op=sheet_autofill sheet=" in r.getMessage())
    for t in ("outcome=done", "lines=1", "filled=2", "units=85", "skipped=1", "stock_short", "elapsed_ms="):
        assert t in line, line


def test_human_edits_afterwards_are_not_refilled(client, seed, two_stores):
    sid = create(client, seed, two_stores["plan_id"])["sheet_id"]
    r = put(client, seed, sid, line_id=two_stores["line_id"], dest_sid=seed.seller_a, units=30)
    assert r.status_code == 200, r.text
    cells = client.get(f"/v1/sheets/{sid}/grid").json()["cells"]
    assert sorted(c["units"] for c in cells) == [10, 30]
    [(_, filled, *_rest)] = events(sid)
    assert sorted(f["units"] for f in filled) == [10, 75]                         # 事件不改，页面据此写「已改」


def test_oversea_sheet_is_not_filled(client, seed, two_stores, stock):  # noqa: F811
    body = create(client, seed, two_stores["plan_id"], route="oversea")
    assert body["autofill"] is None
    assert client.get(f"/v1/sheets/{body['sheet_id']}/grid").json()["cells"] == []
    assert events(body["sheet_id"]) == []
    assert client.get(f"/v1/sheets/{body['sheet_id']}").json()["autofill"] is None


def test_stock_unavailable_still_creates_the_sheet_and_names_why(client, seed, two_stores, stock, caplog):  # noqa: F811
    stock["s"] = FakeStock(fail=True)
    body = create(client, seed, two_stores["plan_id"])
    af = body["autofill"]
    assert af["outcome"] == "failed" and af["event_written"] is True
    assert af["error"]["stage"] == "stock" and af["error"]["error"] == "stock_unavailable"
    assert af["error"]["fields"]["cause"]["code"] == "ETIMEDOUT"
    assert client.get(f"/v1/sheets/{body['sheet_id']}/grid").json()["cells"] == []
    d = client.get(f"/v1/sheets/{body['sheet_id']}").json()
    assert d["autofill"]["outcome"] == "failed" and d["autofill"]["error"]["stage"] == "stock"
    assert any("op=sheet_autofill" in r.getMessage() and "outcome=failed" in r.getMessage() for r in caplog.records)


def test_a_gate_refusal_writes_no_cell_at_all(client, seed, two_stores, monkeypatch):
    """任一格被 PUT /cells 那套闸挡下 → 一格都不写（不留一半），事件点名那一格与错误码。"""
    from rules import autofill
    real = autofill.allocate

    def too_much(*a, **k):
        r = real(*a, **k)
        if r["filled"]:                                     # 先有一遍不带库存的空跑
            r["filled"].append({**r["filled"][0], "seller_sku": seed.msku_b[0], "units": 50})   # Σ 135 > 总量 100
        return r
    monkeypatch.setattr(autofill, "allocate", too_much)
    body = create(client, seed, two_stores["plan_id"])
    assert body["autofill"]["outcome"] == "failed"
    assert body["autofill"]["error"]["error"] == "over_total"
    assert client.get(f"/v1/sheets/{body['sheet_id']}/grid").json()["cells"] == []
    [(outcome, filled, _s, error, _r)] = events(body["sheet_id"])
    assert (outcome, filled, error["stage"]) == ("failed", [], "write")


def test_nothing_claimed_reads_no_stock_and_records_why(client, seed, world, stock):  # noqa: F811
    """world 的记录没有认领 msku：不读库存，事件写 done + 0 格 + 原因。"""
    stock["s"].asked.clear()
    body = create(client, seed, world["plan_id"])
    assert (body["autofill"]["outcome"], body["autofill"]["filled"]) == ("done", 0)
    assert stock["s"].asked == []
    [(_, filled, skipped, _e, _r)] = events(body["sheet_id"])
    assert filled == [] and {s["reason"] for s in skipped} == {"no_claimed_msku", "no_eligible_store"}


def test_event_is_append_only(client, seed, two_stores):
    sid = create(client, seed, two_stores["plan_id"])["sheet_id"]
    with pytest.raises(psycopg2.Error), pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE dispatch_autofill SET filled='[]' WHERE sheet_id=%s", (sid,))
