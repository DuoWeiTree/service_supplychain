"""spec §6：grid 的预测块。场景 S1 的数字见 Task 7 的推导。"""
import json

from shared.pg_client import pg_conn

P = ["2026-10", "2026-11", "2026-12"]


def H(a):
    return {"x-actor": a}


def s1(client, seed, extra=()):
    pid = client.post("/v1/plans", json={"title": "S1", "period_start": "2026-10-01", "months": 3},
                      headers=H(seed.actor)).json()["plan_id"]
    for ms in (seed.msku_a, seed.msku_c, seed.msku_d, *extra):
        client.post(f"/v1/plans/{pid}/claims", json={"seller_sku": ms[0], "sid": ms[1]},
                    headers=H(seed.actor))
    # ★ 人填期望销量：库存只扣生效值，与认领时种的预测销量解耦（Phase 2 会换掉预测算法）
    for ms, units in ((seed.msku_a, (100, 120, 90)), (seed.msku_c, (40, 44, 44)),
                      (seed.msku_d, (7, 8, 6))):
        for p, u in zip(P, units):
            client.put(f"/v1/plans/{pid}/demand/{ms[0]}/{ms[1]}/{p}",
                       json={"expected_units": u}, headers=H(seed.actor))
    with pg_conn() as c, c.cursor() as cur:
        for ms, rate in ((seed.msku_a, 4), (seed.msku_c, 2), (seed.msku_d, 1)):
            cur.execute("UPDATE plan_demand_cell SET system_basis = %s"
                        " WHERE plan_id = %s AND seller_sku = %s AND sid = %s",
                        (json.dumps({"base_rate": rate}), pid, *ms))
    client.put(f"/v1/plans/{pid}/purchase/{seed.sku_a}/2026-10", json={"planned_units": 200},
               headers=H(seed.actor))
    return pid


def grid(client, seed, pid):
    r = client.get(f"/v1/plans/{pid}/grid", headers=H(seed.actor))
    assert r.status_code == 200, r.text
    return r.json()


def rows(g, key, **match):
    return [r for r in g[key] if all(r[k] == v for k, v in match.items())]


def chain(rs):
    return [(r["opening"], r["demand"], r["inbound_planned"], r["closing"]) for r in rs]


def test_params_say_which_rules_were_used(client, seed, lead_time_reset):
    # PF-1：认领按 base_rate 种预测后，grid 如实报 embargo_days = 7（设计取值 · 未实测）
    g = grid(client, seed, s1(client, seed))
    assert g["params"] == {"as_of": "2026-09-21", "route": "oversea_sea", "make_days": 30,
                           "ship_days": 60, "anchor_day": 15, "embargo_days": 7}


def test_default_lead_time_msku_chain_and_beyond_horizon(client, seed, lead_time_reset):
    g = grid(client, seed, s1(client, seed))
    a = rows(g, "forecast", seller_sku="MSKU-A")
    assert chain(a) == [(264, 100, 0, 164), (164, 120, 0, 44), (44, 90, 0, -46)]
    assert a[0]["basis"]["bridge"]["units"] == 36
    assert [r["closing"] for r in rows(g, "forecast", seller_sku="MSKU-D")] == [24, 16, 10]
    assert g["beyond_horizon"] == [{"sku": seed.sku_a, "order_period": "2026-10", "units": 200,
                                    "placed": "2026-10-15", "made": "2026-11-14",
                                    "arrive": "2027-01-13", "reason": "arrives_after_horizon"}]


