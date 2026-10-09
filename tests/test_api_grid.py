import itertools

from shared.pg_client import pg_conn


def H(a):
    return {"x-actor": a}


def setup_plan(client, seed, mskus=(("MSKU-A", "11072"),)):
    pid = client.post("/v1/plans", json={"title": "10 月计划", "period_start": "2026-10-01",
                                         "months": 3}, headers=H(seed.actor)).json()["plan_id"]
    for ms in mskus:
        client.post(f"/v1/plans/{pid}/claims", json={"seller_sku": ms[0], "sid": ms[1]},
                    headers=H(seed.actor))
    return pid


def test_grid_blocks_carry_exactly_the_ruled_keys(client, seed):
    """★ 契约测试：键少一个前端就读到 undefined，多一个不致命但要是有意的。"""
    pid = setup_plan(client, seed)
    g = client.get(f"/v1/plans/{pid}/grid", headers=H(seed.actor)).json()
    assert g["periods"] == ["2026-10", "2026-11", "2026-12"]
    # spec §3.3：依据随数走 —— demand 行多一个 system_basis
    assert set(g["demand"][0]) == {"seller_sku", "sid", "sku", "period", "system_units",
                                   "expected_units", "basis", "system_extrapolated",
                                   "effective_units", "system_basis"}
    # ★ effective_units 是 effective_demand() 的单一来源，不许前端自己重算一遍
    for row in g["demand"]:
        expected = row["expected_units"] if row["expected_units"] is not None \
            else row["system_units"]
        assert row["effective_units"] == expected
    assert set(g["purchase"][0]) == {"sku", "period", "planned_units"}
    # ★ spec §6：阶段 A 的「在仓 − 期望」口径已被取代；两套库存数并存就是两个真相（spec §4.6）。
    #   ★ forecast_msku 是本任务之外（Task 7）已经存在的顶层键（MskuProjection 是 list 子类，
    #   序列化会丢掉 msku 级依据，故显式发出去）——不在本任务的取代范围内，仍要在。
    assert set(g) == {"plan_id", "periods", "demand", "purchase", "source_notes", "params",
                      "forecast", "forecast_msku", "forecast_store", "forecast_sku",
                      "beyond_horizon", "unallocated"}


def test_store_closing_is_onhand_minus_demand_and_in_transit_stays_out(client, seed):
    """★ closing = opening(onhand) − demand + inbound（店内可分配批次）。已有在途（ordered_open）
    是认领前就存在的货号级批次，从不喂进任何店铺的台账（plan_forecast.py：`inp.ordered_open`
    只在货号层序列化成 pipeline.ordered_open，不进 project_msku 的逐日账）——
    「计划内只有一家店认领」不等于它是这批货的唯一消费者。"""
    pid = setup_plan(client, seed)
    client.put(f"/v1/plans/{pid}/demand/MSKU-A/11072/2026-10",
               json={"expected_units": 100}, headers=H(seed.actor))
    g = client.get(f"/v1/plans/{pid}/grid", headers=H(seed.actor)).json()
    oct_row = next(r for r in g["forecast_store"]
                   if r["sid"] == "11072" and r["period"] == "2026-10")
    # ★ 300 是 as_of(9-21) 那天的在仓事实，opening 是 10 月期初 —— 中间 9-21→9-30 逐日
    #   按销量扣减（14 §0），不再是旧口径那个原样搬过来的 onhand；恒等式照样成立
    assert oct_row["inbound_planned"] == 0
    assert oct_row["opening"] == 261
    assert oct_row["closing"] == oct_row["opening"] - oct_row["demand"] + oct_row["inbound_planned"]
    assert oct_row["demand"] == 100
    assert oct_row["state"] == "ok" and oct_row["reason"] is None
    assert g["params"]["as_of"] == "2026-09-21"
    # ★ 这一格的在途确实存在（Oct 50+30，Nov 另有 120，货号层总量 200），只是没有店铺
    #   归属 —— 非零的 pipeline.ordered_open 必须与店铺行的 inbound_planned=0 同时出现，
    #   否则「没有货」和「有货但不知道是谁的」长得一模一样
    sku_oct = next(r for r in g["forecast_sku"]
                   if r["sku"] == seed.sku_a and r["period"] == "2026-10")
    assert sku_oct["pipeline"]["ordered_open"] == 200


