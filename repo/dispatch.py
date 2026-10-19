"""排货表相关 SQL。★ 只收 cursor（G-2）；事务边界在 usecase/。"""
from __future__ import annotations

import json

CELL_COLS = ("arrangement_id", "line_id", "sku", "seller_sku", "sid", "from_wid", "channel",
             "dest_wid", "dest_sid", "units")
PACKING_COLS = ("box_count", "box_len", "box_wid", "box_hei", "box_weight", "units_per_box")


def insert_sheet(cur, actor: str, plan_id: int, route: str) -> int:
    cur.execute("INSERT INTO sheet (created_by, plan_id, route) VALUES (%s, %s, %s) RETURNING sheet_id",
                (actor, plan_id, route))
    return cur.fetchone()[0]


def draft_of(cur, plan_id: int, route: str) -> int | None:
    cur.execute("SELECT sheet_id FROM sheet WHERE plan_id=%s AND route=%s AND state='草稿'", (plan_id, route))
    r = cur.fetchone()
    return None if r is None else r[0]


def read_plan(cur, plan_id: int) -> dict | None:
    cur.execute("SELECT plan_id, title, archived_at FROM plan WHERE plan_id = %s", (plan_id,))
    r = cur.fetchone()
    return None if r is None else {"plan_id": r[0], "title": r[1], "archived_at": r[2]}


#: ★ CA-D10：未撤销 / 未完结的记录都算「活的」，能排；上限按量
LIVE_LINE = "l.state NOT IN ('已撤销','已完结')"


def dispatch_plans(cur) -> list[dict]:
    """CA-D11 入口：未归档、还有活记录的计划，带量的汇总（需求侧，PG 内可得的部分）。"""
    cur.execute(
        "SELECT p.plan_id, p.title, max(l.rev), count(*)::int, sum(l.total_units)::int,"
        "       sum(po.confirmed_units)::int, sum(po.ordered_units)::int,"
        "       sum(d.staged_units)::int, sum(d.dispatched_units)::int, sum(d.pending_units)::int"
        "  FROM plan p JOIN plan_line l USING (plan_id)"
        "  JOIN v_plan_line_po_qty po USING (line_id) JOIN v_plan_line_dispatch_qty d USING (line_id)"
        f" WHERE p.archived_at IS NULL AND {LIVE_LINE}"
        " GROUP BY p.plan_id, p.title ORDER BY p.plan_id DESC")
    cols = ("plan_id", "title", "rev", "lines", "total", "confirmed", "ordered", "staged", "dispatched", "pending")
    plans = [dict(zip(cols, r)) for r in cur.fetchall()]
    cur.execute("SELECT plan_id, sheet_id, state, route FROM sheet WHERE plan_id = ANY(%s) AND state <> '已取消'"
                " ORDER BY sheet_id", ([p["plan_id"] for p in plans],))
    sheets: dict = {}
    for pid, sid, st, route in cur.fetchall():
        sheets.setdefault(pid, []).append({"sheet_id": sid, "state": st, "route": route})
    # 每个产品（活记录）一行：/dispatch 要回答「哪个产品还没排完」，不只是计划的合计
    cur.execute(
        "SELECT l.plan_id, l.line_id, l.sku, k.name, l.period_start, l.state, d.total_units, d.staged_units,"
        "       d.dispatched_units, d.pending_units, po.confirmed_units, po.ordered_units"
        "  FROM plan_line l JOIN v_plan_line_dispatch_qty d USING (line_id)"
        "  JOIN v_plan_line_po_qty po USING (line_id) LEFT JOIN sku_catalog k USING (sku)"
        f" WHERE l.plan_id = ANY(%s) AND {LIVE_LINE} ORDER BY l.plan_id, d.pending_units DESC, l.sku, l.line_id",
        ([p["plan_id"] for p in plans],))
    rcols = ("line_id", "sku", "sku_name", "period", "state", "total", "staged", "dispatched", "pending",
             "confirmed", "ordered")
    records: dict = {}
    for pid, *rest in cur.fetchall():
        r = dict(zip(rcols, rest))
        records.setdefault(pid, []).append({**r, "period": r["period"].isoformat()})
    return [{**p, "sheets": sheets.get(p["plan_id"], []), "records": records.get(p["plan_id"], [])} for p in plans]


def lock_sheet(cur, sheet_id: int) -> dict | None:
    cur.execute("SELECT sheet_id, state, watermark_at_build, watermark_at_confirm, created_by, created_at, plan_id,"
                " route FROM sheet WHERE sheet_id = %s FOR UPDATE", (sheet_id,))
    r = cur.fetchone()
    return None if r is None else dict(zip(
        ("sheet_id", "state", "watermark_at_build", "watermark_at_confirm", "created_by", "created_at", "plan_id",
         "route"), r))


def read_sheet(cur, sheet_id: int) -> dict | None:
    cur.execute("SELECT s.sheet_id, s.state, s.watermark_at_build, s.watermark_at_confirm, s.created_by, s.created_at,"
                " (SELECT last_value FROM ledger_seq), s.plan_id, p.title, s.route"
                " FROM sheet s LEFT JOIN plan p USING (plan_id) WHERE s.sheet_id = %s", (sheet_id,))
    r = cur.fetchone()
    return None if r is None else dict(zip(
        ("sheet_id", "state", "watermark_at_build", "watermark_at_confirm", "created_by", "created_at",
         "ledger_now", "plan_id", "plan_title", "route"), r))


def list_sheets(cur, state: str | None) -> list[dict]:
    cur.execute(
        "SELECT s.sheet_id, s.state, s.created_by, s.created_at, s.plan_id, max(p.title), s.route,"
        "       count(DISTINCT c.consignment_id) FILTER (WHERE c.our_stage NOT IN ('已放弃','已取消'))::int,"
        "       coalesce(sum(l.units) FILTER (WHERE c.our_stage NOT IN ('已放弃','已取消')), 0)::int"
        "  FROM sheet s LEFT JOIN plan p ON p.plan_id = s.plan_id LEFT JOIN consignment c USING (sheet_id)"
        "  LEFT JOIN consignment_line l ON l.consignment_id = c.consignment_id"
        " WHERE %s::text IS NULL OR s.state = %s"
        " GROUP BY s.sheet_id ORDER BY s.sheet_id DESC", (state, state))
    return [dict(zip(("sheet_id", "state", "created_by", "created_at", "plan_id", "plan_title", "route",
                      "consignments", "units"), r))
            for r in cur.fetchall()]


