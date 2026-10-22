"""认领一个 msku 进计划 —— `POST /plans/{id}/claims`、Excel 导入、对外预测预览共用。

★ 事务边界归调用方：这里只拿 cursor，不开连接、不提交、不回滚。导入要把几十个
  认领放进一个事务，端点只放一个，两者调的是同一个函数。
★ 取数源异常（ChUnavailable 等）原样上抛 —— 翻成 HTTP 形状是 api 层的事。
"""
from __future__ import annotations

import datetime as dt
import json
import logging

from dim.ch_source import ChDataUnusable, ChUnavailable, UnknownShape
from dim.order_store_map import UnknownStore
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
from shared.pg_client import pg_error_fields

log = logging.getLogger("scm.usecase")

#: L6：usecase 不 import psycopg2，按 SQLSTATE 认唯一冲突
_UNIQUE_VIOLATION = "23505"


#: 有销量取数源的平台。★ 阶段 A 只有 Amazon：`daily_sales` 唯一的
#: 源是 `amazon_sp_api_report_all_orders`，按 `(store, sales_channel)` 取数
#: （dim/order_store_map.py）。非 Amazon 的店在那张表里**按构造**就不会有行 ——
#: 那是「不适用」，不是「认不出这个店」，更不是「卖了 0 件」。
#: ★ 不在这里硬编店铺号：判据是 `seller.platform`（镜像列，001:29），新平台
#: 接进来时改的是这一行，不是散落各处的 if。平台的销量源到位了就把它加进来。
SALES_PLATFORM = ("amazon",)

#: 「这个 msku 压根没有销量取数源」这**一个事实**的唯一说法（plan_projection 按它排出预测 Σ）。
#: ★★ 残留轮次裁定：同一件事不许有两套词汇。两种成因（下面的 `cause`）都落在
#:   这一个 `reason` 上 —— 前端判「要不要让人来填这一格」只看 `reason`，
#:   不必跟着后端每接一个新平台就加一个分支。
NOT_APPLICABLE = "not_applicable_no_sales_source"
#: 成因。★ 与 `reason` 分开是因为它们回答的是两个问题：`reason` 是「这一格
#:   是什么」（事实），`cause` 是「为什么」（出处）。挤进一个字段，就会出现
#:   `not_applicable_non_amazon_platform` 被用在一个 **Amazon** 店上 ——
#:   那个标记会如实地撒谎。
CAUSE_NON_AMAZON_PLATFORM = "non_amazon_platform"
CAUSE_STORE_ABSENT_FROM_ORDER_REPORT = "store_absent_from_order_report"


class UnknownMsku(Exception):
    def __init__(self, seller_sku: str, sid: str):
        self.seller_sku, self.sid = seller_sku, sid
        super().__init__(f"msku 不在桥表里：{seller_sku}/{sid}")


class ClaimedElsewhere(Exception):
    """`holder` = (plan_id, claimed_by, title)；撞唯一索引那一支为 None —— 事务已中止，
    查不了是谁，由调用方回滚后用 `claim_holder` 补名。★ 补名要在新连接上查：
    `pg_conn()` 的 SET search_path 在同一事务里，回滚会把它一起撤掉。"""

    def __init__(self, seller_sku: str, sid: str, holder: tuple[int, str, str] | None):
        self.seller_sku, self.sid, self.holder = seller_sku, sid, holder
        super().__init__(f"msku 已被另一张计划占用：{seller_sku}/{sid} holder={holder}")


class PlanNotFound(Exception):
    def __init__(self, plan_id: int):
        self.plan_id = plan_id
        super().__init__(f"计划不存在：{plan_id}")


class StockWindowPastAnchor(Exception):
    """★ task-14：SQL_FBA_STOCK_DAYS 不判 settled —— 还在写的采集日上，没写到的 msku
    会被读成断货。只有止于 as_of − embargo_days 才安全。越线是我们自己的代码错了 → 500。"""

    def __init__(self, until: dt.date, anchor: dt.date, as_of: dt.date, embargo_days: int):
        self.until, self.anchor, self.as_of, self.embargo_days = until, anchor, as_of, embargo_days
        super().__init__(f"断货日窗口 {until} 越过估计锚点 {anchor}")


def plan_periods(cur, plan_id: int) -> list[dt.date]:
    cur.execute("SELECT period_start, months FROM plan WHERE plan_id = %s", (plan_id,))
    row = cur.fetchone()
    if row is None:
        raise PlanNotFound(plan_id)
    start, months = row
    return [dt.date(start.year + (start.month - 1 + i) // 12,
                    (start.month - 1 + i) % 12 + 1, 1) for i in range(months)]