def test_onhand_is_the_sum_of_that_stores_mskus(client, seed, use_source):
    """★ forecast_store 的键是 (sku, sid)，而在仓在库里是 msku 级 ——
    合计行就是各 msku 之和，不许另算一遍（14 §1 ①）。

    ★ fixture 里 MSKU-B 在仓是 0，300 == 300+0 时断言对「掉一个 msku」证伪不了 ——
    改用 monkeypatch 让第二个 msku 非零（50），这样掉一个就不等于 350。
    """
    from fixture_source import FIXTURES, FixtureSource
    fake_onhand = {("MSKU-A", "11072"): 300, ("MSKU-B", "11072"): 50}

    class Src(FixtureSource):
        def onhand_available(self, seller_sku, sid):
            return fake_onhand.get((seller_sku, sid))

    use_source(lambda: Src(FIXTURES))
    pid = setup_plan(client, seed, mskus=(("MSKU-A", "11072"), ("MSKU-B", "11072")))
    g = client.get(f"/v1/plans/{pid}/grid", headers=H(seed.actor)).json()
    rows = sorted([r for r in g["forecast_store"] if r["sid"] == "11072"],
                  key=lambda r: r["period"])
    assert len(rows) == 3
    # ★ 300 + 50 = 350 是 as_of 那天的合计在仓事实；10 月期初已经过 9-21→9-30 逐日扣减
    #   （两个 msku 的合并日均），281 才是「没掉一个 msku」时的正确期初 —— 掉一个就不是 281
    assert rows[0]["opening"] == 281, "MSKU-A 300 + MSKU-B 50 逐日扣减后 —— 掉一个就不是 281"

    # ★ 在仓事实只在首月进一次，其后期初 = 上月期末（14 §0 的月度链）——
    #   不这样断言就漏掉「按 msku 去重」这件事：去重掉了会从 1050 起步
    #   （2 个 msku × 3 期各计一次：3×300 + 3×50）而不是从 350 起步。
    for prev, cur in itertools.pairwise(rows):
        assert cur["opening"] == prev["closing"], \
            "★ 在仓只在首月进一次，其后期初 = 上月期末（14 §0）；去重掉了会从 1050 起步"


def test_in_transit_is_never_handed_to_a_store(client, seed):
    """★ 不分单店多店：两个店各自的 inbound_planned 都是 0，总量只在货号层 pipeline 里。

    给单店发全额是分摊假设（同一货号可能被本计划之外的店铺在卖），
    平摊是另一种分摊假设 —— 两种都是发明分配规则（C4 禁）。
    """
    pid = setup_plan(client, seed, mskus=(("MSKU-A", "11072"), ("MSKU-C", "11094")))
    g = client.get(f"/v1/plans/{pid}/grid", headers=H(seed.actor)).json()
    oct_rows = [r for r in g["forecast_store"] if r["period"] == "2026-10"]
    assert {r["sid"] for r in oct_rows} == {"11072", "11094"}
    assert all(r["inbound_planned"] == 0 for r in oct_rows)
    assert all(r["state"] == "ok" and r["reason"] is None for r in oct_rows)
    # ★ 两个店的 inbound 都是 0，但期末仍算得出来 —— 别把「不知道是谁的」传染成「什么都不知道」
    assert all(r["closing"] is not None for r in oct_rows)
    # ★ 同一批货没有被数两遍：两个店共享同一个货号，货号层只出现一次、一个总量
    sku_oct = next(r for r in g["forecast_sku"]
                   if r["sku"] == seed.sku_a and r["period"] == "2026-10")
    assert sku_oct["pipeline"]["ordered_open"] == 200
    assert len(sku_oct["pipeline"]["ordered_open_sources"]) == 3


