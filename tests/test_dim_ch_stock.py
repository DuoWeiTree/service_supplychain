"""本地仓可发库存 · 采购行项进度的按需读取（spec 2026-10-10 §4 · CA-D8）。★ 纯层：query 注入。"""
import datetime as dt

import pytest

from dim import ch_stock as st
from dim.ch_source import ChDataUnusable, ChUnavailable

D10, D9 = dt.date(2026, 10, 10), dt.date(2026, 10, 9)
AT10, AT9 = dt.datetime(2026, 10, 10, 6, 33, 29), dt.datetime(2026, 10, 9, 6, 33, 1)
LOCAL = {1, 2}
KNOWN = {"11072", "11094"}


def days(*rows):
    return list(rows)


#: ★ 第 5 列 = 那天最后一行的 _captured_at（CA-D12：拿它和领星现读的时刻比谁新）
GOOD_DAYS = days((D10, 7978, 57, True, AT10), (D9, 7978, 57, True, AT9))


def fake(days_rows, stock_rows=(), items_as_of=(D10, AT10), items=(), fail=None):
    calls = []

    def q(sql, params=None):
        calls.append((sql, params))
        if fail is not None and fail in sql:
            raise ConnectionRefusedError("refused")
        if sql == st.SQL_STOCK_DAYS:
            return list(days_rows)
        if sql == st.SQL_STOCK:
            return list(stock_rows)
        if sql == st.SQL_PO_ITEMS_AS_OF:
            return [items_as_of]
        if sql == st.SQL_PO_ITEMS:
            return list(items)
        raise AssertionError(f"unexpected sql {sql[:40]}")
    q.calls = calls
    return q


def src(q, **kw):
    return st.ChStock(q, local_wids=LOCAL, known_sids=KNOWN, **kw)


def test_buckets_split_shared_and_store_and_sum_fnsku():
    q = fake(GOOD_DAYS, [
        (1, "SKU-A", "0", 100),
        (1, "SKU-A", "", 5),            # ★ '' 与 '0' 都是共享桶（10-10 实测两种都有）
        (1, "SKU-A", "11072", 7),
        (1, "SKU-A", "11072", 3),       # 同店不同 fnsku → 合进同一桶
        (2, "SKU-A", "11094", 4),
    ])
    snap = src(q).snapshot({"SKU-A"})
    assert snap.as_of == D10
    assert snap.buckets[("SKU-A", 1)] == {"shared": 105, "11072": 10}
    assert snap.buckets[("SKU-A", 2)] == {"shared": 0, "11094": 4}


def test_unknown_seller_bucket_excluded_and_counted():
    q = fake(GOOD_DAYS, [(1, "SKU-A", "110625196267809792", 2626), (1, "SKU-A", "0", 1)])
    snap = src(q).snapshot({"SKU-A"})
    assert snap.buckets[("SKU-A", 1)] == {"shared": 1}
    assert snap.dropped["unknown_seller"] == {"rows": 1, "units": 2626}


def test_non_local_warehouse_rows_are_dropped_and_counted():
    q = fake(GOOD_DAYS, [(22890, "SKU-A", "0", 50), (1, "SKU-A", "0", 1)])
    snap = src(q).snapshot({"SKU-A"})
    assert ("SKU-A", 22890) not in snap.buckets
    assert snap.dropped["not_local_wid"] == {"rows": 1, "units": 50}


def test_counters_start_at_zero_so_checked_differs_from_not_checked():
    snap = src(fake(GOOD_DAYS, [])).snapshot({"SKU-A"})
    assert snap.dropped == {"unknown_seller": {"rows": 0, "units": 0},
                            "not_local_wid": {"rows": 0, "units": 0}}
    assert snap.buckets == {}


def test_rejected_day_falls_back_and_is_recorded():
    q = fake(days((D10, 3000, 57, True, AT10), (D9, 7978, 57, True, AT9)), [(1, "SKU-A", "0", 1)])
    snap = src(q).snapshot({"SKU-A"})
    assert snap.as_of == D9 and snap.captured_at == AT9
    assert snap.rejected and snap.rejected[0]["date"] == D10.isoformat()
    _, params = [c for c in q.calls if c[0] == st.SQL_STOCK][0]
    assert params["as_of"] == D9.isoformat() and params["skus"] == ["SKU-A"]


def test_no_capture_days_is_not_empty_stock():
    with pytest.raises(st.UnknownShape) as ei:
        src(fake([])).snapshot({"SKU-A"})
    assert st.STOCK_TABLE in str(ei.value)


def test_all_days_rejected_raises_with_evidence():
    with pytest.raises(ChDataUnusable):
        src(fake(days((D10, 10, 1, True, AT10)))).snapshot({"SKU-A"})


def test_connection_failure_names_target_and_cause():
    q = fake(GOOD_DAYS, fail=st.STOCK_TABLE)
    with pytest.raises(ChUnavailable) as ei:
        src(q, classify_failure=lambda e: {"kind": "refused", "type": type(e).__name__}).snapshot({"SKU-A"})
    assert ei.value.target == st.STOCK_TABLE
    assert ei.value.cause["kind"] == "refused"


def test_po_progress_maps_entered_and_pending_per_order_and_sku():
    q = fake(GOOD_DAYS, items=[("PO1", "SKU-A", 30, 70), ("PO1", "SKU-B", 0, 5)])
    got = src(q).po_progress({"PO1"})
    assert got[("PO1", "SKU-A")] == st.PoItemProgress(entered=30, pending=70, captured=AT10)
    assert got[("PO1", "SKU-B")].pending == 5


def test_po_progress_without_order_sns_does_not_query():
    q = fake(GOOD_DAYS)
    assert src(q).po_progress(set()) == {}
    assert q.calls == []


def test_snapshot_of_all_skus_uses_the_unfiltered_query():
    """无计划来源区要看全部本地可发 —— 不按货号过滤（CA-D2）。"""
    q = fake(GOOD_DAYS)
    q_all = lambda sql, params=None: (q.calls.append((sql, params)) or  # noqa: E731
                                      ([(1, "SKU-Z", "0", 9)] if sql == st.SQL_STOCK_ALL else q(sql, params)))
    snap = src(q_all).snapshot(None)
    assert snap.buckets == {("SKU-Z", 1): {"shared": 9}}
    assert any(c[0] == st.SQL_STOCK_ALL for c in q.calls)


def test_bucketize_is_shared_by_ch_and_live_reads():
    """CA-D12：CH 与领星现读同一套分桶（共享 '0' / '' · 认得的店 · 认不出的店丢弃计数 · 非本地仓丢弃计数）。"""
    buckets, dropped = st.bucketize([(1, "SKU-A", "0", 5), (1, "SKU-A", "11072", 2), (1, "SKU-A", "999", 4),
                                     (77, "SKU-A", "0", 9)], LOCAL, KNOWN)
    assert buckets == {("SKU-A", 1): {"shared": 5, "11072": 2}}
    assert dropped == {"unknown_seller": {"rows": 1, "units": 4}, "not_local_wid": {"rows": 1, "units": 9}}
