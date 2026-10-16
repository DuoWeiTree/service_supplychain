"""FBA 排货单 → 领星 FBA 发货计划批次（spec 2026-10-13-dispatch-submit-fba-plan §1~§3 · CC-D3）。纯函数。"""
from rules import fba_plan

A = "11072"


def cons(cid, lines, stage="已确认"):
    return {"consignment_id": cid, "channel": "fba", "dest_wid": None, "dest_sid": A, "our_stage": stage,
            "lines": [{"seller_sku": m, "sid": s, "from_wid": w, "sku": k, "units": u} for m, s, w, k, u in lines]}


FNSKU = {("M-A", A): "X00A", ("M-B", A): "X00B"}
BOX = {("M-A", A, 1): {"box_count": 2, "units_per_box": 5}, ("M-B", A, 1): {"box_count": 1, "units_per_box": 3},
       ("M-A", A, 2): {"box_count": 1, "units_per_box": 4}}


def plan(cs, ext=None, fnsku=FNSKU, box=BOX):
    return fba_plan.plan_orders(cs, fnsku, box, ext or {})


def test_one_batch_per_consignment_and_from_warehouse_with_reference_in_remark():
    p = plan([cons(5, [("M-A", A, 1, "SKU-A", 10), ("M-B", A, 1, "SKU-B", 3), ("M-A", A, 2, "SKU-A", 4)])])
    assert [(o["idem_key"], o["s_wid"], o["dest_sid"]) for o in p["orders"]] == [
        ("SCM-FBA-5-1", 1, A), ("SCM-FBA-5-2", 2, A)]
    pl = fba_plan.payload(p["orders"][0], sheet_id=7)
    assert pl == {"remark": "SCM-FBA-5-1", "product_list": [
        {"sid": 11072, "packing_type": 2, "msku": "M-A", "fnsku": "X00A", "shipment_plan_quantity": 10,
         "quantity_in_case": 5, "box_num": 2, "wid": 1},
        {"sid": 11072, "packing_type": 2, "msku": "M-B", "fnsku": "X00B", "shipment_plan_quantity": 3,
         "quantity_in_case": 3, "box_num": 1, "wid": 1}]}


def test_missing_fnsku_and_packing_are_named_before_any_call():
    p = plan([cons(5, [("M-Z", A, 1, "SKU-Z", 1), ("M-A", A, 3, "SKU-A", 2)])])
    assert p["blockers"] == [{"code": "fnsku_missing", "seller_sku": "M-Z", "sid": A},
                             {"code": "packing_missing", "seller_sku": "M-Z", "sid": A, "from_wid": 1},
                             {"code": "packing_missing", "seller_sku": "M-A", "sid": A, "from_wid": 3}]


def test_only_confirmed_or_dispatched_fba_consignments_and_created_ones_are_not_pending():
    p = plan([cons(5, [("M-A", A, 1, "SKU-A", 10)]), cons(6, [("M-A", A, 2, "SKU-A", 4)], stage="草稿")],
             ext={(5, 1): "RP1"})
    assert [(o["idem_key"], o["ext_no"]) for o in p["orders"]] == [("SCM-FBA-5-1", "RP1")]
    assert fba_plan.pending(p) == []


def test_token_changes_with_content():
    a = plan([cons(5, [("M-A", A, 1, "SKU-A", 10)])])
    b = plan([cons(5, [("M-A", A, 1, "SKU-A", 9)])])
    assert fba_plan.confirm_token(a) != fba_plan.confirm_token(b)


def test_plan_status_maps_to_lingxing_words():
    assert [fba_plan.stage_of(s) for s in (-5, 0, 5, 10, "5")] == [
        ("已驳回", -1), ("待审核", 1), ("待处理", 2), ("已处理", 3), ("待处理", 2)]
    assert fba_plan.stage_of(7) is None


def test_batch_stage_is_the_slowest_plan_and_rejection_wins():
    assert fba_plan.batch_stage([{"status": 10}, {"status": 5}]) == ("待处理", 2)
    assert fba_plan.batch_stage([{"status": 10}, {"status": -5}]) == ("已驳回", -1)
    assert fba_plan.batch_stage([{"status": 10}]) == ("已处理", 3)
    assert fba_plan.batch_stage([]) is None and fba_plan.batch_stage([{"status": 99}]) is None
