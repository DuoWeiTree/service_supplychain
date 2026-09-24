"""spec §3.3 / §3.4：认领时用 base_rate 种预测销量，依据随数一起存。
fixture：as_of 2026-09-21 → 估计锚点 09-14；MSKU-A 每天 4 件（OP-3：4.325/天），MSKU-C 每天 2 件、09-01~07 断货。"""
import datetime as dt

import pytest

from shared.pg_client import pg_conn


def H(a):
    return {"x-actor": a}


def claim(client, seed, ms):
    pid = client.post("/v1/plans", json={"title": "t", "period_start": "2026-10-01", "months": 3},
                      headers=H(seed.actor)).json()["plan_id"]
    r = client.post(f"/v1/plans/{pid}/claims", json={"seller_sku": ms[0], "sid": ms[1]},
                    headers=H(seed.actor))
    assert r.status_code == 200, r.text
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT system_units, system_extrapolated, system_basis FROM plan_demand_cell"
                    " WHERE plan_id = %s ORDER BY period_start", (pid,))
        return r.json(), cur.fetchall()


def test_claim_seeds_rate_times_days_rounded_up(client, seed):
    body, rows = claim(client, seed, seed.msku_a)
    assert [r[0] for r in rows] == [135, 130, 135]          # ceil(4.3251 × 31/30/31)
    assert [r[1] for r in rows] == [False, False, False]    # ★ spec §3.3：算法如实给 false
    b = rows[0][2]
    assert b["method"] == "baseline_rate_v1"
    assert (b["as_of"], b["estimation_anchor"], b["embargo_days"]) == ("2026-09-21", "2026-09-14", 7)
    assert b["base_rate"] == pytest.approx(4.325139122267326, rel=1e-12)
    assert b["window"] == {"from": "2026-07-21", "until": "2026-09-14"}
    assert b["target_window"] == {"from": "2026-06-17", "until": "2026-09-14"}
    assert (b["days_in_month"], b["censored_days"]) == (31, 0)
    assert b["units_raw"] == pytest.approx(4.325139122267326 * 31)
    assert rows[1][2]["days_in_month"] == 30
    assert (b["target_units"], b["target_days"]) == (360, 56)      # target_rate = 360 ÷ 56
    assert b["constants"]["winsor_min_nonzero"] == 5
    assert body["no_history"] == []


def test_stockout_days_are_censored_and_counted(client, seed):
    _, rows = claim(client, seed, seed.msku_c)
    assert [r[0] for r in rows] == [67, 65, 67]
    assert rows[0][2]["censored_days"] == 7
    assert rows[0][2]["base_rate"] == pytest.approx(2.15964558760763, rel=1e-12)


def test_no_history_is_null_named_and_explained(client, seed, use_source):
    from api.ui import plans
    from dim.fixture_source import FixtureSource

    class NoSales(FixtureSource):
        def daily_sales(self, *a):
            return {}

    use_source(lambda: NoSales(plans.FIXTURES))
    body, rows = claim(client, seed, seed.msku_a)
    assert [r[0] for r in rows] == [None, None, None]
    assert rows[0][2]["reason"] == "no_sales_in_window" and rows[0][2]["base_rate"] is None
    assert body["no_history"] == [{"seller_sku": "MSKU-A", "sid": "11072",
                                   "reason": "no_sales_history"}]


def test_grid_demand_rows_carry_the_basis_and_params_carry_embargo(client, seed):
    claim(client, seed, seed.msku_a)
    pid = client.get("/v1/plans").json()["plans"][0]["plan_id"]
    g = client.get(f"/v1/plans/{pid}/grid").json()
    assert g["demand"][0]["system_basis"]["days_in_month"] == 31
    assert g["params"]["embargo_days"] == 7
    a = [r for r in g["forecast"] if r["seller_sku"] == "MSKU-A"]
    assert a[0]["basis"]["bridge"]["units"] == 39      # 9 月 ceil(4.3251×30)=130，× 0.3
    assert a[0]["opening"] == 261


