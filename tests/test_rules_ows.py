"""排货单 → 领星备货单的组单规则（spec 2026-10-12 §3 · CB-D2/D5/D7 · CB-D4 实调）。纯函数。"""
from rules import ows

A, B = "11072", "11094"


def cons(cid, lines, dest_wid=9, channel="oversea", stage="已确认"):
    return {"consignment_id": cid, "channel": channel, "dest_wid": dest_wid, "dest_sid": None, "our_stage": stage,
            "lines": [{"seller_sku": m, "sid": s, "from_wid": w, "sku": k, "units": u} for m, s, w, k, u in lines]}


PRODUCTS = {"SKU-A": 741805, "SKU-B": 741806}
FNSKU = {("M-A", A): "X00A", ("M-C", B): "X00C"}
LOGI = {9: {"logistics_id": 28762, "channel_name": "美森快船", "enabled": True}}


def plan(cs, ext=None, products=PRODUCTS, logi=LOGI):
    return ows.plan_orders(cs, logi, products, FNSKU, ext or {})


def test_one_consignment_two_from_warehouses_is_two_orders():
    p = plan([cons(5, [("M-A", A, 1, "SKU-A", 3), ("M-A", A, 2, "SKU-A", 4)])])
    assert [(o["idem_key"], o["s_wid"], o["r_wid"], o["logistics_id"]) for o in p["orders"]] == [
        ("SCM-CN-5-1", 1, 9, 28762), ("SCM-CN-5-2", 2, 9, 28762)]


def test_product_lines_carry_fnsku_and_sid_when_known_else_merge_by_product():
    """CB-D7：有 fnsku 的带 fnsku + sid；没有的只带 product_id，同货号合并。"""
    p = plan([cons(5, [("M-A", A, 1, "SKU-A", 3), ("M-Z", A, 1, "SKU-A", 2), ("M-Y", B, 1, "SKU-A", 1),
                       ("M-C", B, 1, "SKU-B", 6)])])
    [o] = p["orders"]
    assert o["product_list"] == [
        {"product_id": 741805, "stock_num": 3, "receive_num": 0, "fnsku": "X00A", "sid": A},
        {"product_id": 741805, "stock_num": 3, "receive_num": 0},
        {"product_id": 741806, "stock_num": 6, "receive_num": 0, "fnsku": "X00C", "sid": B},
    ]


def test_payload_is_status_40_new_logistics_format():
    [o] = plan([cons(5, [("M-A", A, 1, "SKU-A", 3)])])["orders"]
    pl = ows.payload(o, sheet_id=7)
    assert pl == {"inbound_order_no": "SCM-CN-5-1", "s_wid": 1, "r_wid": 9, "logistics_id": 28762, "status": 40,
                  "logistics_list_type": 1, "head_logistics_list": {"tax_fee_type": 3},
                  "remark": "scm 排货表 #7 排货单 #5",
                  "product_list": [{"product_id": 741805, "stock_num": 3, "receive_num": 0, "fnsku": "X00A", "sid": A}]}


def test_fba_and_draft_consignments_are_not_submitted():
    p = plan([cons(5, [("M-A", A, 1, "SKU-A", 3)], channel="fba"), cons(6, [("M-A", A, 1, "SKU-A", 3)], stage="草稿")])
    assert p["orders"] == [] and p["skipped_fba"] == [5]


def test_already_created_orders_are_marked_done_and_left_out_of_pending():
    p = plan([cons(5, [("M-A", A, 1, "SKU-A", 3), ("M-A", A, 2, "SKU-A", 4)])], ext={(5, 1): "OWS1"})
    assert [(o["idem_key"], o["ext_no"]) for o in p["orders"]] == [("SCM-CN-5-1", "OWS1"), ("SCM-CN-5-2", None)]
    assert [o["idem_key"] for o in ows.pending(p)] == ["SCM-CN-5-2"]


def test_blockers_name_missing_product_id_and_logistics():
    p = plan([cons(5, [("M-A", A, 1, "SKU-A", 3), ("M-B", A, 1, "SKU-X", 1)], dest_wid=10)])
    assert p["blockers"] == [{"code": "product_id_missing", "sku": "SKU-X"},
                             {"code": "logistics_missing", "dest_wid": 10}]


def test_disabled_logistics_blocks():
    p = plan([cons(5, [("M-A", A, 1, "SKU-A", 3)])], logi={9: {**LOGI[9], "enabled": False}})
    assert p["blockers"] == [{"code": "logistics_disabled", "dest_wid": 9, "logistics_id": 28762}]


def test_confirm_token_changes_with_content_not_with_order_of_input():
    a = plan([cons(5, [("M-A", A, 1, "SKU-A", 3)]), cons(6, [("M-C", B, 2, "SKU-B", 1)])])
    b = plan([cons(6, [("M-C", B, 2, "SKU-B", 1)]), cons(5, [("M-A", A, 1, "SKU-A", 3)])])
    c = plan([cons(5, [("M-A", A, 1, "SKU-A", 4)]), cons(6, [("M-C", B, 2, "SKU-B", 1)])])
    assert ows.confirm_token(a) == ows.confirm_token(b) != ows.confirm_token(c)


# ── 三方仓 sku 编码（10-12 实调：领星拒「第三方sku不能为空」；领星文档 CreateInbound：当仓库是有 api 海外仓必填）──

def test_api_warehouse_orders_carry_the_third_party_code():
    p = plan([cons(5, [("M-A", A, 1, "SKU-A", 3)])])
    ows.apply_sku_codes(p, {9: {741805: ["X-CODE"]}})
    assert p["orders"][0]["product_list"][0]["oversea_product_code"] == "X-CODE" and p["blockers"] == []


def test_unmatched_product_blocks_and_names_sku_and_warehouse():
    p = plan([cons(5, [("M-A", A, 1, "SKU-A", 3), ("M-C", B, 1, "SKU-B", 1)])])
    ows.apply_sku_codes(p, {9: {741805: ["X-CODE"]}})
    assert p["blockers"] == [{"code": "third_party_sku_missing", "dest_wid": 9, "sku": "SKU-B", "product_id": 741806}]


def test_two_codes_for_one_product_is_ambiguous_not_a_guess():
    p = plan([cons(5, [("M-A", A, 1, "SKU-A", 3)])])
    ows.apply_sku_codes(p, {9: {741805: ["X1", "X2"]}})
    assert p["blockers"] == [{"code": "third_party_sku_ambiguous", "dest_wid": 9, "sku": "SKU-A", "product_id": 741805,
                              "codes": ["X1", "X2"]}]


def test_unreadable_mapping_blocks_and_warehouses_without_api_are_untouched():
    p = plan([cons(5, [("M-A", A, 1, "SKU-A", 3)])])
    ows.apply_sku_codes(p, {9: None})
    assert p["blockers"] == [{"code": "sku_match_unavailable", "dest_wid": 9}]
    q = plan([cons(5, [("M-A", A, 1, "SKU-A", 3)])])
    ows.apply_sku_codes(q, {})                                     # 不是有 API 的三方仓：不查、不带
    assert "oversea_product_code" not in q["orders"][0]["product_list"][0] and q["blockers"] == []


def test_codes_change_the_confirm_token():
    p, q = plan([cons(5, [("M-A", A, 1, "SKU-A", 3)])]), plan([cons(5, [("M-A", A, 1, "SKU-A", 3)])])
    ows.apply_sku_codes(p, {9: {741805: ["X1"]}})
    ows.apply_sku_codes(q, {9: {741805: ["X2"]}})
    assert ows.confirm_token(p) != ows.confirm_token(q)
