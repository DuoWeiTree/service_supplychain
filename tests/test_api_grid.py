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
    assert set(g["demand"][0]) == {"seller_sku", "sid", "sku", "period", "system_units",
                                   "expected_units", "basis", "system_extrapolated",
                                   "effective_units"}
    # ★ effective_units 是 effective_demand() 的单一来源，不许前端自己重算一遍
    for row in g["demand"]:
        expected = row["expected_units"] if row["expected_units"] is not None \
            else row["system_units"]
        assert row["effective_units"] == expected
    assert set(g["purchase"][0]) == {"sku", "period", "planned_units"}
    assert set(g["inventory"][0]) == {"sku", "sid", "period", "onhand", "inbound",
                                      "closing", "basis"}
    assert set(g["inventory"][0]["basis"]) == {
        "source", "as_of", "includes_plan_purchase", "demand", "reason",
        "closing_reason", "sku_level_in_transit", "sources"}
    assert g["inventory"][0]["basis"]["includes_plan_purchase"] is False
    assert all(r["no_seller_attribution"] is True for r in g["sku_pipeline"])


def test_inventory_is_onhand_minus_demand_and_in_transit_stays_out(client, seed):
    """★ closing = onhand − demand。采购在途是货号级的，不进任何店铺的加项 ——
    「计划内只有一家店认领」不等于它是这批货的唯一消费者。"""
    pid = setup_plan(client, seed)
    client.put(f"/v1/plans/{pid}/demand/MSKU-A/11072/2026-10",
               json={"expected_units": 100}, headers=H(seed.actor))
    g = client.get(f"/v1/plans/{pid}/grid", headers=H(seed.actor)).json()
    oct_row = next(r for r in g["inventory"] if r["period"] == "2026-10")
    assert (oct_row["onhand"], oct_row["inbound"], oct_row["closing"]) == (300, None, 200)
    assert oct_row["basis"]["demand"] == 100
    assert oct_row["basis"]["reason"] == "no_seller_attribution"
    assert oct_row["basis"]["closing_reason"] is None, "★ 在途没归属不该把期末也变成未知"
    assert oct_row["basis"]["includes_plan_purchase"] is False
    assert oct_row["basis"]["as_of"] == "2026-09-21"
    # ★ 这一格的在途确实存在（80 = 50 + 30），只是没有店铺归属 ——
    #   空的 sources 与非零的 sku_level_in_transit 必须同时出现，
    #   否则「没有货」和「有货但不知道是谁的」长得一模一样
    assert oct_row["basis"]["sources"] == [] and oct_row["basis"]["sku_level_in_transit"] == 80


def test_onhand_is_the_sum_of_that_stores_mskus(client, seed, monkeypatch):
    """★ inventory 的键是 (sku, sid)，而在仓在库里是 msku 级 ——
    合计行就是各 msku 之和，不许另算一遍（14 §1 ①）。

    ★ fixture 里 MSKU-B 在仓是 0，300 == 300+0 时断言对「掉一个 msku」证伪不了 ——
    改用 monkeypatch 让第二个 msku 非零（50），这样掉一个就不等于 350。
    """
    from api.ui import plans as plans_module
    fake_onhand = {("MSKU-A", "11072"): 300, ("MSKU-B", "11072"): 50}
    monkeypatch.setattr(plans_module.SOURCE, "onhand_available",
                        lambda seller_sku, sid: fake_onhand.get((seller_sku, sid)))
    pid = setup_plan(client, seed, mskus=(("MSKU-A", "11072"), ("MSKU-B", "11072")))
    g = client.get(f"/v1/plans/{pid}/grid", headers=H(seed.actor)).json()
    rows = sorted([r for r in g["inventory"] if r["sid"] == "11072"], key=lambda r: r["period"])
    assert len(rows) == 3
    assert rows[0]["onhand"] == 350, "MSKU-A 300 + MSKU-B 50 —— 掉一个就不是 350"

    # ★ 在仓事实只在首月进一次，其后期初 = 上月期末（14 §0 的月度链）——
    #   不这样断言就漏掉「按 msku 去重」这件事：去重掉了会从 1050 起步
    #   （2 个 msku × 3 期各计一次：3×300 + 3×50）而不是从 350 起步。
    for prev, cur in itertools.pairwise(rows):
        assert cur["onhand"] == prev["closing"], \
            "★ 在仓只在首月进一次，其后期初 = 上月期末（14 §0）；去重掉了会从 1050 起步"


