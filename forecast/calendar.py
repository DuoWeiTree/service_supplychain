"""天算、月显示（14 §2.3）与半月均分（spec §4.2）。纯函数。"""
from __future__ import annotations

import calendar as _cal
import datetime as dt
import math
from fractions import Fraction

#: 14 §2.3 · M-12：下单月 15 号。★ 不开放（spec §4.4）。
ANCHOR_DAY = 15


def month_start(d: dt.date) -> dt.date:
    return d.replace(day=1)


def days_in_month(d: dt.date) -> int:
    return _cal.monthrange(d.year, d.month)[1]


def month_end(d: dt.date) -> dt.date:
    return d.replace(day=days_in_month(d))


def add_months(m: dt.date, n: int) -> dt.date:
    k = m.year * 12 + m.month - 1 + n
    return dt.date(k // 12, k % 12 + 1, 1)


def span(first: dt.date, last: dt.date) -> list[dt.date]:
    return [first + dt.timedelta(days=i) for i in range((last - first).days + 1)]


def halfmonth_weight(d: dt.date) -> Fraction:
    """前半月（1..15）每天 1/30，后半月每天 1/(2·(月长−15))：两个半月各拿一半。
    ★ 与兄弟仓 calendar.halfmonth_weights 同一约定（formulas.md §3.3 第一种）。"""
    n = days_in_month(d)
    return Fraction(1, 30) if d.day <= 15 else Fraction(1, 2 * (n - 15))


def halfmonth_daily(total: int | Fraction, month: dt.date) -> dict[dt.date, Fraction]:
    m = month_start(month)
    out = {d: Fraction(total) * halfmonth_weight(d) for d in span(m, month_end(m))}
    assert sum(out.values()) == Fraction(total), "半月摊分改了月总数"
    return out


def _check_segment(first: dt.date, last: dt.date) -> None:
    if month_start(first) != month_start(last) or first > last:
        raise ValueError(f"段必须在同一个月内且 first ≤ last：{first} ~ {last}")


def segment_units(total: int, first: dt.date, last: dt.date) -> int:
    """同一个月里 [first, last] 分到的**整数**件数（OP-7：向上取整，与预测销量同向）。"""
    _check_segment(first, last)
    return math.ceil(Fraction(total) * sum(halfmonth_weight(d) for d in span(first, last)))


def segment_detail(total: int, first: dt.date, last: dt.date) -> dict:
    """segment_units 的每一步，给依据面板复算用（终审 I-4）：
    total × (前半月天数 ÷ 30 + 后半月天数 ÷ 2·(月长−15)) = raw → 向上取整 units。"""
    _check_segment(first, last)
    n = days_in_month(first)
    days = span(first, last)
    raw = Fraction(total) * sum(halfmonth_weight(d) for d in days)
    return {"from": first.isoformat(), "to": last.isoformat(), "month_total": total,
            "month_days": n,
            "first_half_days": sum(1 for d in days if d.day <= 15), "first_half_divisor": 30,
            "second_half_days": sum(1 for d in days if d.day > 15),
            "second_half_divisor": 2 * (n - 15),
            "raw": float(raw), "units": segment_units(total, first, last)}


def segment_daily(total: int, first: dt.date, last: dt.date) -> dict[dt.date, Fraction]:
    """把 segment_units 件按半月权重摊回每天。★ Σ ≡ segment_units（构造保证）——
    于是所有月末头寸都是整数，恒等式③④精确成立。"""
    units = segment_units(total, first, last)
    days = span(first, last)
    ws = [halfmonth_weight(d) for d in days]
    w = sum(ws)
    out = {d: Fraction(units) * x / w for d, x in zip(days, ws)}
    assert sum(out.values()) == units
    return out


def order_arrival(
    order_period: dt.date, make_days: int, ship_days: int, anchor_day: int = ANCHOR_DAY
) -> tuple[dt.date, dt.date, dt.date]:
    """(下单日, 完工日, 到货日)。下单日 = 下单月的 anchor_day 号（14 §2.3 期望值模型）。"""
    placed = month_start(order_period).replace(day=anchor_day)
    made = placed + dt.timedelta(days=make_days)
    return placed, made, made + dt.timedelta(days=ship_days)


def units_for_month(rate: float, days: int) -> int:
    """spec §3.3：预测销量向上取整（17 表 system_value）。
    ★ 先 round 到 9 位（设计取值 · 未实测，OP-10）：1.1×30 在 float 里是 33.000000000000004，
      直接 ceil 会凭空多出一件。"""
    return math.ceil(round(rate * days, 9))
