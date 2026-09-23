"""计划：建 · 列 · 认领 · 网格 · 两种量 · 归档。"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Annotated

import psycopg2
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from api.ui.deps import (
    actor,
    actor_optional,
    declared,
    ensure_writable,
    known_states,
    require_fresh_mirrors,
)
from api.ui.errors import ApiError
from api.ui.source_factory import (  # noqa: F401 - 供测试用 plans.FIXTURES 构造替身源
    FIXTURES,
    source_dep,
)
from dim.ch_source import (
    ChDataUnusable,
    ChUnavailable,
    PurchaseTableStale,
    UnknownShape,
    is_overdue,
)
from dim.order_store_map import UnknownStore
from dim.source import Source
from forecast.estimate import InsufficientHistory, monthly_estimate
from forecast.projection import inventory_projection
from rules.effective import effective_demand
from shared.pg_client import pg_conn, timed

log = logging.getLogger("scm.api")

router = APIRouter(dependencies=[Depends(require_fresh_mirrors)])

#: 取数源由 `Depends(source_dep)` **每请求**装配一次（api/ui/source_factory.py）。
#: ★ 终审 C-1/C-2/C-3：这里原先是一个模块级单例 `SOURCE = make_source()`。它把
#:   一个 CH 客户端和 `ChSource` 全部可变的每次调用状态（两份缓存、`stats()`、
#:   `history_window()`、`purchase_as_of`）钉在进程上，而 grid()/claim() 是
#:   plain def 端点、跑在 Starlette 的线程池里 —— 于是并发请求会互相踩。
#:   换源仍然只改 config.toml 的 [forecast] source，dim/source.py 的协议不变。


#: 有月销量取数源的平台。★ 阶段 A 只有 Amazon：`monthly_sales_history` 唯一的
#: 源是 `amazon_sp_api_report_all_orders`，按 `(store, sales_channel)` 取数
#: （dim/order_store_map.py）。非 Amazon 的店在那张表里**按构造**就不会有行 ——
#: 那是「不适用」，不是「认不出这个店」，更不是「卖了 0 件」。
#: ★ 不在这里硬编店铺号：判据是 `seller.platform`（镜像列，001:29），新平台
#: 接进来时改的是这一行，不是散落各处的 if。平台的销量源到位了就把它加进来。
_SALES_PLATFORM = ("amazon",)


def _source_error(e: Exception) -> ApiError:
    """把 ChSource 的失败翻成 ApiError —— 同 jobs/refresh_dims.ChUnreachable → refresh_failed
    的形状，不另发明一种。★ 一律 503：拿不到数算出来的曲线看起来完全正常，S-29/S-30 的
    错误形状统一裁定同样适用于这条路径。"""
    if isinstance(e, ChUnavailable):
        return ApiError(503, "forecast_source_unavailable",
                        "预测取数源连不上，拒绝服务 —— 不拿旧数或空数冒充",
                        {"target": e.target, **e.cause})
    if isinstance(e, ChDataUnusable):
        return ApiError(503, "forecast_source_unusable",
                        "近 7 个采集日全都掉档或未采完 —— 这批数据不可用，不是「库存为 0」",
                        {"rejected": e.rejected})
    if isinstance(e, PurchaseTableStale):
        # ★ OQ-5 裁定：陈旧阈值突破 → 未知，不是 0；与「掉档守卫全拒」同一类失败，
        #   共用 forecast_source_unusable，不再单起一个错误码。
        return ApiError(503, "forecast_source_unusable",
                        "采购单快照太久没更新 —— 在途视为未知，不是 0",
                        {"captured": e.captured.isoformat(), "age_days": e.age_days,
                         "threshold_days": e.threshold_days})
    if isinstance(e, UnknownShape):
        return ApiError(503, "forecast_source_unusable", "取数源出现认不出的形态",
                        {"detail": str(e)})
    if isinstance(e, UnknownStore):
        # ★ fix round 1：design §8 OQ-3 点名的真实缺口（6 组 (store, sales_channel)、
        #   近 90 天 21 单没有对应 sid）会从 monthly_sales_history() → store_for()
        #   一路抛到这里。与 UnknownShape 同一类「认不出的形态」，不新开错误码；
        #   detail 里带着 store_for() 原样写出的 sid（它是唯一已知量——正因为它没有
        #   对应的 (store, channel) 才会抛这个异常），运维据此去补
        #   dim/order_store_map.py::STORE_SID 那一行。
        return ApiError(503, "forecast_source_unusable",
                        "这个店没有声明取数映射（dim/order_store_map.py 缺一行）"
                        "—— 销量取不到，不能当成「卖了 0 件」",
                        {"detail": str(e)})
    # ★ 复核裁定（fix round 1，不改）：这支 raise e 目前不可达——claim()/grid() 的
    #   except 元组已经收窄到本函数认识的全部五种类型，元组之外的异常根本不会走
    #   到这里。签名标 -> ApiError 与这一支的行为因此有点不对称，留着不改：
    #   真正要防的洞不在这里，是"except 元组要跟上 ChSource 实际会抛的异常集合"。
    raise e   # ★ 认不出的异常不许被兜成这五种之一——同 CLAUDE.md「不许 else 兜底」


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


def _periods(cur, plan_id: int) -> list[dt.date]:
    cur.execute("SELECT period_start, months FROM plan WHERE plan_id = %s", (plan_id,))
    row = cur.fetchone()
    if row is None:
        raise ApiError(404, "plan_not_found", "计划不存在", {"plan_id": plan_id})
    start, months = row
    return [dt.date(start.year + (start.month - 1 + i) // 12,
                    (start.month - 1 + i) % 12 + 1, 1) for i in range(months)]


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
        periods = _periods(cur, plan_id)
        # ★ 终审 I-2：顺手把 seller.platform 取回来 —— 它决定这个 msku 的销量
        #   **有没有取数源**（见下面 `_SALES_PLATFORM`）。FK msku_bridge_seller_fk
        #   保证桥表每一行都有对应的 seller 行，所以 JOIN 不会吃掉任何 msku。
        cur.execute("SELECT b.sku, s.platform FROM msku_bridge b"
                    " JOIN seller s ON s.seller_id = b.sid"
                    " WHERE b.seller_sku = %s AND b.sid = %s", (seller_sku, sid))
        row = cur.fetchone()
        if row is None:
            raise ApiError(404, "unknown_msku", "msku 不在桥表里",
                           {"seller_sku": seller_sku, "sid": sid})
        sku, platform = row

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
                # ★ team-lead 裁定：claimed_by 里加 title（join plan.title）——
                #   occupier 查询本来就在一条新连接上跑，join 不多花一次往返。
                cur2.execute("SELECT m.plan_id, m.claimed_by, p.title FROM msku_claim m"
                             " JOIN plan p ON p.plan_id = m.plan_id"
                             " WHERE m.seller_sku = %s AND m.sid = %s"
                             "   AND m.released_at IS NULL AND NOT m.plan_ordered",
                             (seller_sku, sid))
                holder = cur2.fetchone()
            raise ApiError(409, "msku_already_claimed", "该 msku 已被另一张尚未下单的计划占用",
                           {"seller_sku": seller_sku, "sid": sid,
                            "claimed_by": {"plan_id": holder[0], "actor": holder[1],
                                          "title": holder[2]}
                            if holder else None}) from None

        no_history = []
        history_window = None
        est = None
        if platform not in _SALES_PLATFORM:
            # ★ 终审 I-2：「这个店压根没有 Amazon 销量源」是**不适用**，不是未知。
            #   走到 monthly_sales_history 的话，CH 档会在 store_for() 上抛
            #   UnknownStore → 503，而 fixture 档同一个 msku 是 200 —— 两档对同一
            #   件事给出两种答案，且 503 那一侧把「不适用」说成了「认不出这个店」。
            #   闸放在调用方，与 grid() 里 `has_fba` 那一闸同一个位置、同一个理由：
            #   ★ 不适用 ≠ 0，也 ≠ 未知（CLAUDE.md 判据一），三者必须分得开。
            no_history.append({"seller_sku": seller_sku, "sid": sid,
                               "reason": "not_applicable_non_amazon_platform",
                               "platform": platform})
        else:
            try:
                est = monthly_estimate(
                    source.monthly_sales_history(seller_sku, sid, len(periods)), len(periods))
            except InsufficientHistory:
                # ★ 没有历史 ≠ 预估 0：格子照建（人还要在上面填），system_units 留 NULL 并点名
                est = None
                no_history.append({"seller_sku": seller_sku, "sid": sid,
                                   "reason": "no_sales_history"})
            except (ChUnavailable, ChDataUnusable, UnknownShape, UnknownStore) as e:
                # ★ fix round 1：UnknownStore 曾经漏在这个元组外——monthly_sales_history()
                #   → order_store_map.store_for() 抛的这个异常会裸着冒成无 S-29 形状的 500。
                #   claim() 不碰采购表，所以这里不需要 PurchaseTableStale（同 grid() 不需要
                #   UnknownStore 一个道理——见 _source_error() 上面的审计注释）。
                raise _source_error(e) from e
            # ★ 用了哪几个月、哪些是补 0、最新那个月距今多少天 —— 订单是滞后采集的
            #   （CLAUDE.md 铁律三），一个没有标记的数比没有数更坏。
            if hasattr(source, "history_window"):
                history_window = source.history_window(seller_sku, sid) or None
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
            "no_history": no_history, "history_window": history_window}


def _stranded_purchase_cells(cur, plan_id: int, seller_sku: str, sid: str) -> list[dict]:
    """释放一个 msku 之后，**留在原地**的货号级采购格子。

    ★ 不删：同货号的兄弟 msku 还在认领中时，那批格子正是它的（PK 不含 msku）。
    ★ 但必须报：`dropped_cells` 只点名了两种格子里的一种，于是货号级那一批
      在界面上无声地留着 —— 提交时它们会以 `no_claimed_msku` 被跳过，
      而人到那时才知道刚才的释放还留下了东西。
    ★ 还有兄弟在认领 ⇒ 一个都不算搁浅；认不出货号（桥表缺行）也不许静默当成空。
    """
    cur.execute("SELECT sku FROM msku_bridge WHERE seller_sku = %s AND sid = %s",
                (seller_sku, sid))
    row = cur.fetchone()
    if row is None:
        log.warning("op=release_claim plan_id=%s seller_sku=%s sid=%s 桥表无此 msku —— "
                    "无法判定货号级采购格子是否搁浅", plan_id, seller_sku, sid)
        return []
    sku = row[0]
    cur.execute("SELECT count(*) FROM msku_claim cl"
                " JOIN msku_bridge b ON b.seller_sku = cl.seller_sku AND b.sid = cl.sid"
                " WHERE cl.plan_id = %s AND cl.released_at IS NULL AND b.sku = %s",
                (plan_id, sku))
    if cur.fetchone()[0]:
        return []
    cur.execute("SELECT period_start FROM plan_purchase_cell"
                " WHERE plan_id = %s AND sku = %s ORDER BY period_start", (plan_id, sku))
    return [{"sku": sku, "period": p.strftime("%Y-%m")} for (p,) in cur.fetchall()]


@router.delete("/plans/{plan_id}/claims/{seller_sku}/{sid}")
def release(plan_id: int, seller_sku: str, sid: str, request: Request,
            who: str = Depends(actor)):
    declared(request)
    with timed("release_claim", actor=who, plan_id=plan_id), pg_conn() as c, c.cursor() as cur:
        ensure_writable(cur, plan_id)
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
        stranded = _stranded_purchase_cells(cur, plan_id, seller_sku, sid)
    log.info("op=release_claim plan_id=%s seller_sku=%s sid=%s dropped=%d stranded=%d",
             plan_id, seller_sku, sid, len(dropped), len(stranded))
    # ★ 丢东西必须有声：删掉的格子逐条回给界面，包括人填过的数
    return {"released": {"seller_sku": seller_sku, "sid": sid}, "dropped_cells": dropped,
            "stranded_purchase_cells": stranded}


def _ym(d: dt.date) -> str:
    return d.strftime("%Y-%m")


def _demand_row(seller_sku: str, sid: str, sku: str, period: str,
                sysu: int | None, expu: int | None, extrap: bool) -> dict:
    """grid() 与 put_demand() 共用的同一份构造器（team-lead 09-22 裁定：一个行形状一个来源）。

    ★ effective_demand() 是 effective_units 唯一裁定者 —— 两个端点各自算一遍迟早分叉，
    put_demand() 曾经就漏了 sku/effective_units 两个字段，前端拿它原地替换 grid 里的行时
    在真实 API 下会读到 undefined（mock 因为整格 clone 而不漏，两个数据源因此在这一格上分了叉）。"""
    eff = effective_demand(sysu, expu)
    return {"seller_sku": seller_sku, "sid": sid, "sku": sku, "period": period,
            "system_units": sysu, "expected_units": expu, "basis": eff.basis,
            # ★ 外推 ≠ 预估，标记必须随数走（14 §5）
            "system_extrapolated": extrap,
            # ★ 单一来源：前端按 (sid, sku) 分块、库存公式的入参都读这一个字段，
            #   不许各自重算 —— effective_demand() 是唯一裁定者（rules/effective.py）
            "effective_units": eff.units}


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
def grid(plan_id: int, request: Request,
         source: Annotated[Source, Depends(source_dep)],
         who: str | None = Depends(actor_optional)):
    declared(request)
    with timed("grid", actor=who, plan_id=plan_id), pg_conn() as c, c.cursor() as cur:
        periods, demand, purchase = _load_grid(cur, plan_id)

    out_demand: list[dict] = []
    #: (sku, sid) → {period: 该店该货号各 msku 的期望销量之和}
    demand_by_store: dict[tuple[str, str], dict[str, int | None]] = {}
    #: (sku, sid) → 在仓合计。★ 该店无 FBA / 各 msku 全无在仓数据 → None，
    #  但这两种「None」不是一回事 —— 下面 applicable_by_store 把它们分开（09-23 真实缺陷）
    onhand_by_store: dict[tuple[str, str], int | None] = {}
    #: (sku, sid) → 该店是否有 FBA（= 该 sid 的 seller.has_fba，同一 sid 下恒定）。
    #  ★ 决定 onhand 为 None 时读作「不适用」还是「未知在仓」——
    #    不能靠 onhand is None 反推，那会把「有 FBA 但没数字」错认成「不适用」，
    #    而这正是负责人在真实数据上撞见的 12 个 na_disagrees_with_seller 孤儿的成因。
    applicable_by_store: dict[tuple[str, str], bool] = {}
    # ★ demand 每个 msku 有 N 个月份行；在仓事实与月份无关，只能对每个 msku 累加一次 ——
    #   否则一个 3 个月的计划会把在仓数算成 3 倍。
    onhand_added: set[tuple[str, str]] = set()

    try:
        for seller_sku, sid, sku, period, sysu, extrap, expu, fba in demand:
            row = _demand_row(seller_sku, sid, sku, _ym(period), sysu, expu, extrap)
            out_demand.append(row)
            eff_units = row["effective_units"]
            key, ym = (sku, sid), _ym(period)
            slot = demand_by_store.setdefault(key, {})
            # ★ 合计行 = 各 msku 之和（14 §1 ①）；只要有一个 msku 未知，这一格就是未知
            if ym not in slot:
                slot[ym] = eff_units
            elif slot[ym] is not None and eff_units is not None:
                slot[ym] += eff_units
            else:
                slot[ym] = None
            if key not in onhand_by_store:
                onhand_by_store[key] = None
            # ★ fba 只取决于 sid（同一 key 下每行都一样），但用 or 而不是覆盖 ——
            #   同一 key 可能被多个 msku 行访问到，任何一次看见 True 就定了
            applicable_by_store[key] = applicable_by_store.get(key, False) or fba
            if fba and (seller_sku, sid) not in onhand_added:
                onhand_added.add((seller_sku, sid))
                got = source.onhand_available(seller_sku, sid)
                if got is not None:
                    onhand_by_store[key] = (onhand_by_store[key] or 0) + got

        in_transit: dict[str, list] = {sku: source.purchase_in_transit(sku)
                                       for sku in {r[0] for r in purchase}}
        as_of_date = source.as_of()
        as_of = as_of_date.isoformat()
        # ★ OQ-6 裁定：sku_pipeline[] 的 bucket 只在 CH 源（具备 purchase_as_of）下
        #   有意义；FixtureSource 没有这个方法，按 hasattr 跳过 —— fixture 路径的
        #   sku_pipeline 形状因此逐字节不变（tests/test_grid_fixture.py 核实）。
        purchase_as_of = source.purchase_as_of() if hasattr(source, "purchase_as_of") else None
        dropped_stats = source.stats() if hasattr(source, "stats") else {}
    # ★ fix round 1 审计（team-lead 要求逐一核对 ChSource 实际会抛的异常集合是否都
    #   进了这个元组）：grid() 只调用 as_of/onhand_available/purchase_in_transit/
    #   purchase_as_of/stats——没有一个会走 order_store_map（那是 monthly_sales_history
    #   专属，只有 claim() 会碰），所以这里不需要 UnknownStore；PurchaseTableStale
    #   只从 purchase_as_of() 抛，grid() 确实会调用它，必须在。
    except (ChUnavailable, ChDataUnusable, PurchaseTableStale, UnknownShape) as e:
        raise _source_error(e) from e

    inventory = []
    for (sku, sid), by_month in sorted(demand_by_store.items()):
        sku_total_by_period = {p: sum(t.units for t in in_transit.get(sku, ()) if t.period == p)
                               for p in by_month}
        # ★ 采购在途一律不进任何店铺的加项（阶段 A 恒 null）：它是货号级的，而
        #   「计划内只有一家店认领」不等于它是这批货的唯一消费者 —— 同一货号可能被
        #   本计划之外的店铺在卖。按单店给全额与平摊一样，都是发明分配规则（C4 禁）。
        #   所以推演的入库项是空的，closing = onhand − demand。
        rows = inventory_projection(onhand_by_store[(sku, sid)], {}, by_month,
                                    applicable=applicable_by_store.get((sku, sid), False))
        for row in rows:
            inventory.append({
                "sku": sku, "sid": sid, "period": row["period"],
                # onhand = 这一格的期初：第一个月是当前在仓事实，其后是上月期末
                "onhand": row["opening"],
                "inbound": None,
                "closing": row["closing"],
                "basis": {
                    "source": "ch", "as_of": as_of,
                    # ★ 裁定：阶段 A 恒 false，但必须**由明细算出**而不是盖章 ——
                    #   盖死的那个在阶段 B 真掺进计划采购时会撒谎，而标记撒谎比
                    #   没有标记更坏（forecast/projection.py 的同名注释）
                    "includes_plan_purchase": not row["basis"]["excludes_plan_purchase"],
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
                                        "sources": [], "no_seller_attribution": True,
                                        # ★ E-13 实测 98.8% 的在途会落进 overdue——这是
                                        #   数据的形状，不是 bug，但不许因为不匹配任何
                                        #   计划月份就从 sku_pipeline 里悄悄消失。
                                        "bucket": (None if purchase_as_of is None else
                                                  ("overdue" if is_overdue(t.period, purchase_as_of)
                                                   else "future"))})
            slot["units"] += t.units
            slot["sources"].append({"units": t.units, "kind": "purchase_in_transit", "ref": t.ref})

    return {
        "plan_id": plan_id,
        "periods": [_ym(p) for p in periods],
        "demand": out_demand,
        "purchase": [{"sku": s, "period": _ym(p), "planned_units": u} for s, p, u in purchase],
        "inventory": inventory,
        "sku_pipeline": [pipeline[k] for k in sorted(pipeline)],
        "source_notes": {
            "as_of": as_of,
            "purchase_as_of": (purchase_as_of.isoformat() if purchase_as_of else None),
            # ★ 被排除的那一侧必须出现在响应里：sid=0 的欧洲共享池实测占全部可售
            #   27.6%（键名固定 shared_pool_excluded，Task 2 裁定），没有在途预计到货日的
            #   行也在这里 —— 丢东西必须有声。
            "dropped": dropped_stats,
        },
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
        # ★ 团队裁定：回整行九键，跟 grid() 里 demand[] 那一格同一个构造器（_demand_row）——
        #   凭 UPDATE...FROM 顺带 JOIN msku_bridge 拿 sku，不必再开一次连接。
        cur.execute("UPDATE plan_demand_cell d SET expected_units = %s, updated_by = %s,"
                    " updated_at = now()"
                    " FROM msku_bridge b"
                    " WHERE d.plan_id = %s AND d.seller_sku = %s AND d.sid = %s"
                    "   AND d.period_start = %s"
                    "   AND b.seller_sku = d.seller_sku AND b.sid = d.sid"
                    " RETURNING d.system_units, d.system_extrapolated, b.sku",
                    (units, who, plan_id, seller_sku, sid, _date(period + "-01", "period")))
        row = cur.fetchone()
    if row is None:
        raise ApiError(404, "cell_not_found", "这一格还没长出来（先认领这个 msku）",
                       {"plan_id": plan_id, "seller_sku": seller_sku, "sid": sid,
                        "period": period})
    sysu, extrap, sku = row
    # ★ 回的是 grid 里 demand[] 那一格同样的九键形状 —— 前端拿它原地替换那一行，
    #   形状不同就要在前端再写一遍映射，而两份映射迟早分叉（曾经漏了 sku/effective_units）
    return {"cell": _demand_row(seller_sku, sid, sku, period, sysu, units, extrap)}


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
