"""当月在途归月（spec 2026-10-15 §2 · FM-D2/D3/Q3）。"""
from __future__ import annotations

import datetime as dt

from forecast.month_in_transit import TransitItem, month_in_transit

D = dt.date
AS_OF = D(2026, 10, 14)
P = [D(2026, 10, 1), D(2026, 11, 1), D(2026, 12, 1)]


def fba(ref, units, eta):
    return TransitItem("fba", ref, units, eta, {"sid": "11072"})


def ows(ref, units, eta):
    return TransitItem("oversea", ref, units, eta, {"to_wid": 22890})


def totals(out):
    return [(r["total"], r["fba"], r["oversea"]) for r in out]


def test_any_day_in_transit_counts_including_arrival_month():
    """FM-D2：31 件 11-08 ~ 11-22 送达 → 10 月 31 · 11 月 31 · 12 月 0（「就算是 1 号，也算是该月在途」）。"""
    out = month_in_transit([fba("A", 7, D(2026, 11, 8)), fba("B", 24, D(2026, 11, 22))], [], AS_OF, P)
    assert totals(out) == [(31, 31, 0), (31, 31, 0), (0, 0, 0)]


def test_arriving_on_the_first_still_counts_that_month():
    out = month_in_transit([fba("A", 5, D(2026, 12, 1))], [], AS_OF, P)
    assert [r["total"] for r in out] == [5, 5, 5]


def test_months_before_as_of_are_unknown_not_zero():
    """终审 I-4：as_of 之前的月份那时在不在路上我们没有记录 —— 未知，不是 0。"""
    out = month_in_transit([fba("A", 5, D(2026, 11, 8))], [], AS_OF, [D(2026, 9, 1), *P])
    assert [r["total"] for r in out] == [None, 5, 5, 0]
    assert (out[0]["fba"], out[0]["oversea"], out[0]["before_as_of"]) == (None, None, True)
    assert out[1]["before_as_of"] is False


def test_unknown_or_overdue_eta_counts_only_the_as_of_month_and_is_named():
    """FM-Q3：此刻确实在路上 → 只计 as_of 所在月，标 current_only。"""
    out = month_in_transit([fba("NOETA", 3, None), fba("LATE", 1, D(2026, 10, 11))], [], AS_OF, P)
    assert [r["total"] for r in out] == [4, 0, 0]
    assert all(i["current_only"] for i in out[0]["items"])


def test_fba_and_oversea_add_up_and_items_carry_source_fields():
    out = month_in_transit([fba("A", 7, D(2026, 11, 8))], [ows("OWS1", 30, D(2026, 11, 5))], AS_OF, P)
    assert totals(out) == [(37, 7, 30), (37, 7, 30), (0, 0, 0)]
    assert [(i["source"], i["ref"], i["units"], i["eta"]) for i in out[0]["items"]] == [
        ("fba", "A", 7, "2026-11-08"), ("oversea", "OWS1", 30, "2026-11-05")]
    assert out[0]["items"][1]["to_wid"] == 22890


def test_unknown_source_makes_total_unknown_not_zero():
    out = month_in_transit([fba("A", 7, D(2026, 11, 8))], None, AS_OF, P)
    assert totals(out) == [(None, 7, None), (None, 7, None), (None, 0, None)]
    out = month_in_transit(None, [], AS_OF, P)
    assert out[0]["total"] is None and out[0]["fba"] is None
