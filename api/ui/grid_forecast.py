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
from dim.ch_source import is_overdue
from forecast.month_in_transit import TransitItem, month_in_transit
from forecast.plan_forecast import MskuInput, PlanForecastInput, forecast_plan
from usecase.plan_claim import NOT_APPLICABLE

log = logging.getLogger("scm.api")

_UNALLOCATED_CORE = ("sku", "order_period", "arrive", "units", "reason")
#: 调用方没给当月在途的取数（旧调用方 / 冻结样例）—— 与「给了、但未知（None）」分开
_ABSENT = object()

#: 「这个 msku 压根没有销量取数源」—— 认领记进 system_basis.reason 的那个词，定义只在 plan_claim。
NO_SALES_SOURCE = NOT_APPLICABLE


def system_not_applicable(system_basis: dict | None) -> bool:
    """★ 预测「不适用」的唯一判据：认领时记下的事实，不是 system_units 为 NULL ——
    NULL 还可能是旧认领没种（OP-8）或查过没卖（no_sales_history），那两种是未知。"""
    return bool(system_basis) and system_basis.get("reason") == NO_SALES_SOURCE


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
                     plan_id: int, purchase_as_of: dt.date | None = None,
                     fba_inbound: dict | None = None, fc: dict | None = None,
                     fba_transit=_ABSENT, oversea_transit=_ABSENT) -> dict:
    """`fba_inbound`: msku → [FbaInbound] / None（当天无货件快照，未知）；`fc`: msku → 调仓中件数
    （spec 2026-10-14）。不给 = 旧调用方：没有货件、没有调仓，不是未知。
    `fba_transit`: 货号 → [(sid, msku, FbaInbound)] / None；`oversea_transit`: 货号 → [TransitItem]，整体 None = 领星
    读不到（spec 2026-10-15 当月在途）。两个都不给 → `month_in_transit` 为 null（没取，不是 0）。"""
    t0 = time.perf_counter()
    by_key: dict[tuple[str, str], dict] = {}
    for r in demand_rows:
        k = (r["seller_sku"], r["sid"])
        slot = by_key.setdefault(k, {"sku": r["sku"], "eff": {}, "sys": {}, "sys_na": False})
        slot["sys_na"] = slot["sys_na"] or system_not_applicable(r.get("system_basis"))
        slot["eff"][_p(r["period"])] = r["effective_units"]
        slot["sys"][_p(r["period"])] = r["system_units"]
    fc = fc or {}

    def _fba(k):
        if fba_inbound is None:
            return ()
        got = fba_inbound.get(k, ())
        return None if got is None else tuple(got)

    def _onhand(k):
        # ★ OP-19 c：期初 = 可售 + 调仓中；可售未知则整体未知（不拿调仓冒充）
        o = onhand.get(k)
        if o is None:
            return None, None
        if fc_ := fc.get(k):
            return o + fc_, {"fulfillable": o, "fc_transfers": fc_}
        return o, ({"fulfillable": o, "fc_transfers": 0} if k in fc else None)

    mskus = [MskuInput(k[0], k[1], v["sku"], bool(fba[k]), _onhand(k)[0], v["eff"], v["sys"],
                       base_rate.get(k), system_applicable=not v["sys_na"], fba_inbound=_fba(k),
                       onhand_parts=_onhand(k)[1])
             for k, v in sorted(by_key.items())]
    purchase: dict[tuple[str, dt.date], int | None] = {(s, p): u for s, p, u in purchase_rows}
    # ★ forecast_plan 的货号全集 = msku ∪ purchase 的键；只在在途里出现的货号会被丢掉。
    #   没有采购格子的计划货号以「采购未填」补进去 —— 在途照算，未填照报。
    plan_skus = {m.sku for m in mskus} | {s for s, _ in purchase} | set(in_transit)
    for sku in plan_skus - {s for s, _ in purchase}:
        purchase.update({(sku, p): None for p in periods})
    # ★ 逾期按在途的快照日判（OQ-6：ETA 所在月早于它即逾期）。源没给单独的采购快照日时，
    #   它的在途就是 as_of 那天的 —— 退到 as_of，并在 params 里写明用的是哪天。
    in_transit_as_of = purchase_as_of or as_of
    inp = PlanForecastInput(
        as_of=as_of, periods=periods, make_days=lead.make_days, ship_days=lead.ship_days,
        mskus=mskus, purchase=purchase,
        ordered_open={sku: [(t.period, t.units, t.ref, is_overdue(t.period, in_transit_as_of))
                            for t in ts]
                      for sku, ts in in_transit.items()})
    # ★ 推演本身与之后的每一道自检都在这一个 try 里：哪一步炸，都留下 plan_id · 耗时 · 原因
    try:
        f = forecast_plan(inp)
        out = _serialize(f, {m.key: m.sku for m in mskus}, plan_skus, as_of, lead, embargo_days,
                         in_transit_as_of)
        if fba_transit is not _ABSENT or oversea_transit is not _ABSENT:
            _attach_month_in_transit(out["forecast_sku"], fba_transit, oversea_transit, as_of, periods)
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