def test_changed_lead_time_recomputes_immediately(client, seed, lead_time_reset):
    """14 B12：改提前期立即重算 —— 下一次 grid 就是新结果。"""
    pid = s1(client, seed)
    client.put("/v1/lead-time", json={"make_days": 10, "ship_days": 20}, headers=H(seed.actor))
    g = grid(client, seed, pid)
    assert chain(rows(g, "forecast", seller_sku="MSKU-C")) == [
        (7, 40, 0, -33), (-33, 44, 110, 33), (33, 44, 0, -11)]
    assert [r["stockout_date"] for r in rows(g, "forecast", seller_sku="MSKU-C")] == [
        "2026-10-06", "2026-11-01", None]
    sku = rows(g, "forecast_sku", sku=seed.sku_a)
    assert [(r["opening"], r["inbound_planned"], r["closing"]) for r in sku] == [
        (271, 0, 131), (131, 200, 167), (167, 0, 33)]
    assert [(r["pipeline"]["making"], r["pipeline"]["shipping"], r["pipeline"]["ordered_open"],
             r["in_transit"]) for r in sku] == [(0, 200, 200, 400), (0, 0, 200, 200),
                                                (0, 0, 200, 200)]
    assert sku[0]["pipeline"]["fba_inbound"] is None
    assert sku[0]["pipeline"]["fba_inbound_reason"] == "not_fetched"
    assert g["params"]["make_days"] == 10


def test_store_rows_sum_mskus_and_no_fba_store_is_not_applicable(client, seed, lead_time_reset):
    g = grid(client, seed, s1(client, seed, extra=(seed.msku_nofba,)))
    w = rows(g, "forecast_store", sid=seed.seller_nofba)
    assert [r["state"] for r in w] == ["not_applicable"] * 3
    assert all(r["closing"] is None for r in w)
    b = rows(g, "forecast_sku", sku=seed.sku_b)
    assert [r["excluded_not_applicable"] for r in b] == [1, 1, 1]
    assert [r["closing"] for r in b] == [24, 16, 10]


def test_grid_unknown_demand_makes_the_batch_unallocated(client, seed, lead_time_reset):
    """Review Focus 2 · OP-12：C 的 11 月期望与预测都空 → 11 月起未知；这批货不分给任何人。"""
    pid = s1(client, seed)
    client.put("/v1/lead-time", json={"make_days": 10, "ship_days": 20}, headers=H(seed.actor))
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE plan_demand_cell SET expected_units = NULL, system_units = NULL,"
                    " system_extrapolated = FALSE WHERE plan_id = %s AND seller_sku = %s"
                    " AND period_start = DATE '2026-11-01'", (pid, seed.msku_c[0]))
    g = grid(client, seed, pid)
    assert [u["reason"] for u in g["unallocated"]] == ["gap_unknown"]
    c_rows = rows(g, "forecast", seller_sku="MSKU-C")
    assert [(r["state"], r["reason"], r["unknown_since"]) for r in c_rows[1:]] == [
        ("unknown", "unknown_demand", "2026-11")] * 2
    a_rows = rows(g, "forecast", seller_sku="MSKU-A")
    assert (a_rows[1]["state"], a_rows[1]["reason"]) == ("unknown", "inbound_unknown")
    assert rows(g, "forecast_sku", sku=seed.sku_a)[1]["closing"] is None


def test_missing_rate_and_missing_onhand_are_named(client, seed, lead_time_reset):
    pid = s1(client, seed, extra=(seed.msku_e,))
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE plan_demand_cell SET system_basis = NULL"
                    " WHERE plan_id = %s AND seller_sku = %s", (pid, seed.msku_d[0]))
    g = grid(client, seed, pid)
    assert {r["reason"] for r in rows(g, "forecast", seller_sku="MSKU-D")} == {"bridge_rate_unknown"}
    assert {r["reason"] for r in rows(g, "forecast", seller_sku="MSKU-E")} == {"unknown_onhand"}


# ---- 以下是 brief 之后补的：接口事实（Task 6/7 交接）逐条上靶 ----


def test_claims_without_system_basis_are_unknown_not_zero(client, seed, lead_time_reset):
    """OP-8：现有认领没有 system_basis → 桥接段 rate 未知 → 整条未知并点名，绝不当 0。"""
    pid = client.post("/v1/plans", json={"title": "旧", "period_start": "2026-10-01",
                                         "months": 3}, headers=H(seed.actor)).json()["plan_id"]
    client.post(f"/v1/plans/{pid}/claims", json={"seller_sku": "MSKU-D", "sid": "11072"},
                headers=H(seed.actor))
    # spec §3.4：认领现在就种 system_basis —— 「旧认领」这一形态要显式造出来
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE plan_demand_cell SET system_basis = NULL WHERE plan_id = %s", (pid,))
    g = grid(client, seed, pid)
    d = rows(g, "forecast", seller_sku="MSKU-D")
    assert [(r["state"], r["reason"], r["closing"]) for r in d] == [
        ("unknown", "bridge_rate_unknown", None)] * 3
    # ★ 桥接段依据在 rate 缺失时照样出现：没 rate、没断货日，但到货与合计那两格在
    br = d[0]["basis"]["bridge"]
    assert (br["rate"], br["units"], br["stockout_date"]) == (None, None, None)
    assert (br["arrivals"], br["arrival_units"]) == ([], 0)


