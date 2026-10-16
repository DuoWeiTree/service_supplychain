"""排货派生与六闸的纯规则（`15` §5 / §6 · CA-D2/D3/D5/D8）。"""
from rules import dispatch as d

A, B = "11072", "11094"


def cell(units, *, line_id=1, sku="SKU-A", msku="M-A", sid=A, from_wid=1, channel="fba",
         dest_wid=None, dest_sid=None):
    if channel == "fba" and dest_sid is None:
        dest_sid = sid
    return {"line_id": line_id, "sku": sku, "seller_sku": msku, "sid": sid, "from_wid": from_wid,
            "channel": channel, "dest_wid": dest_wid, "dest_sid": dest_sid, "units": units}


# ── 派生（15 §5.3 的两例 + CA-D2）─────────────────────────

def test_same_store_same_sku_two_warehouses_is_two_lines():
    got = d.derive([cell(5, from_wid=1), cell(3, from_wid=2)])
    assert list(got) == [("fba", A)]
    assert sorted(got[("fba", A)]) == [("M-A", A, 1), ("M-A", A, 2)]


def test_two_stores_same_oversea_warehouse_is_one_consignment_two_lines():
    got = d.derive([cell(5, sid=A, msku="M-A", channel="oversea", dest_wid=9),
                    cell(4, sid=B, msku="M-C", channel="oversea", dest_wid=9)])
    assert list(got) == [("oversea", 9)]
    assert len(got[("oversea", 9)]) == 2


def test_planned_and_unplanned_same_line_key_merge_and_split_counts():
    got = d.derive([cell(5, line_id=1), cell(2, line_id=7), cell(3, line_id=None)])
    line = got[("fba", A)][("M-A", A, 1)]
    assert line == {"sku": "SKU-A", "units": 10, "planned": 7, "unplanned": 3}


def test_same_msku_to_fba_and_oversea_is_two_consignments():
    got = d.derive([cell(5), cell(5, channel="oversea", dest_wid=9)])
    assert set(got) == {("fba", A), ("oversea", 9)}


def test_zero_units_cells_are_ignored():
    assert d.derive([cell(0)]) == {}


def test_diff_names_created_removed_kept_and_orphan_packing():
    old = {("fba", A), ("oversea", 9)}
    new = d.derive([cell(5, sid=B, msku="M-C")])
    packing = {("M-A", A, 1), ("M-C", B, 1)}
    got = d.diff(old, new, packing)
    assert got["created"] == [("fba", B)]
    assert got["removed"] == [("fba", A), ("oversea", 9)]
    assert got["kept"] == []
    assert got["orphan_packing"] == [("M-A", A, 1)]


# ── 桶（CA-D8）──────────────────────────────────────────

def test_bucket_feasible_own_bucket_first_then_shared():
    assert d.bucket_feasible({A: 10}, {"shared": 0, A: 10}) == (True, 0)
    assert d.bucket_feasible({A: 12, B: 3}, {"shared": 5, A: 10}) == (True, 5)
    assert d.bucket_feasible({A: 12, B: 4}, {"shared": 5, A: 10}) == (False, 6)


def test_other_stores_bucket_is_not_usable():
    assert d.bucket_feasible({B: 1}, {"shared": 0, A: 100}) == (False, 1)


def test_available_for_store():
    buckets = {"shared": 5, A: 10}
    assert d.available_for(A, {A: 4}, buckets) == 6 + 5
    assert d.available_for(B, {A: 12}, buckets) == 3
    assert d.available_for(B, {A: 12, B: 5}, buckets) == -2   # ★ 负数 = 已超，照实给


# ── 六闸 ───────────────────────────────────────────────

def cons(cid, channel, lines, dest_wid=None, dest_sid=None):
    return {"consignment_id": cid, "channel": channel, "dest_wid": dest_wid, "dest_sid": dest_sid,
            "lines": [{"seller_sku": m, "sid": s, "from_wid": w, "sku": "SKU-A", "units": u}
                      for m, s, w, u in lines]}


FULL_BOX = {"box_count": 1, "box_len": 1, "box_wid": 1, "box_hei": 1, "box_weight": 1, "units_per_box": 5}


def gates(**kw):
    base = dict(consignments=[cons(1, "fba", [("M-A", A, 1, 5)], dest_sid=A)],
                packing={("M-A", A, 1): FULL_BOX},
                stock={("SKU-A", 1): {"shared": 10}}, stock_error=None,
                committed={("SKU-A", 1): {A: 5}}, ledger_conflicts=[],
                dest_kinds={9: "oversea_self", 1: "local"}, fba_sids={A}, logistics={})
    base.update(kw)
    return d.gates(**base)


