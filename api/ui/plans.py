"""计划：建 · 列 · 认领 · 网格 · 两种量 · 归档。"""
from __future__ import annotations

import datetime as dt
import json
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
from api.ui.grid_forecast import NO_SALES_SOURCE, forecast_payload
from api.ui.lead_time import read_lead_time
from api.ui.source_factory import (  # noqa: F401 - 供测试用 plans.FIXTURES 构造替身源
    FIXTURES,
    source_dep,
)
from dim.ch_source import (
    ChDataUnusable,
    ChUnavailable,
    PurchaseTableStale,
    UnknownShape,
)
from dim.order_store_map import UnknownStore
from dim.source import Source
from forecast.baseline import (
    EMBARGO_DAYS,
    HALFLIFE_DAYS,
    SHRINK_PRIOR,
    TARGET_WINDOW_DAYS,
    WINDOW_DAYS,
    WINSOR_MIN_NONZERO,
    WINSOR_Q,
    data_windows,
    estimate_rate,
    estimation_anchor,
)
from forecast.calendar import days_in_month, units_for_month
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


#: 有销量取数源的平台。★ 阶段 A 只有 Amazon：`daily_sales` 唯一的
#: 源是 `amazon_sp_api_report_all_orders`，按 `(store, sales_channel)` 取数
#: （dim/order_store_map.py）。非 Amazon 的店在那张表里**按构造**就不会有行 ——
#: 那是「不适用」，不是「认不出这个店」，更不是「卖了 0 件」。
#: ★ 不在这里硬编店铺号：判据是 `seller.platform`（镜像列，001:29），新平台
#: 接进来时改的是这一行，不是散落各处的 if。平台的销量源到位了就把它加进来。
_SALES_PLATFORM = ("amazon",)

#: 「这个 msku 压根没有销量取数源」这**一个事实**的唯一说法。
#: ★★ 残留轮次裁定：同一件事不许有两套词汇。两种成因（下面的 `cause`）都落在
#:   这一个 `reason` 上 —— 前端判「要不要让人来填这一格」只看 `reason`，
#:   不必跟着后端每接一个新平台就加一个分支。
NOT_APPLICABLE = NO_SALES_SOURCE      # 词只定义一处：grid_forecast 按它排出预测 Σ
#: 成因。★ 与 `reason` 分开是因为它们回答的是两个问题：`reason` 是「这一格
#:   是什么」（事实），`cause` 是「为什么」（出处）。挤进一个字段，就会出现
#:   `not_applicable_non_amazon_platform` 被用在一个 **Amazon** 店上 ——
#:   那个标记会如实地撒谎。
CAUSE_NON_AMAZON_PLATFORM = "non_amazon_platform"
CAUSE_STORE_ABSENT_FROM_ORDER_REPORT = "store_absent_from_order_report"


