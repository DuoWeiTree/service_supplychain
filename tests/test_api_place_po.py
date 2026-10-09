import json

import pytest

from shared.pg_client import pg_conn
from tests.helpers import H, mint


@pytest.fixture
def draft(client, seed):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO supplier VALUES ('8085','01',now())")
        _, lid = mint(cur, seed.actor, seed.sku_a, total=10)
        cur.execute("UPDATE plan_line SET demand_by_seller=%s::jsonb WHERE line_id=%s",
                    (json.dumps({seed.seller_a: {"units": 5, "basis": "human"}}), lid))
    body = {"supplier_id": "8085", "purchaser_id": 111, "opt_uid": 10925905, "qc_type": 2,
            "lines": [{"sku": seed.sku_a, "wid": seed.wid, "units": 10, "price": "2",
                       "sources": [{"line_id": lid, "seller_id": seed.seller_a, "units": 10}]}]}
    po_id = client.post("/v1/purchase-orders", json=body, headers=H(seed.actor)).json()["po_id"]
    return po_id, lid


def _place(client, seed, po_id, token=None):
    token = token or client.get(f"/v1/purchase-orders/{po_id}").json()["confirm_token"]
    return client.post(f"/v1/purchase-orders/{po_id}/place", json={"confirm_token": token},
                       headers=H(seed.actor))


def _line_state(lid):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT state FROM plan_line WHERE line_id=%s", (lid,))
        return cur.fetchone()[0]


def _calls(po_id):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT endpoint, outcome, retry_of IS NOT NULL FROM erp_call WHERE po_id=%s ORDER BY call_id",
                    (po_id,))
        return cur.fetchall()


def test_ok_freezes_po_and_moves_line_to_ordered(client, seed, draft):
    po_id, lid = draft
    r = _place(client, seed, po_id)
    assert r.status_code == 200 and r.json()["outcome"] == "placed"
    po = client.get(f"/v1/purchase-orders/{po_id}").json()
    assert po["state"] == "已下单" and po["order_sn"].startswith("FAKE-PO") and "placed_mode" not in po
    assert po["lines"][0]["lx_item_id"]                     # 读回首检回填了行号
    assert _line_state(lid) == "已下单"
    assert _calls(po_id) == [("create_purchase_order", "ok", False), ("purchase_order_list", "ok", False)]


def test_stale_token_refused(client, seed, draft):
    po_id, _ = draft
    r = _place(client, seed, po_id, token="nope")
    assert r.status_code == 409 and r.json()["error"] == "stale_confirm_token"


def test_rejected_keeps_draft_and_echoes_lingxing(client, seed, draft, lingxing):
    po_id, lid = draft
    lingxing.enqueue("create_purchase_order", "rejected")
    r = _place(client, seed, po_id)
    assert r.json() | {"erp_call_id": None} == {"outcome": "rejected", "po_id": po_id, "lx_code": "1001",
                                                "lx_msg": "double: 供应商不存在", "erp_call_id": None}
    assert client.get(f"/v1/purchase-orders/{po_id}").json()["state"] == "草稿"
    assert _line_state(lid) == "已确认"


def test_delete_after_attempt_discards_and_keeps_evidence(client, seed, draft, lingxing):
    po_id, lid = draft
    lingxing.enqueue("create_purchase_order", "rejected")
    _place(client, seed, po_id)
    assert client.delete(f"/v1/purchase-orders/{po_id}", headers=H(seed.actor)).status_code == 204
    po = client.get(f"/v1/purchase-orders/{po_id}").json()
    assert po["discarded_at"] and po["lines"] == []
    assert _line_state(lid) == "已提交"                    # 额度释放
    assert _calls(po_id) == [("create_purchase_order", "rejected", False)]   # 证据还在


def test_retry_after_not_sent_is_chained(client, seed, draft, lingxing):
    po_id, _ = draft
    lingxing.enqueue("create_purchase_order", "not_sent")
    _place(client, seed, po_id)
    _place(client, seed, po_id)
    creates = [c for c in _calls(po_id) if c[0] == "create_purchase_order"]
    assert creates == [("create_purchase_order", "not_sent", False), ("create_purchase_order", "ok", True)]


