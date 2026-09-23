"""运维触发接口。★ 仅运维可用的手动触发（07:482）。"""
from jobs.lock import advisory_lock


def test_trigger_requires_actor(client, seed):
    assert client.post("/v1/jobs/refresh-dims", json={}).status_code == 400


def test_trigger_reports_每个镜像的结果(client, seed, monkeypatch):
    from jobs import refresh_dims as rd
    monkeypatch.setattr(rd, "refresh_all",
                        lambda *a, **k: [rd.RunRow("sku_catalog", 1, True, 3, 0, None)])
    r = client.post("/v1/jobs/refresh-dims", json={}, headers={"x-actor": seed.actor})
    assert r.status_code == 200
    assert r.json()["runs"][0] == {"mirror": "sku_catalog", "run_id": 1, "ok": True,
                                   "rows_in": 3, "rows_dropped": 0, "error": None}


def test_trigger_while_another_run_holds_the_lock_is_409(client, seed):
    with advisory_lock():
        r = client.post("/v1/jobs/refresh-dims", json={}, headers={"x-actor": seed.actor})
    assert r.status_code == 409 and r.json()["error"] == "refresh_in_flight"


def test_unknown_mirror_is_404_not_silently_all(client, seed):
    """★ 打错名字却照常刷了全部，比不让刷更坏。"""
    r = client.post("/v1/jobs/refresh-dims", json={"only": "nope"},
                    headers={"x-actor": seed.actor})
    assert r.status_code == 404 and r.json()["error"] == "unknown_mirror"


def test_undeclared_query_param_is_400(client, seed):
    r = client.post("/v1/jobs/refresh-dims?force=1", json={},
                    headers={"x-actor": seed.actor})
    assert r.status_code == 400 and r.json()["error"] == "unknown_query_param"
