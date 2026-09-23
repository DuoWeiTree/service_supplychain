"""ChSource 预测取数的纯变换。★ 假 query 只按 SQL 的关键字回放，不真连 CH。

Fix round 1（团队负责人裁定 2026-09-23）：
· 绝对下限从硬编码常量搬进 `[forecast]` 配置（`min_rows` / `min_distinct_sid`），
  `pick_snapshot_date` 不再读模块级常量，改吃调用方传入的地板参数——常量焊在
  代码里会在业务量变化后腐烂成「再也不会触发」或「永远触发」。
· 拒绝理由拆成两种、按指标（rows/sid）再各拆一次——`*_drop_vs_neighbour`
  （跟前一天比掉了）与 `*_below_floor`（压根没到过底线，哪怕邻居也一样低）——
  运营看日志才分得清「今天突然塌了」和「已经烂了好几天」。
· 「已写完」判定挪进 ClickHouse 自己算（`now() - INTERVAL 30 MINUTE >
  max(_captured_at)`），本文件的假 query 直接回放这个布尔值，不再在 Python
  侧模拟一个假的 wall clock——跨机器比较两个 naive datetime 是本轮要修的
  bug 本身（sandbox 系统时区与 CH 不一致时会静默算错，且不报错）。

Fix round 2（团队负责人裁定 2026-09-23）：
· `ChUnavailable` 之前定义了从没被抛出过——这里补上：`Query` 抛异常时，
  `ChSource.as_of()` 必须分类后抛 `ChUnavailable`，不许让原始 driver 异常
  漏出去。分类复用 `shared.ch_client.describe_failure`（同一套区分「超时」
  与「连不上」的机器——`dim/` 不许 import 它，测试文件不受这条限制，直接
  注入真正的分类器，而不是自己另造一套判断逻辑）。
· `[forecast]` 的 `snapshot_lookback_days`/`snapshot_settle_minutes` 之前
  声明了没人读，现在真的接进 `ChSource.__init__`（`lookback_days`/
  `settle_minutes`），下面补「改了会变」的测试。

Task 3（`purchase_in_transit`，controller 裁定 09-23，design §4.4 / §8 OQ-5 OQ-6）：
· ★★ 必须按采购单 `status` 过滤，不能只靠 `quantity_real > quantity_receive`——
  E-12 实测 `status=9（已完成）` 的行项 `quantity_receive` 全为 0，只按算术
  会多出真实开口量的 3.7 倍。
· `expect_arrive_time` 为 NULL 的行（E-14）：丢弃 + 计数，不许落进 `else ''`
  再被下游过滤掉。
· `purchase_as_of` 用采购表自己的 `max(_captured_date)`（覆盖型表，没有历史
  快照，E-14 实测比 fba_detail 晚一天）——陈旧阈值（OQ-5）与 fba_detail 的
  `as_of()` 比较年龄，超过 `purchase_staleness_days` 就 `PurchaseTableStale`，
  不许把陈旧数据读成「在途为 0」。
· `is_overdue`（OQ-6）是纯函数：`period` 早于 `as_of` 所在月即逾期——不重新
  定日期，归桶留给装配层（Task 5）。
"""
from __future__ import annotations

import datetime as dt

import pytest

from dim import ch_source as cs
from shared.ch_client import describe_failure

#: 实测形状（design §1.3 E-1/E-3）：近 20 个采集日 7,934~8,080 行、每日恰好 21 个 sid，
#: 当天 06:34~07:39 起跑、约 60 秒跑完。
FULL = 8000
#: ★ E-1 实测下限，与 `shared.config._FORECAST_DEFAULTS` 的
#:   `min_rows`/`min_distinct_sid` 保持同一个数（design §1.3 E-1，2026-09-23）。
MIN_ROWS = 7934
MIN_SID = 21


def day(d: int, rows: int = FULL, sids: int = 21, settled: bool = True):
    """★「已写完」现在由 CH 用它自己的 `now()` 算好、随行一起回来——
    这里直接回放那个布尔值，不再在 Python 侧摆一个假的 wall clock。"""
    return (dt.date(2026, 9, d), rows, sids, settled)


def test_picks_the_newest_settled_day():
    got, rejected = cs.pick_snapshot_date(
        [day(23), day(22), day(21)], 0.30, MIN_ROWS, MIN_SID)
    assert got == dt.date(2026, 9, 23)
    assert rejected == []


def test_rejects_a_half_written_batch_and_names_it():
    """★ 半写的当天批次：行数只有一半 —— 必须退一天，且退这一步要留下证据。
    这是「跟前一天比掉了」（drop_vs_neighbour）：虽然 4000 也踩在 min_rows 的
    地板下面，但有邻居可比时优先报更具体的那种——「昨天还好好的」比
    「压根没到过底线」更能定位到哪天开始坏的。"""
    got, rejected = cs.pick_snapshot_date(
        [day(23, rows=4000), day(22), day(21)], 0.30, MIN_ROWS, MIN_SID)
    assert got == dt.date(2026, 9, 22)
    assert rejected == [{"date": "2026-09-23", "rows": 4000, "uniq_sid": 21,
                         "reason": "rows_drop_vs_neighbour"}]