def test_not_sent_is_safe_to_retry(client, seed, draft, lingxing):
    po_id, _ = draft
    lingxing.enqueue("create_purchase_order", "not_sent")
    assert _place(client, seed, po_id).json()["outcome"] == "not_sent"
    assert _place(client, seed, po_id).json()["outcome"] == "placed"


def test_unknown_but_created_is_found_by_lookup(client, seed, draft, lingxing):
    """Review Focus #2：发出去了没回音，但领星建了单 —— 查重找回，不重下。"""
    po_id, lid = draft
    lingxing.enqueue("create_purchase_order", "unknown_created")
    r = _place(client, seed, po_id)
    assert r.json()["outcome"] == "placed" and r.json()["found_by"] == "lookup"
    assert _line_state(lid) == "已下单"
    assert [c[0] for c in _calls(po_id)].count("create_purchase_order") == 1


def test_unknown_absent_goes_to_needs_verify_and_blocks_everything(client, seed, draft, lingxing):
    """Review Focus #2 / #4：查不到 → 转人工；此后改 / 删 / 再下单一律 409 needs_verify。"""
    po_id, _ = draft
    lingxing.enqueue("create_purchase_order", "unknown_absent")
    r = _place(client, seed, po_id)
    assert r.json()["outcome"] == "needs_verify"
    po = client.get(f"/v1/purchase-orders/{po_id}").json()
    assert po["state"] == "草稿" and [a["kind"] for a in po["attention"]] == ["needs_verify"]
    again = _place(client, seed, po_id)
    assert again.status_code == 409 and again.json()["error"] == "needs_verify"
    assert client.delete(f"/v1/purchase-orders/{po_id}", headers=H(seed.actor)).json()["error"] == "needs_verify"
    assert [c[0] for c in _calls(po_id)].count("create_purchase_order") == 1


def test_stuck_in_flight_is_recovered_by_lookup(client, seed, draft, lingxing):
    """Review Focus #3：领星成功、本地还没记账就被杀 —— 启动恢复按幂等键找回。"""
    from usecase import place_po
    po_id, lid = draft
    # 造现场：领星侧已有单，本地只有一条 in_flight
    lingxing.enqueue("create_purchase_order", "unknown_created")
    from erp import gateway
    gateway.call("create_purchase_order", {"custom_order_sn": f"SCM-PO-{po_id}", "supplier_id": 8085,
                                           "purchaser_id": 111, "opt_uid": 1, "qc_type": 2, "wid": seed.wid,
                                           "product_list": [{"sku": seed.sku_a, "wid": seed.wid,
                                                             "quantity_real": 10, "price": "2"}]})
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO erp_call (endpoint, side_effect, po_id, idem_key, actor, request,"
                    " started_at) VALUES ('create_purchase_order','irreversible',%s,%s,%s,'{}',"
                    " now() - interval '1 hour')", (po_id, f"SCM-PO-{po_id}", seed.actor))
    out = place_po.recover_stuck()
    assert [o["po_id"] for o in out] == [po_id]
    assert client.get(f"/v1/purchase-orders/{po_id}").json()["state"] == "已下单"
    assert _line_state(lid) == "已下单"


def test_placing_a_discarded_draft_is_po_discarded(client, seed, draft, lingxing):
    """控制者裁定 1：作废草稿（明细已清）不能落到 preflight_failed 之类的次生错误上。"""
    po_id, _ = draft
    lingxing.enqueue("create_purchase_order", "rejected")
    _place(client, seed, po_id)
    client.delete(f"/v1/purchase-orders/{po_id}", headers=H(seed.actor))
    r = _place(client, seed, po_id)
    assert r.status_code == 409 and r.json()["error"] == "po_discarded"
    assert [c[0] for c in _calls(po_id)].count("create_purchase_order") == 1


# ---- fix round 1：下单后「发出过但没记账」的窗口 ----

