import datetime as dt
import logging

import pytest

from dim import ch_source as cs
from dim import order_store_map

D = dt.date


class Q:
    def __init__(self, rows=None, exc=None):
        self.rows, self.exc, self.calls = rows or [], exc, []

    def __call__(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params))
        if self.exc:
            raise self.exc
        return list(self.rows)


def test_daily_sales_dedups_by_order_item_and_keeps_the_window():
    q = Q([(D(2026, 9, 1), 4), (D(2026, 9, 2), 0)])
    got = cs.ChSource(q).daily_sales("MSKU-A", "11072", D(2026, 6, 17), D(2026, 9, 14))
    assert got == {D(2026, 9, 1): 4, D(2026, 9, 2): 0}
    sql, params = q.calls[0]
    assert "GROUP BY amazon_order_id, order_item_id" in sql and "argMax(order_status" in sql
    store, channel = order_store_map.store_for("11072")
    assert params == {"store": store, "channel": channel, "seller_sku": "MSKU-A",
                      "start": "2026-06-17", "end": "2026-09-14"}


def test_daily_sales_outside_the_window_is_an_unknown_shape():
    q = Q([(D(2026, 9, 20), 4)])
    with pytest.raises(cs.UnknownShape):
        cs.ChSource(q).daily_sales("MSKU-A", "11072", D(2026, 6, 17), D(2026, 9, 14))


def test_daily_sales_failure_says_who_how_long_and_how(caplog):
    caplog.set_level(logging.INFO)
    q = Q(exc=TimeoutError("read timed out"))
    with pytest.raises(cs.ChUnavailable) as ei:
        cs.ChSource(q).daily_sales("MSKU-A", "11072", D(2026, 6, 17), D(2026, 9, 14))
    assert ei.value.target == cs.ORDERS_TABLE
    assert "op=ch_daily_sales outcome=fail" in caplog.text and "elapsed_ms=" in caplog.text


def test_daily_sales_leaves_a_window_mark_for_its_own_key():
    src = cs.ChSource(Q([(D(2026, 9, 1), 4)]))
    src.daily_sales("MSKU-A", "11072", D(2026, 6, 17), D(2026, 9, 14))
    w = src.history_window("MSKU-A", "11072")
    assert (w["from"], w["until"], w["days_with_orders"]) == ("2026-06-17", "2026-09-14", 1)
    assert src.history_window("MSKU-C", "11094") == {}


def test_daily_sales_is_one_number_per_day_summed_in_sql():
    """★ baseline_rate 按天只认一个数（协议 dim/source.py）。同一天多个 order_item
    必须在 SQL 里合计：内层按 order_item 去重，外层按日 GROUP BY + sum。"""
    sql = " ".join(cs.SQL_DAILY_SALES.split())
    inner, _, outer = sql.rpartition(")")
    assert "GROUP BY amazon_order_id, order_item_id" in inner
    assert "sum(units)" in sql.split("FROM (")[0]
    assert "GROUP BY d" in outer and "ORDER BY d" in outer


def test_daily_sales_duplicate_day_rows_are_an_unknown_shape_not_an_overwrite():
    """★ 外层 GROUP BY d 保证一天一行；真出现两行，说明 SQL 形态变了 ——
    dict 覆盖会静默丢掉其中一行的件数。"""
    q = Q([(D(2026, 9, 1), 4), (D(2026, 9, 1), 3)])
    with pytest.raises(cs.UnknownShape, match="2026-09-01"):
        cs.ChSource(q).daily_sales("MSKU-A", "11072", D(2026, 6, 17), D(2026, 9, 14))


def test_parse_stock_days_three_ways():
    """OP-15：过了掉档守卫的日子里「缺行或 ≤ 0」= 断货；没过的日子不 censor、点名。"""
    rows = [(D(2026, 9, 1), 8000, 21, 1, 5),    # 采到了、有货
            (D(2026, 9, 2), 8000, 21, 0, 0),    # 采到了、缺行（L-1：0 库存不返回行）→ 断货
            (D(2026, 9, 3), 8000, 21, 2, 0),    # 采到了、可售 0 → 断货
            (D(2026, 9, 4), 100, 3, 0, 0)]      # 掉档 → 不 censor
    got = cs.parse_stock_days(rows, D(2026, 9, 1), D(2026, 9, 5), min_rows=7934,
                              min_distinct_sid=21)
    assert got.censored == frozenset({D(2026, 9, 2), D(2026, 9, 3)})
    assert got.captured == frozenset({D(2026, 9, 1), D(2026, 9, 2), D(2026, 9, 3)})
    assert got.rejected == ({"date": "2026-09-04", "rows": 100, "uniq_sid": 3},)