def test_rejects_a_batch_still_being_written():
    """★ 「已写完」现在由 CH 自己的 now() 判定，这里直接回放 settled=False——
    不用再在 Python 侧摆一个假的 now（design §4.1 判据 1 / Fix round 1）。"""
    got, rejected = cs.pick_snapshot_date(
        [day(23, settled=False), day(22), day(21)], 0.30, MIN_ROWS, MIN_SID)
    assert got == dt.date(2026, 9, 22)
    assert rejected[0]["reason"] == "not_settled"


def test_rejects_a_day_that_lost_stores_even_when_row_count_holds():
    """★ 整店 0 行是采集缺口的形状，总行数看不出来（jobs/refresh_dims.py:150-153 同一条）。"""
    got, rejected = cs.pick_snapshot_date(
        [day(23, sids=12), day(22), day(21)], 0.30, MIN_ROWS, MIN_SID)
    assert got == dt.date(2026, 9, 22)
    assert rejected[0]["reason"] == "sid_drop_vs_neighbour"


def test_all_candidates_rejected_raises_with_every_number():
    """★ 三天行数一样低（100），彼此之间比不出「掉」——纯相邻比较的洞。
    必须靠 min_rows 这条绝对地板兜住，理由是 rows_below_floor（整窗口一直
    没好过），不是 rows_drop_vs_neighbour（那是「昨天还好好的」）。"""
    days = [day(23, rows=100), day(22, rows=100), day(21, rows=100)]
    with pytest.raises(cs.ChDataUnusable) as e:
        cs.pick_snapshot_date(days, 0.30, MIN_ROWS, MIN_SID)
    assert len(e.value.rejected) == 3
    assert "2026-09-23" in str(e.value)
    assert all(r["reason"] == "rows_below_floor" for r in e.value.rejected)


def test_all_candidates_rejected_when_sid_uniformly_below_floor():
    """★ 同一个洞的另一半：行数正常但 sid 数三天都一样低于地板——必须报
    sid_below_floor，不是 sid_drop_vs_neighbour（邻居也一样低，比不出掉档）。"""
    days = [day(23, sids=10), day(22, sids=10), day(21, sids=10)]
    with pytest.raises(cs.ChDataUnusable) as e:
        cs.pick_snapshot_date(days, 0.30, MIN_ROWS, MIN_SID)
    assert len(e.value.rejected) == 3
    assert all(r["reason"] == "sid_below_floor" for r in e.value.rejected)


def test_empty_candidate_list_is_not_a_silent_empty():
    with pytest.raises(cs.UnknownShape, match="lingxing_inventory_fba_detail"):
        cs.pick_snapshot_date([], 0.30, MIN_ROWS, MIN_SID)


def test_as_of_caches_within_ttl_and_rebuilds_after():
    calls = []

    def q(sql: str):
        calls.append(sql)
        return [day(23), day(22)]

    # ★ 这个 clock 只用于 ChSource 的 TTL 自比（前后两次读数相减），不参与
    #   「已写完」判定——那条已经搬进 CH 自己的 now()（Fix round 1）。自比
    #   安全，不管系统时区是什么。
    clock = [dt.datetime(2026, 9, 23, 12, 0, 0)]  # noqa: DTZ001
    src = cs.ChSource(q, now=lambda: clock[0], cache_ttl_s=300)
    assert src.as_of() == dt.date(2026, 9, 23)
    assert src.as_of() == dt.date(2026, 9, 23)
    assert len(calls) == 1, "TTL 内重复解析 = 每次 grid 都多打一次 CH"
    clock[0] += dt.timedelta(seconds=301)
    src.as_of()
    assert len(calls) == 2, "TTL 过了还不重建 = 缓存在保留陈旧"


def _raise(exc: BaseException):
    def q(sql: str):
        raise exc
    return q


def test_connect_refused_becomes_ch_unavailable_with_the_right_kind():
    """★ 用真正的 describe_failure 分类（不是另造一套）——ch_client 文档记录的
    真实形态：`... ← ConnectionRefusedError(111)`。"""
    try:
        raise ConnectionRefusedError(111, "Connection refused")
    except ConnectionRefusedError as inner:
        exc = RuntimeError("driver 包了一层")
        exc.__cause__ = inner

    src = cs.ChSource(_raise(exc), classify_failure=describe_failure)
    with pytest.raises(cs.ChUnavailable) as e:
        src.as_of()
    assert e.value.cause["kind"] == "connect_refused"
    assert e.value.target == cs.FBA_DETAIL_TABLE
    # ★ 原始 driver 异常类型不许漏出去——调用方只应该看见 ChUnavailable。
    assert not isinstance(e.value, RuntimeError)


