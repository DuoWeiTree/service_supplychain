"""计划：建 · 列 · 认领 · 网格 · 两种量 · 归档。"""
from __future__ import annotations

import datetime as dt
import json
import logging
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from api.common.deps import (
    actor,
    actor_optional,
    declared,
    ensure_plan,
    ensure_writable,
    known_states,
    require_fresh_mirrors,
)
from api.common.errors import ApiError
from api.common.source_errors import SOURCE_ERRORS, source_error
from api.common.source_factory import source_dep
from dim.ch_source import (
    ChDataUnusable,
    ChUnavailable,
    UnknownShape,
)
from dim.order_store_map import UnknownStore
from dim.source import Source
from shared.pg_client import pg_conn, timed
from usecase import plan_claim
from usecase.plan_projection import demand_row, project

log = logging.getLogger("scm.api")

router = APIRouter(dependencies=[Depends(require_fresh_mirrors)])

#: 取数源由 `Depends(source_dep)` **每请求**装配一次（api/common/source_factory.py）。
#: ★ 终审 C-1/C-2/C-3：这里原先是一个模块级单例 `SOURCE = make_source()`。它把
#:   一个 CH 客户端和 `ChSource` 全部可变的每次调用状态（两份缓存、`stats()`、
#:   `history_window()`、`purchase_as_of`）钉在进程上，而 grid()/claim() 是
#:   plain def 端点、跑在 Starlette 的线程池里 —— 于是并发请求会互相踩。




def _date(s: str, field: str) -> dt.date:
    try:
        return dt.date.fromisoformat(s)
    except (TypeError, ValueError):
        raise ApiError(400, "bad_date", f"{field} 不是 YYYY-MM-DD", {"field": field,
                                                                     "got": s}) from None


@router.post("/plans")
def create_plan(request: Request, body: dict, who: str = Depends(actor)):
    declared(request)
    title = (body.get("title") or "").strip()
    if not title:
        raise ApiError(400, "title_required", "标题必填", {"field": "title"})
    start = _date(body.get("period_start"), "period_start")
    months = body.get("months", 3)          # ★ M-10：默认 3，范围 1~24 由库层 CHECK 裁决
    # ★ 库层的 CHECK 管的是**范围**不是**类型**：字符串会以 22P02 撞成 500（让前端去
    #   重试一件永远不会成功的事），而 3.5 会被 PG 悄悄四舍五入成 4 —— 一个人没要过的值，
    #   且没有任何回显。范围仍归 CHECK，类型在这里挡。
    if not isinstance(months, int) or isinstance(months, bool):
        raise ApiError(400, "validation_error", "按 fields 逐项改",
                       {"fields": ["months"], "got": months})
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
    with timed("list_plans", actor=who), pg_conn() as c, c.cursor() as cur:
        # ★ state= 打错字不许悄悄返回空集 —— 那与「这个范围里确实没有」长得一模一样。
        #   宇宙与 /v1/plan-lines 同一个出处（deps.known_states），不另立一份。
        if q.get("state"):
            known = known_states(cur)
            if q["state"] not in known:
                raise ApiError(400, "bad_state", "state 不是白名单里的状态值",
                               {"got": q["state"], "allowed": known})
        # ★ 候选集 = owner/state 圈定的那一批。archived 的排除数只能在候选集里数：
        #   在全库上数的话，按 owner 过滤时报的是别人计划的归档数，而那不是这次查询丢的。
        where, args = [], []
        if q.get("owner"):
            where.append("p.owner_actor = %s")
            args.append(q["owner"])
        if q.get("state"):
            where.append("v.overall = %s")
            args.append(q["state"])
        candidates = " AND ".join(where) if where else "true"
        cur.execute(
            "SELECT p.plan_id, p.title, p.period_start, p.months, p.owner_actor,"
            " p.archived_at, v.overall, v.state_rev"
            " FROM plan p JOIN v_plan_overall_state v USING (plan_id)"
            f" WHERE (%s OR p.archived_at IS NULL) AND {candidates}"
            " ORDER BY p.plan_id DESC", [want_archived, *args])
        # ★ 对外的字段名是 state（裁定第 3 条）；库里的列叫 overall，
        #   两边同名反而会让人以为它是张表上的字段 —— 它是派生的
        plans = [{"plan_id": r[0], "title": r[1], "period_start": r[2].isoformat(),
                  "months": r[3], "owner_actor": r[4],
                  "archived_at": r[5].isoformat() if r[5] else None,
                  "state": r[6], "state_rev": r[7]} for r in cur.fetchall()]
        # ★ 统计被丢掉的那一侧：不说「挡掉了几张」，人只会觉得计划凭空少了
        cur.execute("SELECT count(*) FROM plan p JOIN v_plan_overall_state v USING (plan_id)"
                    f" WHERE p.archived_at IS NOT NULL AND {candidates}", args)
        archived = 0 if want_archived else cur.fetchone()[0]
    return {"plans": plans, "excluded": {"archived": archived}}


