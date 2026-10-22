"""usecase/plan_delete：未提交计划的硬删除（IM-D4/D5）。

★ 行没了之后 `op=delete_plan` 日志是唯一痕迹，所以成功与每种拒绝都对日志断言。"""
import pytest
from helpers import H, prepared

from shared.pg_client import pg_conn
from usecase.plan_delete import PlanNotFound, PlanReferenced, PlanSubmitted, delete_plan

TABLES = ("plan", "msku_claim", "plan_demand_cell", "plan_purchase_cell")


def rows(pid):
    out = {}
    with pg_conn() as c, c.cursor() as cur:
        for t in TABLES:
            cur.execute(f"SELECT count(*) FROM {t} WHERE plan_id = %s", (pid,))
            out[t] = cur.fetchone()[0]
    return out


def test_delete_unsubmitted_clears_all_four_tables_and_frees_mskus(client, seed, scm_log):
    pid = prepared(client, seed)
    before = rows(pid)
    assert all(before.values()), before
    out = delete_plan(pid, seed.actor)
    assert rows(pid) == dict.fromkeys(TABLES, 0)
    assert out["plan_id"] == pid and out["title"] == "10 月计划"
    assert {(m["seller_sku"], m["sid"]) for m in out["mskus"]} == {seed.msku_a, seed.msku_c}
    assert out["demand_cells"] == before["plan_demand_cell"]
    assert out["purchase_cells"] == before["plan_purchase_cell"]
    msg = scm_log.text
    assert "op=delete_plan" in msg and "outcome=ok" in msg
    assert "MSKU-A/11072" in msg and "MSKU-C/11094" in msg
    new = client.post("/v1/plans", json={"title": "新", "period_start": "2026-10-01", "months": 3},
                      headers=H(seed.actor)).json()["plan_id"]
    r = client.post(f"/v1/plans/{new}/claims",
                    json={"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]},
                    headers=H(seed.actor))
    assert r.status_code in (200, 201), r.text


def test_submitted_plan_refused_and_untouched(client, seed, scm_log):
    pid = prepared(client, seed)
    assert client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor)).status_code == 200
    before = rows(pid)
    with pytest.raises(PlanSubmitted) as ei:
        delete_plan(pid, seed.actor)
    assert ei.value.revs == 1
    assert rows(pid) == before
    assert "op=delete_plan" in scm_log.text and "outcome=PlanSubmitted" in scm_log.text
    assert "MSKU-A/11072" in scm_log.text


def test_archived_but_never_submitted_is_deletable(client, seed):
    pid = prepared(client, seed)
    assert client.post(f"/v1/plans/{pid}/archive", headers=H(seed.actor)).status_code == 200
    delete_plan(pid, seed.actor)
    assert rows(pid) == dict.fromkeys(TABLES, 0)


def test_not_found(client, seed, scm_log):
    with pytest.raises(PlanNotFound):
        delete_plan(999999, seed.actor)
    assert "op=delete_plan" in scm_log.text and "outcome=PlanNotFound" in scm_log.text


def test_foreign_reference_rolls_back_and_names_table(client, seed, scm_log):
    pid = prepared(client, seed)
    before = rows(pid)
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO sheet (created_by, plan_id, route) VALUES (%s, %s, 'fba')", (seed.actor, pid))
    with pytest.raises(PlanReferenced) as ei:
        delete_plan(pid, seed.actor)
    assert ei.value.table == "sheet"
    assert rows(pid) == before
    assert "outcome=PlanReferenced" in scm_log.text and "sheet" in scm_log.text


def test_non_fk_db_failure_logs_detail_and_cause(seed, scm_log, monkeypatch):
    """非外键的库错（断连、死锁…）：日志得说出怎么炸的 —— 光一个类名加 pgcode 定位不了。"""
    import contextlib

    import usecase.plan_delete as mod

    class FakePgError(Exception):
        pgcode = "08006"

    class Cur:
        def execute(self, *a):
            try:
                raise ConnectionResetError(104, "Connection reset by peer")
            except ConnectionResetError as inner:
                raise FakePgError("server closed the connection unexpectedly") from inner

    @contextlib.contextmanager
    def broken():
        conn = type("C", (), {"cursor": lambda self: contextlib.nullcontext(Cur())})()
        yield conn

    monkeypatch.setattr(mod, "pg_conn", broken)
    with pytest.raises(FakePgError):
        delete_plan(1, seed.actor)
    text = scm_log.text
    assert "outcome=FakePgError" in text and "pgcode=08006" in text
    assert "server closed the connection unexpectedly" in text
    assert "ConnectionResetError" in text and "errno=104" in text
