"""海外仓在途实查（spec 2026-10-15 §2.2 · FM-D6）：领星 listInbound status 50 + 10 分钟缓存。"""
from __future__ import annotations

import datetime as dt

import pytest

from erp import gateway
from erp.transport import CallResult
from usecase import oversea_in_transit as oit


def order(no, eta, products, sent="2026-09-30 11:40:00"):
    return {"overseas_order_no": no, "status": 50, "s_wid": 36782, "r_wid": 22890, "estimated_time": eta,
            "send_good_handle_time": sent, "real_delivery_time": sent[:10],
            "products": [{"sku": s, "stock_num": n, "receive_num": r} for s, n, r in products]}


@pytest.fixture
def lx(monkeypatch):
    oit.reset_cache()
    pages: list[list[dict]] = []
    calls: list[dict] = []

    def call(name, params):
        assert name == "ows_in_transit_list"
        calls.append(params)
        i = params["page"] - 1
        data = pages[i] if i < len(pages) else []
        return None, CallResult("ok", "openapi.lingxing.com", "/erp/sc/routing/owms/inbound/listInbound", 5,
                                body={"code": 0, "data": data, "total": sum(len(p) for p in pages)})

    monkeypatch.setattr(gateway, "call", call)
    return pages, calls


def test_groups_remaining_units_by_sku_with_order_fields(lx):
    pages, _ = lx
    pages.append([order("OWS1", "2026-11-05", [("A", 30, 0), ("B", 5, 5)]), order("OWS2", "", [("A", 10, 4)])])
    got = oit.in_transit_by_sku()
    assert got.ok
    assert [(x.ref, x.units, x.eta) for x in got.by_sku["A"]] == [("OWS1", 30, dt.date(2026, 11, 5)), ("OWS2", 6, None)]
    assert "B" not in got.by_sku                         # 收完了 = 不在途
    assert got.by_sku["A"][0].extra == {"from_wid": 36782, "to_wid": 22890, "shipped_at": "2026-09-30"}


def test_pages_until_short_page(lx):
    pages, calls = lx
    pages.extend([[order(f"OWS{i}", "2026-11-05", [("A", 1, 0)]) for i in range(50)],
                  [order("OWSX", "2026-11-05", [("A", 1, 0)])]])
    assert sum(x.units for x in oit.in_transit_by_sku().by_sku["A"]) == 51
    assert [c["page"] for c in calls] == [1, 2] and all(c["status"] == 50 for c in calls)


def test_cached_for_ten_minutes(lx, monkeypatch):
    pages, calls = lx
    pages.append([order("OWS1", "2026-11-05", [("A", 1, 0)])])
    clock = [1000.0]
    monkeypatch.setattr(oit, "_now", lambda: clock[0])
    oit.in_transit_by_sku()
    clock[0] += 599
    oit.in_transit_by_sku()
    assert len(calls) == 1
    clock[0] += 2
    oit.in_transit_by_sku()
    assert len(calls) == 2


def test_failure_is_unknown_not_empty(monkeypatch, caplog):
    oit.reset_cache()
    monkeypatch.setattr(gateway, "call", lambda name, params: (None, CallResult(
        "unknown", "openapi.lingxing.com", "/erp/sc/routing/owms/inbound/listInbound", 8000, cause="ReadTimeout")))
    got = oit.in_transit_by_sku()
    assert not got.ok and got.by_sku is None and "ReadTimeout" in got.reason
    assert "op=erp_oversea_in_transit" in caplog.text and "outcome=unknown" in caplog.text


# ── 终审修正 ────────────────────────────────────────────────────────

def test_malformed_dates_and_numbers_do_not_crash_and_are_counted(lx):
    """I-1：领星常给 0000-00-00；件数可能是 "12.0" —— 一张坏单不许把整张计划表拖成 500。"""
    pages, _ = lx
    pages.append([order("OWS1", "0000-00-00 00:00:00", [("A", "12.0", "2")], sent="0000-00-00"),
                  order("OWS2", "2026-11-05", [("B", "abc", 0)])])
    got = oit.in_transit_by_sku()
    assert got.ok
    assert [(x.ref, x.units, x.eta) for x in got.by_sku["A"]] == [("OWS1", 10, None)]
    assert "B" not in got.by_sku
    assert got.bad_rows == 1


def test_short_of_reported_total_is_not_ok(lx, monkeypatch):
    """I-2：取到的单数少于领星报的 total（翻页上限 / 分页失常）→ 不当完整结果，也不缓存。"""
    pages, _ = lx
    monkeypatch.setattr(oit, "MAX_PAGES", 1)
    pages.extend([[order(f"OWS{i}", "2026-11-05", [("A", 1, 0)]) for i in range(50)],
                  [order("OWSX", "2026-11-05", [("A", 1, 0)])]])
    got = oit.in_transit_by_sku()
    assert not got.ok and "truncated" in got.reason
    assert oit._cache is None or not oit._cache[1].ok


def test_failure_is_remembered_briefly_so_requests_do_not_pile_up(monkeypatch):
    """I-3：领星挂了 —— 失败记 60 秒，期间不再逐个等 30 秒超时；过后再查。"""
    oit.reset_cache()
    n = [0]

    def call(name, params):
        n[0] += 1
        return None, CallResult("unknown", "openapi.lingxing.com", "/x", 30000, cause="ReadTimeout")

    monkeypatch.setattr(gateway, "call", call)
    clock = [1000.0]
    monkeypatch.setattr(oit, "_now", lambda: clock[0])
    assert not oit.in_transit_by_sku().ok
    clock[0] += 59
    assert not oit.in_transit_by_sku().ok
    assert n[0] == 1
    clock[0] += 2
    oit.in_transit_by_sku()
    assert n[0] == 2