def lock_plan_line(cur, line_id: int) -> dict | None:
    """★ NO KEY UPDATE：与 016 assert_line_dispatch_qty 同档，按记录串行化并发安排。"""
    cur.execute("SELECT line_id, sku, state, total_units, demand_by_seller, plan_id FROM plan_line"
                " WHERE line_id = %s FOR NO KEY UPDATE", (line_id,))
    r = cur.fetchone()
    return None if r is None else dict(zip(("line_id", "sku", "state", "total_units", "demand_by_seller",
                                            "plan_id"), r))


def line_used_excluding(cur, line_id: int, exclude_arrangement: int | None) -> int:
    cur.execute(
        "SELECT coalesce(sum(m.units), 0)::int FROM dispatch_source_map m"
        "  JOIN dispatch_arrangement a USING (arrangement_id)"
        "  JOIN consignment c ON c.consignment_id = m.consignment_id"
        " WHERE a.line_id = %s AND c.our_stage IN ('草稿','已确认','已投送','已归档')"
        "   AND a.arrangement_id IS DISTINCT FROM %s", (line_id, exclude_arrangement))
    return cur.fetchone()[0]


def bridge_sku(cur, seller_sku: str, sid: str) -> str | None:
    cur.execute("SELECT sku FROM msku_bridge WHERE seller_sku = %s AND sid = %s", (seller_sku, sid))
    r = cur.fetchone()
    return None if r is None else r[0]


def warehouse_kinds(cur) -> dict[int, dict]:
    cur.execute("SELECT wid, name, kind, market FROM warehouse ORDER BY wid")
    return {r[0]: {"wid": r[0], "name": r[1], "kind": r[2], "market": r[3]} for r in cur.fetchall()}


def sellers(cur) -> dict[str, dict]:
    cur.execute("SELECT seller_id, name, market, has_fba FROM seller ORDER BY seller_id")
    return {r[0]: {"sid": r[0], "name": r[1], "market": r[2], "has_fba": r[3]} for r in cur.fetchall()}


def find_cell(cur, sheet_id: int, line_id, seller_sku, sid, from_wid, channel, dest_wid, dest_sid) -> dict | None:
    cur.execute(
        f"SELECT {', '.join(CELL_COLS)} FROM dispatch_arrangement"
        " WHERE sheet_id = %s AND line_id IS NOT DISTINCT FROM %s AND seller_sku = %s AND sid = %s"
        "   AND from_wid = %s AND channel = %s AND dest_wid IS NOT DISTINCT FROM %s"
        "   AND dest_sid IS NOT DISTINCT FROM %s",
        (sheet_id, line_id, seller_sku, sid, from_wid, channel, dest_wid, dest_sid))
    r = cur.fetchone()
    return None if r is None else dict(zip(CELL_COLS, r))


def upsert_cell(cur, existing: dict | None, sheet_id: int, cell: dict, actor: str) -> None:
    if existing is not None:
        cur.execute("UPDATE dispatch_arrangement SET units = %s, ledger_seq = nextval('ledger_seq'),"
                    " updated_by = %s, updated_at = now() WHERE arrangement_id = %s",
                    (cell["units"], actor, existing["arrangement_id"]))
        return
    cur.execute(
        "INSERT INTO dispatch_arrangement (sheet_id, line_id, sku, seller_sku, sid, from_wid, channel,"
        " dest_wid, dest_sid, units, updated_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (sheet_id, cell["line_id"], cell["sku"], cell["seller_sku"], cell["sid"], cell["from_wid"],
         cell["channel"], cell["dest_wid"], cell["dest_sid"], cell["units"], actor))


def delete_cell(cur, arrangement_id: int) -> None:
    cur.execute("DELETE FROM dispatch_arrangement WHERE arrangement_id = %s", (arrangement_id,))


def sheet_cells(cur, sheet_id: int) -> list[dict]:
    cur.execute(f"SELECT {', '.join(CELL_COLS)} FROM dispatch_arrangement WHERE sheet_id = %s"
                " ORDER BY arrangement_id", (sheet_id,))
    return [dict(zip(CELL_COLS, r)) for r in cur.fetchall()]


def consignment_keys(cur, sheet_id: int) -> dict[tuple, int]:
    cur.execute("SELECT consignment_id, channel, dest_wid, dest_sid FROM consignment"
                " WHERE sheet_id = %s AND our_stage = '草稿'", (sheet_id,))
    return {(("fba", r[3]) if r[1] == "fba" else ("oversea", r[2])): r[0] for r in cur.fetchall()}


def has_outbound_trace(cur, consignment_id: int) -> bool:
    cur.execute("SELECT EXISTS (SELECT 1 FROM erp_call WHERE consignment_id = %(c)s)"
                "    OR EXISTS (SELECT 1 FROM dispatch_attention WHERE consignment_id = %(c)s)"
                "    OR EXISTS (SELECT 1 FROM ext_ref WHERE consignment_id = %(c)s)", {"c": consignment_id})
    return cur.fetchone()[0]


def rewrite_derivation(cur, sheet_id: int, cells: list[dict], derived: dict, actor: str) -> None:
    """★ 草稿表整表重写派生物：删光排货行（级联映射）→ 删掉不再需要的排货单 → 补新单 → 写行 → 写映射。
    人填的东西（安排、装箱）不在这里动。量约束（016 c_dispatch_qty）延迟到提交时判。"""
    keys = consignment_keys(cur, sheet_id)
    if keys:
        cur.execute("DELETE FROM consignment_line WHERE consignment_id = ANY(%s)", (list(keys.values()),))
    for dk, cid in keys.items():
        if dk not in derived:
            if has_outbound_trace(cur, cid):
                # ★ 发过领星（哪怕被拒）的排货单不删 —— erp_call / 待核实 / 领星单号是只追加的留痕，删了就断了。
                #   改「已放弃」（025：唯一索引不再算它，改回原目的地会新开一张）
                consignment_event(cur, cid, "草稿", "已放弃", actor, f"rederive:{sheet_id}")
            else:
                cur.execute("DELETE FROM consignment WHERE consignment_id = %s", (cid,))
    for dk in derived:
        if dk not in keys:
            channel, dest = dk
            cur.execute("INSERT INTO consignment (sheet_id, channel, dest_wid, dest_sid, created_by)"
                        " VALUES (%s,%s,%s,%s,%s) RETURNING consignment_id",
                        (sheet_id, channel, dest if channel == "oversea" else None,
                         dest if channel == "fba" else None, actor))
            keys[dk] = cur.fetchone()[0]
    for dk, lines in derived.items():
        for (seller_sku, sid, from_wid), ln in lines.items():
            cur.execute("INSERT INTO consignment_line (consignment_id, seller_sku, sid, from_wid, sku, units)"
                        " VALUES (%s,%s,%s,%s,%s,%s)", (keys[dk], seller_sku, sid, from_wid, ln["sku"], ln["units"]))
    for c in cells:
        dk = ("fba", c["dest_sid"]) if c["channel"] == "fba" else ("oversea", c["dest_wid"])
        cur.execute("INSERT INTO dispatch_source_map (arrangement_id, consignment_id, seller_sku, sid, from_wid,"
                    " units) VALUES (%s,%s,%s,%s,%s,%s)",
                    (c["arrangement_id"], keys[dk], c["seller_sku"], c["sid"], c["from_wid"], c["units"]))


