"""领星备货单回执（spec 2026-10-13）：只读领星 API · 只追加观察 · 全部已完成才归档 · 两轴不一致只点名。"""
from shared.pg_client import pg_conn
from tests.helpers import H
from tests.test_api_submit_ows import confirmed, preview, submit  # noqa: F401
from tests.test_api_sheets import OVERSEA, stock, world  # noqa: F401


def receipt(client, seed, sid):
    return client.post(f"/v1/sheets/{sid}/receipt", headers=H(seed.actor))


def detail(client, sid):
    return client.get(f"/v1/sheets/{sid}").json()


def refs(client, sid):
    return {e["ext_no"]: e for e in detail(client, sid)["ext_refs"]}


def oversea_stage(client, sid):
    return next(c["our_stage"] for c in detail(client, sid)["consignments"] if c["channel"] == "oversea")


def test_progress_is_read_back_and_all_complete_archives(client, seed, confirmed, lingxing):
    submit(client, seed, confirmed)
    r = receipt(client, seed, confirmed)
    assert r.status_code == 200, r.text
    assert {x["stage"] for x in r.json()["results"]} == {"待发货"} and r.json()["archived"] == []
    for no in list(lingxing._ows):
        lingxing.set_ows_status(no, "50", "1")
    receipt(client, seed, confirmed)
    assert {e["erp_stage"] for e in refs(client, confirmed).values()} == {"待收货"}
    for no in list(lingxing._ows):
        lingxing.set_ows_status(no, "60")
    r = receipt(client, seed, confirmed).json()
    assert len(r["archived"]) == 1 and oversea_stage(client, confirmed) == "已归档"
    assert all(e["stage_observed_at"] for e in refs(client, confirmed).values())


def test_one_complete_one_pending_does_not_archive(client, seed, confirmed, lingxing):
    submit(client, seed, confirmed)
    first, _ = sorted(lingxing._ows)
    lingxing.set_ows_status(first, "60")
    r = receipt(client, seed, confirmed).json()
    assert r["archived"] == [] and oversea_stage(client, confirmed) == "已投送"


def test_order_deleted_in_lingxing_is_named_not_acted_on(client, seed, confirmed, lingxing):
    submit(client, seed, confirmed)
    gone = sorted(lingxing._ows)[0]
    del lingxing._ows[gone]
    receipt(client, seed, confirmed)
    e = refs(client, confirmed)[gone]
    assert (e["erp_stage"], e["drift"]) == ("领星无此单", "领星里没有这张单")
    assert oversea_stage(client, confirmed) == "已投送"


def test_read_failure_writes_no_observation_and_is_named(client, seed, confirmed, lingxing):
    submit(client, seed, confirmed)
    lingxing.enqueue("ows_order_detail", "not_sent")
    r = receipt(client, seed, confirmed).json()
    assert sorted(x["outcome"] for x in r["results"]) == ["not_sent", "ok"]
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT ok, failed, jsonb_array_length(failures) FROM receipt_run_result WHERE run_id=%s", (r["run_id"],))
        assert cur.fetchone() == (1, 1, 1)
        cur.execute("SELECT count(*) FROM ext_stage_obs WHERE run_id=%s", (r["run_id"],))
        assert cur.fetchone()[0] == 1


def test_manual_receipt_refused_while_sheet_busy_and_tick_skips_it(client, seed, confirmed, lingxing):
    from usecase import receipt as uc, submit_ows
    submit(client, seed, confirmed)
    with submit_ows.sheet_op(confirmed):
        r = receipt(client, seed, confirmed)
        assert r.status_code == 409 and r.json()["error"] == "sheet_busy"
        out = uc.tick()
    assert (out["skipped"], out["refs"]) == (1, 0)


def test_tick_reads_only_dispatched_consignments(client, seed, confirmed, lingxing):
    from usecase import receipt as uc
    assert uc.tick()["refs"] == 0                       # 已确认、没提交：不读
    submit(client, seed, confirmed)
    out = uc.tick()
    assert (out["refs"], out["ok"]) == (2, 2)
    for no in list(lingxing._ows):
        lingxing.set_ows_status(no, "60")
    assert len(uc.tick()["archived"]) == 1
    assert uc.tick()["refs"] == 0                       # 已归档：不再读
