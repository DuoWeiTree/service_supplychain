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


def test_ch_source_has_no_remaining_stub_methods():
    """★ 空实现会让「该做没做」和「本来就不用做」长得一模一样（规则五）。

    ★ 这条测试原先守的是「`monthly_sales_history` 仍显式抛 `NotImplementedError`」——
    Task 4（2026-09-23）把它也接上真取数之后，`as_of()`/`onhand_available()`/
    `purchase_in_transit()`/`monthly_sales_history()` 四个协议方法（`dim/source.py`）
    没有一个还留着占位实现，原断言（「调用它、断言抛 NotImplementedError」）已经
    无的放矢——继续留着只会在下一次有人真的加占位方法时保持沉默。

    诚实的替代：不再挑某一个方法「还没做」，改为扫全部四个协议方法的源码，断言
    没有一处 `raise NotImplementedError`——这样无论将来占位的是哪一个新方法，
    这条测试都会红，而不是像原来那样只盯着一个已经写完的名字。四个方法各自的
    真实行为（含丢弃计数、口径标记）都在 `tests/test_dim_ch_source_forecast.py`
    与 `tests/test_dim_ch_source_live.py` 覆盖，这里只守「别再悄悄留占位」这一条。
    """
    import inspect

    for name in ("as_of", "monthly_sales_history", "onhand_available", "purchase_in_transit"):
        src = inspect.getsource(getattr(ChSource, name))
        assert "NotImplementedError" not in src, (
            f"ChSource.{name} 仍是占位实现——空实现会让「该做没做」和"
            "「本来就不用做」长得一模一样")
