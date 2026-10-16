"""排货单 → 领星海外仓备货单（spec 2026-10-12 §3）。★ 纯函数（L1）。

★ 领星一张备货单只有一个发货仓 s_wid ⇒ 一张海外仓排货单按来源仓拆成 N 张（CB-D5）。
★ 建单形状来自 CB-D4 实调：status 40 · logistics_list_type 1 · head_logistics_list {tax_fee_type: 3}。
"""
from __future__ import annotations

import hashlib
import json

STATUS_PENDING_SHIP = 40          # CB-D2：40 待发货（领星默认 60 已完成，不用）
TAX_FEE_TYPE = 3                  # 3 = 按产品数量分摊；设计取值 · 未实测（CB-Q5）
SUBMITTABLE = ("已确认", "已投送")


def idem_key(consignment_id: int, from_wid: int) -> str:
    return f"SCM-CN-{consignment_id}-{from_wid}"


def plan_orders(consignments: list[dict], logistics: dict, products: dict, fnsku: dict, ext: dict) -> dict:
    """→ {orders: [...], blockers: [...], skipped_fba: [...]}。
    `products`: sku → product_id（None / 缺 = 镜像没有）；`fnsku`: (seller_sku, sid) → fnsku；
    `ext`: (consignment_id, from_wid) → 已建的领星单号。"""
    orders, blockers, skipped_fba = [], [], []
    missing_pid, missing_lg = set(), {}
    for c in sorted(consignments, key=lambda x: x["consignment_id"]):
        if c["our_stage"] not in SUBMITTABLE:
            continue
        if c["channel"] != "oversea":
            skipped_fba.append(c["consignment_id"])      # FBA 走 STA（C-c）
            continue
        lg = logistics.get(c["dest_wid"])
        if lg is None:
            missing_lg[c["dest_wid"]] = {"code": "logistics_missing", "dest_wid": c["dest_wid"]}
        elif not lg.get("enabled"):
            missing_lg[c["dest_wid"]] = {"code": "logistics_disabled", "dest_wid": c["dest_wid"],
                                         "logistics_id": lg["logistics_id"]}
        for wid in sorted({ln["from_wid"] for ln in c["lines"]}):
            lines = [ln for ln in c["lines"] if ln["from_wid"] == wid]
            plist: dict[tuple, dict] = {}
            skus_of: dict[int, str] = {}
            for ln in lines:
                pid = products.get(ln["sku"])
                if pid is None:
                    missing_pid.add(ln["sku"])
                    continue
                skus_of[pid] = ln["sku"]
                fn = fnsku.get((ln["seller_sku"], ln["sid"]))
                key = (pid, fn, ln["sid"]) if fn else (pid, None, None)     # ★ CB-D7
                row = plist.setdefault(key, {"product_id": pid, "stock_num": 0, "receive_num": 0,
                                             **({"fnsku": fn, "sid": ln["sid"]} if fn else {})})
                row["stock_num"] += ln["units"]
            orders.append({
                "key": f"{c['consignment_id']}-{wid}", "consignment_id": c["consignment_id"], "s_wid": wid,
                "r_wid": c["dest_wid"], "logistics_id": lg["logistics_id"] if lg else None,
                "channel_name": lg.get("channel_name") if lg else None,
                "idem_key": idem_key(c["consignment_id"], wid),
                "product_list": sorted(plist.values(), key=lambda r: (r["product_id"], r.get("fnsku") is None,
                                                                      r.get("fnsku") or "")),
                "lines": [{k: ln[k] for k in ("seller_sku", "sid", "sku", "units")} for ln in lines],
                "product_skus": skus_of,           # 只给点名用，不进 payload
                "ext_no": ext.get((c["consignment_id"], wid)),
            })
    blockers += [{"code": "product_id_missing", "sku": s} for s in sorted(missing_pid)]
    blockers += [missing_lg[w] for w in sorted(missing_lg)]
    return {"orders": orders, "blockers": blockers, "skipped_fba": skipped_fba}


def apply_sku_codes(plan: dict, codes: dict[int, dict[int, list[str]] | None]) -> None:
    """有 API 的三方海外仓：每个产品行带上三方 sku 编码 `oversea_product_code`（领星文档 CreateInbound：
    「当仓库是有 api 海外仓必填」；10-12 实调没带 → 拒「第三方sku不能为空」）。
    `codes`：目的仓 wid → {product_id: [已配对的三方编码]}；None = 配对读不到；不在 dict 里 = 不是 API 仓，不查不带。
    ★ 没配对 / 多个编码 / 读不到一律成阻断项点名，不猜编码（编码常 ≠ 货号，如 DCC1041224AL → DCC1041224AL-1）。"""
    seen: set = set()
    for o in plan["orders"]:
        if o["r_wid"] not in codes or o["ext_no"] is not None:     # 已建的单不再要求
            continue
        m = codes[o["r_wid"]]
        if m is None:
            if ("unavail", o["r_wid"]) not in seen:
                plan["blockers"].append({"code": "sku_match_unavailable", "dest_wid": o["r_wid"]})
                seen.add(("unavail", o["r_wid"]))
            continue
        for row in o["product_list"]:
            pid = row["product_id"]
            got = sorted(set(m.get(pid) or []))
            if len(got) == 1:
                row["oversea_product_code"] = got[0]
                continue
            key = (o["r_wid"], pid)
            if key in seen:
                continue
            seen.add(key)
            b = {"code": "third_party_sku_missing" if not got else "third_party_sku_ambiguous", "dest_wid": o["r_wid"],
                 "sku": o.get("product_skus", {}).get(pid), "product_id": pid}
            plan["blockers"].append({**b, "codes": got} if got else b)


def pending(plan: dict) -> list[dict]:
    """还没建的（断点续跑：已拿到单号的跳过，出站 #3）。"""
    return [o for o in plan["orders"] if o["ext_no"] is None]


def payload(order: dict, sheet_id: int) -> dict:
    return {"inbound_order_no": order["idem_key"], "s_wid": order["s_wid"], "r_wid": order["r_wid"],
            "logistics_id": order["logistics_id"], "status": STATUS_PENDING_SHIP,
            "logistics_list_type": 1, "head_logistics_list": {"tax_fee_type": TAX_FEE_TYPE},
            "remark": f"scm 排货表 #{sheet_id} 排货单 #{order['consignment_id']}",
            "product_list": order["product_list"]}


def confirm_token(plan: dict) -> str:
    """确认框里看到的待建内容的摘要（同采购单 H-1：派生，不存储）。内容一变，旧 token 就对不上。"""
    content = sorted((o["idem_key"], o["logistics_id"], json.dumps(o["product_list"], sort_keys=True))
                     for o in pending(plan))
    return hashlib.sha256(json.dumps(content, ensure_ascii=False).encode()).hexdigest()[:16]
