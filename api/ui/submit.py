"""提交与版本。★ 提交是**一个事务**铸出全部记录 —— 半铸的计划单无法解释（01 §5）。"""
from __future__ import annotations

import datetime as dt
import json
import logging

from fastapi import APIRouter, Depends, Request

from api.ui.deps import (
    actor,
    actor_optional,
    declared,
    ensure_plan,
    ensure_writable,
    require_fresh_mirrors,
)
from api.ui.errors import ApiError
from rules.digest import content_digest
from rules.submit import (
    DemandCell,
    PrevLine,
    PurchaseCell,
    reconcile,
    select_submittable,
    seller_units,
)
from shared.pg_client import pg_conn, timed

log = logging.getLogger("scm.api")

router = APIRouter(dependencies=[Depends(require_fresh_mirrors)])

TERMINAL = ("已完结", "已撤销")

#: ★ 一版当时包含哪些记录 = rev 列仍是它的 ∪ 再提交时从它带走的（013 plan_line_carry）。
#  只看 rev 列，旧版会在记录被带进新版后「少了几条」，diff 也会把没变的报成新增。
REV_MEMBERS = ("SELECT line_id, rev FROM plan_line WHERE plan_id = %(p)s"
               " UNION ALL SELECT line_id, from_rev FROM plan_line_carry WHERE plan_id = %(p)s")


def _ensure_rev(cur, plan_id: int, rev: int) -> None:
    cur.execute("SELECT 1 FROM plan_rev WHERE plan_id = %s AND rev = %s", (plan_id, rev))
    if cur.fetchone() is None:
        raise ApiError(404, "rev_not_found", "没有这一版", {"plan_id": plan_id, "rev": rev})


def _cells(cur, plan_id: int):
    cur.execute("SELECT sku, period_start, planned_units FROM plan_purchase_cell"
                " WHERE plan_id = %s", (plan_id,))
    purchase = [PurchaseCell(s, p.strftime("%Y-%m"), u) for s, p, u in cur.fetchall()]
    cur.execute(
        "SELECT d.seller_sku, d.sid, b.sku, d.period_start, d.system_units, d.expected_units"
        "  FROM plan_demand_cell d"
        "  JOIN msku_bridge b ON b.seller_sku = d.seller_sku AND b.sid = d.sid"
        "  JOIN msku_claim cl ON cl.plan_id = d.plan_id AND cl.seller_sku = d.seller_sku"
        "   AND cl.sid = d.sid AND cl.released_at IS NULL"
        " WHERE d.plan_id = %s", (plan_id,))
    demand = [DemandCell(ss, sid, sku, p.strftime("%Y-%m"), sy, ex)
              for ss, sid, sku, p, sy, ex in cur.fetchall()]
    cur.execute("SELECT seller_sku, sid FROM msku_claim"
                " WHERE plan_id = %s AND released_at IS NULL", (plan_id,))
    claimed = {(r[0], r[1]) for r in cur.fetchall()}
    return purchase, demand, claimed


def _lock_prev_lines(cur, plan_id: int) -> tuple[list[PrevLine], dict[int, tuple[int, int]]]:
    """计划里全部未撤销的记录（含 已完结，裁定 A），按 line_id 升序 FOR UPDATE。

    ★ 升序：与 repo.lock_plan_lines、cancel_rev 同序取锁，不成环。
    ★ FOR UPDATE 而不是 NO KEY UPDATE：保留的记录要改 rev，而 rev 在
      plan_line_one_per_cell 这条唯一键里 —— 改键列本来就要这一档锁，
      且它挡住并发组单插映射时外键要的 KEY SHARE，D1 判完到提交之间不会有新来源挂上来。
    """
    cur.execute("SELECT line_id, sku, period_start, total_units, demand_by_seller, state,"
                "       demand_at_submit, rev"
                "  FROM plan_line WHERE plan_id = %s AND state <> '已撤销'"
                " ORDER BY line_id FOR UPDATE", (plan_id,))
    rows = cur.fetchall()
    prev = [PrevLine(r[0], r[1], r[2].strftime("%Y-%m"), r[3], r[4], r[5]) for r in rows]
    return prev, {r[0]: (r[6], r[7]) for r in rows}


