"""采购组单的纯判据（spec §3.2 §5，PB-D2/D3/D9）。

★ 与迁移 012 的 seller_caps() / v_check_line_state 逐字同口径：
  最大余数法，并列按 seller_id 升序；状态判据照 04:378-380。
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation

NON_TERMINAL_PURCHASE_STATES = ("已提交", "已确认", "已下单")

#: 白名单里的边（003 + 010）。只描述本模块会走的那几条。
_EDGES = {
    ("已提交", "已确认"), ("已确认", "已下单"),
    ("已确认", "已提交"), ("已下单", "已确认"), ("已下单", "已提交"),
}
_FORWARD = ["已提交", "已确认", "已下单"]


class NoSellerBasis(Exception):
    def __init__(self, sellers: list[str]):
        self.sellers = sellers
        super().__init__(f"期望销量无法作为分店上限依据：{sellers or '（没有任何店）'}")


def seller_caps(total_units: int, demand_by_seller: dict) -> dict[str, int]:
    bad = sorted(s for s, v in demand_by_seller.items()
                 if (v or {}).get("basis") == "unknown" or not isinstance((v or {}).get("units"), int))
    if bad:
        raise NoSellerBasis(bad)
    expected = {s: v["units"] for s, v in demand_by_seller.items()}
    total_expected = sum(expected.values())
    if total_expected == 0:
        raise NoSellerBasis(sorted(expected))
    floors = {s: total_units * e // total_expected for s, e in expected.items()}
    extra = total_units - sum(floors.values())
    by_remainder = sorted(expected, key=lambda s: (-(total_units * expected[s] % total_expected), s))
    for s in by_remainder[:extra]:
        floors[s] += 1
    return floors


def target_state(total: int, confirmed: int, ordered: int) -> str:
    if total > 0 and ordered == total:
        return "已下单"
    if total > 0 and confirmed == total:
        return "已确认"
    return "已提交"


def transition_path(current: str, target: str) -> list[tuple[str, str]]:
    """前进一格一格走（组单 + 下单可连跳，04:370）；退回走直达边。"""
    if current == target:
        return []
    if (current, target) in _EDGES and _FORWARD.index(target) < _FORWARD.index(current):
        return [(current, target)]
    path, at = [], current
    while at != target:
        nxt = _FORWARD[_FORWARD.index(at) + 1]
        path.append((at, nxt))
        at = nxt
    return path


def _gate(name: str, ok: bool, **detail) -> dict:
    return {"gate": name, "ok": bool(ok), "detail": detail}


def _nonneg_number(x) -> bool:
    """单价从库里读回是 numeric::text（'2.0000'），从请求来可能是数字或字符串 —— 统一按 Decimal 判。"""
    try:
        return Decimal(str(x)) >= 0
    except (InvalidOperation, TypeError, ValueError):
        return False


def preflight(po: dict) -> list[dict]:
    """★ 一次返回全部闸（spec §11 G6），不是遇到第一个就停。"""
    gates = [
        _gate("supplier", po.get("supplier_id")),
        _gate("purchaser", po.get("purchaser_id")),
        _gate("opt_uid", po.get("opt_uid")),
        _gate("qc_type", po.get("qc_type") in (0, 1, 2), got=po.get("qc_type")),
        _gate("lines", po.get("lines")),
    ]
    for ln in po.get("lines") or []:
        n = ln.get("line_no")
        gates.append(_gate("line_wid", ln.get("wid"), line_no=n))
        gates.append(_gate("line_price", ln.get("price") is not None and _nonneg_number(ln["price"]), line_no=n))
        gates.append(_gate("line_units", (ln.get("units") or 0) >= (ln.get("sources_units") or 0) > -1,
                           line_no=n, units=ln.get("units"), sources=ln.get("sources_units")))
        unplanned = (ln.get("units") or 0) - (ln.get("sources_units") or 0)
        gates.append(_gate("line_unplanned_reason",
                           unplanned <= 0 or (ln.get("unplanned_reason") or "").strip(),
                           line_no=n, unplanned=max(unplanned, 0)))
    return gates


def po_digest(po: dict) -> str:
    return hashlib.sha256(json.dumps(po, sort_keys=True, ensure_ascii=False,
                                     default=str).encode("utf-8")).hexdigest()


def confirm_token(po_id: int, digest: str, salt: str) -> str:
    """H-1：派生、不存储、不过期；内容一变 token 就对不上（00:350-357）。"""
    return hashlib.sha256(f"po|{po_id}|{digest}|{salt}".encode()).hexdigest()
