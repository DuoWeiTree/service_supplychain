from helpers import H, advance, prepared

from shared.pg_client import pg_conn


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
    # ★ UX-D13：前端状态筛选按「全集 − 派生桶 − 够不着」渲染，派生桶由接口说
    assert body["scope_note"]["derived_buckets"] == ["进行中", "已提交未确认"]


def test_a_confirmed_plan_lands_in_its_own_bucket_not_only_in_progress(client, seed):
    """★ 「已确认」在阶段 A 到得了（白名单里有边；手动 transition 现为 422 derived_state，spec §5 / C1，
    所以下面用白名单事件造），
    却曾经没有自己的桶 —— 这样的计划只在「进行中」里出现一次，
    看板上「提交了还没人确认」和「已经确认过」长得一模一样，而这两件事催的是不同的人。
    """
    pid = prepared(client, seed)
    client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    lines = client.get("/v1/plan-lines", params={"plan_ids": pid},
                       headers=H(seed.actor)).json()["lines"]
    assert lines, "没有铸出记录 —— 下面的断言会是空转的"
    for line in lines:
        # spec §5 / C1：手推「已确认」现为 422 derived_state，setup 改走白名单事件
        with pg_conn() as c, c.cursor() as cur:
            advance(cur, line["line_id"], "已确认", actor=seed.actor)
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


def _claimed_without_sales(client, seed, use_source):
    """认领时取不到销量（system_units 留 NULL），人只填一格采购 —— 再换源重算，预估会从 NULL 变成数。"""
    from fixture_source import FIXTURES, FixtureSource

    class NoSales(FixtureSource):
        def daily_sales(self, *a):
            return {}

    use_source(lambda: NoSales(FIXTURES))
    pid = client.post("/v1/plans", json={"title": "重算", "period_start": "2026-10-01", "months": 3},
                      headers=H(seed.actor)).json()["plan_id"]
    r = client.post(f"/v1/plans/{pid}/claims", json={"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]},
                    headers=H(seed.actor))
    assert r.status_code == 200, r.text
    client.put(f"/v1/plans/{pid}/purchase/{seed.sku_a}/2026-10", json={"planned_units": 50},
               headers=H(seed.actor))
    assert client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor)).status_code == 200
    use_source(lambda: FixtureSource(FIXTURES))
    return pid


def test_recompute_alone_does_not_count_as_changed_since_submit(client, seed, use_source):
    """★ UX-D12：「提交后又改过」只算人改的。系统重算预估让 system_units 变了，
    人一格没动 —— 列表上不许催人重新提交（否则主线狼来了）。"""
    pid = _claimed_without_sales(client, seed, use_source)
    r = client.post(f"/v1/plans/{pid}/forecast/recompute", headers=H(seed.actor))
    assert r.status_code == 200 and len(r.json()["recomputed"]) > 0, "重算没改任何格 —— 下面的断言是空转的"
    body = client.get("/v1/dashboard/unsubmitted", headers=H(seed.actor)).json()
    assert body["changed_since_submit"] == []


def test_human_edit_after_recompute_is_counted_with_cell_count(client, seed, use_source):
    """人改了两格 → 进「改过」，且回显人改了几格、跟哪一版比的（一个数要能回答它是关于什么的）。"""
    pid = _claimed_without_sales(client, seed, use_source)
    client.post(f"/v1/plans/{pid}/forecast/recompute", headers=H(seed.actor))
    client.put(f"/v1/plans/{pid}/demand/{seed.msku_a[0]}/{seed.msku_a[1]}/2026-11",
               json={"expected_units": 7}, headers=H(seed.actor))
    client.put(f"/v1/plans/{pid}/purchase/{seed.sku_a}/2026-10", json={"planned_units": 60},
               headers=H(seed.actor))
    body = client.get("/v1/dashboard/unsubmitted", headers=H(seed.actor)).json()
    assert body["changed_since_submit"] == [
        {"plan_id": pid, "title": "重算", "since_rev": 1, "human_cells": 2, "basis": "input_snapshot"}]


def test_edit_then_revert_is_not_a_change(client, seed):
    """人改了又改回原值 —— 与提交时的人填一致，不算改过。"""
    pid = prepared(client, seed)
    client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    url = f"/v1/plans/{pid}/purchase/{seed.sku_a}/2026-10"
    client.put(url, json={"planned_units": 1}, headers=H(seed.actor))
    client.put(url, json={"planned_units": 500}, headers=H(seed.actor))
    assert client.get("/v1/dashboard/unsubmitted", headers=H(seed.actor)).json()["changed_since_submit"] == []


def test_releasing_an_msku_is_a_human_change(client, seed):
    """移出 msku 是人的动作：提交时的格子少了，要催重新提交。"""
    pid = prepared(client, seed)
    client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    r = client.delete(f"/v1/plans/{pid}/claims/{seed.msku_c[0]}/{seed.msku_c[1]}", headers=H(seed.actor))
    assert r.status_code == 200, r.text
    changed = client.get("/v1/dashboard/unsubmitted", headers=H(seed.actor)).json()["changed_since_submit"]
    assert [(c["plan_id"], c["basis"]) for c in changed] == [(pid, "input_snapshot")]
    assert changed[0]["human_cells"] == 3       # MSKU-C 三个月的格子走了


def test_rev_submitted_before_snapshot_existed_says_so(client, seed):
    """028 之前提交的版本没有人填快照：退回整份摘要比较，并在 basis 上说出来（回退必须有声）。"""
    pid = prepared(client, seed)
    client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE plan_rev SET input_snapshot = NULL WHERE plan_id = %s", (pid,))
    client.put(f"/v1/plans/{pid}/purchase/{seed.sku_a}/2026-11", json={"planned_units": 9},
               headers=H(seed.actor))
    changed = client.get("/v1/dashboard/unsubmitted", headers=H(seed.actor)).json()["changed_since_submit"]
    assert changed == [{"plan_id": pid, "title": "10 月计划", "since_rev": 1,
                        "human_cells": None, "basis": "content_digest"}]