def packing(cur, sheet_id: int) -> dict[tuple, dict]:
    cur.execute(f"SELECT seller_sku, sid, from_wid, {', '.join(PACKING_COLS)} FROM consignment_packing"
                " WHERE sheet_id = %s", (sheet_id,))
    return {(r[0], r[1], r[2]): {k: (float(v) if v is not None and not isinstance(v, int) else v)
                                 for k, v in zip(PACKING_COLS, r[3:])} for r in cur.fetchall()}


def upsert_packing(cur, sheet_id: int, key: tuple, box: dict, actor: str) -> None:
    cols = ", ".join(PACKING_COLS)
    sets = ", ".join(f"{k} = EXCLUDED.{k}" for k in PACKING_COLS)
    cur.execute(f"INSERT INTO consignment_packing (sheet_id, seller_sku, sid, from_wid, {cols}, updated_by)"
                f" VALUES (%s,%s,%s,%s,{', '.join(['%s'] * len(PACKING_COLS))},%s)"
                f" ON CONFLICT (sheet_id, seller_sku, sid, from_wid) DO UPDATE SET {sets},"
                " updated_by = EXCLUDED.updated_by, updated_at = now()",
                (sheet_id, *key, *[box.get(k) for k in PACKING_COLS], actor))


def consignments(cur, sheet_id: int) -> list[dict]:
    cur.execute("SELECT consignment_id, channel, dest_wid, dest_sid, our_stage, idem_key FROM consignment"
                " WHERE sheet_id = %s ORDER BY consignment_id", (sheet_id,))
    out = [dict(zip(("consignment_id", "channel", "dest_wid", "dest_sid", "our_stage", "idem_key"), r), lines=[])
           for r in cur.fetchall()]
    by_id = {c["consignment_id"]: c for c in out}
    cur.execute(
        "SELECT l.consignment_id, l.seller_sku, l.sid, l.from_wid, l.sku, l.units,"
        "       coalesce(sum(m.units) FILTER (WHERE a.line_id IS NOT NULL), 0)::int,"
        "       coalesce(sum(m.units) FILTER (WHERE a.line_id IS NULL), 0)::int"
        "  FROM consignment_line l JOIN consignment c USING (consignment_id)"
        "  LEFT JOIN dispatch_source_map m"
        "    ON (m.consignment_id, m.seller_sku, m.sid, m.from_wid) = (l.consignment_id, l.seller_sku, l.sid, l.from_wid)"
        "  LEFT JOIN dispatch_arrangement a USING (arrangement_id)"
        " WHERE c.sheet_id = %s GROUP BY 1,2,3,4,5,6 ORDER BY 1,2,3,4", (sheet_id,))
    for r in cur.fetchall():
        by_id[r[0]]["lines"].append(dict(zip(("seller_sku", "sid", "from_wid", "sku", "units", "planned",
                                              "unplanned"), r[1:])))
    return out


def dispatchable_lines(cur, sheet_id: int, plan_id: int | None) -> list[dict]:
    """这张表那张计划的全部活记录（CA-D10 按量排、CA-D11 一表一计划；还差 0 的也列，看得见「已满足」），
    外加本表已有格子的记录（不论状态 —— 撤销 / 退回后还要撤得回来，C1）。"""
    cur.execute(
        "SELECT l.line_id, l.plan_id, p.title, l.rev, l.sku, k.name, l.period_start, l.state,"
        "       q.total_units, q.staged_units, q.dispatched_units, q.pending_units, l.demand_by_seller,"
        "       po.confirmed_units, po.ordered_units,"
        # ★ 与 v_plan_line_dispatch_qty 同口径分成「未发 / 已投送」：staged 不含已投送，本表那份也不许含，
        #   否则「别的表已排未发」= staged − staged_here 会出负数（评审 P1 #11，sheet 11 实测 −1）
        "       coalesce((SELECT sum(m.units) FILTER (WHERE c.our_stage IN ('草稿','已确认'))"
        "                   FROM dispatch_arrangement a JOIN dispatch_source_map m USING (arrangement_id)"
        "                   JOIN consignment c ON c.consignment_id = m.consignment_id"
        "                  WHERE a.sheet_id = %s AND a.line_id = l.line_id), 0)::int,"
        "       coalesce((SELECT sum(m.units) FILTER (WHERE c.our_stage IN ('已投送','已归档'))"
        "                   FROM dispatch_arrangement a JOIN dispatch_source_map m USING (arrangement_id)"
        "                   JOIN consignment c ON c.consignment_id = m.consignment_id"
        "                  WHERE a.sheet_id = %s AND a.line_id = l.line_id), 0)::int"
        "  FROM plan_line l JOIN plan p USING (plan_id) JOIN sku_catalog k USING (sku)"
        "  JOIN v_plan_line_dispatch_qty q USING (line_id) JOIN v_plan_line_po_qty po USING (line_id)"
        f" WHERE (l.plan_id = %s AND {LIVE_LINE})"
        "    OR EXISTS (SELECT 1 FROM dispatch_arrangement a WHERE a.sheet_id = %s AND a.line_id = l.line_id)"
        " ORDER BY l.sku, l.period_start, l.line_id", (sheet_id, sheet_id, plan_id, sheet_id))
    cols = ("line_id", "plan_id", "plan_title", "rev", "sku", "sku_name", "period", "state", "total",
            "staged", "dispatched", "pending", "demand_by_seller", "confirmed", "ordered", "staged_here",
            "dispatched_here")
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def count_lines_not_dispatchable(cur, plan_id: int | None) -> int:
    """被丢掉的一侧：这张计划里已撤销 / 已完结、因此不进网格的记录数（CLAUDE.md：统计被丢掉的那一侧）。"""
    cur.execute("SELECT count(*) FROM plan_line l WHERE l.plan_id = %s AND NOT (" + LIVE_LINE + ")", (plan_id,))
    return cur.fetchone()[0]


