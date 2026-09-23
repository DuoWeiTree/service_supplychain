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
    # fixture 里 MSKU-A 的历史是 100/120/90，计划 3 个月 → 不外推
    assert [r[1] for r in rows] == [100, 120, 90]
    assert [r[2] for r in rows] == [False, False, False]
    assert [r[3] for r in rows] == [None, None, None], "★ 人填列留空 = 未知，不预填"


def test_msku_without_history_is_seeded_null_and_named(client, seed, use_source):
    """★ 没有历史 ≠ 预估 0。格子照建，system_units 留 NULL，并在返回里点名。

    ★ 终审 I-2 之后换了靶子：原先拿 MSKU-W（Walmart 店）当「没有历史」，而那
      其实是**不适用**（见下一条）。这里改成一个 Amazon 店的 msku，用一个真的
      返回空历史的源 —— 「查过了、一行都没有」与「压根没有这条取数链路」是
      两件事，两个 reason 各有各的靶子。"""
    from api.ui import plans
    from dim.fixture_source import FixtureSource

    class NoHistory(FixtureSource):
        def monthly_sales_history(self, seller_sku, sid, months):
            return []

    use_source(lambda: NoHistory(plans.FIXTURES))
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
    p = mk(client, seed)
    body = {"seller_sku": seed.msku_nofba[0], "sid": seed.msku_nofba[1]}
    r0 = client.post(f"/v1/plans/{p}/claims", json=body, headers=H(seed.actor))
    assert r0.status_code == 200, r0.text
    fixture_body = r0.json()

    use_source(lambda: cs.ChSource(must_not_be_called))
    r = client.post(f"/v1/plans/{p}/claims", json=body, headers=H(seed.actor))
    assert r.status_code == 200, r.text
    ch_body = r.json()
    assert ch_body["no_history"] == fixture_body["no_history"], (
        f"两档对同一个 msku 给出不同答案：ch={ch_body['no_history']} "
        f"fixture={fixture_body['no_history']}")
    assert ch_body["history_window"] == fixture_body["history_window"] is None


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


def test_release_names_the_purchase_cells_it_leaves_behind(client, seed):
    """★ 释放删掉的是 msku 级的期望销量格子；货号级的采购格子**留在原地**
    （PK 不含 msku，兄弟 msku 还要用）—— 但只报一种格子，另一种就无声地留着。

    实测后果：释放了该货号唯一的 msku 之后，`GET /grid` 里那条 purchase 仍在，
    `PUT …/purchase/…` 仍返回 200，而提交时它以 no_claimed_msku 被跳过 ——
    人要到那时才知道刚才的释放还留下了东西。
    """
    p = mk(client, seed)
    body = {"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]}
    client.post(f"/v1/plans/{p}/claims", json=body, headers=H(seed.actor))
    client.put(f"/v1/plans/{p}/purchase/{seed.sku_a}/2026-10",
               json={"planned_units": 500}, headers=H(seed.actor))
    r = client.delete(f"/v1/plans/{p}/claims/{seed.msku_a[0]}/{seed.msku_a[1]}",
                      headers=H(seed.actor)).json()
    assert r["stranded_purchase_cells"] == [
        {"sku": seed.sku_a, "period": "2026-10"},
        {"sku": seed.sku_a, "period": "2026-11"},
        {"sku": seed.sku_a, "period": "2026-12"}]
    # ★ 报了不等于删了：兄弟 msku 再认领回来时要用的就是这几行
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM plan_purchase_cell WHERE plan_id = %s AND sku = %s",
                    (p, seed.sku_a))
        assert cur.fetchone()[0] == 3