def test_unknown_sku_row_says_why_and_which_msku(client, seed, lead_time_reset):
    """SkuMonth 没有 reason 字段：货号行未知时，原因从下面的 msku 带上来，并指到是哪一个。"""
    pid = s1(client, seed, extra=(seed.msku_e,))
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE plan_demand_cell SET system_basis = NULL"
                    " WHERE plan_id = %s AND seller_sku = %s", (pid, seed.msku_d[0]))
    g = grid(client, seed, pid)
    b = rows(g, "forecast_sku", sku=seed.sku_b)
    assert [(r["state"], r["reason"], r["reason_from"]) for r in b] == [
        ("unknown", "bridge_rate_unknown", {"seller_sku": "MSKU-D", "sid": "11072"})] * 3
    e = rows(g, "forecast_sku", sku=seed.sku_e)
    assert {(r["state"], r["reason"]) for r in e} == {("unknown", "unknown_onhand")}
    a = rows(g, "forecast_sku", sku=seed.sku_a)
    assert [(r["state"], r["reason"], r["reason_from"]) for r in a] == [("ok", None, None)] * 3


def test_msku_level_first_stockout_is_emitted(client, seed, lead_time_reset):
    """MskuProjection 是 list 子类：通用序列化会丢掉 msku 级首个断货日，必须显式发出去。"""
    pid = s1(client, seed)
    client.put("/v1/lead-time", json={"make_days": 10, "ship_days": 20}, headers=H(seed.actor))
    g = grid(client, seed, pid)
    by = {(r["seller_sku"], r["sid"]): r for r in g["forecast_msku"]}
    assert set(by) == {seed.msku_a, seed.msku_c, seed.msku_d}
    assert by[seed.msku_c]["first_stockout_date"] == "2026-10-06"
    assert by[seed.msku_d]["first_stockout_date"] is None
    assert by[seed.msku_c]["sku"] == seed.sku_a


def test_sku_whose_last_msku_was_released_keeps_its_pipeline(client, seed, lead_time_reset):
    """释放后搁浅的采购格子 → 货号没有 msku，但它的在途不许在推演里消失。"""
    pid = s1(client, seed)
    r = client.delete(f"/v1/plans/{pid}/claims/MSKU-D/11072", headers=H(seed.actor))
    assert r.status_code == 200, r.text
    g = grid(client, seed, pid)
    b = rows(g, "forecast_sku", sku=seed.sku_b)
    assert [(r["state"], r["reason"], r["pipeline"]["ordered_open"]) for r in b] == [
        ("no_msku", "no_msku", 60)] * 3
    assert b[0]["pipeline"]["ordered_open_sources"] == [
        {"period": "2026-10", "units": 60, "ref": "PO-2026-0918"}]


def test_sku_with_no_purchase_cells_is_not_dropped(client, seed, lead_time_reset):
    """plan_forecast 的货号全集 = msku ∪ purchase 的键：只在在途里出现的货号会被丢掉。
    ★ 所以没有采购格子的货号也要以「采购未填」进 purchase —— 在途照算，未填照报。"""
    pid = s1(client, seed)
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("DELETE FROM plan_purchase_cell WHERE plan_id = %s AND sku = %s",
                    (pid, seed.sku_b))
    g = grid(client, seed, pid)
    b = rows(g, "forecast_sku", sku=seed.sku_b)
    assert [r["pipeline"]["ordered_open"] for r in b] == [60, 60, 60]
    assert b[0]["unfilled_purchase"] == P