def _po_refs(cur, line_ids: list[int]) -> dict[int, list[int]]:
    """被哪些未作废采购单（含草稿）引用。
    ★ 作废有两种：草稿作废（明细与映射已清，查不到）· 领星作废（is_void，映射留档）"""
    if not line_ids:
        return {}
    cur.execute("SELECT m.line_id, array_agg(DISTINCT m.po_id ORDER BY m.po_id)"
                "  FROM po_source_map m"
                "  JOIN v_po_effective e ON e.po_id = m.po_id"
                "   AND NOT e.is_void AND e.discarded_at IS NULL"
                " WHERE m.line_id = ANY(%s) GROUP BY m.line_id", (line_ids,))
    return dict(cur.fetchall())


def _mint(cur, plan_id: int, rev: int, m, who: str) -> int:
    period = dt.date.fromisoformat(m.period + "-01")
    cur.execute(
        "INSERT INTO plan_line (plan_id, rev, sku, period_start, total_units,"
        " demand_by_seller, demand_at_submit, state)"
        " VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, '已提交') RETURNING line_id",
        (plan_id, rev, m.sku, period, m.total_units,
         json.dumps(m.demand_by_seller, ensure_ascii=False), m.demand_at_submit))
    line_id = cur.fetchone()[0]
    # ★ 铸出也留痕：没有这一行，事件表就不是完整履历（S-2；库层还有延迟约束兜底）
    cur.execute("INSERT INTO plan_line_event (line_id, from_state, to_state, actor, src)"
                " VALUES (%s, '[*]', '已提交', %s, %s)",
                (line_id, who, f"submit:{plan_id}#{rev}"))
    return line_id