def test_parse_stock_days_duplicate_capture_day_is_an_unknown_shape():
    rows = [(D(2026, 9, 1), 8000, 21, 1, 5), (D(2026, 9, 1), 8000, 21, 0, 0)]
    with pytest.raises(cs.UnknownShape, match="2026-09-01"):
        cs.parse_stock_days(rows, D(2026, 9, 1), D(2026, 9, 5), min_rows=7934,
                            min_distinct_sid=21)


def test_fba_stock_days_rejected_days_are_neither_stock_out_nor_in_stock(caplog):
    """★ OP-15 第三类：没过掉档守卫的采集日（行数不够 / 店数不够）——那天 msku
    缺行或可售 0 都不能读成断货，有货也不能读成「采到了、有货」；只点名。
    被拒与没采到的日子都要进 stats() 和日志，不许静默消失。"""
    caplog.set_level(logging.INFO)
    rows = [(D(2026, 9, 1), 8000, 21, 1, 5),   # 采到了、有货
            (D(2026, 9, 2), 8000, 21, 0, 0),   # 采到了、缺行 → 断货
            (D(2026, 9, 3), 5000, 21, 0, 0),   # 行数掉档、msku 缺行 → 拒收，不是断货
            (D(2026, 9, 4), 8000, 12, 1, 0),   # 店数掉档、可售 0 → 拒收，不是断货
            (D(2026, 9, 5), 3000, 9, 1, 7)]    # 两样都掉、有货 → 拒收，也不是「有货」
    src = cs.ChSource(Q(rows))
    got = src.fba_stock_days("MSKU-C", "11094", D(2026, 9, 1), D(2026, 9, 7))
    assert got.censored == frozenset({D(2026, 9, 2)})
    assert got.captured == frozenset({D(2026, 9, 1), D(2026, 9, 2)})
    assert [r["date"] for r in got.rejected] == ["2026-09-03", "2026-09-04", "2026-09-05"]
    rejected_days = {D.fromisoformat(r["date"]) for r in got.rejected}
    assert not rejected_days & got.censored and not rejected_days & got.captured
    assert src.stats()["stock_days"] == {
        "rejected_dates": ["2026-09-03", "2026-09-04", "2026-09-05"],
        "uncaptured_dates": ["2026-09-06", "2026-09-07"]}
    assert caplog.text.count("op=ch_stock_days outcome=rejected_day") == 3
    assert "censored=1 rejected=3 uncaptured=2" in caplog.text


def test_fba_stock_days_stats_accumulate_across_keys_without_double_counting():
    """★ 拒收日是整表的事（掉档守卫看全表行数），不是某个 msku 的事 —— 多个 msku
    各问一遍，stats 按日期取并集，不按调用次数累加。"""
    rows = [(D(2026, 9, 1), 8000, 21, 1, 5), (D(2026, 9, 2), 100, 3, 0, 0)]
    src = cs.ChSource(Q(rows))
    src.fba_stock_days("MSKU-C", "11094", D(2026, 9, 1), D(2026, 9, 2))
    src.fba_stock_days("MSKU-A", "11072", D(2026, 9, 1), D(2026, 9, 3))
    assert src.stats()["stock_days"] == {"rejected_dates": ["2026-09-02"],
                                         "uncaptured_dates": ["2026-09-03"]}


def test_fba_stock_days_queries_the_detail_table_read_only():
    q = Q([(D(2026, 9, 2), 8000, 21, 0, 0)])
    got = cs.ChSource(q).fba_stock_days("MSKU-C", "11094", D(2026, 7, 21), D(2026, 9, 14))
    assert got.censored == frozenset({D(2026, 9, 2)})
    sql, params = q.calls[0]
    assert cs.FBA_DETAIL_TABLE in sql and "afn_fulfillable_quantity" in sql
    assert params == {"sid": "11094", "seller_sku": "MSKU-C", "start": "2026-07-21",
                      "end": "2026-09-14"}


def test_fba_stock_days_failure_is_classified():
    with pytest.raises(cs.ChUnavailable) as ei:
        cs.ChSource(Q(exc=ConnectionRefusedError(111, "refused"))).fba_stock_days(
            "MSKU-C", "11094", D(2026, 7, 21), D(2026, 9, 14))
    assert ei.value.target == cs.FBA_DETAIL_TABLE
