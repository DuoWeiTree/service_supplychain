"""FBA 排货单 → 领星 FBA 发货计划批次（spec 2026-10-13-dispatch-submit-fba-plan · CC-D1/D3）。★ 纯函数（L1）。

★ 一张排货单 × 来源仓 = 一个批次（同 CB-D5），参考号写批次 remark —— 领星无幂等键（`07` §3），靠它查重。
★ 我们的名字不用领星占用词 shipment_plan（CLAUDE.md 命名隔离）；`shipment_plan_quantity` 只出现在 payload 键上。
"""
from __future__ import annotations

import hashlib
import json

PACKING_TYPE = 2                  # 2 原厂；设计取值 · 未实测（CC-Q3）
SUBMITTABLE = ("已确认", "已投送")

#: shipmentPlanLists 计划 status（领星文档 FBA/ShipmentPlanLists）→ (领星进度, rank)；-5 驳回为旁路
_STAGES = {"-5": ("已驳回", -1), "0": ("待审核", 1), "5": ("待处理", 2), "10": ("已处理", 3)}
PROCESSED = "10"                  # 已处理 = 人已在领星推进（STA / 发货），取消不许再动（CC-D4）


def idem_key(consignment_id: int, from_wid: int) -> str:
    return f"SCM-FBA-{consignment_id}-{from_wid}"


def stage_of(status) -> tuple[str, int] | None:
    return None if status is None else _STAGES.get(str(status).strip())


def plan_orders(consignments: list[dict], fnsku: dict, packing: dict, ext: dict) -> dict:
    """→ {orders, blockers}。`fnsku`: (seller_sku, sid) → fnsku；`packing`: (seller_sku, sid, from_wid) → 装箱；
    `ext`: (consignment_id, from_wid) → 已建批次号。"""
    orders, blockers = [], []
    for c in sorted(consignments, key=lambda x: x["consignment_id"]):
        if c["channel"] != "fba" or c["our_stage"] not in SUBMITTABLE:
            continue
        for wid in sorted({ln["from_wid"] for ln in c["lines"]}):
            rows = []
            for ln in sorted((x for x in c["lines"] if x["from_wid"] == wid), key=lambda x: (x["seller_sku"], x["sid"])):
                fn = fnsku.get((ln["seller_sku"], ln["sid"]))
                box = packing.get((ln["seller_sku"], ln["sid"], wid)) or {}
                if not fn:
                    blockers.append({"code": "fnsku_missing", "seller_sku": ln["seller_sku"], "sid": ln["sid"]})
                if not box.get("box_count") or not box.get("units_per_box"):
                    blockers.append({"code": "packing_missing", "seller_sku": ln["seller_sku"], "sid": ln["sid"],
                                     "from_wid": wid})
                rows.append({"sid": int(ln["sid"]), "packing_type": PACKING_TYPE, "msku": ln["seller_sku"], "fnsku": fn,
                             "shipment_plan_quantity": ln["units"], "quantity_in_case": box.get("units_per_box"),
                             "box_num": box.get("box_count"), "wid": wid})
            orders.append({"key": f"{c['consignment_id']}-{wid}", "consignment_id": c["consignment_id"], "s_wid": wid,
                           "r_wid": None, "dest_sid": c["dest_sid"], "logistics_id": None, "channel_name": None,
                           "idem_key": idem_key(c["consignment_id"], wid), "product_list": rows,
                           "lines": [{k: ln[k] for k in ("seller_sku", "sid", "sku", "units")}
                                     for ln in c["lines"] if ln["from_wid"] == wid],
                           "ext_no": ext.get((c["consignment_id"], wid))})
    return {"orders": orders, "blockers": blockers, "skipped_fba": []}


def pending(plan: dict) -> list[dict]:
    return [o for o in plan["orders"] if o["ext_no"] is None]


def payload(order: dict, sheet_id: int) -> dict:
    """★ 批次 remark = 参考号（查重靠它）；排货表号进不了批次备注 —— 会破坏精确匹配。"""
    return {"remark": order["idem_key"], "product_list": order["product_list"]}


def confirm_token(plan: dict) -> str:
    content = sorted((o["idem_key"], json.dumps(o["product_list"], sort_keys=True)) for o in pending(plan))
    return hashlib.sha256(json.dumps(content, ensure_ascii=False).encode()).hexdigest()[:16]


def batch_stage(plans: list[dict]) -> tuple[str, int] | None:
    """一个批次的领星进度 = 最慢的那个计划；任一驳回 → 已驳回（旁路）；有认不出的状态 → None（不猜）。"""
    st = [stage_of(p.get("status")) for p in plans]
    if not st or any(x is None for x in st):
        return None
    bypass = [x for x in st if x[1] < 0]
    return bypass[0] if bypass else min(st, key=lambda x: x[1])