def _claimed_elsewhere(seller_sku: str, sid: str, holder) -> ApiError:
    return ApiError(409, "msku_already_claimed", "该 msku 已被另一张尚未下单的计划占用",
                    {"seller_sku": seller_sku, "sid": sid,
                     "claimed_by": {"plan_id": holder[0], "actor": holder[1], "title": holder[2]}
                     if holder else None})


#: 越线是我们自己的代码错了 → 500（见 plan_claim.StockWindowPastAnchor）
STOCK_WINDOW_PAST_ANCHOR = "stock_window_past_anchor"

#: 认领 / 重算取数时 usecase 原样上抛的异常（PurchaseTableStale 不在内：认领不碰采购表）
_SEED_ERRORS = (ChUnavailable, ChDataUnusable, UnknownShape, UnknownStore,
                plan_claim.StockWindowPastAnchor)


def _seed_error(e: Exception) -> ApiError:
    if isinstance(e, plan_claim.StockWindowPastAnchor):
        return ApiError(500, STOCK_WINDOW_PAST_ANCHOR,
                        "断货日窗口越过了估计锚点 —— 未结算的采集日会被读成断货，拒绝估计",
                        {"until": e.until.isoformat(), "anchor": e.anchor.isoformat(),
                         "as_of": e.as_of.isoformat(), "embargo_days": e.embargo_days})
    return source_error(e)


@router.post("/plans/{plan_id}/claims")
def claim(plan_id: int, body: dict, request: Request,
          source: Annotated[Source, Depends(source_dep)],
          who: str = Depends(actor)):
    declared(request)
    seller_sku, sid = body.get("seller_sku"), body.get("sid")
    if not seller_sku or not sid:
        raise ApiError(400, "bad_request", "seller_sku 与 sid 必填",
                       {"got": {"seller_sku": seller_sku, "sid": sid}})
    with timed("claim", actor=who, plan_id=plan_id), pg_conn() as c, c.cursor() as cur:
        ensure_writable(cur, plan_id)
        try:
            return plan_claim.claim_msku(cur, source, plan_id, seller_sku, sid, who)
        except plan_claim.UnknownMsku:
            raise ApiError(404, "unknown_msku", "msku 不在桥表里",
                           {"seller_sku": seller_sku, "sid": sid}) from None
        except plan_claim.ClaimedElsewhere as e:
            holder = e.holder
            if holder is None:
                # 撞唯一索引：事务已中止，回滚后再查是谁占的，只为把 409 的 detail 点到名。
                # ★ 新开连接：pg_conn() 的 SET search_path 在同一事务里，回滚会把它一起撤掉
                c.rollback()
                with pg_conn() as c2, c2.cursor() as cur2:
                    holder = plan_claim.claim_holder(cur2, seller_sku, sid)
            raise _claimed_elsewhere(seller_sku, sid, holder) from None
        except _SEED_ERRORS as e:
            raise _seed_error(e) from e


