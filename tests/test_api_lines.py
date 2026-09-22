from helpers import H, prepared

from api.ui.dashboard import UNREACHABLE_IN_STAGE_A
from shared.pg_client import pg_conn


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

    # ★ 名字承诺了 sku 与 period 两种 group_by，只测 sku 那半名实不符（review finding 5）
    body = client.get("/v1/plan-lines", params={"plan_ids": p1, "group_by": "period"},
                      headers=H(seed.actor)).json()
    assert body["groups"]["2026-10"] == {"lines": 1, "total_units": 500, "demand_at_submit": 240}


def test_unknown_state_filter_is_400_not_a_silent_empty_result(client, seed):
    """★ Finding 2：state= 传错值不许悄悄返回空集——那跟「这个范围里确实没有
    这个状态」长得一模一样，人分不出是打错了字还是问对了但没有。"""
    p1, _ = submitted(client, seed)
    r = client.get("/v1/plan-lines", params={"plan_ids": p1, "state": "不存在的状态"},
                   headers=H(seed.actor))
    assert r.status_code == 400 and r.json()["error"] == "bad_state"
    assert r.json()["allowed"] == ["已提交", "已确认", "已下单", "准备排货", "已排货",
                                   "已完结", "已撤销"]


def test_unreachable_stage_a_states_are_real_states_in_the_whitelist(client, seed):
    """★ Finding 8：dashboard.UNREACHABLE_IN_STAGE_A 是手写列表，跟白名单脱钩
    会悄悄失真——这里把它钉在真实白名单（bad_state 的 allowed[]，与
    _allowed_next 同一个出处）上，状态改名而没同步就在这里炸给人看，
    而不是让看板悄悄一直谎报「阶段 A 到不了」。"""
    r = client.get("/v1/plan-lines", params={"state": "不存在的状态"}, headers=H(seed.actor))
    known = set(r.json()["allowed"])
    assert set(UNREACHABLE_IN_STAGE_A) <= known


def test_excluded_reports_a_separate_drop_count_per_filter(client, seed):
    """★ Finding 1：excluded{} 必须逐 filter 各自计数，而不是合并成一个匿名总数——
    state 与 period 在同一份候选集里各自丢掉不同数量的行，一个数分不清是谁丢的。"""
    p1 = prepared(client, seed)
    client.put(f"/v1/plans/{p1}/purchase/{seed.sku_a}/2026-11",
              json={"planned_units": 300}, headers=H(seed.actor))
    client.put(f"/v1/plans/{p1}/purchase/{seed.sku_a}/2026-12",
              json={"planned_units": 200}, headers=H(seed.actor))
    client.post(f"/v1/plans/{p1}/submit", headers=H(seed.actor))
    p2, _ = submitted_other_msku(client, seed)

    lines_p1 = client.get("/v1/plan-lines", params={"plan_ids": p1},
                          headers=H(seed.actor)).json()["lines"]
    nov_line = next(row for row in lines_p1 if row["period"] == "2026-11")
    r = client.post(f"/v1/plan-lines/{nov_line['line_id']}/cancel",
                    json={"reason": "改方案"}, headers=H(seed.actor))
    assert r.status_code == 200

    # 候选集（plan_ids=p1,p2）：sku_a/10月/已提交、sku_a/11月/已撤销、
    # sku_a/12月/已提交、sku_b/10月/已提交 —— 4 行
    body = client.get("/v1/plan-lines", params={"plan_ids": f"{p1},{p2}",
                                                 "state": "已提交", "period": "2026-10"},
                      headers=H(seed.actor)).json()
    assert body["excluded"]["state"] == 1    # 只有 11 月那条被撤销，丢 1
    assert body["excluded"]["period"] == 2   # 11 月 + 12 月都不是 10 月，丢 2
    assert body["excluded"]["sku"] == 0
    assert body["excluded"]["category"] == 0


def test_excluded_never_counts_rows_outside_plan_ids(client, seed):
    """★ Finding 1：候选集只按 plan_ids 圈定——没被勾选的那张计划的行，
    不管过滤器是什么，都不该出现在任何一个 excluded 桶里。"""
    p1, _ = submitted(client, seed)
    submitted_other_msku(client, seed)   # p2：刻意不放进 plan_ids
    body = client.get("/v1/plan-lines", params={"plan_ids": p1, "state": "已撤销"},
                      headers=H(seed.actor)).json()
    assert body["lines"] == []
    # 候选集只有 p1 那 1 行（已提交）；state=已撤销 把它整条丢掉——
    # p2 完全没进候选集，不该被算进任何一个桶
    assert body["excluded"] == {"state": 1, "sku": 0, "category": 0, "period": 0}


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
    # ★ Finding 6：测试名字说「记录了理由」，光看状态变化证明不了理由真的落库了
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT reason FROM plan_line_event"
                    " WHERE line_id = %s AND to_state = '已撤销'", (line,))
        reason = cur.fetchone()[0]
    assert reason == "改方案"


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