def _token(client, po_id):
    return client.get(f"/v1/purchase-orders/{po_id}").json()["confirm_token"]


def _creates(po_id):
    return [c for c in _calls(po_id) if c[0] == "create_purchase_order"]


def _attentions(po_id):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT kind, resolution, detail FROM po_attention WHERE po_id=%s ORDER BY attention_id",
                    (po_id,))
        return cur.fetchall()


def _insert_call_row(po_id, actor, outcome, age="1 hour"):
    """直接造一条 create 调用现场。in_flight 以外的带 finished_at。"""
    fin = "NULL" if outcome == "in_flight" else f"now() - interval '{age}' + interval '1 second'"
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO erp_call (endpoint, side_effect, po_id, idem_key, actor, request,"
                    f" outcome, started_at, finished_at) VALUES ('create_purchase_order','irreversible',"
                    f" %s, %s, %s, '{{}}', %s, now() - interval '{age}', {fin}) RETURNING call_id",
                    (po_id, f"SCM-PO-{po_id}", actor, outcome))
        return cur.fetchone()[0]


def test_second_click_during_lookup_is_refused(client, seed, draft, monkeypatch, lingxing):
    from erp import gateway
    from usecase import place_po
    from usecase.draft_po import Conflict
    po_id, _ = draft
    token = _token(client, po_id)
    lingxing.enqueue("create_purchase_order", "unknown_absent")
    real, inner = gateway.call, {}

    def spy(name, params):
        if name == "purchase_order_list" and not inner:
            try:
                place_po.place(po_id, token, seed.actor)     # 第二次点击落在查重窗口
            except Conflict as e:
                inner["err"] = e.error
        return real(name, params)
    monkeypatch.setattr(gateway, "call", spy)
    place_po.place(po_id, token, seed.actor)
    assert inner["err"] == "needs_verify"
    assert len(_creates(po_id)) == 1


def test_ok_txn_failure_leaves_in_flight_and_recovery_finds_order(client, seed, draft, monkeypatch):
    """记账事务整体回滚 → 调用仍是 in_flight（挡住重下）；启动恢复按幂等键找回，不重下。"""
    from usecase import place_po
    po_id, lid = draft
    token = _token(client, po_id)

    def boom(*a, **k):
        raise RuntimeError("die in settle txn")
    real_mark_placed = place_po.repo.mark_placed
    monkeypatch.setattr(place_po.repo, "mark_placed", boom)
    with pytest.raises(RuntimeError):
        place_po.place(po_id, token, seed.actor)
    # ★ 不用 monkeypatch.undo()：它会连 conftest 注入的领星替身一起撤掉
    monkeypatch.setattr(place_po.repo, "mark_placed", real_mark_placed)
    with pytest.raises(Exception) as ei:
        place_po.place(po_id, token, seed.actor)
    assert ei.value.error == "po_placing"
    out = place_po.recover_stuck(0)
    assert [o["outcome"] for o in out] == ["placed"]
    assert len(_creates(po_id)) == 1 and _line_state(lid) == "已下单"


def test_crash_after_close_unknown_is_covered_by_attention(client, seed, draft, monkeypatch, lingxing):
    """close_call 与待核实同事务：之后查重阶段崩溃，也不会留下可重下的窗口。"""
    from usecase import place_po
    po_id, _ = draft
    token = _token(client, po_id)
    lingxing.enqueue("create_purchase_order", "unknown_absent")

    def boom(*a, **k):
        raise RuntimeError("die in lookup")
    real_lookup = place_po.lookup_and_settle
    monkeypatch.setattr(place_po, "lookup_and_settle", boom)
    with pytest.raises(RuntimeError):
        place_po.place(po_id, token, seed.actor)
    monkeypatch.setattr(place_po, "lookup_and_settle", real_lookup)   # 同上：不 undo 替身
    assert [a[0] for a in _attentions(po_id)] == ["needs_verify"]
    r = _place(client, seed, po_id)
    assert r.status_code == 409 and r.json()["error"] == "needs_verify"
    assert len(_creates(po_id)) == 1


