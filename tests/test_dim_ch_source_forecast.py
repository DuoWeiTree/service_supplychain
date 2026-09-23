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
"""
from __future__ import annotations

import datetime as dt

import pytest

from dim import ch_source as cs

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
