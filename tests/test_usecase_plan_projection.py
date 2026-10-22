"""usecase/plan_projection：grid 与对外预测预览共用的推演组装。

★ 判据同 test_usecase_plan_claim：直接调用与经端点逐键相等，而不是各自看起来对。"""
import pytest

from shared.pg_client import pg_conn


def H(a):
    return {"x-actor": a}


def _plan_with_claims(client, seed):
    pid = client.post("/v1/plans", json={"title": "P", "period_start": "2026-10-01", "months": 3},
                      headers=H(seed.actor)).json()["plan_id"]
    for ms in (seed.msku_a, seed.msku_d):
        r = client.post(f"/v1/plans/{pid}/claims", json={"seller_sku": ms[0], "sid": ms[1]},
                        headers=H(seed.actor))
        assert r.status_code == 200, r.text
    client.put(f"/v1/plans/{pid}/purchase/{seed.sku_a}/2026-10", json={"planned_units": 200},
               headers=H(seed.actor))
    return pid


def _rows(pid):
    from api.ui.plans import _load_grid
    from usecase.lead_time import read_lead_time
    with pg_conn() as c, c.cursor() as cur:
        return (*_load_grid(cur, pid), read_lead_time(cur))


def test_project_matches_grid_for_the_same_rows(client, seed, lead_time_reset):
    from fixture_source import FIXTURES, FixtureSource

    from usecase.plan_projection import project
    pid = _plan_with_claims(client, seed)
    r = client.get(f"/v1/plans/{pid}/grid", headers=H(seed.actor))
    assert r.status_code == 200, r.text
    g = r.json()
    periods, demand, purchase, lead = _rows(pid)
    assert demand, "认领没长出格子 —— 下面的对核就是空对空"
    out = project(FixtureSource(FIXTURES), lead, demand, purchase, periods, plan_id=pid)
    # has_image 是 grid 自己补的（界面用），不归推演
    g_sku = [{k: v for k, v in row.items() if k != "has_image"} for row in g["forecast_sku"]]
    assert out["forecast_sku"] == g_sku
    for key in ("params", "forecast", "forecast_msku", "forecast_store", "beyond_horizon",
                "unallocated", "demand", "source_notes"):
        assert out[key] == g[key], key
    assert out["forecast"] and out["forecast_msku"], "空对空不算相等"


def test_preview_logs_plan_id_as_preview(client, seed, lead_time_reset, scm_log):
    from fixture_source import FIXTURES, FixtureSource

    from usecase.plan_projection import project
    pid = _plan_with_claims(client, seed)
    periods, demand, purchase, lead = _rows(pid)
    project(FixtureSource(FIXTURES), lead, demand, purchase, periods, plan_id=None)
    line = next(r.getMessage() for r in scm_log.records if "op=grid_forecast" in r.getMessage())
    assert "plan_id=preview" in line and "outcome=ok" in line


def test_source_failure_propagates_unwrapped(client, seed, lead_time_reset):
    """usecase 不认识 ApiError：取数源的异常原样上抛，由调用方（grid / pub）翻译。"""
    from fixture_source import FIXTURES, FixtureSource

    from dim.ch_source import ChUnavailable
    from usecase.plan_projection import project

    class Down(FixtureSource):
        def as_of(self):
            raise ChUnavailable("192.0.2.1:8123", {"code": "ECONNREFUSED"})

    pid = _plan_with_claims(client, seed)
    periods, demand, purchase, lead = _rows(pid)
    with pytest.raises(ChUnavailable):
        project(Down(FIXTURES), lead, demand, purchase, periods, plan_id=None)