def mskus_by_sku_sid(cur, skus: list[str]) -> dict[tuple[str, str], list[dict]]:
    cur.execute("SELECT sku, sid, seller_sku, fnsku FROM msku_bridge WHERE sku = ANY(%s)"
                " ORDER BY sku, sid, seller_sku", (skus,))
    out: dict = {}
    for sku, sid, ssku, fnsku in cur.fetchall():
        out.setdefault((sku, sid), []).append({"seller_sku": ssku, "fnsku": fnsku})
    return out


def sku_names(cur, skus: list[str]) -> dict[str, str]:
    cur.execute("SELECT sku, name FROM sku_catalog WHERE sku = ANY(%s)", (skus,))
    return dict(cur.fetchall())


def linked_pos(cur, line_ids: list[int]) -> dict[int, list[dict]]:
    cur.execute(
        "SELECT m.line_id, e.po_id, e.order_sn, e.state, e.is_void, l.wid, sum(m.units)::int"
        "  FROM po_source_map m JOIN v_po_effective e ON e.po_id = m.po_id"
        "  JOIN po_line l ON (l.po_id, l.line_no) = (m.po_id, m.line_no)"
        " WHERE m.line_id = ANY(%s) AND e.discarded_at IS NULL"
        " GROUP BY 1,2,3,4,5,6 ORDER BY 1,2", (line_ids,))
    out: dict = {}
    for r in cur.fetchall():
        out.setdefault(r[0], []).append(dict(zip(("po_id", "order_sn", "state", "is_void", "wid", "units"), r[1:])))
    return out


def committed(cur, skus: list[str] | None = None) -> dict[tuple[str, int], dict[str, int]]:
    cur.execute("SELECT sku, from_wid, sid, units FROM v_stock_committed"
                " WHERE %s::text[] IS NULL OR sku = ANY(%s)", (skus, skus))
    out: dict = {}
    for sku, wid, sid, units in cur.fetchall():
        out.setdefault((sku, wid), {})[sid] = units
    return out


def ledger_conflicts(cur, sheet_id: int, seen: int | None) -> list[dict]:
    """G4（CA-Q12 暂行范围）：别的未发排货表在 seen 之后写过同一 货号×来源仓。"""
    if seen is None:
        return []
    cur.execute(
        "SELECT DISTINCT o.sheet_id, o.sku, o.from_wid FROM dispatch_arrangement o"
        "  JOIN sheet s ON s.sheet_id = o.sheet_id AND s.state IN ('草稿','已确认')"
        " WHERE o.sheet_id <> %s AND o.ledger_seq > %s"
        "   AND (o.sku, o.from_wid) IN (SELECT sku, from_wid FROM dispatch_arrangement WHERE sheet_id = %s)"
        " ORDER BY 1,2,3", (sheet_id, seen, sheet_id))
    return [dict(zip(("sheet_id", "sku", "from_wid"), r)) for r in cur.fetchall()]


def sheet_violations(cur, sheet_id: int) -> list[dict]:
    """`v_check_dispatch` 中属于这张表的行（确认前的完整性闸）。"""
    cur.execute(
        "SELECT check_name, ref FROM v_check_dispatch v"
        " WHERE (v.ref ->> 'sheet_id')::bigint = %s"
        "    OR (v.ref ->> 'consignment_id')::bigint IN (SELECT consignment_id FROM consignment WHERE sheet_id = %s)"
        "    OR (v.ref ->> 'line_id')::bigint IN (SELECT line_id FROM dispatch_arrangement WHERE sheet_id = %s)"
        " ORDER BY check_name", (sheet_id, sheet_id, sheet_id))
    return [{"check_name": a, "ref": b} for a, b in cur.fetchall()]


def lock_sheet_lines(cur, sheet_id: int, extra: int | None) -> None:
    """★ I2：按 line_id 升序一次锁全这张表涉及的记录（+ 这次要写的那条）。
    重派生会重写整表映射，提交时的延迟量约束要锁表里每条记录 —— 不先按序锁全，两张表交叉写就成环。"""
    cur.execute("SELECT line_id FROM plan_line WHERE line_id IN ("
                "  SELECT line_id FROM dispatch_arrangement WHERE sheet_id = %s AND line_id IS NOT NULL"
                "  UNION SELECT %s::bigint) ORDER BY line_id FOR NO KEY UPDATE", (sheet_id, extra))


def insert_gate_run(cur, sheet_id: int, actor: str, ok: bool, seen: int | None, stock_as_of, stock_rejected,
                    gates_json: str) -> int:
    cur.execute("INSERT INTO sheet_gate_run (sheet_id, actor, ok, seen_watermark, ledger_at, stock_as_of,"
                " stock_rejected, gates) VALUES (%s,%s,%s,%s,(SELECT last_value FROM ledger_seq),%s,%s,%s)"
                " RETURNING run_id", (sheet_id, actor, ok, seen, stock_as_of, stock_rejected, gates_json))
    return cur.fetchone()[0]


def last_gate_run(cur, sheet_id: int) -> dict | None:
    cur.execute("SELECT run_id, ok, gates, stock_as_of, at, actor FROM sheet_gate_run WHERE sheet_id = %s"
                " ORDER BY run_id DESC LIMIT 1", (sheet_id,))
    r = cur.fetchone()
    return None if r is None else dict(zip(("run_id", "ok", "gates", "stock_as_of", "at", "actor"), r))


def sheet_event(cur, sheet_id: int, frm: str, to: str, actor: str, reason: str | None, src: str) -> None:
    cur.execute("INSERT INTO sheet_event (sheet_id, from_state, to_state, actor, reason, src)"
                " VALUES (%s,%s,%s,%s,%s,%s)", (sheet_id, frm, to, actor, reason, src))


