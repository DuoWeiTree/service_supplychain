"""`/v1/dispatch/plans` 的排货表「下一步」事实（spec 2026-10-15 §5 · DL-D7：只用 PG 已存的事实，不出站）。"""
from repo import dispatch as dispatch_repo
from shared.pg_client import pg_conn
from tests.helpers import H
from tests.test_api_sheets import BOX, OVERSEA, add_line, new_sheet, packing_url, put, stock, world  # noqa: F401
from tests.test_api_submit_fba import confirmed_fba  # noqa: F401
from tests.test_api_submit_ows import confirmed, submit  # noqa: F401

FACTS = ("in_flight", "attention", "drift", "to_lock", "to_submit", "packing_missing", "logistics_missing",
         "consignments", "all_submitted")
ZERO = {"in_flight": 0, "attention": 0, "drift": [], "to_lock": 0, "to_submit": 0, "packing_missing": 0,
        "logistics_missing": 0, "consignments": 0, "all_submitted": False}


def facts(client, sheet_id):
    sheets = [s for p in client.get("/v1/dispatch/plans").json()["plans"] for s in p["sheets"]]
    s = next(s for s in sheets if s["sheet_id"] == sheet_id)
    return {k: s[k] for k in FACTS}


def test_empty_draft_has_nothing(client, seed, world):
    assert facts(client, new_sheet(client, seed)) == ZERO


def test_fba_draft_counts_missing_packing_lines_until_filled(client, seed, world):
    sid = new_sheet(client, seed)
    put(client, seed, sid, line_id=world["line_id"], units=3)
    assert facts(client, sid) == {**ZERO, "consignments": 1, "packing_missing": 1}
    assert client.put(packing_url(sid, seed), json=BOX, headers=H(seed.actor)).status_code == 200
    assert facts(client, sid) == {**ZERO, "consignments": 1}


def test_oversea_draft_counts_consignments_without_logistics(client, seed, world):
    sid = new_sheet(client, seed, route="oversea")
    put(client, seed, sid, line_id=world["line_id"], units=3, channel="oversea", dest_wid=OVERSEA)
    assert facts(client, sid) == {**ZERO, "consignments": 1, "logistics_missing": 1}
    client.get("/v1/logistics-channels", headers=H(seed.actor))
    assert client.put(f"/v1/sheets/{sid}/logistics/{OVERSEA}", json={"logistics_id": 28762},
                      headers=H(seed.actor)).status_code == 200
    assert facts(client, sid) == {**ZERO, "consignments": 1}


def test_confirmed_oversea_counts_each_consignment_source_pair_until_submitted(client, seed, confirmed):
    """DL-D11：一张排货单从两个来源仓发 = 两张领星备货单（CB-D5），与详情页「提交领星（2 张）」同一个 N。"""
    assert facts(client, confirmed) == {**ZERO, "consignments": 1, "to_submit": 2}
    assert submit(client, seed, confirmed).status_code == 200
    assert facts(client, confirmed) == {**ZERO, "consignments": 1, "all_submitted": True}


def test_unknown_create_result_is_counted_as_attention(client, seed, confirmed, lingxing):
    lingxing.enqueue("create_ows_order", "unknown_absent")
    submit(client, seed, confirmed)
    f = facts(client, confirmed)
    assert (f["attention"], f["in_flight"], f["to_submit"]) == (1, 0, 2)


def test_drift_is_named_per_live_order(client, seed, confirmed, lingxing):
    submit(client, seed, confirmed)
    del lingxing._ows[sorted(lingxing._ows)[0]]
    assert client.post(f"/v1/sheets/{confirmed}/receipt", headers=H(seed.actor)).status_code == 200
    assert facts(client, confirmed)["drift"] == ["领星里没有这张单"]


def test_in_flight_write_call_shows_on_list_and_detail_read_calls_do_not(client, seed, confirmed):
    cid = client.get(f"/v1/sheets/{confirmed}").json()["consignments"][0]["consignment_id"]
    with pg_conn() as c, c.cursor() as cur:
        dispatch_repo.insert_write_call(cur, endpoint="create_ows_order", side_effect="irreversible",
                                        consignment_id=cid, idem_key=f"SCM-CN-{cid}-1", actor=seed.actor, request={})
        dispatch_repo.insert_write_call(cur, endpoint="ows_order_nos", side_effect="read",
                                        consignment_id=cid, idem_key=None, actor=seed.actor, request={})
    assert facts(client, confirmed)["in_flight"] == 1
    assert client.get(f"/v1/sheets/{confirmed}").json()["in_flight"] == 1


def test_fba_plan_built_but_not_locked_is_to_lock_not_to_submit(client, seed, confirmed_fba, lingxing):
    assert facts(client, confirmed_fba) == {**ZERO, "consignments": 1, "to_submit": 1}
    lingxing.enqueue("fba_plan_lock", "rejected")
    submit(client, seed, confirmed_fba)
    assert facts(client, confirmed_fba) == {**ZERO, "consignments": 1, "to_lock": 1}
    submit(client, seed, confirmed_fba)                      # 只补锁，不重建（CC-D3）
    assert facts(client, confirmed_fba) == {**ZERO, "consignments": 1, "all_submitted": True}


def test_records_carry_confirmed_and_ordered(client, seed, world):
    with pg_conn() as c, c.cursor() as cur:
        l2 = add_line(cur, world["plan_id"], seed.sku_a, 180, [seed.seller_a])
    [p] = client.get("/v1/dispatch/plans").json()["plans"]
    got = {r["line_id"]: (r["confirmed"], r["ordered"]) for r in p["records"]}
    assert got == {world["line_id"]: (100, 100), l2: (0, 0)}


def test_a_failing_part_is_named_not_silently_defaulted(client, seed, world, monkeypatch):
    new_sheet(client, seed)

    def boom(cur, sheet_ids):
        raise RuntimeError("boom")

    monkeypatch.setattr(dispatch_repo, "sheets_open_attention", boom)
    r = client.get("/v1/dispatch/plans")
    assert r.status_code == 500, r.text
    assert (r.json()["error"], r.json()["part"], r.json()["err_type"]) == ("dispatch_plans_failed", "attention",
                                                                           "RuntimeError")
