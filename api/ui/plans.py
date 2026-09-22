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
from forecast.projection import inventory_projection
from rules.effective import effective_demand
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


def _ym(d: dt.date) -> str:
    return d.strftime("%Y-%m")


def _load_grid(cur, plan_id: int):
    periods = _periods(cur, plan_id)
    cur.execute(
        "SELECT d.seller_sku, d.sid, b.sku, d.period_start, d.system_units,"
        "       d.system_extrapolated, d.expected_units, s.has_fba"
        "  FROM plan_demand_cell d"
        "  JOIN msku_bridge b ON b.seller_sku = d.seller_sku AND b.sid = d.sid"
        "  JOIN seller s ON s.seller_id = d.sid"
        " WHERE d.plan_id = %s ORDER BY b.sku, d.seller_sku, d.sid, d.period_start", (plan_id,))
    demand = cur.fetchall()
    cur.execute("SELECT sku, period_start, planned_units FROM plan_purchase_cell"
                " WHERE plan_id = %s ORDER BY sku, period_start", (plan_id,))
    purchase = cur.fetchall()
    return periods, demand, purchase


@router.get("/plans/{plan_id}/grid")
def grid(plan_id: int, request: Request, who: str | None = Depends(actor_optional)):
    declared(request)
    with timed("grid", actor=who, plan_id=plan_id), pg_conn() as c, c.cursor() as cur:
        periods, demand, purchase = _load_grid(cur, plan_id)

    out_demand: list[dict] = []
    #: (sku, sid) → {period: 该店该货号各 msku 的期望销量之和}
    demand_by_store: dict[tuple[str, str], dict[str, int | None]] = {}
    #: (sku, sid) → 在仓合计。★ 该店无 FBA / 各 msku 全无在仓数据 → None（不适用），不是 0
    onhand_by_store: dict[tuple[str, str], int | None] = {}
    # ★ demand 每个 msku 有 N 个月份行；在仓事实与月份无关，只能对每个 msku 累加一次 ——
    #   否则一个 3 个月的计划会把在仓数算成 3 倍。
    onhand_added: set[tuple[str, str]] = set()

    for seller_sku, sid, sku, period, sysu, extrap, expu, fba in demand:
        eff = effective_demand(sysu, expu)
        out_demand.append({"seller_sku": seller_sku, "sid": sid, "period": _ym(period),
                           "system_units": sysu, "expected_units": expu, "basis": eff.basis,
                           # ★ 外推 ≠ 预估，标记必须随数走（14 §5）
                           "system_extrapolated": extrap})
        key, ym = (sku, sid), _ym(period)
        slot = demand_by_store.setdefault(key, {})
        # ★ 合计行 = 各 msku 之和（14 §1 ①）；只要有一个 msku 未知，这一格就是未知
        if ym not in slot:
            slot[ym] = eff.units
        elif slot[ym] is not None and eff.units is not None:
            slot[ym] += eff.units
        else:
            slot[ym] = None
        if key not in onhand_by_store:
            onhand_by_store[key] = None
        if fba and (seller_sku, sid) not in onhand_added:
            onhand_added.add((seller_sku, sid))
            got = SOURCE.onhand_available(seller_sku, sid)
            if got is not None:
                onhand_by_store[key] = (onhand_by_store[key] or 0) + got

    in_transit: dict[str, list] = {sku: SOURCE.purchase_in_transit(sku)
                                   for sku in {r[0] for r in purchase}}
    as_of = SOURCE.as_of().isoformat()

    inventory = []
    for (sku, sid), by_month in sorted(demand_by_store.items()):
        sku_total_by_period = {p: sum(t.units for t in in_transit.get(sku, ()) if t.period == p)
                               for p in by_month}
        # ★ 采购在途一律不进任何店铺的加项（阶段 A 恒 null）：它是货号级的，而
        #   「计划内只有一家店认领」不等于它是这批货的唯一消费者 —— 同一货号可能被
        #   本计划之外的店铺在卖。按单店给全额与平摊一样，都是发明分配规则（C4 禁）。
        #   所以推演的入库项是空的，closing = onhand − demand。
        rows = inventory_projection(onhand_by_store[(sku, sid)], {}, by_month)
        for row in rows:
            inventory.append({
                "sku": sku, "sid": sid, "period": row["period"],
                # onhand = 这一格的期初：第一个月是当前在仓事实，其后是上月期末
                "onhand": row["opening"],
                "inbound": None,
                "closing": row["closing"],
                "basis": {
                    "source": "ch", "as_of": as_of,
                    # ★ 裁定：恒 false 且必须显式返回 —— 「没算进来」不能长成「算了是 0」
                    "includes_plan_purchase": False,
                    "demand": row["demand"],
                    # ★ 恒定：这一格的入库为什么是 null
                    "reason": "no_seller_attribution",
                    # ★ 与上面那条分开：这一格的期末为什么是 null（没有就是 None）。
                    #   两件事挤进一个字段，「既未知又恒定」的那一格只说得出一件
                    "closing_reason": row["basis"].get("reason"),
                    # ★ 店铺级入库的逐笔依据。阶段 A 无数据源 ⇒ 恒空，而它必须与下面
                    #   非零的 sku_level_in_transit 并排出现：否则「没有货」和
                    #   「有货但不知道是谁的」长得一模一样
                    "sources": [],
                    "sku_level_in_transit": sku_total_by_period[row["period"]],
                },
            })

    pipeline: dict[tuple[str, str], dict] = {}
    for sku, rows in sorted(in_transit.items()):
        for t in rows:
            slot = pipeline.setdefault((sku, t.period),
                                       {"sku": sku, "period": t.period, "units": 0,
                                        "sources": [], "no_seller_attribution": True})
            slot["units"] += t.units
            slot["sources"].append({"units": t.units, "kind": "purchase_in_transit", "ref": t.ref})

    return {
        "plan_id": plan_id,
        "periods": [_ym(p) for p in periods],
        "demand": out_demand,
        "purchase": [{"sku": s, "period": _ym(p), "planned_units": u} for s, p, u in purchase],
        "inventory": inventory,
        "sku_pipeline": [pipeline[k] for k in sorted(pipeline)],
    }