def _no_sales_source_basis(cause: str) -> dict:
    """★ 「不适用」要活过认领那一次响应 —— grid 读不到当时的 no_history，只读得到格子。
    记进 system_basis（不加列），`reason` 与 no_history 同一个词；没有 base_rate ——
    不适用不是速率 0。旧认领 system_basis 为 NULL 的仍是未知（OP-8），与这个分得开。"""
    return {"reason": NOT_APPLICABLE, "cause": cause}



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
        # ★ F4（复核 09-23）：`stale_table` 点名是哪张表老了——行项表停了是
        #   没有新采购，单据表停了是状态冻住（还可能两张一起停，
        #   `dim.ch_source.BOTH_PURCHASE_TABLES`），处置不同。不加这个字段，
        #   两个成因相反的 503 body 会逐字节相同，拿到它的人得去翻日志才知道
        #   该查哪张——日志已经点名了，响应体不该比日志更瞎。
        return ApiError(503, "forecast_source_unusable",
                        "采购单快照太久没更新 —— 在途视为未知，不是 0",
                        {"captured": e.captured.isoformat(), "age_days": e.age_days,
                         "threshold_days": e.threshold_days, "stale_table": e.stale_table})
    if isinstance(e, UnknownShape):
        return ApiError(503, "forecast_source_unusable", "取数源出现认不出的形态",
                        {"detail": str(e)})
    if isinstance(e, UnknownStore):
        # ★ fix round 1：design §8 OQ-3 点名的真实缺口（6 组 (store, sales_channel)、
        #   近 90 天 21 单没有对应 sid）会从 daily_sales() → store_for()
        #   一路抛到这里。与 UnknownShape 同一类「认不出的形态」，不新开错误码；
        #   detail 里带着 store_for() 原样写出的 sid（它是唯一已知量——正因为它没有
        #   对应的 (store, channel) 才会抛这个异常）。
        # ★★ 残留轮次：这句 hint 原先对**所有** UnknownStore 都说「缺一行，去补」，
        #   包括 `NO_ORDER_REPORT_SID` 上那三个已登记的缺口 —— 而对它们，补一行
        #   就是硬编一行不存在的映射，把另一家店的销量记到它头上（正是 M-4 刚从
        #   `sid_for_or_raise` 拿掉的那条错建议，留在了人**先读到**的字段里）。
        # ★ 修法不是在这里再劈一支（那一支没有调用路径，而没有路径的分支没人能
        #   信）。分派放在 `claim()` 的 `except UnknownStore` 里，按
        #   `e.registered_gap` 把「已登记的缺口」翻成 200 + 不适用 —— 所以走到
        #   这里的**只有「没人声明过」那一种**，这句「去补一行」也就只对着它该
        #   对的人说。改 `claim()` 那处分派的人要连这句话一起重新想。
        return ApiError(503, "forecast_source_unusable",
                        "这个店没有声明取数映射（dim/order_store_map.py 缺一行）"
                        "—— 销量取不到，不能当成「卖了 0 件」",
                        {"detail": str(e), "sid": e.sid})
    # ★ 复核裁定（fix round 1，不改）：这支 raise e 目前不可达——claim()/grid() 的
    #   except 元组已经收窄到本函数认识的全部五种类型，元组之外的异常根本不会走
    #   到这里。签名标 -> ApiError 与这一支的行为因此有点不对称，留着不改：
    #   真正要防的洞不在这里，是"except 元组要跟上 ChSource 实际会抛的异常集合"。
    raise e   # ★ 认不出的异常不许被兜成这五种之一——同 CLAUDE.md「不许 else 兜底」


def _history_window(source, seller_sku: str, sid: str) -> dict | None:
    """这一次取数的口径标记。★ 只有**真查过**的那两条路径调它（取到了 / 查了没有）。

    ★ 不适用那两支不调：口径标记的意思是「查了，用的是这几个月」，给一个没查过
    的格子贴上它，就是说了一句没发生的事。`FixtureSource` 没有这个方法 ⇒ None。
    """
    if not hasattr(source, "history_window"):
        return None
    return source.history_window(seller_sku, sid) or None


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


#: ★ task-14：SQL_FBA_STOCK_DAYS 不判 settled —— 还在写的采集日上，没写到的 msku
#:   会被读成断货。只有止于 as_of − embargo_days 才安全。越线是我们自己的代码错了 → 500。
STOCK_WINDOW_PAST_ANCHOR = "stock_window_past_anchor"


def _check_stock_window(as_of: dt.date, until: dt.date, plan_id: int, seller_sku: str,
                        sid: str) -> None:
    anchor = estimation_anchor(as_of)
    if until > anchor:
        log.error("op=claim_forecast outcome=StockWindowPastAnchor plan_id=%s seller_sku=%s"
                  " sid=%s as_of=%s until=%s anchor=%s embargo_days=%s",
                  plan_id, seller_sku, sid, as_of, until, anchor, EMBARGO_DAYS)
        raise ApiError(500, STOCK_WINDOW_PAST_ANCHOR,
                       "断货日窗口越过了估计锚点 —— 未结算的采集日会被读成断货，拒绝估计",
                       {"until": until.isoformat(), "anchor": anchor.isoformat(),
                        "as_of": as_of.isoformat(), "embargo_days": EMBARGO_DAYS})


