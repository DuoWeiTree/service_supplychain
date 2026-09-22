"""库存推演。阶段 A 的口径（00e §1）：

    期末 = 期初 − 期望销量 + 当期入库（在仓 + 采购在途，全是 CH 现成事实）

★ 本计划的计划采购量**不在**加项里 —— 它要穿过四段管道才可售，而管道属阶段 B/C。
  所以每一格都带 excludes_plan_purchase，把「没算进来」说出来；该标记**由明细算出**，
  不是常量 —— 一个永远为 True 的标记在真掺进计划采购时会撒谎。

★ closing 为 None 的那两种格子（未知 / 不适用），shortage 与 gap 一律跟着 None：
  「不知道缺多少」不是「一件都不缺」，给 0/False 等于替人回答。
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import NamedTuple


class InboundSource(NamedTuple):
    units: int
    kind: str       # 阶段 A 只有 'purchase_in_transit'
    ref: str        # 单号等可回查的依据


class PeriodMismatch(Exception):
    def __init__(self, extra: list[str]):
        self.extra = extra
        super().__init__(f"入库落在没有期望销量的月份上：{extra}。静默忽略就是漏货。")


def inventory_projection(
    onhand: int | None,
    inbound_by_month: Mapping[str, Sequence[InboundSource]],
    expected_by_month: Mapping[str, int | None],
) -> list[dict]:
    """periods 由 expected_by_month 的键给出（每个计划月都必须有键，值可以是 None）。

    ★ inbound 传的是**明细**不是合计：合计在这里求和，
      「有结论必有依据」就不依赖调用方自觉（恒等式⑥）。
    """
    periods = sorted(expected_by_month)
    extra = sorted(set(inbound_by_month) - set(periods))
    if extra:
        raise PeriodMismatch(extra)

    rows: list[dict] = []
    cur: int | None = onhand
    unknown = False
    for period in periods:
        sources = list(inbound_by_month.get(period, ()))
        inbound = sum(s.units for s in sources)
        demand = expected_by_month[period]
        basis = {
            "opening": cur,
            "demand": demand,
            "inbound": inbound,
            "sources": [{"units": s.units, "kind": s.kind, "ref": s.ref} for s in sources],
            # ★ 00e:43 要求标出来：这条曲线里有没有本计划的采购。
            #   ★ 算出来，不许盖章 —— 盖死成 True 的话，真掺进一笔计划采购时
            #     标记仍说「没算」，而标记撒谎比没有标记更坏：看的人会信它。
            "excludes_plan_purchase": not any(s.kind == "plan_purchase" for s in sources),
        }
        if onhand is None:
            # ★ 该店铺没有 FBA（02 §3.1a）—— 不适用，不是 0，也不是未知
            basis["reason"] = "not_applicable"
            rows.append({"period": period, "opening": None, "demand": demand,
                         "inbound": inbound, "closing": None, "shortage": None, "gap": None,
                         "unknown": False, "not_applicable": True, "basis": basis})
            continue
        if demand is None:
            unknown = True
        if unknown:
            basis["reason"] = "unknown_demand"
            rows.append({"period": period, "opening": cur, "demand": demand,
                         "inbound": inbound, "closing": None, "shortage": None, "gap": None,
                         "unknown": True, "not_applicable": False, "basis": basis})
            cur = None
            continue
        closing = cur - demand + inbound
        rows.append({"period": period, "opening": cur, "demand": demand,
                     "inbound": inbound, "closing": closing,
                     "shortage": closing < 0, "gap": -closing if closing < 0 else 0,
                     "unknown": False, "not_applicable": False, "basis": basis})
        cur = closing
    return rows
