import datetime as dt
from fractions import Fraction

import pytest

from forecast.calendar import (
    ANCHOR_DAY, add_months, days_in_month, halfmonth_daily, halfmonth_weight, month_end,
    order_arrival, segment_daily, segment_units, units_for_month,
)

D = dt.date


@pytest.mark.parametrize("month", [D(2026, 10, 1), D(2026, 11, 1), D(2027, 2, 1), D(2028, 2, 1)])
def test_halfmonth_split_keeps_the_month_total_exactly(month):
    """spec §4.2：摊分不改月总数 —— Σ当月每日 ≡ 期望销量[月]，且 15 号累计正好一半。"""
    daily = halfmonth_daily(100, month)
    assert len(daily) == days_in_month(month)
    assert sum(daily.values()) == 100
    assert sum(v for d, v in daily.items() if d.day <= 15) == 50


def test_halfmonth_weight_values():
    assert halfmonth_weight(D(2026, 10, 15)) == Fraction(1, 30)
    assert halfmonth_weight(D(2026, 10, 16)) == Fraction(1, 32)   # 31 天月：后半 16 天
    assert halfmonth_weight(D(2027, 2, 16)) == Fraction(1, 26)    # 28 天月：后半 13 天


def test_segment_units_rounds_up_and_full_month_is_exact():
    """OP-7：段 = ceil(月总量 × 段权重)。"""
    # 9/22~9/30：9 天 × 1/30 = 0.3
    assert segment_units(120, D(2026, 9, 22), D(2026, 9, 30)) == 36
    assert segment_units(65, D(2026, 9, 22), D(2026, 9, 30)) == 20        # 19.5 → 20
    # 10/11~10/31：5×1/30 + 16×1/32 = 2/3
    assert segment_units(30, D(2026, 10, 11), D(2026, 10, 31)) == 20
    assert segment_units(137, D(2026, 10, 1), D(2026, 10, 31)) == 137


def test_segment_daily_sums_to_segment_units():
    daily = segment_daily(65, D(2026, 9, 22), D(2026, 9, 30))
    assert sorted(daily) == [D(2026, 9, d) for d in range(22, 31)]
    assert sum(daily.values()) == 20


def test_segment_must_stay_inside_one_month():
    with pytest.raises(ValueError):
        segment_units(10, D(2026, 9, 30), D(2026, 10, 1))


def test_order_in_october_with_30_plus_60_arrives_in_january():
    """spec §7 · 14 §2.3：10 月下单、30 + 60 天 → 2027-01（15 号锚）。"""
    placed, made, arrive = order_arrival(D(2026, 10, 1), 30, 60)
    assert ANCHOR_DAY == 15
    assert placed == D(2026, 10, 15)
    assert made == D(2026, 11, 14)          # 14 §2.3「10 月下单 + 生产 30 天 → 2026-11 完工」
    assert arrive == D(2027, 1, 13)


def test_domestic_33_days_lands_in_november():
    """14 §2.3：10 月下单 + 国内 33 天 → 2026-11 可售。"""
    assert order_arrival(D(2026, 10, 1), 30, 3)[2] == D(2026, 11, 17)


def test_units_for_month_rounds_up():
    """spec §3.3：向上取整。"""
    assert units_for_month(4.13, 30) == 124        # spec §5 的算例
    assert units_for_month(4.325139122267326, 31) == 135


def test_units_for_month_ignores_float_noise():
    """Review Focus 4（OP-10）：整数结果不许被浮点噪声多算一件。

    ★ 偏离 brief 原样例：brief/计划文档举的 `1.1 × 30` 实测在 IEEE754 double 下
    是 `33.0`（bit-exact，Python/Node 一致，`round(1.1*30,9)==33.0` 前就已经如此，
    与「33.000000000000004」的说法不符——这条断言在任何合规实现上都过不了，
    已用真实存在噪声的输入替换，取值未回改上游文档，仅在本文件内订正
    （见 task-2-report.md 的 self-review）。
    """
    assert 64.4 * 30 > 1932                    # 真实噪声：1932.0000000000002
    assert units_for_month(64.4, 30) == 1932


def test_month_helpers():
    assert month_end(D(2027, 2, 3)) == D(2027, 2, 28)
    assert add_months(D(2026, 11, 1), 3) == D(2027, 2, 1)