@router.post("/plans/{plan_id}/submit")
def submit(plan_id: int, request: Request, who: str = Depends(actor)):
    """R2：直接再提交。同一事务里先处置上一版活记录（保留 / 撤销），再立新版 ——
    S-4「任一时刻只 1 版在流转」不变，承重墙求和不翻倍。"""
    declared(request)
    with timed("submit", actor=who, plan_id=plan_id), pg_conn() as c, c.cursor() as cur:
        # ★ 同一计划的提交者串行：不锁的话两个并发再提交各自看到同一组「上一版活记录」
        cur.execute("SELECT 1 FROM plan WHERE plan_id = %s FOR NO KEY UPDATE", (plan_id,))
        ensure_writable(cur, plan_id)
        purchase, demand, claimed = _cells(cur, plan_id)
        lines, skipped = select_submittable(purchase, demand, claimed)
        digest = content_digest(purchase, demand)
        prev, prev_extra = _lock_prev_lines(cur, plan_id)
        refs = _po_refs(cur, [p.line_id for p in prev])
        try:
            rec = reconcile(prev, lines, set(refs))
        except ValueError as e:
            log.error("op=submit outcome=s4_violation plan_id=%s actor=%s detail=%s",
                      plan_id, who, e)
            raise
        blocked = [{"sku": p.sku, "period": p.period, "line_id": p.line_id, "state": p.state,
                    "change": change, "po_ids": refs.get(p.line_id, [])}
                   for p, change in rec.blocked]
        if blocked:
            log.warning("op=submit outcome=lines_in_purchase plan_id=%s actor=%s offenders=%s",
                        plan_id, who, "; ".join(
                            f"sku={b['sku']} period={b['period']} line_id={b['line_id']}"
                            f" state={b['state']} change={b['change']} po_ids={b['po_ids']}"
                            for b in blocked))
            raise ApiError(409, "lines_in_purchase",
                           "要改动的记录已进采购：先从采购单里移掉，或保持该货号·月不变",
                           {"plan_id": plan_id, "lines": blocked})

        cur.execute("SELECT coalesce(max(rev), 0) + 1 FROM plan_rev WHERE plan_id = %s",
                    (plan_id,))
        rev = cur.fetchone()[0]
        src = f"resubmit:{plan_id}#{rev}"
        # ★ D3：这不是人直接指定状态的「撤销」，是「再提交」这个动作的后果 —— 照样记 actor + 理由 + src
        for p, _ in rec.replaced:
            cur.execute("INSERT INTO plan_line_event (line_id, from_state, to_state, actor, reason, src)"
                        " VALUES (%s, %s, '已撤销', %s, %s, %s)",
                        (p.line_id, p.state, who, f"被 rev {rev} 替换", src))
        for p in rec.removed:
            cur.execute("INSERT INTO plan_line_event (line_id, from_state, to_state, actor, reason, src)"
                        " VALUES (%s, %s, '已撤销', %s, %s, %s)",
                        (p.line_id, p.state, who, f"rev {rev} 不再包含", src))

        # ★ 先以「不在流转」立起来：保留的记录还挂在旧版上，旧版此刻仍占着部分唯一索引；
        #   带走它们之后旧版由 close_rev_if_settled 让位，最后 open_rev_if_live 判新版。
        cur.execute("INSERT INTO plan_rev (plan_id, rev, content_digest, submitted_by, in_flight)"
                    " VALUES (%s, %s, %s, %s, false)", (plan_id, rev, digest, who))
        for p in rec.kept:
            cur.execute("INSERT INTO plan_line_carry (line_id, plan_id, from_rev, to_rev, actor, src)"
                        " SELECT line_id, plan_id, rev, %s, %s, %s FROM plan_line WHERE line_id = %s",
                        (rev, who, f"carry:{plan_id}#{rev}", p.line_id))
        new_ids = {(m.sku, m.period): _mint(cur, plan_id, rev, m, who)
                   for m in sorted([n for _, n in rec.replaced] + rec.added)}
        for s in skipped:
            cur.execute("INSERT INTO plan_submit_skip (plan_id, rev, sku, period_start, reason)"
                        " VALUES (%s, %s, %s, %s, %s)",
                        (plan_id, rev, s.sku, dt.date.fromisoformat(s.period + "-01"), s.reason))
        # ★ 铸出 0 条、也没保留任何记录的空版本不许占着在流转位（04 §3.1 的空计划单）
        cur.execute("SELECT open_rev_if_live(%s, %s)", (plan_id, rev))
        in_flight = cur.fetchone()[0]

    counts = {"kept": len(rec.kept), "replaced": len(rec.replaced),
              "added": len(rec.added), "removed": len(rec.removed)}
    if rec.split_frozen or rec.left_completed:
        # ★ 两类「这次的格子与记录不一致、但照样放行」—— 不留痕，下次没人知道它们为什么对不上
        log.info("op=submit plan_id=%s rev=%s split_frozen=%s left_completed=%s detail=%s",
                 plan_id, rev, [p.line_id for p, _ in rec.split_frozen],
                 [p.line_id for p in rec.left_completed],
                 [{"line_id": p.line_id, "from": seller_units(p.demand_by_seller),
                   "to": seller_units(m.demand_by_seller)} for p, m in rec.split_frozen])
    # ★ timed() 的 fields 在进入时就定死，塞不进事务算出来的 rev 与四个数 —— 补这一行事后日志
    log.info("op=submit plan_id=%s rev=%s kept=%d replaced=%d added=%d removed=%d"
             " lines=%d skipped=%d in_flight=%s prev_revs=%s",
             plan_id, rev, *counts.values(), len(lines), len(skipped), in_flight,
             sorted({r for _, r in prev_extra.values()}))
    return {"rev": rev, "lines": len(lines), "in_flight": in_flight,
            "content_digest": digest,
            "skipped": [{"sku": s.sku, "period": s.period, "reason": s.reason}
                        for s in sorted(skipped)],
            "kept": {"count": counts["kept"],
                     "lines": [{"sku": p.sku, "period": p.period, "line_id": p.line_id,
                                "state": p.state, "total_units": p.total_units}
                               for p in rec.kept]},
            "replaced": {"count": counts["replaced"],
                         "lines": [{"sku": p.sku, "period": p.period,
                                    "old_line_id": p.line_id,
                                    "new_line_id": new_ids[(m.sku, m.period)],
                                    "total_units": {"from": p.total_units, "to": m.total_units},
                                    "demand_at_submit": {"from": prev_extra[p.line_id][0],
                                                         "to": m.demand_at_submit}}
                                   for p, m in rec.replaced]},
            "added": {"count": counts["added"],
                      "lines": [{"sku": m.sku, "period": m.period,
                                 "line_id": new_ids[(m.sku, m.period)],
                                 "total_units": m.total_units} for m in rec.added]},
            "removed": {"count": counts["removed"],
                        "lines": [{"sku": p.sku, "period": p.period, "line_id": p.line_id,
                                   "total_units": p.total_units} for p in rec.removed]},
            "split_frozen": {"count": len(rec.split_frozen),
                             "lines": [{"sku": p.sku, "period": p.period, "line_id": p.line_id,
                                        "split": {"from": seller_units(p.demand_by_seller),
                                                  "to": seller_units(m.demand_by_seller)}}
                                       for p, m in rec.split_frozen]},
            "left_completed": {"count": len(rec.left_completed),
                               "lines": [{"sku": p.sku, "period": p.period, "line_id": p.line_id,
                                          "rev": prev_extra[p.line_id][1],
                                          "total_units": p.total_units}
                                         for p in rec.left_completed]}}