def test_stock_days_are_never_asked_past_the_estimation_anchor(client, seed, use_source,
                                                                monkeypatch, scm_log):
    """★ task-14 交代：SQL_FBA_STOCK_DAYS 不看 settled —— 还在写的采集日会被读成断货。
    只有窗口止于 as_of − embargo_days 才安全，所以调用方必须守住，窗口一越线就点名炸。"""
    from api.ui import plans
    from dim.fixture_source import FixtureSource
    from forecast.baseline import DataWindows

    asked = []

    class Spy(FixtureSource):
        def fba_stock_days(self, seller_sku, sid, start, end):
            asked.append((start, end))
            return super().fba_stock_days(seller_sku, sid, start, end)

    use_source(lambda: Spy(plans.FIXTURES))
    claim(client, seed, seed.msku_a)
    assert asked == [(dt.date(2026, 7, 21), dt.date(2026, 9, 14))]   # 止于锚点 09-14

    # 窗口若被改成越过锚点一天（as_of 09-21 − 7 = 09-14，这里给 09-15）→ 必须拒绝
    def past_anchor(as_of, embargo_days=plans.EMBARGO_DAYS):
        a = as_of - dt.timedelta(days=embargo_days)
        return DataWindows(a, a - dt.timedelta(days=89), a - dt.timedelta(days=55),
                           a + dt.timedelta(days=1))

    monkeypatch.setattr(plans, "data_windows", past_anchor)
    asked.clear()
    pid = client.post("/v1/plans", json={"title": "t", "period_start": "2026-10-01", "months": 3},
                      headers=H(seed.actor)).json()["plan_id"]
    r = client.post(f"/v1/plans/{pid}/claims", json={"seller_sku": "MSKU-B", "sid": "11072"},
                    headers=H(seed.actor))
    assert r.status_code == 500
    assert r.json() == {"error": "stock_window_past_anchor", "hint": r.json()["hint"],
                        "until": "2026-09-15", "anchor": "2026-09-14", "as_of": "2026-09-21",
                        "embargo_days": 7}
    assert asked == [], "越线的窗口一次都不许问到取数源"
    # ★ 炸了要能光看日志定位：点名是哪个守卫、越到了哪一天
    assert "outcome=StockWindowPastAnchor" in scm_log.text
    assert "2026-09-15" in scm_log.text and "2026-09-14" in scm_log.text
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM plan_demand_cell WHERE plan_id = %s", (pid,))
        assert cur.fetchone()[0] == 0, "越线就整笔回滚，不许留半截格子"


def test_extrapolated_flag_is_what_the_estimator_says(client, seed, use_source, monkeypatch):
    """★ spec §3.3：system_extrapolated 由算法如实给，不是 claim() 盖章 ——
    估计器说 True，格子就得是 True（现行 baseline_rate_v1 恒给 False，见上面第一条）。"""
    import dataclasses

    from api.ui import plans

    real = plans.estimate_rate
    monkeypatch.setattr(plans, "estimate_rate",
                        lambda *a, **k: dataclasses.replace(real(*a, **k), extrapolated=True))
    _, rows = claim(client, seed, seed.msku_a)
    assert [r[1] for r in rows] == [True, True, True]
    assert [r[0] for r in rows] == [135, 130, 135]


def test_no_rate_is_never_marked_extrapolated(client, seed, use_source, monkeypatch):
    """★ 没有数就没有外推（库层 CHECK：外推 ⇒ system_units 非空）—— 估计器说 True 也不许写上去。"""
    import dataclasses

    from api.ui import plans
    from dim.fixture_source import FixtureSource

    class NoSales(FixtureSource):
        def daily_sales(self, *a):
            return {}

    use_source(lambda: NoSales(plans.FIXTURES))
    real = plans.estimate_rate
    monkeypatch.setattr(plans, "estimate_rate",
                        lambda *a, **k: dataclasses.replace(real(*a, **k), extrapolated=True))
    _, rows = claim(client, seed, seed.msku_a)
    assert [(r[0], r[1]) for r in rows] == [(None, False)] * 3
