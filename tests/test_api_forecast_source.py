"""装配：配置开关 · 惰性建连 · 取不到数一律 503。"""
from __future__ import annotations

import datetime as dt

import pytest

from api.ui import source_factory as sf
from dim import ch_source as cs
from dim.fixture_source import FixtureSource
from dim.order_store_map import UnknownStore
from dim.source import InTransit


def test_default_source_is_fixture(monkeypatch):
    """★ 默认值必须是离线跑得通的那个 —— 漏配不该变成「测试去连生产 CH」。"""
    monkeypatch.setattr(sf, "forecast", lambda: {"source": "fixture"})
    assert isinstance(sf.make_source(), FixtureSource)


def test_ch_source_does_not_connect_at_construction(monkeypatch):
    """★ import 时建连会让「配置写错」在启动前一秒才炸，而那时日志已经刷过去了。"""
    built = []
    monkeypatch.setattr(sf, "forecast", lambda: {"source": "ch", "cache_ttl_seconds": 300,
                                                  "drop_threshold": 0.30})
    monkeypatch.setattr(sf, "ch_client", lambda: built.append(1) or object())
    monkeypatch.setattr(sf, "ch_query", lambda c: (lambda sql: []))
    src = sf.make_source()
    assert built == [], "构造时就连了 CH"
    # ★ 查询桩给的空行 rows=[] 会被 pick_snapshot_date 判成「近 7 天一个采集日都没有」，
    #   抛具体的 UnknownShape——这里只关心「真的打了一次查询」这件事，不断言异常细节。
    with pytest.raises(cs.UnknownShape):
        src.as_of()
    assert built == [1], "第一次真查询时才该建连"


def test_unknown_source_value_hard_fails(monkeypatch):
    monkeypatch.setattr(sf, "forecast", lambda: {"source": "excel"})
    with pytest.raises(ValueError, match="excel"):
        sf.make_source()


def test_ch_source_injects_the_real_classify_failure(monkeypatch):
    """★★ 控制器验收判据第一条：漏注 classify_failure 不报错、测试也照样绿，
    但会让生产每一次 CH 抖动都报 kind='unknown'，废掉「超时」与「连不上」
    的区分。钉住 make_source 真的把 shared.ch_client.describe_failure 传给
    了 ChSource —— 不看内部属性，走行为：一次真实的 TimeoutError 必须被
    分类成 'timeout'，而不是默认占位分类器给的 'unknown'。"""
    monkeypatch.setattr(sf, "forecast", lambda: {"source": "ch"})
    monkeypatch.setattr(sf, "ch_client", lambda: object())

    def boom(sql: str) -> list[tuple]:
        raise TimeoutError("connection timed out")

    monkeypatch.setattr(sf, "ch_query", lambda c: boom)
    src = sf.make_source()
    with pytest.raises(cs.ChUnavailable) as ei:
        src.as_of()
    assert ei.value.cause["kind"] == "timeout", (
        f"kind={ei.value.cause['kind']!r} —— 应为 'timeout'。若是 'unknown'，说明"
        " make_source() 没把 shared.ch_client.describe_failure 真正传给 ChSource"
        "（用了默认占位分类器），生产环境的每次 CH 抖动都会报不出原因")


def test_ch_unavailable_becomes_503_naming_the_kind(client, seed, monkeypatch):
    """★ 与 mirror_stale 同一条理由：拿不到数算出来的曲线看起来完全正常。
    调度器必须能区分「旧」「无」「不适用」。"""
    from api.ui import plans

    class Dead:
        def as_of(self):
            raise cs.ChUnavailable("192.168.66.211:8123/jxd_raw",
                                   {"kind": "connect_refused", "code": 111})
        def monthly_sales_history(self, *a):
            raise cs.ChUnavailable("x", {"kind": "timeout"})
        def onhand_available(self, *a):
            raise cs.ChUnavailable("x", {"kind": "timeout"})
        def purchase_in_transit(self, *a):
            raise cs.ChUnavailable("x", {"kind": "timeout"})

    monkeypatch.setattr(plans, "SOURCE", Dead())
    plan = client.post("/v1/plans", json={"title": "t", "period_start": "2026-10-01",
                                          "months": 3}, headers={"x-actor": seed.actor}).json()
    r = client.get(f"/v1/plans/{plan['plan_id']}/grid")
    assert r.status_code == 503
    body = r.json()
    assert body["error"] == "forecast_source_unavailable"
    assert body["kind"] == "connect_refused", "只说「失败了」等于没说 —— 超时与连不上处置相反"
    assert body["target"] == "192.168.66.211:8123/jxd_raw"


