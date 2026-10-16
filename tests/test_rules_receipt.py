"""领星备货单进度（spec 2026-10-13 §2 · `04` §4.4）。纯函数。"""
import datetime as dt

import pytest

from rules import receipt


@pytest.mark.parametrize("status,stage,rank", [
    ("10", "待审核", 1), ("30", "待配货", 2), ("40", "待发货", 3), ("50", "待收货", 4), ("60", "已完成", 5),
    ("20", "已驳回", -1), ("51", "已撤销", -1), (40, "待发货", 3),
])
def test_status_maps_to_lingxing_words(status, stage, rank):
    assert receipt.stage_of(status) == (stage, rank)


def test_unknown_status_is_not_guessed():
    assert receipt.stage_of("99") is None and receipt.stage_of(None) is None


def test_gone_from_lingxing_is_a_bypass_stage():
    assert receipt.GONE == ("领星无此单", -1)


T = [dt.datetime(2026, 10, 13, h) for h in range(6)]


def test_current_takes_highest_rank_not_latest():
    obs = [(T[0], "待发货", 3), (T[1], "已完成", 5), (T[2], "待收货", 4)]      # 读回倒退
    assert receipt.current(obs) == ("已完成", 5, T[2])


def test_current_prefers_latest_bypass():
    obs = [(T[0], "待发货", 3), (T[1], "已撤销", -1), (T[2], "待发货", 3)]
    assert receipt.current(obs) == ("已撤销", -1, T[2])


def test_current_of_nothing_is_none():
    assert receipt.current([]) is None


def test_archive_only_when_every_order_is_complete():
    assert receipt.all_complete(["已完成", "已完成"]) is True
    assert receipt.all_complete(["已完成", "待收货"]) is False
    assert receipt.all_complete([]) is False


@pytest.mark.parametrize("stage,drift", [
    ("已驳回", "领星已驳回"), ("已撤销", "领星已撤销"), ("领星无此单", "领星里没有这张单"), ("待发货", None), (None, None),
])
def test_drift_against_our_dispatched(stage, drift):
    assert receipt.drift("已投送", stage) == drift
