"""提交与版本。★ 提交是**一个事务**铸出全部记录 —— 半铸的计划单无法解释（01 §5）。"""
from __future__ import annotations

import datetime as dt
import json
import logging

import psycopg2
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
from rules.submit import DemandCell, PurchaseCell, select_submittable
from shared.pg_client import pg_conn, timed

log = logging.getLogger("scm.api")

router = APIRouter(dependencies=[Depends(require_fresh_mirrors)])

TERMINAL = ("已完结", "已撤销")


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


@router.post("/plans/{plan_id}/submit")
def submit(plan_id: int, request: Request, who: str = Depends(actor)):
    declared(request)
    with timed("submit", actor=who, plan_id=plan_id), pg_conn() as c, c.cursor() as cur:
        ensure_writable(cur, plan_id)
        purchase, demand, claimed = _cells(cur, plan_id)
        lines, skipped = select_submittable(purchase, demand, claimed)
        digest = content_digest(purchase, demand)

        cur.execute("SELECT coalesce(max(rev), 0) + 1 FROM plan_rev WHERE plan_id = %s",
                    (plan_id,))
        rev = cur.fetchone()[0]
        try:
            cur.execute("INSERT INTO plan_rev (plan_id, rev, content_digest, submitted_by)"
                        " VALUES (%s, %s, %s, %s)", (plan_id, rev, digest, who))
        except psycopg2.errors.UniqueViolation:
            # ★ 撞的是「同一计划只许 1 版在流转」那条部分唯一索引（S-4）。
            #   回滚后另起一条连接去查是哪一版 —— 只说「有一版在流转」，人得自己去翻。
            c.rollback()
            with pg_conn() as c2, c2.cursor() as cur2:
                cur2.execute("SELECT rev, submitted_by, submitted_at FROM plan_rev"
                             " WHERE plan_id = %s AND in_flight", (plan_id,))
                old = cur2.fetchone()
            raise ApiError(409, "rev_in_flight", "该计划已有一版在流转",
                           {"plan_id": plan_id,
                            "in_flight_rev": old[0] if old else None,
                            "submitted_by": old[1] if old else None,
                            "submitted_at": old[2].isoformat() if old else None}) from None

        for m in lines:
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
        for s in skipped:
            cur.execute("INSERT INTO plan_submit_skip (plan_id, rev, sku, period_start, reason)"
                        " VALUES (%s, %s, %s, %s, %s)",
                        (plan_id, rev, s.sku, dt.date.fromisoformat(s.period + "-01"), s.reason))

        # ★ 铸出 0 条记录的空版本没有任何行能触发 t_rev_in_flight，
        #   不在这里收一次，它会永远占着「唯一在流转」那个位子（04 §3.1 的空计划单）
        cur.execute("SELECT close_rev_if_settled(%s, %s)", (plan_id, rev))
        cur.execute("SELECT in_flight FROM plan_rev WHERE plan_id = %s AND rev = %s",
                    (plan_id, rev))
        in_flight = cur.fetchone()[0]

    # ★ timed() 的 fields 在进入时就定死，塞不进事务算出来的 rev/lines/skipped ——
    #   补这一行事后日志，否则翻日志看得到「谁提交失败了」看不到「铸出的是第几版」
    log.info("op=submit plan_id=%s rev=%s lines=%d skipped=%d in_flight=%s",
             plan_id, rev, len(lines), len(skipped), in_flight)
    return {"rev": rev, "lines": len(lines), "in_flight": in_flight,
            "content_digest": digest,
            "skipped": [{"sku": s.sku, "period": s.period, "reason": s.reason}
                        for s in sorted(skipped)]}


@router.get("/plans/{plan_id}/revs")
def revs(plan_id: int, request: Request, who: str | None = Depends(actor_optional)):
    declared(request)
    with timed("revs", actor=who, plan_id=plan_id), pg_conn() as c, c.cursor() as cur:
        ensure_plan(cur, plan_id)
        cur.execute(
            "SELECT r.rev, r.content_digest, r.is_current, r.in_flight, r.submitted_by,"
            "       r.submitted_at, count(l.line_id)"
            "  FROM plan_rev r LEFT JOIN plan_line l ON l.plan_id = r.plan_id AND l.rev = r.rev"
            " WHERE r.plan_id = %s GROUP BY r.rev, r.content_digest, r.is_current, r.in_flight,"
            "       r.submitted_by, r.submitted_at ORDER BY r.rev DESC", (plan_id,))
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
        cur.execute("SELECT rev, sku, period_start, total_units, demand_at_submit"
                    " FROM plan_line WHERE plan_id = %s AND rev IN (%s, %s)", (plan_id, a, b))
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
                    " WHERE plan_id = %s AND rev = %s AND state NOT IN %s FOR UPDATE",
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