def _no_sales_source_basis(cause: str) -> dict:
    """★ 「不适用」要活过认领那一次响应 —— grid 读不到当时的 no_history，只读得到格子。
    记进 system_basis（不加列），`reason` 与 no_history 同一个词；没有 base_rate ——
    不适用不是速率 0。旧认领 system_basis 为 NULL 的仍是未知（OP-8），与这个分得开。"""
    return {"reason": NOT_APPLICABLE, "cause": cause}


def _history_window(source, seller_sku: str, sid: str) -> dict | None:
    """这一次取数的口径标记。★ 只有**真查过**的那两条路径调它（取到了 / 查了没有）。

    ★ 不适用那两支不调：口径标记的意思是「查了，用的是这几个月」，给一个没查过
    的格子贴上它，就是说了一句没发生的事。源没有这个方法 ⇒ None。
    """
    if not hasattr(source, "history_window"):
        return None
    return source.history_window(seller_sku, sid) or None


def _check_stock_window(as_of: dt.date, until: dt.date, plan_id: int | None, seller_sku: str,
                        sid: str) -> None:
    anchor = estimation_anchor(as_of)
    if until > anchor:
        log.error("op=seed_forecast outcome=StockWindowPastAnchor plan_id=%s seller_sku=%s"
                  " sid=%s as_of=%s until=%s anchor=%s embargo_days=%s",
                  plan_id, seller_sku, sid, as_of, until, anchor, EMBARGO_DAYS)
        raise StockWindowPastAnchor(until, anchor, as_of, EMBARGO_DAYS)


def system_basis(est, stock, win) -> dict:
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


def seed_forecast(source, plan_id: int | None, seller_sku: str, sid: str, platform: str) -> dict:
    """算一次预测销量与依据 —— 认领、重算、导入、预览共用的**唯一**一份。两份算法迟早
    分叉，本仓已经在 `_demand_row` 上踩过这个坑（见其 docstring），task-23 明确要求
    不许再开第二份。`plan_id` 只进日志（预览没有计划，传 None）。

    返回 {"rate": float|None, "extrapolated": bool, "basis": dict|None,
          "history_window": dict|None, "no_history": dict|None}——
    "no_history" 是要点名进响应 no_history[]/no_rate[] 的那一条（不适用 / 算不出
    历史），能算出速率时是 None。取数失败原样上抛（api 层翻成既有 503 形状）；
    调用方都在同一个事务里，回滚归它。
    """
    if platform not in SALES_PLATFORM:
        # ★ 终审 I-2：「这个店压根没有 Amazon 销量源」是**不适用**，不是未知。
        #   走到 daily_sales 的话，会在 store_for() 上抛 UnknownStore → 503，
        #   把「不适用」说成了「认不出这个店」。
        #   闸放在调用方，与 grid() 里 `has_fba` 那一闸同一个位置、同一个理由：
        #   ★ 不适用 ≠ 0，也 ≠ 未知（CLAUDE.md 判据一），三者必须分得开。
        return {"rate": None, "extrapolated": False,
                "basis": _no_sales_source_basis(CAUSE_NON_AMAZON_PLATFORM),
                "history_window": None,
                "no_history": {"seller_sku": seller_sku, "sid": sid,
                               "reason": NOT_APPLICABLE,
                               "cause": CAUSE_NON_AMAZON_PLATFORM, "platform": platform}}
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
        #   api 层 `_source_error` 里那支「已登记的缺口」永远走不到，而一个没有
        #   路径的分支是没人能信的分支 —— 本仓已经被这种东西咬过两次
        #   （`ChUnavailable` 定义了从没抛过；折叠计数器没有靶子）。
        #   分派放在这里之后，`_source_error` 只会收到「没人声明过」
        #   那一种，它那句「去补一行」也就只对着它该对的人说。
        if not e.registered_gap:
            log.warning("op=seed_forecast outcome=UnknownStore plan_id=%s seller_sku=%s sid=%s"
                        " store_sid=%s", plan_id, seller_sku, sid, e.sid)
            raise
        return {"rate": None, "extrapolated": False,
                "basis": _no_sales_source_basis(CAUSE_STORE_ABSENT_FROM_ORDER_REPORT),
                # ★ 不设 history_window：这一支压根没查过，而口径标记的意思是
                #   「查了，用的是这几个月」。给它一个标记就是说了一句没发生的事。
                "history_window": None,
                "no_history": {"seller_sku": seller_sku, "sid": sid,
                               "reason": NOT_APPLICABLE,
                               "cause": CAUSE_STORE_ABSENT_FROM_ORDER_REPORT,
                               "detail": e.registered_gap}}
    except (ChUnavailable, ChDataUnusable, UnknownShape) as e:
        # 认领不碰采购表，所以这里不会有 PurchaseTableStale。
        log.warning("op=seed_forecast outcome=%s plan_id=%s seller_sku=%s sid=%s detail=%s",
                    type(e).__name__, plan_id, seller_sku, sid, e)
        raise
    est = estimate_rate(daily, stock.censored, as_of)
    basis = system_basis(est, stock, win)
    rate = est.result.rate if est.result else None
    extrapolated = est.extrapolated and rate is not None
    # ★ 用了哪段日子、订单是滞后采集的（CLAUDE.md 铁律三）——
    #   一个没有标记的数比没有数更坏。
    history_window = _history_window(source, seller_sku, sid)
    log.info("op=seed_forecast plan_id=%s seller_sku=%s sid=%s as_of=%s anchor=%s"
             " sales_days=%d censored=%d rejected=%d uncaptured=%d base_rate=%s"
             " extrapolated=%s",
             plan_id, seller_sku, sid, as_of, est.estimation_anchor, len(daily),
             basis["censored_days"], basis["stock_days_rejected"],
             basis["stock_days_uncaptured"], rate, extrapolated)
    no_history = (None if rate is not None else
                  # ★ 没有历史 ≠ 预估 0：格子照建，system_units 留 NULL 并点名（spec §3.3）
                  {"seller_sku": seller_sku, "sid": sid, "reason": "no_sales_history"})
    return {"rate": rate, "extrapolated": extrapolated, "basis": basis,
            "history_window": history_window, "no_history": no_history}