def test_self_heal_refuses_and_opens_attention(client, seed, draft):
    """旧数据 / 异常路径留下「create=unknown 却无待核实」的草稿：place 补开待核实再拒。"""
    po_id, _ = draft
    _insert_call_row(po_id, seed.actor, "unknown")
    r = _place(client, seed, po_id)
    assert r.status_code == 409 and r.json()["error"] == "needs_verify"
    att = _attentions(po_id)
    assert [a[0] for a in att] == ["needs_verify"] and att[0][2]["opened_by"] == "self_heal"
    assert len(_creates(po_id)) == 1


def test_confirmed_absent_later_than_the_call_allows_replace(client, seed, draft):
    po_id, _ = draft
    cid = _insert_call_row(po_id, seed.actor, "unknown", age="2 hours")
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO po_attention (po_id, kind, opened_by_call) VALUES (%s,'needs_verify',%s)"
                    " RETURNING attention_id", (po_id, cid))
        aid = cur.fetchone()[0]
        cur.execute("UPDATE po_attention SET resolved_at=now(), resolution='confirmed_absent', resolved_by=%s"
                    " WHERE attention_id=%s", (seed.actor, aid))
    assert _place(client, seed, po_id).json()["outcome"] == "placed"


def test_recover_scans_unsettled_drafts(client, seed, draft):
    from usecase import place_po
    po_id, _ = draft
    _insert_call_row(po_id, seed.actor, "unknown")
    out = place_po.recover_stuck()
    assert [o["outcome"] for o in out] == ["needs_verify"]
    att = _attentions(po_id)
    assert [a[0] for a in att] == ["needs_verify"] and att[0][2]["opened_by"] == "recover"


@pytest.mark.parametrize("lookup_outcome", ["not_sent", "rejected", "unknown_absent"])
def test_failed_lookup_is_not_absence(client, seed, draft, lookup_outcome, lingxing):
    po_id, lid = draft
    lingxing.enqueue("create_purchase_order", "unknown_created")     # 领星其实建了单
    lingxing.enqueue("purchase_order_list", lookup_outcome)          # 但查重本身失败
    r = _place(client, seed, po_id)
    assert r.json()["outcome"] == "needs_verify" and r.json()["lookup_outcome"] != "ok"
    assert _line_state(lid) == "已确认"
    assert _place(client, seed, po_id).json()["error"] == "needs_verify"
    assert len(_creates(po_id)) == 1


def test_truncated_lookup_keeps_needs_verify(client, seed, draft, monkeypatch, lingxing):
    import dataclasses

    from erp import gateway
    po_id, _ = draft
    lingxing.enqueue("create_purchase_order", "unknown_absent")
    real = gateway.call

    def spy(name, params):
        ep, r = real(name, params)
        if name == "purchase_order_list":
            r = dataclasses.replace(r, body={**r.body, "total": 999})
        return ep, r
    monkeypatch.setattr(gateway, "call", spy)
    r = _place(client, seed, po_id).json()
    assert r["outcome"] == "needs_verify" and r["truncated"] is True


def test_ok_without_order_sn_goes_through_lookup(client, seed, draft, monkeypatch):
    from usecase import place_po
    monkeypatch.setattr(place_po, "extract_order_sn", lambda body: None)
    po_id, lid = draft
    r = _place(client, seed, po_id).json()
    assert r["outcome"] == "placed" and r["found_by"] == "lookup"
    assert _line_state(lid) == "已下单" and len(_creates(po_id)) == 1
    assert [a[1] for a in _attentions(po_id)] == ["rechecked_found"]


def test_concurrent_click_is_po_placing(client, seed, draft):
    po_id, _ = draft
    _insert_call_row(po_id, seed.actor, "in_flight", age="1 second")
    r = _place(client, seed, po_id)
    assert r.status_code == 409 and r.json()["error"] == "po_placing"


