"""ChSource 预测取数的纯变换。★ 假 query 只按 SQL 的关键字回放，不真连 CH。

★ 本文件全用 naive datetime —— 与 `pick_snapshot_date` 收到的 CH `_captured_at`
  是同一种，比较两个 tz-aware/naive 混用的 datetime 会直接抛 TypeError。
"""
from __future__ import annotations

import datetime as dt

import pytest

from dim import ch_source as cs

#: 实测形状（design §1.3 E-1/E-3）：近 20 个采集日 7,934~8,080 行、每日恰好 21 个 sid，
#: 当天 06:34~07:39 起跑、约 60 秒跑完。
FULL = 8000
NOW = dt.datetime(2026, 9, 23, 12, 0, 0)  # noqa: DTZ001


def day(d: int, rows: int = FULL, sids: int = 21, done_at: str = "06:35:00"):
    return (dt.date(2026, 9, d), rows, sids,
            dt.datetime.fromisoformat(f"2026-09-{d:02d}T{done_at}"))


def test_picks_the_newest_settled_day():
    got, rejected = cs.pick_snapshot_date([day(23), day(22), day(21)], NOW, 0.30)
    assert got == dt.date(2026, 9, 23)
    assert rejected == []


def test_rejects_a_half_written_batch_and_names_it():
    """★ 半写的当天批次：行数只有一半 —— 必须退一天，且退这一步要留下证据。"""
    got, rejected = cs.pick_snapshot_date([day(23, rows=4000), day(22), day(21)], NOW, 0.30)
    assert got == dt.date(2026, 9, 22)
    assert rejected == [{"date": "2026-09-23", "rows": 4000, "uniq_sid": 21,
                         "reason": "coverage_drop_rows"}]


def test_rejects_a_batch_still_being_written():
    """★ 行数够了但刚写完 30 分钟不到 —— 同样不能用（design §4.1 判据 1）。"""
    now = dt.datetime(2026, 9, 23, 6, 50, 0)  # noqa: DTZ001
    got, rejected = cs.pick_snapshot_date([day(23), day(22), day(21)], now, 0.30)
    assert got == dt.date(2026, 9, 22)
    assert rejected[0]["reason"] == "not_settled"


def test_rejects_a_day_that_lost_stores_even_when_row_count_holds():
    """★ 整店 0 行是采集缺口的形状，总行数看不出来（jobs/refresh_dims.py:150-153 同一条）。"""
    got, rejected = cs.pick_snapshot_date([day(23, sids=12), day(22), day(21)], NOW, 0.30)
    assert got == dt.date(2026, 9, 22)
    assert rejected[0]["reason"] == "coverage_drop_uniq_sid"


def test_all_candidates_rejected_raises_with_every_number():
    days = [day(23, rows=100), day(22, rows=100), day(21, rows=100)]
    with pytest.raises(cs.ChDataUnusable) as e:
        cs.pick_snapshot_date(days, NOW, 0.30)
    assert len(e.value.rejected) == 3
    assert "2026-09-23" in str(e.value)


def test_empty_candidate_list_is_not_a_silent_empty():
    with pytest.raises(cs.UnknownShape, match="lingxing_inventory_fba_detail"):
        cs.pick_snapshot_date([], NOW, 0.30)


def test_as_of_caches_within_ttl_and_rebuilds_after():
    calls = []

    def q(sql: str):
        calls.append(sql)
        return [day(23), day(22)]

    clock = [dt.datetime(2026, 9, 23, 12, 0, 0)]  # noqa: DTZ001
    src = cs.ChSource(q, now=lambda: clock[0], cache_ttl_s=300)
    assert src.as_of() == dt.date(2026, 9, 23)
    assert src.as_of() == dt.date(2026, 9, 23)
    assert len(calls) == 1, "TTL 内重复解析 = 每次 grid 都多打一次 CH"
    clock[0] += dt.timedelta(seconds=301)
    src.as_of()
    assert len(calls) == 2, "TTL 过了还不重建 = 缓存在保留陈旧"