def cell_values(rate: float | None, basis: dict | None,
                period: dt.date) -> tuple[int | None, dict | None]:
    """`system_units` + `system_basis` 这一对：`days_in_month`/`units_raw` 是逐月才补得出
    的两个字段，认领、重算、预览共用这一份，不各自算一遍。"""
    n = days_in_month(period)
    units = units_for_month(rate, n) if rate is not None else None
    cell_basis = (None if basis is None else
                  {**basis, "days_in_month": n, "units_raw": rate * n if rate is not None else None})
    return units, cell_basis


def _seed(source, seller_sku: str, sid: str, sku: str, platform: str, has_fba: bool,
          periods: list[dt.date], plan_id: int | None) -> tuple[dict, list[tuple]]:
    seeded = seed_forecast(source, plan_id, seller_sku, sid, platform)
    rows = []
    for period in periods:
        units, basis = cell_values(seeded["rate"], seeded["basis"], period)
        rows.append((seller_sku, sid, sku, period, units, seeded["extrapolated"], None,
                     has_fba, basis))
    return seeded, rows


def seed_rows(source, seller_sku: str, sid: str, sku: str, platform: str, has_fba: bool,
              periods: list[dt.date], *, plan_id: int | None = None) -> list[tuple]:
    """只算不写。与 `_load_grid` 的 demand 行同形：(seller_sku, sid, sku, period,
    system_units, system_extrapolated, expected_units=None, has_fba, system_basis)。
    ★ `claim_msku` 用同一份构造写格子 —— 预览与认领不会分叉。"""
    return _seed(source, seller_sku, sid, sku, platform, has_fba, periods, plan_id)[1]


def claim_holder(cur, seller_sku: str, sid: str) -> tuple[int, str, str] | None:
    """活认领的占用者 (plan_id, claimed_by, title)。已下单计划的认领不算占用。"""
    cur.execute("SELECT m.plan_id, m.claimed_by, p.title FROM msku_claim m"
                " JOIN plan p ON p.plan_id = m.plan_id"
                " WHERE m.seller_sku = %s AND m.sid = %s"
                "   AND m.released_at IS NULL AND NOT m.plan_ordered",
                (seller_sku, sid))
    return cur.fetchone()


