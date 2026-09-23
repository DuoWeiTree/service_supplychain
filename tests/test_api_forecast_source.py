"""装配：配置开关 · 惰性建连 · 取不到数一律 503。"""
from __future__ import annotations

import pytest

from api.ui import source_factory as sf
from dim import ch_source as cs
from dim.fixture_source import FixtureSource


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
