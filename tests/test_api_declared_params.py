"""未声明查询参数一律 400 —— 写端点与 /health 也不例外。

★ 为什么不能只在读端点上拦：拼错的参数被静默忽略时，返回的是一次**看起来成功的写**。
  `POST /v1/plans?typo=1` 201、`POST …/claims?typo=1` 200 —— 人以为那个参数生效了，
  而它从头到尾没有被任何代码看过一眼。
"""
import pytest
from helpers import H, prepared


def cases(seed, pid):
    ms, sku = seed.msku_a, seed.sku_a
    return {
        "create_plan": ("post", "/v1/plans",
                        {"title": "t", "period_start": "2026-10-01"}),
        "claim": ("post", f"/v1/plans/{pid}/claims",
                  {"seller_sku": ms[0], "sid": ms[1]}),
        "release": ("delete", f"/v1/plans/{pid}/claims/{ms[0]}/{ms[1]}", None),
        "put_demand": ("put", f"/v1/plans/{pid}/demand/{ms[0]}/{ms[1]}/2026-10",
                       {"expected_units": 7}),
        "put_purchase": ("put", f"/v1/plans/{pid}/purchase/{sku}/2026-10",
                         {"planned_units": 7}),
        "submit": ("post", f"/v1/plans/{pid}/submit", None),
        "cancel_rev": ("post", f"/v1/plans/{pid}/revs/1/cancel", {"reason": "r"}),
        "mark_current": ("post", f"/v1/plans/{pid}/revs/1/current", None),
        "archive": ("post", f"/v1/plans/{pid}/archive", None),
        "cancel_line": ("post", "/v1/plan-lines/1/cancel", {"reason": "r"}),
        "transition": ("post", "/v1/plan-lines/1/transition", {"to_state": "已确认"}),
        "readiness": ("get", "/v1/readiness", None),
        "health": ("get", "/health", None),
    }


NAMES = list(cases(type("S", (), {"msku_a": ("a", "b"), "sku_a": "c"})(), 1))


@pytest.mark.parametrize("name", NAMES)
def test_an_unknown_query_param_is_400_on_every_write(client, seed, name):
    pid = prepared(client, seed)
    method, path, body = cases(seed, pid)[name]
    kw = {"headers": H(seed.actor), "params": {"typo": "1"}}
    if body is not None:
        kw["json"] = body
    r = getattr(client, method)(path, **kw)
    assert r.status_code == 400, f"{name} 忽略了未声明参数，返回 {r.status_code}：{r.text}"
    assert r.json()["error"] == "unknown_query_param"
    assert r.json()["unknown"] == ["typo"]


@pytest.mark.parametrize("name", NAMES)
def test_the_same_calls_are_not_400_without_the_typo(client, seed, name):
    """★ 靶子的另一半：拿掉那个参数，这些调用不许再以 unknown_query_param 挂掉 ——
    否则上面那条全绿也可能只是因为它们本来就在 400。"""
    pid = prepared(client, seed)
    method, path, body = cases(seed, pid)[name]
    kw = {"headers": H(seed.actor)}
    if body is not None:
        kw["json"] = body
    r = getattr(client, method)(path, **kw)
    assert r.status_code != 400 or r.json()["error"] != "unknown_query_param", r.text
