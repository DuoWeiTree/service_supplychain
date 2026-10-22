"""排货表接口（spec 2026-10-10 §6）。★ 本地库存走注入的替身（stock_dep），不碰真 CH。"""
import datetime as dt
import json
import threading

import pytest

from dim.ch_source import ChUnavailable
from dim.ch_stock import PoItemProgress, StockSnapshot
from shared.pg_client import pg_conn
from tests.helpers import H, advance, mint

D10 = dt.date(2026, 10, 10)
AT10 = dt.datetime(2026, 10, 10, 6, 33, 29)
OVERSEA = 2


class FakeStock:
    def __init__(self, buckets=None, progress=None, fail=False):
        self.buckets = buckets or {}
        self.progress = progress or {}
        self.fail = fail
        self.asked = []

    def snapshot(self, skus):
        self.asked.append(skus)
        if self.fail:
            raise ChUnavailable(target="jxd_raw.lingxing_inventory_warehouse",
                                cause={"kind": "connect_timeout", "code": "ETIMEDOUT"})
        rows = {k: v for k, v in self.buckets.items() if skus is None or k[0] in skus}
        return StockSnapshot(D10, AT10, rows, {"unknown_seller": {"rows": 1, "units": 26},
                                         "not_local_wid": {"rows": 0, "units": 0}}, ())

    def po_progress(self, sns):
        if self.fail:
            raise ChUnavailable(target="jxd_raw.lingxing_purchase_order_list_items", cause={"kind": "x"})
        return {k: v for k, v in self.progress.items() if k[0] in sns}


@pytest.fixture
def stock(client, lingxing):
    """CH 替身（网格的底）+ 领星替身（现读 / 确认前现读，CA-D12）—— 同一份库存，两处一致。"""
    from api.common.source_factory import stock_dep
    holder = {"s": FakeStock(), "lx": lingxing}
    client.app.dependency_overrides[stock_dep] = lambda: (lambda local, known: holder["s"])
    yield holder
    client.app.dependency_overrides.pop(stock_dep, None)


