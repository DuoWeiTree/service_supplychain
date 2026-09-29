"""★ 判据⑥ 的后端一半：mock 与真 API 必须是**同一份数据**，不是两份长得像的。

两份各自维护的话，前端跑 mock 全绿、切 api 才发现字段名不一样 ——
而那时候界面已经照着 mock 写完了。
"""
import json
import os
from pathlib import Path

from shared.pg_client import pg_conn

FIXTURE = Path(__file__).parent / "fixtures" / "grid_response.json"


def _build(client, seed):
    pid = client.post("/v1/plans", json={"title": "10 月计划", "period_start": "2026-10-01",
                                         "months": 3},
                      headers={"x-actor": seed.actor}).json()["plan_id"]
    # ★ 刻意覆盖六种形态：人填 / 采用系统预估 / 该平台无 FBA / 在途无店铺归属 / 需求未知 /
    #   有 FBA 但没取到在仓数字（09-23 真实缺陷：不适用与未知在仓压成一个会在真实
    #   FBA 店上撒谎，seed.msku_e 独占一个 (sku, sid)，fba_onhand.csv 里刻意没有它）
    for ms in (seed.msku_a, seed.msku_c, seed.msku_d, seed.msku_nofba, seed.msku_b, seed.msku_e):
        client.post(f"/v1/plans/{pid}/claims", json={"seller_sku": ms[0], "sid": ms[1]},
                    headers={"x-actor": seed.actor})
    client.put(f"/v1/plans/{pid}/demand/{seed.msku_a[0]}/{seed.msku_a[1]}/2026-10",
               json={"expected_units": 130}, headers={"x-actor": seed.actor})
    client.put(f"/v1/plans/{pid}/purchase/{seed.sku_a}/2026-10",
               json={"planned_units": 500}, headers={"x-actor": seed.actor})
    # ★ MSKU-B 其实有真实销售历史（tests/fixtures/sales_history.csv 2026-07~09），
    #   claim 之后 system_units 会是真数字，不是「无历史」—— 单靠认领拿不到
    #   unknown_demand 那一格。仿 test_api_grid.py::
    #   test_the_two_reasons_behind_a_null_closing_are_distinguishable 的做法，
    #   显式清空它的 system_units，制造一个绑在 FBA 店、demand 真正未知的格子。
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE plan_demand_cell SET system_units = NULL, system_extrapolated = FALSE"
                    " WHERE plan_id = %s AND seller_sku = %s AND sid = %s",
                    (pid, seed.msku_b[0], seed.msku_b[1]))
        # ★ sku-image 计划 Task 3：这批货号里 sku_a 有真主图、sku_b/sku_e 在
        #   sku_category 里压根没有行 —— 两种 has_image 形态都要在这份 fixture
        #   里出现，否则前端（mock 数据源）永远画不出"有真图的行"那一种。
        cur.execute(
            "INSERT INTO sku_category (sku, category_id, category_path, pm_sku_matched,"
            " match_path, main_image_url, image_is_placeholder, refreshed_at)"
            " VALUES (%s, 'C1', '宠物用品/猫用品', %s, 'direct', 'https://x/real.jpg', false, now())",
            (seed.sku_a, seed.sku_a))
    body = client.get(f"/v1/plans/{pid}/grid", headers={"x-actor": seed.actor}).json()
    body["plan_id"] = 1        # ★ 固化前抹掉自增主键，否则 fixture 每轮都在变
    return body