@router.post("/plans/{plan_id}/forecast/recompute")
def recompute_forecast(plan_id: int, request: Request,
                       source: Annotated[Source, Depends(source_dep)],
                       who: str = Depends(actor)):
    """已认领的格子重新算一次预测销量（task-23，W-7 的另一半）。

    ★ 根因：`claim()` 只在认领那一刻 seed 一次 `system_units`/`system_basis`，换源之后
    已经存在的认领不会自己回头再算 —— 换源前建的计划，取不到真实销量历史时
    `system_units` 留了 NULL（这是对的：没有历史 ≠ 0），但换源后这些格子永远不会
    自己变成已知，桥接段也就跟着永远未知（实测：计划 1 的 36 格里 18 格
    `('unknown','bridge_rate_unknown')`）。
    ★ 绝不改 `expected_units`（人填的）——本任务第一判据，下面的 UPDATE 语句
      压根不带这一列。
    ★ 与 `claim()` 同一份算法（`plan_claim.seed_forecast` + `cell_values`），不许另写一份——
      两份迟早分叉，本仓已经在 `_demand_row` 上踩过这个坑。
    ★ 一个事务：`pg_conn()` 在块内异常时统一回滚，取数中途失败（CH 挂了 / 掉档 /
      采购表陈旧）不会留下半批已经写完的格子——不必再手写一层事务边界。
    ★ 只读态计划按 `ensure_writable` 既有的 404/409 形状拒绝，不新开错误码。
    """
    declared(request)
    t0 = time.perf_counter()
    with timed("recompute_forecast", actor=who, plan_id=plan_id), pg_conn() as c, c.cursor() as cur:
        ensure_writable(cur, plan_id)
        periods = plan_claim.plan_periods(cur, plan_id)
        # ★ 与 claim() 同一个判据：只对「在占用中」的认领重算（released_at IS NULL）
        cur.execute(
            "SELECT m.seller_sku, m.sid, s.platform FROM msku_claim m"
            " JOIN msku_bridge b ON b.seller_sku = m.seller_sku AND b.sid = m.sid"
            " JOIN seller s ON s.seller_id = m.sid"
            " WHERE m.plan_id = %s AND m.released_at IS NULL"
            " ORDER BY b.sku, m.seller_sku, m.sid", (plan_id,))
        claims = cur.fetchall()
        cur.execute(
            "SELECT seller_sku, sid, period_start, system_units FROM plan_demand_cell"
            " WHERE plan_id = %s", (plan_id,))
        before = {(r[0], r[1], r[2]): r[3] for r in cur.fetchall()}

        recomputed: list[dict] = []
        no_rate: list[dict] = []
        unchanged = 0
        for seller_sku, sid, platform in claims:
            try:
                seeded = plan_claim.seed_forecast(source, plan_id, seller_sku, sid, platform)
            except _SEED_ERRORS as e:
                raise _seed_error(e) from e
            rate, extrapolated, basis = seeded["rate"], seeded["extrapolated"], seeded["basis"]
            # ★ 判据⑥：「算不出速率」与「算出来了」分开列，不合成一个成功计数
            if seeded["no_history"] is not None:
                no_rate.append(seeded["no_history"])
            for period in periods:
                units, cell_basis = plan_claim.cell_values(rate, basis, period)
                cur.execute(
                    "UPDATE plan_demand_cell SET system_units = %s,"
                    "  system_extrapolated = %s, system_basis = %s"
                    " WHERE plan_id = %s AND seller_sku = %s AND sid = %s AND period_start = %s",
                    (units, extrapolated, None if cell_basis is None else json.dumps(cell_basis),
                     plan_id, seller_sku, sid, period))
                prior = before.get((seller_sku, sid, period))
                if prior == units:
                    unchanged += 1
                else:
                    recomputed.append({"seller_sku": seller_sku, "sid": sid,
                                       "period": _ym(period), "before": prior, "after": units})
    elapsed_ms = (time.perf_counter() - t0) * 1000
    # ★ timed() 的 fields 在进入时就定死，塞不进事务里才算出来的计数——同 archive() 的做法，
    #   补这一行事后日志：plan_id / 认领数 / 变化格子数 / 算不出速率的数 / 耗时缺一个都查不了。
    log.info("op=recompute_forecast plan_id=%s actor=%s claims=%d recomputed=%d no_rate=%d"
             " unchanged=%d elapsed_ms=%.0f",
             plan_id, who, len(claims), len(recomputed), len(no_rate), unchanged, elapsed_ms)
    return {"recomputed": recomputed, "unchanged": unchanged, "no_rate": no_rate}


