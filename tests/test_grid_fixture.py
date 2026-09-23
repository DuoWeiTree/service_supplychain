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
    # ★ 刻意覆盖五种形态：人填 / 采用系统预估 / 该平台无 FBA / 在途无店铺归属 / 需求未知
    for ms in (seed.msku_a, seed.msku_c, seed.msku_d, seed.msku_nofba, seed.msku_b):
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
    # ★ 三选三：任何一种形态的种子路径被删掉，这条测试都必须红，
    #   不能只守住其中一部分（子集断言会让「缺一种」蒙混过关）。
    assert {r["basis"] for r in saved["demand"]} == {"human", "system", "unknown"}
    assert any(r["system_extrapolated"] for r in saved["demand"]), "缺「外推」那一种"
    inv = saved["inventory"]
    assert {r["basis"]["closing_reason"] for r in inv} == {
        None, "not_applicable", "unknown_demand"}, \
        "三种 closing_reason（有数 / 该平台无 FBA / 需求未知）必须齐全"
    # ★ 在途恒无店铺归属：每一格都这么标，而总量只在 sku_level_in_transit / sku_pipeline 里
    assert all(r["inbound"] is None for r in inv)
    assert all(r["basis"]["reason"] == "no_seller_attribution" for r in inv)
    assert any(r["basis"]["sku_level_in_transit"] for r in inv), \
        "缺「有在途但没归属」那一种 —— 它与「没有在途」必须长得不一样"
    assert any(r["closing"] is not None for r in inv), "期末不该因为在途没归属而全是 null"
    assert all(r["basis"]["includes_plan_purchase"] is False for r in inv)
    assert saved["sku_pipeline"] and all(r["no_seller_attribution"] is True
                                         for r in saved["sku_pipeline"])
