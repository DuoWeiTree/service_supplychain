"""装配：只有真 CH（PB-D17）· 旧开关启动即拒 · 惰性建连 · 取不到数一律 503。"""
from __future__ import annotations

import datetime as dt
import tomllib
from pathlib import Path

import conftest as ct
import pytest
from fixture_source import FIXTURES, FixtureSource

from api.common import source_factory as sf
from dim import ch_source as cs
from dim.order_store_map import UnknownStore
from dim.source import InTransit, StockDays
from shared import config as config_module

ROOT = Path(__file__).resolve().parents[1]


def test_forecast_config_has_no_source_switch():
    """PB-D17：预测只读真 CH。默认值里没有这个键，示例配置里也不再教人写它。"""
    assert "source" not in config_module._FORECAST_DEFAULTS
    example = tomllib.loads((ROOT / "config.example.toml").read_text("utf-8"))
    assert "source" not in example.get("forecast", {}), "config.example.toml 还在教人写 [forecast] source"


def test_make_source_only_builds_the_real_ch_source(monkeypatch):
    """★ 走生产那个 make_source（conftest 的 autouse 夹具把模块上的那个换成了 CSV 替身）。"""
    monkeypatch.setattr(sf, "forecast", lambda: dict(config_module._FORECAST_DEFAULTS))
    assert isinstance(ct.REAL_MAKE_SOURCE(), cs.ChSource)


def test_offline_tests_get_the_csv_double_not_ch():
    """★ 2026-09-28：本机 config.toml 指向真 CH 时，23 条离线测试打了生产 ClickHouse。
    钉子删掉之后，挡住它的是 conftest 的 autouse 注入 —— 这条验注入本身在场。"""
    assert isinstance(sf.make_source(), FixtureSource)
    assert sf.make_source is not ct.REAL_MAKE_SOURCE


@pytest.mark.parametrize("value", ["fixture", "ch", ""])
def test_leftover_forecast_source_refuses_to_start(monkeypatch, value):
    """旧 config.toml 残留 [forecast] source（任何值）→ 拒启动：留着 fixture 的人以为自己在看假数据。"""
    from api import check_forecast_config
    monkeypatch.setattr(config_module, "forecast",
                        lambda: {**config_module._FORECAST_DEFAULTS, "source": value})
    with pytest.raises(RuntimeError, match=r"\[forecast\] source 已删除（PB-D17"):
        check_forecast_config()


def test_leftover_forecast_source_refuses_at_startup_not_at_the_first_request(wipe, monkeypatch):
    """★ 配置类错误在启动钩子炸（全局规范第 4 条），不是每个请求各报一次 500。"""
    from starlette.testclient import TestClient

    import api

    monkeypatch.setattr(config_module, "forecast",
                        lambda: {**config_module._FORECAST_DEFAULTS, "source": "ch"})
    with pytest.raises(RuntimeError, match="PB-D17"), TestClient(api.create_app()):
        pass


def test_without_the_source_key_the_app_starts(wipe, monkeypatch):
    from starlette.testclient import TestClient

    import api

    monkeypatch.setattr(config_module, "forecast", lambda: dict(config_module._FORECAST_DEFAULTS))
    with TestClient(api.create_app()) as c:
        assert c.get("/health").status_code == 200


def test_ch_source_does_not_connect_at_construction(monkeypatch):
    """★ import 时建连会让「配置写错」在启动前一秒才炸，而那时日志已经刷过去了。"""
    built = []
    monkeypatch.setattr(sf, "forecast", lambda: {"cache_ttl_seconds": 300, "drop_threshold": 0.30})
    monkeypatch.setattr(sf, "ch_client", lambda: built.append(1) or object())
    monkeypatch.setattr(sf, "ch_query", lambda c: (lambda sql, parameters=None: []))
    src = ct.REAL_MAKE_SOURCE()
    assert built == [], "构造时就连了 CH"
    # ★ 查询桩给的空行 rows=[] 会被 pick_snapshot_date 判成「近 7 天一个采集日都没有」，
    #   抛具体的 UnknownShape——这里只关心「真的打了一次查询」这件事，不断言异常细节。
    with pytest.raises(cs.UnknownShape):
        src.as_of()
    assert built == [1], "第一次真查询时才该建连"


