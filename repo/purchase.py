"""采购单相关 SQL。★ 只收 cursor（G-2）；事务边界在 usecase/。"""
from __future__ import annotations

import json
import logging


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


def open_attention_of(cur, po_id: int, kind: str) -> dict | None:
    cur.execute("SELECT attention_id, opened_by_call, detail FROM po_attention"
                " WHERE po_id = %s AND kind = %s AND resolved_at IS NULL", (po_id, kind))
    r = cur.fetchone()
    return None if r is None else {"attention_id": r[0], "opened_by_call": r[1], "detail": r[2]}


def any_in_flight(cur, po_id: int, stale_minutes: int) -> bool:
    """★ 读调用也算：查重 / 核对在 HTTP 期间同样是 in_flight，此时不能下「确认无此单」的结论。
    但只算新鲜的读调用：超过 V5 的是进程死在半路留下的，由恢复收尾，不能永远挡住人工。
    不可逆调用不论新旧都算 —— 下单真在路上时确认「没建」恰好是错的。"""
    cur.execute("SELECT EXISTS (SELECT 1 FROM erp_call WHERE po_id = %s AND outcome = 'in_flight'"
                " AND (side_effect = 'irreversible' OR started_at >= now() - make_interval(mins => %s)))",
                (po_id, stale_minutes))
    return cur.fetchone()[0]


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
                " order_sn, state, placed_at, draft_version, created_by, is_void, discarded_at"
                " FROM v_po_effective WHERE po_id = %s", (po_id,))
    r = cur.fetchone()
    if r is None:
        return None
    keys = ("po_id", "supplier_id", "purchaser_id", "opt_uid", "qc_type", "wid", "custom_order_sn",
            "order_sn", "state", "placed_at", "draft_version", "created_by", "is_void",
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
    cur.execute("SELECT DISTINCT m.line_id, p.state FROM po_source_map m JOIN plan_line p USING (line_id)"
                " WHERE m.po_id = %s ORDER BY 1", (po_id,))
    po["source_line_states"] = [{"line_id": a, "state": b} for a, b in cur.fetchall()]
    return po


def supplier_exists(cur, supplier_id: str) -> bool:
    cur.execute("SELECT EXISTS (SELECT 1 FROM supplier WHERE supplier_id = %s)", (str(supplier_id),))
    return cur.fetchone()[0]


def list_pos(cur, state: str | None) -> list[dict]:
    cur.execute("SELECT e.po_id, e.custom_order_sn, e.supplier_id, s.name, e.state, e.order_sn, e.is_void,"
                " (SELECT count(*) FROM po_line l WHERE l.po_id = e.po_id),"
                " (SELECT coalesce(sum(units), 0) FROM po_line l WHERE l.po_id = e.po_id),"
                " (SELECT count(*) FROM po_attention a WHERE a.po_id = e.po_id AND a.resolved_at IS NULL),"
                " e.created_by, e.created_at"
                " FROM v_po_effective e JOIN supplier s USING (supplier_id)"
                " WHERE e.discarded_at IS NULL AND (%s::text IS NULL OR e.state = %s)"
                " ORDER BY e.po_id DESC", (state, state))
    keys = ("po_id", "custom_order_sn", "supplier_id", "supplier_name", "state", "order_sn", "is_void",
            "lines", "units", "open_attention", "created_by", "created_at")
    out = [dict(zip(keys, r)) for r in cur.fetchall()]
    for o in out:
        o["created_at"] = o["created_at"].isoformat()
    return out


def insert_call(cur, *, endpoint: str, side_effect: str, po_id: int | None, idem_key: str | None,
                actor: str, request: dict, retry_of: int | None = None) -> int:
    cur.execute("INSERT INTO erp_call (endpoint, side_effect, po_id, idem_key, actor, request, retry_of)"
                " VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s) RETURNING call_id",
                (endpoint, side_effect, po_id, idem_key, actor,
                 json.dumps(request, ensure_ascii=False, default=str), retry_of))
    return cur.fetchone()[0]


def mark_placed(cur, po_id: int, order_sn: str, actor: str, src: str) -> None:
    cur.execute("UPDATE po SET order_sn=%s, placed_at=now() WHERE po_id=%s", (order_sn, po_id))
    cur.execute("INSERT INTO po_event (po_id, from_state, to_state, actor, src)"
                " VALUES (%s, '草稿', '已下单', %s, %s)", (po_id, actor, src))


def open_attention_row(cur, po_id: int, kind: str, call_id: int | None, detail: dict) -> int | None:
    cur.execute("INSERT INTO po_attention (po_id, kind, opened_by_call, detail) VALUES (%s, %s, %s, %s::jsonb)"
                " ON CONFLICT (po_id, kind) WHERE resolved_at IS NULL DO NOTHING RETURNING attention_id",
                (po_id, kind, call_id, json.dumps(detail, ensure_ascii=False, default=str)))
    r = cur.fetchone()
    return r[0] if r else None


def resolve_attention(cur, po_id: int, kind: str, resolution: str, actor: str, reason: str | None) -> int:
    cur.execute("UPDATE po_attention SET resolved_at=now(), resolution=%s, resolved_by=%s, reason=%s"
                " WHERE po_id=%s AND kind=%s AND resolved_at IS NULL",
                (resolution, actor, reason, po_id, kind))
    return cur.rowcount


def last_create_call(cur, po_id: int) -> int | None:
    cur.execute("SELECT max(call_id) FROM erp_call WHERE po_id = %s AND endpoint = 'create_purchase_order'",
                (po_id,))
    return cur.fetchone()[0]


def ok_lookups_after(cur, po_id: int, after_call: int) -> list[tuple[int, dict | None]]:
    """本次 create 之后成功的查单调用及其响应（查重窗口与登记已有单的窄查询都算）。"""
    cur.execute("SELECT call_id, response FROM erp_call WHERE po_id = %s AND endpoint = 'purchase_order_list'"
                " AND outcome = 'ok' AND call_id > %s ORDER BY call_id", (po_id, after_call))
    return cur.fetchall()


def stuck_calls(cur, minutes: int, side_effect: str = "irreversible") -> list[tuple[int, int, str]]:
    cur.execute("SELECT call_id, po_id, actor FROM erp_call WHERE outcome='in_flight' AND side_effect=%s"
                " AND started_at < now() - make_interval(mins => %s) ORDER BY call_id", (side_effect, minutes))
    return cur.fetchall()


def stuck_call_rows(cur, minutes: int) -> list[dict]:
    """待处理队列用：卡住的不可逆调用（只读推导，不落表）。"""
    cur.execute("SELECT c.call_id, c.po_id, c.endpoint, c.actor, c.started_at, p.custom_order_sn, p.supplier_id"
                " FROM erp_call c JOIN po p USING (po_id)"
                " WHERE c.outcome='in_flight' AND c.side_effect='irreversible'"
                " AND c.started_at < now() - make_interval(mins => %s) ORDER BY c.started_at", (minutes,))
    keys = ("call_id", "po_id", "endpoint", "actor", "started_at", "custom_order_sn", "supplier_id")
    return [dict(zip(keys, r, strict=True)) for r in cur.fetchall()]


log = logging.getLogger("scm.repo.purchase")


def _lx_item_id(it: dict) -> str | None:
    """★ 领星响应里明细行号叫 `id`（刀0 实测 2026-10-08）；`item_id` 是 CH 来源观测的键（M3）。"""
    v = it.get("id")
    if v is None:
        v = it.get("item_id")
    return None if v is None else str(v)


def insert_item_obs(cur, order_sn: str, items: list[dict], source: str, call_id: int | None) -> int:
    n = 0
    for it in items:
        if str(it.get("is_delete", 0)) not in ("0", "False", "false"):
            continue
        cur.execute("INSERT INTO po_item_obs (order_sn, lx_item_id, sku, wid, quantity_real, price, source,"
                    " source_captured_at, call_id) VALUES (%s, %s, %s, %s, %s, %s, %s, now(), %s)",
                    (order_sn, _lx_item_id(it),
                     it["sku"], it.get("wid"), int(it["quantity_real"]), it.get("price"), source, call_id))
        n += 1
    return n


def backfill_item_ids(cur, po_id: int, items: list[dict]) -> int:
    n = skipped = 0
    for it in items:
        lx_id = _lx_item_id(it)
        if lx_id is None:
            skipped += 1
            continue
        cur.execute("UPDATE po_line SET lx_item_id=%s WHERE po_id=%s AND sku=%s AND wid IS NOT DISTINCT FROM %s"
                    " AND lx_item_id IS NULL", (lx_id, po_id, it["sku"], it.get("wid")))
        n += cur.rowcount
    if skipped:
        log.warning("op=backfill_item_ids po_id=%s skipped_no_id=%s of=%s", po_id, skipped, len(items))
    return n


def close_call_if_in_flight(cur, call_id: int, r) -> int:
    """★ 只收仍在 in_flight 的：别的路径已收尾则 rowcount=0，调用方跳过。"""
    cur.execute("UPDATE erp_call SET outcome=%s, http_status=%s, lx_code=%s, lx_msg=%s, cause=%s,"
                " response=%s::jsonb, host=%s, path=%s, elapsed_ms=%s, finished_at=now()"
                " WHERE call_id=%s AND outcome='in_flight'",
                (r.outcome, r.http_status, r.lx_code, r.lx_msg, r.cause,
                 json.dumps(r.body, ensure_ascii=False, default=str) if r.body is not None else None,
                 r.host, r.path, r.elapsed_ms, call_id))
    return cur.rowcount


def get_call(cur, call_id: int) -> dict | None:
    cur.execute("SELECT call_id, po_id, endpoint, outcome, cause, started_at, finished_at"
                " FROM erp_call WHERE call_id = %s", (call_id,))
    r = cur.fetchone()
    return None if r is None else dict(zip(("call_id", "po_id", "endpoint", "outcome", "cause", "started_at",
                                            "finished_at"), r, strict=True))


def latest_create_call(cur, po_id: int) -> dict | None:
    cid = last_create_call(cur, po_id)
    return None if cid is None else get_call(cur, cid)


def confirmed_absent_after(cur, po_id: int, finished_at) -> bool:
    cur.execute("SELECT EXISTS (SELECT 1 FROM po_attention WHERE po_id = %s AND kind = 'needs_verify'"
                " AND resolution = 'confirmed_absent' AND resolved_at > %s)", (po_id, finished_at))
    return cur.fetchone()[0]


def unsettled_drafts(cur) -> list[tuple[int, int, str]]:
    """草稿的最近一次 create 是 ok / unknown，既没有未处理的待核实、也没有之后的「确认没建」。"""
    cur.execute(
        "SELECT c.call_id, c.po_id, c.actor FROM erp_call c"
        " JOIN po p ON p.po_id = c.po_id AND p.state = '草稿' AND p.discarded_at IS NULL"
        " WHERE c.endpoint = 'create_purchase_order' AND c.outcome IN ('ok', 'unknown')"
        " AND c.call_id = (SELECT max(x.call_id) FROM erp_call x WHERE x.po_id = c.po_id"
        "                  AND x.endpoint = 'create_purchase_order')"
        " AND NOT EXISTS (SELECT 1 FROM po_attention a WHERE a.po_id = c.po_id AND a.kind = 'needs_verify'"
        "                 AND (a.resolved_at IS NULL OR (a.resolution = 'confirmed_absent'"
        "                                                AND a.resolved_at > c.finished_at)))"
        " ORDER BY c.call_id")
    return cur.fetchall()
