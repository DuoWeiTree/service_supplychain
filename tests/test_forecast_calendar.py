import datetime as dt
from fractions import Fraction

import pytest

from forecast.calendar import (
    ANCHOR_DAY,
    add_months,
    days_in_month,
    halfmonth_daily,
    halfmonth_weight,
    month_end,
    order_arrival,
    segment_daily,
    segment_detail,
    segment_units,
    units_for_month,
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


def test_segment_detail_lets_the_panel_reproduce_the_units():
    """★ 终审 I-4：依据面板要能从屏上的数复算出扣减量 —— 月总量、前后半月各几天、各自除数、
    乘积（不取整）与向上取整后的件数，全由算的那一处给出，前端不再算一遍。"""
    # 10/11~10/31：130 × (5 ÷ 30 + 16 ÷ 32) = 86.666… → 87
    assert segment_detail(130, D(2026, 10, 11), D(2026, 10, 31)) == {
        "from": "2026-10-11", "to": "2026-10-31", "month_total": 130, "month_days": 31,
        "first_half_days": 5, "first_half_divisor": 30,
        "second_half_days": 16, "second_half_divisor": 32,
        "raw": pytest.approx(86.66666666666667, rel=1e-15), "units": 87}
    # 9/22~9/30：130 × 9 ÷ 30 = 39 整 —— raw 与 units 相等，面板据此不说「向上取整」
    d = segment_detail(130, D(2026, 9, 22), D(2026, 9, 30))
    assert (d["first_half_days"], d["second_half_days"], d["second_half_divisor"],
            d["raw"], d["units"]) == (0, 9, 30, 39, 39)
    assert d["units"] == segment_units(130, D(2026, 9, 22), D(2026, 9, 30))


def test_segment_daily_sums_to_segment_units():
    daily = segment_daily(65, D(2026, 9, 22), D(2026, 9, 30))
    assert sorted(daily) == [D(2026, 9, d) for d in range(22, 31)]
    assert sum(daily.values()) == 20


def test_segment_must_stay_inside_one_month():
    with pytest.raises(ValueError):
        segment_units(10, D(2026, 9, 30), D(2026, 10, 1))


def test_segment_rejects_reversed_range_within_the_same_month():
    """`_check_segment` 的第二个分支：同月但 first > last —— 跨月分支不会碰到它。"""
    with pytest.raises(ValueError):
        segment_units(10, D(2026, 9, 30), D(2026, 9, 22))


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
