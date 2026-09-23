"""提交闸：哪些格子铸成记录、哪些被跳过、各店的期望销量冻结成什么。"""
from __future__ import annotations

from typing import NamedTuple

from rules.effective import effective_demand

#: basis 的强弱序。★ 一个店铺下多个 msku 时取**最弱**的那档 ——
#  取最强的会把「有一半是猜的」说成「人填的」。
_BASIS_RANK = {"human": 0, "system": 1, "unknown": 2}


class PurchaseCell(NamedTuple):
    sku: str
    period: str
    planned_units: int | None


class DemandCell(NamedTuple):
    seller_sku: str
    sid: str
    sku: str
    period: str
    system_units: int | None
    expected_units: int | None


class Line(NamedTuple):
    sku: str
    period: str
    total_units: int
    demand_by_seller: dict      # {sid: {"units": int|None, "basis": str, "mskus": [...]}}
    demand_at_submit: int


class Skipped(NamedTuple):
    sku: str
    period: str
    reason: str                 # zero_purchase | no_claimed_msku


def select_submittable(
    purchase_cells: list[PurchaseCell],
    demand_cells: list[DemandCell],
    claimed: set[tuple[str, str]],
) -> tuple[list[Line], list[Skipped]]:
    """`claimed` = 该计划当前仍有效的 (seller_sku, sid) 集合。"""
    stray = sorted({(c.seller_sku, c.sid) for c in demand_cells} - claimed)
    if stray:
        # ★ 过滤掉就是静默丢失：格子存在而认领没了，是数据坏了，不是「少算一点」
        raise ValueError(f"期望销量格子指向未认领的 msku：{stray}")

    skus_with_claim = {c.sku for c in demand_cells}
    lines: list[Line] = []
    skipped: list[Skipped] = []

    for pc in sorted(purchase_cells):
        if pc.sku not in skus_with_claim:
            # ★ 先判结构：没有落点比「填了 0」更早一步，两个都成立时报这个
            skipped.append(Skipped(pc.sku, pc.period, "no_claimed_msku"))
            continue
        if not pc.planned_units:            # None 或 0
            skipped.append(Skipped(pc.sku, pc.period, "zero_purchase"))
            continue

        by_sid: dict[str, dict] = {}
        for dc in sorted(demand_cells):
            if dc.sku != pc.sku or dc.period != pc.period:
                continue
            eff = effective_demand(dc.system_units, dc.expected_units)
            slot = by_sid.setdefault(dc.sid, {"units": 0, "basis": "human", "mskus": []})
            slot["mskus"].append({"seller_sku": dc.seller_sku,
                                  "units": eff.units, "basis": eff.basis})
            if eff.units is not None:
                slot["units"] += eff.units
            if _BASIS_RANK[eff.basis] > _BASIS_RANK[slot["basis"]]:
                slot["basis"] = eff.basis
        for slot in by_sid.values():
            if all(m["units"] is None for m in slot["mskus"]):
                slot["units"] = None        # ★ 一个都没有 → null，不是 0
        total_demand = sum(s["units"] or 0 for s in by_sid.values())
        lines.append(Line(pc.sku, pc.period, pc.planned_units, by_sid, total_demand))

    return lines, skipped
