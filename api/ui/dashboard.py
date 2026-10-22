"""两个看板。★ 计划的状态是记录状态的木桶派生，不是独立字段（08 §1.1）。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request

from api.common.deps import actor_optional, declared, known_states, require_fresh_mirrors
from rules.digest import content_digest, human_changes, input_snapshot
from rules.submit import DemandCell, PurchaseCell
from shared.pg_client import pg_conn, timed

log = logging.getLogger("scm.api")

router = APIRouter(dependencies=[Depends(require_fresh_mirrors)])

#: 派生桶：不是记录状态，是由记录状态算出来的两个口径。
#: ★ 其余的桶一律从 `plan_line_state_rank` ∪ {已撤销} 推出来（deps.known_states），
#:   不手列 —— 手列的宇宙必然漏掉第 N+1 种：`已确认` 在阶段 A 经
#:   `POST /plan-lines/{id}/transition` 真的到得了，而它曾经没有自己的桶，
#:   那样的计划只在「进行中」里出现，看板上看不出它已经被确认过。
DERIVED_BUCKETS = ["进行中", "已提交未确认"]
#: 阶段 A 铸不出这几个态（要承重墙①②）。★ 接口照样返回它们（诚实），
#  由前端按阶段不渲染 —— 接口自己抹成 0，「没有」和「还没做」就长得一样了（S-20）。
UNREACHABLE_IN_STAGE_A = ["已下单", "准备排货", "已排货"]


@router.get("/dashboard/plans")
def dashboard_plans(request: Request, who: str | None = Depends(actor_optional)):
    declared(request)
    with timed("dashboard_plans", actor=who), pg_conn() as c, c.cursor() as cur:
        counted = DERIVED_BUCKETS + known_states(cur)
        cur.execute("SELECT v.overall, count(*) FROM v_plan_overall_state v"
                    " JOIN plan p USING (plan_id) WHERE p.archived_at IS NULL"
                    " GROUP BY v.overall")
        by_state = {k: n for k, n in cur.fetchall()}
    counts = {k: 0 for k in counted}
    for state, n in by_state.items():
        if state in counts:
            counts[state] += n
        if state not in (None, "已完结", "已撤销"):
            counts["进行中"] += n
        if state == "已提交":
            counts["已提交未确认"] += n
    return {"counts": counts,
            "scope_note": {"unreachable_in_stage_a": UNREACHABLE_IN_STAGE_A,
                           # ★ 前端的状态筛选要的是「记录状态」，不是这两个口径 —— 由接口说哪些是派生的，前端不手列
                           "derived_buckets": DERIVED_BUCKETS,
                           "never_submitted_excluded": by_state.get(None, 0)}}


@router.get("/dashboard/unsubmitted")
def dashboard_unsubmitted(request: Request, who: str | None = Depends(actor_optional)):
    declared(request)
    never, changed = [], []
    with timed("dashboard_unsubmitted", actor=who), pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT p.plan_id, p.title, r.rev, r.content_digest, r.input_snapshot"
                    "  FROM plan p"
                    "  LEFT JOIN LATERAL (SELECT rev, content_digest, input_snapshot FROM plan_rev"
                    "     WHERE plan_id = p.plan_id ORDER BY rev DESC LIMIT 1) r ON true"
                    " WHERE p.archived_at IS NULL ORDER BY p.plan_id")
        plans = cur.fetchall()
        legacy: list[int] = []
        for plan_id, title, rev, digest, snapshot in plans:
            if rev is None:
                never.append({"plan_id": plan_id, "title": title})
                continue
            cur.execute("SELECT sku, period_start, planned_units FROM plan_purchase_cell"
                        " WHERE plan_id = %s", (plan_id,))
            purchase = [PurchaseCell(s, p.strftime("%Y-%m"), u) for s, p, u in cur.fetchall()]
            cur.execute(
                "SELECT d.seller_sku, d.sid, b.sku, d.period_start, d.system_units,"
                "       d.expected_units"
                "  FROM plan_demand_cell d"
                "  JOIN msku_bridge b ON b.seller_sku = d.seller_sku AND b.sid = d.sid"
                "  JOIN msku_claim cl ON cl.plan_id = d.plan_id"
                "   AND cl.seller_sku = d.seller_sku AND cl.sid = d.sid"
                "   AND cl.released_at IS NULL"
                " WHERE d.plan_id = %s", (plan_id,))
            demand = [DemandCell(ss, sid, sku, p.strftime("%Y-%m"), sy, ex)
                      for ss, sid, sku, p, sy, ex in cur.fetchall()]
            # ★ UX-D12：只算人改的 —— 比人填的那一层；重算预估改的是 system_units，不进这里
            if snapshot is not None:
                n = human_changes(snapshot, input_snapshot(purchase, demand))
                if n > 0:
                    changed.append({"plan_id": plan_id, "title": title, "since_rev": rev,
                                    "human_cells": n, "basis": "input_snapshot"})
                continue
            # 028 之前提交的版本没有快照：只能比整份摘要（含预估），basis 上说出来，不冒充人改的格数
            legacy.append(plan_id)
            if content_digest(purchase, demand) != digest:
                changed.append({"plan_id": plan_id, "title": title, "since_rev": rev,
                                "human_cells": None, "basis": "content_digest"})
    if legacy:
        log.info("op=dashboard_unsubmitted basis=content_digest reason=rev_before_028 plan_ids=%s", legacy)
    # ★ 两栏分开：从未提交要人去提交，改过要人去重新提交 —— 催的是两种动作
    return {"never_submitted": never, "changed_since_submit": changed}