def test_the_source_config_is_logged_once_at_startup(wipe, monkeypatch, scm_log):
    """★ 每请求装配不许每请求打一行 —— 那会把访问日志淹掉，而这行的用处恰恰是
    「启动日志第一屏可见」。同时它要说清生命周期，不然下一个人会以为还有缓存。"""
    from starlette.testclient import TestClient

    import api

    monkeypatch.setattr(sf, "forecast", lambda: {"cache_ttl_seconds": 300})
    with TestClient(api.create_app()) as c:
        c.get("/health")
    lines = [r for r in scm_log.text.splitlines() if "op=forecast_source" in r]
    assert len(lines) == 1, f"op=forecast_source 打了 {len(lines)} 行：{lines}"
    assert "kind=ch" in lines[0] and "lifecycle=per_request" in lines[0], lines[0]


def test_ch_source_injects_the_real_classify_failure(monkeypatch):
    """★★ 控制器验收判据第一条：漏注 classify_failure 不报错、测试也照样绿，
    但会让生产每一次 CH 抖动都报 kind='unknown'，废掉「超时」与「连不上」
    的区分。钉住 make_source 真的把 shared.ch_client.describe_failure 传给
    了 ChSource —— 不看内部属性，走行为：一次真实的 TimeoutError 必须被
    分类成 'timeout'，而不是默认占位分类器给的 'unknown'。"""
    monkeypatch.setattr(sf, "forecast", dict)
    monkeypatch.setattr(sf, "ch_client", lambda: object())

    def boom(sql: str, parameters: dict | None = None) -> list[tuple]:
        raise TimeoutError("connection timed out")

    monkeypatch.setattr(sf, "ch_query", lambda c: boom)
    src = ct.REAL_MAKE_SOURCE()
    with pytest.raises(cs.ChUnavailable) as ei:
        src.as_of()
    assert ei.value.cause["kind"] == "timeout", (
        f"kind={ei.value.cause['kind']!r} —— 应为 'timeout'。若是 'unknown'，说明"
        " make_source() 没把 shared.ch_client.describe_failure 真正传给 ChSource"
        "（用了默认占位分类器），生产环境的每次 CH 抖动都会报不出原因")


def test_ch_unavailable_becomes_503_naming_the_kind(client, seed, use_source):
    """★ 与 mirror_stale 同一条理由：拿不到数算出来的曲线看起来完全正常。
    调度器必须能区分「旧」「无」「不适用」。"""

    class Dead:
        def as_of(self):
            raise cs.ChUnavailable("192.168.66.211:8123/jxd_raw",
                                   {"kind": "connect_refused", "code": 111})
        # spec §3.1：预测换成 base_rate × 天数，claim 取日销（此处只走 grid，不影响）
        def daily_sales(self, *a):
            raise cs.ChUnavailable("x", {"kind": "timeout"})
        def onhand_available(self, *a):
            raise cs.ChUnavailable("x", {"kind": "timeout"})
        def purchase_in_transit(self, *a):
            raise cs.ChUnavailable("x", {"kind": "timeout"})

    use_source(Dead)
    plan = client.post("/v1/plans", json={"title": "t", "period_start": "2026-10-01",
                                          "months": 3}, headers={"x-actor": seed.actor}).json()
    r = client.get(f"/v1/plans/{plan['plan_id']}/grid")
    assert r.status_code == 503
    body = r.json()
    assert body["error"] == "forecast_source_unavailable"
    assert body["kind"] == "connect_refused", "只说「失败了」等于没说 —— 超时与连不上处置相反"
    assert body["target"] == "192.168.66.211:8123/jxd_raw"


