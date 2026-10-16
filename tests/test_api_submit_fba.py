"""C-c1 FBA 排货单 → 领星 FBA 发货计划（spec 2026-10-13-dispatch-submit-fba-plan · CC-D1~D4）。领星走替身。"""
import pytest

from shared.pg_client import pg_conn
from tests.helpers import H
from tests.test_api_sheets import BOX, FakeStock, new_sheet, packing_url, put, stock, sync_live, world  # noqa: F401
from tests.test_api_submit_ows import preview, submit, stages  # noqa: F401


@pytest.fixture
def confirmed_fba(client, seed, world, stock, lingxing):  # noqa: F811
    """已确认的平台仓 · FBA 表：一张 FBA 排货单（店 seller_a · 本地仓 seed.wid · 5 件 · 装箱已填）。"""
    lingxing.set_listing(seed.msku_a[1], seed.msku_a[0], "X00AAA")     # ★ AR-D1：fnsku 现查领星 listing
    stock["s"] = FakeStock(buckets={(seed.sku_a, seed.wid): {"shared": 80}})
    sync_live(lingxing, stock["s"])
    sid = new_sheet(client, seed)
    put(client, seed, sid, line_id=world["line_id"], units=5)
    client.put(packing_url(sid, seed), json=BOX, headers=H(seed.actor))
    seen = client.get(f"/v1/sheets/{sid}").json()["watermark"]
    assert client.post(f"/v1/sheets/{sid}/confirm", json={"seen_watermark": seen}, headers=H(seed.actor)).status_code == 200
    return sid


def plans(lingxing):
    return [x for g in lingxing._fba.values() for x in g["list"]]


def test_preview_is_one_batch_with_reference_in_remark(client, seed, confirmed_fba):
    p = preview(client, confirmed_fba)
    [o] = p["orders"]
    assert p["blockers"] == [] and o["idem_key"].startswith("SCM-FBA-") and o["ext_no"] is None
    assert o["product_list"][0]["fnsku"] == "X00AAA" and o["product_list"][0]["shipment_plan_quantity"] == 5


def test_submit_creates_the_plan_locks_stock_then_dispatched(client, seed, confirmed_fba, lingxing):
    r = submit(client, seed, confirmed_fba)
    assert r.status_code == 200, r.text
    assert [x["outcome"] for x in r.json()["results"]] == ["created"]
    [g] = lingxing._fba.values()
    assert g["remark"] == preview(client, confirmed_fba)["orders"][0]["idem_key"]
    assert [x["lock_status"] for x in plans(lingxing)] == [2]
    assert stages(client, confirmed_fba) == {"fba": "已投送"}
    [e] = client.get(f"/v1/sheets/{confirmed_fba}").json()["ext_refs"]
    assert (e["kind"], e["ext_no"], e["locked"]) == ("fba_plan", g["seq"], True)


def test_lock_failure_keeps_it_confirmed_and_resubmit_only_relocks(client, seed, confirmed_fba, lingxing):
    lingxing.enqueue("fba_plan_lock", "rejected")
    r = submit(client, seed, confirmed_fba).json()
    assert [x["outcome"] for x in r["results"]] == ["lock_failed"]
    assert stages(client, confirmed_fba) == {"fba": "已确认"}
    assert preview(client, confirmed_fba)["to_lock"] and len(lingxing.fba_creates) == 1
    r = submit(client, seed, confirmed_fba).json()
    assert [x["outcome"] for x in r["results"]] == ["locked"] and len(lingxing.fba_creates) == 1
    assert stages(client, confirmed_fba) == {"fba": "已投送"}