def sheet_events(cur, sheet_id: int) -> list[dict]:
    cur.execute("SELECT from_state, to_state, actor, reason, at FROM sheet_event WHERE sheet_id = %s"
                " ORDER BY seq", (sheet_id,))
    return [dict(zip(("from_state", "to_state", "actor", "reason", "at"), r)) for r in cur.fetchall()]


# ── UX-D7：新建 FBA 表时的默认分配（迁移 026）────────────────────────

def bridge_skus(cur, keys: list[tuple[str, str]]) -> dict[tuple[str, str], str]:
    """(seller_sku, sid) → 镜像里的货号；不在镜像里的键不出现（调用方点名 msku_not_in_bridge）。"""
    if not keys:
        return {}
    cur.execute("SELECT seller_sku, sid, sku FROM msku_bridge"
                " WHERE (seller_sku, sid) IN (SELECT * FROM unnest(%s::text[], %s::text[]))",
                ([k[0] for k in keys], [k[1] for k in keys]))
    return {(r[0], r[1]): r[2] for r in cur.fetchall()}


def lock_lines(cur, line_ids: list[int]) -> None:
    """★ 同 lock_sheet_lines：按 line_id 升序一次锁全，不按分配顺序（月份序）逐条锁 —— 交叉写会成环。"""
    cur.execute("SELECT line_id FROM plan_line WHERE line_id = ANY(%s) ORDER BY line_id FOR NO KEY UPDATE",
                (sorted(line_ids),))


AUTOFILL_COLS = ("rule", "outcome", "inputs", "filled", "skipped", "error", "elapsed_ms", "actor", "at")


def insert_autofill(cur, sheet_id: int, rule: str, outcome: str, inputs: dict, filled: list, skipped: list,
                    error: dict | None, elapsed_ms: int, actor: str) -> None:
    def dump(v):
        return None if v is None else json.dumps(v, ensure_ascii=False, default=str)
    cur.execute("INSERT INTO dispatch_autofill (sheet_id, rule, outcome, inputs, filled, skipped, error, elapsed_ms,"
                " actor) VALUES (%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s)",
                (sheet_id, rule, outcome, dump(inputs), dump(filled), dump(skipped), dump(error), elapsed_ms, actor))


def autofill(cur, sheet_id: int) -> dict | None:
    cur.execute(f"SELECT {', '.join(AUTOFILL_COLS)} FROM dispatch_autofill WHERE sheet_id = %s", (sheet_id,))
    r = cur.fetchone()
    return None if r is None else dict(zip(AUTOFILL_COLS, r))


# ── CA-D12：领星现读 ─────────────────────────────────────────

def insert_stock_read(cur, sku: str, call_id: int | None, buckets: dict, dropped: dict, rows_in: int,
                      actor: str) -> tuple[int, object]:
    cur.execute("INSERT INTO stock_read (sku, call_id, rows_in, dropped, actor) VALUES (%s,%s,%s,%s::jsonb,%s)"
                " RETURNING read_id, read_at", (sku, call_id, rows_in, json.dumps(dropped), actor))
    read_id, read_at = cur.fetchone()
    for (k_sku, wid), b in buckets.items():
        for bucket, valid in b.items():
            cur.execute("INSERT INTO stock_obs (read_id, sku, wid, bucket, valid) VALUES (%s,%s,%s,%s,%s)",
                        (read_id, k_sku, wid, bucket, valid))
    return read_id, read_at


def latest_stock_reads(cur, skus: list[str] | None) -> dict[str, dict]:
    """每个货号最近一次领星现读：{sku: {"read_at", "buckets": {(sku, wid): {bucket: valid}}}}。
    ★ 现读里没有某个仓的行 = 那个仓此刻没有这个货号（整批读过），不是「没查」。"""
    cur.execute(
        "SELECT DISTINCT ON (sku) sku, read_id, read_at FROM stock_read"
        " WHERE %s::text[] IS NULL OR sku = ANY(%s) ORDER BY sku, read_at DESC, read_id DESC", (skus, skus))
    heads = {sku: (rid, at) for sku, rid, at in cur.fetchall()}
    out: dict[str, dict] = {sku: {"read_at": at, "read_id": rid, "buckets": {}} for sku, (rid, at) in heads.items()}
    if heads:
        cur.execute("SELECT read_id, sku, wid, bucket, valid FROM stock_obs WHERE read_id = ANY(%s)",
                    ([rid for rid, _ in heads.values()],))
        for _rid, sku, wid, bucket, valid in cur.fetchall():
            out[sku]["buckets"].setdefault((sku, wid), {"shared": 0})[bucket] = valid
    return out


def latest_item_progress(cur, order_sns: list[str]) -> dict[tuple[str, str], tuple[int, int, object]]:
    """采购页领星读回（`po_item_obs`，source=api）最近一次里每个 (单号, 货号) 的 已入库 / 待到货。
    ★ 老观测（019 之前）没有这两列 → 不算数，不当 0。"""
    cur.execute(
        "WITH last AS (SELECT DISTINCT ON (order_sn) order_sn, source_captured_at AS at, call_id FROM po_item_obs"
        "               WHERE order_sn = ANY(%s) AND source = 'api' AND quantity_entry IS NOT NULL"
        "               ORDER BY order_sn, source_captured_at DESC, obs_id DESC)"
        " SELECT o.order_sn, o.sku, sum(o.quantity_entry)::int, sum(o.quantity_receive)::int, l.at"
        "   FROM po_item_obs o JOIN last l ON l.order_sn = o.order_sn AND l.at = o.source_captured_at"
        "    AND l.call_id IS NOT DISTINCT FROM o.call_id"
        "  WHERE o.quantity_entry IS NOT NULL GROUP BY o.order_sn, o.sku, l.at", (order_sns,))
    return {(sn, sku): (e, p, at) for sn, sku, e, p, at in cur.fetchall()}


def placed_pos_for_sku(cur, sheet_id: int, sku: str) -> list[tuple[int, str]]:
    """这张表那张计划里该货号的记录所关联、已下到领星的采购单（现查时一并读回）。"""
    cur.execute(
        "SELECT DISTINCT e.po_id, e.order_sn FROM po_source_map m JOIN v_po_effective e ON e.po_id = m.po_id"
        "  JOIN plan_line l ON l.line_id = m.line_id JOIN sheet s ON s.plan_id = l.plan_id"
        " WHERE s.sheet_id = %s AND l.sku = %s AND e.state = '已下单' AND e.order_sn IS NOT NULL"
        "   AND e.discarded_at IS NULL ORDER BY e.po_id", (sheet_id, sku))
    return [(r[0], r[1]) for r in cur.fetchall()]


