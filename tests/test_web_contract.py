"""前端契约样本：真后端响应 ↔ web/src/testing/fixtures/contract/*.json。

★ 为什么有它：前端的桩与夹具都是手写的，写错的类型会与界面「互相印证」地一起绿 ——
  purchaser_id / opt_uid / wid 后端是整数、前端一直按字符串写，编辑器 .trim() 一个数就炸（final review C1）。
  这里用真端点造出响应，与提交在 web 里的样本比**类型骨架**；web 侧的契约测试拿同一批样本跑编辑器与页面。

★ 比骨架不比值：po_id、时间戳、token 每次跑都不同，类型不许变。
  样本要重写（后端形状有意改了）：`SCM_WRITE_CONTRACT=1 .venv/bin/python -m pytest tests/test_web_contract.py`，
  再看 git diff —— 测试自己从不悄悄改文件。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path

from api.ui.purchase_options import history_query_dep
from shared.pg_client import pg_conn
from tests.helpers import H, advance, mint

DIR = Path(__file__).resolve().parents[1] / "web" / "src" / "testing" / "fixtures" / "contract"
WRITE = os.environ.get("SCM_WRITE_CONTRACT") == "1"


def skeleton(v):
    if isinstance(v, dict):
        return {k: skeleton(x) for k, x in sorted(v.items())}
    if isinstance(v, list):
        out = []
        for s in (skeleton(x) for x in v):
            if s not in out:
                out.append(s)
        return ["list", out]
    return type(v).__name__


def _scrub(v):
    """L8（tests/test_layering_web.py）：web/src 里不许出现领星的英文名。只改写值，不碰键 ——
    骨架只看键与类型，改值不影响比对；键里若出现了，L8 照样会红。"""
    if isinstance(v, dict):
        return {k: _scrub(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_scrub(x) for x in v]
    if isinstance(v, str):
        return re.sub("lingxing", "erp", v, flags=re.IGNORECASE)
    return v


def _line(cur, seed, total=10):
    _, lid = mint(cur, seed.actor, seed.sku_a, total=total)
    cur.execute("UPDATE plan_line SET demand_by_seller=%s::jsonb WHERE line_id=%s",
                (json.dumps({seed.seller_a: {"units": 5, "basis": "human"}}), lid))
    return lid


def _body(seed, lid, units):
    return {"supplier_id": "8085", "purchaser_id": 111, "opt_uid": 10925905, "qc_type": 2,
            "lines": [{"sku": seed.sku_a, "wid": seed.wid, "units": units, "price": "2.5",
                       "sources": [{"line_id": lid, "seller_id": seed.seller_a, "units": units}]}]}


def _place(client, seed, po_id):
    tok = client.get(f"/v1/purchase-orders/{po_id}").json()["confirm_token"]
    return client.post(f"/v1/purchase-orders/{po_id}/place", json={"confirm_token": tok}, headers=H(seed.actor)).json()


def test_contract_samples_match_backend_types(client, seed, lingxing):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO supplier VALUES ('8085','01[吉信德]',now())")
        l_draft, l_nv, l_placed = _line(cur, seed), _line(cur, seed), _line(cur, seed)
    post = lambda b: client.post("/v1/purchase-orders", json=b, headers=H(seed.actor)).json()["po_id"]
    draft = post(_body(seed, l_draft, 4))
    nv = post(_body(seed, l_nv, 10))
    placed = post(_body(seed, l_placed, 10))
    lingxing.enqueue("create_purchase_order", "unknown_absent")
    assert _place(client, seed, nv)["outcome"] == "needs_verify"
    assert _place(client, seed, placed)["outcome"] == "placed"
    client.app.dependency_overrides[history_query_dep] = lambda: (
        lambda sql, params=None: [("185", "777", "王五", 3, dt.date(2026, 10, 7))])

    got = {
        "po-draft": client.get(f"/v1/purchase-orders/{draft}").json(),
        "po-needs-verify": client.get(f"/v1/purchase-orders/{nv}").json(),
        "po-placed": client.get(f"/v1/purchase-orders/{placed}").json(),
        "po-list": client.get("/v1/purchase-orders").json(),
        "preflight": client.get(f"/v1/purchase-orders/{draft}/preflight").json(),
        "timeline-needs-verify": client.get(f"/v1/purchase-orders/{nv}/timeline").json(),
        "attention": client.get("/v1/attention").json(),
        "purchase-options": client.get("/v1/purchase-options").json(),
        "plan-lines": client.get("/v1/plan-lines?orderable=1").json(),
        "line-trace": client.get(f"/v1/plan-lines/{l_draft}/trace").json(),
    }
    # 样本本身要有内容：空表的骨架什么也约束不了
    assert got["po-list"]["purchase_orders"] and got["plan-lines"]["lines"] and got["attention"]["items"]
    assert got["timeline-needs-verify"]["conclusion"] and got["purchase-options"]["purchasers"]

    _compare(got)


def _compare(got: dict) -> None:
    if WRITE:
        DIR.mkdir(parents=True, exist_ok=True)
        for name, body in got.items():
            (DIR / f"{name}.json").write_text(json.dumps(_scrub(body), ensure_ascii=False, indent=2) + "\n", "utf-8")
    drift = {}
    for name, body in got.items():
        path = DIR / f"{name}.json"
        assert path.exists(), f"缺契约样本 {path}；用 SCM_WRITE_CONTRACT=1 生成后提交"
        want = skeleton(json.loads(path.read_text("utf-8")))
        if skeleton(body) != want:
            drift[name] = {"backend": skeleton(body), "committed": want}
    assert not drift, f"后端响应的类型骨架与 web 契约样本不符（改了形状就重写样本并改前端）：{json.dumps(drift, ensure_ascii=False)}"


def test_contract_submit_samples(client, seed):
    """提交结果页（R1 · D5 · 裁定 A/B）与 409 lines_in_purchase 的形状。
    ★ 每个桶都要有内容：空列表的骨架是 ["list", []]，约束不了明细的键与类型。"""
    a = H(seed.actor)
    put = lambda path, body: client.put(f"/v1/plans/{pid}/{path}", json=body, headers=a)
    pid = client.post("/v1/plans", json={"title": "契约", "period_start": "2026-10-01", "months": 3},
                      headers=a).json()["plan_id"]
    for ms in (seed.msku_a, seed.msku_c, seed.msku_d):
        assert client.post(f"/v1/plans/{pid}/claims", json={"seller_sku": ms[0], "sid": ms[1]},
                           headers=a).status_code == 200
    put(f"demand/{seed.msku_a[0]}/{seed.msku_a[1]}/2026-10", {"expected_units": 120})
    put(f"demand/{seed.msku_c[0]}/{seed.msku_c[1]}/2026-10", {"expected_units": 120})
    put(f"demand/{seed.msku_d[0]}/{seed.msku_d[1]}/2026-10", {"expected_units": 50})
    for sku, period, units in [(seed.sku_a, "2026-10", 500), (seed.sku_a, "2026-11", 100),
                               (seed.sku_a, "2026-12", 50), (seed.sku_b, "2026-10", 300)]:
        put(f"purchase/{sku}/{period}", {"planned_units": units})
    assert client.post(f"/v1/plans/{pid}/submit", headers=a).status_code == 200
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT sku, period_start, line_id FROM plan_line WHERE plan_id = %s", (pid,))
        lid = {(r[0], r[1].strftime("%Y-%m")): r[2] for r in cur.fetchall()}
        advance(cur, lid[(seed.sku_a, "2026-10")], "已确认", "已下单", actor=seed.actor)
        advance(cur, lid[(seed.sku_a, "2026-12")], "已确认", "已下单", "准备排货", "已排货", "已完结",
                actor=seed.actor)
    put(f"demand/{seed.msku_a[0]}/{seed.msku_a[1]}/2026-10", {"expected_units": 130})  # split_frozen
    put(f"purchase/{seed.sku_a}/2026-11", {"planned_units": 150})                      # replaced
    put(f"purchase/{seed.sku_a}/2026-12", {"planned_units": None})                     # left_completed
    put(f"purchase/{seed.sku_b}/2026-10", {"planned_units": None})                     # removed
    put(f"purchase/{seed.sku_b}/2026-11", {"planned_units": 0})                        # zero_purchase
    put(f"purchase/{seed.sku_b}/2026-12", {"planned_units": 80})                       # added
    r = client.post(f"/v1/plans/{pid}/submit", headers=a)
    assert r.status_code == 200, r.text
    resubmit = r.json()
    for k in ("kept", "replaced", "added", "removed", "split_frozen", "left_completed"):
        assert resubmit[k]["lines"], k
    assert {s["reason"] for s in resubmit["skipped"]} >= {"empty_purchase", "zero_purchase"}

    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO supplier VALUES ('8085','01[吉信德]',now()) ON CONFLICT DO NOTHING")
        cur.execute("SELECT line_id FROM plan_line WHERE plan_id = %s AND sku = %s"
                    " AND period_start = '2026-12-01' AND state = '已提交'", (pid, seed.sku_b))
        lid_b = cur.fetchone()[0]
    assert client.post("/v1/purchase-orders", json=_body(seed, lid_b, 10) | {"lines": [
        {"sku": seed.sku_b, "wid": seed.wid, "units": 10, "price": "2.5",
         "sources": [{"line_id": lid_b, "seller_id": seed.msku_d[1], "units": 10}]}]},
        headers=a).status_code == 201
    put(f"purchase/{seed.sku_b}/2026-12", {"planned_units": 90})
    r = client.post(f"/v1/plans/{pid}/submit", headers=a)
    assert r.status_code == 409 and r.json()["error"] == "lines_in_purchase", r.text
    blocked = r.json()
    assert any(x["po_ids"] for x in blocked["lines"])

    _compare({"submit-resubmit": resubmit, "submit-lines-in-purchase": blocked})


def test_skeleton_catches_string_vs_int():
    """★ 证伪：骨架真的分得开 111 与 "111"。"""
    assert skeleton({"purchaser_id": 111}) != skeleton({"purchaser_id": "111"})
    assert skeleton([{"wid": 1}]) != skeleton([{"wid": "1"}])