def _drop_purchase_cells(cur, plan_id: int, sku: str) -> list[dict]:
    """删掉一个货号在本计划里的全部采购格子，逐格带值回报（null 照报：空 ≠ 0）。

    ★ 业主裁定 2026-09-27（推翻 F5「搁浅采购格未删、待处理」）：货号没有 msku 了，
      货号本身也要移出 —— 留着的采购格会让网格把货号行长回来。删可以，但必须有声。"""
    cur.execute("DELETE FROM plan_purchase_cell WHERE plan_id = %s AND sku = %s"
                " RETURNING period_start, planned_units", (plan_id, sku))
    return [{"sku": sku, "period": p.strftime("%Y-%m"), "planned_units": u}
            for p, u in sorted(cur.fetchall())]


def _cells_log(cells: list[dict]) -> str:
    return ",".join(f"{c['period']}:{c['planned_units']}" for c in cells) or "-"


def _sku_of(cur, plan_id: int, seller_sku: str, sid: str) -> str | None:
    cur.execute("SELECT sku FROM msku_bridge WHERE seller_sku = %s AND sid = %s",
                (seller_sku, sid))
    row = cur.fetchone()
    if row is None:
        # ★ 认不出货号不许静默当成「没兄弟」去删 —— 报出来，采购格不动
        log.warning("op=release_claim plan_id=%s seller_sku=%s sid=%s 桥表无此 msku —— "
                    "无法判定货号是否随之移出，采购格未动", plan_id, seller_sku, sid)
        return None
    return row[0]


def _has_active_msku(cur, plan_id: int, sku: str) -> bool:
    cur.execute("SELECT count(*) FROM msku_claim cl"
                " JOIN msku_bridge b ON b.seller_sku = cl.seller_sku AND b.sid = cl.sid"
                " WHERE cl.plan_id = %s AND cl.released_at IS NULL AND b.sku = %s",
                (plan_id, sku))
    return cur.fetchone()[0] > 0


@router.delete("/plans/{plan_id}/claims/{seller_sku}/{sid}")
def release(plan_id: int, seller_sku: str, sid: str, request: Request,
            who: str = Depends(actor)):
    declared(request)
    with timed("release_claim", actor=who, plan_id=plan_id), pg_conn() as c, c.cursor() as cur:
        ensure_writable(cur, plan_id)
        sku = _sku_of(cur, plan_id, seller_sku, sid)
        if sku is not None:
            plan_claim.lock_sku(cur, plan_id, sku)
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
        # ★ 必须在释放之后算：之前算的话，刚释放的这一个自己还算在「兄弟」里
        sku_removed = sku is not None and not _has_active_msku(cur, plan_id, sku)
        dropped_purchase = _drop_purchase_cells(cur, plan_id, sku) if sku_removed else []
    log.info("op=release_claim plan_id=%s actor=%s seller_sku=%s sid=%s sku=%s dropped=%d"
             " sku_removed=%s dropped_purchase=%s",
             plan_id, who, seller_sku, sid, sku, len(dropped), sku_removed,
             _cells_log(dropped_purchase))
    # ★ 丢东西必须有声：删掉的格子逐条回给界面，包括人填过的数
    return {"released": {"seller_sku": seller_sku, "sid": sid}, "dropped_cells": dropped,
            "dropped_purchase_cells": dropped_purchase}


