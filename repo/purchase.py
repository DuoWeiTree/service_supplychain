"""采购单相关 SQL。★ 只收 cursor（G-2）；事务边界在 usecase/。"""
from __future__ import annotations


def lock_plan_lines(cur, line_ids: list[int]) -> dict[int, dict]:
    """★ 按 line_id 升序加锁，并发组单时同序取锁不成环。
    NO KEY UPDATE 而非 UPDATE：012 的提交检查同用此档，且不与 po_source_map 外键的 KEY SHARE 冲突。"""
    cur.execute("SELECT line_id, state, total_units, demand_by_seller FROM plan_line"
                " WHERE line_id = ANY(%s) ORDER BY line_id FOR NO KEY UPDATE", (sorted(set(line_ids)),))
    return {r[0]: {"line_id": r[0], "state": r[1], "total_units": r[2], "demand_by_seller": r[3]}
            for r in cur.fetchall()}


def seller_used(cur, line_ids: list[int], exclude_po: int | None) -> dict[tuple[int, str], int]:
    cur.execute(
        "SELECT m.line_id, m.seller_id, sum(m.units)::int FROM po_source_map m"
        " JOIN v_po_effective e ON e.po_id = m.po_id AND NOT e.is_void"
        " WHERE m.line_id = ANY(%s) AND m.po_id IS DISTINCT FROM %s GROUP BY 1, 2",
        (line_ids, exclude_po))
    return {(r[0], r[1]): r[2] for r in cur.fetchall()}


def line_qty(cur, line_ids: list[int]) -> dict[int, dict]:
    cur.execute("SELECT line_id, total_units, state, confirmed_units, ordered_units"
                " FROM v_plan_line_po_qty WHERE line_id = ANY(%s)", (line_ids,))
    return {r[0]: {"total": r[1], "state": r[2], "confirmed": r[3], "ordered": r[4]}
            for r in cur.fetchall()}


def insert_line_event(cur, line_id: int, frm: str, to: str, actor: str, src: str) -> None:
    cur.execute("INSERT INTO plan_line_event (line_id, from_state, to_state, actor, src)"
                " VALUES (%s, %s, %s, %s, %s)", (line_id, frm, to, actor, src))


def insert_po(cur, h: dict, actor: str) -> int:
    cur.execute("INSERT INTO po (supplier_id, purchaser_id, opt_uid, qc_type, wid, created_by)"
                " VALUES (%s, %s, %s, %s, %s, %s) RETURNING po_id",
                (h["supplier_id"], h["purchaser_id"], h["opt_uid"], h["qc_type"], h.get("wid"), actor))
    return cur.fetchone()[0]


def update_po_header(cur, po_id: int, h: dict) -> int:
    cur.execute("UPDATE po SET supplier_id=%s, purchaser_id=%s, opt_uid=%s, qc_type=%s, wid=%s,"
                " draft_version = draft_version + 1, updated_at = now()"
                " WHERE po_id = %s RETURNING draft_version",
                (h["supplier_id"], h["purchaser_id"], h["opt_uid"], h["qc_type"], h.get("wid"), po_id))
    return cur.fetchone()[0]


def lock_po(cur, po_id: int) -> dict | None:
    cur.execute("SELECT po_id, state, draft_version, order_sn, discarded_at FROM po WHERE po_id = %s FOR UPDATE",
                (po_id,))
    r = cur.fetchone()
    return None if r is None else {"po_id": r[0], "state": r[1], "draft_version": r[2], "order_sn": r[3],
                                   "discarded": r[4] is not None}


def has_calls(cur, po_id: int) -> bool:
    cur.execute("SELECT EXISTS (SELECT 1 FROM erp_call WHERE po_id = %s)", (po_id,))
    return cur.fetchone()[0]


def discard_po(cur, po_id: int, actor: str) -> None:
    """★ 试过下单的草稿：清明细（映射随 CASCADE 走、额度释放），单头与 erp_call 留档。"""
    cur.execute("DELETE FROM po_line WHERE po_id = %s", (po_id,))
    cur.execute("UPDATE po SET discarded_at = now(), discarded_by = %s, draft_version = draft_version + 1,"
                " updated_at = now() WHERE po_id = %s", (actor, po_id))


def open_attention(cur, po_id: int) -> list[dict]:
    cur.execute("SELECT attention_id, kind, opened_at FROM po_attention"
                " WHERE po_id = %s AND resolved_at IS NULL ORDER BY attention_id", (po_id,))
    return [{"attention_id": r[0], "kind": r[1], "opened_at": r[2].isoformat()} for r in cur.fetchall()]


