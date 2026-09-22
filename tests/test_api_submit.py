from helpers import H, prepared

from shared.pg_client import pg_conn


def test_submit_mints_lines_and_lists_every_skipped_cell(client, seed):
    """★ 判据①②：铸出 rev，且被跳过的两个月逐条列出。"""
    pid = prepared(client, seed)
    r = client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor)).json()
    assert r["rev"] == 1 and r["lines"] == 1
    assert [(s["period"], s["reason"]) for s in r["skipped"]] == [
        ("2026-11", "zero_purchase"), ("2026-12", "zero_purchase")]
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT sku, total_units, demand_at_submit, demand_by_seller, state"
                    " FROM plan_line WHERE plan_id = %s", (pid,))
        sku, total, dsum, by_seller, state = cur.fetchone()
    assert (total, dsum, state) == (500, 240, "已提交")
    assert set(by_seller) == {"11072", "11094"}
    assert by_seller["11072"]["basis"] == "human"


def test_every_line_has_its_birth_event(client, seed):
    """★ S-2：事件链的第一行不许缺，否则事件表不是完整履历。"""
    pid = prepared(client, seed)
    client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM plan_line WHERE plan_id = %s", (pid,))
        # ★ 铸出 0 条时下面那句「缺事件的记录数 == 0」会真空绿 —— 先确认真有记录可查
        assert cur.fetchone()[0] > 0
        cur.execute("SELECT count(*) FROM plan_line l"
                    " WHERE l.plan_id = %s AND NOT EXISTS (SELECT 1 FROM plan_line_event e"
                    "   WHERE e.line_id = l.line_id AND e.from_state = '[*]')", (pid,))
        assert cur.fetchone()[0] == 0


def test_second_submit_while_one_is_in_flight_is_409_naming_the_old_rev(client, seed):
    """★ 判据④ / S-4：改了只能出新 rev，而旧版还在流转就拒 —— 点名旧版。"""
    pid = prepared(client, seed)
    client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    r = client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    assert r.status_code == 409 and r.json()["error"] == "rev_in_flight"
    assert r.json()["in_flight_rev"] == 1


def test_an_empty_rev_does_not_hold_the_in_flight_slot(client, seed):
    """★ T20：全部格子被跳过 → 空计划单 → 整体已撤销，且**不许占着在流转位**。
    占着的话，这张计划从此再也提交不了，而错误信息会说「有一版在流转」——
    人去找那一版，找到的是一张空的。"""
    pid = prepared(client, seed, purchase=None)
    r = client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor)).json()
    assert r["lines"] == 0 and r["in_flight"] is False and len(r["skipped"]) == 3
    rows = client.get("/v1/plans", headers=H(seed.actor)).json()["plans"]
    assert rows[0]["state"] == "已撤销"
    assert client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor)).status_code == 200


def test_skip_rows_are_persisted_and_append_only(client, seed):
    pid = prepared(client, seed)
    client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM plan_submit_skip WHERE plan_id = %s", (pid,))
        assert cur.fetchone()[0] == 2


def test_revs_report_in_flight_and_current(client, seed):
    pid = prepared(client, seed)
    client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    body = client.get(f"/v1/plans/{pid}/revs", headers=H(seed.actor)).json()
    assert body["in_flight_rev"] == 1 and body["current_rev"] is None
    client.post(f"/v1/plans/{pid}/revs/1/current", headers=H(seed.actor))
    body = client.get(f"/v1/plans/{pid}/revs", headers=H(seed.actor)).json()
    assert body["current_rev"] == 1 and body["revs"][0]["lines"] == 1


def test_cancelling_a_rev_cancels_every_live_line_with_one_reason(client, seed):
    """★ A-8：一个理由记在每条上。理由不填 → 400。"""
    pid = prepared(client, seed)
    client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    bad = client.post(f"/v1/plans/{pid}/revs/1/cancel", json={}, headers=H(seed.actor))
    assert bad.status_code == 400 and bad.json()["error"] == "reason_required"
    ok = client.post(f"/v1/plans/{pid}/revs/1/cancel", json={"reason": "供应商断供"},
                     headers=H(seed.actor)).json()
    assert len(ok["cancelled"]) == 1
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT reason FROM plan_line_event WHERE to_state = '已撤销'")
        assert [r[0] for r in cur.fetchall()] == ["供应商断供"]


def test_a_new_rev_is_allowed_after_the_old_one_settles(client, seed):
    """★ 判据④ 的另一半：内容不可变 —— 要改就出新 rev。"""
    pid = prepared(client, seed)
    client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    client.post(f"/v1/plans/{pid}/revs/1/cancel", json={"reason": "重来"}, headers=H(seed.actor))
    client.put(f"/v1/plans/{pid}/purchase/{seed.sku_a}/2026-10",
               json={"planned_units": 600}, headers=H(seed.actor))
    r2 = client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor)).json()
    assert r2["rev"] == 2
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT rev, total_units FROM plan_line WHERE plan_id = %s ORDER BY rev",
                    (pid,))
        assert cur.fetchall() == [(1, 500), (2, 600)], "★ 旧版的数一个字节都没变"


