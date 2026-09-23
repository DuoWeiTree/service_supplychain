"""运维触发接口。★ 仅运维可用的手动触发（07:482）。"""
from jobs.lock import advisory_lock
from tests.helpers import make_ch_unreachable


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


def test_unreachable_ch_is_503_with_a_named_cause_not_a_bare_500(client, seed, monkeypatch):
    """★ 终审 I-3：镜像陈旧多半**就是因为** CH 连不上，而这时去点这个专门用来
    修它的端点，原先拿到的是一个不符合 S-29/S-30 形状的裸 500
    （`"Internal Server Error"`，没有 error / hint，也没说是谁连不上）。

    ★ 选 503 不选 500：这是上游依赖不可用，不是本服务算错了。两者调用方的
      处置不同 —— 503 该重试/去查 CH，500 该来查我们的代码。
    """
    target = make_ch_unreachable(monkeypatch)
    r = client.post("/v1/jobs/refresh-dims", json={}, headers={"x-actor": seed.actor})
    assert r.status_code == 503, f"上游连不上必须是 503，实际 {r.status_code}：{r.text}"
    body = r.json()
    assert body["error"] == "refresh_failed", body
    assert "hint" in body, f"S-29 形状缺 hint：{body}"
    assert target in str(body.get("target")), f"没点名打的谁：{body}"
    assert "connect_refused" in str(body.get("cause")), f"没点名怎么失败的：{body}"


def test_undeclared_query_param_is_400(client, seed):
    r = client.post("/v1/jobs/refresh-dims?force=1", json={},
                    headers={"x-actor": seed.actor})
    assert r.status_code == 400 and r.json()["error"] == "unknown_query_param"
