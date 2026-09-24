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
    q = Q([(D(2026, 9, 1), 4, 0), (D(2026, 9, 2), 0, 1)])
    got = cs.ChSource(q).daily_sales("MSKU-A", "11072", D(2026, 6, 17), D(2026, 9, 14))
    assert got == {D(2026, 9, 1): 4}          # 09-02 只有被取消的 → 不出行（终审 I-3）
    sql, params = q.calls[0]
    assert "GROUP BY amazon_order_id, order_item_id" in sql and "argMax(order_status" in sql
    store, channel = order_store_map.store_for("11072")
    assert params == {"store": store, "channel": channel, "seller_sku": "MSKU-A",
                      "start": "2026-06-17", "end": "2026-09-14"}


def test_daily_sales_outside_the_window_is_an_unknown_shape():
    q = Q([(D(2026, 9, 20), 4, 0)])
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
    src = cs.ChSource(Q([(D(2026, 9, 1), 4, 0)]))
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
    q = Q([(D(2026, 9, 1), 4, 0), (D(2026, 9, 1), 3, 0)])
    with pytest.raises(cs.UnknownShape, match="2026-09-01"):
        cs.ChSource(q).daily_sales("MSKU-A", "11072", D(2026, 6, 17), D(2026, 9, 14))


def test_parse_stock_days_three_ways():
    """OP-15：过了掉档守卫的日子里「缺行或 ≤ 0」= 断货；没过的日子不 censor、点名。"""
    rows = [(D(2026, 9, 1), 8000, 21, 1, 5),    # 采到了、有货
            (D(2026, 9, 2), 8000, 21, 0, 0),    # 采到了、缺行（L-1：0 库存不返回行）→ 断货
            (D(2026, 9, 3), 8000, 21, 2, 0),    # 采到了、可售 0 → 断货
            (D(2026, 9, 4), 100, 3, 0, 0)]      # 掉档 → 不 censor
    got = cs.parse_stock_days(rows, D(2026, 9, 1), D(2026, 9, 5), min_rows=7934,
                              min_distinct_sid=21, drop_threshold=0.30)
    assert got.censored == frozenset({D(2026, 9, 2), D(2026, 9, 3)})
    assert got.captured == frozenset({D(2026, 9, 1), D(2026, 9, 2), D(2026, 9, 3)})
    assert got.rejected == ({"date": "2026-09-04", "rows": 100, "uniq_sid": 3,
                             "reason": "rows_below_floor"},)


def test_parse_stock_days_uses_the_same_tolerance_as_pick_snapshot_date():
    """★ 控制器裁定（task-14 复核 Important #1）：采集日守卫与 pick_snapshot_date 同一把尺子
    —— rows < min_rows × (1 − drop_threshold) 才算掉档。实测 08-14..09-03 的整天是
    7,765~7,892 行 / 21 店，严格 7,934 会把它们全拒掉、连真断货也不 censor；
    20 行的残桩（07-24、08-13）仍必须被拒。"""
    rows = [(D(2026, 9, 3), 7800, 21, 1, 0),    # 整天，低于 E-1 最小值但在容差内 → 采到了、断货
            (D(2026, 8, 14), 7765, 21, 1, 3),   # 实测最早的整天 → 采到了、有货
            (D(2026, 8, 13), 20, 1, 0, 0)]      # 残桩 → 拒收
    got = cs.parse_stock_days(rows, D(2026, 8, 13), D(2026, 9, 3), min_rows=7934,
                              min_distinct_sid=21, drop_threshold=0.30)
    assert got.captured == frozenset({D(2026, 9, 3), D(2026, 8, 14)})
    assert got.censored == frozenset({D(2026, 9, 3)})
    assert got.rejected == ({"date": "2026-08-13", "rows": 20, "uniq_sid": 1,
                             "reason": "rows_below_floor"},)


def test_parse_stock_days_duplicate_capture_day_is_an_unknown_shape():
    rows = [(D(2026, 9, 1), 8000, 21, 1, 5), (D(2026, 9, 1), 8000, 21, 0, 0)]
    with pytest.raises(cs.UnknownShape, match="2026-09-01"):
        cs.parse_stock_days(rows, D(2026, 9, 1), D(2026, 9, 5), min_rows=7934,
                            min_distinct_sid=21, drop_threshold=0.30)


def test_fba_stock_days_rejected_days_are_neither_stock_out_nor_in_stock(caplog):
    """★ OP-15 第三类：没过掉档守卫的采集日（行数不够 / 店数不够）——那天 msku
    缺行或可售 0 都不能读成断货，有货也不能读成「采到了、有货」；只点名。
    被拒与没采到的日子都要进 stats() 和日志，不许静默消失。"""
    caplog.set_level(logging.INFO)
    rows = [(D(2026, 9, 1), 8000, 21, 1, 5),   # 采到了、有货
            (D(2026, 9, 2), 8000, 21, 0, 0),   # 采到了、缺行 → 断货
            (D(2026, 9, 3), 5000, 21, 0, 0),   # 行数掉档、msku 缺行 → 拒收，不是断货
            (D(2026, 9, 4), 8000, 12, 1, 0),   # 店数掉档（12 < 21×0.7）、可售 0 → 拒收
            (D(2026, 9, 5), 3000, 9, 1, 7)]    # 两样都掉、有货 → 拒收，也不是「有货」
    src = cs.ChSource(Q(rows))
    got = src.fba_stock_days("MSKU-C", "11094", D(2026, 9, 1), D(2026, 9, 7))
    assert got.censored == frozenset({D(2026, 9, 2)})
    assert got.captured == frozenset({D(2026, 9, 1), D(2026, 9, 2)})
    assert [(r["date"], r["reason"]) for r in got.rejected] == [
        ("2026-09-03", "rows_below_floor"), ("2026-09-04", "sid_below_floor"),
        ("2026-09-05", "rows_below_floor")]
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