@router.delete("/plans/{plan_id}/skus/{sku}")
def remove_sku(plan_id: int, sku: str, request: Request, who: str = Depends(actor)):
    """移出货号：跨店释放它的全部 msku + 删期望销量格 + 删采购格，一个事务。

    ★ 一个调用而不是前端逐个 release：逐个做中途失败会留下半个货号（msku 少了一部分、
      采购格还在），而那正是业主要消灭的形状。"""
    declared(request)
    with timed("remove_sku", actor=who, plan_id=plan_id, sku=sku), pg_conn() as c, \
            c.cursor() as cur:
        ensure_writable(cur, plan_id)
        plan_claim.lock_sku(cur, plan_id, sku)
        # 与 release 同一顺序：期望销量格 → 认领 → 采购格
        cur.execute("DELETE FROM plan_demand_cell d USING msku_bridge b"
                    " WHERE b.seller_sku = d.seller_sku AND b.sid = d.sid AND b.sku = %s"
                    "   AND d.plan_id = %s"
                    " RETURNING d.seller_sku, d.sid, d.period_start, d.expected_units",
                    (sku, plan_id))
        dropped = [{"seller_sku": s, "sid": i, "period": p.strftime("%Y-%m"), "expected_units": u}
                   for s, i, p, u in sorted(cur.fetchall())]
        cur.execute("UPDATE msku_claim cl SET released_at = now(), released_by = %s"
                    "  FROM msku_bridge b"
                    " WHERE b.seller_sku = cl.seller_sku AND b.sid = cl.sid AND b.sku = %s"
                    "   AND cl.plan_id = %s AND cl.released_at IS NULL"
                    " RETURNING cl.seller_sku, cl.sid", (who, sku, plan_id))
        released = [{"seller_sku": s, "sid": i} for s, i in sorted(cur.fetchall())]
        dropped_purchase = _drop_purchase_cells(cur, plan_id, sku)
        if not (released or dropped or dropped_purchase):
            raise ApiError(404, "sku_not_in_plan", "这张计划里没有这个货号",
                           {"plan_id": plan_id, "sku": sku})
    log.info("op=remove_sku plan_id=%s actor=%s sku=%s released=%s dropped=%d"
             " dropped_human=%d dropped_purchase=%s",
             plan_id, who, sku, ",".join(f"{m['seller_sku']}/{m['sid']}" for m in released) or "-",
             len(dropped), sum(d["expected_units"] is not None for d in dropped),
             _cells_log(dropped_purchase))
    return {"sku": sku, "released": released, "dropped_cells": dropped,
            "dropped_purchase_cells": dropped_purchase}


def _ym(d: dt.date) -> str:
    return d.strftime("%Y-%m")


def _load_grid(cur, plan_id: int):
    ensure_plan(cur, plan_id)
    periods = plan_claim.plan_periods(cur, plan_id)
    cur.execute(
        "SELECT d.seller_sku, d.sid, b.sku, d.period_start, d.system_units,"
        "       d.system_extrapolated, d.expected_units, s.has_fba, d.system_basis"
        "  FROM plan_demand_cell d"
        "  JOIN msku_bridge b ON b.seller_sku = d.seller_sku AND b.sid = d.sid"
        "  JOIN seller s ON s.seller_id = d.sid"
        " WHERE d.plan_id = %s ORDER BY b.sku, d.seller_sku, d.sid, d.period_start", (plan_id,))
    demand = cur.fetchall()
    cur.execute("SELECT sku, period_start, planned_units FROM plan_purchase_cell"
                " WHERE plan_id = %s ORDER BY sku, period_start", (plan_id,))
    purchase = cur.fetchall()
    return periods, demand, purchase


def _has_image_by_sku(cur, skus: list[str]) -> dict[str, bool]:
    """`sku_category` 左连——★ 零出站，这里只是一条 SQL，不碰 `shared/pm_client.py`
    半个字符（sku-image 计划 Task 3 第一判据：grid 一次请求不产生任何 PM 出站）。

    三态收敛成一个明确的布尔，不是 null/缺字段——`image_is_placeholder` 为
    真、或这个货号在 `sku_category` 里压根没有行，都算 `False`；只有"有一行
    且不是占位图"才算 `True`。用 `NOT image_is_placeholder` 直接在 SQL 里过滤，
    没匹配上的货号自然不在结果集里，调用方对缺席的 sku 统一按 `False` 兜底
    （`.get(sku, False)`），不许让「查不到」在这里悄悄变成 `None`。
    """
    if not skus:
        return {}
    cur.execute("SELECT sku FROM sku_category"
                " WHERE sku = ANY(%s) AND NOT image_is_placeholder", (skus,))
    return dict.fromkeys((r[0] for r in cur.fetchall()), True)


def _attach_has_image(forecast_sku_rows: list[dict]) -> None:
    """把 `has_image` 就地写进 `forecast_payload()` 产出的每一行 `forecast_sku`。

    ★ 单独一次 `pg_conn()`——`_load_grid()`/`read_lead_time()` 用的那个连接
    在这里已经关闭（同一函数体里 `with pg_conn() as c, c.cursor() as cur:`
    的作用域只包到取数那一步），`forecast_payload()` 本身不碰 DB（模块
    docstring："这里不算任何一个数……只搬运与改名"），所以这一步只能另开
    一个短连接，不能复用已经关闭的游标。
    """
    skus = sorted({r["sku"] for r in forecast_sku_rows})
    with timed("grid_has_image", skus=len(skus)), pg_conn() as c, c.cursor() as cur:
        has_image = _has_image_by_sku(cur, skus)
    for r in forecast_sku_rows:
        r["has_image"] = has_image.get(r["sku"], False)


