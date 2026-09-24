import logging

import pytest

from shared.pg_client import pg_conn


def H(a):
    return {"x-actor": a}


def test_get_returns_the_seeded_default(client, seed, lead_time_reset):
    r = client.get("/v1/lead-time")
    assert r.status_code == 200
    assert r.json() == {"route": "oversea_sea", "make_days": 30, "ship_days": 60,
                        "anchor_day": 15,
                        "valid_from": {"making": "2026-01-01", "shipping": "2026-01-01"}}


def test_put_changes_only_what_is_given_and_logs_old_to_new(client, seed, lead_time_reset,
                                                            caplog):
    """spec §6：改了写日志（旧值 → 新值）。"""
    caplog.set_level(logging.INFO)
    r = client.put("/v1/lead-time", json={"make_days": 10}, headers=H(seed.actor))
    assert r.status_code == 200
    body = r.json()
    assert (body["make_days"], body["ship_days"]) == (10, 60)
    assert body["changed"] == [{"segment": "making", "from": 30, "to": 10}]
    assert "op=lead_time_update" in caplog.text and "segment=making from=30 to=10" in caplog.text
    assert client.get("/v1/lead-time").json()["make_days"] == 10


def test_put_keeps_history_and_same_day_edits_in_place(client, seed, lead_time_reset):
    """OP-4：旧行关掉留痕；同一天再改只改今天那行。"""
    client.put("/v1/lead-time", json={"make_days": 10}, headers=H(seed.actor))
    client.put("/v1/lead-time", json={"make_days": 12}, headers=H(seed.actor))
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT days, valid_to IS NULL FROM lead_time"
                    " WHERE route = 'oversea_sea' AND segment = 'making' ORDER BY valid_from")
        assert cur.fetchall() == [(30, False), (12, True)]


def test_put_same_value_changes_nothing(client, seed, lead_time_reset):
    r = client.put("/v1/lead-time", json={"ship_days": 60}, headers=H(seed.actor))
    assert r.json()["changed"] == []


@pytest.mark.parametrize("body", [{"make_days": -1}, {"ship_days": 721}, {"make_days": "30"},
                                  {"make_days": 3.5}, {"make_days": True}, {}])
def test_put_rejects_out_of_range(client, seed, lead_time_reset, body):
    """Review Focus 3：范围 0~720 天（03 §41 CHECK），类型在这里挡。"""
    r = client.put("/v1/lead-time", json=body, headers=H(seed.actor))
    assert r.status_code == 400
    assert r.json()["error"] == "validation_error"


def test_put_needs_an_actor(client, seed, lead_time_reset):
    assert client.put("/v1/lead-time", json={"make_days": 1}).status_code == 400


def test_misconfigured_table_is_a_named_500(client, seed, lead_time_reset):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE lead_time SET valid_to = DATE '2026-02-01'"
                    " WHERE route = 'oversea_sea' AND segment = 'making'")
    r = client.get("/v1/lead-time")
    assert r.status_code == 500
    assert r.json()["error"] == "lead_time_misconfigured"


def test_startup_names_a_misconfigured_lead_time(business_db, lead_time_reset, caplog):
    """CLAUDE.md：配置类错误在启动日志第一屏可见，不等第一个请求才炸。"""
    from api import _startup_check
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("DELETE FROM lead_time WHERE route = 'oversea_sea' AND segment = 'shipping'")
    caplog.set_level(logging.INFO)
    _startup_check()
    assert "stage=lead_time" in caplog.text and "outcome=fail" in caplog.text
