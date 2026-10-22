"""销售计划 Excel 导入 · 格式规则（spec §2）。纯层：吃已读出的单元格。"""
from datetime import datetime

import pytest

from rules.plan_import import COLUMNS, MAX_ROWS, parse_rows

P = ["2026-11", "2026-12"]
H = list(COLUMNS) + P


def demand(msku="m1", sid="S1", sku=None, a=None, b=None, name=None, mk=None):
    return ["销量", sku, sid, name, mk, msku, a, b]


def purchase(sku="K1", a=None, b=None):
    return ["采购", sku, None, None, None, None, a, b]


def codes(r):
    return [e.code for e in r.errors]


def test_units_blank_zero_float():
    r = parse_rows(H, [demand(a=None, b=0), demand(msku="m2", a=12.0, b=7)], P)
    assert r.errors == []
    assert r.demand[0].units == {"2026-11": None, "2026-12": 0}
    assert r.demand[1].units == {"2026-11": 12, "2026-12": 7}


@pytest.mark.parametrize("bad", [12.5, -1, "十"])
def test_bad_units_carries_row_and_col(bad):
    r = parse_rows(H, [demand(), demand(msku="m2", b=bad)], P)
    (e,) = r.errors
    assert (e.code, e.row, e.col) == ("bad_units", 3, "H")
    assert e.seller_sku == "m2" and e.sid == "S1"


def test_header_shuffled_and_spaced():
    h = [" msku ", "2026-12", "类型", "店铺ID ", "2026-11", "货号", "店铺", "国家"]
    r = parse_rows(h, [["m1", 5, "销量", "S1", 3, None, None, None]], P)
    assert r.errors == []
    assert r.demand[0].units == {"2026-11": 3, "2026-12": 5}


def test_datetime_header_is_month():
    h = list(COLUMNS) + [datetime(2026, 11, 1), "2026-12"]  # noqa: DTZ001
    r = parse_rows(h, [demand(a=1)], P)
    assert r.errors == [] and r.demand[0].units["2026-11"] == 1


def test_missing_and_extra_month():
    r = parse_rows(list(COLUMNS) + ["2026-11"], [demand()], P)
    assert codes(r) == ["missing_month"]
    r = parse_rows(H + ["2027-01"], [demand()], P)
    assert codes(r) == ["extra_month"]


def test_bad_header_missing_fixed_column():
    r = parse_rows(["类型", "货号"] + P, [demand()], P)
    assert "bad_header" in codes(r)


def test_bad_type_and_missing_fields():
    rows = [["退货", None, "S1", None, None, "m1", 1, 1],
            demand(msku=None), demand(msku="m2", sid=None), purchase(sku=None)]
    r = parse_rows(H, rows, P)
    assert codes(r) == ["bad_type", "missing_field", "missing_field", "missing_field"]
    assert [e.col for e in r.errors] == ["A", "F", "C", "B"]


def test_duplicates_name_both_rows():
    r = parse_rows(H, [demand(), demand(), purchase(), purchase()], P)
    dup = [e for e in r.errors if e.code == "duplicate_row"]
    assert sorted(e.row for e in dup) == [2, 3, 4, 5]
    assert all(len(e.extra["rows"]) == 2 for e in dup)


def test_msku_trim_keeps_case():
    r = parse_rows(H, [demand(msku=" msku-A ")], P)
    assert r.demand[0].seller_sku == "msku-A"


def test_numeric_sid_becomes_text():
    r = parse_rows(H, [demand(sid=1234.0)], P)
    assert r.demand[0].sid == "1234"


def test_empty_file_and_too_large():
    assert codes(parse_rows(H, [], P)) == ["empty_file"]
    assert codes(parse_rows(H, [[None] * 8], P)) == ["empty_file"]
    big = [demand(msku=f"m{i}") for i in range(MAX_ROWS + 1)]
    assert codes(parse_rows(H, big, P)) == ["file_too_large"]
    assert parse_rows(H, big[:MAX_ROWS], P).errors == []


def test_purchase_sku_not_claimed_in_file_only():
    r = parse_rows(H, [demand(sku="K1"), purchase("K1"), purchase("K9")], P)
    assert [(e.code, e.row) for e in r.errors] == [("purchase_sku_not_claimed", 4)]
    # 销量行没填货号：留给 Task 6 用桥表补，这里不判
    r = parse_rows(H, [demand(sku=None), purchase("K1")], P)
    assert r.errors[0].code == "purchase_sku_not_claimed"


def test_all_errors_reported_at_once():
    rows = [demand(a=-1), ["退货", None, "S1", None, None, "m2", 1, 1], demand(msku=None)]
    assert len(parse_rows(H, rows, P).errors) == 3
