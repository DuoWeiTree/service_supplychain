"""当月在途（spec 2026-10-15-forecast-three-metrics §2）。纯函数：只吃值，不碰 PG / CH / 领星。

★ 归月（FM-D2）：当月有任一天在路上就计入 —— `[as_of, eta] ∩ 当月 ≠ ∅`，到达那天所在月也算。
★ eta 未知 / 已过（逾期未收）：此刻确实在路上，只计 as_of 所在月并标 `current_only`（FM-Q3，暂行）。
★ 某来源整体未知（None）：该来源与合计都是 None —— 不当 0。
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field

from forecast.calendar import month_end, month_start

SOURCES = ("fba", "oversea")


@dataclass(frozen=True)
class TransitItem:
    source: str                          # "fba" | "oversea"
    ref: str                             # 货件号 / 备货单号 —— 运营拿它去领星核实
    units: int
    eta: dt.date | None
    extra: dict = field(default_factory=dict)


def _months_of(x: TransitItem, as_of: dt.date) -> tuple[set[dt.date], bool]:
    if x.eta is None or x.eta < as_of:
        return {month_start(as_of)}, True
    out, m = set(), month_start(as_of)
    while m <= x.eta:
        out.add(m)
        m = month_start(month_end(m) + dt.timedelta(days=1))
    return out, False


def month_in_transit(fba: Sequence[TransitItem] | None, oversea: Sequence[TransitItem] | None,
                     as_of: dt.date, periods: Sequence[dt.date]) -> list[dict]:
    """→ 每个计划月 `{period, total, fba, oversea, items[]}`（items 按来源、eta、单号排序）。"""
    by_source = {"fba": fba, "oversea": oversea}
    placed: list[tuple[TransitItem, set[dt.date], bool]] = [
        (x, *_months_of(x, as_of)) for src in SOURCES for x in (by_source[src] or ())]
    rows = []
    for p in periods:
        if p < month_start(as_of):
            # ★ 那时在不在路上我们没有记录 —— 未知，不是 0（终审 I-4）
            rows.append({"period": p.strftime("%Y-%m"), "total": None, "fba": None, "oversea": None, "items": [],
                         "before_as_of": True})
            continue
        here = [(x, cur) for x, ms, cur in placed if p in ms]
        sums = {src: (None if by_source[src] is None else sum(x.units for x, _ in here if x.source == src))
                for src in SOURCES}
        total = None if any(v is None for v in sums.values()) else sum(sums.values())
        items = [{"source": x.source, "ref": x.ref, "units": x.units,
                  "eta": x.eta.isoformat() if x.eta else None, "current_only": cur, **x.extra}
                 for x, cur in sorted(here, key=lambda t: (SOURCES.index(t[0].source), t[0].eta or dt.date.max,
                                                           t[0].ref))]
        rows.append({"period": p.strftime("%Y-%m"), "total": total, **sums, "items": items, "before_as_of": False})
    return rows
