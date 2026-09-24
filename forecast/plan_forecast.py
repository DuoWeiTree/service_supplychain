"""计划整表的库存推演（spec §4）。纯函数：只吃值，不碰 PG / CH / 配置。

每个 msku 从 as_of 收盘起逐日记账（ledger.py），再按月读出期初 / 期末。
★ 月末头寸必为整数（OP-7：每一段都是 ceil 后的整件再摊回逐日），所以恒等式③④逐位成立。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from fractions import Fraction

from forecast.calendar import (
    add_months,
    days_in_month,
    month_end,
    month_start,
    segment_daily,
    segment_units,
    span,
    units_for_month,
)
from forecast.ledger import run_ledger

ONE = dt.timedelta(days=1)


@dataclass(frozen=True)
class MskuInput:
    seller_sku: str
    sid: str
    sku: str
    applicable: bool                        # seller.has_fba（02 §3.1a）
    onhand: int | None                      # as_of 当天 FBA 可售；None + applicable → 未知在仓
    effective: dict[dt.date, int | None]    # 计划月（月初）→ 期望销量生效值
    system: dict[dt.date, int | None]       # 计划月 → 预测销量（只汇总，不进台账）
    base_rate: float | None                 # system_basis.base_rate；W-6 桥接段用

    @property
    def key(self) -> tuple[str, str]:
        return (self.seller_sku, self.sid)


@dataclass(frozen=True)
class Arrival:
    date: dt.date
    units: int | None                       # None = 这批分不出来（OP-12）
    note: dict


@dataclass(frozen=True)
class MskuMonth:
    period: dt.date
    state: str
    reason: str | None
    unknown_since: dt.date | None
    opening: int | None
    demand: int | None
    inbound: int | None
    closing: int | None
    stockout_date: dt.date | None
    bridge: dict | None
    arrivals: tuple[dict, ...]


def _int(x: Fraction) -> int:
    if x.denominator != 1:
        raise AssertionError(f"月末头寸必须是整数（OP-7 构造保证），得到 {x}")
    return int(x)


def _blank(p: dt.date, state: str, reason: str, since: dt.date | None = None) -> MskuMonth:
    return MskuMonth(p, state, reason, since, None, None, None, None, None, None, ())


def project_msku(m: MskuInput, as_of: dt.date, periods: list[dt.date],
                 arrivals: list[Arrival] = ()) -> list[MskuMonth]:
    if not m.applicable:
        return [_blank(p, "not_applicable", "not_applicable") for p in periods]
    if m.onhand is None:
        return [_blank(p, "unknown", "unknown_onhand", periods[0]) for p in periods]
    start, end = as_of + ONE, month_end(periods[-1])
    live = [p for p in periods if month_end(p) >= start]
    if not live:
        return [_blank(p, "elapsed", "elapsed") for p in periods]
    first_plan = periods[0]

    demand: dict[dt.date, Fraction | None] = {}
    bridge = None
    if start < first_plan:                                   # ★ W-6 桥接段：按预测扣
        if m.base_rate is None:
            demand.update({d: None for d in span(start, first_plan - ONE)})
        else:
            totals, units, mm = {}, 0, month_start(start)
            while mm < first_plan:
                a, b = max(start, mm), min(first_plan - ONE, month_end(mm))
                total = units_for_month(m.base_rate, days_in_month(mm))
                totals[mm.strftime("%Y-%m")] = total
                demand.update(segment_daily(total, a, b))
                units += segment_units(total, a, b)
                mm = add_months(mm, 1)
            bridge = {"from": start.isoformat(), "to": (first_plan - ONE).isoformat(),
                      "units": units, "by": "forecast", "rate": m.base_rate,
                      "month_totals": totals, "onhand_as_of": m.onhand}
    for p in live:                                           # ★ 计划月：扣期望销量
        a, eff = max(start, p), m.effective.get(p)
        if eff is None:
            demand.update({d: None for d in span(a, month_end(p))})
        else:
            demand.update(segment_daily(eff, a, month_end(p)))

    arr: dict[dt.date, Fraction | None] = {}
    notes: dict[dt.date, list[dict]] = {}
    for x in arrivals:
        if not start <= x.date <= end:
            raise ValueError(f"到货日 {x.date} 不在推演窗 [{start}, {end}] 内 —— "
                             "调用方应先把它列进 beyond_horizon")
        prev = arr.get(x.date, Fraction(0))
        arr[x.date] = None if (prev is None or x.units is None) else prev + x.units
        notes.setdefault(x.date, []).append({"date": x.date.isoformat(), "units": x.units,
                                             **x.note})

    days = span(start, end)
    L = run_ledger(m.onhand, start, [demand[d] for d in days],
                   [arr.get(d, Fraction(0)) for d in days])
    cause = since = None
    if L.unknown_from is not None:
        u = L.unknown_from
        cause = ("inbound_unknown" if demand[u] is not None
                 else "bridge_rate_unknown" if u < first_plan else "unknown_demand")
        since = max(month_start(u), live[0])

    rows: list[MskuMonth] = []
    for p in periods:
        if month_end(p) < start:
            rows.append(_blank(p, "elapsed", "elapsed"))
            continue
        a, b = max(start, p), month_end(p)
        partial = a != p
        eff = m.effective.get(p)
        dem = None if eff is None else segment_units(eff, a, b)
        opening, closing = L.signed_at(a - ONE), L.signed_at(b)
        inb = [arr.get(d, Fraction(0)) for d in span(a, b)]
        inbound = None if any(v is None for v in inb) else _int(sum(inb, Fraction(0)))
        month_notes = tuple(n for d in sorted(notes) if a <= d <= b for n in notes[d])
        br = bridge if (p == first_plan and not partial) else None
        shown_opening = None if (partial or opening is None) else _int(opening)
        if closing is None:
            rows.append(MskuMonth(p, "unknown", cause, since, shown_opening, dem, inbound, None,
                                  None, br, month_notes))
            continue
        if not partial and closing != opening - dem + inbound:   # ★ 恒等式③
            raise AssertionError(f"{m.key} {p}: {closing} ≠ {opening} − {dem} + {inbound}")
        rows.append(MskuMonth(p, "partial" if partial else "ok",
                              "as_of_inside_month" if partial else None, None, shown_opening,
                              dem, inbound, _int(closing), L.first_unmet(a, b), br, month_notes))
    return rows
