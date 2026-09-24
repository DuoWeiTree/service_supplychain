"""计划整表的库存推演（spec §4）。纯函数：只吃值，不碰 PG / CH / 配置。

每个 msku 从 as_of 收盘起逐日记账（ledger.py），再按月读出期初 / 期末。
★ 月末头寸必为整数（OP-7：每一段都是 ceil 后的整件再摊回逐日），所以恒等式③④逐位成立。
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction

from forecast.allocate import Batch, allocate
from forecast.calendar import (
    add_months,
    days_in_month,
    month_end,
    month_start,
    order_arrival,
    segment_daily,
    segment_detail,
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
    base_rate: float | None                 # system_basis.base_rate；W-6 桥接段用（缺则按首月期望，I-5）
    # ★ False = 这个 msku 没有销量取数源（认领时记进 system_basis），预测「不适用」——
    #   与 FBA 不适用同一种排除：不进上一层的预测 Σ，不当 0，也不把上一层拖成未知
    system_applicable: bool = True

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
    #: as_of 落在本月里（partial）：台账从 as_of 次日起算，起点是 as_of 当天的可售 ——
    #: 本月已过去的那几天不在账上。★ 不叫「期初」：它不是月初的数
    as_of_start: dict | None = None


class MskuProjection(list):
    """逐月行（list[MskuMonth]）+ msku 级依据。继承 list：调用方按月下标取行不必改。

    first_stockout_date —— 整本台账（含 W-6 桥接段）第一个 daily_unmet > 0 的日子（spec §4.5）；
      逐月的 stockout_date 只管「该月内」（OP-18），持续断货时每月都报 1 号。
    unplaced_arrivals —— 这个 msku 推演不了（不适用 / 在仓未知）时传进来的到货，带 reason 点名。
    """

    def __init__(self, rows: Sequence[MskuMonth], first_stockout_date: dt.date | None = None,
                 unplaced_arrivals: Sequence[dict] = ()):
        super().__init__(rows)
        self.first_stockout_date = first_stockout_date
        self.unplaced_arrivals = tuple(unplaced_arrivals)


def _note(x: Arrival) -> dict:
    return {"date": x.date.isoformat(), "units": x.units, **x.note}


def _int(x: Fraction) -> int:
    if x.denominator != 1:
        raise AssertionError(f"月末头寸必须是整数（OP-7 构造保证），得到 {x}")
    return int(x)


def _blank(p: dt.date, state: str, reason: str, since: dt.date | None = None) -> MskuMonth:
    return MskuMonth(p, state, reason, since, None, None, None, None, None, None, ())


def _demand(m: MskuInput, p: dt.date, start: dt.date) -> int | None:
    eff = m.effective.get(p)
    return None if eff is None else segment_units(eff, max(start, p), month_end(p))


def _check_periods(periods: Sequence[dt.date]) -> None:
    if not periods:
        raise ValueError("计划月为空")
    for i, p in enumerate(periods):
        if p.day != 1:
            raise ValueError(f"计划月 {p} 不是月初")
        if i and p != add_months(periods[i - 1], 1):
            raise ValueError(f"计划月 {p} 与上一个 {periods[i - 1]} 不相接"
                             f"（应为 {add_months(periods[i - 1], 1)}）")


def project_msku(m: MskuInput, as_of: dt.date, periods: Sequence[dt.date],
                 arrivals: Sequence[Arrival] = ()) -> MskuProjection:
    _check_periods(periods)
    start, end = as_of + ONE, month_end(periods[-1])
    for x in arrivals:                   # ★ 每条路径都查：提前走掉的分支不许吞掉越界到货
        if not start <= x.date <= end:
            raise ValueError(f"到货日 {x.date} 不在推演窗 [{start}, {end}] 内 —— "
                             "调用方应先把它列进 beyond_horizon")
    live = [p for p in periods if month_end(p) >= start]
    if not m.applicable or m.onhand is None:
        reason = "not_applicable" if not m.applicable else "unknown_onhand"
        unplaced = [{**_note(x), "reason": reason} for x in sorted(arrivals, key=lambda x: x.date)]
        if not m.applicable:
            rows = [_blank(p, "not_applicable", "not_applicable") for p in periods]
        else:                            # 同 no-rate：未知的是头寸，期望销量照常回显
            rows = [_blank(p, "elapsed", "elapsed") if p not in live else
                    MskuMonth(p, "unknown", "unknown_onhand", live[0], None, _demand(m, p, start),
                              None, None, None, None, ()) for p in periods]
        return MskuProjection(rows, None, unplaced)
    if not live:
        return MskuProjection([_blank(p, "elapsed", "elapsed") for p in periods])
    first_plan = periods[0]

    demand: dict[dt.date, Fraction | None] = {}
    bridge = None
    if start < first_plan:                                   # ★ W-6 桥接段：按预测扣
        totals: dict[str, int] = {}
        segs: list[dict] = []
        units: int | None = None
        rate, by, extra = m.base_rate, "forecast", {}
        first_eff = m.effective.get(first_plan)
        if rate is None and first_eff is not None:
            # ★ 终审 I-5（我的建议，未裁定 · 00 W 组）：没有预测速率（新品 / 无销量源）时按
            #   首月期望折成日速率 —— 否则 OP-8 是死胡同：人填了期望，库存照样永远未知。
            days = days_in_month(first_plan)
            rate, by = first_eff / days, "first_month_expected"
            extra = {"first_month_expected": {"period": first_plan.strftime("%Y-%m"),
                                              "units": first_eff, "days": days}}
        if rate is None:
            by = "unknown"
            demand.update({d: None for d in span(start, first_plan - ONE)})
        else:
            units, mm = 0, month_start(start)
            while mm < first_plan:
                a, b = max(start, mm), min(first_plan - ONE, month_end(mm))
                total = units_for_month(rate, days_in_month(mm))
                totals[mm.strftime("%Y-%m")] = total
                # ★ 终审 I-4：面板要能复算 —— 月总量的未取整乘积（同 units_for_month 的 round 9 位）
                #   与段的半月天数 / 除数 / 乘积 / 向上取整，都由这里给
                segs.append({**segment_detail(total, a, b), "month": mm.strftime("%Y-%m"),
                             "month_total_raw": round(rate * days_in_month(mm), 9)})
                demand.update(segment_daily(total, a, b))
                units += segment_units(total, a, b)
                mm = add_months(mm, 1)
        bridge = {"from": start.isoformat(), "to": (first_plan - ONE).isoformat(),
                  "units": units, "by": by, "rate": rate, **extra,
                  "month_totals": totals, "segments": segs, "onhand_as_of": m.onhand}
    for p in live:                                           # ★ 计划月：扣期望销量
        a, eff = max(start, p), m.effective.get(p)
        if eff is None:
            demand.update({d: None for d in span(a, month_end(p))})
        else:
            demand.update(segment_daily(eff, a, month_end(p)))

    arr: dict[dt.date, Fraction | None] = {}
    notes: dict[dt.date, list[dict]] = {}
    for x in arrivals:
        prev = arr.get(x.date, Fraction(0))
        arr[x.date] = None if (prev is None or x.units is None) else prev + x.units
        notes.setdefault(x.date, []).append(_note(x))

    if bridge is not None:
        # ★ 14 B3：桥接段到的货不在任何计划月的 arrivals 里 —— 必须在期初依据里点名，
        #   否则期初变了却说不出为什么。
        ins = [n for d in sorted(notes) if d < first_plan for n in notes[d]]
        vals = [arr[d] for d in notes if d < first_plan]
        bridge["arrivals"] = ins
        bridge["arrival_units"] = (None if any(v is None for v in vals)
                                   else _int(sum(vals, Fraction(0))))

    days = span(start, end)
    L = run_ledger(m.onhand, start, [demand[d] for d in days],
                   [arr.get(d, Fraction(0)) for d in days])
    if bridge is not None:               # ★ spec §4.5：桥接段也是台账，断货可能就在这里
        so = L.first_unmet(start, first_plan - ONE)
        bridge["stockout_date"] = None if so is None else so.isoformat()
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
        dem = _demand(m, p, start)
        opening, closing = L.signed_at(a - ONE), L.signed_at(b)
        inb = [arr.get(d, Fraction(0)) for d in span(a, b)]
        inbound = None if any(v is None for v in inb) else _int(sum(inb, Fraction(0)))
        month_notes = tuple(n for d in sorted(notes) if a <= d <= b for n in notes[d])
        br = bridge if (p == first_plan and not partial) else None
        shown_opening = None if (partial or opening is None) else _int(opening)
        eff = m.effective.get(p)
        start_note = ({"as_of": as_of.isoformat(), "onhand_as_of": m.onhand, "from": a.isoformat(),
                       "segment": None if eff is None else segment_detail(eff, a, b)}
                      if partial else None)
        if br is not None and shown_opening is not None and shown_opening != (
                m.onhand - br["units"] + br["arrival_units"]):
            raise AssertionError(f"{m.key} {p}: 期初 {shown_opening} ≠ {m.onhand} − "
                                 f"{br['units']} + {br['arrival_units']}")
        if closing is None:
            # first_unmet 跳过未知日：月中转未知之前已发生的断货照报
            rows.append(MskuMonth(p, "unknown", cause, since, shown_opening, dem, inbound, None,
                                  L.first_unmet(a, b), br, month_notes, start_note))
            continue
        # ★ 恒等式③；partial 月的起点是 as_of 可售，dem 是 as_of 次日到月末那一段
        if closing != opening - dem + inbound:
            raise AssertionError(f"{m.key} {p}: {closing} ≠ {opening} − {dem} + {inbound}")
        rows.append(MskuMonth(p, "partial" if partial else "ok",
                              "as_of_inside_month" if partial else None, None, shown_opening,
                              dem, inbound, _int(closing), L.first_unmet(a, b), br, month_notes,
                              start_note))
    return MskuProjection(rows, L.first_unmet(start, end))


@dataclass(frozen=True)
class PlanForecastInput:
    as_of: dt.date
    periods: list[dt.date]
    make_days: int
    ship_days: int
    mskus: list[MskuInput]
    purchase: dict[tuple[str, dt.date], int | None]
    #: sku → [(预计到货月 "YYYY-MM", 件数, 单号, 已逾期)]。★ 月份是 ETA 所在月，不是下单月；
    #: 已逾期由装配层按采购快照判（OQ-6），这里只搬运与合计 —— 不改日期
    ordered_open: dict[str, list[tuple[str, int, str, bool]]]


@dataclass(frozen=True)
class StoreMonth:
    sku: str
    sid: str
    period: dt.date
    state: str
    reason: str | None
    demand: int | None
    system: int | None
    opening: int | None
    inbound: int | None
    closing: int | None
    system_not_applicable: bool = False     # 店里每个 msku 的预测都不适用


@dataclass(frozen=True)
class SkuMonth:
    sku: str
    period: dt.date
    state: str
    demand: int | None                      # 含不适用店（期望 Σ）
    system: int | None
    opening: int | None
    stock_demand: int | None                # 只含进库存求和的 msku —— 恒等式用这个
    inbound: int | None
    closing: int | None
    excluded_not_applicable: int
    making: int
    shipping: int
    ordered_open: int
    ordered_open_overdue: int               # ordered_open 里预计到货月已过的那部分（OQ-6）
    ordered_sources: tuple[dict, ...]
    in_transit: int
    unfilled_purchase: tuple[str, ...]
    reason: str | None = None
    reason_from: tuple[str, str] | None = None   # 未知时：让它未知的那个 msku（seller_sku, sid）
    system_not_applicable: bool = False     # 每家店的预测都不适用


@dataclass(frozen=True)
class PlanForecast:
    msku: dict[tuple[str, str], list[MskuMonth]]    # 值是 MskuProjection，带 msku 级依据
    store: list[StoreMonth]
    sku: list[SkuMonth]
    beyond_horizon: list[dict]
    unallocated: list[dict]


def _sum(vals) -> int | None:
    vals = list(vals)
    return None if (not vals or any(v is None for v in vals)) else sum(vals)


def _beyond(b: Batch, reason: str) -> dict:
    return {"sku": b.sku, "order_period": b.order_period.strftime("%Y-%m"), "units": b.units,
            "placed": b.placed.isoformat(), "made": b.made.isoformat(),
            "arrive": b.arrive.isoformat(), "reason": reason}


def _combine_state(rows: list[tuple[tuple[str, str], MskuMonth]]
                   ) -> tuple[str, str | None, tuple[str, str] | None]:
    """(msku key, 该月行) → (state, reason, 让它未知的那个 msku)。店铺行与货号行共用这一条规则。"""
    if all(r.state == "elapsed" for _, r in rows):
        return "elapsed", "elapsed", None
    bad = next(((k, r) for k, r in rows if r.closing is None), None)
    if bad is not None:
        return "unknown", bad[1].reason, bad[0]
    if any(r.state == "partial" for _, r in rows):
        return "partial", "as_of_inside_month", None
    return "ok", None, None


def forecast_plan(inp: PlanForecastInput) -> PlanForecast:
    _check_periods(inp.periods)
    start, end = inp.as_of + ONE, month_end(inp.periods[-1])
    by_sku: dict[str, list[MskuInput]] = {}
    for m in inp.mskus:
        by_sku.setdefault(m.sku, []).append(m)
    skus = sorted(set(by_sku) | {s for s, _ in inp.purchase})

    batches: dict[str, list[Batch]] = {s: [] for s in skus}
    unfilled: dict[str, list[str]] = {s: [] for s in skus}
    for (sku, p), units in sorted(inp.purchase.items()):
        if units is None:
            unfilled[sku].append(p.strftime("%Y-%m"))       # ★ OP-13：不静默
        elif units < 0:
            raise ValueError(f"{sku} {p.strftime('%Y-%m')} 采购量为负：{units}")
        elif units > 0:
            batches[sku].append(Batch(sku, p, units, *order_arrival(p, inp.make_days,
                                                                     inp.ship_days)))
    beyond: list[dict] = []
    placeable: dict[str, list[Batch]] = {}
    for sku, bs in batches.items():
        for b in bs:
            if b.arrive > end:
                beyond.append(_beyond(b, "arrives_after_horizon"))
            elif b.arrive < start:
                beyond.append(_beyond(b, "arrives_before_as_of"))
            else:
                placeable.setdefault(sku, []).append(b)

    # 第一遍 · 基线（不补货）
    base = {m.key: project_msku(m, inp.as_of, inp.periods) for m in inp.mskus}
    arrivals: dict[tuple[str, str], list[Arrival]] = {m.key: [] for m in inp.mskus}
    unallocated: list[dict] = []
    for sku in skus:
        ms = {m.key: m for m in by_sku.get(sku, [])}
        keys = sorted((k for k, m in ms.items() if m.applicable), key=lambda k: (k[1], k[0]))
        baseline = {k: {r.period: r.closing for r in base[k]} for k in keys}
        weights = {k: dict(ms[k].effective) for k in keys}
        gives, lost = allocate(placeable.get(sku, []), keys, baseline, weights)   # 第二遍
        unallocated += lost
        for b, gs in gives.items():
            for g in gs:
                arrivals[g.key].append(Arrival(b.arrive, g.units, {
                    "order_period": b.order_period.strftime("%Y-%m"), "rule": g.rule,
                    "gap_part": g.gap_part, "weight_part": g.weight_part, "pool": g.pool}))
        for u in lost:
            if u["reason"] != "gap_unknown":
                continue
            # ★ OP-12：整批分不出 → 能推演的 msku 从到货日起未知。在仓未知的本来就未知，
            #   且这批已在上面批次级点名 —— 再塞给它只会经 unplaced_arrivals 重复列一遍。
            for k in keys:
                if ms[k].onhand is not None:
                    arrivals[k].append(Arrival(dt.date.fromisoformat(u["arrive"]), None,
                                               {"order_period": u["order_period"],
                                                "reason": "gap_unknown", "pool": u["units"]}))

    final = {m.key: project_msku(m, inp.as_of, inp.periods, arrivals[m.key]) for m in inp.mskus}
    for m in inp.mskus:              # ★ 推演不了的 msku 拿到的到货，上一层不许丢
        unallocated += [{"sku": m.sku, "seller_sku": m.seller_sku, "sid": m.sid,
                         "arrive": x["date"], **x} for x in final[m.key].unplaced_arrivals]

    stores: list[StoreMonth] = []
    sku_rows: list[SkuMonth] = []
    for sku in skus:
        ms_list = by_sku.get(sku, [])
        groups: dict[str, list[MskuInput]] = {}
        for m in ms_list:
            groups.setdefault(m.sid, []).append(m)
        for sid, group in groups.items():
            if len({m.applicable for m in group}) > 1:
                raise ValueError(f"{sku} 店铺 {sid} 下 msku 的 has_fba 不一致："
                                 f"{[(m.seller_sku, m.applicable) for m in group]}")
        refs = tuple({"period": per, "units": u, "ref": ref, "overdue": od}
                     for per, u, ref, od in inp.ordered_open.get(sku, []))
        ordered = sum(x["units"] for x in refs)
        ordered_overdue = sum(x["units"] for x in refs if x["overdue"])
        for i, p in enumerate(inp.periods):
            month_stores: list[StoreMonth] = []
            for sid in sorted(groups):
                group = groups[sid]
                rows = [final[m.key][i] for m in group]
                dem = _sum(m.effective.get(p) for m in group)
                sys_live = [m for m in group if m.system_applicable]
                sysu = _sum(m.system.get(p) for m in sys_live) if sys_live else None
                sys_na = not sys_live
                if not group[0].applicable:              # ★ 不适用 ≠ 0：库存三格留空
                    month_stores.append(StoreMonth(sku, sid, p, "not_applicable",
                                                   "not_applicable", dem, sysu, None, None, None,
                                                   system_not_applicable=sys_na))
                    continue
                state, reason, _ = _combine_state([(m.key, final[m.key][i]) for m in group])
                month_stores.append(StoreMonth(
                    sku, sid, p, state, reason, dem, sysu,
                    _sum(r.opening for r in rows), _sum(r.inbound for r in rows),
                    _sum(r.closing for r in rows), system_not_applicable=sys_na))
            stores += month_stores
            live = [s for s in month_stores if s.state != "not_applicable"]
            me = month_end(p)
            making = sum(b.units for b in batches[sku] if b.placed <= me < b.made)
            shipping = sum(b.units for b in batches[sku] if b.made <= me < b.arrive)
            if not month_stores:
                state, reason, reason_from = "no_msku", "no_msku", None
            elif not live:
                state, reason, reason_from = "not_applicable", "not_applicable", None
            else:
                state, reason, reason_from = _combine_state(
                    [(m.key, final[m.key][i]) for m in ms_list if m.applicable])

            sys_stores = [s for s in month_stores if not s.system_not_applicable]

            def stock(f, live=live):
                return _sum(f(s) for s in live) if live else None

            row = SkuMonth(
                sku, p, state,
                _sum(s.demand for s in month_stores) if month_stores else None,
                _sum(s.system for s in sys_stores) if sys_stores else None,
                stock(lambda s: s.opening),
                _sum(final[m.key][i].demand for m in ms_list if m.applicable) if live else None,
                stock(lambda s: s.inbound), stock(lambda s: s.closing),
                len(month_stores) - len(live), making, shipping, ordered, ordered_overdue, refs,
                making + shipping + ordered, tuple(unfilled[sku]), reason, reason_from,
                system_not_applicable=bool(month_stores) and not sys_stores)
            if (row.state == "ok" and row.closing
                    != row.opening - row.stock_demand + row.inbound):   # ★ 恒等式③ 货号层
                raise AssertionError(f"{sku} {p} 货号层恒等式不成立：{row}")
            sku_rows.append(row)
    return PlanForecast(final, stores, sku_rows, beyond, unallocated)
