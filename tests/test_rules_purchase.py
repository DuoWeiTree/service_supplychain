import pytest

from rules.purchase import (
    confirm_token,
    po_digest,
    preflight,
    stray_sellers,
    target_state,
    transition_path,
)


@pytest.mark.parametrize("demand,sids,named", [
    # PB-D22：期望为空 / 0 / unknown 都不挡 —— 只看店在不在记录里
    ({"A": {"units": None, "basis": "unknown"}}, ["A"], []),
    ({"A": {"units": 0, "basis": "human"}, "B": {"units": 3, "basis": "system"}}, ["A", "B"], []),
    ({"A": {"units": 5, "basis": "human"}}, ["A", "Z", "Y"], ["Y", "Z"]),
    ({}, ["A"], ["A"]),
])
def test_stray_sellers(demand, sids, named):
    assert stray_sellers(demand, sids) == named


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

from rules.purchase import create_order_payload, extract_order_sn, orders_by_custom_sn


def test_payload_carries_idem_key_and_lines():
    po = {"custom_order_sn": "SCM-PO-7", "supplier_id": "8085", "purchaser_id": 111, "opt_uid": 1,
          "qc_type": 2, "lines": [{"sku": "S1", "wid": 1001, "units": 30, "price": "12.5",
                                   "expect_arrive_date": "2026-12-01"}]}
    p = create_order_payload(po)
    # PB-D26：我们手里的是系统 id —— 发 wid 实收「仓库不存在」（erp_call 4）
    assert p["custom_order_sn"] == "SCM-PO-7" and p["sys_supplier_id"] == 8085 and p["sys_wid"] == 1001
    assert "wid" not in p and "supplier_id" not in p
    assert p["product_list"] == [{"sku": "S1", "quantity_real": 30, "price": Decimal("12.5"),
                                  "expect_arrive_time": "2026-12-01"}]


def test_payload_refuses_multi_warehouse():
    # PB-D25：单头 wid=0 实收「仓库参数错误」（erp_call 3）—— 多仓根本不该走到组包
    po = {"custom_order_sn": "X", "supplier_id": "8085", "purchaser_id": 1, "opt_uid": 1, "qc_type": 2,
          "lines": [{"sku": "A", "wid": 1, "units": 1, "price": "1", "expect_arrive_date": None},
                    {"sku": "B", "wid": 2, "units": 1, "price": "1", "expect_arrive_date": None}]}
    with pytest.raises(ValueError, match="只能一个仓"):
        create_order_payload(po)


def test_preflight_single_wid_gate():
    from rules.purchase import preflight
    base = {"supplier_id": "1", "purchaser_id": 1, "opt_uid": 1, "qc_type": 2, "source_line_states": []}
    line = {"sku": "A", "units": 1, "sources_units": 1, "price": "1", "line_no": 1}
    two = preflight({**base, "lines": [{**line, "wid": 1}, {**line, "line_no": 2, "wid": 2}]})
    one = preflight({**base, "lines": [{**line, "wid": 1}, {**line, "line_no": 2, "wid": 1}]})
    g2 = next(g for g in two if g["gate"] == "single_wid")
    assert g2 == {"gate": "single_wid", "ok": False, "detail": {"wids": [1, 2]}}
    assert next(g for g in one if g["gate"] == "single_wid")["ok"] is True


@pytest.mark.parametrize("body,want", [
    ({"code": 500, "message": "内部错误", "error_details": "仓库参数错误"}, "内部错误：仓库参数错误"),
    ({"code": "403", "msg": "授权失效"}, "授权失效"),
    ({"code": 1, "message": "x", "error_details": "x"}, "x"),
])
def test_lx_msg_keeps_error_details(body, want):
    from erp.transport import _lx_msg
    assert _lx_msg(body) == want


@pytest.mark.parametrize("body,want", [
    ({"data": {"order_sn": "PO1"}}, "PO1"),
    ({"data": [{"order_sn": "PO2"}]}, "PO2"),
    ({"data": {}}, None), ({}, None),
])
def test_extract_order_sn_shapes(body, want):
    assert extract_order_sn(body) == want


def test_orders_by_custom_sn_ignores_others():
    rows = [{"order_sn": "PO1", "custom_order_sn": "PO1"}, {"order_sn": "PO2", "custom_order_sn": "SCM-PO-3"}]
    assert [o["order_sn"] for o in orders_by_custom_sn(rows, "SCM-PO-3")] == ["PO2"]
    assert orders_by_custom_sn(rows, "SCM-PO-4") == []


def _po_with_source(states):
    po = {"supplier_id": "8085", "purchaser_id": 111, "opt_uid": 1, "qc_type": 2,
          "lines": [{"line_no": 1, "sku": "S", "wid": 1, "units": 5, "price": "2", "sources_units": 5,
                     "sources": [{"line_id": 7, "seller_id": "A", "units": 5}]}]}
    if states is not None:
        po["source_line_states"] = states
    return po


def test_sources_orderable_names_cancelled_lines():
    g = next(g for g in preflight(_po_with_source([{"line_id": 7, "state": "已撤销"}]))
             if g["gate"] == "sources_orderable")
    assert g["ok"] is False and g["detail"]["offenders"] == [{"line_id": 7, "state": "已撤销"}]
    assert all(g["ok"] for g in preflight(_po_with_source([{"line_id": 7, "state": "已确认"}])))


def test_sources_orderable_unloaded_states_is_not_ok():
    """★ 有来源却没带状态 = 没查过，不是「都可组单」。"""
    g = next(g for g in preflight(_po_with_source(None)) if g["gate"] == "sources_orderable")
    assert g["ok"] is False and g["detail"]["loaded"] is False


@pytest.mark.parametrize("outcome,code,kind", [
    ("rejected", "403", "auth"), ("rejected", 403, "auth"),
    ("rejected", "1001", "business"), ("rejected", None, "business"),
    ("ok", "0", None), ("unknown", None, None),
])
def test_rejection_kind(outcome, code, kind):
    from erp.transport import rejection_kind
    assert rejection_kind(outcome, code) == kind
