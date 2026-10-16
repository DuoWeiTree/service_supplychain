"""FBA 在途货件 + 调仓中预留的 CH 取数（spec 2026-10-14-forecast-fba-inbound §3）。"""
from __future__ import annotations

import datetime as dt

from dim import ch_source as cs

D = dt.date


FROM, TO = D(2026, 11, 15), D(2026, 11, 22)


def row(sid="11072", msku="M", sh="FBA1", st="SHIPPED", shipped=5, rec=0, f=FROM, t=TO):
    return (sid, msku, sh, st, shipped, rec, f, t)


# ── parse_fba_inbound ──────────────────────────────────────────────

def test_remaining_is_shipped_minus_received_per_shipment():
    got, dropped = cs.parse_fba_inbound([row(), row(sh="FBA2", st="RECEIVING", shipped=2, rec=1)])
    assert [(x.shipment_id, x.units, x.deliver_to) for x in got[("11072", "M")]] == [
        ("FBA1", 5, D(2026, 11, 22)), ("FBA2", 1, D(2026, 11, 22))]
    assert dropped == {}


def test_fully_received_is_not_in_transit():
    got, dropped = cs.parse_fba_inbound([row(st="RECEIVING", shipped=4, rec=4)])
    assert got == {} and dropped == {}


def test_over_received_is_zero_and_counted():
    got, dropped = cs.parse_fba_inbound([row(st="RECEIVING", shipped=3, rec=6)])
    assert got == {}
    assert dropped["over_received"] == {"rows": 1, "units": 3}


def test_not_shipped_statuses_are_counted_not_placed():
    got, dropped = cs.parse_fba_inbound([row(st="WORKING", shipped=13), row(sh="FBA9", st="READY_TO_SHIP", shipped=20),
                                         row(sh="FBAX", st="CANCELLED", shipped=4)])
    assert got == {}
    assert dropped["not_shipped"] == {"rows": 2, "units": 33}


def test_duplicate_shipment_msku_rows_merge_once_and_are_counted():
    got, dropped = cs.parse_fba_inbound([row(), row()])
    assert [x.units for x in got[("11072", "M")]] == [5]
    assert dropped["rows_collapsed"] == 1


def test_shipment_belongs_only_to_its_own_store():
    got, _ = cs.parse_fba_inbound([row(sid="11072"), row(sid="11073", sh="FBA2", shipped=2)])
    assert [x.shipment_id for x in got[("11072", "M")]] == ["FBA1"]
    assert [x.shipment_id for x in got[("11073", "M")]] == ["FBA2"]


def test_missing_or_blank_dates_kept_as_none_and_strings_parsed():
    got, _ = cs.parse_fba_inbound([row(f=None, t=""), row(sh="FBA2", f="2026-11-01", t="2026-11-08 00:00:00")])
    a, b = got[("11072", "M")]
    assert a.deliver_to is None and a.deliver_from is None
    assert (b.deliver_from, b.deliver_to) == (D(2026, 11, 1), D(2026, 11, 8))


# ── parse_fba_extra：调仓中 + 亚马逊的在途数（对账用）──────────────

def test_extra_splits_fc_transfers_and_counts_null():
    fc, shipped, dropped = cs.parse_fba_extra([("11072", "M", 2, 0, 31), ("11072", "N", 0, 1, 0), ("0", "Z", 1, 0, 3)])
    assert fc == {("11072", "M"): 2, ("11072", "N"): 0}
    assert shipped == {("11072", "M"): 31, ("11072", "N"): 0}
    assert dropped == {"fc_transfers_null": 1}


# ── ChSource：批量缓存 · 无快照 = 未知 · 对账 ─────────────────────────

import logging

from tests.test_dim_ch_source_forecast import onhand_query


def src_with(inbound_rows, extra_rows=(("11072", "M", 2, 0, 31),)):
    base = onhand_query()
    calls: list[str] = []

    def q(sql, parameters=None):
        calls.append(sql)
        if sql is cs.SQL_FBA_INBOUND_DAY:
            return [(dt.date.fromisoformat(parameters["as_of"]) if inbound_rows else None,)]
        if sql is cs.SQL_FBA_INBOUND:
            return list(inbound_rows)
        if sql is cs.SQL_FBA_EXTRA:
            return list(extra_rows)
        return base(sql, parameters)

    src = cs.ChSource(q)
    src.calls = calls
    return src


def test_fba_inbound_none_when_no_snapshot_on_as_of():
    """★ FI-Q1：当天没有货件快照 → 未知（None），不回退旧快照、不当 0。"""
    assert src_with([]).fba_inbound("M", "11072") is None


def test_fba_inbound_reads_once_for_many_mskus():
    src = src_with([row(t=D(2026, 11, 22))])
    assert [x.units for x in src.fba_inbound("M", "11072")] == [5]
    assert src.fba_inbound("N", "11072") == []
    assert sum(s is cs.SQL_FBA_INBOUND for s in src.calls) == 1


def test_fc_transfers_from_extra_batch_and_zero_when_absent():
    src = src_with([])
    assert src.fc_transfers("M", "11072") == 2
    assert src.fc_transfers("NEVER", "11072") == 0


def test_reconcile_warns_when_shipped_differs(caplog):
    src = src_with([row(t=D(2026, 11, 22))])
    with caplog.at_level(logging.WARNING, logger="scm.dim"):
        src.fba_inbound("M", "11072")
    assert "op=fba_inbound_reconcile" in caplog.text and "diff=26" in caplog.text