@pytest.fixture
def world(seed, stock):
    """记录 lid（sku_a，总量 100，店 seller_a）→ 采购单 PO-1 已下单 → 记录 已下单；一个海外仓。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO warehouse (wid, name, kind, market, refreshed_at)"
                    " VALUES (%s, '30-LA自有海外仓', 'oversea_self', 'US', now())", (OVERSEA,))
        cur.execute("INSERT INTO supplier VALUES ('8085','01[吉信德]',now())")
        plan_id, lid = mint(cur, seed.actor, seed.sku_a, total=100)
        cur.execute("UPDATE plan_line SET demand_by_seller=%s::jsonb WHERE line_id=%s",
                    (json.dumps({seed.seller_a: {"units": 60, "basis": "human"}}), lid))
        cur.execute("INSERT INTO po (supplier_id, purchaser_id, opt_uid, qc_type, created_by)"
                    " VALUES ('8085', 111, 1, 2, %s) RETURNING po_id", (seed.actor,))
        po_id = cur.fetchone()[0]
        cur.execute("INSERT INTO po_line (po_id, line_no, sku, wid, units, price)"
                    " VALUES (%s, 1, %s, %s, 100, 1)", (po_id, seed.sku_a, seed.wid))
        cur.execute("INSERT INTO po_source_map VALUES (%s, 1, %s, %s, 100)", (po_id, lid, seed.seller_a))
        cur.execute("UPDATE po SET order_sn='PO-1', placed_at=now() WHERE po_id=%s", (po_id,))
        cur.execute("INSERT INTO po_event (po_id, from_state, to_state, actor, src)"
                    " VALUES (%s, '草稿', '已下单', %s, 't')", (po_id, seed.actor))
        advance(cur, lid, "已确认", "已下单", actor=seed.actor)
    stock["s"] = FakeStock(
        buckets={(seed.sku_a, seed.wid): {"shared": 80, seed.seller_a: 5},
                 (seed.sku_e, seed.wid): {"shared": 9}},
        progress={("PO-1", seed.sku_a): PoItemProgress(entered=85, pending=15, captured=AT10)})
    sync_live(stock["lx"], stock["s"])
    return {"line_id": lid, "po_id": po_id, "plan_id": plan_id}


def sync_live(lx, fake):
    """领星现读看到与 CH 替身相同的库存（店铺桶用 sid，共享桶用 '0'）。
    ★ 用夹具给的替身对象，不 import tests.conftest —— pytest 另载一份模块，那里的 DOUBLE 不是网关在用的这个。"""
    lx.set_stock([(wid, sku, "0" if k == "shared" else k, n)
                      for (sku, wid), b in fake.buckets.items() for k, n in b.items()])


def add_line(cur, plan_id, sku, total, sellers, state="已提交"):
    """同一张计划同一版里再加一条记录（mint 每次新建计划，CA-D11 下排货表只排一张计划）。"""
    cur.execute("SELECT max(rev) FROM plan_rev WHERE plan_id=%s", (plan_id,))
    rev = cur.fetchone()[0]
    cur.execute("INSERT INTO plan_line (plan_id, rev, sku, period_start, total_units, demand_by_seller,"
                " demand_at_submit, state) VALUES (%s,%s,%s,'2026-11-01',%s,%s::jsonb,0,%s) RETURNING line_id",
                (plan_id, rev, sku, total, json.dumps({s_: {"units": 1, "basis": "human"} for s_ in sellers}), state))
    lid = cur.fetchone()[0]
    cur.execute("INSERT INTO plan_line_event (line_id, from_state, to_state, actor, src)"
                " VALUES (%s,'[*]',%s,'alice','test')", (lid, state))
    return lid


def first_plan():
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT min(plan_id) FROM plan")
        return cur.fetchone()[0]


def new_sheet(client, seed, plan_id=None, route="fba"):
    r = client.post("/v1/sheets", json={"plan_id": plan_id or first_plan(), "route": route}, headers=H(seed.actor))
    assert r.status_code == 201, r.text
    return r.json()["sheet_id"]


def put(client, seed, sheet, **cell):
    body = {"line_id": None, "seller_sku": seed.msku_a[0], "sid": seed.msku_a[1], "from_wid": seed.wid,
            "channel": "fba", "dest_wid": None, "dest_sid": None, "units": 1}
    body.update(cell)
    return client.put(f"/v1/sheets/{sheet}/cells", json=body, headers=H(seed.actor))


def test_create_and_list(client, seed, world):
    sid = new_sheet(client, seed)
    body = client.get("/v1/sheets").json()
    assert [s["sheet_id"] for s in body["sheets"]] == [sid]
    s0 = body["sheets"][0]
    assert s0["state"] == "草稿" and s0["consignments"] == 0
    assert (s0["plan_id"], s0["plan_title"]) == (world["plan_id"], "t")


def test_create_needs_an_existing_plan(client, seed, world):
    r = client.post("/v1/sheets", json={}, headers=H(seed.actor))
    assert r.status_code == 400 and r.json()["error"] == "missing_required"
    r = client.post("/v1/sheets", json={"plan_id": 999, "route": "fba"}, headers=H(seed.actor))
    assert r.status_code == 404 and r.json()["error"] == "plan_not_found"


def test_dispatch_plans_lists_unfinished_plans_with_progress(client, seed, world):
    """CA-D11 入口：未完成的销售计划 + 每张的 需求 · 组单 · 下单 · 已排 · 还差 · 已有排货表。"""
    sid = new_sheet(client, seed)
    put(client, seed, sid, line_id=world["line_id"], units=30)
    body = client.get("/v1/dispatch/plans").json()
    [p] = body["plans"]
    assert {k: p[k] for k in ("plan_id", "title", "rev", "lines", "total", "confirmed", "ordered",
                              "staged", "dispatched", "pending")} == \
        {"plan_id": world["plan_id"], "title": "t", "rev": 1, "lines": 1, "total": 100, "confirmed": 100,
         "ordered": 100, "staged": 30, "dispatched": 0, "pending": 70}
    assert [{k: s[k] for k in ("sheet_id", "state", "route")} for s in p["sheets"]] == \
        [{"sheet_id": sid, "state": "草稿", "route": "fba"}]


def test_dispatch_plans_list_each_product_and_cancel_gives_it_back(client, seed, world):
    """/dispatch 要看得见「哪个产品还没排完」；取消排货表后，那个产品的还差回到原数。"""
    sid = new_sheet(client, seed)
    put(client, seed, sid, line_id=world["line_id"], units=30)
    [p] = client.get("/v1/dispatch/plans").json()["plans"]
    [r] = p["records"]
    assert {k: r[k] for k in ("line_id", "sku", "total", "staged", "dispatched", "pending")} == \
        {"line_id": world["line_id"], "sku": seed.sku_a, "total": 100, "staged": 30, "dispatched": 0, "pending": 70}
    assert r["sku_name"] and r["period"] and r["state"]
    assert client.post(f"/v1/sheets/{sid}/cancel", json={"reason": "t"}, headers=H(seed.actor)).status_code == 200
    [p] = client.get("/v1/dispatch/plans").json()["plans"]
    assert (p["records"][0]["staged"], p["records"][0]["pending"], p["sheets"]) == (0, 100, [])


def test_partially_ordered_line_is_listed_and_dispatchable(client, seed, world):
    """★ CA-D10：只下单一部分（停在 已提交）的记录照样列出、照样能排；采购进度只展示。"""
    with pg_conn() as c, c.cursor() as cur:
        l2 = add_line(cur, world["plan_id"], seed.sku_a, 180, [seed.seller_a])
    sid = new_sheet(client, seed)
    row = next(d for d in client.get(f"/v1/sheets/{sid}/grid").json()["demand"] if d["line_id"] == l2)
    assert (row["state"], row["confirmed"], row["ordered"], row["pending"]) == ("已提交", 0, 0, 180)
    assert put(client, seed, sid, line_id=l2, units=10).status_code == 200


def test_grid_rows_carry_purchase_arrival_and_other_sheets(client, seed, world):
    other = new_sheet(client, seed, route="oversea")
    put(client, seed, other, line_id=world["line_id"], units=20, channel="oversea", dest_wid=OVERSEA)
    sid = new_sheet(client, seed)
    put(client, seed, sid, line_id=world["line_id"], units=5, seller_sku=seed.msku_b[0])
    row = client.get(f"/v1/sheets/{sid}/grid").json()["demand"][0]
    assert {k: row[k] for k in ("total", "confirmed", "ordered", "entered", "arriving", "staged_here",
                                "staged_other", "pending")} == \
        {"total": 100, "confirmed": 100, "ordered": 100, "entered": 85, "arriving": 15, "staged_here": 5,
         "staged_other": 20, "pending": 75}


def test_other_plans_lines_are_not_in_the_grid(client, seed, world):
    with pg_conn() as c, c.cursor() as cur:
        _, other_lid = mint(cur, seed.actor, seed.sku_a, total=50)
        cur.execute("UPDATE plan_line SET demand_by_seller=%s::jsonb WHERE line_id=%s",
                    (json.dumps({seed.seller_a: {"units": 1, "basis": "human"}}), other_lid))
    g = client.get(f"/v1/sheets/{new_sheet(client, seed)}/grid").json()
    assert [d["line_id"] for d in g["demand"]] == [world["line_id"]]
    assert g["sheet"]["plan_id"] == world["plan_id"]
    r = put(client, seed, g["sheet"]["sheet_id"], line_id=other_lid, units=1)
    assert r.status_code == 409 and r.json()["error"] == "line_not_in_plan"


def test_grid_expands_line_to_store_msku_with_po_progress_and_buckets(client, seed, world):
    sid = new_sheet(client, seed)
    g = client.get(f"/v1/sheets/{sid}/grid").json()
    [row] = g["demand"]
    assert row["line_id"] == world["line_id"] and row["pending"] == 100 and row["sku"] == seed.sku_a
    [store] = row["stores"]
    assert store["sid"] == seed.seller_a and store["expected"] == 60
    assert sorted(m["seller_sku"] for m in store["mskus"]) == ["MSKU-A", "MSKU-B"]
    [po] = row["purchase_orders"]
    assert po["order_sn"] == "PO-1" and po["units"] == 100
    assert (po["entered"], po["pending"], po["captured"]) == (85, 15, "2026-10-10T06:33:29+00:00")
    [st] = [s for s in g["stock"] if s["sku"] == seed.sku_a]
    assert st["wid"] == seed.wid and st["buckets"] == {"shared": 80, seed.seller_a: 5}
    assert st["available"][seed.seller_a] == 85
    assert g["stock_as_of"] == "2026-10-10"
    assert g["excluded"]["stock"]["unknown_seller"] == {"rows": 1, "units": 26}
    assert seed.sku_e in [u["sku"] for u in g["unplanned"]]
    assert [w["wid"] for w in g["warehouses"]["oversea"]] == [OVERSEA]


def test_grid_survives_stock_unavailable(client, seed, world, stock):
    stock["s"] = FakeStock(fail=True)
    g = client.get(f"/v1/sheets/{new_sheet(client, seed)}/grid").json()
    assert g["stock"] is None and g["stock_error"]["target"].endswith("lingxing_inventory_warehouse")
    assert g["stock_error"]["cause"]["kind"] == "connect_timeout"
    assert len(g["demand"]) == 1, "需求侧照常给"
    assert g["demand"][0]["purchase_orders"][0]["entered"] is None


def test_planned_cell_derives_one_consignment_and_line(client, seed, world):
    sid = new_sheet(client, seed)
    r = put(client, seed, sid, line_id=world["line_id"], units=30)
    assert r.status_code == 200, r.text
    assert r.json()["consignments_diff"]["created"] == [["fba", seed.seller_a]]
    s = client.get(f"/v1/sheets/{sid}").json()
    [c] = s["consignments"]
    assert c["channel"] == "fba" and c["dest_sid"] == seed.seller_a and c["our_stage"] == "草稿"
    assert c["lines"] == [{"seller_sku": "MSKU-A", "sid": seed.seller_a, "from_wid": seed.wid,
                           "sku": seed.sku_a, "units": 30, "planned": 30, "unplanned": 0}]
    g = client.get(f"/v1/sheets/{sid}/grid").json()
    assert g["demand"][0]["pending"] == 70 and g["demand"][0]["staged_here"] == 30
    assert client.get("/v1/checks/dispatch").json()["ok"] is True


def test_rewrite_and_withdraw_cell(client, seed, world):
    sid = new_sheet(client, seed)
    put(client, seed, sid, line_id=world["line_id"], units=30)
    put(client, seed, sid, line_id=world["line_id"], units=20)
    assert client.get(f"/v1/sheets/{sid}").json()["consignments"][0]["lines"][0]["units"] == 20
    r = put(client, seed, sid, line_id=world["line_id"], units=0)
    assert r.json()["consignments_diff"]["removed"] == [["fba", seed.seller_a]]
    assert client.get(f"/v1/sheets/{sid}").json()["consignments"] == []


def test_moving_a_cell_out_of_the_sheet_route_is_refused_and_nothing_changes(client, seed, world):
    """CA-D13：FBA 表里的格子不能挪去海外仓（另开海外仓表）；原子拒绝，原格子原样。"""
    sid = new_sheet(client, seed)
    put(client, seed, sid, line_id=world["line_id"], units=30)
    r = put(client, seed, sid, line_id=world["line_id"], units=30, channel="oversea", dest_wid=OVERSEA,
            replaces={"channel": "fba", "dest_wid": None, "dest_sid": seed.seller_a, "from_wid": seed.wid})
    assert r.status_code == 409 and r.json()["error"] == "route_mismatch"
    [c] = client.get(f"/v1/sheets/{sid}").json()["consignments"]
    assert (c["channel"], c["lines"][0]["units"]) == ("fba", 30)


def test_unplanned_cell_needs_no_plan_line(client, seed, world):
    sid = new_sheet(client, seed, route="oversea")
    r = put(client, seed, sid, seller_sku=seed.msku_e[0], sid=seed.msku_e[1], units=5,
            channel="oversea", dest_wid=OVERSEA)
    assert r.status_code == 200, r.text
    line = client.get(f"/v1/sheets/{sid}").json()["consignments"][0]["lines"][0]
    assert (line["sku"], line["planned"], line["unplanned"]) == (seed.sku_e, 0, 5)


def test_over_total_names_the_numbers(client, seed, world):
    s1, s2 = new_sheet(client, seed), new_sheet(client, seed, route="oversea")     # 总量跨去向共用
    put(client, seed, s1, line_id=world["line_id"], units=70)
    r = put(client, seed, s2, line_id=world["line_id"], units=31, channel="oversea", dest_wid=OVERSEA)
    assert r.status_code == 409
    assert {k: r.json()[k] for k in ("error", "total", "used", "requested")} == \
        {"error": "over_total", "total": 100, "used": 70, "requested": 31}


def test_seller_not_in_line_is_409(client, seed, world):
    r = put(client, seed, new_sheet(client, seed), line_id=world["line_id"],
            seller_sku=seed.msku_c[0], sid=seed.msku_c[1])
    assert r.status_code == 409 and r.json()["error"] == "seller_not_in_line"
    assert r.json()["allowed"] == [seed.seller_a]


def test_bad_destination_and_warehouse_are_409(client, seed, world):
    sid = new_sheet(client, seed)
    r = put(client, seed, sid, channel="oversea", dest_wid=seed.wid)
    assert r.status_code == 409 and r.json()["error"] == "invalid_destination"
    r = put(client, seed, sid, from_wid=OVERSEA)
    assert r.status_code == 409 and r.json()["error"] == "not_local_warehouse"
    r = put(client, seed, sid, units=-1)
    assert r.status_code == 400 and r.json()["error"] == "bad_units"


def test_over_available_only_warns(client, seed, world):
    r = put(client, seed, new_sheet(client, seed), line_id=world["line_id"], units=90)
    assert r.status_code == 200
    assert r.json()["warnings"] == [{"code": "over_available", "sku": seed.sku_a, "from_wid": seed.wid,
                                     "sid": seed.seller_a, "available": -5}]


def test_concurrent_cells_same_line_one_409(client, seed, world):
    s1, s2 = new_sheet(client, seed), new_sheet(client, seed, route="oversea")
    out = []
    route = {s1: {}, s2: {"channel": "oversea", "dest_wid": OVERSEA}}

    def go(s):
        out.append(put(client, seed, s, line_id=world["line_id"], units=60, **route[s]).status_code)
    ts = [threading.Thread(target=go, args=(s,)) for s in (s1, s2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(out) == [200, 409]


def test_unknown_sheet_is_404(client, seed, world):
    assert client.get("/v1/sheets/999/grid").status_code == 404
    assert put(client, seed, 999).status_code == 404


# ── Task 7：装箱 · 预检 · 确认 · 取消 ───────────────────────────

BOX = {"box_count": 2, "box_len": 40, "box_wid": 30, "box_hei": 20, "box_weight": 8.5, "units_per_box": 15}


def packing_url(sheet, seed, msku=None, wid=None):
    m = msku or seed.msku_a
    return f"/v1/sheets/{sheet}/packing/{m[0]}/{m[1]}/{wid or seed.wid}"


def watermark(client, sheet):
    return client.get(f"/v1/sheets/{sheet}").json()["watermark"]


def confirm(client, seed, sheet, seen=None):
    return client.post(f"/v1/sheets/{sheet}/confirm",
                       json={"seen_watermark": seen if seen is not None else watermark(client, sheet)},
                       headers=H(seed.actor))


def gate_runs(sheet):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT ok FROM sheet_gate_run WHERE sheet_id=%s ORDER BY run_id", (sheet,))
        return [r[0] for r in cur.fetchall()]


def test_packing_only_for_fba_lines(client, seed, world):
    sid = new_sheet(client, seed)
    put(client, seed, sid, line_id=world["line_id"], units=30)
    r = client.put(packing_url(sid, seed), json=BOX, headers=H(seed.actor))
    assert r.status_code == 200, r.text
    assert client.get(f"/v1/sheets/{sid}").json()["packing"][0]["box_weight"] == 8.5
    put(client, seed, sid, seller_sku=seed.msku_e[0], sid=seed.msku_e[1], units=3,
        channel="oversea", dest_wid=OVERSEA)
    r = client.put(packing_url(sid, seed, msku=seed.msku_e), json=BOX, headers=H(seed.actor))
    assert r.status_code == 409 and r.json()["error"] == "packing_not_applicable"
    r = client.put(packing_url(sid, seed), json={**BOX, "box_len": -1}, headers=H(seed.actor))
    assert r.status_code == 400 and r.json()["error"] == "bad_packing"


def test_confirm_blocked_lists_every_gate_and_records_the_run(client, seed, world):
    sid = new_sheet(client, seed)
    put(client, seed, sid, line_id=world["line_id"], units=30)
    r = confirm(client, seed, sid)
    assert r.status_code == 409 and r.json()["error"] == "confirm_blocked"
    gs = {g["gate"]: g for g in r.json()["gates"]}
    assert list(gs) == ["G1", "G2", "G3", "G4", "G5", "G6"]                 # ★ CA-D13：FBA 表不列 G7
    assert gs["G1"]["ok"] is False and gs["G3"]["ok"] is True and gs["G5"]["ok"] is None
    assert gate_runs(sid) == [False]
    assert client.get(f"/v1/sheets/{sid}").json()["state"] == "草稿"


def test_preflight_matches_confirm_without_writing(client, seed, world):
    sid = new_sheet(client, seed)
    put(client, seed, sid, line_id=world["line_id"], units=30)
    p = client.get(f"/v1/sheets/{sid}/preflight").json()
    assert p["ok"] is False and [g["gate"] for g in p["gates"] if g["ok"] is False] == ["G1"]
    assert gate_runs(sid) == []


def test_confirm_succeeds_then_everything_is_frozen(client, seed, world):
    sid = new_sheet(client, seed)
    put(client, seed, sid, line_id=world["line_id"], units=30)
    client.put(packing_url(sid, seed), json=BOX, headers=H(seed.actor))
    r = confirm(client, seed, sid)
    assert r.status_code == 200, r.text
    s = client.get(f"/v1/sheets/{sid}").json()
    assert s["state"] == "已确认" and [c["our_stage"] for c in s["consignments"]] == ["已确认"]
    assert gate_runs(sid) == [True]
    r = put(client, seed, sid, line_id=world["line_id"], units=31)
    assert r.status_code == 409 and r.json()["error"] == "sheet_frozen"
    assert client.put(packing_url(sid, seed), json=BOX, headers=H(seed.actor)).json()["error"] == "sheet_frozen"


def test_confirm_g3_short_names_the_numbers(client, seed, world, stock):
    sid = new_sheet(client, seed)
    put(client, seed, sid, line_id=world["line_id"], units=90)      # 可发 85
    client.put(packing_url(sid, seed), json=BOX, headers=H(seed.actor))
    r = confirm(client, seed, sid)
    g3 = {g["gate"]: g for g in r.json()["gates"]}["G3"]
    assert g3["ok"] is False and g3["detail"][0]["short"] == 5


def test_confirm_stock_unavailable_is_503_and_recorded(client, seed, world, stock):
    sid = new_sheet(client, seed)
    put(client, seed, sid, line_id=world["line_id"], units=30)
    client.put(packing_url(sid, seed), json=BOX, headers=H(seed.actor))
    stock["lx"].enqueue("local_inventory_details", "not_sent")   # CA-D12：确认前现读领星失败
    r = confirm(client, seed, sid)
    assert r.status_code == 503 and r.json()["error"] == "stock_unavailable"
    assert r.json()["cause"]["outcome"] == "not_sent"
    assert gate_runs(sid) == [False]


def test_g4_names_other_sheets_written_after_seen(client, seed, world):
    s1, s2 = new_sheet(client, seed), new_sheet(client, seed, route="oversea")
    put(client, seed, s1, line_id=world["line_id"], units=10)
    client.put(packing_url(s1, seed), json=BOX, headers=H(seed.actor))
    seen = watermark(client, s1)
    put(client, seed, s2, seller_sku=seed.msku_b[0], sid=seed.msku_b[1], units=1, channel="oversea", dest_wid=OVERSEA)
    r = confirm(client, seed, s1, seen=seen)
    g4 = {g["gate"]: g for g in r.json()["gates"]}["G4"]
    assert g4["ok"] is False and g4["detail"] == [{"sheet_id": s2, "sku": seed.sku_a, "from_wid": seed.wid}]


def test_cancel_needs_reason_and_releases(client, seed, world):
    sid = new_sheet(client, seed)
    put(client, seed, sid, line_id=world["line_id"], units=100)
    client.put(packing_url(sid, seed), json=BOX, headers=H(seed.actor))
    stock_ok = confirm(client, seed, sid)
    assert stock_ok.status_code == 409    # 可发 85 < 100 —— 草稿态取消
    r = client.post(f"/v1/sheets/{sid}/cancel", json={"reason": " "}, headers=H(seed.actor))
    assert r.status_code == 400 and r.json()["error"] == "reason_required"
    r = client.post(f"/v1/sheets/{sid}/cancel", json={"reason": "换方案"}, headers=H(seed.actor))
    assert r.status_code == 200 and client.get(f"/v1/sheets/{sid}").json()["state"] == "已取消"
    g = client.get(f"/v1/sheets/{new_sheet(client, seed)}/grid").json()
    assert g["demand"][0]["pending"] == 100


def test_cancel_confirmed_sheet(client, seed, world):
    sid = new_sheet(client, seed)
    put(client, seed, sid, line_id=world["line_id"], units=30)
    client.put(packing_url(sid, seed), json=BOX, headers=H(seed.actor))
    assert confirm(client, seed, sid).status_code == 200
    r = client.post(f"/v1/sheets/{sid}/cancel", json={"reason": "客户取消"}, headers=H(seed.actor))
    assert r.status_code == 200
    s = client.get(f"/v1/sheets/{sid}").json()
    assert s["state"] == "已取消" and s["consignments"][0]["our_stage"] == "已取消"
    r = client.post(f"/v1/sheets/{sid}/cancel", json={"reason": "again"}, headers=H(seed.actor))
    assert r.status_code == 409 and r.json()["error"] == "sheet_frozen"


# ── 终审修复 ───────────────────────────────────────────────

def test_revoked_line_cell_can_be_withdrawn_and_blocks_confirm(client, seed, world):
    """C1：记录撤销后，格子必须撤得回去；不撤就不许确认，且网格里看得见那条记录。"""
    sid = new_sheet(client, seed)
    put(client, seed, sid, line_id=world["line_id"], units=30)
    client.put(packing_url(sid, seed), json=BOX, headers=H(seed.actor))
    with pg_conn() as c, c.cursor() as cur:
        advance(cur, world["line_id"], "已撤销", actor=seed.actor)
    g = client.get(f"/v1/sheets/{sid}/grid").json()
    assert [(d["line_id"], d["state"]) for d in g["demand"]] == [(world["line_id"], "已撤销")]
    r = confirm(client, seed, sid)
    assert r.status_code == 409 and r.json()["error"] == "dispatch_check_failed"
    assert r.json()["violations"][0]["check_name"] == "line_not_dispatchable"
    r = put(client, seed, sid, line_id=world["line_id"], units=0)
    assert r.status_code == 200, r.text
    assert client.get("/v1/checks/dispatch").json()["ok"] is True


def test_moving_onto_an_existing_cell_is_refused_not_overwritten(client, seed, world):
    """I1：挪到一个已有安排的格子 → 409，不悄悄覆盖。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO warehouse (wid, name, kind, market, refreshed_at) VALUES (3,'二号仓','local',NULL,now())")
    sid = new_sheet(client, seed)
    put(client, seed, sid, line_id=world["line_id"], units=30)
    put(client, seed, sid, line_id=world["line_id"], units=20, from_wid=3)
    r = put(client, seed, sid, line_id=world["line_id"], units=30, from_wid=3,
            replaces={"channel": "fba", "dest_wid": None, "dest_sid": seed.seller_a, "from_wid": seed.wid})
    assert r.status_code == 409 and r.json()["error"] == "cell_exists" and r.json()["existing_units"] == 20
    units = sorted(ln["units"] for c in client.get(f"/v1/sheets/{sid}").json()["consignments"] for ln in c["lines"])
    assert units == [20, 30]