def by(gs):
    return {g["gate"]: g for g in gs}


def test_gates_are_always_all_seven_in_order():
    gs = gates()
    assert [g["gate"] for g in gs] == ["G1", "G2", "G3", "G4", "G5", "G6", "G7"]
    assert all(set(g) >= {"gate", "ok", "code", "detail", "fix"} for g in gs)


def test_all_pass_except_g5_which_is_unchecked():
    g = by(gates())
    assert [g[k]["ok"] for k in ("G1", "G2", "G3", "G4", "G6", "G7")] == [True] * 6
    assert g["G5"]["ok"] is None and "待裁定" in g["G5"]["detail"]
    assert d.confirmable(gates()) is True


def test_g1_only_checks_fba_lines():
    g = by(gates(consignments=[cons(1, "oversea", [("M-A", A, 1, 5)], dest_wid=9)], packing={}))
    assert g["G1"]["ok"] is True
    g = by(gates(packing={("M-A", A, 1): {**FULL_BOX, "box_weight": None}}))
    assert g["G1"]["ok"] is False and g["G1"]["code"] == "packing_incomplete"
    assert g["G1"]["detail"] == [{"seller_sku": "M-A", "sid": A, "from_wid": 1, "missing": ["box_weight"]}]


def test_g3_unknown_stock_is_not_a_pass():
    g = by(gates(stock=None, stock_error={"target": "x", "cause": {"kind": "timeout"}}))
    assert g["G3"]["ok"] is None and g["G3"]["detail"]["cause"]["kind"] == "timeout"
    assert d.confirmable(gates(stock=None, stock_error={})) is False


def test_g3_fails_with_numbers_when_buckets_cannot_cover():
    g = by(gates(committed={("SKU-A", 1): {A: 15}}))
    assert g["G3"]["ok"] is False and g["G3"]["code"] == "insufficient_available"
    assert g["G3"]["detail"] == [{"sku": "SKU-A", "from_wid": 1, "need": {A: 15},
                                  "buckets": {"shared": 10}, "short": 5}]


def test_g3_missing_stock_row_is_zero_on_an_accepted_snapshot():
    g = by(gates(stock={}))
    assert g["G3"]["ok"] is False


def test_g4_names_ledger_conflicts():
    g = by(gates(ledger_conflicts=[{"sheet_id": 9, "sku": "SKU-A", "from_wid": 1}]))
    assert g["G4"]["ok"] is False and g["G4"]["code"] == "ledger_moved"


def test_g2_and_g6_destination_checks():
    g = by(gates(consignments=[cons(1, "oversea", [("M-A", A, 1, 5)], dest_wid=1)]))
    assert g["G6"]["ok"] is False and g["G6"]["code"] == "invalid_destination"
    g = by(gates(fba_sids=set()))
    assert g["G6"]["ok"] is False
    g = by(gates(consignments=[cons(1, "fba", [("M-A", A, 1, 5)], dest_sid=None)]))
    assert g["G2"]["ok"] is False and g["G2"]["code"] == "dest_incomplete"


def test_empty_sheet_cannot_be_confirmed():
    gs = gates(consignments=[], packing={}, committed={})
    assert d.confirmable(gs) is False
    assert by(gs)["G6"]["ok"] is False and by(gs)["G6"]["detail"] == "没有任何排货单"


def test_g7_every_oversea_consignment_needs_an_enabled_logistics_channel():
    """CB-D3：海外仓排货单必选物流方式；FBA 单不需要。"""
    os_ = [cons(1, "oversea", [("M-A", A, 1, 5)], dest_wid=9)]
    g = by(gates(consignments=os_, logistics={}))
    assert g["G7"]["ok"] is False and g["G7"]["code"] == "logistics_missing" and g["G7"]["detail"] == [{"dest_wid": 9}]
    assert by(gates(consignments=os_, logistics={9: {"logistics_id": 1, "enabled": True}}))["G7"]["ok"] is True
    g = by(gates(consignments=os_, logistics={9: {"logistics_id": 1, "enabled": False}}))
    assert g["G7"]["ok"] is False and g["G7"]["detail"] == [{"dest_wid": 9, "logistics_id": 1, "disabled": True}]
    assert by(gates())["G7"]["ok"] is True                     # 只有 FBA 单
