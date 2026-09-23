from helpers import H, prepared


def test_plan_counts_return_the_full_set_even_where_stage_a_cannot_reach(client, seed):
    """★ S-20：接口返回全集（诚实），前端按阶段不渲染够不着的态。
    接口自己把它们抹成 0，「没有」和「还没做」就长得一样了。

    ★ P1：桶的宇宙从 `plan_line_state_rank` ∪ {已撤销} 推出来，不手列 ——
      手列的那一版漏掉了 `已确认`（阶段 A 经 transition 真的到得了）、`已完结`
      和 `已撤销`，它们落在 counts 之外**不留痕**。
    """
    pid = prepared(client, seed)
    client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    body = client.get("/v1/dashboard/plans", headers=H(seed.actor)).json()
    assert set(body["counts"]) == {"进行中", "已提交未确认",
                                   "已提交", "已确认", "已下单", "准备排货", "已排货",
                                   "已完结", "已撤销"}
    assert body["counts"]["已提交"] == 1 and body["counts"]["已排货"] == 0
    assert body["scope_note"]["unreachable_in_stage_a"] == ["已下单", "准备排货", "已排货"]


def test_a_confirmed_plan_lands_in_its_own_bucket_not_only_in_progress(client, seed):
    """★ 「已确认」在阶段 A 走得通（POST /plan-lines/{id}/transition 在白名单里），
    却曾经没有自己的桶 —— 这样的计划只在「进行中」里出现一次，
    看板上「提交了还没人确认」和「已经确认过」长得一模一样，而这两件事催的是不同的人。
    """
    pid = prepared(client, seed)
    client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    lines = client.get("/v1/plan-lines", params={"plan_ids": pid},
                       headers=H(seed.actor)).json()["lines"]
    assert lines, "没有铸出记录 —— 下面的断言会是空转的"
    for line in lines:
        r = client.post(f"/v1/plan-lines/{line['line_id']}/transition",
                        json={"to_state": "已确认"}, headers=H(seed.actor))
        assert r.status_code == 200, r.text
    counts = client.get("/v1/dashboard/plans", headers=H(seed.actor)).json()["counts"]
    assert counts["已确认"] == 1
    assert counts["已提交"] == 0 and counts["已提交未确认"] == 0
    assert counts["进行中"] == 1, "★ 已确认仍在进行中 —— 两个口径并存，不是二选一"


def test_unsubmitted_has_two_columns(client, seed):
    """★ 两栏：从未提交 vs 提交后又改过 —— 催的是两种人，合成一栏就催错人。"""
    never = client.post("/v1/plans", json={"title": "没提交过", "period_start": "2026-10-01"},
                        headers=H(seed.actor)).json()["plan_id"]
    changed = prepared(client, seed)
    client.post(f"/v1/plans/{changed}/submit", headers=H(seed.actor))
    client.put(f"/v1/plans/{changed}/purchase/{seed.sku_a}/2026-11",
               json={"planned_units": 90}, headers=H(seed.actor))
    body = client.get("/v1/dashboard/unsubmitted", headers=H(seed.actor)).json()
    assert [p["plan_id"] for p in body["never_submitted"]] == [never]
    assert [p["plan_id"] for p in body["changed_since_submit"]] == [changed]


def test_a_plan_that_did_not_change_is_in_neither_column(client, seed):
    pid = prepared(client, seed)
    client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    body = client.get("/v1/dashboard/unsubmitted", headers=H(seed.actor)).json()
    assert body["never_submitted"] == [] and body["changed_since_submit"] == []