def test_timeout_becomes_ch_unavailable_with_the_right_kind():
    """★ 同上，真实形态：`... ← ConnectTimeoutError ← TimeoutError`——
    「超时」与「连不上」处置相反，kind 必须能分得开。"""
    try:
        raise TimeoutError("timed out")
    except TimeoutError as inner:
        exc = RuntimeError("driver 包了一层")
        exc.__cause__ = inner

    src = cs.ChSource(_raise(exc), classify_failure=describe_failure)
    with pytest.raises(cs.ChUnavailable) as e:
        src.as_of()
    assert e.value.cause["kind"] == "timeout"
    assert not isinstance(e.value, RuntimeError)


def test_ch_unavailable_default_classifier_still_fires_without_injection():
    """★ 不注入 classify_failure 时（dim/ 不许 import shared.ch_client，没法
    默认就用真分类器）也必须抛 ChUnavailable，只是 kind 诚实地写 "unknown"——
    不能因为没注入分类器就让原始异常漏出去。"""
    src = cs.ChSource(_raise(RuntimeError("boom")))
    with pytest.raises(cs.ChUnavailable) as e:
        src.as_of()
    assert e.value.cause["kind"] == "unknown"


def test_lookback_days_is_wired_into_the_sql():
    """★ [forecast].snapshot_lookback_days 之前声明了没人读——这里证明
    改它是真的会改变发给 CH 的 SQL，不是一个不生效的旋钮。"""
    calls = []

    def q(sql: str):
        calls.append(sql)
        return [day(23)]

    cs.ChSource(q, lookback_days=3).as_of()
    assert "today() - 3" in calls[0]


def test_settle_minutes_is_wired_into_the_sql():
    """★ 同上，snapshot_settle_minutes。"""
    calls = []

    def q(sql: str):
        calls.append(sql)
        return [day(23)]

    cs.ChSource(q, settle_minutes=10).as_of()
    assert "INTERVAL 10 MINUTE" in calls[0]


def test_lookback_days_shows_up_in_the_empty_candidate_error():
    """★ pick_snapshot_date 自己也吃这个参数（用于报错文案），不是只在 SQL
    格式化里用一次就丢掉。"""
    with pytest.raises(cs.UnknownShape, match=r"近 3 天"):
        cs.pick_snapshot_date([], 0.30, MIN_ROWS, MIN_SID, lookback_days=3)


#: ★ 实测形状（design §1.3 E-4）：sid=0 是欧洲共享池
#:   （name='PETSFIT-JXD-UK欧洲仓'，seller_group_name='PETSFIT-JXD-PL,PETSFIT-JXD-SE'），
#:   1,114 行 / 14,073 件，占全部可售 50,947 件的 27.6%。它不是任何单店的在仓。
ONHAND_ROWS = [
    ("11072", "DCC1800264G1Z2B", 672, 1),
    ("11072", "DVCD105013ALZ2B", 1323, 1),
    ("0", "002F2RedM", 14073, 1114),      # ← 共享池，必须被排除且计数
    ("11094", "MSKU-UK-1", 0, 1),         # ← 真的是 0，不是缺行
]


def onhand_query(rows=None, days=None):
    def q(sql: str):
        if "lingxing_inventory_fba_detail" in sql and "_captured_date >=" in sql:
            return days if days is not None else [day(23), day(22)]
        if "afn_fulfillable_quantity" in sql:
            return ONHAND_ROWS if rows is None else rows
        raise AssertionError(f"没预料到的 SQL：{sql[:80]}")
    return q


def test_shared_pool_sid_zero_is_excluded_and_counted():
    """★ 27.6% 的可售被排除，不留痕就再也没人想得起来它去哪了。"""
    by_key, dropped = cs.parse_onhand(ONHAND_ROWS)
    assert ("0", "002F2RedM") not in by_key
    assert dropped == {"shared_pool_excluded": {"rows": 1114, "units": 14073}}


def test_a_real_zero_is_zero_not_missing():
    by_key, _ = cs.parse_onhand(ONHAND_ROWS)
    assert by_key[("11094", "MSKU-UK-1")] == 0


def test_msku_absent_from_an_accepted_batch_is_zero():
    """★ L-1：FBA 报表不返回库存为 0 的 SKU —— 缺行 = 0，而断货正是最该被看见的状态。"""
    src = cs.ChSource(onhand_query())
    assert src.onhand_available("NEVER-STOCKED", "11072") == 0


def test_onhand_reads_the_snapshot_once_for_many_mskus():
    calls = []
    base = onhand_query()

    def q(sql):
        calls.append(sql)
        return base(sql)

    src = cs.ChSource(q)
    for msku in ("DCC1800264G1Z2B", "DVCD105013ALZ2B", "NEVER-STOCKED"):
        src.onhand_available(msku, "11072")
    assert sum("afn_fulfillable_quantity" in s for s in calls) == 1, (
        "grid 逐 msku 调 21 次 —— 不批量就是 21 次往返")