def _system_basis(est, stock, win) -> dict:
    """spec §3.3：依据随数一起存 —— 界面点开能看到「预测 124 = 4.13 件/天 × 30 天」。"""
    r = est.result
    window_days = (win.until - win.censor_from).days + 1
    return {
        "method": "baseline_rate_v1", "as_of": est.as_of.isoformat(),
        "estimation_anchor": est.estimation_anchor.isoformat(),
        "embargo_days": est.embargo_days,
        "base_rate": r.rate if r else None, "raw_rate": r.raw_rate if r else None,
        "n_eff": r.n_eff if r else None, "target_rate": r.target_rate if r else None,
        # ★ target_rate = target_units ÷ target_days；天数从首次有销量起算、最多 90（OP-3 原样）
        "target_units": r.target_units if r else None, "target_days": r.target_days if r else None,
        "winsor_cap": r.winsor_cap if r else None,
        "first_seen": r.first_seen.isoformat() if (r and r.first_seen) else None,
        "window": {"from": win.censor_from.isoformat(), "until": win.until.isoformat()},
        "target_window": {"from": win.sales_from.isoformat(), "until": win.until.isoformat()},
        "censored_days": r.censored_days if r else len(stock.censored),
        "stock_days_rejected": len(stock.rejected),
        # ★ 没采到 ≠ 采到了没断货：这些天不 censor，数目必须说出来
        "stock_days_uncaptured": window_days - len(stock.captured) - len(stock.rejected),
        "reason": None if r else "no_sales_in_window",
        "constants": {"halflife_days": HALFLIFE_DAYS, "window_days": WINDOW_DAYS,
                      "target_window_days": TARGET_WINDOW_DAYS, "shrink_prior": SHRINK_PRIOR,
                      "winsor_q": WINSOR_Q, "winsor_min_nonzero": WINSOR_MIN_NONZERO},
    }


