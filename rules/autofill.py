"""新建 FBA 排货表时的默认分配（UX-D7 ~ D10 · spec 2026-10-16 §1.2）。★ 纯函数，离线可测（L1）。

★ 产出就是人的格子（UX-D7）：调用方拿去走与 `PUT /cells` 同一套闸写进 dispatch_arrangement；
  「这是系统填的」只记在事件表，不记在格子上（铁律 6）。
"""
from __future__ import annotations

from rules.dispatch import available_for

RULE = "UX-D8/D9 v1"


def split(total: int, weights: dict) -> dict:
    """最大余数法：Σ 恰好 = total。余数相同按键序先给（可复现）。"""
    w_sum = sum(weights.values())
    keys = sorted(weights)
    base = {k: total * weights[k] // w_sum for k in keys}
    left = total - sum(base.values())
    by_rem = sorted(keys, key=lambda k: (-(total * weights[k] % w_sum), k))
    for k in by_rem[:left]:
        base[k] += 1
    return base


def _usable(items: dict, weight_of) -> tuple[dict, list]:
    """只有一个候选时比例无关，全给它（UX-Q10）；多个时期望空 / 0 的不分（空 ≠ 0，不猜）。"""
    if len(items) == 1:
        return {k: 1 for k in items}, []
    kept = {k: weight_of(v) for k, v in items.items() if (weight_of(v) or 0) > 0}
    return kept, sorted(k for k in items if k not in kept)


def _is_fbm(seller_sku: str) -> bool:
    return seller_sku.upper().endswith("-FBM")      # ★ UX-Q12 暂行：自发货 msku 不进 FBA 表


def allocate(lines: list[dict], sellers: dict, bridge: dict, buckets: dict, committed: dict) -> dict:
    """lines: [{line_id, sku, period, pending, demand_by_seller}]；sellers: {sid: {has_fba}}；
    bridge: {(seller_sku, sid): sku}；buckets: {(sku, wid): {bucket: units}}（只含本地仓）；
    committed: {(sku, wid): {sid: units}}（已承诺未发，跨全部排货表）。
    → {"filled": [{line_id, sku, seller_sku, sid, from_wid, units}], "skipped": [{line_id, reason, …}]}"""
    need = {k: dict(v) for k, v in committed.items()}         # 每取一格就记进去：后面的记录看到的是扣过的
    filled: list[dict] = []
    skipped: list[dict] = []
    for ln in sorted(lines, key=lambda x: (str(x["period"]), x["sku"], x["line_id"])):   # ★ UX-D9：月份先后
        lid, sku, pending = ln["line_id"], ln["sku"], ln["pending"]
        if pending <= 0:
            skipped.append({"line_id": lid, "reason": "satisfied", "pending": pending})
            continue
        stores: dict[str, dict] = {}
        for sid, info in sorted((ln["demand_by_seller"] or {}).items()):
            info = info or {}
            if sid not in sellers:
                skipped.append({"line_id": lid, "sid": sid, "reason": "seller_not_in_mirror"})
                continue
            if not sellers[sid].get("has_fba"):
                skipped.append({"line_id": lid, "sid": sid, "reason": "no_fba"})
                continue
            mskus: dict[str, dict] = {}
            for m in info.get("mskus") or []:
                ss = m["seller_sku"]
                if _is_fbm(ss):
                    skipped.append({"line_id": lid, "sid": sid, "seller_sku": ss, "reason": "fbm_msku"})
                elif (ss, sid) not in bridge:
                    skipped.append({"line_id": lid, "sid": sid, "seller_sku": ss, "reason": "msku_not_in_bridge"})
                elif bridge[(ss, sid)] != sku:
                    skipped.append({"line_id": lid, "sid": sid, "seller_sku": ss, "reason": "msku_not_this_sku",
                                    "msku_sku": bridge[(ss, sid)]})
                else:
                    mskus[ss] = m
            if not info.get("mskus"):
                skipped.append({"line_id": lid, "sid": sid, "reason": "no_claimed_msku"})
                continue
            kept, dropped = _usable(mskus, lambda m: m.get("units"))
            skipped += [{"line_id": lid, "sid": sid, "seller_sku": ss, "reason": "expected_unknown"} for ss in dropped]
            if kept:
                stores[sid] = {"expected": info.get("units"), "mskus": kept}
        s_kept, s_dropped = _usable(stores, lambda s: s["expected"])
        skipped += [{"line_id": lid, "sid": sid, "reason": "expected_unknown"} for sid in s_dropped]
        if not s_kept:
            skipped.append({"line_id": lid, "reason": "no_eligible_store", "pending": pending})
            continue
        got = 0
        for sid, share in split(pending, s_kept).items():
            for ss, m_share in split(share, stores[sid]["mskus"]).items():
                got += _take(filled, need, buckets, lid, sku, ss, sid, m_share)
        if got < pending:
            skipped.append({"line_id": lid, "reason": "stock_short" if got else "no_stock",
                            "wanted": pending, "filled": got})
    return {"filled": filled, "skipped": skipped}


def _take(filled: list, need: dict, buckets: dict, lid, sku: str, ss: str, sid: str, units: int) -> int:
    """可安排多的仓先取，不够溢到下一个；每格封顶到该仓对这家店的可安排（UX-D8）。"""
    if units <= 0:
        return 0
    wids = sorted((w for (k, w) in buckets if k == sku),
                  key=lambda w: (-available_for(sid, need.get((sku, w), {}), buckets[(sku, w)]), w))
    got = 0
    for w in wids:
        avail = available_for(sid, need.get((sku, w), {}), buckets[(sku, w)])
        take = min(units - got, avail)
        if take <= 0:
            continue
        filled.append({"line_id": lid, "sku": sku, "seller_sku": ss, "sid": sid, "from_wid": w, "units": take})
        n = need.setdefault((sku, w), {})
        n[sid] = n.get(sid, 0) + take
        got += take
        if got == units:
            break
    return got