@router.get("/plans/{plan_id}/grid")
def grid(plan_id: int, request: Request,
         source: Annotated[Source, Depends(source_dep)],
         who: str | None = Depends(actor_optional)):
    declared(request)
    # ★ 连接包住整段推演：project 从同一个 cur 读提前期（预览走同一条路）
    with timed("grid", actor=who, plan_id=plan_id), pg_conn() as c, c.cursor() as cur:
        periods, demand, purchase = _load_grid(cur, plan_id)
        try:
            proj = project(source, cur, demand, purchase, periods, plan_id=plan_id)
        except SOURCE_ERRORS as e:
            raise source_error(e) from e
    _attach_has_image(proj["forecast_sku"])
    out_demand, notes = proj.pop("demand"), proj.pop("source_notes")
    # ★ 键序照旧（响应逐字节不变）
    return {
        **proj,
        "plan_id": plan_id,
        "periods": [_ym(p) for p in periods],
        "demand": out_demand,
        "purchase": [{"sku": s, "period": _ym(p), "planned_units": u} for s, p, u in purchase],
        "source_notes": notes,
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
               request: Request, who: str = Depends(actor)):
    declared(request)
    units = _units(body, "expected_units")
    # ★ 一格一事务：重算（预测）放在事务外 —— 预测慢，不该把行锁攥着（01 §5）
    with timed("put_demand", actor=who, plan_id=plan_id), pg_conn() as c, c.cursor() as cur:
        ensure_writable(cur, plan_id)
        # ★ 团队裁定：回整行十键，跟 grid() 里 demand[] 那一格同一个构造器（_demand_row）——
        #   凭 UPDATE...FROM 顺带 JOIN msku_bridge 拿 sku，不必再开一次连接。
        cur.execute("UPDATE plan_demand_cell d SET expected_units = %s, updated_by = %s,"
                    " updated_at = now()"
                    " FROM msku_bridge b"
                    " WHERE d.plan_id = %s AND d.seller_sku = %s AND d.sid = %s"
                    "   AND d.period_start = %s"
                    "   AND b.seller_sku = d.seller_sku AND b.sid = d.sid"
                    " RETURNING d.system_units, d.system_extrapolated, b.sku, d.system_basis",
                    (units, who, plan_id, seller_sku, sid, _date(period + "-01", "period")))
        row = cur.fetchone()
    if row is None:
        raise ApiError(404, "cell_not_found", "这一格还没长出来（先认领这个 msku）",
                       {"plan_id": plan_id, "seller_sku": seller_sku, "sid": sid,
                        "period": period})
    sysu, extrap, sku, sbasis = row
    # ★ 回的是 grid 里 demand[] 那一格同样的十键形状 —— 前端拿它原地替换那一行，
    #   形状不同就要在前端再写一遍映射，而两份映射迟早分叉（曾经漏了 sku/effective_units）
    return {"cell": demand_row(seller_sku, sid, sku, period, sysu, units, extrap, sbasis)}


@router.put("/plans/{plan_id}/purchase/{sku}/{period}")
def put_purchase(plan_id: int, sku: str, period: str, body: dict, request: Request,
                 who: str = Depends(actor)):
    declared(request)
    units = _units(body, "planned_units")
    with timed("put_purchase", actor=who, plan_id=plan_id), pg_conn() as c, c.cursor() as cur:
        ensure_writable(cur, plan_id)
        cur.execute("UPDATE plan_purchase_cell SET planned_units = %s, updated_by = %s,"
                    " updated_at = now()"
                    " WHERE plan_id = %s AND sku = %s AND period_start = %s RETURNING 1",
                    (units, who, plan_id, sku, _date(period + "-01", "period")))
        hit = cur.fetchone()
    if hit is None:
        raise ApiError(404, "cell_not_found", "这一格还没长出来（先认领该货号下的 msku）",
                       {"plan_id": plan_id, "sku": sku, "period": period})
    return {"cell": {"sku": sku, "period": period, "planned_units": units}}