@router.get("/plans/{plan_id}/revs")
def revs(plan_id: int, request: Request, who: str | None = Depends(actor_optional)):
    declared(request)
    with timed("revs", actor=who, plan_id=plan_id), pg_conn() as c, c.cursor() as cur:
        ensure_plan(cur, plan_id)
        cur.execute(
            f"WITH m AS ({REV_MEMBERS})"
            " SELECT r.rev, r.content_digest, r.is_current, r.in_flight, r.submitted_by,"
            "       r.submitted_at, count(m.line_id)"
            "  FROM plan_rev r LEFT JOIN m ON m.rev = r.rev"
            " WHERE r.plan_id = %(p)s GROUP BY r.rev, r.content_digest, r.is_current, r.in_flight,"
            "       r.submitted_by, r.submitted_at ORDER BY r.rev DESC", {"p": plan_id})
        rows = cur.fetchall()
    revs_out = [{"rev": r[0], "content_digest": r[1], "is_current": r[2], "in_flight": r[3],
                 "submitted_by": r[4], "submitted_at": r[5].isoformat(), "lines": r[6]}
                for r in rows]
    return {"revs": revs_out,
            "in_flight_rev": next((r["rev"] for r in revs_out if r["in_flight"]), None),
            "current_rev": next((r["rev"] for r in revs_out if r["is_current"]), None)}


@router.post("/plans/{plan_id}/revs/{rev}/current")
def mark_current(plan_id: int, rev: int, request: Request, who: str = Depends(actor)):
    declared(request)
    with timed("mark_current", actor=who, plan_id=plan_id), pg_conn() as c, c.cursor() as cur:
        ensure_writable(cur, plan_id)
        # 先清后立：部分唯一索引不可延迟，同一语句里换不过来
        cur.execute("UPDATE plan_rev SET is_current = false WHERE plan_id = %s AND is_current",
                    (plan_id,))
        cur.execute("UPDATE plan_rev SET is_current = true WHERE plan_id = %s AND rev = %s"
                    " RETURNING rev", (plan_id, rev))
        if cur.fetchone() is None:
            raise ApiError(404, "rev_not_found", "没有这一版",
                           {"plan_id": plan_id, "rev": rev})
    return {"current_rev": rev}