def test_a_sibling_msku_keeps_the_purchase_cells_from_being_stranded(client, seed):
    """★ 「搁浅」与「还有人用」必须分得开：同货号还有 msku 在认领中时，
    那批格子一个都没搁浅 —— 恒报「搁浅」等于每次释放都喊一次狼来了。"""
    p = mk(client, seed)
    for ms in (seed.msku_a, seed.msku_b):      # 两个 msku 同属 sku_a
        client.post(f"/v1/plans/{p}/claims", json={"seller_sku": ms[0], "sid": ms[1]},
                    headers=H(seed.actor))
    client.put(f"/v1/plans/{p}/demand/{seed.msku_a[0]}/{seed.msku_a[1]}/2026-10",
               json={"expected_units": 130}, headers=H(seed.actor))
    r = client.delete(f"/v1/plans/{p}/claims/{seed.msku_a[0]}/{seed.msku_a[1]}",
                      headers=H(seed.actor)).json()
    assert r["stranded_purchase_cells"] == []
    # ★ 两个列表各说各的事：这一次确实删掉了三个期望销量格子
    assert [x["period"] for x in r["dropped_cells"]] == ["2026-10", "2026-11", "2026-12"]
    assert {"period": "2026-10", "expected_units": 130} in r["dropped_cells"]


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
      用真 `ChSource`，它的 `query` 是一条**一被调用就炸**的桩 —— 闸没拦住就会
      去查一个不存在的取数源。
    """
    from dim import ch_source as cs

    def must_not_be_called(sql, parameters=None):
        raise AssertionError(
            f"没有销量来源的店去查了 CH —— 闸没拦住。SQL={' '.join(sql.split())[:120]}")

    ms = _seed_gap_store(seed)
    use_source(lambda: cs.ChSource(must_not_be_called))
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
    use_source(lambda: cs.ChSource(must_not_be_called))
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


def test_the_unknown_store_hint_splits_by_cause():
    """★★ 残留轮次（must）：运维**先读 hint**。两种成因的处置是**相反**的 ——
    没人声明过的 sid 该去补一行 `STORE_SID`；已登记的取数缺口**绝不能补**
    （补了就是硬编一行不存在的映射，把另一家店的销量记到它头上）。

    修复前两种共用一句 hint「这个店没有声明取数映射（缺一行）」，包括那三个
    已登记的 sid —— 正是 M-4 刚从 `sid_for_or_raise` 里拿掉的那条错建议，
    原样留在了人先读到的那个字段里。

    ★ 为什么直接调翻译函数而不走一次 HTTP：`claim()` 现在把「已登记的缺口」
      在到达这里之前就翻成了 200 + 不适用（上面那条测试），所以这一支在今天
      **没有端到端路径**。留着并钉住它，是因为 `_source_error` 是整个取数失败
      家族的唯一翻译口，而 `store_for()` 是公开的 dim API —— 一个对自己接受的
      异常形态**给错建议**的翻译口，比没有这一支更坏（这正是本条要修的缺陷本身）。
    """
    from api.ui import plans

    registered = m.UnknownStore("sid='11098' …", sid="11098",
                                registered_gap=m.NO_ORDER_REPORT_SID["11098"])
    err = plans._source_error(registered)
    body = err.body()
    assert err.status == 503 and body["error"] == "forecast_source_unusable"
    assert "不要" in body["hint"] and "由人填" in body["hint"], body["hint"]
    assert "缺一行" not in body["hint"], (
        f"已登记的缺口还在被建议去补映射表：{body['hint']}")
    assert body["registered_gap"] == m.NO_ORDER_REPORT_SID["11098"]
    assert body["sid"] == "11098"

    # ★ 另一支：没人声明过 —— 必须照旧给出「去补一行」这条**正确**的建议。
    never = m.UnknownStore("sid='99999' …", sid="99999", registered_gap=None)
    other = plans._source_error(never).body()
    assert "缺一行" in other["hint"], other["hint"]
    assert "registered_gap" not in other


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

    client.app.dependency_overrides[source_dep] = lambda: cs.ChSource(must_not_be_called)
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
    assert "registered_gap" not in body, (
        "没人声明过的 sid 不该带 registered_gap —— 那会让它看起来像一条已登记的缺口")