def _fba_items(xs) -> list[TransitItem] | None:
    if xs is None:
        return None
    return [TransitItem("fba", x.shipment_id, x.units, x.deliver_to,
                        {"sid": sid, "msku": msku, "status": x.status,
                         "deliver_from": x.deliver_from.isoformat() if x.deliver_from else None})
            for sid, msku, x in xs]


def _attach_month_in_transit(sku_rows: list[dict], fba_transit, oversea_transit, as_of: dt.date,
                             periods: list[dt.date]) -> None:
    """货号行「当月在途」（spec 2026-10-15）。★ 这里只搬运：归月规则在 forecast/month_in_transit.py。"""
    by_sku: dict[str, dict[str, dict]] = {}
    for sku in {r["sku"] for r in sku_rows}:
        fba = None if fba_transit is _ABSENT or fba_transit is None else _fba_items(fba_transit.get(sku, []))
        ows = (None if oversea_transit is _ABSENT or oversea_transit is None
               else list(oversea_transit.get(sku, [])))
        by_sku[sku] = {m["period"]: m for m in month_in_transit(fba, ows, as_of, periods)}
    for r in sku_rows:
        m = by_sku[r["sku"]].get(r["period"])
        r["month_in_transit"] = None if m is None else {k: v for k, v in m.items() if k != "period"}


def _serialize(f, sku_of: dict[tuple[str, str], str], plan_skus: set[str], as_of: dt.date,
               lead: LeadTime, embargo_days: int | None, in_transit_as_of: dt.date) -> dict:
    got_skus = {s.sku for s in f.sku}
    if plan_skus - got_skus:            # ★ 静默丢失已踩六次：货号行少了就炸，不往下发
        raise AssertionError(f"计划货号在推演里丢了：{sorted(plan_skus - got_skus)}")
    sku_json = [{
        "sku": s.sku, "period": _ym(s.period), "state": s.state, "reason": s.reason,
        "reason_from": (None if s.reason_from is None else
                        {"seller_sku": s.reason_from[0], "sid": s.reason_from[1]}),
        "demand": s.demand, "system": s.system, "system_not_applicable": s.system_not_applicable,
        "opening": s.opening,
        "stock_demand": s.stock_demand, "inbound_planned": s.inbound, "closing": s.closing,
        "excluded_not_applicable": s.excluded_not_applicable,
        "pipeline": {"making": s.making, "shipping": s.shipping, "ordered_open": s.ordered_open,
                     "ordered_open_overdue": s.ordered_open_overdue,
                     "ordered_open_sources": list(s.ordered_sources),
                     # ★ spec 2026-10-14：已进台账（月末）的 FBA 货件剩余；不进 in_transit（不重复计）
                     "fba_inbound": s.fba_inbound,
                     "fba_inbound_reason": None if s.fba_inbound is not None else "fba_inbound_unavailable"},
        "in_transit": s.in_transit, "in_transit_excludes": ["fba_inbound"],
        "unfilled_purchase": list(s.unfilled_purchase),
        "month_in_transit": None} for s in f.sku]
    return {
        "params": {"as_of": as_of.isoformat(), "route": lead.route, "make_days": lead.make_days,
                   "ship_days": lead.ship_days, "anchor_day": lead.anchor_day,
                   "embargo_days": embargo_days, "in_transit_as_of": in_transit_as_of.isoformat()},
        "forecast": [{"seller_sku": k[0], "sid": k[1], "sku": sku_of[k],
                      "period": _ym(r.period), "state": r.state, "reason": r.reason,
                      "unknown_since": _ym(r.unknown_since), "opening": r.opening,
                      "demand": r.demand, "inbound_planned": r.inbound, "closing": r.closing,
                      "stockout_date": _iso(r.stockout_date),
                      "basis": {"bridge": r.bridge, "arrivals": list(r.arrivals),
                                "as_of_start": r.as_of_start}}
                     for k, rs in sorted(f.msku.items()) for r in rs],
        # ★ MskuProjection 是 list 子类：通用序列化会丢掉 msku 级依据，这里显式发出去
        "forecast_msku": [{"seller_sku": k[0], "sid": k[1], "sku": sku_of[k],
                           "first_stockout_date": _iso(rs.first_stockout_date)}
                          for k, rs in sorted(f.msku.items())],
        "forecast_store": [{"sku": s.sku, "sid": s.sid, "period": _ym(s.period),
                            "state": s.state, "reason": s.reason, "demand": s.demand,
                            "system": s.system, "system_not_applicable": s.system_not_applicable,
                            "opening": s.opening,
                            "inbound_planned": s.inbound, "closing": s.closing}
                           for s in f.store],
        "forecast_sku": sku_json,
        "beyond_horizon": f.beyond_horizon,
        "unallocated": [unallocated_json(u) for u in f.unallocated],
    }
