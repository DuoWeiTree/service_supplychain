"""计划：建 · 列 · 认领 · 网格 · 两种量 · 归档。"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from api.ui.deps import actor, actor_optional, declared, require_fresh_mirrors
from api.ui.errors import ApiError
from shared.pg_client import pg_conn, timed

router = APIRouter(dependencies=[Depends(require_fresh_mirrors)])


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