def test_claim_returns_the_history_window(client, seed, use_source):
    """★ 没有标记的数比没有数更坏：用了哪几个月、哪些是补 0，界面要说得出来。"""
    class Src(FixtureSource):
        def history_window(self, seller_sku, sid):
            return {"store": "PETSFIT_NORTH_AMERICA", "asked_about": [seller_sku, sid], "sales_channel": "Amazon.com",
                    "months": ["2026-07-01", "2026-08-01"], "zero_filled": ["2026-07-01"],
                    "newest_month": "2026-08-01", "newest_month_age_days": 23,
                    "lag_note": "orders_are_lagging_collected; deduped by order_item"}

    use_source(lambda: Src(FIXTURES))
    plan = client.post("/v1/plans", json={"title": "t", "period_start": "2026-10-01",
                                          "months": 2}, headers={"x-actor": seed.actor}).json()
    r = client.post(f"/v1/plans/{plan['plan_id']}/claims",
                    json={"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]},
                    headers={"x-actor": seed.actor})
    assert r.status_code == 200
    assert r.json()["history_window"]["zero_filled"] == ["2026-07-01"]


def test_unknown_store_becomes_503_naming_it(client, seed, use_source):
    """★ fix round 1（阻塞项）：design §8 OQ-3 点名的真实缺口 —— 一个 sid 在
    dim/order_store_map.py::STORE_SID 里没有对应的 (store, sales_channel) 时，
    daily_sales() 经 order_store_map.store_for() 抛 UnknownStore。
    这条异常曾经漏在 claim() 的 except 元组外，裸成没有 S-29 形状的 500 ——
    复核在 TestClient 上实测复现过。补上后必须落成与 ChUnavailable 同一家族的
    503 forecast_source_unusable，detail 里带着 store_for() 唯一已知的那个量
    （sid），运维据此去 STORE_SID 补一行。"""
    class Src(FixtureSource):
        # spec §3.1：预测换成 base_rate × 天数，store_for() 现在从 daily_sales() 抛
        def daily_sales(self, *a):
            raise UnknownStore(
                "sid='11072' 在订单报表里没有对应的 store —— "
                "这个店的销量取不到，不能当成「卖了 0 件」")

    use_source(lambda: Src(FIXTURES))
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


def test_grid_wires_dropped_stats_into_source_notes(client, seed, use_source):
    """★ fix round 1（must #3）：source_notes.dropped 在 tracked 套件里只被
    CSV 替身路径断言过恒为 {}（FixtureSource 没有 stats()）——CH 路径下真正
    非空的取值此前零覆盖。钉住 SOURCE.stats() 原样透传进响应，不是被悄悄
    改写或丢弃。"""

    class ChStub:
        def as_of(self):
            return dt.date(2026, 9, 21)

        # spec §3.1：预测换成 base_rate × 天数 —— 空日销 → 没 rate，claim() 照旧建格
        def daily_sales(self, *a):
            return {}

        def fba_stock_days(self, *a):
            return StockDays(frozenset(), frozenset(), ())

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

    use_source(ChStub)
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


def test_grid_wires_purchase_as_of_into_source_notes(client, seed, use_source):
    """★ Task 21：sku_pipeline[].bucket（'overdue'/'future'，靠 is_overdue() 算）随
    sku_pipeline[] 一起退役 —— 阶段 A 的「逾期 / 未来」分桶在新的预测块（forecast_sku
    的 pipeline.ordered_open）里没有等价物，OQ-6 的判断已经没有消费者。
    purchase_as_of() 本身没有退役：它还是 OQ-5 陈旧闸（PurchaseTableStale 的判据），
    只是现在唯一的出口是 source_notes.purchase_as_of —— CSV 替身路径下这条此前零
    覆盖（FixtureSource 没有 purchase_as_of() 方法，恒 None），这里钉住 CH 路径下
    真的把 SOURCE.purchase_as_of() 原样传进了响应。"""

    class ChStub:
        def as_of(self):
            return dt.date(2026, 9, 21)

        # spec §3.1：预测换成 base_rate × 天数 —— 空日销 → 没 rate，claim() 照旧建格
        def daily_sales(self, *a):
            return {}

        def fba_stock_days(self, *a):
            return StockDays(frozenset(), frozenset(), ())

        def onhand_available(self, *a):
            return None

        def purchase_in_transit(self, sku):
            return [InTransit("2026-07", 40, "PO-OLD"), InTransit("2026-11", 25, "PO-FUTURE")]

        def purchase_as_of(self):
            return dt.date(2026, 9, 20)

        def stats(self):
            return {}

    use_source(ChStub)
    plan = client.post("/v1/plans", json={"title": "t", "period_start": "2026-10-01",
                                          "months": 2}, headers={"x-actor": seed.actor}).json()
    client.post(f"/v1/plans/{plan['plan_id']}/claims",
               json={"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]},
               headers={"x-actor": seed.actor})
    r = client.get(f"/v1/plans/{plan['plan_id']}/grid", headers={"x-actor": seed.actor})
    assert r.status_code == 200
    assert r.json()["source_notes"]["purchase_as_of"] == "2026-09-20"
    # ★ 终审 I-2：逾期 / 未来的分桶换到预测块里逐笔带 —— 预计到货月早于采购快照月即逾期
    #   （is_overdue，OQ-6），period 原样（不改日期），货号行另给逾期合计
    body = r.json()
    assert body["params"]["in_transit_as_of"] == "2026-09-20"
    sku_row = next(s for s in body["forecast_sku"] if s["sku"] == seed.sku_a)
    assert sku_row["pipeline"]["ordered_open_sources"] == [
        {"period": "2026-07", "units": 40, "ref": "PO-OLD", "overdue": True},
        {"period": "2026-11", "units": 25, "ref": "PO-FUTURE", "overdue": False}]
    assert (sku_row["pipeline"]["ordered_open"],
            sku_row["pipeline"]["ordered_open_overdue"]) == (65, 40)