def test_unknown_and_absent_opens_needs_verify_and_never_retries(client, seed, confirmed_fba, lingxing):
    lingxing.enqueue("create_fba_plan", "unknown_absent")
    r = submit(client, seed, confirmed_fba).json()
    assert [x["outcome"] for x in r["results"]] == ["needs_verify"] and len(lingxing.fba_creates) == 1
    aid = r["results"][0]["attention_id"]
    idem = preview(client, confirmed_fba)["orders"][0]["idem_key"]
    lingxing.fba_put({"remark": idem, "product_list": [{"sid": int(seed.seller_a), "msku": seed.msku_a[0],
                                                         "fnsku": "X00AAA", "shipment_plan_quantity": 5, "wid": seed.wid}]})
    v = client.post(f"/v1/sheets/{confirmed_fba}/ext-refs/verify", json={"attention_id": aid}, headers=H(seed.actor))
    assert v.status_code == 200 and v.json()["needs_lock"] is True, v.text
    r = submit(client, seed, confirmed_fba).json()
    assert [x["outcome"] for x in r["results"]] == ["locked"] and stages(client, confirmed_fba) == {"fba": "已投送"}


def test_unknown_but_created_is_found_by_lookup_not_created_twice(client, seed, confirmed_fba, lingxing):
    lingxing.enqueue("create_fba_plan", "unknown_created")
    r = submit(client, seed, confirmed_fba).json()
    assert [x["outcome"] for x in r["results"]] == ["adopted"] and len(lingxing._fba) == 1


def test_missing_fnsku_blocks_before_any_call(client, seed, confirmed_fba, lingxing):
    lingxing.set_listing(seed.msku_a[1], seed.msku_a[0], "")          # 10-13 实测：listing 在售但 FNSKU 为空
    assert [b["code"] for b in preview(client, confirmed_fba)["blockers"]] == ["fnsku_missing"]
    r = submit(client, seed, confirmed_fba)
    assert r.status_code == 409 and r.json()["error"] == "submission_blocked" and lingxing.fba_creates == []


def test_cancel_releases_stock_marks_plan_and_names_manual_void(client, seed, confirmed_fba, lingxing):
    submit(client, seed, confirmed_fba)
    r = client.post(f"/v1/sheets/{confirmed_fba}/cancel", json={"reason": "换方案"}, headers=H(seed.actor))
    assert r.status_code == 200, r.text
    [mv] = r.json()["manual_void"]
    [p] = plans(lingxing)
    assert (p["lock_status"], p["remark"]) == (1, "SCM 已取消 请作废") and mv["plans"] == [p["order_sn"]]
    d = client.get(f"/v1/sheets/{confirmed_fba}").json()
    assert d["state"] == "已取消" and all(e["voided_at"] for e in d["ext_refs"])


def test_cancel_refused_when_a_plan_is_already_processed(client, seed, confirmed_fba, lingxing):
    submit(client, seed, confirmed_fba)
    [seq] = list(lingxing._fba)
    lingxing.set_fba_status(seq, 10)
    r = client.post(f"/v1/sheets/{confirmed_fba}/cancel", json={"reason": "x"}, headers=H(seed.actor))
    assert r.status_code == 409 and r.json()["error"] == "fba_plan_not_cancellable", r.text
    assert [p["lock_status"] for p in plans(lingxing)] == [2]                 # 一点没动


def test_receipt_reads_back_the_plan_status(client, seed, confirmed_fba, lingxing):
    submit(client, seed, confirmed_fba)
    [seq] = list(lingxing._fba)
    lingxing.set_fba_status(seq, 10)
    r = client.post(f"/v1/sheets/{confirmed_fba}/receipt", headers=H(seed.actor)).json()
    assert [x["stage"] for x in r["results"]] == ["已处理"] and r["archived"] == []
    [e] = client.get(f"/v1/sheets/{confirmed_fba}").json()["ext_refs"]
    assert e["erp_stage"] == "已处理"


def test_fnsku_is_read_live_from_listing_not_the_mirror(client, seed, confirmed_fba, lingxing):
    """AR-D1：镜像里 fnsku 为空（CH 隔天才刷），领星 listing 现在有 → 照领星的提交。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE msku_bridge SET fnsku=NULL WHERE seller_sku=%s AND sid=%s", seed.msku_a)
    p = preview(client, confirmed_fba)
    assert p["blockers"] == [] and p["orders"][0]["product_list"][0]["fnsku"] == "X00AAA"


def test_unreadable_listing_blocks_and_names_it(client, seed, confirmed_fba, lingxing):
    lingxing.enqueue("amazon_listing", "not_sent")
    assert [b["code"] for b in preview(client, confirmed_fba)["blockers"]] == ["fnsku_unavailable"]