def test_unusable_batch_raises_instead_of_returning_zero():
    """★ 这条是整个设计的支点：拿不到数时返回 0 会让「断货」和「查不到」长得一样。"""
    bad = [day(23, rows=100), day(22, rows=100)]
    src = cs.ChSource(onhand_query(days=bad))
    with pytest.raises(cs.ChDataUnusable):
        src.onhand_available("DCC1800264G1Z2B", "11072")


def test_duplicate_rows_are_summed_and_counted():
    """★ 今天一行不重（实测 8,080/8,080 n=1），但重复行是 L-4 点名的形态。"""
    rows = [("11072", "X", 10, 1), ("11072", "X", 5, 1)]
    by_key, dropped = cs.parse_onhand(rows)
    assert by_key[("11072", "X")] == 15
    assert dropped["rows_collapsed"] == 1


def test_a_single_groups_own_raw_rows_above_one_is_collapsed_and_counted():
    """★ 与上一条测的是**另一种**重复形态：这里只有一条 Python 行，但它自己的
    `count()`（SQL 的 GROUP BY 已经把 3 条原始表行 sum 到这一条里）> 1——
    SQL 端已经正确 sum() 过了，这条测试只管「有没有把这件事记下来」。
    ★ review 发现：这条分支此前一个测试都没覆盖——删掉它，
    `test_shared_pool_sid_zero_is_excluded_and_counted` /
    `test_a_real_zero_is_zero_not_missing` / `test_duplicate_rows_are_summed_and_counted`
    三条全部照样通过（共享池行先 `continue` 跳过了这条分支，上一条测试走的是
    「同一个 key 出现在两条不同的返回行里」那另一种形态）。今天生产数据里没有
    任何非共享池的 (sid, seller_sku) 分组 `raw_rows > 1`（E-1：8,080/8,080 全部
    n=1），所以这条分支目前在真实数据上不可观测——这条测试就是唯一能证伪它
    的地方。"""
    rows = [("11072", "Y", 30, 3)]
    by_key, dropped = cs.parse_onhand(rows)
    assert by_key[("11072", "Y")] == 30
    assert dropped["rows_collapsed"] == 2


def test_onhand_query_failure_becomes_ch_unavailable():
    """★ as_of() 拿到快照日之后，在仓批量查询本身仍可能失败（连接在两次
    往返之间断掉）——必须走 as_of() 同一条 classify_failure/ChUnavailable
    路径，不许让裸 driver 异常从 onhand_available 漏出去（同 Task 1 的
    ChUnavailable 曾经「定义了但从没抛过」那个教训，见 progress.md Task 1
    Fix round 2）。"""

    def q(sql: str):
        if "lingxing_inventory_fba_detail" in sql and "_captured_date >=" in sql:
            return [day(23), day(22)]
        if "afn_fulfillable_quantity" in sql:
            raise ConnectionRefusedError(111, "Connection refused")
        raise AssertionError(f"没预料到的 SQL：{sql[:80]}")

    src = cs.ChSource(q, classify_failure=describe_failure)
    with pytest.raises(cs.ChUnavailable) as e:
        src.onhand_available("DCC1800264G1Z2B", "11072")
    assert e.value.cause["kind"] == "connect_refused"
    assert e.value.target == cs.FBA_DETAIL_TABLE


def test_onhand_stats_reports_the_drop_reasons_from_the_last_batch():
    """★ `stats()` 供上层写进日志与响应（brief 接口 4）——不是内部细节。"""
    src = cs.ChSource(onhand_query())
    src.onhand_available("DCC1800264G1Z2B", "11072")
    assert src.stats() == {"onhand": {"shared_pool_excluded": {"rows": 1114, "units": 14073}}}


def test_onhand_cache_rebuilds_when_as_of_moves_to_a_new_day():
    """★ 缓存键是解析出来的快照日——同一天不可变，日期一变整份丢弃重建
    （不合并，记忆 defaults-preserve-staleness）。TTL 到期但候选日不变时
    缓存应当保留（同一天重建也是浪费），所以这条测试真的把候选日往前推
    一天，而不是只让 TTL 过期。"""
    calls = []
    state = {"days": [day(23), day(22)]}

    def q(sql):
        calls.append(sql)
        if "lingxing_inventory_fba_detail" in sql and "_captured_date >=" in sql:
            return state["days"]
        if "afn_fulfillable_quantity" in sql:
            if "2026-09-23" in sql:
                return [("11072", "X", 1, 1)]
            return [("11072", "X", 2, 1)]
        raise AssertionError(f"没预料到的 SQL：{sql[:80]}")

    clock = [dt.datetime(2026, 9, 23, 12, 0, 0)]  # noqa: DTZ001
    src = cs.ChSource(q, now=lambda: clock[0], cache_ttl_s=300)
    assert src.onhand_available("X", "11072") == 1
    assert src.onhand_available("X", "11072") == 1
    assert sum("afn_fulfillable_quantity" in c for c in calls) == 1

    clock[0] += dt.timedelta(seconds=301)
    state["days"] = [day(24), day(23)]
    calls.clear()
    assert src.onhand_available("X", "11072") == 2
    assert sum("afn_fulfillable_quantity" in c for c in calls) == 1