def sheet_skus(cur, sheet_id: int) -> list[str]:
    cur.execute("SELECT DISTINCT sku FROM dispatch_arrangement WHERE sheet_id = %s ORDER BY sku", (sheet_id,))
    return [r[0] for r in cur.fetchall()]


# ── C-b：物流方式（CB-D3）──────────────────────────────────────

def upsert_channels(cur, rows: list[tuple]) -> int:
    """(logistics_id, channel_name, method_name, provider_name, receive_country_codes(json), enabled)。只 upsert 不删（同镜像）。"""
    for r in rows:
        cur.execute("INSERT INTO logistics_channel (logistics_id, channel_name, method_name, provider_name,"
                    " receive_country_codes, enabled, refreshed_at) VALUES (%s,%s,%s,%s,%s::jsonb,%s,now())"
                    " ON CONFLICT (logistics_id) DO UPDATE SET channel_name=EXCLUDED.channel_name,"
                    " method_name=EXCLUDED.method_name, provider_name=EXCLUDED.provider_name,"
                    " receive_country_codes=EXCLUDED.receive_country_codes, enabled=EXCLUDED.enabled,"
                    " refreshed_at=EXCLUDED.refreshed_at", r)
    return len(rows)


def channels(cur) -> tuple[list[dict], object]:
    cur.execute("SELECT logistics_id, channel_name, method_name, provider_name, receive_country_codes, enabled"
                " FROM logistics_channel ORDER BY enabled DESC, channel_name")
    rows = [dict(zip(("logistics_id", "channel_name", "method_name", "provider_name", "receive_country_codes",
                      "enabled"), r)) for r in cur.fetchall()]
    cur.execute("SELECT max(refreshed_at) FROM logistics_channel")
    return rows, cur.fetchone()[0]


def channel(cur, logistics_id: int) -> dict | None:
    cur.execute("SELECT logistics_id, channel_name, enabled FROM logistics_channel WHERE logistics_id=%s", (logistics_id,))
    r = cur.fetchone()
    return None if r is None else {"logistics_id": r[0], "channel_name": r[1], "enabled": r[2]}


def set_logistics(cur, sheet_id: int, dest_wid: int, logistics_id: int, actor: str) -> None:
    cur.execute("INSERT INTO consignment_logistics (sheet_id, dest_wid, logistics_id, updated_by) VALUES (%s,%s,%s,%s)"
                " ON CONFLICT (sheet_id, dest_wid) DO UPDATE SET logistics_id=EXCLUDED.logistics_id,"
                " updated_by=EXCLUDED.updated_by, updated_at=now()", (sheet_id, dest_wid, logistics_id, actor))


def sheet_logistics(cur, sheet_id: int) -> dict[int, dict]:
    cur.execute("SELECT l.dest_wid, l.logistics_id, c.channel_name, c.enabled FROM consignment_logistics l"
                " JOIN logistics_channel c USING (logistics_id) WHERE l.sheet_id=%s", (sheet_id,))
    return {r[0]: {"logistics_id": r[1], "channel_name": r[2], "enabled": r[3]} for r in cur.fetchall()}


# ── C-b：提交领星备货单（spec 2026-10-12）────────────────────────

def product_ids(cur, skus: list[str]) -> dict[str, int | None]:
    cur.execute("SELECT sku, product_id FROM sku_catalog WHERE sku = ANY(%s)", (skus,))
    return dict(cur.fetchall())


def fnsku_map(cur, keys: list[tuple[str, str]]) -> dict[tuple[str, str], str]:
    if not keys:
        return {}
    cur.execute("SELECT seller_sku, sid, fnsku FROM msku_bridge WHERE (seller_sku, sid) IN (SELECT * FROM unnest(%s::text[], %s::text[]))"
                " AND fnsku IS NOT NULL", ([k[0] for k in keys], [k[1] for k in keys]))
    return {(r[0], r[1]): r[2] for r in cur.fetchall()}


def ext_refs(cur, sheet_id: int) -> list[dict]:
    """这张表在领星建过的备货单（含已作废的，带 voided_at）。"""
    cur.execute(
        "SELECT e.ext_ref_id, e.consignment_id, e.from_wid, e.kind, e.idem_key, e.ext_no, e.found_by, e.actor, e.at,"
        "       v.at, v.reason, p.order_sns, l.locked FROM ext_ref e JOIN consignment c USING (consignment_id)"
        "  LEFT JOIN fba_plan_order p USING (ext_ref_id) LEFT JOIN v_fba_plan_lock l USING (ext_ref_id)"
        "  LEFT JOIN ext_ref_void v USING (ext_ref_id) WHERE c.sheet_id = %s ORDER BY e.ext_ref_id", (sheet_id,))
    cols = ("ext_ref_id", "consignment_id", "from_wid", "kind", "idem_key", "ext_no", "found_by", "actor", "at",
            "voided_at", "void_reason", "order_sns", "locked")
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def insert_ext_ref(cur, consignment_id: int, from_wid: int, idem: str, ext_no: str, call_id: int | None,
                   found_by: str, actor: str, kind: str = "ows") -> int:
    cur.execute("INSERT INTO ext_ref (consignment_id, from_wid, kind, idem_key, ext_no, call_id, found_by, actor)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING ext_ref_id",
                (consignment_id, from_wid, kind, idem, ext_no, call_id, found_by, actor))
    return cur.fetchone()[0]


def void_ext_ref(cur, ext_ref_id: int, call_id: int | None, actor: str, reason: str) -> None:
    cur.execute("INSERT INTO ext_ref_void (ext_ref_id, call_id, actor, reason) VALUES (%s,%s,%s,%s)",
                (ext_ref_id, call_id, actor, reason))


def insert_write_call(cur, *, endpoint: str, side_effect: str, consignment_id: int, idem_key: str | None,
                      actor: str, request: dict) -> int:
    """★ 部分唯一索引 erp_call_one_write_in_flight_per_consignment：同一排货单同一时刻只一条在途写调用。"""
    cur.execute("INSERT INTO erp_call (endpoint, side_effect, consignment_id, idem_key, actor, request)"
                " VALUES (%s,%s,%s,%s,%s,%s::jsonb) RETURNING call_id",
                (endpoint, side_effect, consignment_id, idem_key, actor, json.dumps(request, ensure_ascii=False, default=str)))
    return cur.fetchone()[0]


