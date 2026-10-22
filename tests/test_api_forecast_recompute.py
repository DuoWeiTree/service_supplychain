"""task-23：已认领的格子要能重算预测（W-7 的另一半）。

★ 根因：`claim()` 只在认领那一刻 seed 一次 `system_units`/`system_basis`，换源之后
已经存在的认领不会自己回头再算——见 `.superpowers/sdd/2026-09-24-plan-forecast/task-23-brief.md`。
fixture：as_of 2026-09-21 → 估计锚点 09-14；MSKU-A 每天 4 件（4.3251/天）。
"""
import datetime as dt

import pytest
from fixture_source import FIXTURES, FixtureSource

from dim.ch_source import ChUnavailable
from shared.pg_client import pg_conn
from usecase import plan_claim


def H(a):
    return {"x-actor": a}


class NoSales(FixtureSource):
    """与 test_api_claims_forecast.py 同一个替身：窗口内一行销量都没有。"""

    def daily_sales(self, *a):
        return {}


def new_plan(client, seed) -> int:
    return client.post("/v1/plans", json={"title": "t", "period_start": "2026-10-01", "months": 3},
                       headers=H(seed.actor)).json()["plan_id"]


def cells(pid: int):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT seller_sku, sid, period_start, system_units, expected_units,"
                    "       system_basis FROM plan_demand_cell WHERE plan_id = %s"
                    " ORDER BY period_start", (pid,))
        return cur.fetchall()


