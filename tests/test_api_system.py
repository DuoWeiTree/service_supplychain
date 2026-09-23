"""接口层的地基：错误形状、操作人、未声明参数、镜像陈旧。"""
import asyncio
import json

from fastapi.exceptions import RequestValidationError
from starlette.requests import Request

from shared.pg_client import pg_conn


def test_health_has_no_prefix(client):
    """★ 探活目标 = base_url + health.path，不加 path_prefix（08 §0）。"""
    assert client.get("/health").status_code == 200
    assert client.get("/v1/health").status_code == 404


def test_error_shape_is_error_hint_plus_named_fields(client, seed):
    r = client.get("/v1/plans", headers={"x-actor": seed.actor}, params={"stat": "x"})
    assert r.status_code == 400
    body = r.json()
    assert body["error"] == "unknown_query_param" and body["hint"]
    # ★ 点名字段平铺在顶层：只说「参数有问题」，前端只能猜
    assert body["unknown"] == ["stat"]
    assert "state" in body["declared"]


def test_a_write_without_the_actor_header_is_400(client, seed):
    """★ 裁定第 6 条：写请求读 x-actor。"""
    r = client.post("/v1/plans", json={"title": "t", "period_start": "2026-10-01"})
    assert r.status_code == 400 and r.json()["error"] == "unknown_actor"
    assert r.json()["header"] == "x-actor"


def test_a_read_without_the_actor_header_is_allowed(client, seed):
    """★ 读不强制 —— 但给了就必须有效，见下一条。
    「给了个错名字却照常返回全量」比「不让读」更坏：人会以为自己看的是那个人的视角。"""
    assert client.get("/v1/plans").status_code == 200


def test_a_read_with_a_bogus_actor_is_400_and_names_it(client, seed):
    r = client.get("/v1/plans", headers={"x-actor": "ghost"})
    assert r.status_code == 400 and r.json()["actor"] == "ghost"


def test_inactive_actor_is_400_and_says_it_is_inactive(client, seed):
    """★ 「查无此人」与「这个人停用了」共用一个 error，但点名字段必须分得开 ——
    不分开，停用的人会以为自己打错了名字。"""
    r = client.post("/v1/plans", json={"title": "t", "period_start": "2026-10-01"},
                    headers={"x-actor": seed.actor_inactive})
    assert r.status_code == 400
    assert (r.json()["actor"], r.json()["active"]) == (seed.actor_inactive, False)


def test_stale_mirror_refuses_service_with_503(client, seed):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE seller SET refreshed_at = now() - interval '40 hours'")
    r = client.get("/v1/plans", headers={"x-actor": seed.actor})
    assert r.status_code == 503 and r.json()["error"] == "mirror_stale"
    assert r.json()["stale"][0]["mirror"] == "seller"


def test_empty_mirror_is_stale_not_fresh(client, wipe):
    """★ 空表的 max(refreshed_at) 是 NULL —— 当成新鲜就是拿空表在服务。"""
    r = client.get("/v1/plans")
    assert r.status_code == 503
    assert {m["mirror"] for m in r.json()["stale"]} == {
        "sku_catalog", "msku_bridge", "seller", "warehouse"}


def test_readiness_says_erp_is_not_implemented(client, seed):
    """★ 空实现会让「该做没做」和「本来就不用做」长得一样（规则五）。"""
    body = client.get("/v1/readiness", headers={"x-actor": seed.actor}).json()
    assert body["erp"] == "not_implemented"
    assert body["migrations"][-1].startswith("00")
    assert len(body["mirrors"]) == 4


def test_readiness_with_an_unreachable_pg_is_503_with_a_named_cause(client, seed, monkeypatch):
    """★ 复审残留 ①：PG 连不上时 `/v1/readiness` 给的是**裸 500**（纯文本
    `Internal Server Error`，不是 S-29 形状、也不是 503）—— 而这恰恰是要用来查
    「为什么起不来」的那个口子（OQ-8 的原话）。

    成因：`_pg_error` 把「认不出的约束」与「压根连不上」当成同一种放行。
    后者正是 I-3 为运维端点判定该给 503 的那一类 —— 同一把尺子要量到底。

    ★ 用死端口让 psycopg2 真的去连、真的被拒（loopback），不 monkeypatch 一个
      假异常：`.pgcode` 是 C 扩展 populate 的，手搓的假货判据对不齐。
    """
    from shared import pg_client
    dead = {**pg_client.business_pg(), "host": "127.0.0.1", "port": 5499}
    monkeypatch.setattr(pg_client, "business_pg", lambda: dead)

    assert client.get("/health").status_code == 200, "PG 连不上也不许拖垮 /health"

    r = client.get("/v1/readiness", headers={"x-actor": seed.actor})
    assert r.status_code == 503, f"上游连不上必须是 503，实际 {r.status_code}：{r.text}"
    body = r.json()
    assert body["error"] == "database_unavailable", body
    assert "hint" in body, f"S-29 形状缺 hint：{body}"
    assert body.get("err_type") == "OperationalError", f"没点名异常类名：{body}"
    assert "5499" in str(body.get("target")), f"没点名打的谁：{body}"


def test_unknown_path_is_404_reshaped(client):
    """★ S-30：框架自己抛的 404 也要走 S-29 的 {error, hint, …} 形状，
    不是 FastAPI 默认的 {"detail": "Not Found"}。"""
    r = client.get("/v1/does-not-exist")
    assert r.status_code == 404
    body = r.json()
    assert body["error"] == "not_found" and body["hint"]
    assert body["path"] == "/v1/does-not-exist" and body["method"] == "GET"


def test_wrong_method_is_405_reshaped(client):
    r = client.delete("/health")
    assert r.status_code == 405
    body = r.json()
    assert body["error"] == "method_not_allowed" and body["hint"]
    assert body["path"] == "/health" and body["method"] == "DELETE"


def test_validation_error_handler_reshapes_to_400_not_422():
    """★ S-30/S-18：请求体校验失败是「你写错了」，400；422 专留给 illegal_transition。

    阶段 A 目前没有解析 body 的路由（Task 10 的 POST /v1/plans 落地后才有端点能
    端到端触发它），先直接调处理函数验证形状与状态码，避免这条规则等到 Task 10
    才第一次被测到。
    """
    from api import _reshape_validation_error

    exc = RequestValidationError([{"loc": ("body", "title"), "msg": "field required",
                                   "type": "missing"}])
    request = Request({"type": "http", "method": "POST", "path": "/v1/plans", "headers": []})
    response = asyncio.run(_reshape_validation_error(request, exc))
    assert response.status_code == 400
    body = json.loads(response.body)
    assert body["error"] == "validation_error" and body["hint"]
    assert body["fields"] == [{"loc": ["body", "title"], "msg": "field required",
                               "type": "missing"}]
