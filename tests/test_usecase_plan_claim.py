"""usecase/plan_claim：认领下沉，`POST /claims`、Excel 导入、对外预测预览共用。

★ 判据：导入与预览各自调用这几个函数，而不是各写一份 —— 所以这里对核的是
  「直接调用」与「经端点」逐列相等，而不是各自看起来对。"""
import datetime as dt

import psycopg2
import pytest

from shared.pg_client import pg_conn

PLAN_TABLES = ("plan", "msku_claim", "plan_demand_cell", "plan_purchase_cell")
#: 逐列对核时去掉的列：两张计划的 plan_id 本就不同；时间戳是写入时刻
SKIP_COLS = {"plan_id", "updated_at"}


def H(a):
    return {"x-actor": a}


def new_plan(client, seed, title="t"):
    r = client.post("/v1/plans", json={"title": title, "period_start": "2026-10-01", "months": 3},
                    headers=H(seed.actor))
    assert r.status_code == 201, r.text
    return r.json()["plan_id"]


def counts():
    with pg_conn() as c, c.cursor() as cur:
        out = {}
        for t in PLAN_TABLES:
            cur.execute(f"SELECT count(*) FROM {t}")
            out[t] = cur.fetchone()[0]
        return out


def cells(pid):
    with pg_conn() as c, c.cursor() as cur:
        out = {}
        for t, order in (("plan_demand_cell", "seller_sku, sid, period_start"),
                         ("plan_purchase_cell", "sku, period_start")):
            cur.execute(f"SELECT * FROM {t} WHERE plan_id = %s ORDER BY {order}", (pid,))
            names = [d.name for d in cur.description]
            out[t] = [{k: v for k, v in zip(names, row) if k not in SKIP_COLS}
                      for row in cur.fetchall()]
        return out


def test_seed_rows_writes_nothing(client, seed):
    from fixture_source import FIXTURES, FixtureSource

    from usecase.plan_claim import plan_periods, seed_rows

    pid = new_plan(client, seed)
    with pg_conn() as c, c.cursor() as cur:
        periods = plan_periods(cur, pid)
    assert periods == [dt.date(2026, 10, 1), dt.date(2026, 11, 1), dt.date(2026, 12, 1)]
    before = counts()
    rows = seed_rows(FixtureSource(FIXTURES), *seed.msku_a, seed.sku_a, "amazon", True, periods)
    assert counts() == before
    # 与 _load_grid 的 demand 行同形：(seller_sku, sid, sku, period, system_units,
    #   system_extrapolated, expected_units, has_fba, system_basis)
    assert [r[:8] for r in rows] == [
        ("MSKU-A", "11072", seed.sku_a, p, u, False, None, True)
        for p, u in zip(periods, (135, 130, 135))]
    assert [r[8]["days_in_month"] for r in rows] == [31, 30, 31]


def test_seed_rows_not_applicable_platform_is_null_not_zero(seed):
    from fixture_source import FIXTURES, FixtureSource

    from usecase.plan_claim import seed_rows

    rows = seed_rows(FixtureSource(FIXTURES), *seed.msku_nofba, seed.sku_b, "walmart", False,
                     [dt.date(2026, 10, 1)])
    assert rows[0][4] is None and rows[0][7] is False
    assert rows[0][8]["reason"] == "not_applicable_no_sales_source"


