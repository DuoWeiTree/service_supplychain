"""UX-D14：计划格子批量写 —— 一个事务、逐格回执、设上限。"""
import logging

from helpers import H, prepared

from api.ui import plans
from shared.pg_client import pg_conn


def _human(pid):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT seller_sku, sid, to_char(period_start, 'YYYY-MM'), expected_units"
                    "  FROM plan_demand_cell WHERE plan_id = %s ORDER BY 1, 2, 3", (pid,))
        demand = cur.fetchall()
        cur.execute("SELECT sku, to_char(period_start, 'YYYY-MM'), planned_units"
                    "  FROM plan_purchase_cell WHERE plan_id = %s ORDER BY 1, 2", (pid,))
        return demand, cur.fetchall()


def test_writes_both_kinds_in_one_call_and_answers_cell_by_cell(client, seed):
    pid = prepared(client, seed)
    (ss_a, sid_a), (ss_c, sid_c) = seed.msku_a, seed.msku_c
    r = client.put(f"/v1/plans/{pid}/cells", headers=H(seed.actor), json={
        "demand": [{"seller_sku": ss_a, "sid": sid_a, "period": "2026-11", "expected_units": 7},
                   {"seller_sku": ss_c, "sid": sid_c, "period": "2026-10", "expected_units": None}],
        "purchase": [{"sku": seed.sku_a, "period": "2026-12", "planned_units": 33}]})
    assert r.status_code == 200, r.text
    body = r.json()
    # ★ 回执与 grid demand[] 同一个十键构造器 —— 前端拿它原地替换，不另写映射
    assert [(c["seller_sku"], c["period"], c["expected_units"]) for c in body["demand"]] == [
        (ss_a, "2026-11", 7), (ss_c, "2026-10", None)]
    assert set(body["demand"][0]) >= {"sku", "effective_units", "system_units", "basis"}
    assert body["purchase"] == [{"sku": seed.sku_a, "period": "2026-12", "planned_units": 33}]
    demand, purchase = _human(pid)
    assert (ss_a, sid_a, "2026-11", 7) in demand and (ss_c, sid_c, "2026-10", None) in demand
    assert (seed.sku_a, "2026-12", 33) in purchase


def test_one_bad_cell_means_nothing_is_written_and_every_offender_is_named(client, seed):
    """★ 全有或全无：半批写进去的计划解释不了。坏格逐格点名，不只报第一个。"""
    pid = prepared(client, seed)
    (ss_a, sid_a) = seed.msku_a
    before = _human(pid)
    r = client.put(f"/v1/plans/{pid}/cells", headers=H(seed.actor), json={
        "demand": [{"seller_sku": ss_a, "sid": sid_a, "period": "2026-11", "expected_units": 7},
                   {"seller_sku": ss_a, "sid": sid_a, "period": "2026-12", "expected_units": -1}],
        "purchase": [{"sku": seed.sku_a, "period": "2026-11", "planned_units": "5oo"}]})
    assert r.status_code == 400 and r.json()["error"] == "bad_units"
    cells = r.json()["cells"]
    assert [(c["kind"], c["period"]) for c in cells] == [("demand", "2026-12"), ("purchase", "2026-11")]
    assert _human(pid) == before


def test_a_missing_cell_rolls_back_the_cells_already_written(client, seed):
    """不存在的格在批尾 —— 前面已 UPDATE 的那几格要跟着回滚。"""
    pid = prepared(client, seed)
    (ss_a, sid_a) = seed.msku_a
    before = _human(pid)
    r = client.put(f"/v1/plans/{pid}/cells", headers=H(seed.actor), json={
        "demand": [{"seller_sku": ss_a, "sid": sid_a, "period": "2026-11", "expected_units": 7},
                   {"seller_sku": "NOPE", "sid": sid_a, "period": "2026-11", "expected_units": 1}],
        "purchase": [{"sku": seed.sku_a, "period": "2027-05", "planned_units": 1}]})
    assert r.status_code == 404 and r.json()["error"] == "cell_not_found"
    missing = r.json()["cells"]
    assert [(c["kind"], c.get("seller_sku") or c.get("sku"), c["period"]) for c in missing] == [
        ("demand", "NOPE", "2026-11"), ("purchase", seed.sku_a, "2027-05")]
    assert _human(pid) == before


def test_limit_duplicates_and_empty_are_refused_before_touching_the_db(client, seed, monkeypatch):
    pid = prepared(client, seed)
    (ss_a, sid_a) = seed.msku_a
    one = {"seller_sku": ss_a, "sid": sid_a, "period": "2026-11", "expected_units": 1}
    monkeypatch.setattr(plans, "CELLS_BATCH_MAX", 2)
    r = client.put(f"/v1/plans/{pid}/cells", headers=H(seed.actor),
                   json={"demand": [one, {**one, "period": "2026-12"}],
                         "purchase": [{"sku": seed.sku_a, "period": "2026-11", "planned_units": 1}]})
    assert r.status_code == 400 and r.json()["error"] == "batch_too_large"
    assert (r.json()["max"], r.json()["got"]) == (2, 3)
    r = client.put(f"/v1/plans/{pid}/cells", headers=H(seed.actor), json={"demand": [one, one]})
    assert r.status_code == 400 and r.json()["error"] == "duplicate_cell"
    r = client.put(f"/v1/plans/{pid}/cells", headers=H(seed.actor), json={"demand": [], "purchase": []})
    assert r.status_code == 400 and r.json()["error"] == "empty_batch"


def test_archived_plan_refuses_the_batch(client, seed):
    pid = prepared(client, seed)
    client.post(f"/v1/plans/{pid}/archive", headers=H(seed.actor))
    r = client.put(f"/v1/plans/{pid}/cells", headers=H(seed.actor),
                   json={"purchase": [{"sku": seed.sku_a, "period": "2026-11", "planned_units": 1}]})
    assert r.status_code == 409 and r.json()["error"] == "plan_archived"


def test_log_answers_who_how_many_how_long_and_which_failed(client, seed, caplog):
    pid = prepared(client, seed)
    caplog.set_level(logging.INFO, logger="scm.api")
    client.put(f"/v1/plans/{pid}/cells", headers=H(seed.actor),
               json={"purchase": [{"sku": seed.sku_a, "period": "2027-05", "planned_units": 1}]})
    line = next(r.getMessage() for r in caplog.records if "op=put_cells" in r.getMessage())
    assert f"plan_id={pid}" in line and "demand=0 purchase=1" in line
    assert "elapsed_ms=" in line and "outcome=cell_not_found" in line and "2027-05" in line