def _active_claim(cur, plan_id: int, seller_sku: str, sid: str) -> tuple[int, str, str] | None:
    """本计划自己的认领优先（重复认领走幂等）；否则是别的未下单计划的占用。"""
    cur.execute("SELECT m.plan_id, m.claimed_by, p.title FROM msku_claim m"
                " JOIN plan p ON p.plan_id = m.plan_id"
                " WHERE m.seller_sku = %s AND m.sid = %s AND m.released_at IS NULL"
                "   AND (m.plan_id = %s OR NOT m.plan_ordered)"
                " ORDER BY (m.plan_id = %s) DESC LIMIT 1",
                (seller_sku, sid, plan_id, plan_id))
    return cur.fetchone()


def lock_sku(cur, plan_id: int, sku: str) -> None:
    """一张计划里一个货号的「认领 / 释放 / 移出货号」互斥，事务结束自动放。

    ★ 为什么是 advisory 锁而不是锁行：要护住的是一个**跨三张表的判断**（这个货号还有没有
      msku 在认领 ⇒ 它的采购格该不该在），而那一刻相关的行可能一行都还不存在（认领前没有
      claim 行，释放后没有采购格），行锁锁不住不存在的行。
    ★ 不变式：三条路径都在**写任何行之前**先拿这一把（之前只读、不加行锁）：同一货号上的写
      一律排队，不会有「攥着行锁再等这把锁」的事务，也就不会互等成死锁（40P01 → 500）。
      认领的取数在拿锁之前、也在写任何行之前；重复认领在读到「已在认领中」时直接返回，
      一把锁都不拿。READ COMMITTED 下拿到锁之后的每条语句都是新快照，看得见前一个事务提交的结果。
    ★ 哈希撞车只会让两个无关货号多排一次队，不会错。"""
    cur.execute("SELECT pg_advisory_xact_lock(%s, hashtext(%s))", (plan_id, sku))