# ---------------------------------------------------------------------------
# Task 3: purchase_in_transit —— 按采购单状态过滤，不按数量算术（design §4.4）
# ---------------------------------------------------------------------------

#: ★ 实测形状（design §1.3 E-12/E-13/E-14）：
#:   · status=9（已完成）的行项 quantity_receive **全为 0** —— 966 行 / 104,479 件。
#:     只按 `quantity_real - quantity_receive > 0` 取在途，会多出真实开口量的 3.7 倍。
#:     SQL 端已经按 `o.st = 2` 过滤掉这批，所以下面的假行只出现「待到货」形态的数据。
#:   · expect_arrive_time 有 1 行为 NULL（50 件）。
PURCHASE_ROWS = [
    ("DVCD105013L1", "2026-10", 600, "PO260514011"),
    ("DVCD105013L1", "2026-11", 240, "PO260514011"),
    ("DVCD105013AL", "2026-10", 500, "PO260721011"),
    ("NO-ETA-SKU", None, 50, "PO260514011"),      # ← expect_arrive_time 为 NULL
]


def purchase_query(captured=dt.date(2026, 9, 22), fba_days=None, rows=None,
                    fail_as_of=None, fail_in_transit=None):
    """★ 与 onhand_query 同一个思路：按 SQL 的关键字分发，不真连 CH。

    `_captured_date >=` 命中 fba_detail 的候选日 SQL（`as_of()` 用它做陈旧比较
    的参照基准）；`max(_captured_date)` + `purchase_order_list_items` 命中
    `purchase_as_of()`；`quantity_receive` 命中 `purchase_in_transit` 的批量取数。
    """
    def q(sql: str):
        if "_captured_date >=" in sql:
            return fba_days if fba_days is not None else [day(23), day(22)]
        if "max(_captured_date)" in sql and "purchase_order_list_items" in sql:
            if fail_as_of is not None:
                raise fail_as_of
            return [(captured,)]
        if "quantity_receive" in sql:
            if fail_in_transit is not None:
                raise fail_in_transit
            return rows if rows is not None else PURCHASE_ROWS
        raise AssertionError(f"没预料到的 SQL：{sql[:80]}")
    return q


def test_missing_eta_is_dropped_loudly_not_silently():
    """★ 认不出的形态不许落进 else '' 再被下游过滤掉 —— 丢了必须数得出来，
    且必须点名 order_sn（design §4.4，review fix round 1）——只留计数意味着
    复现时还得手工连一次 CH 才查得出是哪张单丢的。"""
    by_sku, dropped = cs.parse_in_transit(PURCHASE_ROWS)
    assert "NO-ETA-SKU" not in by_sku
    assert dropped == {"missing_eta_rows": 1, "missing_eta_units": 50,
                       "missing_eta_refs": ["PO260514011"]}


def test_in_transit_rows_keep_their_order_number():
    by_sku, _ = cs.parse_in_transit(PURCHASE_ROWS)
    assert by_sku["DVCD105013L1"] == [
        cs.InTransit("2026-10", 600, "PO260514011"),
        cs.InTransit("2026-11", 240, "PO260514011"),
    ]


def test_sql_filters_on_order_status_not_on_quantity_arithmetic():
    """★ 这条守的是 E-12 那个坑：已完成单的 quantity_receive 全是 0。
    光靠 `real > receive` 会把 104,479 件已到货的货算成在途。"""
    sql = " ".join(cs.SQL_PURCHASE_IN_TRANSIT.split())
    assert "argMax(status" in sql, "必须按采购单当前状态判，不是按行项算术"
    assert "o.st = 2" in sql, "只有「待到货」算在途"
    assert "is_delete" in sql


def test_unknown_sku_returns_empty_list_not_none():
    src = cs.ChSource(purchase_query())
    assert src.purchase_in_transit("NOT-A-SKU") == []
    assert src.purchase_as_of() == dt.date(2026, 9, 22)


def test_in_transit_reads_the_batch_once_for_many_skus():
    """★ 同 onhand_available 的批量道理：grid 逐货号调用，不能每次都打一次 CH。"""
    calls = []
    base = purchase_query()

    def q(sql):
        calls.append(sql)
        return base(sql)

    src = cs.ChSource(q)
    for sku in ("DVCD105013L1", "DVCD105013AL", "NOT-A-SKU"):
        src.purchase_in_transit(sku)
    assert sum("quantity_receive" in c for c in calls) == 1


