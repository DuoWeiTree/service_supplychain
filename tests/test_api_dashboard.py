from helpers import H, prepared


def test_plan_counts_return_the_full_set_even_where_stage_a_cannot_reach(client, seed):
    """★ S-20：接口返回全集（诚实），前端按阶段不渲染够不着的态。
    接口自己把它们抹成 0，「没有」和「还没做」就长得一样了。"""
    pid = prepared(client, seed)
    client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    body = client.get("/v1/dashboard/plans", headers=H(seed.actor)).json()
    assert set(body["counts"]) == {"进行中", "已提交", "已提交未确认", "已下单",
                                   "准备排货", "已排货"}
    assert body["counts"]["已提交"] == 1 and body["counts"]["已排货"] == 0
    assert body["scope_note"]["unreachable_in_stage_a"] == ["已下单", "准备排货", "已排货"]


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
