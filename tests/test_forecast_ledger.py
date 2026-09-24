import datetime as dt
from fractions import Fraction as F

import pytest

from forecast.ledger import run_ledger

D = dt.date


def test_signed_and_available_follow_the_three_formulas():
    """spec §4.1：可售 = max(0, 昨天 + 到货 − 卖)；卖不出去 = max(0, 卖 − 昨天 − 到货)；带符号不截断。"""
    L = run_ledger(5, D(2026, 10, 1), [F(3), F(4), F(2)], [F(0), F(0), F(10)])
    assert L.signed == (F(2), F(-2), F(6))
    assert L.available == (F(2), F(0), F(8))
    assert L.daily_unmet == (F(0), F(2), F(0))
    assert L.unknown_from is None


def test_identity_signed_equals_available_minus_cumulative_unmet():
    """spec §4.1 恒等式：带符号头寸 ≡ 可售 − 累计卖不出去（每一天）。"""
    L = run_ledger(1, D(2026, 10, 1), [F(2)] * 5, [F(0), F(0), F(3), F(0), F(0)])
    cum = F(0)
    for s, a, u in zip(L.signed, L.available, L.daily_unmet):
        cum += u
        assert s == a - cum


def test_today_arrival_serves_today_demand():
    """FBA 先入库后出单（兄弟仓 ledger.project 的约定）。"""
    L = run_ledger(0, D(2026, 10, 1), [F(5)], [F(5)])
    assert L.daily_unmet == (F(0),)


def test_unknown_demand_propagates_forward_not_as_zero():
    """spec §4.2 · 14 B1/B2：空 ≠ 0，而且从那天起向后全部未知。"""
    L = run_ledger(10, D(2026, 10, 1), [F(1), None, F(1)], [F(0), F(0), F(0)])
    assert L.signed == (F(9), None, None)
    assert L.unknown_from == D(2026, 10, 2)


def test_unknown_arrival_also_propagates():
    L = run_ledger(10, D(2026, 10, 1), [F(1), F(1)], [None, F(0)])
    assert L.signed == (None, None)
    assert L.unknown_from == D(2026, 10, 1)


def test_signed_at_the_day_before_start_is_the_opening():
    L = run_ledger(200, D(2026, 10, 1), [F(1)], [F(0)])
    assert L.signed_at(D(2026, 9, 30)) == 200
    assert L.signed_at(D(2026, 10, 1)) == 199


def test_first_unmet_is_the_first_day_that_could_not_be_served():
    """spec §4.5：预计断货日 = 截断台账里第一个 daily_unmet > 0 的日子。"""
    L = run_ledger(7, D(2026, 10, 1), [F(4, 3)] * 10, [F(0)] * 10)
    assert L.first_unmet(D(2026, 10, 1), D(2026, 10, 10)) == D(2026, 10, 6)
    assert L.first_unmet(D(2026, 10, 1), D(2026, 10, 5)) is None


def test_negative_inputs_are_rejected():
    with pytest.raises(ValueError):
        run_ledger(-1, D(2026, 10, 1), [F(0)], [F(0)])
    with pytest.raises(ValueError):
        run_ledger(0, D(2026, 10, 1), [F(-1)], [F(0)])
    with pytest.raises(ValueError):
        run_ledger(0, D(2026, 10, 1), [F(0), F(0)], [F(0)])


def test_negative_value_on_the_unknown_flip_day_is_still_rejected():
    """负值校验不能被「这天恰好把 unknown_from 定下来」的 continue 绕过去。"""
    with pytest.raises(ValueError):
        run_ledger(10, D(2026, 10, 1), [None], [F(-1)])
    with pytest.raises(ValueError):
        run_ledger(10, D(2026, 10, 1), [F(-1)], [None])


def test_first_unmet_rejects_dates_outside_the_ledger_span():
    L = run_ledger(7, D(2026, 10, 1), [F(4, 3)] * 10, [F(0)] * 10)
    with pytest.raises(ValueError):
        L.first_unmet(D(2026, 9, 30), D(2026, 10, 10))
    with pytest.raises(ValueError):
        L.first_unmet(D(2026, 10, 1), D(2026, 10, 11))
