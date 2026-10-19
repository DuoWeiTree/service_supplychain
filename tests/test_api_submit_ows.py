"""C-b 提交领星备货单（spec 2026-10-12 §5.2）。★ 领星走替身；断点续跑 · 查重 · 待核实 · 绝不自动重试。"""
import pytest

from shared.pg_client import pg_conn
from tests.helpers import H
from tests.test_api_sheets import (  # noqa: F401
    BOX, OVERSEA, FakeStock, new_sheet, packing_url, put, stock, sync_live, world,
)

WID2 = 3


@pytest.fixture
def confirmed(client, seed, world, stock, lingxing):  # noqa: F811
    """已确认的海外仓表（CA-D13）：海外仓排货单从两个本地仓发（seed.wid 5 件 + WID2 3 件）。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO warehouse (wid, name, kind, market, refreshed_at) VALUES (%s,'二号仓','local',NULL,now())", (WID2,))
        cur.execute("UPDATE sku_catalog SET product_id = 741805 WHERE sku=%s", (seed.sku_a,))
        cur.execute("UPDATE msku_bridge SET fnsku='X00AAA' WHERE seller_sku=%s AND sid=%s", seed.msku_a)
    stock["s"] = FakeStock(buckets={(seed.sku_a, seed.wid): {"shared": 80}, (seed.sku_a, WID2): {"shared": 10}})
    sync_live(lingxing, stock["s"])
    sid = new_sheet(client, seed, route="oversea")
    put(client, seed, sid, line_id=world["line_id"], units=5, channel="oversea", dest_wid=OVERSEA)
    put(client, seed, sid, line_id=world["line_id"], units=3, channel="oversea", dest_wid=OVERSEA, from_wid=WID2)
    client.get("/v1/logistics-channels", headers=H(seed.actor))
    client.put(f"/v1/sheets/{sid}/logistics/{OVERSEA}", json={"logistics_id": 28762}, headers=H(seed.actor))
    seen = client.get(f"/v1/sheets/{sid}").json()["watermark"]
    r = client.post(f"/v1/sheets/{sid}/confirm", json={"seen_watermark": seen}, headers=H(seed.actor))
    assert r.status_code == 200, r.text
    return sid


def preview(client, sid):
    return client.get(f"/v1/sheets/{sid}/submission").json()


def submit(client, seed, sid, token=None):
    return client.post(f"/v1/sheets/{sid}/submit", json={"confirm_token": token or preview(client, sid)["confirm_token"]},
                       headers=H(seed.actor))


def stages(client, sid):
    return {c["channel"]: c["our_stage"] for c in client.get(f"/v1/sheets/{sid}").json()["consignments"]}


def test_preview_lists_one_order_per_from_warehouse_with_payload(client, seed, confirmed):
    p = preview(client, confirmed)
    assert [(o["s_wid"], o["r_wid"], o["logistics_id"], o["ext_no"]) for o in p["orders"]] == [
        (seed.wid, OVERSEA, 28762, None), (WID2, OVERSEA, 28762, None)]
    assert p["orders"][0]["product_list"] == [{"product_id": 741805, "stock_num": 5, "receive_num": 0,
                                                "fnsku": "X00AAA", "sid": seed.seller_a}]
    assert p["blockers"] == [] and p["skipped_fba"] == [] and p["confirm_token"]


def test_submit_creates_every_order_then_consignment_is_dispatched(client, seed, confirmed, lingxing):
    r = submit(client, seed, confirmed)
    assert r.status_code == 200, r.text
    assert [x["outcome"] for x in r.json()["results"]] == ["created", "created"]
    assert [c["status"] for c in lingxing._ows.values()] == ["40", "40"]
    assert lingxing.ows_creates[0]["status"] == 40 and lingxing.ows_creates[0]["logistics_list_type"] == 1
    assert stages(client, confirmed) == {"oversea": "已投送"}
    d = client.get(f"/v1/sheets/{confirmed}").json()
    assert sorted(e["ext_no"] for e in d["ext_refs"]) == sorted(lingxing._ows)
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM erp_call WHERE endpoint='create_ows_order' AND outcome='ok' AND consignment_id IS NOT NULL")
        assert cur.fetchone()[0] == 2


def test_stale_token_is_refused(client, seed, confirmed, lingxing):
    r = submit(client, seed, confirmed, token="nope")
    assert r.status_code == 409 and r.json()["error"] == "stale_confirm_token"
    assert lingxing.ows_creates == []


def test_rejection_stops_and_resume_only_creates_the_rest(client, seed, confirmed, lingxing):
    lingxing.enqueue("create_ows_order", "ok")
    lingxing.enqueue("create_ows_order", "rejected")
    r = submit(client, seed, confirmed).json()
    assert [x["outcome"] for x in r["results"]] == ["created", "rejected"]
    assert r["results"][1]["lx_msg"] == "double: 物流方式不存在"
    assert stages(client, confirmed)["oversea"] == "已确认"
    r = submit(client, seed, confirmed).json()                      # ★ 断点续跑
    assert [x["outcome"] for x in r["results"]] == ["created"]
    assert len(lingxing.ows_creates) == 3 and stages(client, confirmed)["oversea"] == "已投送"


def test_unknown_but_created_is_found_by_lookup_not_created_twice(client, seed, confirmed, lingxing):
    lingxing.enqueue("create_ows_order", "unknown_created")
    r = submit(client, seed, confirmed).json()
    assert [x["outcome"] for x in r["results"]] == ["adopted", "created"]
    assert len(lingxing._ows) == 2 and len(lingxing.ows_creates) == 2


def test_unknown_and_absent_opens_needs_verify_and_never_retries(client, seed, confirmed, lingxing):
    lingxing.enqueue("create_ows_order", "unknown_absent")
    r = submit(client, seed, confirmed).json()
    assert [x["outcome"] for x in r["results"]] == ["needs_verify"]
    assert len(lingxing.ows_creates) == 1
    r = submit(client, seed, confirmed)
    assert r.status_code == 409 and r.json()["error"] == "needs_verify"
    assert len(lingxing.ows_creates) == 1, "★ 待核实时绝不再发"
    assert preview(client, confirmed)["attention"][0]["idem_key"].startswith("SCM-CN-")


def test_lookup_first_adopts_an_order_that_already_exists(client, seed, confirmed, lingxing):
    first = preview(client, confirmed)["orders"][0]
    lingxing._ows_put({"inbound_order_no": first["idem_key"], "s_wid": first["s_wid"], "r_wid": OVERSEA, "product_list": []})
    r = submit(client, seed, confirmed).json()
    assert [x["outcome"] for x in r["results"]] == ["adopted", "created"]
    assert len(lingxing.ows_creates) == 1


def test_not_sent_stops_and_can_simply_be_retried(client, seed, confirmed, lingxing):
    lingxing.enqueue("create_ows_order", "not_sent")
    r = submit(client, seed, confirmed).json()
    assert [x["outcome"] for x in r["results"]] == ["not_sent"]
    assert [x["outcome"] for x in submit(client, seed, confirmed).json()["results"]] == ["created", "created"]


def test_missing_product_id_blocks_before_any_call(client, seed, confirmed, lingxing):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE sku_catalog SET product_id = NULL WHERE sku=%s", (seed.sku_a,))
    r = submit(client, seed, confirmed)
    assert r.status_code == 409 and r.json()["error"] == "submission_blocked"
    assert r.json()["blockers"] == [{"code": "product_id_missing", "sku": seed.sku_a}]
    assert lingxing.ows_creates == []


def test_draft_sheet_cannot_be_submitted(client, seed, world, stock, lingxing):  # noqa: F811
    sid = new_sheet(client, seed)
    r = client.post(f"/v1/sheets/{sid}/submit", json={"confirm_token": "x"}, headers=H(seed.actor))
    assert r.status_code == 409 and r.json()["error"] == "sheet_not_confirmed"


# ── Task 5：待核实三出口 · 取消时删领星单（spec §5.3 / §5.4 · CB-D6）────────────

def needs_verify(client, seed, sid, lingxing):
    lingxing.enqueue("create_ows_order", "unknown_absent")
    r = submit(client, seed, sid).json()
    return r["results"][0]["attention_id"], r["results"][0]["idem_key"]


def test_recheck_adopts_when_the_order_shows_up(client, seed, confirmed, lingxing):
    aid, idem = needs_verify(client, seed, confirmed, lingxing)
    r = client.post(f"/v1/sheets/{confirmed}/ext-refs/verify", json={"attention_id": aid}, headers=H(seed.actor))
    assert r.status_code == 409 and r.json()["error"] == "still_unknown"
    lingxing._ows_put({"inbound_order_no": idem, "s_wid": seed.wid, "r_wid": OVERSEA, "product_list": []})
    r = client.post(f"/v1/sheets/{confirmed}/ext-refs/verify", json={"attention_id": aid}, headers=H(seed.actor))
    assert r.status_code == 200 and r.json()["ext_no"]
    assert preview(client, confirmed)["attention"] == []
    assert [x["outcome"] for x in submit(client, seed, confirmed).json()["results"]] == ["created"]
    assert stages(client, confirmed)["oversea"] == "已投送"


def test_register_checks_the_number_really_is_ours(client, seed, confirmed, lingxing):
    aid, idem = needs_verify(client, seed, confirmed, lingxing)
    other = lingxing._ows_put({"inbound_order_no": "SOMEONE-ELSE", "s_wid": 1, "r_wid": OVERSEA, "product_list": []})
    r = client.post(f"/v1/sheets/{confirmed}/ext-refs/register", json={"attention_id": aid, "ext_no": other},
                    headers=H(seed.actor))
    assert r.status_code == 409 and r.json()["error"] == "ext_no_mismatch"
    mine = lingxing._ows_put({"inbound_order_no": idem, "s_wid": seed.wid, "r_wid": OVERSEA, "product_list": []})
    r = client.post(f"/v1/sheets/{confirmed}/ext-refs/register", json={"attention_id": aid, "ext_no": mine},
                    headers=H(seed.actor))
    assert r.status_code == 200 and preview(client, confirmed)["attention"] == []


def test_confirm_absent_needs_reason_and_then_allows_recreate(client, seed, confirmed, lingxing):
    aid, _ = needs_verify(client, seed, confirmed, lingxing)
    r = client.post(f"/v1/sheets/{confirmed}/ext-refs/confirm-absent", json={"attention_id": aid, "reason": " "},
                    headers=H(seed.actor))
    assert r.status_code == 400 and r.json()["error"] == "reason_required"
    r = client.post(f"/v1/sheets/{confirmed}/ext-refs/confirm-absent", json={"attention_id": aid, "reason": "领星里搜过没有"},
                    headers=H(seed.actor))
    assert r.status_code == 200
    assert [x["outcome"] for x in submit(client, seed, confirmed).json()["results"]] == ["created", "created"]


def test_confirm_absent_refused_when_lingxing_actually_has_it(client, seed, confirmed, lingxing):
    aid, idem = needs_verify(client, seed, confirmed, lingxing)
    lingxing._ows_put({"inbound_order_no": idem, "s_wid": seed.wid, "r_wid": OVERSEA, "product_list": []})
    r = client.post(f"/v1/sheets/{confirmed}/ext-refs/confirm-absent", json={"attention_id": aid, "reason": "x"},
                    headers=H(seed.actor))
    assert r.status_code == 409 and r.json()["error"] == "contradicts_evidence"


def cancel(client, seed, sid):
    return client.post(f"/v1/sheets/{sid}/cancel", json={"reason": "换方案"}, headers=H(seed.actor))


def test_cancel_after_submit_deletes_every_order_in_lingxing(client, seed, confirmed, lingxing):
    submit(client, seed, confirmed)
    r = cancel(client, seed, confirmed)
    assert r.status_code == 200, r.text
    assert lingxing._ows == {}
    d = client.get(f"/v1/sheets/{confirmed}").json()
    assert d["state"] == "已取消" and {c["our_stage"] for c in d["consignments"]} == {"已取消"}
    assert all(e["voided_at"] for e in d["ext_refs"])


def test_cancel_refused_when_any_order_has_shipped_and_nothing_is_deleted(client, seed, confirmed, lingxing):
    submit(client, seed, confirmed)
    shipped = sorted(lingxing._ows)[1]
    lingxing.ship_ows(shipped)
    r = cancel(client, seed, confirmed)
    assert r.status_code == 409 and r.json()["error"] == "ows_not_cancellable"
    assert r.json()["orders"] == [{"ext_no": shipped, "status": "50"}]
    assert len(lingxing._ows) == 2
    assert client.get(f"/v1/sheets/{confirmed}").json()["state"] == "已确认"


def test_cancel_refused_while_a_submission_needs_verify(client, seed, confirmed, lingxing):
    needs_verify(client, seed, confirmed, lingxing)
    r = cancel(client, seed, confirmed)
    assert r.status_code == 409 and r.json()["error"] == "needs_verify"


# ── 终审修复（C1 · C2 · I1 · I2 · I3 · I4）──────────────────────────

def test_half_failed_cancel_blocks_resubmit_until_cancel_finishes(client, seed, confirmed, lingxing):
    """C1：删了一张、另一张删失败 → 表仍已确认；此时不许再提交（会建出一张没人记的真单），只能把取消做完。"""
    submit(client, seed, confirmed)
    lingxing.enqueue("ows_order_delete", "ok")
    lingxing.enqueue("ows_order_delete", "not_sent")
    assert cancel(client, seed, confirmed).status_code == 503
    p = preview(client, confirmed)
    assert [b["code"] for b in p["blockers"]] == ["cancel_incomplete"]
    r = submit(client, seed, confirmed)
    assert r.status_code == 409 and r.json()["error"] == "submission_blocked"
    creates = len(lingxing.ows_creates)
    assert cancel(client, seed, confirmed).status_code == 200
    assert lingxing._ows == {} and len(lingxing.ows_creates) == creates


def test_ext_ref_insert_failure_still_closes_the_call_and_names_the_order(client, seed, confirmed, lingxing, monkeypatch):
    """C1 后半：领星已建、本地记账失败 —— 调用不许卡在 in_flight，结果点名单号。"""
    from repo import dispatch as repo_d

    def boom(*a, **k):
        raise RuntimeError("simulated")
    monkeypatch.setattr(repo_d, "insert_ext_ref", boom)
    r = submit(client, seed, confirmed).json()
    assert r["results"][0]["outcome"] == "unrecorded" and r["results"][0]["ext_no"] in lingxing._ows
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM erp_call WHERE outcome='in_flight'")
        assert cur.fetchone()[0] == 0


def test_submit_and_cancel_refuse_while_another_operation_holds_the_sheet(client, seed, confirmed, lingxing):
    """C2 / I3：同一张表的提交 / 取消 / 核实互斥；撞上的那个 409，不发任何出站。"""
    from usecase import submit_ows
    with submit_ows.sheet_op(confirmed):
        r = submit(client, seed, confirmed)
        assert r.status_code == 409 and r.json()["error"] == "sheet_busy"
        r = cancel(client, seed, confirmed)
        assert r.status_code == 409 and r.json()["error"] == "sheet_busy"
    assert lingxing.ows_creates == []
    assert submit(client, seed, confirmed).status_code == 200


def test_cancel_treats_an_order_already_gone_in_lingxing_as_deleted(client, seed, confirmed, lingxing):
    """I1：上次删除超时其实删掉了 → 详情「不存在或已删除」且按参考号查不到 → 记作废，不永远 503。"""
    submit(client, seed, confirmed)
    gone = sorted(lingxing._ows)[0]
    del lingxing._ows[gone]
    r = cancel(client, seed, confirmed)
    assert r.status_code == 200, r.text
    d = client.get(f"/v1/sheets/{confirmed}").json()
    assert d["state"] == "已取消" and all(e["voided_at"] for e in d["ext_refs"])
    assert next(e for e in d["ext_refs"] if e["ext_no"] == gone)["void_reason"].endswith("（领星已无此单）")


def test_stuck_create_call_is_recovered_into_needs_verify(client, seed, confirmed, lingxing):
    """I2：建单调用中途进程没了（in_flight 卡住）→ 恢复收成 unknown 并开待核实，之后能走三出口。"""
    from usecase import submit_ows
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT consignment_id FROM consignment WHERE sheet_id=%s AND channel='oversea'", (confirmed,))
        cid = cur.fetchone()[0]
        cur.execute("INSERT INTO erp_call (endpoint, side_effect, consignment_id, idem_key, actor, request, started_at)"
                    " VALUES ('create_ows_order','reversible',%s,%s,%s,'{}', now() - interval '1 hour')",
                    (cid, f"SCM-CN-{cid}-{seed.wid}", seed.actor))
    out = submit_ows.recover_stuck(minutes=5)
    assert [o["idem_key"] for o in out] == [f"SCM-CN-{cid}-{seed.wid}"]
    assert [a["idem_key"] for a in preview(client, confirmed)["attention"]] == [f"SCM-CN-{cid}-{seed.wid}"]
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM erp_call WHERE outcome='in_flight'")
        assert cur.fetchone()[0] == 0


def test_two_orders_with_our_reference_go_to_needs_verify_not_adopt_first(client, seed, confirmed, lingxing):
    """I4：查重命中两张（领星若不拒重复参考号）→ 不挑第一张认下，转人工并点名全部单号。"""
    idem = preview(client, confirmed)["orders"][0]["idem_key"]
    a = lingxing._ows_put({"inbound_order_no": idem, "s_wid": seed.wid, "r_wid": OVERSEA, "product_list": []})
    b = lingxing._ows_put({"inbound_order_no": idem, "s_wid": seed.wid, "r_wid": OVERSEA, "product_list": []})
    r = submit(client, seed, confirmed).json()
    assert r["results"][0]["outcome"] == "needs_verify" and lingxing.ows_creates == []
    [att] = preview(client, confirmed)["attention"]
    assert sorted(att["detail"]["hits"]) == sorted([a, b])


# ── 三方海外仓 sku 编码（10-12 实调：魔方云仓 拒「第三方sku不能为空」）────────────

@pytest.fixture
def confirmed_3pl(client, seed, world, stock, lingxing):  # noqa: F811
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE warehouse SET kind='oversea_3pl' WHERE wid=%s", (OVERSEA,))
    lingxing.set_match(OVERSEA, None, "SOMETHING-ELSE", matched=False)      # 接了 API 的仓（有三方产品）
    return confirmed.__wrapped__(client, seed, world, stock, lingxing)


def test_unmatched_product_on_api_warehouse_is_blocked_before_any_create(client, seed, confirmed_3pl, lingxing):
    p = preview(client, confirmed_3pl)
    assert {(b["code"], b["sku"], b["dest_wid"]) for b in p["blockers"]} == {("third_party_sku_missing", seed.sku_a, OVERSEA)}
    r = submit(client, seed, confirmed_3pl)
    assert r.status_code == 409 and r.json()["error"] == "submission_blocked" and lingxing.ows_creates == []


def test_matched_product_is_sent_with_the_third_party_code(client, seed, confirmed_3pl, lingxing):
    lingxing.set_match(OVERSEA, 741805, "DCC-3PL-1")
    r = submit(client, seed, confirmed_3pl)
    assert r.status_code == 200, r.text
    assert [x["outcome"] for x in r.json()["results"]] == ["created", "created"]
    assert {pl["oversea_product_code"] for c in lingxing.ows_creates for pl in c["product_list"]} == {"DCC-3PL-1"}


def test_unreadable_match_list_blocks_and_names_it(client, seed, confirmed_3pl, lingxing):
    lingxing.enqueue("ows_sku_match", "not_sent")
    assert [b["code"] for b in preview(client, confirmed_3pl)["blockers"]] == ["sku_match_unavailable"]


# ── CA-D14 退回修改：没发到领星前都能改；发过就只能取消 ──────────────────────

def reopen(client, seed, sid, reason="选错仓库"):
    return client.post(f"/v1/sheets/{sid}/reopen", json={"reason": reason}, headers=H(seed.actor))


def test_reopen_then_move_destination_then_confirm_again(client, seed, confirmed, lingxing):
    """负责人 10-12：确认后发现选错仓库 —— 不新建表，退回修改、改目的地、再确认。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO warehouse (wid, name, kind, market, refreshed_at) VALUES (7,'另一个海外仓','oversea_self','US',now())")
    r = reopen(client, seed, confirmed)
    assert r.status_code == 200 and r.json()["state"] == "草稿", r.text
    d = client.get(f"/v1/sheets/{confirmed}").json()
    assert d["state"] == "草稿" and {c["our_stage"] for c in d["consignments"]} == {"草稿"}
    grid = client.get(f"/v1/sheets/{confirmed}/grid").json()
    for c in [x for x in grid["cells"] if x["dest_wid"] == OVERSEA]:
        r = put(client, seed, confirmed, line_id=c["line_id"], seller_sku=c["seller_sku"], sid=c["sid"],
                from_wid=c["from_wid"], channel="oversea", dest_wid=7, units=c["units"],
                replaces={"channel": "oversea", "dest_wid": OVERSEA, "dest_sid": None, "from_wid": c["from_wid"]})
        assert r.status_code == 200, r.text
    assert client.put(f"/v1/sheets/{confirmed}/logistics/7", json={"logistics_id": 28762}, headers=H(seed.actor)).status_code == 200
    seen = client.get(f"/v1/sheets/{confirmed}").json()["watermark"]
    r = client.post(f"/v1/sheets/{confirmed}/confirm", json={"seen_watermark": seen}, headers=H(seed.actor))
    assert r.status_code == 200, r.text
    assert {o["r_wid"] for o in preview(client, confirmed)["orders"]} == {7}


