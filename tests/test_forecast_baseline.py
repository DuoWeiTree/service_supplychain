"""base_rate 四步流水线各自被处理了（兄弟仓 tests/test_baseline.py 逐条搬）。"""
import datetime as dt

import pytest

from forecast.baseline import SHRINK_PRIOR, baseline_rate
from forecast.calendar import units_for_month

A = dt.date(2026, 8, 19)


def back(i):
    return A - dt.timedelta(days=i)


def test_absent_days_count_as_zero_not_missing():
    """densify：没出现的日子 = 卖了 0。"""
    days = [back(i) for i in range(56)]
    a = baseline_rate({days[0]: 10, days[1]: 10}, set(), A, prior=0.0)
    b = baseline_rate({d: (10 if d in days[:2] else 0) for d in days}, set(), A, prior=0.0)
    assert a.rate == pytest.approx(b.rate)


def test_stockout_days_are_censored_not_treated_as_zero_demand():
    """censor：断货日的 0 是供给为 0，不是需求为 0。"""
    sales = {back(i): (0 if i < 14 else 5) for i in range(28)}
    cen = {back(i) for i in range(14)}
    naive = baseline_rate(sales, set(), A, prior=0.0)
    fixed = baseline_rate(sales, cen, A, prior=0.0)
    assert fixed.rate > naive.rate * 2
    assert fixed.rate == pytest.approx(5.0, abs=0.01)
    assert fixed.censored_days == 14


def test_promo_spike_does_not_hijack_the_baseline():
    flat = {back(i): 5 for i in range(28)}
    spiked = {**flat, back(3): 100}
    lift = baseline_rate(spiked, set(), A, prior=0.0).rate / baseline_rate(flat, set(), A, prior=0.0).rate
    assert lift < 1.5
    assert baseline_rate(spiked, set(), A, prior=0.0).winsor_cap is not None


def test_shrinks_toward_the_mskus_own_long_window():
    h = {**{back(i): 10 for i in range(56, 90)}, **{back(i): 1 for i in range(56)}}
    raw = baseline_rate(h, set(), A, prior=0.0).rate
    shrunk = baseline_rate(h, set(), A, prior=SHRINK_PRIOR).rate
    assert raw < shrunk < 10


def test_new_msku_is_not_diluted_by_pre_launch_days():
    new = baseline_rate({back(i): 6 for i in range(20)}, set(), A, prior=0.0)
    old = baseline_rate({back(i): 6 for i in range(56)}, set(), A, prior=0.0)
    assert new.rate == pytest.approx(old.rate, rel=0.01)
    assert new.first_seen == back(19)


def test_monthly_forecast_scales_by_days():
    """spec §3.1：预测销量 = base_rate × 该月天数（季节指数停用）。"""
    r = baseline_rate({back(i): 10 for i in range(28)}, set(), A).rate
    assert units_for_month(r, 31) / units_for_month(r, 30) == pytest.approx(31 / 30, rel=0.02)


def test_no_rows_in_window_is_none_not_zero():
    """spec §3.3：没有历史 ≠ 0。"""
    assert baseline_rate({}, set(), A) is None
    assert baseline_rate({back(80): 3}, set(), A) is None


def test_known_sibling_defect_is_ported_verbatim():
    """★ OP-3：兄弟仓 first_seen 只在 56 天窗里找 → 90 天目标被除以 56。
    恒卖 4 件/天 120 天 → 4.325（高估 8.1%）。先搬不修：修的那一次这条**应当**变红。"""
    r = baseline_rate({back(i): 4 for i in range(120)}, set(), A)
    assert r.rate == pytest.approx(4.325139122267326, rel=1e-12)
    # 目标窗日均 = 近 90 天销量 ÷ 天数（从 56 天窗里找到的首销日起算，最多 90）—— 依据回显这两个数
    assert (r.target_units, r.target_days) == (360, 56) and r.target_rate == 360 / 56


def test_estimation_anchor_moves_back_embargo_days():
    """spec §3.1 ②：估计锚点从 as_of 前挪 7 天（设计取值 · 未实测，W-5）。"""
    from forecast.baseline import EMBARGO_DAYS, data_windows, estimation_anchor
    assert EMBARGO_DAYS == 7
    assert estimation_anchor(dt.date(2026, 9, 21)) == dt.date(2026, 9, 14)
    w = data_windows(dt.date(2026, 9, 21))
    assert (w.estimation_anchor, w.censor_from, w.sales_from, w.until) == (
        dt.date(2026, 9, 14), dt.date(2026, 7, 21), dt.date(2026, 6, 17), dt.date(2026, 9, 14))


@pytest.mark.parametrize("bad", [-1, 1.5, True, None])
def test_embargo_must_be_a_non_negative_int(bad):
    from forecast.baseline import estimation_anchor
    with pytest.raises(ValueError):
        estimation_anchor(dt.date(2026, 9, 21), bad)


def test_the_incomplete_recent_days_no_longer_drag_the_rate_down():
    """06 §4：近几天订单没采全 → v1 系统性低估。挪开锚点后，最后 7 天的缺口不再进估计。"""
    from forecast.baseline import estimate_rate
    as_of = dt.date(2026, 9, 21)
    full = {as_of - dt.timedelta(days=i): 10 for i in range(120)}
    lagged = {**full, **{as_of - dt.timedelta(days=i): 3 for i in range(7)}}   # 近 7 天只采到 3 成
    e = estimate_rate(lagged, set(), as_of)
    assert (e.embargo_days, e.estimation_anchor) == (7, dt.date(2026, 9, 14))
    assert e.result.rate == pytest.approx(estimate_rate(full, set(), as_of).result.rate)
