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