def test_lookup_call_points_at_the_create_call(client, seed, draft, lingxing):
    po_id, _ = draft
    lingxing.enqueue("create_purchase_order", "unknown_created")
    _place(client, seed, po_id)
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT call_id FROM erp_call WHERE po_id=%s AND endpoint='create_purchase_order'", (po_id,))
        create_id = cur.fetchone()[0]
        cur.execute("SELECT retry_of FROM erp_call WHERE po_id=%s AND endpoint='purchase_order_list'"
                    " ORDER BY call_id", (po_id,))
        assert cur.fetchall()[0][0] == create_id


def test_recover_with_order_absent_goes_needs_verify(client, seed, draft):
    from usecase import place_po
    po_id, lid = draft
    cid = _insert_call_row(po_id, seed.actor, "in_flight")
    out = place_po.recover_stuck()
    assert [o["outcome"] for o in out] == ["needs_verify"] and out[0]["erp_call_id"] == cid
    assert _line_state(lid) == "已确认"


def test_fresh_in_flight_is_left_alone(client, seed, draft):
    from usecase import place_po
    po_id, _ = draft
    _insert_call_row(po_id, seed.actor, "in_flight", age="1 second")
    assert place_po.recover_stuck() == []
    assert _calls(po_id)[-1][1] == "in_flight"


def test_attention_detail_is_facts_only(client, seed, draft, lingxing):
    po_id, _ = draft
    lingxing.enqueue("create_purchase_order", "unknown_absent")
    _place(client, seed, po_id)
    d = _attentions(po_id)[0][2]
    assert set(d) == {"create_call_id", "create_outcome", "create_cause", "request_confirmed_sent",
                      "custom_order_sn", "opened_by"}
    assert d["create_outcome"] == "unknown" and d["request_confirmed_sent"] is True and d["opened_by"] == "place"
    assert d["custom_order_sn"] == f"SCM-PO-{po_id}"


def test_lookup_on_discarded_po_does_not_mark_placed(client, seed, draft, lingxing):
    from usecase import place_po
    po_id, _ = draft
    lingxing.enqueue("create_purchase_order", "unknown_created")
    lingxing.enqueue("purchase_order_list", "not_sent")
    _place(client, seed, po_id)
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE po SET discarded_at=now(), discarded_by=%s WHERE po_id=%s", (seed.actor, po_id))
    res = place_po.lookup_and_settle(po_id, seed.actor, None, "t")
    assert res["outcome"] == "needs_verify"
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT state, order_sn FROM po WHERE po_id=%s", (po_id,))
        assert cur.fetchone() == ("草稿", None)
    assert _attentions(po_id)[0][1] is None


def test_read_back_failure_does_not_undo_placed(client, seed, draft, monkeypatch):
    from usecase import place_po

    def boom(*a, **k):
        raise RuntimeError("obs insert failed")
    monkeypatch.setattr(place_po, "read_back", boom)
    po_id, lid = draft
    r = _place(client, seed, po_id)
    assert r.status_code == 200 and r.json()["outcome"] == "placed"
    assert _line_state(lid) == "已下单"


# ---- 终审修复波 ----

def test_settle_after_recovery_closed_the_call_is_honest(client, seed, draft, monkeypatch, scm_log):
    """#1：事务② 之前恢复已把这条调用收成 unknown 并查回了单 —— place 不 500、不丢领星给的单号。"""
    from erp import gateway
    from usecase import place_po
    po_id, lid = draft
    token = _token(client, po_id)
    real = gateway.call

    def spy(name, params):
        ep, r = real(name, params)
        if name == "create_purchase_order":
            assert [o["outcome"] for o in place_po.recover_stuck(0)] == ["placed"]
        return ep, r
    monkeypatch.setattr(gateway, "call", spy)
    res = place_po.place(po_id, token, seed.actor)
    assert res["outcome"] == "placed" and res["settled_by"] == "recover"
    assert res["order_sn"] == "FAKE-PO000001" and _line_state(lid) == "已下单"
    assert "op=po_place_settle_lost_race" in scm_log.text and "FAKE-PO000001" in scm_log.text
    assert [c[1] for c in _creates(po_id)] == ["unknown"]