def claim_msku(cur, source, plan_id: int, seller_sku: str, sid: str, who: str,
               seeded: list[tuple] | None = None) -> dict:
    """认领并种格子。调用方先 `ensure_writable`；返回体即 `POST /claims` 的 200 响应。

    ★ 撞唯一索引时**不回滚**：事务归调用方（导入里还有别的行）。抛
      `ClaimedElsewhere(holder=None)`，调用方回滚后用 `claim_holder` 补名。
    ★ `seeded`（`seed_rows` 的产出）：调用方已在事务外取过数 —— 导入几十个 msku，
      逐个取数会让写事务攥着连接跨过几十次取数源。给了就不碰 `source`；返回体里
      `no_history` / `history_window` 为 None（没在这里算，不是「没有」）。"""
    periods = plan_periods(cur, plan_id)
    # ★ 终审 I-2：顺手把 seller.platform 取回来 —— 它决定这个 msku 的销量
    #   **有没有取数源**（见 `SALES_PLATFORM`）。FK msku_bridge_seller_fk
    #   保证桥表每一行都有对应的 seller 行，所以 JOIN 不会吃掉任何 msku。
    cur.execute("SELECT b.sku, s.platform, s.has_fba FROM msku_bridge b"
                " JOIN seller s ON s.seller_id = b.sid"
                " WHERE b.seller_sku = %s AND b.sid = %s", (seller_sku, sid))
    row = cur.fetchone()
    if row is None:
        raise UnknownMsku(seller_sku, sid)
    sku, platform, has_fba = row

    # ★ 控制器裁定（fix round 2）：同一计划里重复认领一个**在认领中**的 msku（双击 / 两个
    #   标签页 / 重试）是幂等空操作 —— 不取数、不重种格子、不碰任何行锁，直接回当前认领。
    #   复活已释放的行、全新认领照走下面的路。被别的计划占着 → 同样在取数之前就 409
    #   （认领行挪到取数之后了，不在这里挡，占用中的 msku 也要白打一次取数源、CH 挂了还会
    #   把 409 变成 503）。这只是一次读：并发下看错了也无妨，裁决仍在下面的 INSERT。
    holder = _active_claim(cur, plan_id, seller_sku, sid)
    if holder is not None and holder[0] != plan_id:
        log.info("op=claim plan_id=%s actor=%s seller_sku=%s sid=%s outcome=claimed_elsewhere"
                 " holder_plan=%s", plan_id, who, seller_sku, sid, holder[0])
        raise ClaimedElsewhere(seller_sku, sid, holder)
    if holder is not None:
        log.info("op=claim plan_id=%s actor=%s seller_sku=%s sid=%s sku=%s already_claimed=true",
                 plan_id, who, seller_sku, sid, sku)
        return {"claimed": {"seller_sku": seller_sku, "sid": sid, "sku": sku},
                "already_claimed": True,
                "seeded": {"demand_cells": 0, "purchase_cells": 0},
                "no_history": [], "history_window": None}

    # ★ task-23：算法唯一入口是 seed_forecast —— 认领与重算两处都调它，不许各自另写一份。
    if seeded is None:
        meta, rows = _seed(source, seller_sku, sid, sku, platform, has_fba, periods, plan_id)
        no_history = [meta["no_history"]] if meta["no_history"] is not None else []
        history_window = meta["history_window"]
    else:
        rows, no_history, history_window = seeded, None, None
        got = [(r[0], r[1], r[2], r[3]) for r in rows]
        want = [(seller_sku, sid, sku, p) for p in periods]
        if got != want:
            # 事务外取数与事务内读桥表之间货号 / 月份变了：写下去就是格子与认领对不上
            raise RuntimeError(f"claim_msku: 预先种好的行与计划对不上 plan_id={plan_id}"
                               f" seller_sku={seller_sku} sid={sid} got={got} want={want}")
    # ★ 与 release / remove_sku 同一把 (plan, sku) 锁，拿在**写任何行之前**（认领行也挪到了
    #   锁之后）：兄弟 msku 的认领与最后一个 msku 的释放并发时，要么释放看见这个兄弟、留下
    #   采购格，要么认领在释放删完之后重新种出采购格 —— 不许出现「有 msku 在认领、却没有
    #   采购格」的货号（INSERT … ON CONFLICT DO NOTHING 不等行锁，挡不住）。
    # ★ 取数在锁之前：锁攥着跨过取数源（CH 可以慢到 20s）会把同货号的兄弟认领全串行。
    #   取数期间本事务一行都没写，所以不会攥着行锁去等这把锁（fix round 2 的 40P01 就是
    #   认领行先被 ON CONFLICT DO UPDATE 锁住、再等这把锁）。
    lock_sku(cur, plan_id, sku)
    # ★ 上面那次读只为快路径；并发下以这里为准 —— 直接插，让部分唯一索引裁决。
    try:
        cur.execute(
            "INSERT INTO msku_claim (plan_id, seller_sku, sid, claimed_by)"
            " VALUES (%s, %s, %s, %s)"
            " ON CONFLICT (plan_id, seller_sku, sid) DO UPDATE"
            "    SET released_at = NULL, released_by = NULL,"
            "        claimed_by = EXCLUDED.claimed_by, claimed_at = now()",
            (plan_id, seller_sku, sid, who))
    except Exception as e:
        if getattr(e, "pgcode", None) != _UNIQUE_VIOLATION:
            raise
        pg = pg_error_fields(e)
        log.info("op=claim plan_id=%s actor=%s seller_sku=%s sid=%s outcome=claimed_elsewhere"
                 " pgcode=%s constraint=%s", plan_id, who, seller_sku, sid, pg["pgcode"],
                 pg["constraint"])
        raise ClaimedElsewhere(seller_sku, sid, None) from None

    for (_, _, _, period, units, extrapolated, _, _, basis) in rows:
        # ★ system_extrapolated 取估计器给的值（spec §3.3：如实给，不盖章）
        cur.execute(
            "INSERT INTO plan_demand_cell (plan_id, seller_sku, sid, period_start,"
            " system_units, system_extrapolated, system_basis, updated_by)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (plan_id, seller_sku, sid, period_start) DO UPDATE"
            "    SET system_units = EXCLUDED.system_units,"
            "        system_extrapolated = EXCLUDED.system_extrapolated,"
            "        system_basis = EXCLUDED.system_basis",
            (plan_id, seller_sku, sid, period, units, extrapolated,
             None if basis is None else json.dumps(basis), who))
    for period in periods:
        # ★ 货号级采购格子必须先长出来：没有行和填了 0 不能长得一样，
        #   否则提交时它连一条 skipped 都留不下（判据②）
        cur.execute("INSERT INTO plan_purchase_cell (plan_id, sku, period_start, updated_by)"
                    " VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    (plan_id, sku, period, who))
    log.info("op=claim plan_id=%s actor=%s seller_sku=%s sid=%s sku=%s cells=%d no_history=%s"
             " preseeded=%s", plan_id, who, seller_sku, sid, sku, len(periods),
             "-" if no_history is None else len(no_history), seeded is not None)
    return {"claimed": {"seller_sku": seller_sku, "sid": sid, "sku": sku},
            "already_claimed": False,
            "seeded": {"demand_cells": len(periods), "purchase_cells": len(periods)},
            "no_history": no_history, "history_window": history_window}
