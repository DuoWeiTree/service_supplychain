import pytest

from rules.purchase import (
    NoSellerBasis,
    confirm_token,
    po_digest,
    preflight,
    seller_caps,
    target_state,
    transition_path,
)


def test_caps_sum_to_total_and_match_sql_tie_break():
    caps = seller_caps(24, {"A": {"units": 12, "basis": "human"}, "B": {"units": 11, "basis": "human"}})
    assert caps == {"A": 13, "B": 11}
    # 并列余数按 seller_id 升序拿多出来的那一件（与 012 seller_caps() 一致）
    caps = seller_caps(1, {"B": {"units": 1, "basis": "human"}, "A": {"units": 1, "basis": "human"}})
    assert caps == {"A": 1, "B": 0}


@pytest.mark.parametrize("demand,named", [
    ({}, []),
    ({"A": {"units": 0, "basis": "human"}}, ["A"]),
    ({"A": {"units": 3, "basis": "unknown"}, "B": {"units": 2, "basis": "human"}}, ["A"]),
    ({"A": {"units": None, "basis": "system"}}, ["A"]),
    ({"A": {"units": True, "basis": "human"}}, ["A"]),
])
def test_no_basis_names_the_sellers(demand, named):
    with pytest.raises(NoSellerBasis) as e:
        seller_caps(10, demand)
    assert e.value.sellers == named


@pytest.mark.parametrize("total,conf,ordd,want", [
    (100, 0, 0, "已提交"), (100, 60, 0, "已提交"), (100, 100, 0, "已确认"),
    (100, 100, 60, "已确认"), (100, 100, 100, "已下单"),
])
def test_target_state(total, conf, ordd, want):
    assert target_state(total, conf, ordd) == want


@pytest.mark.parametrize("cur,tgt,path", [
    ("已提交", "已下单", [("已提交", "已确认"), ("已确认", "已下单")]),
    ("已确认", "已提交", [("已确认", "已提交")]),
    ("已下单", "已提交", [("已下单", "已提交")]),
    ("已下单", "已确认", [("已下单", "已确认")]),
    ("已确认", "已确认", []),
])
def test_transition_path_uses_whitelisted_edges(cur, tgt, path):
    assert transition_path(cur, tgt) == path


def test_preflight_lists_every_failing_gate_at_once():
    gates = preflight({"supplier_id": None, "purchaser_id": 111, "opt_uid": 0, "qc_type": 2,
                       "lines": [{"line_no": 1, "sku": "S", "wid": None, "units": 5, "price": None,
                                  "sources_units": 3, "unplanned_reason": ""}]})
    failed = {g["gate"] for g in gates if not g["ok"]}
    assert failed == {"supplier", "opt_uid", "line_wid", "line_price", "line_unplanned_reason"}


def test_preflight_accepts_price_read_back_as_text():
    gates = preflight({"supplier_id": "8085", "purchaser_id": 111, "opt_uid": 1, "qc_type": 2,
                       "lines": [{"line_no": 1, "sku": "S", "wid": 1, "units": 5, "price": "2.0000",
                                  "sources_units": 5, "unplanned_reason": None}]})
    assert all(g["ok"] for g in gates)


def test_preflight_empty_po_fails():
    gates = preflight({"supplier_id": "8085", "purchaser_id": 111, "opt_uid": 1, "qc_type": 2, "lines": []})
    assert [g["gate"] for g in gates if not g["ok"]] == ["lines"]


def test_digest_changes_with_content_and_token_with_digest():
    a = {"supplier_id": "8085", "lines": [{"sku": "S", "units": 5}]}
    b = {"supplier_id": "8085", "lines": [{"sku": "S", "units": 6}]}
    assert po_digest(a) != po_digest(b)
    assert po_digest(a) == po_digest({"lines": [{"units": 5, "sku": "S"}], "supplier_id": "8085"})
    assert confirm_token(1, po_digest(a), "salt") != confirm_token(1, po_digest(b), "salt")


from decimal import Decimal

from rules.purchase import create_order_payload, extract_order_sn, find_by_custom_sn


def test_payload_carries_idem_key_and_lines():
    po = {"custom_order_sn": "SCM-PO-7", "supplier_id": "8085", "purchaser_id": 111, "opt_uid": 1,
          "qc_type": 2, "lines": [{"sku": "S1", "wid": 1001, "units": 30, "price": "12.5",
                                   "expect_arrive_date": "2026-12-01"}]}
    p = create_order_payload(po)
    assert p["custom_order_sn"] == "SCM-PO-7" and p["supplier_id"] == 8085 and p["wid"] == 1001
    assert p["product_list"] == [{"sku": "S1", "wid": 1001, "quantity_real": 30, "price": Decimal("12.5"),
                                  "expect_arrive_time": "2026-12-01"}]


def test_payload_multi_warehouse_header_wid_is_zero():
    po = {"custom_order_sn": "X", "supplier_id": "8085", "purchaser_id": 1, "opt_uid": 1, "qc_type": 2,
          "lines": [{"sku": "A", "wid": 1, "units": 1, "price": "1", "expect_arrive_date": None},
                    {"sku": "B", "wid": 2, "units": 1, "price": "1", "expect_arrive_date": None}]}
    assert create_order_payload(po)["wid"] == 0      # B7：实测单头 wid=0 是多仓采购的形态


@pytest.mark.parametrize("body,want", [
    ({"data": {"order_sn": "PO1"}}, "PO1"),
    ({"data": [{"order_sn": "PO2"}]}, "PO2"),
    ({"data": {}}, None), ({}, None),
])
def test_extract_order_sn_shapes(body, want):
    assert extract_order_sn(body) == want


def test_find_by_custom_sn_ignores_others():
    rows = [{"order_sn": "PO1", "custom_order_sn": "PO1"}, {"order_sn": "PO2", "custom_order_sn": "SCM-PO-3"}]
    assert find_by_custom_sn(rows, "SCM-PO-3")["order_sn"] == "PO2"
    assert find_by_custom_sn(rows, "SCM-PO-4") is None