def test_in_transit_is_never_handed_to_a_store(client, seed):
    """★ 不分单店多店：两个店各自的 inbound 都是 null，总量只在 basis 与 sku_pipeline 里。

    给单店发全额是分摊假设（同一货号可能被本计划之外的店铺在卖），
    平摊是另一种分摊假设 —— 两种都是发明分配规则（C4 禁）。
    """
    pid = setup_plan(client, seed, mskus=(("MSKU-A", "11072"), ("MSKU-C", "11094")))
    g = client.get(f"/v1/plans/{pid}/grid", headers=H(seed.actor)).json()
    oct_rows = [r for r in g["inventory"] if r["period"] == "2026-10"]
    assert {r["sid"] for r in oct_rows} == {"11072", "11094"}
    assert all(r["inbound"] is None for r in oct_rows)
    assert all(r["basis"]["reason"] == "no_seller_attribution" for r in oct_rows)
    assert all(r["basis"]["sku_level_in_transit"] == 80 for r in oct_rows)
    # ★ 两个店的在途都是 null，但期末仍算得出来 —— 别把「不知道是谁的」传染成「什么都不知道」
    assert all(r["closing"] is not None for r in oct_rows)
    # ★ 同一批货没有被数两遍：两行的 sku_level_in_transit 是同一个 80，不是各自一份
    oct_pipe = next(r for r in g["sku_pipeline"] if r["period"] == "2026-10")
    assert oct_pipe["units"] == 80 and len(oct_pipe["sources"]) == 2


def test_demand_cell_keeps_system_and_human_apart(client, seed):
    pid = setup_plan(client, seed)
    g = client.get(f"/v1/plans/{pid}/grid", headers=H(seed.actor)).json()
    cell = next(r for r in g["demand"] if r["period"] == "2026-10")
    assert cell == {"seller_sku": "MSKU-A", "sid": "11072", "sku": "DCC1800264G1",
                    "period": "2026-10", "system_units": 100, "expected_units": None,
                    "basis": "system", "system_extrapolated": False, "effective_units": 100}


def test_extrapolated_flag_travels_with_the_number(client, seed):
    """★ 14 §5：标记必须随结果一起返回，不能让界面回头读原始格子。"""
    pid = client.post("/v1/plans", json={"title": "长周期", "period_start": "2026-10-01",
                                         "months": 7}, headers=H(seed.actor)).json()["plan_id"]
    client.post(f"/v1/plans/{pid}/claims", json={"seller_sku": "MSKU-A", "sid": "11072"},
                headers=H(seed.actor))
    g = client.get(f"/v1/plans/{pid}/grid", headers=H(seed.actor)).json()
    flags = [r["system_extrapolated"] for r in sorted(g["demand"], key=lambda r: r["period"])]
    assert flags == [False, False, False, True, True, True, True]
    assert not all(flags), "★ 恒真的标记等于白标"


def test_no_fba_seller_is_not_applicable_not_zero(client, seed):
    """★ 02 §3.1a：无 FBA 的平台是「不适用」，不是 0 —— closing 为 null（裁定第 1 条）。"""
    pid = setup_plan(client, seed, mskus=(("MSKU-W", "90001"),))
    g = client.get(f"/v1/plans/{pid}/grid", headers=H(seed.actor)).json()
    row = g["inventory"][0]
    assert row["onhand"] is None and row["closing"] is None
    assert row["basis"]["closing_reason"] == "not_applicable"


def test_the_two_reasons_behind_a_null_closing_are_distinguishable(client, seed):
    """★ closing 的 null 只有两种成因，记在 closing_reason 上（头部口径 b）；
    inbound 恒 null 那件事记在 reason 上，两者不许挤进同一个字段。"""
    pid = setup_plan(client, seed)
    client.put(f"/v1/plans/{pid}/demand/MSKU-A/11072/2026-10",
               json={"expected_units": None}, headers=H(seed.actor))
    with pg_conn() as c, c.cursor() as cur:   # 把系统预估也清掉 → 这一格真的是未知
        cur.execute("UPDATE plan_demand_cell SET system_units = NULL"
                    " WHERE plan_id = %s AND period_start = '2026-10-01'", (pid,))
    g = client.get(f"/v1/plans/{pid}/grid", headers=H(seed.actor)).json()
    rows = sorted(g["inventory"], key=lambda r: r["period"])
    assert [r["closing"] for r in rows] == [None, None, None]
    assert rows[0]["basis"]["closing_reason"] == "unknown_demand"
    assert rows[0]["basis"]["reason"] == "no_seller_attribution"


def test_put_demand_and_purchase_are_one_cell_one_transaction(client, seed):
    pid = setup_plan(client, seed)
    r1 = client.put(f"/v1/plans/{pid}/demand/MSKU-A/11072/2026-11",
                    json={"expected_units": 130}, headers=H(seed.actor))
    assert r1.status_code == 200 and r1.json()["cell"]["expected_units"] == 130
    assert r1.json()["cell"]["basis"] == "human"
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