def test_recompute_fills_a_null_cell_with_full_basis(client, seed, use_source):
    """claim() 在没有销量时把 system_units 留 NULL；「换源」之后 recompute 应当把它算出来，
    且 system_basis 七项齐全：base_rate、月天数、估计锚点、embargo_days、窗口起止、
    censor 天数、收缩前后两个速率（raw_rate / base_rate）。"""
    use_source(lambda: NoSales(FIXTURES))
    pid = new_plan(client, seed)
    r = client.post(f"/v1/plans/{pid}/claims", json={"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]},
                    headers=H(seed.actor))
    assert r.status_code == 200, r.text
    before = cells(pid)
    assert [row[3] for row in before] == [None, None, None]

    use_source(lambda: FixtureSource(FIXTURES))  # 「换源」：这次真的有销量历史了
    r2 = client.post(f"/v1/plans/{pid}/forecast/recompute", headers=H(seed.actor))
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["no_rate"] == []
    assert body["unchanged"] == 0
    assert len(body["recomputed"]) == 3
    assert [c["before"] for c in body["recomputed"]] == [None, None, None]
    assert [c["after"] for c in body["recomputed"]] == [135, 130, 135]   # ceil(4.3251×31/30/31)

    after = cells(pid)
    assert [row[3] for row in after] == [135, 130, 135]
    b = after[0][5]
    assert b["method"] == "baseline_rate_v1"
    assert (b["as_of"], b["estimation_anchor"], b["embargo_days"]) == ("2026-09-21", "2026-09-14", 7)
    assert b["base_rate"] == pytest.approx(4.325139122267326, rel=1e-12)
    assert b["raw_rate"] is not None and b["raw_rate"] != b["base_rate"]   # 收缩前 ≠ 收缩后
    assert b["window"] == {"from": "2026-07-21", "until": "2026-09-14"}
    assert b["censored_days"] == 0


def test_recompute_never_touches_expected_units(client, seed):
    """★ 判据①：人填的 expected_units 一个字节都不许变。"""
    pid = new_plan(client, seed)
    client.post(f"/v1/plans/{pid}/claims", json={"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]},
               headers=H(seed.actor))
    client.put(f"/v1/plans/{pid}/demand/{seed.msku_a[0]}/{seed.msku_a[1]}/2026-10",
              json={"expected_units": 999}, headers=H(seed.actor))
    before = {(r[0], r[1], r[2]): r[4] for r in cells(pid)}
    assert before[(seed.msku_a[0], seed.msku_a[1], dt.date(2026, 10, 1))] == 999

    r = client.post(f"/v1/plans/{pid}/forecast/recompute", headers=H(seed.actor))
    assert r.status_code == 200, r.text

    after = {(r[0], r[1], r[2]): r[4] for r in cells(pid)}
    assert after == before, "重算改动了 expected_units —— 这是本任务的第一判据"


def test_no_rate_msku_stays_null_and_is_named_separately(client, seed, use_source):
    """★ 判据③⑥：算不出速率的 msku 走 no_rate[]，system_units 仍是 NULL（不是 0），
    且与「算出来了」分开列，不合成一个成功计数。"""
    use_source(lambda: NoSales(FIXTURES))
    pid = new_plan(client, seed)
    client.post(f"/v1/plans/{pid}/claims", json={"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]},
               headers=H(seed.actor))
    before = cells(pid)
    assert [row[3] for row in before] == [None, None, None]

    r = client.post(f"/v1/plans/{pid}/forecast/recompute", headers=H(seed.actor))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["no_rate"] == [{"seller_sku": "MSKU-A", "sid": "11072", "reason": "no_sales_history"}]
    assert body["recomputed"] == [], "null → null 没有变化，不该出现在 recomputed 里"
    assert body["unchanged"] == 3

    after = cells(pid)
    assert [row[3] for row in after] == [None, None, None], "算不出速率必须仍是 NULL，不是 0"


def test_recompute_on_archived_plan_is_the_existing_409_shape(client, seed):
    """★ 只读态计划按既有形状拒绝（ensure_writable），不新开错误码。"""
    pid = new_plan(client, seed)
    client.post(f"/v1/plans/{pid}/claims", json={"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]},
               headers=H(seed.actor))
    assert client.post(f"/v1/plans/{pid}/archive", headers=H(seed.actor)).status_code == 200
    r = client.post(f"/v1/plans/{pid}/forecast/recompute", headers=H(seed.actor))
    assert r.status_code == 409
    assert r.json()["error"] == "plan_archived"
    assert r.json()["plan_id"] == pid


def test_a_mid_batch_source_failure_leaves_no_cell_changed(client, seed, use_source):
    """★ 判据⑤：取数中途失败 ⇒ 一格都没落（事务性）。两个 msku 挂在同一张计划里，
    第二个（按 sku 排序排在后面）的 daily_sales 抛 ChUnavailable —— 第一个已经算完、
    还没提交，整个事务必须连它一起回滚。"""
    pid = new_plan(client, seed)
    client.post(f"/v1/plans/{pid}/claims", json={"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]},
               headers=H(seed.actor))
    client.post(f"/v1/plans/{pid}/claims", json={"seller_sku": seed.msku_d[0], "sid": seed.msku_d[1]},
               headers=H(seed.actor))
    before = cells(pid)

    class FlakyOnSecond(FixtureSource):
        def __init__(self, root):
            super().__init__(root)
            self.calls = 0

        def daily_sales(self, seller_sku, sid, start, end):
            self.calls += 1
            if self.calls == 2:
                raise ChUnavailable("ch://test", {"code": "ETIMEDOUT"})
            return super().daily_sales(seller_sku, sid, start, end)

    use_source(lambda: FlakyOnSecond(FIXTURES))
    r = client.post(f"/v1/plans/{pid}/forecast/recompute", headers=H(seed.actor))
    assert r.status_code == 503, r.text

    after = cells(pid)
    assert after == before, "越到一半就整笔回滚，不许留半截"


def test_claim_and_recompute_share_the_same_forecast_function(client, seed, monkeypatch):
    """判据⑥：认领与重算走的是同一个函数——用调用计数证，不用 `is` 比 interned 常量。
    monkeypatch 替换 `plan_claim.seed_forecast` 后，两条路径若真的共用一个引用，
    两次调用都会落在同一个 spy 上；如果重算另写了一份算法，这里只会数到 1 次。"""
    calls = []
    real = plan_claim.seed_forecast

    def spy(source, plan_id, seller_sku, sid, platform):
        calls.append((plan_id, seller_sku, sid, platform))
        return real(source, plan_id, seller_sku, sid, platform)

    monkeypatch.setattr(plan_claim, "seed_forecast", spy)

    pid = new_plan(client, seed)
    r = client.post(f"/v1/plans/{pid}/claims", json={"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]},
                    headers=H(seed.actor))
    assert r.status_code == 200, r.text
    assert len(calls) == 1

    r2 = client.post(f"/v1/plans/{pid}/forecast/recompute", headers=H(seed.actor))
    assert r2.status_code == 200, r2.text
    assert len(calls) == 2, "recompute_forecast() 没有打中同一个 seed_forecast 引用"
    assert calls[1] == (pid, seed.msku_a[0], seed.msku_a[1], "amazon")