def test_grid_fixture_matches_the_live_api(client, seed):
    live = _build(client, seed)
    if os.environ.get("UPDATE_GRID_FIXTURE"):
        # ★ 整份重写，不合并 —— 合并会把删掉的字段永远留在 fixture 里
        FIXTURE.write_text(json.dumps(live, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    saved = json.loads(FIXTURE.read_text("utf-8"))
    assert saved == live, ("grid 的形状变了。确认是有意改动后，用 "
                           "UPDATE_GRID_FIXTURE=1 重跑本测试重新固化，"
                           "并告诉前端这一版的差异。")


def test_the_fixture_covers_the_shapes_that_look_alike(client, seed):
    """★ fixture 若只有「正常」那一种形态，前端就永远不会画出另外几种 ——
    而那几种恰恰是「看起来像 0」的那些。"""
    saved = json.loads(FIXTURE.read_text("utf-8"))
    # ★ 四选四：任何一种形态的种子路径被删掉，这条测试都必须红，
    #   不能只守住其中一部分（子集断言会让「缺一种」蒙混过关）。
    assert {r["basis"] for r in saved["demand"]} == {"human", "system", "unknown"}
    # ★ spec §3.3：速率模型对任何月份都算得出 → 不再产生外推；标记列保留、由算法如实给 false
    assert not any(r["system_extrapolated"] for r in saved["demand"])
    a = [r for r in saved["forecast"] if r["seller_sku"] == "MSKU-A"]
    assert a[0]["basis"]["bridge"]["units"] == 39 and a[0]["opening"] == 261
    assert any(r["state"] == "ok" and r["closing"] is not None for r in saved["forecast"])
    assert any(r["reason"] == "unknown_demand" for r in saved["forecast"]), "缺「期望未知」"
    # ★ 阶段 A 的 inventory[]/sku_pipeline[] 已退役（spec §6）；四种「看起来像 0」的
    #   形态改从 forecast_store 取，等价事实原样保留：
    fs = saved["forecast_store"]
    # ★ 四选四：任何一种形态的种子路径被删掉，这条测试都必须红 ——
    #   子集断言会让「缺一种」蒙混过关，所以用恰好相等而不是 any()
    assert {(r["state"], r["reason"]) for r in fs} == {
        ("ok", None), ("not_applicable", "not_applicable"),
        ("unknown", "unknown_demand"), ("unknown", "unknown_onhand")}, \
        "四种店铺行状态（有数 / 该平台无 FBA / 需求未知 / 有 FBA 但未知在仓）必须齐全"
    # ★ 09-23 真实缺陷：不适用与未知在仓都会让 opening/closing 是 null，但必须分得开 ——
    #   压成一个就是负责人在真实数据上撞见的 na_disagrees_with_seller 坍缩
    assert not any(r["state"] == "not_applicable" and r["reason"] == "unknown_onhand" for r in fs)
    assert any(r["state"] == "ok" and r["closing"] is not None for r in fs), "缺「有数」那一种"
    # ★ 在途恒无店铺归属：forecast_store 的 inbound_planned 不因为货号已有在途而非零 ——
    #   总量只在货号层 forecast_sku 的 pipeline.ordered_open 里
    assert all(r["inbound_planned"] == 0 for r in fs if r["inbound_planned"] is not None)
    assert any(r["pipeline"]["ordered_open"] for r in saved["forecast_sku"]), \
        "缺「有在途但没归属」那一种 —— 它与「没有在途」必须长得不一样"
    # ★ spec §6 的预测块必须随 fixture 出去，否则前端永远在画一份没有它们的 mock
    # ★ forecast_msku 也在内：buildGridModel 无守卫地 .map 它，缺了整页崩而没有测试会红
    for key in ("params", "forecast", "forecast_msku", "forecast_store", "forecast_sku",
                "beyond_horizon", "unallocated"):
        assert key in saved, f"fixture 缺 {key}"
    assert any(r["state"] == "not_applicable" for r in saved["forecast_store"]), "缺「不适用」"
    # ★ 预测不适用（没有销量取数源）与 FBA 不适用是两件事，各要一个靶子
    assert any(r["system_not_applicable"] for r in saved["forecast_store"]), "缺「预测不适用」"
    # ★ sku-image 计划 Task 3：has_image 两态都要有靶子——只有 true 或只有 false
    #   前端就永远画不出另一种（按需预览的核心分支：出不出图片入口）。
    assert any(r["has_image"] for r in saved["forecast_sku"]), "缺「有真图」那一种"
    assert any(not r["has_image"] for r in saved["forecast_sku"]), "缺「没有图」那一种"
    assert any((r["system_basis"] or {}).get("reason") == "not_applicable_no_sales_source"
               for r in saved["demand"]), "缺「认领时记下的无销量源」"
    assert any(r["reason"] == "unknown_onhand" for r in saved["forecast"]), "缺「未知在仓」"
    assert saved["beyond_horizon"], "缺「周期外到货」—— 默认 30/60 下 10 月的 500 件必然在周期外"
