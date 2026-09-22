import datetime as dt


def mk(client, seed, **kw):
    body = {"title": "10 月计划", "period_start": "2026-10-01", "months": 3} | kw
    return client.post("/v1/plans", json=body, headers={"x-actor": seed.actor})


def test_create_returns_the_new_plan_id(client, seed):
    r = mk(client, seed)
    assert r.status_code == 201 and isinstance(r.json()["plan_id"], int)


def test_months_defaults_to_three(client, seed):
    r = client.post("/v1/plans", json={"title": "t", "period_start": "2026-10-01"},
                    headers={"x-actor": seed.actor})
    pid = r.json()["plan_id"]
    got = client.get("/v1/plans", headers={"x-actor": seed.actor}).json()["plans"]
    assert [p for p in got if p["plan_id"] == pid][0]["months"] == 3


def test_bad_months_and_bad_start_are_400_with_their_own_codes(client, seed):
    assert mk(client, seed, months=25).json()["error"] == "bad_months"
    assert mk(client, seed, period_start="2026-10-15").json()["error"] == "bad_period_start"


def test_list_reports_derived_overall_state_not_a_column(client, seed):
    mk(client, seed)
    rows = client.get("/v1/plans", headers={"x-actor": seed.actor}).json()["plans"]
    assert rows[0]["state"] is None, "★ 从未提交 → 没有状态，不是 0、不是已撤销"


def test_filters_are_declared_and_typos_are_400(client, seed):
    mk(client, seed)
    ok = client.get("/v1/plans", params={"owner": seed.actor, "archived": "false"},
                    headers={"x-actor": seed.actor})
    assert ok.status_code == 200
    bad = client.get("/v1/plans", params={"owener": seed.actor}, headers={"x-actor": seed.actor})
    assert bad.status_code == 400 and bad.json()["unknown"] == ["owener"]


def test_archived_filter_splits_the_two_sides(client, seed):
    """★ 过滤必须同时统计被丢掉的那一侧：默认不返回已归档的，
    但接口要告诉你被挡掉了几张，而不是让人以为计划凭空少了。"""
    pid = mk(client, seed).json()["plan_id"]
    client.post(f"/v1/plans/{pid}/archive", headers={"x-actor": seed.actor})
    body = client.get("/v1/plans", headers={"x-actor": seed.actor}).json()
    assert body["plans"] == [] and body["excluded"] == {"archived": 1}