def test_claim_msku_seeds_cells_like_the_endpoint(client, seed):
    from fixture_source import FIXTURES, FixtureSource

    from usecase.plan_claim import claim_msku

    via_api = new_plan(client, seed, "经端点")
    r = client.post(f"/v1/plans/{via_api}/claims",
                    json={"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]}, headers=H(seed.actor))
    assert r.status_code == 200, r.text
    expected = cells(via_api)
    assert len(expected["plan_demand_cell"]) == 3 and len(expected["plan_purchase_cell"]) == 3
    # 释放掉，好让另一张计划能认领同一个 msku（释放会删期望销量格，先存下来再放）
    assert client.delete(f"/v1/plans/{via_api}/claims/{seed.msku_a[0]}/{seed.msku_a[1]}",
                         headers=H(seed.actor)).status_code == 200

    direct = new_plan(client, seed, "直调")
    with pg_conn() as c, c.cursor() as cur:
        body = claim_msku(cur, FixtureSource(FIXTURES), direct, *seed.msku_a, seed.actor)
    assert body == {**r.json()}
    assert cells(direct) == expected


def test_claim_msku_raises_unknown_msku(client, seed):
    from fixture_source import FIXTURES, FixtureSource

    from usecase.plan_claim import UnknownMsku, claim_msku

    pid = new_plan(client, seed)
    with pg_conn() as c, c.cursor() as cur, pytest.raises(UnknownMsku) as ei:
        claim_msku(cur, FixtureSource(FIXTURES), pid, "NOPE", "11072", seed.actor)
    assert (ei.value.seller_sku, ei.value.sid) == ("NOPE", "11072")


def test_claim_msku_names_the_holder_on_the_fast_path(client, seed):
    from fixture_source import FIXTURES, FixtureSource

    from usecase.plan_claim import ClaimedElsewhere, claim_msku

    holder = new_plan(client, seed, "先占")
    client.post(f"/v1/plans/{holder}/claims",
                json={"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]}, headers=H(seed.actor))
    pid = new_plan(client, seed, "后到")
    with pg_conn() as c, c.cursor() as cur, pytest.raises(ClaimedElsewhere) as ei:
        claim_msku(cur, FixtureSource(FIXTURES), pid, *seed.msku_a, seed.actor)
    assert ei.value.holder == (holder, seed.actor, "先占")


def test_claim_msku_raises_claimed_elsewhere_without_rolling_back(client, seed, monkeypatch):
    """撞唯一索引那一支：快路径读看漏（并发）时，由 INSERT 裁决。usecase 不替调用方回滚 ——
    导入要把整个事务交给自己的边界；回滚之后再用 claim_holder 补名。"""
    from fixture_source import FIXTURES, FixtureSource

    from usecase import plan_claim

    holder = new_plan(client, seed, "先占")
    client.post(f"/v1/plans/{holder}/claims",
                json={"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]}, headers=H(seed.actor))
    pid = new_plan(client, seed, "后到")
    # 模拟并发：快路径那次读没看见占用者
    monkeypatch.setattr(plan_claim, "_active_claim", lambda *a: None)
    with pg_conn() as c:
        with c.cursor() as cur, pytest.raises(plan_claim.ClaimedElsewhere) as ei:
            plan_claim.claim_msku(cur, FixtureSource(FIXTURES), pid, *seed.msku_a, seed.actor)
        assert ei.value.holder is None
        assert c.get_transaction_status() == psycopg2.extensions.TRANSACTION_STATUS_INERROR
        c.rollback()
    with pg_conn() as c, c.cursor() as cur:   # 新连接：回滚撤掉了 search_path
        assert plan_claim.claim_holder(cur, *seed.msku_a) == (holder, seed.actor, "先占")


def test_endpoint_names_the_holder_after_a_unique_violation(client, seed, monkeypatch):
    """端点那一侧：回滚后补名必须真的查得到 —— 回滚会撤掉 search_path，在原连接上查会 500。"""
    from usecase import plan_claim

    holder = new_plan(client, seed, "先占")
    client.post(f"/v1/plans/{holder}/claims",
                json={"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]}, headers=H(seed.actor))
    pid = new_plan(client, seed, "后到")
    monkeypatch.setattr(plan_claim, "_active_claim", lambda *a: None)
    r = client.post(f"/v1/plans/{pid}/claims",
                    json={"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]}, headers=H(seed.actor))
    assert r.status_code == 409, r.text
    assert r.json()["error"] == "msku_already_claimed"
    assert r.json()["claimed_by"] == {"plan_id": holder, "actor": seed.actor, "title": "先占"}
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM plan_demand_cell WHERE plan_id = %s", (pid,))
        assert cur.fetchone()[0] == 0
