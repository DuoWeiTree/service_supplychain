from helpers import H, prepared


def submitted(client, seed):
    pid = prepared(client, seed)
    client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    line = client.get("/v1/plan-lines", params={"plan_ids": pid},
                      headers=H(seed.actor)).json()["lines"][0]
    return pid, line["line_id"]


def submitted_other_msku(client, seed):
    """跟 submitted() 一样的流程，换一组 msku/sku（msku_d → sku_b）。

    ★ prepared() 固定认领 msku_a/msku_c（同归 sku_a）；同一个 seed 里对着
    同一组 msku 调 prepared() 两次，第二次会撞 msku_claim_one_active_idx
    （409 msku_already_claimed）—— 认领互斥是既有裁决（02 §2.2），不是本任务的接口
    该绕开的东西。两张计划要各自站得住脚，只能各占各的 msku。
    """
    pid = client.post("/v1/plans", json={"title": "11 月计划", "period_start": "2026-10-01",
                                          "months": 3}, headers=H(seed.actor)).json()["plan_id"]
    client.post(f"/v1/plans/{pid}/claims",
               json={"seller_sku": seed.msku_d[0], "sid": seed.msku_d[1]}, headers=H(seed.actor))
    client.put(f"/v1/plans/{pid}/demand/{seed.msku_d[0]}/{seed.msku_d[1]}/2026-10",
              json={"expected_units": 120}, headers=H(seed.actor))
    client.put(f"/v1/plans/{pid}/purchase/{seed.sku_b}/2026-10",
              json={"planned_units": 500}, headers=H(seed.actor))
    client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    line = client.get("/v1/plan-lines", params={"plan_ids": pid},
                      headers=H(seed.actor)).json()["lines"][0]
    return pid, line["line_id"]


def test_lines_can_be_viewed_across_several_plans(client, seed):
    """★ G3：勾选多份联合查看。"""
    p1, _ = submitted(client, seed)
    p2, _ = submitted_other_msku(client, seed)
    body = client.get("/v1/plan-lines", params={"plan_ids": f"{p1},{p2}"},
                      headers=H(seed.actor)).json()
    assert {r["plan_id"] for r in body["lines"]} == {p1, p2}


def test_group_by_sku_and_period(client, seed):
    p1, _ = submitted(client, seed)
    body = client.get("/v1/plan-lines", params={"plan_ids": p1, "group_by": "sku"},
                      headers=H(seed.actor)).json()
    assert body["groups"][seed.sku_a]["total_units"] == 500


def test_category_is_refused_loudly_not_ignored(client, seed):
    """★ 阶段 A 没有品类镜像（A-1 属另一条线）。静默忽略这个参数，
    返回的是「全量」而不是报错 —— 而全量看起来完全正常。"""
    p1, _ = submitted(client, seed)
    r = client.get("/v1/plan-lines", params={"plan_ids": p1, "group_by": "category"},
                   headers=H(seed.actor))
    assert r.status_code == 400 and r.json()["error"] == "category_not_available"


def test_cancel_one_line_requires_a_reason(client, seed):
    _, line = submitted(client, seed)
    r = client.post(f"/v1/plan-lines/{line}/cancel", json={}, headers=H(seed.actor))
    assert r.status_code == 400 and r.json()["error"] == "reason_required"


def test_cancel_one_line_records_the_reason_on_it(client, seed):
    _, line = submitted(client, seed)
    r = client.post(f"/v1/plan-lines/{line}/cancel", json={"reason": "改方案"},
                    headers=H(seed.actor))
    assert r.status_code == 200 and r.json()["state"] == "已撤销"


def test_cancelling_a_terminal_line_is_422_terminal_state(client, seed):
    _, line = submitted(client, seed)
    client.post(f"/v1/plan-lines/{line}/cancel", json={"reason": "改方案"}, headers=H(seed.actor))
    r = client.post(f"/v1/plan-lines/{line}/cancel", json={"reason": "再撤一次"},
                    headers=H(seed.actor))
    assert r.status_code == 422 and r.json()["error"] == "terminal_state"
    assert r.json()["state"] == "已撤销"


def test_illegal_transition_echoes_where_it_could_go(client, seed):
    """★ 只说「不许」而不说「那能去哪」，前端只能猜（04 §1.4）。"""
    _, line = submitted(client, seed)
    r = client.post(f"/v1/plan-lines/{line}/transition",
                    json={"to_state": "已排货"}, headers=H(seed.actor))
    assert r.status_code == 422 and r.json()["error"] == "illegal_transition"
    assert r.json()["allowed"] == ["已确认", "已撤销"]


def test_unknown_state_value_is_400(client, seed):
    _, line = submitted(client, seed)
    r = client.post(f"/v1/plan-lines/{line}/transition", json={"to_state": "飞了"},
                    headers=H(seed.actor))
    assert r.status_code == 400 and r.json()["error"] == "unknown_state"
