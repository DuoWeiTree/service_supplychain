"""排货派生与六项确认闸（`15` §5 / §6 · spec 2026-10-10 §5）。★ 纯函数，离线可测（L1）。

★ 派生规则无判断余地（N-3）：一张排货单 = 一个 (渠道, 目的地)；渠道与目的地取自格子本身（CA-D5）。
★ 需求侧与物理侧不合成（`04` §1.2.2）：本模块只拿「已承诺」比「桶」，不拿待排量减库存。
"""
from __future__ import annotations

SHARED = "shared"
BOX_FIELDS = ("box_count", "box_len", "box_wid", "box_hei", "box_weight", "units_per_box")


def dest_key(cell: dict) -> tuple:
    return ("fba", cell["dest_sid"]) if cell["channel"] == "fba" else ("oversea", cell["dest_wid"])


def line_key(cell: dict) -> tuple:
    return (cell["seller_sku"], cell["sid"], cell["from_wid"])


def derive(cells: list[dict]) -> dict:
    """格子 → {dest_key: {line_key: {"sku","units","planned","unplanned"}}}。
    ★ 安排 → 排货行是 N:1（同 msku×店×来源仓 的多条记录 / 无计划来源合一行，`15` §2.2）。"""
    out: dict = {}
    for c in cells:
        if not c.get("units"):
            continue
        line = out.setdefault(dest_key(c), {}).setdefault(
            line_key(c), {"sku": c["sku"], "units": 0, "planned": 0, "unplanned": 0})
        line["units"] += c["units"]
        line["planned" if c.get("line_id") is not None else "unplanned"] += c["units"]
    return out


def diff(old_keys: set, new: dict, packing_keys: set) -> dict:
    """★ 装箱对不上新键的逐项点名（`15` §4「不许冲掉已填的装箱；对不上键的逐项点名」）。"""
    fba_lines = {lk for dk, lines in new.items() if dk[0] == "fba" for lk in lines}
    return {"created": sorted(set(new) - old_keys, key=repr),
            "removed": sorted(old_keys - set(new), key=repr),
            "kept": sorted(old_keys & set(new), key=repr),
            "orphan_packing": sorted(packing_keys - fba_lines, key=repr)}


def _overflow(need_by_sid: dict, buckets: dict) -> int:
    return sum(max(0, n - buckets.get(s, 0)) for s, n in need_by_sid.items())


def bucket_feasible(need_by_sid: dict, buckets: dict) -> tuple[bool, int]:
    """CA-D8：店 X 先用本店桶，不够的才吃共享桶；别店的桶不可用。返回 (可行, 溢出到共享桶的量)。"""
    over = _overflow(need_by_sid, buckets)
    return over <= buckets.get(SHARED, 0), over


def available_for(sid: str, need_by_sid: dict, buckets: dict) -> int:
    """店 sid 在这个 货号×仓 还能再承诺多少。★ 负数 = 已经超了，照实给，不截成 0。"""
    own_left = max(0, buckets.get(sid, 0) - need_by_sid.get(sid, 0))
    return own_left + buckets.get(SHARED, 0) - _overflow(need_by_sid, buckets)


def _gate(gate: str, ok, code: str, detail, fix: str) -> dict:
    return {"gate": gate, "ok": ok, "code": code, "detail": detail, "fix": fix}


def gates(*, consignments: list[dict], packing: dict, stock: dict | None, stock_error: dict | None,
          committed: dict, ledger_conflicts: list, dest_kinds: dict, fba_sids: set,
          logistics: dict) -> list[dict]:
    """★ 永远返回 G1~G7 全表（`10` 原则五：通过的也列出来，每条带「谁能修」）。
    `ok`：True 过 · False 不过 · None 无法判定 / 未检查（★ 不当成过）。"""
    g1 = []
    for c in consignments:
        if c["channel"] != "fba":
            continue        # ★ 海外仓不在此填装箱（`10` §4）
        for ln in c["lines"]:
            k = (ln["seller_sku"], ln["sid"], ln["from_wid"])
            box = packing.get(k) or {}
            missing = [f for f in BOX_FIELDS if box.get(f) is None]
            if missing:
                g1.append({"seller_sku": k[0], "sid": k[1], "from_wid": k[2], "missing": missing})

    g2 = [c["consignment_id"] for c in consignments if c["channel"] == "fba" and not c.get("dest_sid")]

    touched = {(ln["sku"], ln["from_wid"]) for c in consignments for ln in c["lines"]}
    if stock is None:
        g3 = _gate("G3", None, "insufficient_available", stock_error or {}, "本地库存读不到：恢复后再确认")
    else:
        short = []
        for sku, wid in sorted(touched):
            need = committed.get((sku, wid), {})
            buckets = stock.get((sku, wid), {SHARED: 0})   # ★ 被接受的快照里没有这行 = 0
            ok, over = bucket_feasible(need, buckets)
            if not ok:
                short.append({"sku": sku, "from_wid": wid, "need": need, "buckets": buckets,
                              "short": over - buckets.get(SHARED, 0)})
        g3 = _gate("G3", not short, "insufficient_available", short, "人：回网格减少安排或换来源仓")

    g6 = []
    for c in consignments:
        if c["channel"] == "oversea":
            if not str(dest_kinds.get(c["dest_wid"], "")).startswith("oversea_"):
                g6.append({"consignment_id": c["consignment_id"], "dest_wid": c["dest_wid"]})
        elif c.get("dest_sid") and c["dest_sid"] not in fba_sids:
            g6.append({"consignment_id": c["consignment_id"], "dest_sid": c["dest_sid"]})

    # G7（CB-D3）：每张海外仓排货单选了物流方式，且渠道仍启用
    g7 = []
    for c in consignments:
        if c["channel"] != "oversea":
            continue
        lg = logistics.get(c["dest_wid"])
        if lg is None:
            g7.append({"dest_wid": c["dest_wid"]})
        elif not lg.get("enabled"):
            g7.append({"dest_wid": c["dest_wid"], "logistics_id": lg["logistics_id"], "disabled": True})

    return [
        _gate("G1", not g1, "packing_incomplete", g1, "人：在排货单上填装箱"),
        _gate("G2", not g2, "dest_incomplete", g2, "系统：由店铺推出；仍缺则回网格重选目的地"),
        g3,
        _gate("G4", not ledger_conflicts, "ledger_moved", ledger_conflicts,
              "人：刷新后核对别的排货表的安排再确认"),
        _gate("G5", None, "unlinked_shipments_pending", "待裁定 · 未检查（CA-Q9）", "—"),
        _gate("G6", bool(consignments) and not g6, "invalid_destination",
              g6 if consignments else "没有任何排货单", "人：回网格改目的地"),
        _gate("G7", not g7, "logistics_missing", g7, "人：在海外仓排货单上选物流方式"),
    ]


#: ★ CA-D13：每个去向只列适用于它的闸（不适用 ≠ 过，所以不列）
ROUTE_GATES = {"fba": ("G1", "G2", "G3", "G4", "G5", "G6"), "oversea": ("G3", "G4", "G5", "G6", "G7")}


def gates_for(route: str | None, gates: list[dict]) -> list[dict]:
    """route 为空（旧版混合表）→ 全列。"""
    keep = ROUTE_GATES.get(route) if route else None
    return gates if keep is None else [g for g in gates if g["gate"] in keep]


def confirmable(gs: list[dict]) -> bool:
    """★ G5 在 C-a 是「未检查」（CA-Q9 暂行：不拦也不当过）；其余必须明确为 True。"""
    return all(g["ok"] is True for g in gs if g["gate"] != "G5")