def test_crossed_writes_on_two_lines_do_not_deadlock(client, seed, world):
    """I2：两张表交叉写两条记录 —— 不许 40P01 → 500。"""
    with pg_conn() as c, c.cursor() as cur:
        l2 = add_line(cur, world["plan_id"], seed.sku_a, 100, [seed.seller_a])
    l1 = world["line_id"]
    for _ in range(5):
        a, b = new_sheet(client, seed), new_sheet(client, seed, route="oversea")
        ob = {"channel": "oversea", "dest_wid": OVERSEA}
        put(client, seed, a, line_id=l2, units=1)
        put(client, seed, b, line_id=l1, units=1, **ob)
        out = []
        ts = [threading.Thread(target=lambda s=s, ln=ln, kw=kw: out.append(put(client, seed, s, line_id=ln, units=2,
                                                                                seller_sku=seed.msku_b[0], **kw).status_code))
              for s, ln, kw in ((a, l1, {}), (b, l2, ob))]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert 500 not in out, out
        for s in (a, b):                    # ★ CA-D13：同计划同去向只一张草稿，下一轮前收掉
            client.post(f"/v1/sheets/{s}/cancel", json={"reason": "t"}, headers=H(seed.actor))



# ── CA-D13：按去向分表（spec 2026-10-12-dispatch-route-split §3 / §5）──────────