def test_unallocated_two_shapes_share_one_core():
    """Task 7：批次级与 msku 级两种形状 —— 共同核心键一样，scope 区分，其余原样带上。"""
    from api.ui.grid_forecast import unallocated_json
    batch = {"sku": "S", "order_period": "2026-10", "arrive": "2026-11-15", "units": 200,
             "reason": "gap_unknown", "unknown": [["M", "1"]]}
    msku = {"sku": "S", "seller_sku": "M", "sid": "1", "arrive": "2026-11-15",
            "date": "2026-11-15", "units": 30, "order_period": "2026-10", "rule": "weight",
            "reason": "unknown_onhand"}
    a, b = unallocated_json(batch), unallocated_json(msku)
    core = {"sku", "order_period", "arrive", "units", "reason"}
    assert core <= set(a) and core <= set(b)
    assert (a["scope"], b["scope"]) == ("batch", "msku")
    assert a["unknown"] == [["M", "1"]] and (b["seller_sku"], b["sid"]) == ("M", "1")


def test_grid_forecast_logs_counts_and_elapsed(client, seed, lead_time_reset, scm_log):
    """Review Focus 5：几百个货号的整表 —— 耗时要有数、有日志。"""
    pid = s1(client, seed)
    grid(client, seed, pid)
    line = next(r.getMessage() for r in scm_log.records if "op=grid_forecast" in r.getMessage())
    assert f"plan_id={pid}" in line and "mskus=3" in line and "elapsed_ms=" in line
    assert "make_days=30 ship_days=60" in line


def test_failure_after_forecast_plan_is_logged_with_plan_id(client, seed, lead_time_reset,
                                                            scm_log, monkeypatch):
    """forecast_plan 之后的自检炸了（这里：货号行丢了）也要有 outcome=fail 那一行，不是裸 500。"""
    import dataclasses

    from api.ui import grid_forecast
    real = grid_forecast.forecast_plan
    monkeypatch.setattr(grid_forecast, "forecast_plan",
                        lambda inp: dataclasses.replace(real(inp), sku=[]))
    pid = s1(client, seed)
    r = client.get(f"/v1/plans/{pid}/grid", headers=H(seed.actor))
    assert r.status_code == 500
    line = next(r.getMessage() for r in scm_log.records
                if "op=grid_forecast" in r.getMessage() and "outcome=fail" in r.getMessage())
    assert f"plan_id={pid}" in line and "elapsed_ms=" in line
    assert "AssertionError" in line and "计划货号在推演里丢了" in line


def test_no_sales_source_store_forecast_is_not_applicable_and_left_out_of_the_sku_sum(
        client, seed, lead_time_reset):
    """★ 预测不适用 ≠ 未知：Walmart 店的预测不进货号 Σ、也不把它拖成未知；
    旧认领（system_basis 为 NULL，OP-8）仍是未知 —— 两者必须分得开。"""
    pid = s1(client, seed, extra=(seed.msku_nofba,))
    g = grid(client, seed, pid)
    w = [r for r in g["demand"] if (r["seller_sku"], r["sid"]) == tuple(seed.msku_nofba)]
    assert all(r["system_basis"]["reason"] == "not_applicable_no_sales_source" for r in w)
    wm = rows(g, "forecast_store", sid=seed.msku_nofba[1])
    assert [(r["system"], r["system_not_applicable"]) for r in wm] == [(None, True)] * 3
    d_sys = [r["system_units"] for r in g["demand"] if r["seller_sku"] == seed.msku_d[0]]
    assert None not in d_sys
    skub = rows(g, "forecast_sku", sku=seed.sku_b)
    assert [(r["system"], r["system_not_applicable"]) for r in skub] == [(u, False) for u in d_sys]

    with pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE plan_demand_cell SET system_basis = NULL"
                    " WHERE plan_id = %s AND seller_sku = %s AND sid = %s", (pid, *seed.msku_nofba))
    g = grid(client, seed, pid)
    wm = rows(g, "forecast_store", sid=seed.msku_nofba[1])
    assert [(r["system"], r["system_not_applicable"]) for r in wm] == [(None, False)] * 3
    assert [r["system"] for r in rows(g, "forecast_sku", sku=seed.sku_b)] == [None] * 3