@router.get("/plans/{plan_id}/diff")
def diff(plan_id: int, request: Request, who: str | None = Depends(actor_optional)):
    declared(request, "from", "to")
    try:
        a, b = int(request.query_params["from"]), int(request.query_params["to"])
    except (KeyError, ValueError):
        raise ApiError(400, "bad_request", "from 与 to 必须是版本号",
                       {"got": dict(request.query_params)}) from None
    with timed("diff", actor=who, plan_id=plan_id), pg_conn() as c, c.cursor() as cur:
        ensure_plan(cur, plan_id)
        cur.execute(f"WITH m AS ({REV_MEMBERS})"
                    " SELECT m.rev, l.sku, l.period_start, l.total_units, l.demand_at_submit"
                    "  FROM m JOIN plan_line l USING (line_id) WHERE m.rev IN (%(a)s, %(b)s)",
                    {"p": plan_id, "a": a, "b": b})
        rows = cur.fetchall()
    left = {(r[1], r[2]): r for r in rows if r[0] == a}
    right = {(r[1], r[2]): r for r in rows if r[0] == b}
    changed = [{"sku": k[0], "period": k[1].strftime("%Y-%m"),
                "total_units": {"from": left[k][3], "to": right[k][3]},
                "demand_at_submit": {"from": left[k][4], "to": right[k][4]}}
               for k in sorted(left.keys() & right.keys())
               if left[k][3:] != right[k][3:]]
    fmt = (lambda k, m: {"sku": k[0], "period": k[1].strftime("%Y-%m"),
                         "total_units": m[k][3]})
    return {"added": [fmt(k, right) for k in sorted(right.keys() - left.keys())],
            "removed": [fmt(k, left) for k in sorted(left.keys() - right.keys())],
            "changed": changed}


@router.post("/plans/{plan_id}/revs/{rev}/cancel")
def cancel_rev(plan_id: int, rev: int, body: dict, request: Request,
               who: str = Depends(actor)):
    declared(request)
    reason = (body.get("reason") or "").strip()
    if not reason:
        # ★ 库层的 require_reason() 也会拦，但那会以 500 的形态出去；
        #   这里给的是能让人改表单的 400（08 §0.1：400 = 你写错了）
        raise ApiError(400, "reason_required", "撤销必须填理由", {"field": "reason"})
    with timed("cancel_rev", actor=who, plan_id=plan_id), pg_conn() as c, c.cursor() as cur:
        ensure_writable(cur, plan_id)
        _ensure_rev(cur, plan_id, rev)
        cur.execute("SELECT line_id, state FROM plan_line"
                    " WHERE plan_id = %s AND rev = %s AND state NOT IN %s"
                    # ★ 与再提交同序（line_id 升序）取锁：两边都 FOR UPDATE 同一批行，乱序会死锁
                    " ORDER BY line_id FOR UPDATE",
                    (plan_id, rev, TERMINAL))
        lines = cur.fetchall()
        # ★ 统计被丢掉的那一侧：候选集就是这一版的全部记录。只报 cancelled[]，
        #   「这一版本来就只有 1 条」与「另外 3 条早已在终态」长得一模一样。
        cur.execute("SELECT count(*) FROM plan_line"
                    " WHERE plan_id = %s AND rev = %s AND state IN %s",
                    (plan_id, rev, TERMINAL))
        skipped_terminal = cur.fetchone()[0]
        for line_id, state in lines:
            # ★ A-8：一个理由记在每条上 —— 记在版本上，逐条查历史时就看不到它
            cur.execute("INSERT INTO plan_line_event"
                        " (line_id, from_state, to_state, actor, reason, src)"
                        " VALUES (%s, %s, '已撤销', %s, %s, %s)",
                        (line_id, state, who, reason, f"cancel_rev:{plan_id}#{rev}"))
    log.info("op=cancel_rev plan_id=%s rev=%s cancelled=%d skipped_terminal=%d",
             plan_id, rev, len(lines), skipped_terminal)
    return {"cancelled": [line_id for line_id, _ in lines],
            "skipped_terminal": skipped_terminal, "reason": reason}