def test_digest_changes_only_when_content_changes(client, seed):
    pid = prepared(client, seed)
    d1 = client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor)).json()["content_digest"]
    client.post(f"/v1/plans/{pid}/revs/1/cancel", json={"reason": "重来"}, headers=H(seed.actor))
    d2 = client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor)).json()["content_digest"]
    assert d1 == d2
    client.put(f"/v1/plans/{pid}/purchase/{seed.sku_a}/2026-10",
               json={"planned_units": 600}, headers=H(seed.actor))
    client.post(f"/v1/plans/{pid}/revs/2/cancel", json={"reason": "再来"}, headers=H(seed.actor))
    d3 = client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor)).json()["content_digest"]
    assert d3 != d1


def test_diff_between_revs_names_what_moved(client, seed):
    pid = prepared(client, seed)
    client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    client.post(f"/v1/plans/{pid}/revs/1/cancel", json={"reason": "重来"}, headers=H(seed.actor))
    client.put(f"/v1/plans/{pid}/purchase/{seed.sku_a}/2026-10",
               json={"planned_units": 600}, headers=H(seed.actor))
    client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    d = client.get(f"/v1/plans/{pid}/diff", params={"from": 1, "to": 2},
                   headers=H(seed.actor)).json()
    assert d["changed"] == [{"sku": seed.sku_a, "period": "2026-10",
                             "total_units": {"from": 500, "to": 600},
                             "demand_at_submit": {"from": 240, "to": 240}}]
    assert d["added"] == [] and d["removed"] == []


def test_diff_reports_added_and_removed_lines(client, seed):
    """★ 键集合恒等时 added/removed 恒空也会绿——补一个两条分支都非空的靶子。"""
    pid = prepared(client, seed)
    client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    client.post(f"/v1/plans/{pid}/revs/1/cancel", json={"reason": "换品"}, headers=H(seed.actor))
    # sku_a 清零 → rev2 里这行消失（removed）
    client.put(f"/v1/plans/{pid}/purchase/{seed.sku_a}/2026-10",
               json={"planned_units": 0}, headers=H(seed.actor))
    # 新认领 msku_d（sku_b）并填两种量 → rev2 多出一行（added）
    client.post(f"/v1/plans/{pid}/claims",
                json={"seller_sku": seed.msku_d[0], "sid": seed.msku_d[1]},
                headers=H(seed.actor))
    client.put(f"/v1/plans/{pid}/demand/{seed.msku_d[0]}/{seed.msku_d[1]}/2026-10",
               json={"expected_units": 50}, headers=H(seed.actor))
    client.put(f"/v1/plans/{pid}/purchase/{seed.sku_b}/2026-10",
               json={"planned_units": 300}, headers=H(seed.actor))
    client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    d = client.get(f"/v1/plans/{pid}/diff", params={"from": 1, "to": 2},
                   headers=H(seed.actor)).json()
    assert d["added"] == [{"sku": seed.sku_b, "period": "2026-10", "total_units": 300}]
    assert d["removed"] == [{"sku": seed.sku_a, "period": "2026-10", "total_units": 500}]


def test_submit_on_a_nonexistent_plan_is_404(client, seed):
    r = client.post("/v1/plans/999999/submit", headers=H(seed.actor))
    assert r.status_code == 404 and r.json()["error"] == "plan_not_found"


def test_cancel_of_a_nonexistent_rev_is_404(client, seed):
    pid = prepared(client, seed)
    r = client.post(f"/v1/plans/{pid}/revs/999/cancel", json={"reason": "test"},
                    headers=H(seed.actor))
    assert r.status_code == 404 and r.json()["error"] == "rev_not_found"


def test_archive_releases_every_claim_in_the_same_transaction(client, seed):
    pid = prepared(client, seed)
    r = client.post(f"/v1/plans/{pid}/archive", headers=H(seed.actor)).json()
    assert r["released"] == 2
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM msku_claim"
                    " WHERE plan_id = %s AND released_at IS NULL", (pid,))
        assert cur.fetchone()[0] == 0
    # ★ 归档后这两个 msku 必须能被别的计划认领，否则「归档」等于永久扣着不放
    p2 = client.post("/v1/plans", json={"title": "下一张", "period_start": "2026-11-01"},
                     headers=H(seed.actor)).json()["plan_id"]
    assert client.post(f"/v1/plans/{p2}/claims",
                       json={"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]},
                       headers=H(seed.actor)).status_code == 200