def in_flight_call(cur, po_id: int) -> int | None:
    cur.execute("SELECT call_id FROM erp_call WHERE po_id = %s AND outcome = 'in_flight'"
                " AND side_effect = 'irreversible'", (po_id,))
    r = cur.fetchone()
    return r[0] if r else None


def source_line_ids(cur, po_id: int) -> list[int]:
    cur.execute("SELECT DISTINCT line_id FROM po_source_map WHERE po_id = %s", (po_id,))
    return [r[0] for r in cur.fetchall()]


def replace_lines(cur, po_id: int, lines: list[dict]) -> None:
    cur.execute("DELETE FROM po_line WHERE po_id = %s", (po_id,))   # 映射随 CASCADE 走
    for n, ln in enumerate(lines, start=1):
        cur.execute("INSERT INTO po_line (po_id, line_no, sku, wid, units, price, expect_arrive_date,"
                    " unplanned_reason) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (po_id, n, ln["sku"], ln["wid"], ln["units"], ln["price"],
                     ln.get("expect_arrive_date"), (ln.get("unplanned_reason") or None)))
        for s in ln["sources"]:
            cur.execute("INSERT INTO po_source_map VALUES (%s, %s, %s, %s, %s)",
                        (po_id, n, s["line_id"], s["seller_id"], s["units"]))


def delete_po(cur, po_id: int) -> None:
    cur.execute("DELETE FROM po WHERE po_id = %s", (po_id,))


def read_po(cur, po_id: int) -> dict | None:
    cur.execute("SELECT po_id, supplier_id, purchaser_id, opt_uid, qc_type, wid, custom_order_sn,"
                " order_sn, state, placed_mode, placed_at, draft_version, created_by, is_void, discarded_at"
                " FROM v_po_effective WHERE po_id = %s", (po_id,))
    r = cur.fetchone()
    if r is None:
        return None
    keys = ("po_id", "supplier_id", "purchaser_id", "opt_uid", "qc_type", "wid", "custom_order_sn",
            "order_sn", "state", "placed_mode", "placed_at", "draft_version", "created_by", "is_void",
            "discarded_at")
    po = dict(zip(keys, r))
    for k in ("placed_at", "discarded_at"):
        po[k] = po[k].isoformat() if po[k] else None
    cur.execute("SELECT line_no, sku, wid, units, price::text, expect_arrive_date, unplanned_reason,"
                " lx_item_id FROM po_line WHERE po_id = %s ORDER BY line_no", (po_id,))
    po["lines"] = [{"line_no": a, "sku": b, "wid": c, "units": d, "price": e,
                    "expect_arrive_date": f.isoformat() if f else None,
                    "unplanned_reason": g, "lx_item_id": h, "sources": []}
                   for a, b, c, d, e, f, g, h in cur.fetchall()]
    cur.execute("SELECT line_no, line_id, seller_id, units FROM po_source_map WHERE po_id = %s"
                " ORDER BY line_no, line_id, seller_id", (po_id,))
    by_no = {ln["line_no"]: ln for ln in po["lines"]}
    for no, lid, sid, u in cur.fetchall():
        by_no[no]["sources"].append({"line_id": lid, "seller_id": sid, "units": u})
    for ln in po["lines"]:
        ln["sources_units"] = sum(s["units"] for s in ln["sources"])
        ln["unplanned_units"] = ln["units"] - ln["sources_units"]
    return po


def list_pos(cur, state: str | None) -> list[dict]:
    cur.execute("SELECT e.po_id, e.supplier_id, s.name, e.state, e.order_sn, e.placed_mode, e.is_void,"
                " (SELECT count(*) FROM po_line l WHERE l.po_id = e.po_id),"
                " (SELECT coalesce(sum(units), 0) FROM po_line l WHERE l.po_id = e.po_id),"
                " (SELECT count(*) FROM po_attention a WHERE a.po_id = e.po_id AND a.resolved_at IS NULL),"
                " e.created_by, e.created_at"
                " FROM v_po_effective e JOIN supplier s USING (supplier_id)"
                " WHERE e.discarded_at IS NULL AND (%s::text IS NULL OR e.state = %s)"
                " ORDER BY e.po_id DESC", (state, state))
    keys = ("po_id", "supplier_id", "supplier_name", "state", "order_sn", "placed_mode", "is_void",
            "lines", "units", "open_attention", "created_by", "created_at")
    out = [dict(zip(keys, r)) for r in cur.fetchall()]
    for o in out:
        o["created_at"] = o["created_at"].isoformat()
    return out