def test_rejected_day_is_logged_once_per_source_not_once_per_msku(caplog):
    """★ 复核 Minor #1：拒收日是整表的事 —— 几百个 msku 各打一遍同一条 WARNING
    会把真正的告警淹掉。同一个 source 上每个拒收日只点名一次。"""
    caplog.set_level(logging.INFO)
    rows = [(D(2026, 9, 1), 8000, 21, 1, 5), (D(2026, 9, 2), 20, 1, 0, 0)]
    src = cs.ChSource(Q(rows))
    for msku in ("MSKU-A", "MSKU-B", "MSKU-C"):
        got = src.fba_stock_days(msku, "11072", D(2026, 9, 1), D(2026, 9, 2))
        assert [r["date"] for r in got.rejected] == ["2026-09-02"]
    assert caplog.text.count("op=ch_stock_days outcome=rejected_day") == 1
    assert caplog.text.count("rejected=1") == 3


def test_stock_days_units_are_null_safe_in_sql():
    """★ 复核 Minor #2：afn_fulfillable_quantity 是 Nullable，msku 缺行那天 sumIf 回 NULL
    （实测 (2026-07-24, 20, 1, 0, None)）。不许靠 hit==0 的短路兜住。"""
    sql = " ".join(cs.SQL_FBA_STOCK_DAYS.split())
    assert "ifNull(sumIf(afn_fulfillable_quantity," in sql and ", 0)) AS units" in sql


def test_daily_sales_counts_cancelled_or_pending_order_items_it_zeroed(caplog):
    """★ 复核 Minor #4：Cancelled/Pending 在 SQL 里被记成 0 件 —— 排除可以，必须有声：
    每个键记进 history_window，整个 source 的合计进 stats()。"""
    caplog.set_level(logging.INFO)
    src = cs.ChSource(Q([(D(2026, 9, 1), 4, 2), (D(2026, 9, 2), 0, 1)]))
    src.daily_sales("MSKU-A", "11072", D(2026, 9, 1), D(2026, 9, 3))
    assert src.history_window("MSKU-A", "11072")["zeroed_order_items"] == 3
    src.daily_sales("MSKU-A", "11072", D(2026, 9, 1), D(2026, 9, 3))   # 同键再问一遍不重复计
    src2_rows = Q([(D(2026, 9, 1), 1, 5)])
    src._q = src2_rows
    src.daily_sales("MSKU-B", "11072", D(2026, 9, 1), D(2026, 9, 3))
    assert src.stats()["daily_sales"] == {"cancelled_or_pending_zeroed_order_items": 8,
                                          "keys": 2}
    assert "zeroed_order_items=3" in caplog.text
    sql = " ".join(cs.SQL_DAILY_SALES.split())
    assert "IN ('Cancelled', 'Pending')" in sql and "AS zeroed_order_items" in sql


def test_daily_sales_cancelled_only_msku_has_no_history_not_rate_zero(caplog):
    """★ 终审 I-3 裁定：窗口里只有取消 / 待付订单的 msku —— SQL 会给每天一行 units=0。
    这些行不许出现在日销里：出了，baseline_rate 就把它当成「卖了 0」给 rate 0；
    不出，它与一行订单都没有的 msku 一样是「没有销量」（未知）。被置零的 order_item
    照记进 zeroed_order_items —— 丢可以，必须有声。"""
    caplog.set_level(logging.INFO)
    src = cs.ChSource(Q([(D(2026, 9, 1), 0, 2), (D(2026, 9, 3), 0, 1)]))
    assert src.daily_sales("MSKU-A", "11072", D(2026, 9, 1), D(2026, 9, 3)) == {}
    w = src.history_window("MSKU-A", "11072")
    assert (w["days_with_orders"], w["zeroed_order_items"], w["zero_unit_days"]) == (0, 3, 2)
    assert "zero_unit_days=2" in caplog.text


def test_daily_sales_zero_unit_rows_are_still_checked_for_duplicates():
    """丢 0 件行不许顺带丢掉「一天两行」这道形状闸。"""
    q = Q([(D(2026, 9, 1), 0, 1), (D(2026, 9, 1), 3, 0)])
    with pytest.raises(cs.UnknownShape, match="2026-09-01"):
        cs.ChSource(q).daily_sales("MSKU-A", "11072", D(2026, 6, 17), D(2026, 9, 14))