def test_settle_after_recovery_found_nothing_reports_needs_verify(client, seed, draft, monkeypatch, lingxing):
    from erp import gateway
    from usecase import place_po
    po_id, _ = draft
    token = _token(client, po_id)
    real = gateway.call

    def spy(name, params):
        ep, r = real(name, params)
        if name == "create_purchase_order":
            lingxing.enqueue("purchase_order_list", "not_sent")
            place_po.recover_stuck(0)
        return ep, r
    monkeypatch.setattr(gateway, "call", spy)
    res = place_po.place(po_id, token, seed.actor)
    assert res["outcome"] == "needs_verify" and res["settled_by"] == "recover"
    assert [a[0] for a in _attentions(po_id) if a[1] is None] == ["needs_verify"]


def test_stuck_call_is_listed_in_attention(client, seed, draft):
    po_id, _ = draft
    cid = _insert_call_row(po_id, seed.actor, "in_flight", age="1 hour")
    items = client.get("/v1/attention").json()["items"]
    stuck = [i for i in items if i["kind"] == "stuck_call"]
    assert [(i["po_id"], i["call_id"]) for i in stuck] == [(po_id, cid)] and stuck[0]["started_at"]


def test_fresh_in_flight_is_not_listed_as_stuck(client, seed, draft):
    po_id, _ = draft
    _insert_call_row(po_id, seed.actor, "in_flight", age="1 second")
    assert client.get("/v1/attention").json()["items"] == []


def _insert_read_call(po_id, actor, age):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO erp_call (endpoint, side_effect, po_id, actor, request, started_at)"
                    " VALUES ('purchase_order_list','read',%s,%s,'{\"search_field_time\":\"create_time\"}',"
                    f" now() - interval '{age}') RETURNING call_id", (po_id, actor))
        return cur.fetchone()[0]


def test_recover_closes_stale_read_calls_as_unknown(client, seed, draft):
    """#2：卡住的读调用由恢复收尾，否则「确认无此单」永远 lookup_in_progress。"""
    from usecase import place_po
    po_id, _ = draft
    stale = _insert_read_call(po_id, seed.actor, "1 hour")
    fresh = _insert_read_call(po_id, seed.actor, "1 second")
    place_po.recover_stuck()
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT call_id, outcome, cause, finished_at IS NOT NULL FROM erp_call WHERE call_id IN (%s,%s)"
                    " ORDER BY call_id", (stale, fresh))
        rows = cur.fetchall()
    assert rows[0][1] == "unknown" and rows[0][2].startswith("recovered: in_flight >") and rows[0][3]
    assert rows[1][1] == "in_flight"


def test_cancelled_source_line_blocks_place(client, seed, draft):
    """#5：记录被撤销后，草稿里挂着它的映射不能直通不可逆下单。"""
    from tests.helpers import advance
    po_id, lid = draft
    with pg_conn() as c, c.cursor() as cur:
        advance(cur, lid, "已撤销", actor=seed.actor)
    po = client.get(f"/v1/purchase-orders/{po_id}").json()
    gate = next(g for g in po["preflight"] if g["gate"] == "sources_orderable")
    assert gate["ok"] is False and gate["detail"]["offenders"] == [{"line_id": lid, "state": "已撤销"}]
    r = _place(client, seed, po_id)
    assert r.status_code == 409 and r.json()["error"] == "preflight_failed"
    assert any(g["gate"] == "sources_orderable" for g in r.json()["failed"])
    assert _creates(po_id) == []