def test_reconcile_silent_when_equal(caplog):
    src = src_with([row(shipped=31, t=D(2026, 11, 22))])
    with caplog.at_level(logging.WARNING):
        src.fba_inbound("M", "11072")
    assert "op=fba_inbound_reconcile" not in caplog.text


# ── 终审修正（I-3 · I-4 · M-3）────────────────────────────────────

def test_unknown_status_and_headerless_items_are_counted_not_silently_dropped():
    got, dropped = cs.parse_fba_inbound([row(st="ERROR", shipped=4), row(sh="FBA8", st="", shipped=6),
                                         row(sh="FBA9", st="CLOSED", shipped=9, rec=9)])
    assert got == {}
    assert dropped["unknown_status"] == {"rows": 1, "units": 4, "statuses": ["ERROR"]}
    assert dropped["items_without_header"] == {"rows": 1, "units": 6}
    assert "status_skipped" not in dropped                  # CLOSED / CANCELLED / DELETED 是预期的，不计


def test_duplicate_keeps_the_latest_capture_before_filtering():
    """I-4：同一快照日两次采集 —— 旧的还在途、新的已签收完 → 以新的为准（不在途），不靠行序。"""
    old = (*row(st="IN_TRANSIT", shipped=5, rec=0), dt.datetime(2026, 10, 14, 6, 0, tzinfo=dt.UTC))
    new = (*row(st="RECEIVING", shipped=5, rec=5), dt.datetime(2026, 10, 14, 9, 0, tzinfo=dt.UTC))
    for rows in ([old, new], [new, old]):
        got, dropped = cs.parse_fba_inbound(rows)
        assert got == {} and dropped["rows_collapsed"] == 1


def test_reconcile_failure_does_not_fail_the_fetch(caplog):
    """M-3：对账只记不拦 —— 取不到对账数也不许把货件取数拖成 503。"""
    base = onhand_query()

    def q(sql, parameters=None):
        if sql is cs.SQL_FBA_INBOUND_DAY:
            return [(dt.date.fromisoformat(parameters["as_of"]),)]
        if sql is cs.SQL_FBA_INBOUND:
            return [row(t=D(2026, 11, 22))]
        if sql is cs.SQL_FBA_EXTRA:
            raise ConnectionError("boom")
        return base(sql, parameters)

    src = cs.ChSource(q)
    with caplog.at_level(logging.WARNING):
        assert [x.units for x in src.fba_inbound("M", "11072")] == [5]
    assert "op=fba_inbound_reconcile outcome=skipped" in caplog.text


# ── spec 2026-10-15：货号级 FBA 在途（当月在途，Σ 全部店铺）────────────

def test_fba_in_transit_by_sku_sums_all_stores_and_counts_rows_without_sku():
    rows = [(*row(sid="11072", msku="M-US"), None, "SKU1"), (*row(sid="11073", msku="M-CA", sh="FBA2", shipped=2), None, "SKU1"),
            (*row(sid="11072", msku="X", sh="FBA3"), None, "")]
    src = src_with(rows)
    got = src.fba_in_transit("SKU1")
    assert sorted((sid, msku, x.shipment_id, x.units) for sid, msku, x in got) == [
        ("11072", "M-US", "FBA1", 5), ("11073", "M-CA", "FBA2", 2)]
    assert src.fba_in_transit("NONE") == []
    assert src.stats()["fba_inbound"]["items_without_sku"] == {"rows": 1, "units": 5}


def test_fba_in_transit_none_when_no_snapshot():
    assert src_with([]).fba_in_transit("SKU1") is None


# ── 10-15 负责人裁定（推翻 FI-Q1）：货件快照当天没采到 → 用前一天的，写明日期；更早 → 未知 ────────

def src_days(days_with_rows: dict):
    """days_with_rows: 采集日 → 货件行。假 CH 先答「最近的快照日」，再按日期给行。"""
    base = onhand_query()
    asked: list = []

    def q(sql, parameters=None):
        if sql is cs.SQL_FBA_INBOUND_DAY:
            lo, hi = dt.date.fromisoformat(parameters["since"]), dt.date.fromisoformat(parameters["as_of"])
            got = [d for d in days_with_rows if lo <= d <= hi]
            return [(max(got) if got else None,)]
        if sql is cs.SQL_FBA_INBOUND:
            asked.append(parameters["day"])
            return list(days_with_rows.get(dt.date.fromisoformat(parameters["day"]), []))
        if sql is cs.SQL_FBA_EXTRA:
            return [("11072", "M", 0, 0, 5)]
        return base(sql, parameters)

    src = cs.ChSource(q)
    src.asked = asked
    return src


def test_falls_back_to_previous_day_and_says_which_day():
    src = src_days({D(2026, 9, 22): [row()]})               # as_of 2026-09-23（onhand_query 的掉档守卫日）
    assert [x.units for x in src.fba_inbound("M", "11072")] == [5]
    assert src.fba_inbound_as_of() == D(2026, 9, 22)
    assert src.asked == ["2026-09-22"]


def test_same_day_snapshot_wins_when_present():
    src = src_days({D(2026, 9, 22): [row(shipped=1)], D(2026, 9, 23): [row(shipped=5)]})
    assert [x.units for x in src.fba_inbound("M", "11072")] == [5]
    assert src.fba_inbound_as_of() == D(2026, 9, 23)


def test_older_than_one_day_is_unknown():
    src = src_days({D(2026, 9, 21): [row()]})
    assert src.fba_inbound("M", "11072") is None
    assert src.fba_inbound_as_of() is None
