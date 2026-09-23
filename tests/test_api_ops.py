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


def test_a_deep_keyerror_is_not_painted_over_as_unknown_mirror(client, seed, monkeypatch):
    """★ 复审残留 ③：`except KeyError` 兜住的不只是「登记表里没这个名字」——
    任何从刷新深处冒上来的 `KeyError`（编程错误、字典拿错键）都会被报成
    404 `unknown_mirror`，于是运维去查一个拼写正确的镜像名为什么「不存在」。

    这与同一个函数里拒绝 `except Exception` 兜底的理由是同一条，只是换了个
    异常类型：把编程错误涂成别的东西。名字该在调用**之前**验，验过之后
    再冒出来的 `KeyError` 就不再是「名字错了」。
    """
    from jobs import refresh_dims as rd

    def _boom(*a, **k):
        raise KeyError("某个字典在刷新深处拿错了键")

    monkeypatch.setattr(rd, "refresh_all", _boom)
    r = client.post("/v1/jobs/refresh-dims", json={"only": "sku_catalog"},
                    headers={"x-actor": seed.actor})
    assert r.status_code != 404, (
        f"深处的 KeyError 被涂成了 404 unknown_mirror：{r.status_code} {r.text}")


def test_a_known_mirror_name_is_validated_before_the_refresh_is_attempted(
        client, seed, monkeypatch):
    """★ 名字验在调用之前：拼错的名字不该先抢锁、先建连、先碰 CH 才被拒。"""
    from jobs import refresh_dims as rd
    called = []
    monkeypatch.setattr(rd, "refresh_all", lambda *a, **k: called.append(k) or [])
    r = client.post("/v1/jobs/refresh-dims", json={"only": "nope"},
                    headers={"x-actor": seed.actor})
    assert r.status_code == 404 and r.json()["error"] == "unknown_mirror"
    assert called == [], "名字是错的，却已经去跑刷新了"


def test_undeclared_query_param_is_400(client, seed):
    r = client.post("/v1/jobs/refresh-dims?force=1", json={},
                    headers={"x-actor": seed.actor})
    assert r.status_code == 400 and r.json()["error"] == "unknown_query_param"
