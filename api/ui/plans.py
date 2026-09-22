"""计划：建 · 列 · 认领 · 网格 · 两种量 · 归档。"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import psycopg2
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from api.ui.deps import actor, actor_optional, declared, require_fresh_mirrors
from api.ui.errors import ApiError
from dim.fixture_source import FixtureSource
from forecast.estimate import InsufficientHistory, monthly_estimate
from shared.pg_client import pg_conn, timed

router = APIRouter(dependencies=[Depends(require_fresh_mirrors)])

#: 阶段 A 的取数源。★ 阶段 B 换成 ChSource 时改的只有这一行（dim/source.py 的协议不变）。
SOURCE = FixtureSource(Path(__file__).resolve().parents[2] / "tests" / "fixtures")


def _date(s: str, field: str) -> dt.date:
    try:
        return dt.date.fromisoformat(s)
    except (TypeError, ValueError):
        raise ApiError(400, "bad_date", f"{field} 不是 YYYY-MM-DD", {"field": field,
                                                                     "got": s}) from None


@router.post("/plans")
def create_plan(request: Request, body: dict, who: str = Depends(actor)):
    title = (body.get("title") or "").strip()
    if not title:
        raise ApiError(400, "title_required", "标题必填", {"field": "title"})
    start = _date(body.get("period_start"), "period_start")
    months = body.get("months", 3)          # ★ M-10：默认 3，范围 1~24 由库层 CHECK 裁决
    with timed("create_plan", actor=who), pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO plan (title, period_start, months, owner_actor, created_by)"
                    " VALUES (%s, %s, %s, %s, %s) RETURNING plan_id",
                    (title, start, months, body.get("owner_actor", who), who))
        plan_id = cur.fetchone()[0]
    return JSONResponse(status_code=201, content={"plan_id": plan_id})


@router.get("/plans")
def list_plans(request: Request, who: str | None = Depends(actor_optional)):
    declared(request, "state", "owner", "archived")
    q = request.query_params
    want_archived = q.get("archived", "false").lower() == "true"
    where, args = ["(%s OR p.archived_at IS NULL)"], [want_archived]
    if q.get("owner"):
        where.append("p.owner_actor = %s")
        args.append(q["owner"])
    if q.get("state"):
        where.append("v.overall = %s")
        args.append(q["state"])
    sql = ("SELECT p.plan_id, p.title, p.period_start, p.months, p.owner_actor,"
           " p.archived_at, v.overall, v.state_rev"
           " FROM plan p JOIN v_plan_overall_state v USING (plan_id)"
           f" WHERE {' AND '.join(where)} ORDER BY p.plan_id DESC")
    with timed("list_plans", actor=who), pg_conn() as c, c.cursor() as cur:
        cur.execute(sql, args)
        # ★ 对外的字段名是 state（裁定第 3 条）；库里的列叫 overall，
        #   两边同名反而会让人以为它是张表上的字段 —— 它是派生的
        plans = [{"plan_id": r[0], "title": r[1], "period_start": r[2].isoformat(),
                  "months": r[3], "owner_actor": r[4],
                  "archived_at": r[5].isoformat() if r[5] else None,
                  "state": r[6], "state_rev": r[7]} for r in cur.fetchall()]
        # ★ 统计被丢掉的那一侧：不说「挡掉了几张」，人只会觉得计划凭空少了
        cur.execute("SELECT count(*) FROM plan WHERE archived_at IS NOT NULL")
        archived = 0 if want_archived else cur.fetchone()[0]
    return {"plans": plans, "excluded": {"archived": archived}}


def _periods(cur, plan_id: int) -> list[dt.date]:
    cur.execute("SELECT period_start, months FROM plan WHERE plan_id = %s", (plan_id,))
    row = cur.fetchone()
    if row is None:
        raise ApiError(404, "plan_not_found", "计划不存在", {"plan_id": plan_id})
    start, months = row
    return [dt.date(start.year + (start.month - 1 + i) // 12,
                    (start.month - 1 + i) % 12 + 1, 1) for i in range(months)]


@router.post("/plans/{plan_id}/claims")
def claim(plan_id: int, body: dict, who: str = Depends(actor)):
    seller_sku, sid = body.get("seller_sku"), body.get("sid")
    if not seller_sku or not sid:
        raise ApiError(400, "bad_request", "seller_sku 与 sid 必填",
                       {"got": {"seller_sku": seller_sku, "sid": sid}})
    with timed("claim", actor=who, plan_id=plan_id), pg_conn() as c, c.cursor() as cur:
        periods = _periods(cur, plan_id)
        cur.execute("SELECT sku FROM msku_bridge WHERE seller_sku = %s AND sid = %s",
                    (seller_sku, sid))
        row = cur.fetchone()
        if row is None:
            raise ApiError(404, "unknown_msku", "msku 不在桥表里",
                           {"seller_sku": seller_sku, "sid": sid})
        sku = row[0]

        # ★ 先查后写挡不住并发，所以这里不查 —— 直接插，让部分唯一索引裁决；
        #   撞上了再回头查是谁占的，只为把 409 的 detail 点到名。
        try:
            cur.execute(
                "INSERT INTO msku_claim (plan_id, seller_sku, sid, claimed_by)"
                " VALUES (%s, %s, %s, %s)"
                " ON CONFLICT (plan_id, seller_sku, sid) DO UPDATE"
                "    SET released_at = NULL, released_by = NULL,"
                "        claimed_by = EXCLUDED.claimed_by, claimed_at = now()",
                (plan_id, seller_sku, sid, who))
        except psycopg2.errors.UniqueViolation:
            c.rollback()
            with pg_conn() as c2, c2.cursor() as cur2:
                cur2.execute("SELECT plan_id, claimed_by FROM msku_claim"
                             " WHERE seller_sku = %s AND sid = %s"
                             "   AND released_at IS NULL AND NOT plan_ordered",
                             (seller_sku, sid))
                holder = cur2.fetchone()
            raise ApiError(409, "msku_already_claimed", "该 msku 已被另一张尚未下单的计划占用",
                           {"seller_sku": seller_sku, "sid": sid,
                            "claimed_by": {"plan_id": holder[0], "actor": holder[1]}
                            if holder else None}) from None

        no_history = []
        try:
            est = monthly_estimate(SOURCE.monthly_sales_history(seller_sku, sid, len(periods)),
                                   len(periods))
        except InsufficientHistory:
            # ★ 没有历史 ≠ 预估 0：格子照建（人还要在上面填），system_units 留 NULL 并点名
            est = None
            no_history.append({"seller_sku": seller_sku, "sid": sid,
                               "reason": "no_sales_history"})
        for i, period in enumerate(periods):
            cur.execute(
                "INSERT INTO plan_demand_cell (plan_id, seller_sku, sid, period_start,"
                " system_units, system_extrapolated, updated_by)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (plan_id, seller_sku, sid, period_start) DO UPDATE"
                "    SET system_units = EXCLUDED.system_units,"
                "        system_extrapolated = EXCLUDED.system_extrapolated",
                (plan_id, seller_sku, sid, period,
                 est[i].units if est else None, bool(est and est[i].system_extrapolated), who))
        for period in periods:
            # ★ 货号级采购格子必须先长出来：没有行和填了 0 不能长得一样，
            #   否则提交时它连一条 skipped 都留不下（判据②）
            cur.execute("INSERT INTO plan_purchase_cell (plan_id, sku, period_start, updated_by)"
                        " VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                        (plan_id, sku, period, who))
    return {"claimed": {"seller_sku": seller_sku, "sid": sid, "sku": sku},
            "seeded": {"demand_cells": len(periods), "purchase_cells": len(periods)},
            "no_history": no_history}


@router.delete("/plans/{plan_id}/claims/{seller_sku}/{sid}")
def release(plan_id: int, seller_sku: str, sid: str, who: str = Depends(actor)):
    with timed("release_claim", actor=who, plan_id=plan_id), pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT period_start, expected_units FROM plan_demand_cell"
                    " WHERE plan_id = %s AND seller_sku = %s AND sid = %s ORDER BY period_start",
                    (plan_id, seller_sku, sid))
        dropped = [{"period": p.strftime("%Y-%m"), "expected_units": u} for p, u in cur.fetchall()]
        cur.execute("DELETE FROM plan_demand_cell"
                    " WHERE plan_id = %s AND seller_sku = %s AND sid = %s",
                    (plan_id, seller_sku, sid))
        cur.execute("UPDATE msku_claim SET released_at = now(), released_by = %s"
                    " WHERE plan_id = %s AND seller_sku = %s AND sid = %s AND released_at IS NULL"
                    " RETURNING plan_id", (who, plan_id, seller_sku, sid))
        if cur.fetchone() is None:
            raise ApiError(404, "claim_not_found", "没有这条在占用中的认领",
                           {"plan_id": plan_id, "seller_sku": seller_sku, "sid": sid})
    # ★ 丢东西必须有声：删掉的格子逐条回给界面，包括人填过的数
    return {"released": {"seller_sku": seller_sku, "sid": sid}, "dropped_cells": dropped}
