"""采购组单的纯判据（spec §3.2 §5，PB-D2/D9/D22）。

★ 与迁移 014 的 assert_line_po_qty / 012 的 v_check_line_state 同口径：
  量闸只有总量 + 店在记录里（PB-D22 取消分店上限）；状态判据照 04:378-380。
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation

NON_TERMINAL_PURCHASE_STATES = ("已提交", "已确认", "已下单")
ORDERABLE_STATES = ("已提交", "已确认")   # 同 010 po_source_precondition

#: 白名单里的边（003 + 010）。只描述本模块会走的那几条。
_EDGES = {
    ("已提交", "已确认"), ("已确认", "已下单"),
    ("已确认", "已提交"), ("已下单", "已确认"), ("已下单", "已提交"),
}
_FORWARD = ["已提交", "已确认", "已下单"]


def stray_sellers(demand_by_seller: dict, seller_ids) -> list[str]:
    """来源店不在这条记录里（PB-D22：上限取消后，店的范围仍限于记录里的店）。"""
    return sorted({s for s in seller_ids if s not in (demand_by_seller or {})})


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
    # PB-D25：领星一张采购单只有单头一个仓（createPurchaseOrder 无行级仓；单头 wid=0 实收「仓库参数错误」，erp_call 3）
    wids = sorted({ln["wid"] for ln in po.get("lines") or [] if ln.get("wid") is not None})
    gates.append(_gate("single_wid", len(wids) <= 1, wids=wids))
    gates.append(_sources_orderable(po))
    return gates


def _sources_orderable(po: dict) -> dict:
    """组单之后记录可能被撤销（映射不随之消失）—— 下单前再判一次来源记录的状态。
    ★ 有来源却没带上状态 = 没查过，不是「都可组单」。"""
    has_sources = any(ln.get("sources") for ln in po.get("lines") or [])
    states = po.get("source_line_states")
    if states is None:
        return _gate("sources_orderable", not has_sources, offenders=[], loaded=False)
    offenders = [{"line_id": s["line_id"], "state": s["state"]} for s in states
                 if s["state"] not in ORDERABLE_STATES]
    return _gate("sources_orderable", not offenders, offenders=offenders)


def po_digest(po: dict) -> str:
    return hashlib.sha256(json.dumps(po, sort_keys=True, ensure_ascii=False,
                                     default=str).encode("utf-8")).hexdigest()


def confirm_token(po_id: int, digest: str, salt: str) -> str:
    """H-1：派生、不存储、不过期；内容一变 token 就对不上（00:350-357）。"""
    return hashlib.sha256(f"po|{po_id}|{digest}|{salt}".encode()).hexdigest()


def create_order_payload(po: dict) -> dict:
    """createPurchaseOrder 入参（17:50-51 · 06:186-190）。
    ★ 仓只在单头（PB-D25）：官方文档 product_list 无 wid；单头 wid=0 被拒「仓库参数错误」（erp_call 3，
      2026-10-09）。读回里 55/143 张单头 wid=0（B7）是领星侧的多仓形态，开放接口建不出来。
    ★ 发 sys_wid / sys_supplier_id，不发 wid / supplier_id（PB-D26）：后两者是「客户 id」，只有经开放接口建的
      仓 / 供应商才有；我们的仓号来自仓库列表（文档：wid = 系统仓库id）、供应商号来自读回（供应商列表文档：
      supplier_id = 系统供应商id）。发成 wid 实收「仓库不存在」（erp_call 4）。
    ★ price 转 Decimal：签名前由 erp.sign.normalize 统一成 JS 同款的数字打印（2.0000 → 2）。"""
    wids = {ln["wid"] for ln in po["lines"]}
    if len(wids) != 1:
        # preflight 的 single_wid 先挡；走到这里是绕过了闸，宁可炸也不发一张必被拒 / 发错仓的单
        raise ValueError(f"一张领星采购单只能一个仓，得到 {sorted(wids, key=str)}")
    out = {"custom_order_sn": po["custom_order_sn"],
           "sys_supplier_id": int(po["supplier_id"]) if str(po["supplier_id"]).isdigit() else po["supplier_id"],
           "purchaser_id": po["purchaser_id"], "opt_uid": po["opt_uid"], "qc_type": po["qc_type"],
           "sys_wid": next(iter(wids)),
           "product_list": []}
    for ln in po["lines"]:
        item = {"sku": ln["sku"], "quantity_real": ln["units"], "price": Decimal(str(ln["price"]))}
        if ln.get("expect_arrive_date"):
            item["expect_arrive_time"] = ln["expect_arrive_date"]
        out["product_list"].append(item)
    return out


def extract_order_sn(body: dict) -> str | None:
    """★ 响应形状未实测（spec §6.7）：认得出就用，认不出返回 None 交给查重，不猜。"""
    d = (body or {}).get("data")
    if isinstance(d, dict):
        return d.get("order_sn") or None
    if isinstance(d, list) and d and isinstance(d[0], dict):
        return d[0].get("order_sn") or None
    return None


def orders_by_custom_sn(orders: list[dict], custom_sn: str) -> list[dict]:
    """同一幂等键在领星可能不止一张（人工补建 / 重复提交）：全部交出去，由调用方决定，不挑第一张。"""
    return [o for o in orders if str(o.get("custom_order_sn")) == custom_sn]


def _live_item(it: dict) -> bool:
    return str(it.get("is_delete", 0)) in ("0", "False", "false")


def item_keys(items) -> list:
    """(sku, wid, units, price) 按 (sku, wid) 配对口径（v_check_po_landed 的无 lx_item_id 分支）；
    数量 / 价格不是数 → 原样保留成对不上，不抛。"""
    out = []
    for sku, wid, units, price in items:
        u = int(units) if str(units).lstrip("-").isdigit() else f"?{units!r}"
        try:
            pr = str(Decimal(str(price)).normalize())
        except (InvalidOperation, TypeError, ValueError):
            pr = f"?{price!r}"
        out.append((str(sku), None if wid is None else str(wid), u, pr))
    return sorted(out, key=repr)


def order_differences(po: dict, order: dict) -> dict:
    """领星的一张单与本采购单的差异；空 dict = 对得上。登记已有单与按幂等键查重共用这一把尺子。
    ★ 已作废（status -1）单独成一项：对得上也不能认。"""
    diff = {}
    if str(order.get("custom_order_sn")) != po["custom_order_sn"]:
        diff["custom_order_sn"] = {"ours": po["custom_order_sn"], "lingxing": order.get("custom_order_sn")}
    if str(order.get("supplier_id")) != str(po["supplier_id"]):
        diff["supplier_id"] = {"ours": po["supplier_id"], "lingxing": order.get("supplier_id")}
    if str(order.get("status")) == "-1":
        diff["status"] = {"ours": "有效", "lingxing": order.get("status")}
    ours = item_keys((ln["sku"], ln["wid"], ln["units"], ln["price"]) for ln in po["lines"])
    theirs = item_keys((it.get("sku"), it.get("wid"), it.get("quantity_real"), it.get("price"))
                       for it in order.get("item_list") or [] if _live_item(it))
    if ours != theirs:
        diff["items"] = {"ours": ours, "lingxing": theirs}
    return diff