def test_reopen_needs_reason_and_only_from_confirmed(client, seed, confirmed, lingxing):
    r = reopen(client, seed, confirmed, reason=" ")
    assert r.status_code == 400 and r.json()["error"] == "reason_required"
    assert reopen(client, seed, confirmed).status_code == 200
    r = reopen(client, seed, confirmed)
    assert r.status_code == 409 and r.json()["error"] == "sheet_not_confirmed"


def test_reopen_refused_once_an_order_is_in_lingxing(client, seed, confirmed, lingxing):
    lingxing.enqueue("create_ows_order", "ok")
    lingxing.enqueue("create_ows_order", "rejected")            # 一张建成、一张被拒 = 部分已发
    submit(client, seed, confirmed)
    r = reopen(client, seed, confirmed)
    assert r.status_code == 409 and r.json()["error"] == "already_submitted", r.text
    assert client.get(f"/v1/sheets/{confirmed}").json()["state"] == "已确认"


def test_reopen_refused_while_another_operation_holds_the_sheet(client, seed, confirmed, lingxing):
    from usecase import submit_ows
    with submit_ows.sheet_op(confirmed):
        r = reopen(client, seed, confirmed)
    assert r.status_code == 409 and r.json()["error"] == "sheet_busy"


def test_reopened_after_rejection_can_move_destination_and_back(client, seed, confirmed, lingxing):
    """10-13 负责人：提交被领星拒 → 退回修改 → 改目的地 500（erp_call 引用着旧排货单，删不掉）。
    带外部留痕的排货单不删，改放弃；改回原目的地也不许撞唯一约束。"""
    lingxing.enqueue("create_ows_order", "rejected")
    submit(client, seed, confirmed)
    assert reopen(client, seed, confirmed).status_code == 200
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO warehouse (wid, name, kind, market, refreshed_at) VALUES (7,'另一个海外仓','oversea_self','US',now())")
        cur.execute("SELECT consignment_id FROM consignment WHERE sheet_id=%s", (confirmed,))
        [old] = [r[0] for r in cur.fetchall()]

    def move(src, dst):
        for c_ in [x for x in client.get(f"/v1/sheets/{confirmed}/grid").json()["cells"] if x["dest_wid"] == src]:
            r = put(client, seed, confirmed, line_id=c_["line_id"], seller_sku=c_["seller_sku"], sid=c_["sid"],
                    from_wid=c_["from_wid"], channel="oversea", dest_wid=dst, units=c_["units"],
                    replaces={"channel": "oversea", "dest_wid": src, "dest_sid": None, "from_wid": c_["from_wid"]})
            assert r.status_code == 200, r.text
    move(OVERSEA, 7)
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT our_stage FROM consignment WHERE consignment_id=%s", (old,))
        assert cur.fetchone()[0] == "已放弃"                          # 留痕在，没删
        cur.execute("SELECT count(*) FROM erp_call WHERE consignment_id=%s", (old,))
        assert cur.fetchone()[0] >= 1
    move(7, OVERSEA)                                                  # 改回原目的地：新开一张，不撞已放弃的
    stages_ = {(c_["dest_wid"], c_["our_stage"]) for c_ in client.get(f"/v1/sheets/{confirmed}").json()["consignments"]}
    assert (OVERSEA, "已放弃") in stages_ and (OVERSEA, "草稿") in stages_


def test_after_dispatch_this_sheet_is_not_counted_as_staged_elsewhere(client, seed, confirmed):
    """评审 P1 #11：staged 不含已投送，staged_here 也不许含 —— 否则「别的表已排未发」= −已投送。"""
    assert submit(client, seed, confirmed).status_code == 200
    [row] = client.get(f"/v1/sheets/{confirmed}/grid").json()["demand"]
    assert {k: row[k] for k in ("staged", "staged_here", "staged_other", "dispatched", "dispatched_here")} == \
        {"staged": 0, "staged_here": 0, "staged_other": 0, "dispatched": 8, "dispatched_here": 8}
