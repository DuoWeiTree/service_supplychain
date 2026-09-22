import datetime as dt
from pathlib import Path

import pytest

from dim.ch_source import ChSource
from dim.fixture_source import FixtureSource

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def s():
    return FixtureSource(FIX)


def test_history_is_month_first_dates_in_order(s):
    assert s.monthly_sales_history("MSKU-A", "11072", 3) == [
        (dt.date(2026, 7, 1), 100), (dt.date(2026, 8, 1), 120), (dt.date(2026, 9, 1), 90)]


def test_history_of_an_unknown_msku_is_empty_not_zero(s):
    """★ 没有行 ≠ 卖了 0 件。调用方要能区分，所以这里返回空列表而不是 [0,0,0]。"""
    assert s.monthly_sales_history("MSKU-NEW", "11072", 3) == []


def test_zero_onhand_and_not_applicable_are_different(s):
    """★ MSKU-B 真的是 0；MSKU-W 所在的店没有 FBA —— 空串必须读成 None。"""
    assert s.onhand_available("MSKU-B", "11072") == 0
    assert s.onhand_available("MSKU-W", "90001") is None


def test_missing_row_is_none_not_just_empty_cell(s):
    """★ 与上面的空串（MSKU-W）不是同一件事：这里连行都不存在，同样必须是 None。"""
    assert s.onhand_available("MSKU-NOPE", "11072") is None


def test_in_transit_keeps_每笔的依据(s):
    rows = s.purchase_in_transit("DCC1800264G1")
    assert [(r.period, r.units, r.ref) for r in rows] == [
        ("2026-10", 50, "PO-2026-0912"),
        ("2026-10", 30, "PO-2026-0915"),
        ("2026-11", 120, "PO-2026-0920")]


def test_unknown_sku_has_no_in_transit_rows(s):
    assert s.purchase_in_transit("NO-SUCH") == []


def test_as_of_travels_with_the_facts(s):
    """★ CH 是采集副本不是实时领星 —— 不带 as_of 的数回答不了「它是关于什么的」。"""
    assert s.as_of() == dt.date(2026, 9, 21)


def test_as_of_missing_file_fails_loudly(tmp_path):
    """★ 缺 as_of.txt 不许悄悄退回今天 —— 那会让「过期的 fixture」看起来和「新鲜」一样。"""
    empty = FixtureSource(tmp_path)
    with pytest.raises(FileNotFoundError) as ei:
        empty.as_of()
    assert str(tmp_path / "as_of.txt") in str(ei.value)


def test_ch_source_is_explicitly_not_implemented():
    """★ 空实现会让「该做没做」和「本来就不用做」长得一模一样（规则五）。"""
    ch = ChSource()
    for call in (lambda: ch.as_of(),
                 lambda: ch.monthly_sales_history("MSKU-A", "11072", 3),
                 lambda: ch.onhand_available("MSKU-A", "11072"),
                 lambda: ch.purchase_in_transit("DCC1800264G1")):
        with pytest.raises(NotImplementedError) as ei:
            call()
        assert "阶段" in str(ei.value)