#: ★ 设计取值 · 未实测（UX-Q30）：一次批量写最多几格。口述量级 100 msku × 6 月 = 600 销量格 + 货号 × 6 采购格。
CELLS_BATCH_MAX = 1000


def _batch_cell(kind: str, raw) -> tuple[dict, str | None]:
    """一格的名字（回报用）与它的毛病；没毛病 → None。"""
    ident = ("seller_sku", "sid", "period") if kind == "demand" else ("sku", "period")
    field = "expected_units" if kind == "demand" else "planned_units"
    if not isinstance(raw, dict):
        return {"kind": kind, "got": raw}, "not_an_object"
    named = {"kind": kind, **{k: raw.get(k) for k in ident}}
    if any(not isinstance(raw.get(k), str) or raw.get(k) == "" for k in ident):
        return named, "missing_key"
    try:
        dt.date.fromisoformat(raw["period"] + "-01")
    except ValueError:
        return named, "bad_period"
    try:
        _units(raw, field)
    except ApiError:
        return {**named, "got": raw.get(field)}, "bad_units"
    return named, None


@router.put("/plans/{plan_id}/cells")
def put_cells(plan_id: int, body: dict, request: Request, who: str = Depends(actor)):
    """UX-D14：粘贴 / 填充 / 批量清空一次写完。★ 一个事务，全有或全无 ——
    半批写进去的计划没人解释得了；坏格与不存在的格逐格点名，不只报第一个。"""
    declared(request)
    t0 = time.perf_counter()
    demand, purchase = body.get("demand") or [], body.get("purchase") or []
    n = len(demand) + len(purchase)

    def done(outcome: str, failed: list[dict] | None = None) -> None:
        # ★ 三问：打的哪张计划 · 多少格 · 多久 · 哪几格没过
        log.info("op=put_cells plan_id=%s actor=%s demand=%d purchase=%d elapsed_ms=%.0f outcome=%s failed=%s",
                 plan_id, who, len(demand), len(purchase), (time.perf_counter() - t0) * 1000, outcome,
                 [f"{c['kind']}:{c.get('seller_sku') or c.get('sku')}|{c.get('sid', '')}|{c.get('period')}"
                  f":{c.get('why', '')}" for c in (failed or [])])

    if not isinstance(demand, list) or not isinstance(purchase, list) or n == 0:
        done("empty_batch")
        raise ApiError(400, "empty_batch", "一格都没给（demand / purchase 都是空的）", {})
    if n > CELLS_BATCH_MAX:
        done("batch_too_large")
        raise ApiError(400, "batch_too_large", f"一次最多写 {CELLS_BATCH_MAX} 格",
                       {"max": CELLS_BATCH_MAX, "got": n})
    bad, seen, dup = [], set(), []
    for kind, items in (("demand", demand), ("purchase", purchase)):
        for raw in items:
            named, why = _batch_cell(kind, raw)
            if why:
                bad.append({**named, "why": why})
                continue
            key = tuple(named.values())
            if key in seen:
                dup.append(named)
            seen.add(key)
    if bad:
        done("bad_units", bad)
        raise ApiError(400, "bad_units", "有格子的数不对（必须是 ≥0 的整数或 null），这一批一格都没写",
                       {"cells": bad})
    if dup:
        done("duplicate_cell", dup)
        raise ApiError(400, "duplicate_cell", "同一格在这一批里出现了两次", {"cells": dup})

    out_d, out_p, missing = [], [], []
    with pg_conn() as c, c.cursor() as cur:
        ensure_writable(cur, plan_id)
        for raw in demand:
            period = dt.date.fromisoformat(raw["period"] + "-01")
            cur.execute("UPDATE plan_demand_cell d SET expected_units = %s, updated_by = %s,"
                        " updated_at = now()"
                        " FROM msku_bridge b"
                        " WHERE d.plan_id = %s AND d.seller_sku = %s AND d.sid = %s"
                        "   AND d.period_start = %s"
                        "   AND b.seller_sku = d.seller_sku AND b.sid = d.sid"
                        " RETURNING d.system_units, d.system_extrapolated, b.sku, d.system_basis",
                        (raw.get("expected_units"), who, plan_id, raw["seller_sku"], raw["sid"], period))
            row = cur.fetchone()
            if row is None:
                missing.append({"kind": "demand", "seller_sku": raw["seller_sku"], "sid": raw["sid"],
                                "period": raw["period"]})
                continue
            sysu, extrap, sku, sbasis = row
            out_d.append(demand_row(raw["seller_sku"], raw["sid"], sku, raw["period"], sysu,
                                     raw.get("expected_units"), extrap, sbasis))
        for raw in purchase:
            cur.execute("UPDATE plan_purchase_cell SET planned_units = %s, updated_by = %s,"
                        " updated_at = now()"
                        " WHERE plan_id = %s AND sku = %s AND period_start = %s RETURNING 1",
                        (raw.get("planned_units"), who, plan_id, raw["sku"],
                         dt.date.fromisoformat(raw["period"] + "-01")))
            if cur.fetchone() is None:
                missing.append({"kind": "purchase", "sku": raw["sku"], "period": raw["period"]})
                continue
            out_p.append({"sku": raw["sku"], "period": raw["period"],
                          "planned_units": raw.get("planned_units")})
        if missing:
            # ★ 在事务里抛：已经 UPDATE 的那几格跟着回滚
            done("cell_not_found", [{**m, "why": "cell_not_found"} for m in missing])
            raise ApiError(404, "cell_not_found", "有格子还没长出来（先认领 msku），这一批一格都没写",
                           {"plan_id": plan_id, "cells": missing})
    done("ok")
    return {"demand": out_d, "purchase": out_p}


