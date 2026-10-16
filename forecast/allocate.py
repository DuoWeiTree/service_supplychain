"""到货给哪个 msku —— 14 §3.1 两遍法（spec §4.4）。纯函数。

★ 这是**预测**不是排货：不写任何表、不产生动作，依据如实记录实际怎么分的（14 §3.2 · §3.3）。
★ 算法照原型 docs/prototype/forecast_demo.py::run_forecast 第二遍 + _spread。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from forecast.calendar import month_start

Key = tuple[str, str]


@dataclass(frozen=True)
class Batch:
    sku: str
    order_period: dt.date
    units: int
    placed: dt.date
    made: dt.date
    arrive: dt.date


@dataclass(frozen=True)
class Give:
    key: Key
    units: int
    gap_part: int
    weight_part: int
    rule: str
    pool: int


def spread(amount: int, weights: dict[Key, int], order: list[Key]) -> dict[Key, int]:
    """按权重摊整数，余数给权重最大的（并列按 order 先后）。★ 合计必须等于 amount。
    ★ 权重全 0 → 按个数均分（OP-11）。"""
    if amount < 0:
        raise ValueError(f"摊负数：{amount}")
    w = {k: weights[k] for k in order}
    if sum(w.values()) == 0:
        w = {k: 1 for k in order}
    tw = sum(w.values())
    out = {k: amount * w[k] // tw for k in order}
    rest = amount - sum(out.values())
    for k in sorted(order, key=lambda k: -w[k])[:rest]:   # sorted 稳定：并列按 order
        out[k] += 1
    assert sum(out.values()) == amount
    return out


def _unallocated(b: Batch, reason: str, unknown: list[Key]) -> dict:
    return {"sku": b.sku, "order_period": b.order_period.strftime("%Y-%m"),
            "arrive": b.arrive.isoformat(), "units": b.units, "reason": reason,
            "unknown": [list(k) for k in unknown]}


def allocate(batches: list[Batch], keys: list[Key],
             baseline: dict[Key, dict[dt.date, int | None]],
             weights: dict[Key, dict[dt.date, int | None]]
             ) -> tuple[dict[Batch, list[Give]], list[dict]]:
    gives: dict[Batch, list[Give]] = {}
    unallocated: list[dict] = []
    allocated = {k: 0 for k in keys}
    for b in sorted(batches, key=lambda x: (x.arrive, x.order_period)):
        if not keys:
            unallocated.append(_unallocated(b, "no_fba_msku", []))
            continue
        am = month_start(b.arrive)
        gap = {k: baseline[k].get(am) for k in keys}
        w = {k: weights[k].get(am) for k in keys}
        unknown = [k for k in keys if gap[k] is None or w[k] is None]
        if unknown:
            unallocated.append(_unallocated(b, "gap_unknown", unknown))
            continue
        need = {k: max(0, max(0, -gap[k]) - allocated[k]) for k in keys}
        total, pool = sum(need.values()), b.units
        tw = sum(w.values())
        if total == 0:
            give = spread(pool, w, keys)
            parts = {k: (0, give[k], f"无人缺货，按计划销量比例 {w[k]}/{tw}") for k in keys}
        elif total <= pool:
            extra = spread(pool - total, w, keys)
            give = {k: need[k] + extra[k] for k in keys}
            parts = {k: (need[k], extra[k], f"缺口 {need[k]} 补齐，余量按计划销量再摊 {extra[k]}")
                     for k in keys}
        else:
            give = {k: pool * need[k] // total for k in keys}
            for k in sorted(keys, key=lambda k: -need[k])[:pool - sum(give.values())]:
                give[k] += 1
            parts = {k: (give[k], 0, f"不够分，按缺口比例 {need[k]}/{total} × {pool}")
                     for k in keys}
        if sum(give.values()) != pool:   # ★ 14 §3.1：对不上就是管道漏货，宁可报错
            raise AssertionError(f"{b} 分配合计 {sum(give.values())} ≠ 这批 {pool}")
        for k in keys:
            allocated[k] += give[k]
        gives[b] = [Give(k, give[k], parts[k][0], parts[k][1], parts[k][2], pool)
                    for k in keys if give[k] > 0]
    return gives, unallocated