def _claimed_elsewhere(seller_sku: str, sid: str, holder) -> ApiError:
    return ApiError(409, "msku_already_claimed", "该 msku 已被另一张尚未下单的计划占用",
                    {"seller_sku": seller_sku, "sid": sid,
                     "claimed_by": {"plan_id": holder[0], "actor": holder[1], "title": holder[2]}
                     if holder else None})


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

        # ★ 控制器裁定（fix round 2）：同一计划里重复认领一个**在认领中**的 msku（双击 / 两个
        #   标签页 / 重试）是幂等空操作 —— 不取数、不重种格子、不碰任何行锁，直接回当前认领。
        #   复活已释放的行、全新认领照走下面的路。被别的计划占着 → 同样在取数之前就 409
        #   （认领行挪到取数之后了，不在这里挡，占用中的 msku 也要白打一次取数源、CH 挂了还会
        #   把 409 变成 503）。这只是一次读：并发下看错了也无妨，裁决仍在下面的 INSERT。
        cur.execute("SELECT m.plan_id, m.claimed_by, p.title FROM msku_claim m"
                    " JOIN plan p ON p.plan_id = m.plan_id"
                    " WHERE m.seller_sku = %s AND m.sid = %s AND m.released_at IS NULL"
                    "   AND (m.plan_id = %s OR NOT m.plan_ordered)"
                    " ORDER BY (m.plan_id = %s) DESC LIMIT 1",
                    (seller_sku, sid, plan_id, plan_id))
        holder = cur.fetchone()
        if holder is not None and holder[0] != plan_id:
            raise _claimed_elsewhere(seller_sku, sid, holder)
        if holder is not None:
            log.info("op=claim plan_id=%s actor=%s seller_sku=%s sid=%s sku=%s already_claimed=true",
                     plan_id, who, seller_sku, sid, sku)
            return {"claimed": {"seller_sku": seller_sku, "sid": sid, "sku": sku},
                    "already_claimed": True,
                    "seeded": {"demand_cells": 0, "purchase_cells": 0},
                    "no_history": [], "history_window": None}

        no_history = []
        history_window = None
        rate = None
        basis = None
        # 没有数就没有外推可言（CHECK：外推 ⇒ system_units 非空）
        extrapolated = False
        if platform not in _SALES_PLATFORM:
            # ★ 终审 I-2：「这个店压根没有 Amazon 销量源」是**不适用**，不是未知。
            #   走到 daily_sales 的话，CH 档会在 store_for() 上抛
            #   UnknownStore → 503，而 fixture 档同一个 msku 是 200 —— 两档对同一
            #   件事给出两种答案，且 503 那一侧把「不适用」说成了「认不出这个店」。
            #   闸放在调用方，与 grid() 里 `has_fba` 那一闸同一个位置、同一个理由：
            #   ★ 不适用 ≠ 0，也 ≠ 未知（CLAUDE.md 判据一），三者必须分得开。
            no_history.append({"seller_sku": seller_sku, "sid": sid,
                               "reason": NOT_APPLICABLE,
                               "cause": CAUSE_NON_AMAZON_PLATFORM,
                               "platform": platform})
            basis = _no_sales_source_basis(CAUSE_NON_AMAZON_PLATFORM)
        else:
            try:
                as_of = source.as_of()
                win = data_windows(as_of)
                _check_stock_window(as_of, win.until, plan_id, seller_sku, sid)
                daily = source.daily_sales(seller_sku, sid, win.sales_from, win.until)
                stock = source.fba_stock_days(seller_sku, sid, win.censor_from, win.until)
            except UnknownStore as e:
                # ★★ 残留轮次裁定（第二轮）：**按异常分派，不预检**。
                #   `NO_ORDER_REPORT_SID` 上的 179 个 msku 的形状与上面那一支
                #   一模一样 —— 这个 msku 没有销量取数源 —— 所以给同一个答案：
                #   格子照建、system_units 留 NULL、标记点名。145 个 msku 认领
                #   不了，比一格等人来填的 NULL 坏得多。
                #   ★ 未知/0 的保证不变：绝不是 0，也绝不是编出来的预估。
                #   ★ 为什么不在调用前查一次 `NO_ORDER_REPORT_SID`：那样
                #   `_source_error` 里那支「已登记的缺口」永远走不到，而一个没有
                #   路径的分支是没人能信的分支 —— 本仓已经被这种东西咬过两次
                #   （`ChUnavailable` 定义了从没抛过；折叠计数器没有靶子）。
                #   分派放在这里之后，`_source_error` 只会收到「没人声明过」
                #   那一种，它那句「去补一行」也就只对着它该对的人说。
                if not e.registered_gap:
                    raise _source_error(e) from e
                no_history.append({"seller_sku": seller_sku, "sid": sid,
                                   "reason": NOT_APPLICABLE,
                                   "cause": CAUSE_STORE_ABSENT_FROM_ORDER_REPORT,
                                   "detail": e.registered_gap})
                basis = _no_sales_source_basis(CAUSE_STORE_ABSENT_FROM_ORDER_REPORT)
                # ★ 不设 history_window：这一支压根没查过，而口径标记的意思是
                #   「查了，用的是这几个月」。给它一个标记就是说了一句没发生的事。
            except (ChUnavailable, ChDataUnusable, UnknownShape) as e:
                # ★ fix round 1：`UnknownStore` 曾经漏在这个元组外——
                #   daily_sales() → order_store_map.store_for() 抛的这个
                #   异常会裸着冒成无 S-29 形状的 500。现在它有了自己的 except
                #   （上面那一支，按成因分派），所以不在这个元组里。
                #   claim() 不碰采购表，所以这里不需要 PurchaseTableStale（同 grid()
                #   不需要 UnknownStore 一个道理——见 _source_error() 上面的审计注释）。
                raise _source_error(e) from e
            else:
                est = estimate_rate(daily, stock.censored, as_of)
                basis = _system_basis(est, stock, win)
                rate = est.result.rate if est.result else None
                extrapolated = est.extrapolated and rate is not None
                # ★ 用了哪段日子、订单是滞后采集的（CLAUDE.md 铁律三）——
                #   一个没有标记的数比没有数更坏。
                history_window = _history_window(source, seller_sku, sid)
                log.info("op=claim_forecast plan_id=%s seller_sku=%s sid=%s as_of=%s anchor=%s"
                         " sales_days=%d censored=%d rejected=%d uncaptured=%d base_rate=%s"
                         " extrapolated=%s",
                         plan_id, seller_sku, sid, as_of, est.estimation_anchor, len(daily),
                         basis["censored_days"], basis["stock_days_rejected"],
                         basis["stock_days_uncaptured"], rate, extrapolated)
                if rate is None:
                    # ★ 没有历史 ≠ 预估 0：格子照建，system_units 留 NULL 并点名（spec §3.3）
                    no_history.append({"seller_sku": seller_sku, "sid": sid,
                                       "reason": "no_sales_history"})
        # ★ 与 release / remove_sku 同一把 (plan, sku) 锁，拿在**写任何行之前**（认领行也挪到了
        #   锁之后）：兄弟 msku 的认领与最后一个 msku 的释放并发时，要么释放看见这个兄弟、留下
        #   采购格，要么认领在释放删完之后重新种出采购格 —— 不许出现「有 msku 在认领、却没有
        #   采购格」的货号（INSERT … ON CONFLICT DO NOTHING 不等行锁，挡不住）。
        # ★ 取数在锁之前：锁攥着跨过取数源（CH 可以慢到 20s）会把同货号的兄弟认领全串行。
        #   取数期间本事务一行都没写，所以不会攥着行锁去等这把锁（fix round 2 的 40P01 就是
        #   认领行先被 ON CONFLICT DO UPDATE 锁住、再等这把锁）。
        _lock_sku(cur, plan_id, sku)
        # ★ 上面那次读只为快路径；并发下以这里为准 —— 直接插，让部分唯一索引裁决；
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
            raise _claimed_elsewhere(seller_sku, sid, holder) from None

        for period in periods:
            n = days_in_month(period)
            units = units_for_month(rate, n) if rate is not None else None
            cell_basis = (None if basis is None else
                          json.dumps({**basis, "days_in_month": n,
                                      "units_raw": rate * n if rate is not None else None}))
            # ★ system_extrapolated 取估计器给的值（spec §3.3：如实给，不盖章）
            cur.execute(
                "INSERT INTO plan_demand_cell (plan_id, seller_sku, sid, period_start,"
                " system_units, system_extrapolated, system_basis, updated_by)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (plan_id, seller_sku, sid, period_start) DO UPDATE"
                "    SET system_units = EXCLUDED.system_units,"
                "        system_extrapolated = EXCLUDED.system_extrapolated,"
                "        system_basis = EXCLUDED.system_basis",
                (plan_id, seller_sku, sid, period, units, extrapolated, cell_basis, who))
        for period in periods:
            # ★ 货号级采购格子必须先长出来：没有行和填了 0 不能长得一样，
            #   否则提交时它连一条 skipped 都留不下（判据②）
            cur.execute("INSERT INTO plan_purchase_cell (plan_id, sku, period_start, updated_by)"
                        " VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                        (plan_id, sku, period, who))
    return {"claimed": {"seller_sku": seller_sku, "sid": sid, "sku": sku},
            "already_claimed": False,
            "seeded": {"demand_cells": len(periods), "purchase_cells": len(periods)},
            "no_history": no_history, "history_window": history_window}


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


def _lock_sku(cur, plan_id: int, sku: str) -> None:
    """一张计划里一个货号的「认领 / 释放 / 移出货号」互斥，事务结束自动放。

    ★ 为什么是 advisory 锁而不是锁行：要护住的是一个**跨三张表的判断**（这个货号还有没有
      msku 在认领 ⇒ 它的采购格该不该在），而那一刻相关的行可能一行都还不存在（认领前没有
      claim 行，释放后没有采购格），行锁锁不住不存在的行。
    ★ 不变式：三条路径都在**写任何行之前**先拿这一把（之前只读、不加行锁）：同一货号上的写
      一律排队，不会有「攥着行锁再等这把锁」的事务，也就不会互等成死锁（40P01 → 500）。
      claim() 的取数在拿锁之前、也在写任何行之前；重复认领在读到「已在认领中」时直接返回，
      一把锁都不拿。READ COMMITTED 下拿到锁之后的每条语句都是新快照，看得见前一个事务提交的结果。
    ★ 哈希撞车只会让两个无关货号多排一次队，不会错。"""
    cur.execute("SELECT pg_advisory_xact_lock(%s, hashtext(%s))", (plan_id, sku))


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
            _lock_sku(cur, plan_id, sku)
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
        _lock_sku(cur, plan_id, sku)
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


def _demand_row(seller_sku: str, sid: str, sku: str, period: str,
                sysu: int | None, expu: int | None, extrap: bool,
                system_basis: dict | None) -> dict:
    """grid() 与 put_demand() 共用的同一份构造器（team-lead 09-22 裁定：一个行形状一个来源）。

    ★ effective_demand() 是 effective_units 唯一裁定者 —— 两个端点各自算一遍迟早分叉，
    put_demand() 曾经就漏了 sku/effective_units 两个字段，前端拿它原地替换 grid 里的行时
    在真实 API 下会读到 undefined（mock 因为整格 clone 而不漏，两个数据源因此在这一格上分了叉）。"""
    eff = effective_demand(sysu, expu)
    return {"seller_sku": seller_sku, "sid": sid, "sku": sku, "period": period,
            "system_units": sysu, "expected_units": expu, "basis": eff.basis,
            # ★ 外推 ≠ 预估，标记必须随数走（14 §5）
            "system_extrapolated": extrap,
            # ★ 依据随数走（spec §3.3）：预测 = base_rate × 天数，点开要看得到
            "system_basis": system_basis,
            # ★ 单一来源：前端按 (sid, sku) 分块、库存公式的入参都读这一个字段，
            #   不许各自重算 —— effective_demand() 是唯一裁定者（rules/effective.py）
            "effective_units": eff.units}


def _load_grid(cur, plan_id: int):
    periods = _periods(cur, plan_id)
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


@router.get("/plans/{plan_id}/grid")
def grid(plan_id: int, request: Request,
         source: Annotated[Source, Depends(source_dep)],
         who: str | None = Depends(actor_optional)):
    declared(request)
    with timed("grid", actor=who, plan_id=plan_id), pg_conn() as c, c.cursor() as cur:
        periods, demand, purchase = _load_grid(cur, plan_id)
        lead = read_lead_time(cur)

    out_demand: list[dict] = []
    #: msku → 推演入参（grid_forecast）。★ 在仓只在 has_fba 时取
    fba_by_msku: dict[tuple[str, str], bool] = {}
    onhand_by_msku: dict[tuple[str, str], int | None] = {}
    rate_by_msku: dict[tuple[str, str], float] = {}

    try:
        for seller_sku, sid, sku, period, sysu, extrap, expu, fba, sbasis in demand:
            row = _demand_row(seller_sku, sid, sku, _ym(period), sysu, expu, extrap, sbasis)
            k = (seller_sku, sid)
            fba_by_msku[k] = fba
            # ★ OP-8：没有 system_basis 的旧认领 → rate 缺席 → 桥接段未知并点名，不当 0
            if sbasis and sbasis.get("base_rate") is not None:
                rate_by_msku[k] = float(sbasis["base_rate"])
            out_demand.append(row)
            if fba:
                onhand_by_msku[k] = source.onhand_available(seller_sku, sid)

        # ★ 采购格子 ∪ 认领的货号：没采购格子的货号，它的在途也要进推演（grid_forecast）
        in_transit: dict[str, list] = {sku: source.purchase_in_transit(sku)
                                       for sku in {r[0] for r in purchase} | {r[2] for r in demand}}
        as_of_date = source.as_of()
        as_of = as_of_date.isoformat()
        # ★ OQ-5 陈旧闸：调用本身就是守卫（越线 → PurchaseTableStale）；结果写进
        #   source_notes.purchase_as_of，并作在途「已逾期」的判据日（OQ-6，终审 I-2）。
        #   FixtureSource 没有这个方法，按 hasattr 跳过。
        purchase_as_of = source.purchase_as_of() if hasattr(source, "purchase_as_of") else None
        dropped_stats = source.stats() if hasattr(source, "stats") else {}
    # ★ fix round 1 审计（team-lead 要求逐一核对 ChSource 实际会抛的异常集合是否都
    #   进了这个元组）：grid() 只调用 as_of/onhand_available/purchase_in_transit/
    #   purchase_as_of/stats——没有一个会走 order_store_map（那是 daily_sales
    #   专属，只有 claim() 会碰），所以这里不需要 UnknownStore；PurchaseTableStale
    #   只从 purchase_as_of() 抛，grid() 确实会调用它，必须在。
    except (ChUnavailable, ChDataUnusable, PurchaseTableStale, UnknownShape) as e:
        raise _source_error(e) from e

    payload = forecast_payload(
        as_of=as_of_date, periods=periods, demand_rows=out_demand, purchase_rows=purchase,
        fba=fba_by_msku, onhand=onhand_by_msku, base_rate=rate_by_msku, in_transit=in_transit,
        lead=lead, embargo_days=EMBARGO_DAYS, plan_id=plan_id, purchase_as_of=purchase_as_of)
    return {
        **payload,
        "plan_id": plan_id,
        "periods": [_ym(p) for p in periods],
        "demand": out_demand,
        "purchase": [{"sku": s, "period": _ym(p), "planned_units": u} for s, p, u in purchase],
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
    return {"cell": _demand_row(seller_sku, sid, sku, period, sysu, units, extrap, sbasis)}


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