def test_create_needs_route_and_one_draft_per_plan_route(client, seed, world):
    r = client.post("/v1/sheets", json={"plan_id": first_plan()}, headers=H(seed.actor))
    assert r.status_code == 400 and r.json()["missing"] == ["route"]
    r = client.post("/v1/sheets", json={"plan_id": first_plan(), "route": "walmart"}, headers=H(seed.actor))
    assert r.status_code == 400 and r.json()["error"] == "bad_route"
    a = new_sheet(client, seed)
    r = client.post("/v1/sheets", json={"plan_id": first_plan(), "route": "fba"}, headers=H(seed.actor))
    assert r.status_code == 409 and (r.json()["error"], r.json()["sheet_id"]) == ("draft_exists", a)
    b = new_sheet(client, seed, route="oversea")
    assert client.get(f"/v1/sheets/{b}").json()["route"] == "oversea"
    assert client.get(f"/v1/sheets/{b}/grid").json()["sheet"]["route"] == "oversea"
    assert {(x["sheet_id"], x["route"]) for x in client.get("/v1/sheets").json()["sheets"]} == {(a, "fba"), (b, "oversea")}
    [p] = client.get("/v1/dispatch/plans").json()["plans"]
    assert [{k: s[k] for k in ("sheet_id", "state", "route")} for s in p["sheets"]] == \
        [{"sheet_id": a, "state": "草稿", "route": "fba"}, {"sheet_id": b, "state": "草稿", "route": "oversea"}]


