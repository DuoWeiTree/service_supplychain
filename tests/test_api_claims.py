import datetime as dt
import threading

import psycopg2.errors

from dim import order_store_map as m
from shared.pg_client import pg_conn


def H(a):
    return {"x-actor": a}


def mk(client, seed, title="10 月计划"):
    return client.post("/v1/plans", json={"title": title, "period_start": "2026-10-01",
                                          "months": 3}, headers=H(seed.actor)).json()["plan_id"]


def test_catalog_without_a_query_deliberately_returns_nothing(client, seed):
    """★ P11：不给条件 → 故意不返回，且必须与「查不到」长得不一样。"""
    r = client.get("/v1/catalog/skus", headers=H(seed.actor)).json()
    assert r["need_query"] is True and r["items"] == [] and r["matched"] == 0
    miss = client.get("/v1/catalog/skus", params={"q": "ZZZ"}, headers=H(seed.actor)).json()
    assert miss["need_query"] is False and miss["items"] == [] and miss["matched"] == 0


def test_claimed_rows_stay_in_the_table_marked(client, seed):
    """★ P11：被占用的行留在表里标出来，不过滤 ——
    过滤掉，人永远不知道「我要的那个为什么没出现」。"""
    p1 = mk(client, seed)
    client.post(f"/v1/plans/{p1}/claims",
                json={"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]}, headers=H(seed.actor))
    items = client.get("/v1/catalog/skus", params={"q": seed.sku_a},
                       headers=H(seed.actor)).json()["items"]
    m = next(x for x in items[0]["mskus"] if x["seller_sku"] == seed.msku_a[0])
    assert m["selectable"] is False and m["claimed_by"]["plan_id"] == p1
    assert items[0]["claimed_by"] == {"plan_id": p1, "title": "10 月计划"}


def test_a_sku_held_by_two_plans_does_not_pick_one(client, seed):
    """★ 两张计划各占该货号的一部分 msku 时，货号级 claimed_by 挑一个显示就是编。
    留 null，名单放 claimed_by_plans —— 「一个答案说不清」要看得出来。"""
    p1, p2 = mk(client, seed), mk(client, seed, title="另一张")
    client.post(f"/v1/plans/{p1}/claims",
                json={"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]}, headers=H(seed.actor))
    client.post(f"/v1/plans/{p2}/claims",
                json={"seller_sku": seed.msku_c[0], "sid": seed.msku_c[1]}, headers=H(seed.actor))
    item = client.get("/v1/catalog/skus", params={"q": seed.sku_a},
                      headers=H(seed.actor)).json()["items"][0]
    assert item["claimed_by"] is None
    assert [x["plan_id"] for x in item["claimed_by_plans"]] == [p1, p2]


def test_unbuildable_sellers_is_empty_for_a_stated_reason(client, seed):
    """★ 这个数组在阶段 A 恒空，成因是 seller 镜像里没有渠道码列。

    盯住列本身：渠道码一进表，这条测试转红，逼 catalog 长出真的判据（06 §1.2）——
    否则它会永远空着，而「没有建不出的店」和「我们根本没判」长得一样。
    """
    from shared.pg_client import pg_conn
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT column_name FROM information_schema.columns"
                    " WHERE table_schema = current_schema() AND table_name = 'seller'")
        cols = {r[0] for r in cur.fetchall()}
    assert "channel_code" not in cols, "seller 有渠道码了 —— 去实现 unbuildable_sellers"
    item = client.get("/v1/catalog/skus", params={"q": seed.sku_a},
                      headers=H(seed.actor)).json()["items"][0]
    assert item["unbuildable_sellers"] == []


def test_truncation_is_reported_with_the_limit(client, seed):
    body = client.get("/v1/catalog/skus", params={"q": "MSKU", "limit": 1},
                      headers=H(seed.actor)).json()
    assert body["truncated"] is True and body["limit"] == 1 and len(body["items"]) == 1


def _claim(client, seed, plan, msku):
    r = client.post(f"/v1/plans/{plan}/claims", json={"seller_sku": msku[0], "sid": msku[1]},
                    headers=H(seed.actor))
    assert r.status_code == 200, r.text


def _age(msku, days):
    """把认领时间往前拨 —— 排序靠 claimed_at，同一秒内连发两次会撞成同一时刻。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE msku_claim SET claimed_at = now() - make_interval(days => %s)"
                    " WHERE seller_sku = %s AND sid = %s", (days, *msku))


def test_recent_lists_skus_any_plan_claimed_newest_first(client, seed):
    """常用 = 历史上任一计划认领过的货号，按最近一次认领倒序；货号只出一次。"""
    p1, p2 = mk(client, seed), mk(client, seed, title="另一张")
    _claim(client, seed, p1, seed.msku_a)
    _claim(client, seed, p2, seed.msku_c)
    _claim(client, seed, p1, seed.msku_d)
    _age(seed.msku_a, 3)
    _age(seed.msku_c, 1)          # sku_a 最近一次 = 1 天前
    _age(seed.msku_d, 2)          # sku_b = 2 天前
    body = client.get("/v1/catalog/skus/recent", headers=H(seed.actor)).json()
    assert [x["sku"] for x in body["items"]] == [seed.sku_a, seed.sku_b]
    assert body["items"][0]["last_claimed_at"] > body["items"][1]["last_claimed_at"]
    # ★ 与搜索同一形状：每个货号带全部 msku 与占用，前端共用一套渲染
    item = body["items"][0]
    assert {m["seller_sku"] for m in item["mskus"]} == {"MSKU-A", "MSKU-B", "MSKU-C"}
    assert item["claimed_by_plans"] and body["unmapped_claims"] == 0


def test_recent_keeps_released_claims(client, seed):
    """释放不删行（002_plan.sql:27）—— 用过就是用过，释放了也算。"""
    p = mk(client, seed)
    _claim(client, seed, p, seed.msku_e)
    r = client.delete(f"/v1/plans/{p}/claims/{seed.msku_e[0]}/{seed.msku_e[1]}",
                      headers=H(seed.actor))
    assert r.status_code == 200, r.text
    body = client.get("/v1/catalog/skus/recent", headers=H(seed.actor)).json()
    assert [x["sku"] for x in body["items"]] == [seed.sku_e]
    assert body["items"][0]["mskus"][0]["selectable"] is True


def test_recent_limit_and_empty(client, seed):
    empty = client.get("/v1/catalog/skus/recent", headers=H(seed.actor)).json()
    assert empty == {"items": [], "limit": 50, "truncated": False, "unmapped_claims": 0}
    p = mk(client, seed)
    _claim(client, seed, p, seed.msku_a)
    _claim(client, seed, p, seed.msku_d)
    body = client.get("/v1/catalog/skus/recent", params={"limit": 1},
                      headers=H(seed.actor)).json()
    assert len(body["items"]) == 1 and body["truncated"] is True and body["limit"] == 1
    bad = client.get("/v1/catalog/skus/recent", params={"limit": "x"}, headers=H(seed.actor))
    assert bad.status_code == 400 and bad.json()["error"] == "bad_limit"


def test_recent_counts_claims_whose_msku_left_the_bridge(client, seed):
    """★ join 丢掉的那一侧要点数：msku 从镜像里没了，认领记录就对不上货号。"""
    p = mk(client, seed)
    # 有格子引用的 bridge 行删不掉（plan_demand_cell_bridge_fk），直接造一条对不上的认领
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO msku_claim (plan_id, seller_sku, sid, claimed_by)"
                    " VALUES (%s, 'MSKU-GONE', '11072', %s)", (p, seed.actor))
    body = client.get("/v1/catalog/skus/recent", headers=H(seed.actor)).json()
    assert body["items"] == [] and body["unmapped_claims"] == 1


def test_sellers_dimension_carries_market_and_has_fba(client, seed):
    rows = client.get("/v1/sellers", headers=H(seed.actor)).json()["sellers"]
    wm = next(r for r in rows if r["seller_id"] == seed.seller_nofba)
    assert wm == {"seller_id": seed.seller_nofba, "name": "A4Pet-WM", "market": "US",
                  "has_fba": False, "platform": "walmart"}


def test_claim_seeds_both_grids_and_names_mskus_without_history(client, seed):
    p = mk(client, seed)
    r = client.post(f"/v1/plans/{p}/claims",
                    json={"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]},
                    headers=H(seed.actor)).json()
    assert r["seeded"] == {"demand_cells": 3, "purchase_cells": 3}
    assert r["no_history"] == []
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT period_start, system_units, system_extrapolated, expected_units"
                    " FROM plan_demand_cell WHERE plan_id = %s ORDER BY period_start", (p,))
        rows = cur.fetchall()
    # spec §3.1：预测换成 base_rate × 天数 —— fixture 日销 4 件/天 → base_rate 4.325（OP-3）
    #   × 31/30/31 天向上取整
    assert [r[1] for r in rows] == [135, 130, 135]
    assert [r[2] for r in rows] == [False, False, False]
    assert [r[3] for r in rows] == [None, None, None], "★ 人填列留空 = 未知，不预填"


def test_msku_without_history_is_seeded_null_and_named(client, seed, use_source):
    """★ 没有历史 ≠ 预估 0。格子照建，system_units 留 NULL，并在返回里点名。

    ★ 终审 I-2 之后换了靶子：原先拿 MSKU-W（Walmart 店）当「没有历史」，而那
      其实是**不适用**（见下一条）。这里改成一个 Amazon 店的 msku，用一个真的
      返回空历史的源 —— 「查过了、一行都没有」与「压根没有这条取数链路」是
      两件事，两个 reason 各有各的靶子。"""
    from fixture_source import FIXTURES, FixtureSource

    class NoHistory(FixtureSource):
        # spec §3.1：预测换成 base_rate × 天数，取数改走日销 —— 「查过了、一行都没有」
        def daily_sales(self, *a):
            return {}

    use_source(lambda: NoHistory(FIXTURES))
    p = mk(client, seed)
    r = client.post(f"/v1/plans/{p}/claims",
                    json={"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]},
                    headers=H(seed.actor)).json()
    assert r["no_history"] == [{"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1],
                                "reason": "no_sales_history"}]
    with pg_conn() as c, c.cursor() as cur:
        # ★ DISTINCT 会把行数一起折叠掉：3 行全 NULL 和只种出 1 行会长得一样。
        #   count(*) 与 count(system_units) 分开数才能证明「3 个格子都建了、且都是 NULL」。
        cur.execute("SELECT count(*), count(system_units) FROM plan_demand_cell WHERE plan_id = %s",
                    (p,))
        assert cur.fetchone() == (3, 0)


def test_a_store_with_no_amazon_sales_source_is_not_applicable_not_unknown(client, seed):
    """★★ 终审 I-2：`seed` 里的 90001 是 `platform='walmart'` —— 它在
    `amazon_sp_api_report_all_orders` 里**按构造**不会有行。那是「不适用」，
    不是「认不出这个店」。

    修复前：fixture 档返回 200 + `no_history/no_sales_history`，CH 档在
    `store_for()` 上抛 `UnknownStore` → **503**。同一件事两档两种答案，而 503
    那一侧还把「不适用」说成了「这个店没有声明取数映射」，运维会去 STORE_SID
    里补一行永远不该存在的映射。"""
    p = mk(client, seed)
    r = client.post(f"/v1/plans/{p}/claims",
                    json={"seller_sku": seed.msku_nofba[0], "sid": seed.msku_nofba[1]},
                    headers=H(seed.actor))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["no_history"] == [{"seller_sku": seed.msku_nofba[0], "sid": seed.msku_nofba[1],
                                   "reason": "not_applicable_no_sales_source",
                                   "cause": "non_amazon_platform",
                                   "platform": "walmart"}], body
    assert body["history_window"] is None, (
        "不适用就不该有口径标记 —— 有标记意味着「查过了」，而这条链路压根没查")
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT count(*), count(system_units) FROM plan_demand_cell WHERE plan_id = %s",
                    (p,))
        assert cur.fetchone() == (3, 0), "格子照建、system_units 留 NULL —— 不适用不是 0"


def test_both_profiles_answer_the_same_for_a_store_with_no_amazon_sales_source(client, seed,
                                                                              use_source):
    """★ 终审 I-2 的判据本身：**两档必须给同一个形状**。

    CH 档用一个真的 `ChSource`，它的 `query` 被换成一条**一被调用就炸**的桩 ——
    这条闸如果没拦住，CH 档就会去查一个不存在的取数源，而那正是修复前的行为。
    """
    from dim import ch_source as cs

    def must_not_be_called(sql, parameters=None):
        raise AssertionError(
            f"非 Amazon 店的 claim 去查了 CH —— 闸没拦住。SQL={' '.join(sql.split())[:120]}")

    # ★ 同一张计划里认领两次（ON CONFLICT DO UPDATE）—— 换成两张计划会撞
    #   409 msku_already_claimed，那是另一条规则，与本条要比的东西无关。
    # ★ fix round 2：在认领中的 msku 再认领是空操作、不取数 —— 中间释放一次，第二次走的是
    #   「复活已释放的行」那条完整路径，闸照样要被走到。
    p = mk(client, seed)
    body = {"seller_sku": seed.msku_nofba[0], "sid": seed.msku_nofba[1]}
    r0 = client.post(f"/v1/plans/{p}/claims", json=body, headers=H(seed.actor))
    assert r0.status_code == 200, r0.text
    fixture_body = r0.json()
    assert client.delete(f"/v1/plans/{p}/claims/{body['seller_sku']}/{body['sid']}",
                         headers=H(seed.actor)).status_code == 200

    use_source(lambda: cs.ChSource(must_not_be_called))
    r = client.post(f"/v1/plans/{p}/claims", json=body, headers=H(seed.actor))
    assert r.status_code == 200, r.text
    ch_body = r.json()
    assert ch_body["no_history"] == fixture_body["no_history"], (
        f"两档对同一个 msku 给出不同答案：ch={ch_body['no_history']} "
        f"fixture={fixture_body['no_history']}")
    assert ch_body["history_window"] == fixture_body["history_window"] is None
    assert ch_body["already_claimed"] is False


def test_two_mskus_of_the_same_sku_do_not_double_the_purchase_cells(client, seed):
    """★ 采购格子是货号级的（PK 不含 seller_sku/sid）：同一货号的第二个 msku 认领
    必须落在同一批格子上，不能翻倍 —— 没有 ON CONFLICT DO NOTHING 会在这里撞主键，
    这条测试就是靠这个主键把「翻倍」和「去重」分得开的。"""
    p = mk(client, seed)
    client.post(f"/v1/plans/{p}/claims",
                json={"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]}, headers=H(seed.actor))
    client.post(f"/v1/plans/{p}/claims",
                json={"seller_sku": seed.msku_b[0], "sid": seed.msku_b[1]}, headers=H(seed.actor))
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM plan_purchase_cell WHERE plan_id = %s AND sku ="
                    " (SELECT sku FROM msku_bridge WHERE seller_sku = %s AND sid = %s)",
                    (p, seed.msku_a[0], seed.msku_a[1]))
        assert cur.fetchone()[0] == 3, "★ 3 个月 × 1 个货号 = 3 行，不是 3×2 个 msku = 6 行"


def test_second_plan_claiming_the_same_msku_is_409_and_names_the_holder(client, seed):
    """★ 判据③ 的接口那一半。"""
    p1, p2 = mk(client, seed), mk(client, seed, title="另一张")
    body = {"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]}
    assert client.post(f"/v1/plans/{p1}/claims", json=body, headers=H(seed.actor)).status_code == 200
    r = client.post(f"/v1/plans/{p2}/claims", json=body, headers=H(seed.actor))
    assert r.status_code == 409 and r.json()["error"] == "msku_already_claimed"
    assert r.json()["claimed_by"] == {"plan_id": p1, "actor": seed.actor, "title": "10 月计划"}


def test_two_connections_racing_for_the_same_msku(client, seed):
    """★ 判据③ 的库层那一半：先查后写挡不住并发，部分唯一索引能。

    B 在 A 未提交时插同一把键 → 必须**阻塞**；A 提交后 B 收到唯一冲突。
    只跑「A 提交完 B 再插」的话，证明的是「重复插入被拒」，不是竞态。
    """
    p1, p2 = mk(client, seed), mk(client, seed, title="另一张")
    err, started = [], threading.Event()

    def other():
        started.set()
        try:
            with pg_conn() as c, c.cursor() as cur:
                cur.execute("INSERT INTO msku_claim (plan_id, seller_sku, sid, claimed_by)"
                            " VALUES (%s, %s, %s, %s)", (p2, *seed.msku_a, seed.actor))
        except psycopg2.errors.UniqueViolation as e:
            err.append(e)

    conn = psycopg2.connect  # noqa: F841  （用 pg_conn 拿一条独立连接）
    with pg_conn() as a, a.cursor() as cur:
        cur.execute("INSERT INTO msku_claim (plan_id, seller_sku, sid, claimed_by)"
                    " VALUES (%s, %s, %s, %s)", (p1, *seed.msku_a, seed.actor))
        t = threading.Thread(target=other)
        t.start()
        started.wait(1)
        t.join(timeout=0.5)
        assert t.is_alive(), "★ B 没有被挡住 —— 唯一索引没生效，或它根本没走到插入"
        # 退出 with → A 提交 → B 被唤醒并撞上唯一索引
    t.join(timeout=5)
    assert not t.is_alive() and len(err) == 1


def test_release_keeps_the_row_and_names_the_cells_it_drops(client, seed):
    """★ 释放不删行；而被一起删掉的期望销量格子必须逐条点名 ——
    「少了几个数」在界面上是看不出来的。"""
    p = mk(client, seed)
    body = {"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]}
    client.post(f"/v1/plans/{p}/claims", json=body, headers=H(seed.actor))
    client.put(f"/v1/plans/{p}/demand/{seed.msku_a[0]}/{seed.msku_a[1]}/2026-10",
               json={"expected_units": 130}, headers=H(seed.actor))
    r = client.delete(f"/v1/plans/{p}/claims/{seed.msku_a[0]}/{seed.msku_a[1]}",
                      headers=H(seed.actor)).json()
    assert {"period": "2026-10", "expected_units": 130} in r["dropped_cells"]
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT released_at IS NOT NULL FROM msku_claim WHERE plan_id = %s", (p,))
        assert cur.fetchone()[0] is True


def purchase_cells(p, sku):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT to_char(period_start, 'YYYY-MM'), planned_units FROM plan_purchase_cell"
                    " WHERE plan_id = %s AND sku = %s ORDER BY period_start", (p, sku))
        return [tuple(r) for r in cur.fetchall()]


def active_claims(p):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT seller_sku, sid FROM msku_claim WHERE plan_id = %s"
                    " AND released_at IS NULL ORDER BY seller_sku", (p,))
        return [tuple(r) for r in cur.fetchall()]


def log_line(scm_log, op):
    return next(r.getMessage() for r in scm_log.records
                if f"op={op} " in r.getMessage() and "dropped_purchase=" in r.getMessage())


def test_releasing_the_last_msku_drops_the_purchase_cells_and_names_them(client, seed):
    """★ 业主裁定 2026-09-27（推翻 F5「搁浅采购格未删、待处理」）：货号的最后一个 msku
    移出后，货号本身也要移出 —— 采购格同一事务删掉，**逐格回报**，人填过的数带值。

    修复前：格子留在原地，网格靠采购格把货号行长回来（DBCD028014AL 就是这么留下的）。"""
    p = mk(client, seed)
    body = {"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]}
    client.post(f"/v1/plans/{p}/claims", json=body, headers=H(seed.actor))
    client.put(f"/v1/plans/{p}/purchase/{seed.sku_a}/2026-10",
               json={"planned_units": 500}, headers=H(seed.actor))
    r = client.delete(f"/v1/plans/{p}/claims/{seed.msku_a[0]}/{seed.msku_a[1]}",
                      headers=H(seed.actor)).json()
    # ★ 空格也点名（null ≠ 0）：删掉的是三行，其中一行是人填的 500
    assert r["dropped_purchase_cells"] == [
        {"sku": seed.sku_a, "period": "2026-10", "planned_units": 500},
        {"sku": seed.sku_a, "period": "2026-11", "planned_units": None},
        {"sku": seed.sku_a, "period": "2026-12", "planned_units": None}]
    assert "stranded_purchase_cells" not in r, "★ 旧说法退役：不再有「留着待处理」这回事"
    assert purchase_cells(p, seed.sku_a) == []
    g = client.get(f"/v1/plans/{p}/grid", headers=H(seed.actor)).json()
    assert seed.sku_a not in {x["sku"] for x in g["purchase"]}
    assert seed.sku_a not in {x["sku"] for x in g["forecast_sku"]}, "★ 货号必须从网格里消失"


def test_releasing_a_non_last_msku_keeps_the_purchase_cells(client, seed):
    """★ 靶子的另一半：同货号还有 msku 在认领中（哪怕在另一家店），采购格一格都不许动 ——
    PK 不含 msku，那批格子正是兄弟 msku 的。"""
    p = mk(client, seed)
    for ms in (seed.msku_a, seed.msku_c):      # 同属 sku_a，不同店
        client.post(f"/v1/plans/{p}/claims", json={"seller_sku": ms[0], "sid": ms[1]},
                    headers=H(seed.actor))
    client.put(f"/v1/plans/{p}/purchase/{seed.sku_a}/2026-10",
               json={"planned_units": 500}, headers=H(seed.actor))
    client.put(f"/v1/plans/{p}/demand/{seed.msku_a[0]}/{seed.msku_a[1]}/2026-10",
               json={"expected_units": 130}, headers=H(seed.actor))
    r = client.delete(f"/v1/plans/{p}/claims/{seed.msku_a[0]}/{seed.msku_a[1]}",
                      headers=H(seed.actor)).json()
    assert r["dropped_purchase_cells"] == []
    assert purchase_cells(p, seed.sku_a) == [("2026-10", 500), ("2026-11", None), ("2026-12", None)]
    # ★ 两个列表各说各的事：这一次确实删掉了三个期望销量格子
    assert [x["period"] for x in r["dropped_cells"]] == ["2026-10", "2026-11", "2026-12"]
    assert {"period": "2026-10", "expected_units": 130} in r["dropped_cells"]


def test_release_last_msku_logs_the_dropped_purchase_cells(client, seed, scm_log):
    p = mk(client, seed)
    client.post(f"/v1/plans/{p}/claims", json={"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]},
                headers=H(seed.actor))
    client.put(f"/v1/plans/{p}/purchase/{seed.sku_a}/2026-11",
               json={"planned_units": 22}, headers=H(seed.actor))
    client.delete(f"/v1/plans/{p}/claims/{seed.msku_a[0]}/{seed.msku_a[1]}", headers=H(seed.actor))
    line = log_line(scm_log, "release_claim")
    assert f"plan_id={p}" in line and f"actor={seed.actor}" in line and f"sku={seed.sku_a}" in line
    assert "2026-11:22" in line and "2026-10:None" in line


def remove(client, seed, p, sku):
    return client.delete(f"/v1/plans/{p}/skus/{sku}", headers=H(seed.actor))


def test_remove_sku_releases_every_msku_across_stores_and_drops_everything_named(client, seed):
    """★ 业主裁定 2026-09-27：「直接删除货号与货号内对应的 msku」—— 一个调用、一个事务，
    跨店把该货号的 msku 全部释放，期望销量格与采购格一并删掉，逐格带值回报。"""
    p = mk(client, seed)
    for ms in (seed.msku_a, seed.msku_b, seed.msku_c, seed.msku_d):   # a/b/c = sku_a，d = sku_b
        client.post(f"/v1/plans/{p}/claims", json={"seller_sku": ms[0], "sid": ms[1]},
                    headers=H(seed.actor))
    client.put(f"/v1/plans/{p}/demand/{seed.msku_c[0]}/{seed.msku_c[1]}/2026-11",
               json={"expected_units": 44}, headers=H(seed.actor))
    client.put(f"/v1/plans/{p}/purchase/{seed.sku_a}/2026-10",
               json={"planned_units": 24}, headers=H(seed.actor))
    r = remove(client, seed, p, seed.sku_a)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["sku"] == seed.sku_a
    assert b["released"] == [{"seller_sku": "MSKU-A", "sid": "11072"},
                             {"seller_sku": "MSKU-B", "sid": "11072"},
                             {"seller_sku": "MSKU-C", "sid": "11094"}]
    assert len(b["dropped_cells"]) == 9
    assert {"seller_sku": "MSKU-C", "sid": "11094", "period": "2026-11",
            "expected_units": 44} in b["dropped_cells"]
    assert b["dropped_purchase_cells"] == [
        {"sku": seed.sku_a, "period": "2026-10", "planned_units": 24},
        {"sku": seed.sku_a, "period": "2026-11", "planned_units": None},
        {"sku": seed.sku_a, "period": "2026-12", "planned_units": None}]
    # ★ 被丢掉的那一侧之外，留下的那一侧也要数：sku_b 一格没动
    assert active_claims(p) == [seed.msku_d]
    assert len(purchase_cells(p, seed.sku_b)) == 3 and purchase_cells(p, seed.sku_a) == []
    g = client.get(f"/v1/plans/{p}/grid", headers=H(seed.actor)).json()
    assert {x["sku"] for x in g["demand"]} == {seed.sku_b}
    assert {x["sku"] for x in g["forecast_sku"]} == {seed.sku_b}


def test_remove_sku_is_atomic(client, seed, monkeypatch):
    """★ 一个事务：中途炸了就一样都不许少 —— 半截移出会留下「msku 没了、采购格还在」的货号。"""
    from api.ui import plans
    p = mk(client, seed)
    for ms in (seed.msku_a, seed.msku_c):
        client.post(f"/v1/plans/{p}/claims", json={"seller_sku": ms[0], "sid": ms[1]},
                    headers=H(seed.actor))

    def boom(*a, **k):
        raise RuntimeError("注入：删采购格时炸了")
    monkeypatch.setattr(plans, "_drop_purchase_cells", boom)
    r = remove(client, seed, p, seed.sku_a)
    assert r.status_code == 500
    assert active_claims(p) == [seed.msku_a, seed.msku_c]
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM plan_demand_cell WHERE plan_id = %s", (p,))
        assert cur.fetchone()[0] == 6
    assert len(purchase_cells(p, seed.sku_a)) == 3


def test_remove_sku_clears_legacy_stranded_purchase_cells(client, seed):
    """★ F5 时代留下的货号（没有 msku、只剩采购格，DBCD028014AL 就是）也得移得掉 ——
    否则裁定之前搁浅的那些永远删不掉。"""
    p = mk(client, seed)
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO plan_purchase_cell (plan_id, sku, period_start, planned_units,"
                    " updated_by) VALUES (%s, %s, '2026-10-01', 7, %s)", (p, seed.sku_b, seed.actor))
    b = remove(client, seed, p, seed.sku_b).json()
    assert b["released"] == [] and b["dropped_cells"] == []
    assert b["dropped_purchase_cells"] == [{"sku": seed.sku_b, "period": "2026-10",
                                            "planned_units": 7}]


def test_remove_sku_not_in_the_plan_is_404(client, seed):
    p = mk(client, seed)
    r = remove(client, seed, p, seed.sku_a)
    assert r.status_code == 404 and r.json()["error"] == "sku_not_in_plan"


def test_remove_sku_logs_what_it_dropped(client, seed, scm_log):
    p = mk(client, seed)
    client.post(f"/v1/plans/{p}/claims", json={"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]},
                headers=H(seed.actor))
    client.put(f"/v1/plans/{p}/purchase/{seed.sku_a}/2026-10",
               json={"planned_units": 24}, headers=H(seed.actor))
    remove(client, seed, p, seed.sku_a)
    line = log_line(scm_log, "remove_sku")
    assert f"plan_id={p}" in line and f"actor={seed.actor}" in line and f"sku={seed.sku_a}" in line
    assert "2026-10:24" in line and "MSKU-A/11072" in line


def test_reclaiming_in_the_same_plan_revives_the_row(client, seed):
    p = mk(client, seed)
    body = {"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]}
    client.post(f"/v1/plans/{p}/claims", json=body, headers=H(seed.actor))
    client.delete(f"/v1/plans/{p}/claims/{seed.msku_a[0]}/{seed.msku_a[1]}", headers=H(seed.actor))
    assert client.post(f"/v1/plans/{p}/claims", json=body, headers=H(seed.actor)).status_code == 200
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM msku_claim WHERE plan_id = %s", (p,))
        assert cur.fetchone()[0] == 1, "★ 复认领是复活那一行，不是新插一行（主键就在那）"


#: ---------------------------------------------------------------------------
#: 残留轮次：没有销量来源的店 —— 两种成因，一个答案（不适用），一套词汇。
#: ---------------------------------------------------------------------------

#: `dim/order_store_map.py::NO_ORDER_REPORT_SID` 里的一个真实 sid。
#: ★ 不另编一个假 sid：这条测试要验的正是「**登记在册的那几个**能不能认领」，
#:   用一个不在名单里的号码会走到另一条分支上，测试就成了空转的。
GAP_SID = "11098"


def _only_as_of(must_not_be_called):
    """只放行 `as_of()` 那一条（fba_detail 的采集日），其余照旧一被调用就炸。

    ★ spec §3.4 之后 claim() 要先拿 as_of 才定得出日销窗口（估计锚点 = as_of − embargo），
      所以「一次往返都没有」不再成立 —— 但要钉的那件事不变：**没去查那个不存在的
      销量取数源**（订单表、断货日一条都不许问）。按异常分派、不预检的裁定也不变。"""
    def q(sql, parameters=None):
        one = " ".join(sql.split())
        if "GROUP BY _captured_date" in one and "countIf(" not in one:
            return [(dt.date(2026, 9, 21), 8000, 21, True)]
        return must_not_be_called(sql, parameters)
    return q


def _seed_gap_store(seed, seller_sku="MSKU-GAP"):
    """种一个 `NO_ORDER_REPORT_SID` 上的店 + 一个它名下的 msku。

    ★ 种在用例里而不是共享 `seed` 夹具里：加进 `seed` 会让每张网格、每个目录
      查询都多出一个 msku，把一批与本条无关的断言一起改掉。
    """
    from shared.pg_client import pg_conn
    with pg_conn() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO seller (seller_id, name, market, has_fba, platform, refreshed_at)"
            " VALUES (%s, %s, %s, %s, %s, now())",
            (GAP_SID, "UNITFREE-GQ-P品牌-US", "US", True, "amazon"))
        cur.execute("INSERT INTO msku_bridge VALUES (%s, %s, %s, now())",
                    (seller_sku, GAP_SID, seed.sku_a))
    return seller_sku, GAP_SID


def test_a_store_with_no_order_report_data_can_still_be_claimed(client, seed, use_source):
    """★★ 残留轮次裁定：`NO_ORDER_REPORT_SID` 上的 179 个 msku 原先一律 503，
    于是一家**活着的美国店**（11098，145 个 msku）的货整个上不了计划。

    这件事的形状与 Walmart 那一种一模一样：**这个 msku 没有销量取数源**。
    一周前已经为那一种裁定过「格子照建、`system_units` 留 NULL、标记点名」，
    这一种就该拿到同一个答案 —— 145 个 msku 认领不了，比一格等人来填的 NULL
    坏得多。

    ★ 未知/0 的保证不变：`system_units` 是 **NULL**，不是 0，也不是编出来的预估。
    ★ **必须跑在 CH 档上**：503 只发生在 CH 档（fixture 档压根不查
      `store_for()`，它对这个 msku 给的是「查过了、没有历史」）。拿 fixture 档
      验这条，就是在一个从来不会 503 的路径上证明「不再 503」。
      用真 `ChSource`，它的 `query` 除了 `as_of()` 那一条之外**一被调用就炸**
      （`_only_as_of`）—— `store_for()` 在问订单表之前就抛，所以这条桩顺带钉住了
      「没去查一个不存在的取数源」。
    """
    from dim import ch_source as cs

    def must_not_be_called(sql, parameters=None):
        raise AssertionError(
            f"没有销量来源的店去查了 CH —— 闸没拦住。SQL={' '.join(sql.split())[:120]}")

    ms = _seed_gap_store(seed)
    use_source(lambda: cs.ChSource(_only_as_of(must_not_be_called)))
    p = mk(client, seed)
    r = client.post(f"/v1/plans/{p}/claims",
                    json={"seller_sku": ms[0], "sid": ms[1]}, headers=H(seed.actor))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["seeded"] == {"demand_cells": 3, "purchase_cells": 3}
    assert body["no_history"] == [{
        "seller_sku": ms[0], "sid": ms[1],
        "reason": "not_applicable_no_sales_source",
        "cause": "store_absent_from_order_report",
        "detail": m.NO_ORDER_REPORT_SID[GAP_SID]}], body
    assert body["history_window"] is None, (
        "不适用就不该有口径标记 —— 有标记意味着「查过了」，而这条链路压根没查")

    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT count(*), count(system_units) FROM plan_demand_cell"
                    " WHERE plan_id = %s", (p,))
        assert cur.fetchone() == (3, 0), (
            "★ 三个格子都要建出来、且 system_units 全为 NULL —— "
            "不适用不是 0，也不是「少种几个格子」")


def test_the_two_no_sales_source_causes_share_one_reason(client, seed, use_source):
    """★ 同一件事只许有一套词汇：Walmart 店与订单报表里没有的店，`reason`
    必须是**同一个值**（前端判「要不要让人来填」只看它），区别只在 `cause`。

    ★ 反面靶子：两个 `cause` 必须真的不同 —— 合成一个就等于把「为什么」丢了，
      而那两个原因的处置完全不同（一个等接平台，一个等采集侧补数据）。
    """
    from dim import ch_source as cs

    def must_not_be_called(sql, parameters=None):
        raise AssertionError("没有销量来源的店去查了 CH —— 闸没拦住")

    ms = _seed_gap_store(seed)
    use_source(lambda: cs.ChSource(_only_as_of(must_not_be_called)))
    p = mk(client, seed)
    gap = client.post(f"/v1/plans/{p}/claims",
                      json={"seller_sku": ms[0], "sid": ms[1]},
                      headers=H(seed.actor)).json()["no_history"][0]
    wm = client.post(f"/v1/plans/{p}/claims",
                     json={"seller_sku": seed.msku_nofba[0], "sid": seed.msku_nofba[1]},
                     headers=H(seed.actor)).json()["no_history"][0]

    assert gap["reason"] == wm["reason"] == "not_applicable_no_sales_source", (
        f"两种成因的 reason 不一致：{gap['reason']!r} vs {wm['reason']!r} —— "
        "同一件事两套词汇，前端就得跟着后端每接一个新平台加一个分支")
    assert gap["cause"] != wm["cause"], (
        f"两种成因的 cause 相同（{gap['cause']!r}）—— 「为什么」丢了，"
        "而这两个原因等的是完全不同的东西")
    assert {gap["cause"], wm["cause"]} == {
        "store_absent_from_order_report", "non_amazon_platform"}
    # ★ 「没有历史」是第三种东西，绝不能跟上面两种共用 reason
    assert gap["reason"] != "no_sales_history"


def test_the_two_unknown_store_causes_take_different_paths_end_to_end(client, seed, use_source):
    """★★ 残留轮次（第二轮裁定）：`UnknownStore` 的两种成因走**两条真实请求
    路径**，而且两条都走得到 —— 分派在 `claim()` 的 `except UnknownStore` 里按
    `e.registered_gap` 做，不在调用之前预检。

    第一版实现是预检：调用 `monthly_sales_history` 之前先查一次登记表。那样
    `_source_error` 里那支「已登记的缺口」永远走不到 —— 它只被一条直接调函数的
    单测撑着，而**一个没有路径的分支是没人能信的分支**：下一个改预检的人会在
    另一个方向上把它悄悄变成孤儿。本仓已经被这种东西咬过两次（`ChUnavailable`
    定义了从没抛过；折叠计数器没有靶子）。

    分派挪进 except 之后，`_source_error` 只会收到「没人声明过」那一种，于是
    「去补一行 STORE_SID」这句话也就只对着它该对的人说 —— 那支专门给已登记缺口
    的 hint 分支**被删掉了**，不是留着没人走。

    ★ 两条路径在同一个用例里对照，因为要钉住的正是「它们不一样」。
    """
    from dim import ch_source as cs
    from shared.pg_client import pg_conn as _pg

    def must_not_be_called(sql, parameters=None):
        raise AssertionError(
            f"两种成因都该在 store_for() 上就被拦住，不该真去查 CH。"
            f"SQL={' '.join(sql.split())[:120]}")

    # 已登记的缺口（11098）与没人声明过的 sid（99999），都是 Amazon 店、都有 FBA
    gap = _seed_gap_store(seed)
    unknown_sid = "99999"
    assert unknown_sid not in m.NO_ORDER_REPORT_SID
    assert unknown_sid not in m.STORE_SID.values()
    with _pg() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO seller (seller_id, name, market, has_fba, platform, refreshed_at)"
            " VALUES (%s, %s, %s, %s, %s, now())",
            (unknown_sid, "谁家的店", "US", True, "amazon"))
        cur.execute("INSERT INTO msku_bridge VALUES (%s, %s, %s, now())",
                    ("MSKU-UNKNOWN", unknown_sid, seed.sku_a))

    use_source(lambda: cs.ChSource(_only_as_of(must_not_be_called)))
    p = mk(client, seed)

    # ① 已登记的缺口 → 200 + 不适用（认领成功，格子等人来填）
    ok = client.post(f"/v1/plans/{p}/claims",
                     json={"seller_sku": gap[0], "sid": gap[1]}, headers=H(seed.actor))
    assert ok.status_code == 200, ok.text
    assert ok.json()["no_history"][0]["cause"] == "store_absent_from_order_report"

    # ② 没人声明过 → 503 + 「去补一行」这条**正确**的建议
    bad = client.post(f"/v1/plans/{p}/claims",
                      json={"seller_sku": "MSKU-UNKNOWN", "sid": unknown_sid},
                      headers=H(seed.actor))
    assert bad.status_code == 503, bad.text
    body = bad.json()
    assert body["error"] == "forecast_source_unusable"
    assert "缺一行" in body["hint"], body["hint"]
    assert body["sid"] == unknown_sid

    # ★ 反面靶子：那句「去补一行」绝不许出现在①那条路径上 —— 它是本轮要修掉的
    #   那条错建议，对一个已登记的缺口来说它是有害的。
    assert "hint" not in ok.json(), (
        f"①走的是成功路径，不该带任何 hint：{ok.json()}")


def test_an_unknown_sid_is_still_told_to_add_the_mapping_row(client, seed):
    """★ 另一支必须照旧：**没人声明过**的 sid 走的是 503，且 hint 要如实说
    「去补一行 STORE_SID」—— 那对这一种是正确的建议。

    这一支有真的端到端路径：一个 Amazon 店、不在 STORE_SID、也不在
    NO_ORDER_REPORT_SID 里。
    """
    from dim import ch_source as cs
    from shared.pg_client import pg_conn as _pg

    unknown_sid = "99999"
    assert unknown_sid not in m.NO_ORDER_REPORT_SID
    assert unknown_sid not in m.STORE_SID.values()
    with _pg() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO seller (seller_id, name, market, has_fba, platform, refreshed_at)"
            " VALUES (%s, %s, %s, %s, %s, now())",
            (unknown_sid, "谁家的店", "US", True, "amazon"))
        cur.execute("INSERT INTO msku_bridge VALUES (%s, %s, %s, now())",
                    ("MSKU-UNKNOWN", unknown_sid, seed.sku_a))

    from api.ui.source_factory import source_dep

    def must_not_be_called(sql, parameters=None):
        raise AssertionError("认不出的 sid 不该走到真取数 —— store_for() 就该拦住")

    client.app.dependency_overrides[source_dep] = lambda: cs.ChSource(_only_as_of(must_not_be_called))
    try:
        p = mk(client, seed)
        r = client.post(f"/v1/plans/{p}/claims",
                        json={"seller_sku": "MSKU-UNKNOWN", "sid": unknown_sid},
                        headers=H(seed.actor))
    finally:
        client.app.dependency_overrides.clear()

    assert r.status_code == 503, r.text
    body = r.json()
    assert body["error"] == "forecast_source_unusable"
    assert "缺一行" in body["hint"], body["hint"]
    assert body["sid"] == unknown_sid
    # ★ dim 层的成因分支也要在响应里看得见：`store_for()` 对「没人声明过」写的是
    #   「新开的店请补进 …STORE_SID」，对已登记的缺口写的是「这是已登记的取数
    #   缺口：…」。两句话在 detail 里不许混 —— 运维照 detail 去查那一行。
    assert "新开的店" in body["detail"], body["detail"]
    assert "已登记的取数缺口" not in body["detail"], body["detail"]


def _bases(pid, ms):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT system_units, system_basis FROM plan_demand_cell"
                    " WHERE plan_id = %s AND seller_sku = %s AND sid = %s ORDER BY period_start",
                    (pid, *ms))
        return cur.fetchall()


def test_no_sales_source_is_persisted_into_system_basis_for_both_causes(client, seed, use_source):
    """★ 「不适用」要活过认领那一次响应：grid 只读得到格子，读不到当时的 no_history。
    记进 system_basis（不加列），reason 与 no_history 同一个词；system_units 仍是 NULL。"""
    from dim import ch_source as cs

    def must_not_be_called(sql, parameters=None):
        raise AssertionError("没有销量来源的店去查了 CH —— 闸没拦住")

    gap = _seed_gap_store(seed)
    use_source(lambda: cs.ChSource(_only_as_of(must_not_be_called)))
    p = mk(client, seed)
    for ms in (seed.msku_nofba, gap):
        r = client.post(f"/v1/plans/{p}/claims", json={"seller_sku": ms[0], "sid": ms[1]},
                        headers=H(seed.actor))
        assert r.status_code == 200, r.text
    for ms, cause in ((seed.msku_nofba, "non_amazon_platform"),
                      (gap, "store_absent_from_order_report")):
        rows = _bases(p, ms)
        assert len(rows) == 3
        for units, basis in rows:
            assert units is None
            assert basis["reason"] == "not_applicable_no_sales_source", basis
            assert basis["cause"] == cause
            assert basis["units_raw"] is None
            assert "base_rate" not in basis, "不适用不是速率 0 —— 不许长出一个 base_rate"


def test_no_sales_history_is_not_marked_not_applicable(client, seed, use_source):
    """反面：Amazon 店查过了、窗口里没卖（no_sales_history）—— 那是未知，不是不适用。"""
    from fixture_source import FIXTURES, FixtureSource

    class NoHistory(FixtureSource):
        def daily_sales(self, *a):
            return {}

    use_source(lambda: NoHistory(FIXTURES))
    p = mk(client, seed)
    r = client.post(f"/v1/plans/{p}/claims", json={"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]},
                    headers=H(seed.actor)).json()
    assert r["no_history"][0]["reason"] == "no_sales_history"
    for units, basis in _bases(p, seed.msku_a):
        assert units is None
        assert basis["reason"] != "not_applicable_no_sales_source", basis


def test_reclaiming_after_releasing_the_last_msku_reseeds_the_purchase_cells(client, seed):
    """★ 不变式：货号下有在认领的 msku ⇒ 它的采购格在（没有行和填了 0 不能长得一样）。
    最后一个 msku 走了、采购格删了，再认领回来必须重新长出来。"""
    p = mk(client, seed)
    body = {"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]}
    client.post(f"/v1/plans/{p}/claims", json=body, headers=H(seed.actor))
    client.delete(f"/v1/plans/{p}/claims/{seed.msku_a[0]}/{seed.msku_a[1]}", headers=H(seed.actor))
    assert purchase_cells(p, seed.sku_a) == []
    assert client.post(f"/v1/plans/{p}/claims", json=body, headers=H(seed.actor)).status_code == 200
    assert purchase_cells(p, seed.sku_a) == [("2026-10", None), ("2026-11", None), ("2026-12", None)]


def test_reclaiming_after_remove_sku_revives_the_row_and_the_sku(client, seed):
    p = mk(client, seed)
    for ms in (seed.msku_a, seed.msku_c):
        client.post(f"/v1/plans/{p}/claims", json={"seller_sku": ms[0], "sid": ms[1]},
                    headers=H(seed.actor))
    assert remove(client, seed, p, seed.sku_a).status_code == 200
    body = {"seller_sku": seed.msku_c[0], "sid": seed.msku_c[1]}
    assert client.post(f"/v1/plans/{p}/claims", json=body, headers=H(seed.actor)).status_code == 200
    with pg_conn() as c, c.cursor() as cur:
        # ★ 同一行复活（软释放，不是新插一行）
        cur.execute("SELECT count(*), count(*) FILTER (WHERE released_at IS NULL) FROM msku_claim"
                    " WHERE plan_id = %s AND seller_sku = %s AND sid = %s", (p, *seed.msku_c))
        assert cur.fetchone() == (1, 1)
    assert len(purchase_cells(p, seed.sku_a)) == 3
    g = client.get(f"/v1/plans/{p}/grid", headers=H(seed.actor)).json()
    assert {x["seller_sku"] for x in g["demand"]} == {seed.msku_c[0]}
    assert seed.sku_a in {x["sku"] for x in g["forecast_sku"]}
    assert {x["sku"] for x in g["purchase"]} == {seed.sku_a}


def _blocked(target, hold_sql):
    """★ 在一个未提交事务里先拿 (plan, sku) 锁（并可做点写），另一线程跑 `target`：
    它必须被挡住；提交后它才走完。只测「A 提交完 B 再跑」证明不了串行。"""
    out, started = {}, threading.Event()

    def run():
        started.set()
        out["r"] = target()

    with pg_conn() as a, a.cursor() as cur:
        for sql, args in hold_sql:
            cur.execute(sql, args)
        t = threading.Thread(target=run)
        t.start()
        started.wait(1)
        t.join(timeout=0.7)
        assert t.is_alive(), "★ 没有被 (plan, sku) 锁挡住 —— 两条路径没拿同一把锁"
    t.join(timeout=10)
    assert not t.is_alive()
    return out["r"]


LOCK = "SELECT pg_advisory_xact_lock(%s, hashtext(%s))"


def test_claim_and_release_and_remove_sku_all_wait_on_the_same_sku_lock(client, seed):
    p = mk(client, seed)
    for ms in (seed.msku_a, seed.msku_b):
        client.post(f"/v1/plans/{p}/claims", json={"seller_sku": ms[0], "sid": ms[1]},
                    headers=H(seed.actor))
    r = _blocked(lambda: client.delete(f"/v1/plans/{p}/claims/{seed.msku_a[0]}/{seed.msku_a[1]}",
                                       headers=H(seed.actor)), [(LOCK, (p, seed.sku_a))])
    assert r.status_code == 200, r.text
    r = _blocked(lambda: client.post(f"/v1/plans/{p}/claims",
                                     json={"seller_sku": seed.msku_c[0], "sid": seed.msku_c[1]},
                                     headers=H(seed.actor)), [(LOCK, (p, seed.sku_a))])
    assert r.status_code == 200, r.text
    r = _blocked(lambda: remove(client, seed, p, seed.sku_a), [(LOCK, (p, seed.sku_a))])
    assert r.status_code == 200, r.text


def test_release_of_the_last_msku_racing_a_sibling_claim_keeps_the_purchase_cells(client, seed):
    """★ 并发：兄弟 msku 的认领还没提交时，最后一个 msku 被释放 —— 释放必须等认领落地、
    看见这个兄弟、留下采购格。否则会出现「有在认领的 msku、却没有采购格」的货号。
    （认领那一侧用同一把锁 + 同样的写模拟；claim() 确实拿这把锁由上一条钉住。）"""
    p = mk(client, seed)
    client.post(f"/v1/plans/{p}/claims", json={"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]},
                headers=H(seed.actor))
    r = _blocked(
        lambda: client.delete(f"/v1/plans/{p}/claims/{seed.msku_a[0]}/{seed.msku_a[1]}",
                              headers=H(seed.actor)),
        [(LOCK, (p, seed.sku_a)),
         ("INSERT INTO msku_claim (plan_id, seller_sku, sid, claimed_by) VALUES (%s, %s, %s, %s)",
          (p, *seed.msku_b, seed.actor))])
    assert r.status_code == 200 and r.json()["dropped_purchase_cells"] == []
    assert len(purchase_cells(p, seed.sku_a)) == 3
    assert active_claims(p) == [seed.msku_b]


def test_two_sibling_releases_racing_still_drop_the_purchase_cells(client, seed):
    """★ 并发：两个兄弟同时释放 —— 各自看见对方「还在」就谁都不删，格子照样搁浅。
    后到的那一个必须等先到的提交，再数时看见对方已释放。"""
    p = mk(client, seed)
    for ms in (seed.msku_a, seed.msku_b):
        client.post(f"/v1/plans/{p}/claims", json={"seller_sku": ms[0], "sid": ms[1]},
                    headers=H(seed.actor))
    r = _blocked(
        lambda: client.delete(f"/v1/plans/{p}/claims/{seed.msku_b[0]}/{seed.msku_b[1]}",
                              headers=H(seed.actor)),
        [(LOCK, (p, seed.sku_a)),
         (("UPDATE msku_claim SET released_at = now(), released_by = %s"
           " WHERE plan_id = %s AND seller_sku = %s AND sid = %s"), (seed.actor, p, *seed.msku_a))])
    assert r.status_code == 200 and len(r.json()["dropped_purchase_cells"]) == 3
    assert purchase_cells(p, seed.sku_a) == [] and active_claims(p) == []


def test_reclaiming_an_active_msku_is_a_no_op_without_touching_the_source(client, seed, use_source):
    """★ 控制器裁定（fix round 2）：同一计划里重复认领一个在认领中的 msku（双击 / 两个标签页 /
    重试）= 幂等空操作：不取数、不重种格子、人填的数不动，响应点明 already_claimed。"""
    from fixture_source import FIXTURES, FixtureSource
    p = mk(client, seed)
    body = {"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]}
    first = client.post(f"/v1/plans/{p}/claims", json=body, headers=H(seed.actor)).json()
    assert first["already_claimed"] is False
    client.put(f"/v1/plans/{p}/demand/{seed.msku_a[0]}/{seed.msku_a[1]}/2026-10",
               json={"expected_units": 7}, headers=H(seed.actor))
    calls = []

    class Counting(FixtureSource):
        def daily_sales(self, *a):
            calls.append(a)
            return super().daily_sales(*a)

    use_source(lambda: Counting(FIXTURES))
    r = client.post(f"/v1/plans/{p}/claims", json=body, headers=H(seed.actor))
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["already_claimed"] is True and b["claimed"]["sku"] == seed.sku_a
    assert b["seeded"] == {"demand_cells": 0, "purchase_cells": 0}
    assert calls == [], "★ 空操作不许去打取数源"
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT expected_units FROM plan_demand_cell WHERE plan_id = %s"
                    " AND seller_sku = %s AND period_start = '2026-10-01'", (p, seed.msku_a[0]))
        assert cur.fetchone()[0] == 7


def test_active_reclaim_racing_release_does_not_deadlock(client, seed, use_source):
    """★ 修复前：重复认领走 ON CONFLICT DO UPDATE → 攥着认领行锁去取数 → 再等 (计划, 货号) 锁；
    同时释放拿着 (计划, 货号) 锁在等那一行 → 40P01，其中一个请求 500。
    两条连接真的交错：认领卡在取数里时释放进场。"""
    from fixture_source import FIXTURES, FixtureSource
    p = mk(client, seed)
    body = {"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]}
    client.post(f"/v1/plans/{p}/claims", json=body, headers=H(seed.actor))
    entered, go = threading.Event(), threading.Event()

    class Gated(FixtureSource):
        def daily_sales(self, *a):
            entered.set()
            go.wait(5)
            return super().daily_sales(*a)

    use_source(lambda: Gated(FIXTURES))
    out = {}
    t1 = threading.Thread(target=lambda: out.__setitem__(
        "claim", client.post(f"/v1/plans/{p}/claims", json=body, headers=H(seed.actor))))
    t1.start()
    entered.wait(1)                          # 修复后不会进取数 —— 等不到就直接往下走
    t2 = threading.Thread(target=lambda: out.__setitem__(
        "release", client.delete(f"/v1/plans/{p}/claims/{seed.msku_a[0]}/{seed.msku_a[1]}",
                                 headers=H(seed.actor))))
    t2.start()
    t2.join(timeout=0.5)
    go.set()
    t1.join(timeout=15)
    t2.join(timeout=15)
    assert not t1.is_alive() and not t2.is_alive()
    assert out["claim"].status_code == 200, out["claim"].text
    assert out["release"].status_code == 200, out["release"].text
    # 终态自洽：释放赢了 → 没有在认领的 msku，也就没有格子
    assert active_claims(p) == []
    assert purchase_cells(p, seed.sku_a) == []
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM plan_demand_cell WHERE plan_id = %s", (p,))
        assert cur.fetchone()[0] == 0