def test_purchase_as_of_empty_result_is_unknown_shape_not_silent():
    """★ 一个采集日都没有 —— 空不是「没有在途」，是采集缺口。"""
    def q(sql):
        if "_captured_date >=" in sql:
            return [day(23), day(22)]
        if "max(_captured_date)" in sql and "purchase_order_list_items" in sql:
            return [(None,)]
        raise AssertionError(f"没预料到的 SQL：{sql[:80]}")

    src = cs.ChSource(q)
    with pytest.raises(cs.UnknownShape, match="purchase_order_list_items"):
        src.purchase_as_of()


def test_purchase_as_of_query_failure_becomes_ch_unavailable():
    """★ 同 as_of()/onhand_available 的模式 —— 查询失败必须分类后抛
    ChUnavailable，不许让原始 driver 异常漏出去。"""
    exc = ConnectionRefusedError(111, "Connection refused")
    src = cs.ChSource(purchase_query(fail_as_of=exc), classify_failure=describe_failure)
    with pytest.raises(cs.ChUnavailable) as e:
        src.purchase_as_of()
    assert e.value.cause["kind"] == "connect_refused"
    assert e.value.target == cs.PURCHASE_ITEMS_TABLE


def test_in_transit_query_failure_becomes_ch_unavailable():
    """★ `purchase_as_of()` 成功之后，批量取数本身仍可能在两次往返之间掉线。"""
    exc = ConnectionRefusedError(111, "Connection refused")
    src = cs.ChSource(purchase_query(fail_in_transit=exc), classify_failure=describe_failure)
    with pytest.raises(cs.ChUnavailable) as e:
        src.purchase_in_transit("ANY-SKU")
    assert e.value.cause["kind"] == "connect_refused"
    assert e.value.target == cs.PURCHASE_ITEMS_TABLE


def test_stats_reports_in_transit_dropped_from_the_last_batch():
    """★ Task 2 的教训：计数器不写测试就是装饰——这条证明真的写进了 stats()，
    且带着 order_sn（review fix round 1）。"""
    src = cs.ChSource(purchase_query())
    src.purchase_in_transit("DVCD105013L1")
    assert src.stats()["in_transit"] == {"missing_eta_rows": 1, "missing_eta_units": 50,
                                         "missing_eta_refs": ["PO260514011"]}


def test_missing_eta_warning_names_the_order_sn_not_just_a_count(scm_log):
    """★ design §4.4：丢弃 + WARNING 点名 order_sn 与件数，不是只留一个计数——
    只留计数意味着复现时还得手工连一次 CH 才查得出是哪张单丢的（review fix
    round 1）。这条测试专门守「count 有、ref 没有」这种退化：只要实现退回成
    只记 missing_eta_rows/units 而不把 ref 带进日志，下面的断言就会因为日志
    文本里找不到 order_sn 而红。★ 用 `scm_log` 而不是裸 `caplog`——`scm.dim`
    挂在 `scm` 下面，一旦本进程别的测试先跑过 `setup_logging()`（`scm.propagate
    = False`），裸 caplog 对着 root 的 handler 就再也抓不到，会静默变成
    「谁先跑就谁绿」（conftest.py `scm_log` 文档已经点名这个坑）。"""
    src = cs.ChSource(purchase_query())
    src.purchase_in_transit("DVCD105013L1")
    warnings = [r.getMessage() for r in scm_log.records if r.levelname == "WARNING"]
    text = " ".join(warnings)
    assert "PO260514011" in text, (
        f"NULL-ETA 那行的 order_sn（PO260514011）必须出现在 WARNING 里——"
        f"实际 WARNING 内容：{warnings!r}")


def test_missing_eta_warning_caps_a_long_ref_list_with_a_tail(scm_log):
    """★ 丢弃行一多，日志行本身不能被撑爆——超过上限截断并附「+N more」尾巴，
    但 `stats()` 里的计数与 refs 列表仍是完整的，截断只发生在日志文本里。"""
    many_rows = [(f"SKU-{i}", None, 1, f"PO-{i:03d}") for i in range(25)]
    src = cs.ChSource(purchase_query(rows=many_rows))
    src.purchase_in_transit("ANY-SKU")
    assert src.stats()["in_transit"]["missing_eta_rows"] == 25
    assert len(src.stats()["in_transit"]["missing_eta_refs"]) == 25, (
        "stats() 不许被截断——截断只应该发生在日志文本，不是丢弃计数本身")
    warnings = [r.getMessage() for r in scm_log.records if r.levelname == "WARNING"]
    text = " ".join(warnings)
    assert "+5 more" in text, (
        f"25 个 order_sn 超过展开上限 20，日志必须截断并留下「+5 more」尾巴，"
        f"不能把全部 25 个原样倒出来：实际 WARNING 内容：{warnings!r}")
    assert "PO-000" in text and "PO-019" in text, "截断前的那 20 个应当原样出现"
    assert "PO-024" not in text, "第 21~25 个应当被截断掉，不在展开列表里"