@router.post("/plans/{plan_id}/archive")
def archive(plan_id: int, request: Request, who: str = Depends(actor)):
    """★ 同一事务释放全部占用：分两次做，中间挂掉就会留下一张归档了却还扣着货的计划。"""
    declared(request)
    with timed("archive", actor=who, plan_id=plan_id), pg_conn() as c, c.cursor() as cur:
        # ★ 再归档一次要 409 而不是把 archived_at 往后推：那会让「什么时候封存的」
        #   变成「最后一次点按钮的时间」，而这张计划的占用其实早就释放完了
        ensure_writable(cur, plan_id)
        cur.execute("UPDATE msku_claim SET released_at = now(), released_by = %s"
                    " WHERE plan_id = %s AND released_at IS NULL RETURNING seller_sku, sid",
                    (who, plan_id))
        released = cur.fetchall()
        cur.execute("UPDATE plan SET archived_at = now() WHERE plan_id = %s"
                    " RETURNING archived_at", (plan_id,))
        row = cur.fetchone()
        if row is None:
            raise ApiError(404, "plan_not_found", "计划不存在", {"plan_id": plan_id})
    # ★ timed() 的 fields 在进入时就定死，塞不进事务算出来的 released ——
    #   补这一行事后日志，否则「归档了却一条都没释放」查不出来
    log.info("op=archive plan_id=%s released=%d", plan_id, len(released))
    return {"archived_at": row[0].isoformat(), "released": len(released),
            "released_mskus": [{"seller_sku": s, "sid": i} for s, i in released]}


@router.get("/plans/{plan_id}/purchase-progress")
def purchase_progress(plan_id: int, request: Request, who: str | None = Depends(actor_optional)):
    """G5：按计划看完成度。四列并排，不合成达成率（00e 阶段 D 同款判据）。"""
    declared(request)
    with timed("plan_purchase_progress", plan_id=plan_id), pg_conn() as c, c.cursor() as cur:
        ensure_plan(cur, plan_id)
        cur.execute("SELECT rev FROM plan_rev WHERE plan_id=%s ORDER BY in_flight DESC, rev DESC LIMIT 1",
                    (plan_id,))
        rev = (cur.fetchone() or [None])[0]
        cur.execute("SELECT l.sku, l.period_start, l.total_units, q.confirmed_units, q.ordered_units"
                    " FROM plan_line l JOIN v_plan_line_po_qty q USING (line_id)"
                    " WHERE l.plan_id=%s AND l.rev=%s ORDER BY l.sku, l.period_start", (plan_id, rev))
        rows = [{"sku": a, "period": b.strftime("%Y-%m"), "planned": c_, "confirmed": d, "ordered": e,
                 "good_in": None} for a, b, c_, d, e in cur.fetchall()]
    return {"plan_id": plan_id, "rev": rev, "rows": rows,
            "basis": {"good_in": "M3 入库读回未接：null 表示「还没接」，不是 0"}}