def test_claim_returns_the_history_window(client, seed, monkeypatch):
    """★ 没有标记的数比没有数更坏：用了哪几个月、哪些是补 0，界面要说得出来。"""
    from api.ui import plans

    class Src(FixtureSource):
        def history_window(self):
            return {"store": "PETSFIT_NORTH_AMERICA", "sales_channel": "Amazon.com",
                    "months": ["2026-07-01", "2026-08-01"], "zero_filled": ["2026-07-01"],
                    "newest_month": "2026-08-01", "newest_month_age_days": 23,
                    "lag_note": "orders_are_lagging_collected; deduped by order_item"}

    monkeypatch.setattr(plans, "SOURCE", Src(plans.FIXTURES))
    plan = client.post("/v1/plans", json={"title": "t", "period_start": "2026-10-01",
                                          "months": 2}, headers={"x-actor": seed.actor}).json()
    r = client.post(f"/v1/plans/{plan['plan_id']}/claims",
                    json={"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]},
                    headers={"x-actor": seed.actor})
    assert r.status_code == 200
    assert r.json()["history_window"]["zero_filled"] == ["2026-07-01"]


def test_unknown_store_becomes_503_naming_it(client, seed, monkeypatch):
    """★ fix round 1（阻塞项）：design §8 OQ-3 点名的真实缺口 —— 一个 sid 在
    dim/order_store_map.py::STORE_SID 里没有对应的 (store, sales_channel) 时，
    monthly_sales_history() 经 order_store_map.store_for() 抛 UnknownStore。
    这条异常曾经漏在 claim() 的 except 元组外，裸成没有 S-29 形状的 500 ——
    复核在 TestClient 上实测复现过。补上后必须落成与 ChUnavailable 同一家族的
    503 forecast_source_unusable，detail 里带着 store_for() 唯一已知的那个量
    （sid），运维据此去 STORE_SID 补一行。"""
    from api.ui import plans

    class Src(FixtureSource):
        def monthly_sales_history(self, *a):
            raise UnknownStore(
                "sid='11072' 在订单报表里没有对应的 store —— "
                "这个店的销量取不到，不能当成「卖了 0 件」")

    monkeypatch.setattr(plans, "SOURCE", Src(plans.FIXTURES))
    plan = client.post("/v1/plans", json={"title": "t", "period_start": "2026-10-01",
                                          "months": 3}, headers={"x-actor": seed.actor}).json()
    r = client.post(f"/v1/plans/{plan['plan_id']}/claims",
                    json={"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]},
                    headers={"x-actor": seed.actor})
    assert r.status_code == 503
    body = r.json()
    assert body["error"] == "forecast_source_unusable"
    assert "11072" in body["detail"], (
        "detail 必须点名认不出的 sid —— 没点名，运维不知道去 STORE_SID 里补哪一行")


def test_grid_wires_dropped_stats_into_source_notes(client, seed, monkeypatch):
    """★ fix round 1（must #3）：source_notes.dropped 在 tracked 套件里只被
    fixture 路径断言过恒为 {}（FixtureSource 没有 stats()）——CH 路径下真正
    非空的取值此前零覆盖。钉住 SOURCE.stats() 原样透传进响应，不是被悄悄
    改写或丢弃。"""
    from api.ui import plans

    class ChStub:
        def as_of(self):
            return dt.date(2026, 9, 21)

        def monthly_sales_history(self, *a):
            return []                      # → InsufficientHistory，claim() 照旧建格

        def onhand_available(self, *a):
            return None

        def purchase_in_transit(self, sku):
            return []

        def purchase_as_of(self):
            return dt.date(2026, 9, 20)

        def stats(self):
            # ★ 键名与取值形状照抄 design §1.3 E-4/parse_onhand 的真实产出——
            #   不是随手编的字典。
            return {"onhand": {"shared_pool_excluded": {"rows": 1114, "units": 14073}}}

    monkeypatch.setattr(plans, "SOURCE", ChStub())
    plan = client.post("/v1/plans", json={"title": "t", "period_start": "2026-10-01",
                                          "months": 2}, headers={"x-actor": seed.actor}).json()
    client.post(f"/v1/plans/{plan['plan_id']}/claims",
               json={"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]},
               headers={"x-actor": seed.actor})
    r = client.get(f"/v1/plans/{plan['plan_id']}/grid", headers={"x-actor": seed.actor})
    assert r.status_code == 200
    assert r.json()["source_notes"]["dropped"] == {
        "onhand": {"shared_pool_excluded": {"rows": 1114, "units": 14073}}}, (
        "source_notes.dropped 必须原样透传 SOURCE.stats()，不是猜的默认值 {}")


def test_grid_computes_bucket_from_purchase_as_of(client, seed, monkeypatch):
    """★ fix round 1（must #3）：sku_pipeline[].bucket 的 'overdue'/'future' 取值
    在 tracked 套件里此前零覆盖 —— fixture 路径下 purchase_as_of 恒 None、
    bucket 因此恒 null，从没有一条测试验过「传对了 purchase_as_of、算出了对的
    bucket 值」这一步（OQ-6 存在的意义正是这个判断）。"""
    from api.ui import plans

    class ChStub:
        def as_of(self):
            return dt.date(2026, 9, 21)

        def monthly_sales_history(self, *a):
            return []

        def onhand_available(self, *a):
            return None

        def purchase_in_transit(self, sku):
            # ★ purchase_as_of 所在月是 2026-09：07 月早于它（逾期），11 月晚于它（未来）。
            return [InTransit("2026-07", 40, "PO-OLD"), InTransit("2026-11", 25, "PO-FUTURE")]

        def purchase_as_of(self):
            return dt.date(2026, 9, 20)

        def stats(self):
            return {}

    monkeypatch.setattr(plans, "SOURCE", ChStub())
    plan = client.post("/v1/plans", json={"title": "t", "period_start": "2026-10-01",
                                          "months": 2}, headers={"x-actor": seed.actor}).json()
    client.post(f"/v1/plans/{plan['plan_id']}/claims",
               json={"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]},
               headers={"x-actor": seed.actor})
    r = client.get(f"/v1/plans/{plan['plan_id']}/grid", headers={"x-actor": seed.actor})
    assert r.status_code == 200
    bucket_by_period = {row["period"]: row["bucket"] for row in r.json()["sku_pipeline"]}
    assert bucket_by_period["2026-07"] == "overdue", \
        "2026-07 早于 purchase_as_of(2026-09) 所在月，必须是 overdue"
    assert bucket_by_period["2026-11"] == "future", \
        "2026-11 晚于 purchase_as_of(2026-09) 所在月，必须是 future"