def test_purchase_table_stale_raises_instead_of_reading_as_zero():
    """★ OQ-5：采购表比 fba_detail 快照日老太多 —— 必须报「未知」，不能悄悄当 0。
    fba as_of=2026-09-23（day(23) 结算），采购 captured=2026-09-18，age_days=5，
    超过默认阈值 3。"""
    src = cs.ChSource(purchase_query(captured=dt.date(2026, 9, 18)))
    with pytest.raises(cs.PurchaseTableStale) as e:
        src.purchase_as_of()
    assert e.value.captured == dt.date(2026, 9, 18)
    assert e.value.age_days == 5
    assert e.value.threshold_days == 3


def test_purchase_table_within_threshold_is_not_stale():
    """★ 边界：age_days 恰好等于阈值（3）不算陈旧 —— E-14 实测本来就常态晚 1 天，
    3 天是「漏采一天仍可用」的余量，不是「漏采一天就报警」。"""
    src = cs.ChSource(purchase_query(captured=dt.date(2026, 9, 20)))
    assert src.purchase_as_of() == dt.date(2026, 9, 20)


def test_purchase_staleness_days_is_wired_into_the_check():
    """★ `[forecast].purchase_staleness_days` 之前不存在——这里证明它是真的
    构造参数、真的改变判定，不是一个不生效的旋钮（同 lookback_days 那条测试
    的教训）。captured=2026-09-21，age_days=2：默认阈值 3 不会报，阈值收紧到
    1 就必须报。"""
    src = cs.ChSource(purchase_query(captured=dt.date(2026, 9, 21)),
                       purchase_staleness_days=1)
    with pytest.raises(cs.PurchaseTableStale) as e:
        src.purchase_as_of()
    assert e.value.age_days == 2
    assert e.value.threshold_days == 1


def test_is_overdue_true_for_a_past_month():
    assert cs.is_overdue("2026-06", dt.date(2026, 9, 23)) is True


def test_is_overdue_false_for_the_current_month():
    assert cs.is_overdue("2026-09", dt.date(2026, 9, 23)) is False


def test_is_overdue_false_for_a_future_month():
    assert cs.is_overdue("2026-10", dt.date(2026, 9, 23)) is False


def test_is_overdue_false_for_a_future_year():
    assert cs.is_overdue("2027-01", dt.date(2026, 9, 23)) is False


# ---------------------------------------------------------------------------
# Task 4: monthly_sales_history —— 去重 · store→sid 映射 · 只取完整月
# （design §4.2，controller 裁定 09-23，见 §8 OQ-1）
# ---------------------------------------------------------------------------

def test_month_window_never_includes_the_current_month():
    """★ 当月是半个月 —— 实测 2026-09 只有 9,130 件 vs 8 月 11,906 件，
    混进去会被读成 23% 的下滑。"""
    got = cs.month_window(dt.date(2026, 9, 23), 3)
    assert got == [dt.date(2026, 6, 1), dt.date(2026, 7, 1), dt.date(2026, 8, 1)]


def test_interior_empty_months_are_filled_with_zero_and_counted():
    """★ 补 0 是为了 monthly_estimate 不抛 HistoryGap（forecast/estimate.py:57），
    而「这个月一件没卖」与「这个月没数据」是两件事 —— 后者不许补，要截短窗口。"""
    window = [dt.date(2026, 6, 1), dt.date(2026, 7, 1), dt.date(2026, 8, 1)]
    series, filled = cs.fill_months(window, {dt.date(2026, 6, 1): 561,
                                             dt.date(2026, 8, 1): 318})
    assert series == [(dt.date(2026, 6, 1), 561), (dt.date(2026, 7, 1), 0),
                      (dt.date(2026, 8, 1), 318)]
    assert filled == 1


def test_no_rows_at_all_returns_empty_not_zeros():
    """★ 协议原话：没有行就返回空列表 —— 不补 0（dim/source.py:25）。
    新品没数据与卖了 0 件必须分得开（forecast/estimate.py:22）。"""
    series, filled = cs.fill_months([dt.date(2026, 8, 1)], {})
    assert series == []
    assert filled == 0


def test_sql_dedupes_by_order_item():
    """★ 实测 8,980 组 (amazon_order_id, order_item_id) 出现两次，跨采集日状态会变
    （Pending → Shipped）。不去重时 2026-08 是 13,611 件、去重后 11,906（−12.5%），
    2026-09 是 11,331 → 9,130（−19.4%）。兄弟仓那条 SQL 没有这一步。"""
    sql = " ".join(cs.SQL_MONTHLY_SALES.split())
    assert "GROUP BY amazon_order_id, order_item_id" in sql
    assert "argMax(order_status" in sql and "argMax(quantity" in sql
    assert "'Cancelled'" in sql and "'Pending'" in sql