def test_demand_cell_keeps_system_and_human_apart(client, seed):
    pid = setup_plan(client, seed)
    g = client.get(f"/v1/plans/{pid}/grid", headers=H(seed.actor)).json()
    cell = next(r for r in g["demand"] if r["period"] == "2026-10")
    # spec §3.1：预测换成 base_rate × 天数 —— ceil(4.325 × 31) = 135；依据随数走（§3.3）
    basis = cell.pop("system_basis")
    assert cell == {"seller_sku": "MSKU-A", "sid": "11072", "sku": "DCC1800264G1",
                    "period": "2026-10", "system_units": 135, "expected_units": None,
                    "basis": "system", "system_extrapolated": False, "effective_units": 135}
    assert (basis["days_in_month"], basis["reason"]) == (31, None)


def test_extrapolated_flag_travels_with_the_number(client, seed):
    """★ 14 §5：标记必须随结果一起返回，不能让界面回头读原始格子。"""
    pid = client.post("/v1/plans", json={"title": "长周期", "period_start": "2026-10-01",
                                         "months": 7}, headers=H(seed.actor)).json()["plan_id"]
    client.post(f"/v1/plans/{pid}/claims", json={"seller_sku": "MSKU-A", "sid": "11072"},
                headers=H(seed.actor))
    # spec §3.3：base_rate × 天数不外推，算法如实给 false —— 7 个月全是 false
    g = client.get(f"/v1/plans/{pid}/grid", headers=H(seed.actor)).json()
    flags = [r["system_extrapolated"] for r in sorted(g["demand"], key=lambda r: r["period"])]
    assert flags == [False] * 7
    # ★ 「随数走」而不是在 grid 里盖章：库里那一格是 true（旧种子），grid 就得如实回 true
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE plan_demand_cell SET system_extrapolated = TRUE"
                    " WHERE plan_id = %s AND period_start = DATE '2027-04-01'", (pid,))
    g = client.get(f"/v1/plans/{pid}/grid", headers=H(seed.actor)).json()
    flags = [r["system_extrapolated"] for r in sorted(g["demand"], key=lambda r: r["period"])]
    assert flags == [False] * 6 + [True]


def test_no_fba_seller_is_not_applicable_not_zero(client, seed):
    """★ 02 §3.1a：无 FBA 的平台是「不适用」，不是 0 —— closing 为 null（裁定第 1 条）。"""
    pid = setup_plan(client, seed, mskus=(("MSKU-W", "90001"),))
    g = client.get(f"/v1/plans/{pid}/grid", headers=H(seed.actor)).json()
    row = next(r for r in g["forecast_store"] if r["sid"] == "90001")
    assert row["opening"] is None and row["closing"] is None
    assert row["state"] == "not_applicable" and row["reason"] == "not_applicable"


def test_fba_seller_missing_the_onhand_number_is_unknown_not_not_applicable(client, seed):
    """★ 09-23 真实缺陷（负责人在真实数据下撞见）：`seed.msku_e` 绑在**有 FBA** 的
    `seller_a` 上，但阶段 A 的冻结 fixture（`tests/fixtures/fba_onhand.csv`）里刻意
    没有它这一行 —— 这是「有 FBA 但数字没取到」，必须读成未知，不是不适用。

    旧实现把 `onhand is None` 一步到位地读成 `not_applicable`，即使这个 (sku, sid)
    对应的店铺 `has_fba=true`——前端 `na_disagrees_with_seller` 守卫正是靠对比
    `closing_reason === 'not_applicable'` 与 `seller.has_fba` 抓到这个坍缩的。
    """
    pid = setup_plan(client, seed, mskus=(seed.msku_e,))
    g = client.get(f"/v1/plans/{pid}/grid", headers=H(seed.actor)).json()
    rows = sorted((r for r in g["forecast"] if r["seller_sku"] == "MSKU-E"),
                  key=lambda r: r["period"])
    assert len(rows) == 3, "三个月计划该有三行"
    for row in rows:
        assert row["opening"] is None and row["closing"] is None
        assert row["reason"] == "unknown_onhand"
        assert row["reason"] != "not_applicable", "该店有 FBA —— 不许被标成不适用"
    # ★ 需求本身是已知的（seed 里给了 MSKU-E 三个月销售历史）——
    #   证明这一格是「在仓未知」而不是被「需求也未知」顺带带出来的
    assert all(r["demand"] is not None for r in rows), \
        "这一格的未知专属于在仓，不该连需求也一起说不清"