def test_cell_outside_the_sheet_route_is_409(client, seed, world):
    sid = new_sheet(client, seed)
    r = put(client, seed, sid, line_id=world["line_id"], units=3, channel="oversea", dest_wid=OVERSEA)
    assert r.status_code == 409 and r.json()["error"] == "route_mismatch"


def test_pending_is_shared_across_routes(client, seed, world):
    a, b = new_sheet(client, seed), new_sheet(client, seed, route="oversea")
    put(client, seed, a, line_id=world["line_id"], units=30)
    row = next(d for d in client.get(f"/v1/sheets/{b}/grid").json()["demand"] if d["line_id"] == world["line_id"])
    assert (row["staged_other"], row["pending"]) == (30, 70)


def test_preflight_lists_only_the_gates_of_its_route(client, seed, world):
    a, b = new_sheet(client, seed), new_sheet(client, seed, route="oversea")
    assert [g["gate"] for g in client.get(f"/v1/sheets/{a}/preflight").json()["gates"]] == \
        ["G1", "G2", "G3", "G4", "G5", "G6"]
    assert [g["gate"] for g in client.get(f"/v1/sheets/{b}/preflight").json()["gates"]] == \
        ["G3", "G4", "G5", "G6", "G7"]


def test_fba_sheet_submission_goes_to_fba_plans_and_draft_has_nothing_to_submit(client, seed, world):
    """★ C-c1（CC-D1）起平台仓 · FBA 表也提交领星（FBA 发货计划）；草稿还没有可提交的单。"""
    a = new_sheet(client, seed)
    put(client, seed, a, line_id=world["line_id"], units=3)
    p = client.get(f"/v1/sheets/{a}/submission").json()
    assert (p["route"], p["orders"], p["blockers"]) == ("fba", [], [])