def _sales_query(rows=None, fail=None, as_of_days=None):
    """★ 同 onhand_query/purchase_query 的思路：按 SQL 关键字分发，不真连 CH。
    `_captured_date >=` 命中 `as_of()` 的候选日 SQL；`toStartOfMonth` 命中
    月销量批量取数。让 `as_of()` 走正常路径解析出 2026-09-23，而不是直接戳
    ChSource 的私有属性。"""
    def q(sql: str):
        if "_captured_date >=" in sql:
            return as_of_days if as_of_days is not None else [day(23), day(22)]
        if "toStartOfMonth" in sql:
            if fail is not None:
                raise fail
            return rows if rows is not None else []
        raise AssertionError(f"没预料到的 SQL：{sql[:80]}")
    return q


def test_monthly_sales_history_looks_up_store_via_sid_and_returns_full_months():
    """★ sid → (store, sales_channel) 走 dim/order_store_map.py，不在这里解析。
    窗口 = as_of 所在月往前数 months 个完整自然月，升序、月初。"""
    rows = [(dt.date(2026, 6, 1), 561), (dt.date(2026, 7, 1), 254),
            (dt.date(2026, 8, 1), 318)]
    src = cs.ChSource(_sales_query(rows), now=lambda: dt.datetime(2026, 9, 23, 12))  # noqa: DTZ001
    got = src.monthly_sales_history("DCC1800264G1Z2B", "11072", 3)
    assert got == [(dt.date(2026, 6, 1), 561), (dt.date(2026, 7, 1), 254),
                   (dt.date(2026, 8, 1), 318)]


def test_monthly_sales_history_rejects_an_unmapped_sid():
    """★ store_for() 认不出的 sid 必须硬失败 —— 不猜一个店垫上（同 order_store_map
    的判据，UnknownStore 就是给这条用的）。"""
    from dim.order_store_map import UnknownStore
    src = cs.ChSource(_sales_query())
    with pytest.raises(UnknownStore, match="11100"):
        src.monthly_sales_history("ANY-MSKU", "11100", 3)


def test_monthly_sales_history_rows_outside_the_window_hard_fail():
    """★ SQL 自己算错了月份边界（或调用方传错窗口）不许被悄悄吞掉 —— 落在窗口
    外的一行意味着这条 SQL 或 month_window 本身的形态变了。"""
    rows = [(dt.date(2026, 5, 1), 100)]  # ← 窗口只到 2026-06，5 月不该出现
    src = cs.ChSource(_sales_query(rows))
    with pytest.raises(cs.UnknownShape, match="2026-05"):
        src.monthly_sales_history("DCC1800264G1Z2B", "11072", 3)


def test_monthly_sales_history_records_the_lag_marker_in_history_window():
    """★ 控制器裁定：口径标记随数走，且不许是可有可无的装饰字符串 ——
    Task 5 要把 lag_note 原样透传进 claims 响应。这里守它真的被写进
    history_window()，且带上补 0 的月份与最新月龄。"""
    rows = [(dt.date(2026, 6, 1), 561), (dt.date(2026, 8, 1), 318)]  # 7 月缺
    src = cs.ChSource(_sales_query(rows))
    got = src.monthly_sales_history("DCC1800264G1Z2B", "11072", 3)
    assert got == [(dt.date(2026, 6, 1), 561), (dt.date(2026, 7, 1), 0),
                   (dt.date(2026, 8, 1), 318)]
    window = src.history_window("DCC1800264G1Z2B", "11072")
    assert window["zero_filled"] == ["2026-07-01"]
    assert window["newest_month"] == "2026-08-01"
    assert window["newest_month_age_days"] == (dt.date(2026, 9, 23) - dt.date(2026, 8, 1)).days
    assert window["lag_note"], "口径标记不许是空字符串或缺失 —— 没有标记的数字比没有数字更坏"
    assert window["store"] == "PETSFIT_NORTH_AMERICA"
    assert window["sales_channel"] == "Amazon.com"


def test_monthly_sales_history_no_rows_returns_empty_and_does_not_zero_fill():
    """★ 协议原话：没有行就返回空列表，不补 0（dim/source.py:25）。"""
    src = cs.ChSource(_sales_query([]))
    got = src.monthly_sales_history("NEVER-SOLD", "11072", 3)
    assert got == []


def test_monthly_sales_history_query_failure_becomes_ch_unavailable():
    """★ 同 onhand_available/purchase_in_transit 的模式 —— 查询失败必须分类后抛
    ChUnavailable，不许让裸 driver 异常从这里漏出去。"""
    exc = ConnectionRefusedError(111, "Connection refused")
    src = cs.ChSource(_sales_query(fail=exc), classify_failure=describe_failure)
    with pytest.raises(cs.ChUnavailable) as e:
        src.monthly_sales_history("DCC1800264G1Z2B", "11072", 3)
    assert e.value.cause["kind"] == "connect_refused"