def open_attention(cur, sheet_id: int) -> list[dict]:
    cur.execute(
        "SELECT a.attention_id, a.consignment_id, a.from_wid, a.idem_key, a.call_id, a.detail, a.opened_at"
        "  FROM dispatch_attention a JOIN consignment c USING (consignment_id)"
        "  LEFT JOIN dispatch_attention_close x USING (attention_id)"
        " WHERE c.sheet_id = %s AND x.attention_id IS NULL ORDER BY a.attention_id", (sheet_id,))
    cols = ("attention_id", "consignment_id", "from_wid", "idem_key", "call_id", "detail", "opened_at")
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def insert_attention(cur, consignment_id: int, from_wid: int, idem: str, call_id: int | None, detail: dict) -> int:
    cur.execute("INSERT INTO dispatch_attention (consignment_id, from_wid, idem_key, call_id, detail)"
                " VALUES (%s,%s,%s,%s,%s::jsonb) RETURNING attention_id",
                (consignment_id, from_wid, idem, call_id, json.dumps(detail, ensure_ascii=False, default=str)))
    return cur.fetchone()[0]


def close_attention(cur, attention_id: int, how: str, ext_ref_id: int | None, actor: str, reason: str | None) -> None:
    cur.execute("INSERT INTO dispatch_attention_close (attention_id, how, ext_ref_id, actor, reason) VALUES (%s,%s,%s,%s,%s)",
                (attention_id, how, ext_ref_id, actor, reason))


def consignment_event(cur, consignment_id: int, frm: str, to: str, actor: str, src: str) -> None:
    cur.execute("INSERT INTO consignment_event (consignment_id, from_state, to_state, actor, src) VALUES (%s,%s,%s,%s,%s)",
                (consignment_id, frm, to, actor, src))


# ── 回执（spec 2026-10-13）：观察只追加，当前进度走 v_ext_stage ─────────────

def receipt_targets(cur, sheet_id: int | None) -> list[dict]:
    """要读回的领星单：未作废、所在排货单 = 已投送（已归档的不再读）。"""
    cur.execute(
        "SELECT e.ext_ref_id, e.ext_no, e.consignment_id, c.sheet_id, e.actor, e.kind, e.at, c.dest_sid FROM ext_ref e"
        "  JOIN consignment c USING (consignment_id)"
        " WHERE c.our_stage = '已投送' AND NOT EXISTS (SELECT 1 FROM ext_ref_void v WHERE v.ext_ref_id = e.ext_ref_id)"
        "   AND (%s::bigint IS NULL OR c.sheet_id = %s) ORDER BY c.sheet_id, e.ext_ref_id", (sheet_id, sheet_id))
    return [dict(zip(("ext_ref_id", "ext_no", "consignment_id", "sheet_id", "actor", "kind", "at", "dest_sid"), r))
            for r in cur.fetchall()]


def insert_receipt_run(cur, trigger: str, actor: str | None, sheet_id: int | None) -> int:
    cur.execute("INSERT INTO receipt_run (trigger, actor, sheet_id) VALUES (%s,%s,%s) RETURNING run_id",
                (trigger, actor, sheet_id))
    return cur.fetchone()[0]


def insert_receipt_result(cur, run_id: int, refs: int, ok: int, failed: int, skipped: int, archived: list[int],
                          failures: list[dict]) -> None:
    cur.execute("INSERT INTO receipt_run_result (run_id, refs, ok, failed, skipped, archived, failures)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)",
                (run_id, refs, ok, failed, skipped, archived, json.dumps(failures, ensure_ascii=False, default=str)))


def insert_stage_obs(cur, ext_ref_id: int, run_id: int, status: str, stage: str, rank: int, sub_status: str | None,
                     detail: dict) -> None:
    cur.execute("INSERT INTO ext_stage_obs (ext_ref_id, run_id, status, stage, stage_rank, sub_status, detail)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)",
                (ext_ref_id, run_id, status, stage, rank, sub_status, json.dumps(detail, ensure_ascii=False, default=str)))


def ext_stages(cur, sheet_id: int) -> dict[int, dict]:
    cur.execute("SELECT s.ext_ref_id, s.stage, s.stage_rank, s.observed_at FROM v_ext_stage s"
                "  JOIN ext_ref e USING (ext_ref_id) JOIN consignment c USING (consignment_id) WHERE c.sheet_id = %s",
                (sheet_id,))
    return {r[0]: {"stage": r[1], "rank": r[2], "observed_at": r[3]} for r in cur.fetchall()}


def archivable(cur, consignment_ids: list[int]) -> list[int]:
    """已投送、且全部未作废领星单当前进度 = 已完成 的排货单（库层 consignment_archive_guard 再核）。"""
    cur.execute(
        "SELECT c.consignment_id FROM consignment c WHERE c.consignment_id = ANY(%s) AND c.our_stage = '已投送'"
        "   AND EXISTS (SELECT 1 FROM ext_ref e WHERE e.consignment_id = c.consignment_id"
        "                 AND NOT EXISTS (SELECT 1 FROM ext_ref_void v WHERE v.ext_ref_id = e.ext_ref_id))"
        "   AND NOT EXISTS (SELECT 1 FROM ext_ref e LEFT JOIN v_ext_stage s USING (ext_ref_id)"
        "                    WHERE e.consignment_id = c.consignment_id"
        "                      AND NOT EXISTS (SELECT 1 FROM ext_ref_void v WHERE v.ext_ref_id = e.ext_ref_id)"
        "                      AND s.stage IS DISTINCT FROM '已完成') ORDER BY 1", (consignment_ids,))
    return [r[0] for r in cur.fetchall()]



# ── C-c1 FBA 发货计划（迁移 024）────────────────────────────────

def insert_fba_plan_order(cur, ext_ref_id: int, order_sns: list[str]) -> None:
    cur.execute("INSERT INTO fba_plan_order (ext_ref_id, order_sns) VALUES (%s,%s)", (ext_ref_id, order_sns))


def insert_fba_stock(cur, ext_ref_id: int, action: str, call_id: int | None, actor: str) -> None:
    cur.execute("INSERT INTO fba_plan_stock (ext_ref_id, action, call_id, actor) VALUES (%s,%s,%s,%s)",
                (ext_ref_id, action, call_id, actor))


