"""grid 的预测块（spec §6）：把 PG / 取数源的值装成 forecast_plan() 的入参，再序列化。
★ 这里不算任何一个数 —— 算都在 forecast/（纯函数）；这里只搬运与改名。

响应里 `unallocated[]` 有两种来源（Task 7），共同核心键 `sku · order_period · arrive · units ·
reason`，`scope` 区分：
  · `batch` —— 整批分不出去（allocate），另带 `unknown`：[[seller_sku, sid], …]
  · `msku`  —— 分到了、但这个 msku 推演不了（不适用 / 在仓未知），另带 `seller_sku · sid` 及分配依据
"""
from __future__ import annotations

import datetime as dt
import logging
import time

from api.ui.lead_time import LeadTime
from forecast.plan_forecast import MskuInput, PlanForecastInput, forecast_plan

log = logging.getLogger("scm.api")

_UNALLOCATED_CORE = ("sku", "order_period", "arrive", "units", "reason")


def _ym(d: dt.date | None) -> str | None:
    return d.strftime("%Y-%m") if d else None


def _p(s: str) -> dt.date:
    return dt.date.fromisoformat(s + "-01")


def _iso(d: dt.date | None) -> str | None:
    return d.isoformat() if d else None


def unallocated_json(u: dict) -> dict:
    missing = [k for k in _UNALLOCATED_CORE if k not in u]
    if missing:                         # ★ 缺核心键前端就读到 undefined —— 在这里炸，不往下传
        raise AssertionError(f"unallocated 条目缺核心键 {missing}：{u}")
    return {**u, "scope": "msku" if "seller_sku" in u else "batch"}


def forecast_payload(*, as_of: dt.date, periods: list[dt.date], demand_rows: list[dict],
                     purchase_rows: list[tuple], fba: dict, onhand: dict, base_rate: dict,
                     in_transit: dict, lead: LeadTime, embargo_days: int | None,
                     plan_id: int) -> dict:
    t0 = time.perf_counter()
    by_key: dict[tuple[str, str], dict] = {}
    for r in demand_rows:
        k = (r["seller_sku"], r["sid"])
        slot = by_key.setdefault(k, {"sku": r["sku"], "eff": {}, "sys": {}})
        slot["eff"][_p(r["period"])] = r["effective_units"]
        slot["sys"][_p(r["period"])] = r["system_units"]
    mskus = [MskuInput(k[0], k[1], v["sku"], bool(fba[k]), onhand.get(k), v["eff"], v["sys"],
                       base_rate.get(k)) for k, v in sorted(by_key.items())]
    purchase: dict[tuple[str, dt.date], int | None] = {(s, p): u for s, p, u in purchase_rows}
    # ★ forecast_plan 的货号全集 = msku ∪ purchase 的键；只在在途里出现的货号会被丢掉。
    #   没有采购格子的计划货号以「采购未填」补进去 —— 在途照算，未填照报。
    plan_skus = {m.sku for m in mskus} | {s for s, _ in purchase} | set(in_transit)
    for sku in plan_skus - {s for s, _ in purchase}:
        purchase.update({(sku, p): None for p in periods})
    inp = PlanForecastInput(
        as_of=as_of, periods=periods, make_days=lead.make_days, ship_days=lead.ship_days,
        mskus=mskus, purchase=purchase,
        ordered_open={sku: [(t.period, t.units, t.ref) for t in ts]
                      for sku, ts in in_transit.items()})
    # ★ 推演本身与之后的每一道自检都在这一个 try 里：哪一步炸，都留下 plan_id · 耗时 · 原因
    try:
        f = forecast_plan(inp)
        out = _serialize(f, {m.key: m.sku for m in mskus}, plan_skus, as_of, lead, embargo_days)
    except Exception as e:
        log.error("op=grid_forecast plan_id=%s outcome=fail mskus=%d make_days=%d ship_days=%d"
                  " elapsed_ms=%d error=%s: %s", plan_id, len(mskus), lead.make_days,
                  lead.ship_days, (time.perf_counter() - t0) * 1000, type(e).__name__, e)
        raise
    log.info("op=grid_forecast plan_id=%s outcome=ok mskus=%d skus=%d beyond=%d unallocated=%d"
             " make_days=%d ship_days=%d elapsed_ms=%d", plan_id, len(mskus), len(plan_skus),
             len(f.beyond_horizon), len(f.unallocated), lead.make_days, lead.ship_days,
             (time.perf_counter() - t0) * 1000)
    return out


def _serialize(f, sku_of: dict[tuple[str, str], str], plan_skus: set[str], as_of: dt.date,
               lead: LeadTime, embargo_days: int | None) -> dict:
    got_skus = {s.sku for s in f.sku}
    if plan_skus - got_skus:            # ★ 静默丢失已踩六次：货号行少了就炸，不往下发
        raise AssertionError(f"计划货号在推演里丢了：{sorted(plan_skus - got_skus)}")
    sku_json = [{
        "sku": s.sku, "period": _ym(s.period), "state": s.state, "reason": s.reason,
        "reason_from": (None if s.reason_from is None else
                        {"seller_sku": s.reason_from[0], "sid": s.reason_from[1]}),
        "demand": s.demand, "system": s.system, "opening": s.opening,
        "stock_demand": s.stock_demand, "inbound_planned": s.inbound, "closing": s.closing,
        "excluded_not_applicable": s.excluded_not_applicable,
        "pipeline": {"making": s.making, "shipping": s.shipping, "ordered_open": s.ordered_open,
                     "ordered_open_sources": list(s.ordered_sources),
                     "fba_inbound": None, "fba_inbound_reason": "not_fetched"},
        "in_transit": s.in_transit, "in_transit_excludes": ["fba_inbound"],
        "unfilled_purchase": list(s.unfilled_purchase)} for s in f.sku]
    return {
        "params": {"as_of": as_of.isoformat(), "route": lead.route, "make_days": lead.make_days,
                   "ship_days": lead.ship_days, "anchor_day": lead.anchor_day,
                   "embargo_days": embargo_days},
        "forecast": [{"seller_sku": k[0], "sid": k[1], "sku": sku_of[k],
                      "period": _ym(r.period), "state": r.state, "reason": r.reason,
                      "unknown_since": _ym(r.unknown_since), "opening": r.opening,
                      "demand": r.demand, "inbound_planned": r.inbound, "closing": r.closing,
                      "stockout_date": _iso(r.stockout_date),
                      "basis": {"bridge": r.bridge, "arrivals": list(r.arrivals)}}
                     for k, rs in sorted(f.msku.items()) for r in rs],
        # ★ MskuProjection 是 list 子类：通用序列化会丢掉 msku 级依据，这里显式发出去
        "forecast_msku": [{"seller_sku": k[0], "sid": k[1], "sku": sku_of[k],
                           "first_stockout_date": _iso(rs.first_stockout_date)}
                          for k, rs in sorted(f.msku.items())],
        "forecast_store": [{"sku": s.sku, "sid": s.sid, "period": _ym(s.period),
                            "state": s.state, "reason": s.reason, "demand": s.demand,
                            "system": s.system, "opening": s.opening,
                            "inbound_planned": s.inbound, "closing": s.closing}
                           for s in f.store],
        "forecast_sku": sku_json,
        "beyond_horizon": f.beyond_horizon,
        "unallocated": [unallocated_json(u) for u in f.unallocated],
    }