def _units(body: dict, field: str) -> int | None:
    v = body.get(field, None)
    if v is None:
        return None                       # ★ 留空 = 未知（M-8），是合法输入
    if not isinstance(v, int) or isinstance(v, bool) or v < 0:
        raise ApiError(400, "bad_units", f"{field} 必须是 ≥0 的整数或 null",
                       {"field": field, "got": v})
    return v


@router.put("/plans/{plan_id}/demand/{seller_sku}/{sid}/{period}")
def put_demand(plan_id: int, seller_sku: str, sid: str, period: str, body: dict,
               who: str = Depends(actor)):
    units = _units(body, "expected_units")
    # ★ 一格一事务：重算（预测）放在事务外 —— 预测慢，不该把行锁攥着（01 §5）
    with timed("put_demand", actor=who, plan_id=plan_id), pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE plan_demand_cell SET expected_units = %s, updated_by = %s,"
                    " updated_at = now()"
                    " WHERE plan_id = %s AND seller_sku = %s AND sid = %s AND period_start = %s"
                    " RETURNING system_units, system_extrapolated",
                    (units, who, plan_id, seller_sku, sid, _date(period + "-01", "period")))
        row = cur.fetchone()
    if row is None:
        raise ApiError(404, "cell_not_found", "这一格还没长出来（先认领这个 msku）",
                       {"plan_id": plan_id, "seller_sku": seller_sku, "sid": sid,
                        "period": period})
    eff = effective_demand(row[0], units)
    # ★ 回的是 grid 里 demand[] 那一格同样的形状 —— 前端拿它原地替换那一行，
    #   形状不同就要在前端再写一遍映射，而两份映射迟早分叉
    return {"cell": {"seller_sku": seller_sku, "sid": sid, "period": period,
                     "system_units": row[0], "expected_units": units,
                     "basis": eff.basis, "system_extrapolated": row[1]}}


@router.put("/plans/{plan_id}/purchase/{sku}/{period}")
def put_purchase(plan_id: int, sku: str, period: str, body: dict, who: str = Depends(actor)):
    units = _units(body, "planned_units")
    with timed("put_purchase", actor=who, plan_id=plan_id), pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE plan_purchase_cell SET planned_units = %s, updated_by = %s,"
                    " updated_at = now()"
                    " WHERE plan_id = %s AND sku = %s AND period_start = %s RETURNING 1",
                    (units, who, plan_id, sku, _date(period + "-01", "period")))
        hit = cur.fetchone()
    if hit is None:
        raise ApiError(404, "cell_not_found", "这一格还没长出来（先认领该货号下的 msku）",
                       {"plan_id": plan_id, "sku": sku, "period": period})
    return {"cell": {"sku": sku, "period": period, "planned_units": units}}