def consignment_dest_sid(cur, consignment_id: int) -> str | None:
    cur.execute("SELECT dest_sid FROM consignment WHERE consignment_id = %s", (consignment_id,))
    r = cur.fetchone()
    return None if r is None else r[0]


# ── /dispatch 排货表 chip 的「下一步」（spec 2026-10-15 §5 · DL-D7：只读本地事实）──────────

def sheets_in_flight(cur, sheet_ids: list[int]) -> dict[int, int]:
    """在途写调用数。判据同 020 erp_call_one_write_in_flight_per_consignment（side_effect <> 'read'）。"""
    cur.execute("SELECT c.sheet_id, count(*)::int FROM erp_call e JOIN consignment c USING (consignment_id)"
                " WHERE c.sheet_id = ANY(%s) AND e.outcome = 'in_flight' AND e.side_effect <> 'read'"
                " GROUP BY c.sheet_id", (sheet_ids,))
    return dict(cur.fetchall())


def sheets_open_attention(cur, sheet_ids: list[int]) -> dict[int, int]:
    cur.execute("SELECT c.sheet_id, count(*)::int FROM dispatch_attention a JOIN consignment c USING (consignment_id)"
                " WHERE c.sheet_id = ANY(%s) AND NOT EXISTS"
                "       (SELECT 1 FROM dispatch_attention_close x WHERE x.attention_id = a.attention_id)"
                " GROUP BY c.sheet_id", (sheet_ids,))
    return dict(cur.fetchall())


def sheets_ext_stages(cur, sheet_ids: list[int]) -> list[tuple[int, str, str | None]]:
    """未作废领星单的 (sheet_id, 我方进度, 领星当前进度)；两轴不一致由 rules.receipt.drift 判（同详情 _with_stage）。"""
    cur.execute("SELECT c.sheet_id, c.our_stage, s.stage FROM ext_ref e JOIN consignment c USING (consignment_id)"
                "  LEFT JOIN v_ext_stage s USING (ext_ref_id)"
                " WHERE c.sheet_id = ANY(%s)"
                "   AND NOT EXISTS (SELECT 1 FROM ext_ref_void v WHERE v.ext_ref_id = e.ext_ref_id)"
                " ORDER BY c.sheet_id, e.ext_ref_id", (sheet_ids,))
    return [tuple(r) for r in cur.fetchall()]


def sheets_submit_progress(cur, sheet_ids: list[int]) -> dict[int, dict]:
    """可提交的 排货单 × 来源仓（= 一张领星单，CB-D5；同 rules/ows · rules/fba_plan 的 orders 粒度与 SUBMITTABLE）：
    共几张 · 还没领星单号几张 · FBA 已建未锁几张。★ channel 按表去向取，旧版混合表同详情走海外仓那条（submit_ows._plan）。
    ★ DL-D11：to_submit 按 排货单 × 来源仓 计（与详情「提交领星（N 张）」同一个 N）。"""
    cur.execute(
        "WITH pairs AS ("
        "  SELECT DISTINCT c.sheet_id, l.consignment_id, l.from_wid"
        "    FROM consignment c JOIN sheet s USING (sheet_id) JOIN consignment_line l USING (consignment_id)"
        "   WHERE c.sheet_id = ANY(%s) AND c.our_stage IN ('已确认','已投送')"
        "     AND c.channel = coalesce(s.route, 'oversea')),"
        " live AS ("
        "  SELECT e.ext_ref_id, e.consignment_id, e.from_wid, e.kind, k.locked"
        "    FROM ext_ref e LEFT JOIN v_fba_plan_lock k USING (ext_ref_id)"
        "   WHERE NOT EXISTS (SELECT 1 FROM ext_ref_void v WHERE v.ext_ref_id = e.ext_ref_id))"
        " SELECT p.sheet_id, count(*)::int, count(*) FILTER (WHERE x.ext_ref_id IS NULL)::int,"
        "        count(*) FILTER (WHERE x.kind = 'fba_plan' AND x.locked IS NOT TRUE)::int"
        "   FROM pairs p LEFT JOIN live x ON x.consignment_id = p.consignment_id AND x.from_wid = p.from_wid"
        "  GROUP BY p.sheet_id", (sheet_ids,))
    return {r[0]: {"orders": r[1], "to_submit": r[2], "to_lock": r[3]} for r in cur.fetchall()}


def sheets_archived(cur, sheet_ids: list[int]) -> dict[int, int]:
    """被回执归档（领星已完成，已投送 → 已归档）的排货单数。DL-D13：情形 5/10/11 的 pairs 只看已确认·已投送，
    全部归档的表在那里是 0 张 —— 不另数就会落到 DL-Q6 兜底。"""
    cur.execute("SELECT sheet_id, count(*)::int FROM consignment"
                " WHERE sheet_id = ANY(%s) AND our_stage = '已归档' GROUP BY sheet_id", (sheet_ids,))
    return dict(cur.fetchall())


def sheets_draft_gaps(cur, sheet_ids: list[int]) -> dict[int, dict]:
    """活的排货单数 · FBA 缺装箱行数（同前端 missingPacking：没有装箱行或六项有空）· 海外仓没选物流方式的单数（G7）。"""
    box_missing = " OR ".join(f"p.{k} IS NULL" for k in PACKING_COLS)
    cur.execute(
        "SELECT c.sheet_id, count(DISTINCT c.consignment_id)::int,"
        "       count(*) FILTER (WHERE c.channel = 'fba' AND l.consignment_id IS NOT NULL"
        f"                        AND (p.sheet_id IS NULL OR {box_missing}))::int,"
        "       count(DISTINCT c.consignment_id) FILTER (WHERE c.channel = 'oversea' AND g.sheet_id IS NULL)::int"
        "  FROM consignment c"
        "  LEFT JOIN consignment_line l ON l.consignment_id = c.consignment_id"
        "  LEFT JOIN consignment_packing p"
        "    ON (p.sheet_id, p.seller_sku, p.sid, p.from_wid) = (c.sheet_id, l.seller_sku, l.sid, l.from_wid)"
        "  LEFT JOIN consignment_logistics g ON (g.sheet_id, g.dest_wid) = (c.sheet_id, c.dest_wid)"
        " WHERE c.sheet_id = ANY(%s) AND c.our_stage NOT IN ('已放弃','已取消')"
        " GROUP BY c.sheet_id", (sheet_ids,))
    return {r[0]: {"consignments": r[1], "packing_missing": r[2], "logistics_missing": r[3]} for r in cur.fetchall()}
