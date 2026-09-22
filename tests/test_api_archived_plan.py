"""归档的计划一律拒写（409 plan_archived），读照旧。

★ 为什么这是一堵墙而不是一个礼貌：`POST /plans/{id}/archive` 在**同一个事务**里
  释放该计划的全部 msku 占用。归档后还能写的话，紧接着的一次认领就把刚释放的那些
  又扣回去 —— 而 `GET /v1/plans` 默认不显示已归档的计划，于是 msku 被一张
  「列表里根本看不见的计划」占着，别人怎么也认领不到。判据③ 就是这面墙。
"""
import pytest
from helpers import H, prepared

from shared.pg_client import pg_conn


def archived_plan(client, seed):
    """一张提交过、随后归档的计划 —— 八条写路径都能打到它。"""
    pid = prepared(client, seed)
    assert client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor)).status_code == 200
    r = client.post(f"/v1/plans/{pid}/archive", headers=H(seed.actor))
    assert r.status_code == 200 and r.json()["released"] == 2, r.text
    return pid


#: ★ 两个路由各自都要打到 —— plans.py 与 submit.py 是两份代码，
#: 只测一边就只有一边挡得住，而两边看起来都「有测试」。
WRITES = ["claim", "release", "put_demand", "put_purchase",      # plans.py
          "submit", "cancel_rev", "mark_current", "archive_again"]  # submit.py / plans.py


def a_write(seed, name) -> tuple[str, str, dict | None]:
    ms, sku = seed.msku_a, seed.sku_a
    return {
        "claim": ("post", "/claims", {"seller_sku": ms[0], "sid": ms[1]}),
        "release": ("delete", f"/claims/{ms[0]}/{ms[1]}", None),
        "put_demand": ("put", f"/demand/{ms[0]}/{ms[1]}/2026-10", {"expected_units": 7}),
        "put_purchase": ("put", f"/purchase/{sku}/2026-10", {"planned_units": 7}),
        "submit": ("post", "/submit", None),
        "cancel_rev": ("post", "/revs/1/cancel", {"reason": "不做了"}),
        "mark_current": ("post", "/revs/1/current", None),
        "archive_again": ("post", "/archive", None),
    }[name]


@pytest.mark.parametrize("name", WRITES)
def test_every_write_on_an_archived_plan_is_409(client, seed, name):
    pid = archived_plan(client, seed)
    method, suffix, body = a_write(seed, name)
    kw = {"headers": H(seed.actor)}
    if body is not None:
        kw["json"] = body
    r = getattr(client, method)(f"/v1/plans/{pid}{suffix}", **kw)
    assert r.status_code == 409, f"{name} 在归档计划上返回了 {r.status_code}：{r.text}"
    assert r.json()["error"] == "plan_archived"
    assert r.json()["plan_id"] == pid and r.json()["archived_at"]


@pytest.mark.parametrize("name", WRITES)
def test_the_same_writes_go_through_before_archiving(client, seed, name):
    """★ 新守卫一开始就是红的有两种成因：抓到了真问题，和误伤了好东西 ——
    两者同形。这条把「没归档时这八条路都走得通」钉住，
    否则上面那条全绿也可能只是因为它们本来就在报错。"""
    pid = prepared(client, seed)
    assert client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor)).status_code == 200
    method, suffix, body = a_write(seed, name)
    kw = {"headers": H(seed.actor)}
    if body is not None:
        kw["json"] = body
    r = getattr(client, method)(f"/v1/plans/{pid}{suffix}", **kw)
    assert r.status_code != 409 or r.json()["error"] != "plan_archived", \
        f"{name} 在没归档的计划上也被当成归档了：{r.text}"


def test_reads_on_an_archived_plan_still_work(client, seed):
    """★ 拒写不是拒读：归档是为了「不再改」，不是为了「查不到」——
    查不到的话，占用记录、旧版本、提交留痕会跟着一起消失在界面上。"""
    pid = archived_plan(client, seed)
    assert client.get(f"/v1/plans/{pid}/grid", headers=H(seed.actor)).status_code == 200
    assert client.get(f"/v1/plans/{pid}/revs", headers=H(seed.actor)).status_code == 200
    assert client.get(f"/v1/plans/{pid}/diff", params={"from": 1, "to": 1},
                      headers=H(seed.actor)).status_code == 200
    listed = client.get("/v1/plans", params={"archived": "true"},
                        headers=H(seed.actor)).json()["plans"]
    assert [p["plan_id"] for p in listed] == [pid]


def test_occupancy_released_by_archive_stays_released(client, seed):
    """★ 复核实测出来的那条链路：归档释放了占用 → 在归档的计划上再认领一次
    （当时返回 200）→ 占用被扣回去 → 别的计划再也认领不到，而列表默认看不见持有者。
    """
    pid = archived_plan(client, seed)
    again = client.post(f"/v1/plans/{pid}/claims",
                        json={"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]},
                        headers=H(seed.actor))
    assert again.status_code == 409 and again.json()["error"] == "plan_archived"
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM msku_claim"
                    " WHERE plan_id = %s AND released_at IS NULL", (pid,))
        assert cur.fetchone()[0] == 0, "★ 归档释放的占用被写回去了"
    p2 = client.post("/v1/plans", json={"title": "下一张", "period_start": "2026-11-01"},
                     headers=H(seed.actor)).json()["plan_id"]
    ok = client.post(f"/v1/plans/{p2}/claims",
                     json={"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]},
                     headers=H(seed.actor))
    assert ok.status_code == 200, ok.text


def test_a_missing_plan_is_still_404_not_409(client, seed):
    """★ 「没有这张计划」与「有、但已封存」分得开：合成一个码，
    人会照着 409 的提示去找一张根本不存在的计划。"""
    r = client.post("/v1/plans/999999/claims",
                    json={"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]},
                    headers=H(seed.actor))
    assert r.status_code == 404 and r.json()["error"] == "plan_not_found"
