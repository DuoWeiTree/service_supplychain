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
    reason: str                 # empty_purchase | zero_purchase | no_claimed_msku


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
        if pc.planned_units is None:
            # ★ 空 ≠ 0（R1）：没填 = 没打算采购；填了 0 = 明确表态。两者给人看的处置不同
            skipped.append(Skipped(pc.sku, pc.period, "empty_purchase"))
            continue
        if pc.planned_units == 0:
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


class PrevLine(NamedTuple):
    """计划里一条未撤销的记录（含 已完结 —— 裁定 A）。"""
    line_id: int
    sku: str
    period: str
    total_units: int
    demand_by_seller: dict
    state: str


class Reconciled(NamedTuple):
    kept: list[PrevLine]
    replaced: list[tuple[PrevLine, Line]]
    added: list[Line]
    removed: list[PrevLine]
    #: 保留里「总量没变、分店拆分变了、但已进采购」的 —— 带走旧拆分（裁定 B(iii)）
    split_frozen: list[tuple[PrevLine, Line]]
    #: 这次铸不出、但已 已完结 的：终态撤不掉，原地留着（裁定 A）
    left_completed: list[PrevLine]
    #: D1：要替换 / 去掉、但已进采购或已过 已提交 的
    blocked: list[tuple[PrevLine, str]]


DONE = "已完结"


def seller_units(demand_by_seller: dict) -> dict:
    """裁定 B(i)：「没变」只比各店的量 —— basis 与 msku 明细不算（重敲同一个数不是变）。"""
    return {sid: (v or {}).get("units") for sid, v in demand_by_seller.items()}


def reconcile(prev: list[PrevLine], lines: list[Line],
              referenced: set[int] = frozenset()) -> Reconciled:
    """按 (货号, 月) 对比计划里每格最新的未撤销记录与这次能铸出的（spec §2 + 裁定 A/B）。

    `referenced` = 被任一未作废采购单（含草稿）引用的 line_id。
    ★ 已进采购（被引用或已过 已提交）的记录只看总量：采购是按它冻结的拆分做的。
    """
    by_cell: dict[tuple[str, str], list[PrevLine]] = {}
    for p in prev:
        by_cell.setdefault((p.sku, p.period), []).append(p)
    dup = {k: sorted(x.line_id for x in v if x.state != DONE)
           for k, v in by_cell.items() if sum(x.state != DONE for x in v) > 1}
    if dup:
        raise ValueError(f"同一货号·月有多条活记录（违反 S-4）：{sorted(dup.items())}")
    # ★ 活记录优先：它不处置，旧版就让不出在流转位；没有活的才看最近那条 已完结
    old = {k: max(v, key=lambda x: (x.state != DONE, x.line_id)) for k, v in by_cell.items()}
    new = {(m.sku, m.period): m for m in lines}

    def in_purchase(p: PrevLine) -> bool:
        return p.state != "已提交" or p.line_id in referenced

    kept, replaced, added, split_frozen, blocked = [], [], [], [], []
    for k in sorted(new):
        m, p = new[k], old.get(k)
        if p is None:
            added.append(m)
            continue
        same_split = seller_units(p.demand_by_seller) == seller_units(m.demand_by_seller)
        if p.total_units == m.total_units and (same_split or in_purchase(p)):
            kept.append(p)
            if not same_split:
                split_frozen.append((p, m))
            continue
        replaced.append((p, m))
        if in_purchase(p):
            blocked.append((p, "replace"))
    removed, left_completed = [], []
    for k in sorted(old.keys() - new.keys()):
        p = old[k]
        if p.state == DONE:
            left_completed.append(p)
            continue
        removed.append(p)
        if in_purchase(p):
            blocked.append((p, "remove"))
    blocked.sort(key=lambda x: (x[0].sku, x[0].period))
    return Reconciled(kept, replaced, added, removed, split_frozen, left_completed, blocked)
