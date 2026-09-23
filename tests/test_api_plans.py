from shared.pg_client import pg_conn


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
    assert next(p for p in got if p["plan_id"] == pid)["months"] == 3


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


def test_months_must_be_an_int_not_a_string_or_a_float(client, seed):
    """★ 库层的 CHECK 管范围不管类型：`"abc"` 会以 22P02 撞成 **500**（前端被告知
    去重试一件永远不会成功的事），`3.5` 会被 PG 悄悄取整成 4 —— 一个人没要过、
    也没有任何回显的值。两种都得是 400，且点名是哪个字段。"""
    for bad in ("abc", 3.5, True, None):
        r = mk(client, seed, months=bad)
        assert r.status_code == 400, f"months={bad!r} 返回了 {r.status_code}：{r.text}"
        assert r.json()["error"] == "validation_error"
        assert r.json()["fields"] == ["months"]


def test_a_float_months_does_not_silently_become_a_plan(client, seed):
    """★ 上一条盯状态码，这条盯后果：3.5 曾经**建出了一张 months=4 的计划**。"""
    before = client.get("/v1/plans", headers={"x-actor": seed.actor}).json()["plans"]
    mk(client, seed, months=3.5)
    after = client.get("/v1/plans", headers={"x-actor": seed.actor}).json()["plans"]
    assert len(after) == len(before)


def test_an_unknown_state_filter_is_400_not_a_silent_empty_list(client, seed):
    """★ 与 /v1/plan-lines 同一条判据：打错字返回空列表，与「确实一张都没有」
    长得一模一样。宇宙必须与那个端点同一个出处，不许在这里另列一份。"""
    mk(client, seed)
    r = client.get("/v1/plans", params={"state": "没有这个态"},
                   headers={"x-actor": seed.actor})
    assert r.status_code == 400 and r.json()["error"] == "bad_state"
    assert r.json()["allowed"] == ["已提交", "已确认", "已下单", "准备排货", "已排货",
                                   "已完结", "已撤销"]
    lines_allowed = client.get("/v1/plan-lines", params={"state": "没有这个态"},
                               headers={"x-actor": seed.actor}).json()["allowed"]
    assert r.json()["allowed"] == lines_allowed, "★ 两个端点的宇宙分叉了"


def test_a_valid_state_filter_still_returns_200(client, seed):
    """★ 新守卫误伤好东西与抓到真问题同形 —— 白名单里的值必须照常过。"""
    mk(client, seed)
    r = client.get("/v1/plans", params={"state": "已提交"}, headers={"x-actor": seed.actor})
    assert r.status_code == 200 and r.json()["plans"] == []


def test_excluded_archived_counts_only_inside_this_query(client, seed):
    """★ 过滤要统计被丢掉的那一侧，而「那一侧」只能在**这次查询的候选集**里数：
    按 owner 过滤时报全库的归档数，等于把别人计划的归档算到你头上。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO actor (actor_id, name) VALUES ('carol', '卡萝')")
    mine = mk(client, seed).json()["plan_id"]
    theirs = client.post("/v1/plans", json={"title": "别人的", "period_start": "2026-10-01",
                                            "owner_actor": "carol"},
                         headers={"x-actor": seed.actor}).json()["plan_id"]
    for pid in (mine, theirs):
        client.post(f"/v1/plans/{pid}/archive", headers={"x-actor": seed.actor})

    both = client.get("/v1/plans", headers={"x-actor": seed.actor}).json()
    assert both["excluded"] == {"archived": 2}
    one = client.get("/v1/plans", params={"owner": "carol"},
                     headers={"x-actor": seed.actor}).json()
    assert one["plans"] == []
    assert one["excluded"] == {"archived": 1}, "★ 报的是全库的归档数，不是这次查询丢的"