def test_a_null_closing_names_which_cause_made_it_null(client, seed):
    """★ closing 的 null 必须点名具体成因，不能囫囵地说「不知道」。

    阶段 A 旧口径把「inbound 恒 null」单独记一个 reason、「closing 为何 null」另记一个
    closing_reason，两者不许挤进同一个字段 —— 那是因为旧口径的 inbound 本身就是个恒定的
    null（没有店铺归属的推断能力）。新口径下 inbound_planned 是推演算出来的真实数字
    （可能是 0，也可能非零），不再是「恒 null 配一个理由」，于是旧口径要拆开的那两件事
    在新口径下自然只剩一件：state/reason 就是让这一格未知的那个具体原因。"""
    pid = setup_plan(client, seed)
    client.put(f"/v1/plans/{pid}/demand/MSKU-A/11072/2026-10",
               json={"expected_units": None}, headers=H(seed.actor))
    with pg_conn() as c, c.cursor() as cur:   # 把系统预估也清掉 → 这一格真的是未知
        cur.execute("UPDATE plan_demand_cell SET system_units = NULL"
                    " WHERE plan_id = %s AND period_start = '2026-10-01'", (pid,))
    g = client.get(f"/v1/plans/{pid}/grid", headers=H(seed.actor)).json()
    rows = sorted([r for r in g["forecast_store"] if r["sid"] == "11072"],
                  key=lambda r: r["period"])
    assert [r["closing"] for r in rows] == [None, None, None]
    assert rows[0]["state"] == "unknown" and rows[0]["reason"] == "unknown_demand"


def test_put_demand_and_purchase_are_one_cell_one_transaction(client, seed):
    pid = setup_plan(client, seed)
    r1 = client.put(f"/v1/plans/{pid}/demand/MSKU-A/11072/2026-11",
                    json={"expected_units": 130}, headers=H(seed.actor))
    cell = r1.json()["cell"]
    assert r1.status_code == 200 and cell["expected_units"] == 130
    assert cell["basis"] == "human"
    # ★ team-lead 裁定：PUT 回的必须是 grid() 那一格同样的十键，不是子集 ——
    #   少 sku/effective_units 两个字段，前端原地替换那一行时在真实 API 下会读到 undefined。
    #   spec §3.3：依据随数走 —— 第十键 system_basis
    assert set(cell) == {"seller_sku", "sid", "sku", "period", "system_units",
                         "expected_units", "basis", "system_extrapolated",
                         "effective_units", "system_basis"}
    assert cell["system_basis"]["days_in_month"] == 30, "PUT 回的依据必须是这一格（11 月）自己的"
    assert cell["sku"] == seed.sku_a
    assert cell["effective_units"] == 130          # 人填优先
    r2 = client.put(f"/v1/plans/{pid}/purchase/{seed.sku_a}/2026-11",
                    json={"planned_units": 500}, headers=H(seed.actor))
    assert r2.status_code == 200 and r2.json()["cell"]["planned_units"] == 500


def test_put_on_a_cell_that_was_never_seeded_is_404(client, seed):
    """★ 只能改已经长出来的格子 —— 凭空插一行会绕过「没认领就没格子」。"""
    pid = setup_plan(client, seed)
    r = client.put(f"/v1/plans/{pid}/demand/MSKU-A/11072/2027-05",
                   json={"expected_units": 1}, headers=H(seed.actor))
    assert r.status_code == 404 and r.json()["error"] == "cell_not_found"


def test_negative_units_are_400(client, seed):
    pid = setup_plan(client, seed)
    r = client.put(f"/v1/plans/{pid}/purchase/{seed.sku_a}/2026-10",
                   json={"planned_units": -1}, headers=H(seed.actor))
    assert r.status_code == 400 and r.json()["error"] == "bad_units"