@pytest.mark.parametrize("code", ["", False, None, "abc"])
def test_ambiguous_rejection_of_irreversible_call_is_unknown(client, seed, draft, monkeypatch, code):
    """#8：2xx 里 code 为空 / false —— 不是明确的业务拒绝，放开草稿就可能重下出第二张真单。"""
    from erp import gateway
    from erp.transport import CallResult
    po_id, _ = draft
    real = gateway.call

    def spy(name, params):
        if name == "create_purchase_order":
            ep = gateway.endpoints.get(name)
            return ep, CallResult("rejected", "fake", ep.path, 5, http_status=200, lx_code=str(code),
                                  body={"code": code, "message": "?"})
        return real(name, params)
    monkeypatch.setattr(gateway, "call", spy)
    r = _place(client, seed, po_id).json()
    assert r["outcome"] == "needs_verify"
    assert _creates(po_id)[0][1] == "unknown"
    assert _place(client, seed, po_id).json()["error"] == "needs_verify"


def test_explicit_business_rejection_still_releases_the_draft(client, seed, draft, monkeypatch):
    from erp import gateway
    from erp.transport import CallResult
    po_id, _ = draft

    def spy(name, params):
        ep = gateway.endpoints.get(name)
        return ep, CallResult("rejected", "fake", ep.path, 5, http_status=200, lx_code="1001",
                              body={"code": "1001", "message": "供应商不存在"})
    monkeypatch.setattr(gateway, "call", spy)
    assert _place(client, seed, po_id).json()["outcome"] == "rejected"


def _created_order(seed, po_id, **over):
    from erp import gateway
    p = {"custom_order_sn": f"SCM-PO-{po_id}", "supplier_id": 8085, "purchaser_id": 111, "opt_uid": 1,
         "qc_type": 2, "wid": seed.wid,
         "product_list": [{"sku": seed.sku_a, "wid": seed.wid, "quantity_real": 10, "price": "2"}]}
    p.update(over)
    gateway.call("create_purchase_order", p)


def test_lookup_hit_that_does_not_match_is_not_adopted(client, seed, draft, lingxing):
    """#7：只凭 custom_order_sn 认单 —— 供应商 / 明细对不上就不记账，开 landed_mismatch 点名差异。"""
    po_id, lid = draft
    lingxing.enqueue("create_purchase_order", "unknown_absent")
    _place(client, seed, po_id)
    _created_order(seed, po_id, supplier_id=9999)
    from usecase import place_po
    res = place_po.lookup_and_settle(po_id, seed.actor, None, "t")
    assert res["outcome"] == "needs_verify" and res["landed_mismatch"] == ["FAKE-PO000001"]
    assert _line_state(lid) == "已确认"
    open_ = {a[0]: a[2] for a in _attentions(po_id) if a[1] is None}
    assert set(open_) == {"needs_verify", "landed_mismatch"}
    assert "supplier_id" in open_["landed_mismatch"]["differences"]
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT state, order_sn FROM po WHERE po_id=%s", (po_id,))
        assert cur.fetchone() == ("草稿", None)


def test_lookup_with_two_orders_on_one_key_is_not_adopted(client, seed, draft, lingxing):
    po_id, lid = draft
    lingxing.enqueue("create_purchase_order", "unknown_absent")
    _place(client, seed, po_id)
    _created_order(seed, po_id)
    _created_order(seed, po_id)
    from usecase import place_po
    res = place_po.lookup_and_settle(po_id, seed.actor, None, "t")
    assert res["outcome"] == "needs_verify"
    assert res["landed_mismatch"] == ["FAKE-PO000001", "FAKE-PO000002"]
    lm = next(a[2] for a in _attentions(po_id) if a[0] == "landed_mismatch")
    assert lm["order_sns"] == ["FAKE-PO000001", "FAKE-PO000002"]
    assert _line_state(lid) == "已确认"


def test_token_covers_expect_arrive_date(client, seed, draft):
    """★ 预计到货日会发给领星（expect_arrive_time）：不靠 draft_version 间接兜，token 自己就要变"""
    po_id, _ = draft
    before = client.get(f"/v1/purchase-orders/{po_id}").json()["confirm_token"]
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE po_line SET expect_arrive_date = DATE '2026-12-01' WHERE po_id = %s", (po_id,))
    after = client.get(f"/v1/purchase-orders/{po_id}").json()["confirm_token"]
    assert before != after
    assert _place(client, seed, po_id, token=before).json()["error"] == "stale_confirm_token"
