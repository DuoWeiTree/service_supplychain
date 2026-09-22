"""计划单记录的查询与状态动作。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request

from api.ui.deps import actor, actor_optional, declared, require_fresh_mirrors
from api.ui.errors import ApiError
from shared.pg_client import pg_conn, timed

log = logging.getLogger("scm.api")

router = APIRouter(dependencies=[Depends(require_fresh_mirrors)])

TERMINAL = ("已完结", "已撤销")


def _allowed_next(cur, state: str) -> list[str]:
    # ★ 按 plan_line_state_rank 排序而不是按 to_state 的字典序：
    #   字典序会把「已撤销」（无 rank，cancel 类）排到「已确认」（forward 类）前面，
    #   allowed[] 就成了「哪个字先」而不是「先往前走的那条路先列」（04 §1.4 的意图）。
    #   已撤销不在 rank 表里（004 迁移的注释），排到最后。
    cur.execute(
        "SELECT t.to_state FROM plan_line_transition t"
        " LEFT JOIN plan_line_state_rank r ON r.state = t.to_state"
        " WHERE t.from_state = %s ORDER BY r.rank NULLS LAST, t.to_state", (state,))
    return [r[0] for r in cur.fetchall()]


def _known_states(cur) -> list[str]:
    """★ 状态值的白名单只有一个出处：`plan_line_state_rank`（004）+ 旁路终态
    「已撤销」（004 注释：它刻意不进 rank 表）。`_allowed_next` 用同一张表校验迁移，
    这里校验 `state=` 查询参数——两处都不许各自维护一份宇宙，会分叉。"""
    cur.execute("SELECT state FROM plan_line_state_rank ORDER BY rank")
    return [r[0] for r in cur.fetchall()] + ["已撤销"]


@router.get("/plan-lines")
def list_lines(request: Request, who: str | None = Depends(actor_optional)):
    declared(request, "plan_ids", "state", "sku", "category", "period", "group_by")
    q = request.query_params
    if q.get("category") or q.get("group_by") == "category":
        # ★ 品类镜像（sku_category / A-1）不在阶段 A 的表里。默默忽略这个参数，
        #   返回的会是「全量」而不是报错 —— 而全量看起来完全正常。
        raise ApiError(400, "category_not_available", "阶段 A 还没有品类镜像",
                       {"depends_on": "sku_category（A-1）"})
    group_by = q.get("group_by")
    if group_by and group_by not in ("sku", "period"):
        raise ApiError(400, "bad_group_by", "group_by 只支持 sku | period",
                       {"got": group_by, "allowed": ["sku", "period"]})

    plan_ids = None
    if q.get("plan_ids"):
        try:
            plan_ids = [int(x) for x in q["plan_ids"].split(",") if x]
        except ValueError:
            raise ApiError(400, "bad_plan_ids", "plan_ids 必须是逗号分隔的整数",
                           {"got": q["plan_ids"]}) from None

    with timed("list_lines", actor=who), pg_conn() as c, c.cursor() as cur:
        if q.get("state"):
            known = _known_states(cur)
            if q["state"] not in known:
                raise ApiError(400, "bad_state", "state 不是白名单里的状态值",
                               {"got": q["state"], "allowed": known})

        # ★ 候选集只按 plan_ids 圈定（没给就是全库）：excluded{} 要回答「这次
        #   查询范围里，每个 filter 各自丢了多少」——候选集圈错了，没被勾选的
        #   计划、没被勾选的行也会被算进「丢掉」，那不是这次查询丢的东西。
        where, args = ["true"], []
        if plan_ids is not None:
            where.append("l.plan_id = ANY(%s)")
            args.append(plan_ids)
        cur.execute("SELECT l.line_id, l.plan_id, l.rev, l.sku, l.period_start, l.total_units,"
                    " l.demand_at_submit, l.demand_by_seller, l.state"
                    f" FROM plan_line l WHERE {' AND '.join(where)}"
                    " ORDER BY l.plan_id, l.rev, l.sku, l.period_start", args)
        candidates = [{"line_id": r[0], "plan_id": r[1], "rev": r[2], "sku": r[3],
                      "period": r[4].strftime("%Y-%m"), "total_units": r[5],
                      "demand_at_submit": r[6], "demand_by_seller": r[7], "state": r[8]}
                     for r in cur.fetchall()]

    # ★ 每个 filter 各自在候选集上报告自己丢了多少，而不是合并成一个匿名总数——
    #   一个数分不清是 state 丢的还是 period 丢的，人只能猜（对照 /v1/plans 的
    #   excluded:{archived:N}：一个具名 filter 一个桶，这里有四个就该有四个桶）。
    excluded = {"state": 0, "sku": 0, "category": 0, "period": 0}
    lines = candidates
    if q.get("state"):
        excluded["state"] = sum(1 for r in candidates if r["state"] != q["state"])
        lines = [r for r in lines if r["state"] == q["state"]]
    if q.get("sku"):
        excluded["sku"] = sum(1 for r in candidates if r["sku"] != q["sku"])
        lines = [r for r in lines if r["sku"] == q["sku"]]
    if q.get("period"):
        excluded["period"] = sum(1 for r in candidates if r["period"] != q["period"])
        lines = [r for r in lines if r["period"] == q["period"]]
    # ★ category 恒为 0：给了这个参数在函数开头就已经 400 了，走不到这里。

    groups: dict[str, dict] = {}
    if group_by:
        key = "sku" if group_by == "sku" else "period"
        for row in lines:
            slot = groups.setdefault(row[key], {"lines": 0, "total_units": 0,
                                                "demand_at_submit": 0})
            slot["lines"] += 1
            slot["total_units"] += row["total_units"]
            slot["demand_at_submit"] += row["demand_at_submit"]
    return {"lines": lines, "groups": groups, "excluded": excluded}


def _transition(line_id: int, to_state: str, reason: str, who: str, src: str) -> tuple[int, str, str]:
    """返回 (line_id, from_state, to_state)。★ from_state 只为 caller 落 post-commit
    日志用——对外响应体仍然只有 {"line_id","state"}（08 §接口清单），不额外回显 from。"""
    with timed("plan_line_transition", actor=who, line_id=line_id, to_state=to_state), \
            pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT state FROM plan_line WHERE line_id = %s FOR UPDATE", (line_id,))
        row = cur.fetchone()
        if row is None:
            raise ApiError(404, "line_not_found", "没有这条记录", {"line_id": line_id})
        state = row[0]
        cur.execute("SELECT count(*) FROM plan_line_transition WHERE to_state = %s", (to_state,))
        if cur.fetchone()[0] == 0:
            raise ApiError(400, "unknown_state", "没有这个状态值",
                           {"got": to_state,
                            "known": _allowed_next(cur, state)})
        if state in TERMINAL:
            raise ApiError(422, "terminal_state", "这条记录已在终态，离不开",
                           {"line_id": line_id, "state": state})
        allowed = _allowed_next(cur, state)
        if to_state not in allowed:
            # ★ allowed[] 必须回显（04 §1.4）
            raise ApiError(422, "illegal_transition", "这条状态迁移不在白名单里",
                           {"line_id": line_id, "from": state, "to": to_state,
                            "allowed": allowed})
        cur.execute("INSERT INTO plan_line_event"
                    " (line_id, from_state, to_state, actor, reason, src)"
                    " VALUES (%s, %s, %s, %s, %s, %s)",
                    (line_id, state, to_state, who, reason or None, src))
    return line_id, state, to_state


@router.post("/plan-lines/{line_id}/cancel")
def cancel_line(line_id: int, body: dict, who: str = Depends(actor)):
    reason = (body.get("reason") or "").strip()
    if not reason:
        raise ApiError(400, "reason_required", "撤销必须填理由", {"field": "reason"})
    lid, from_state, to_state = _transition(line_id, "已撤销", reason, who, f"cancel_line:{line_id}")
    # ★ timed() 的 fields 在进入时就定死，塞不进事务里才查到的 from_state ——
    #   补这一行事后日志，否则翻日志看得到「谁撤销失败了」看不到「从哪个状态撤的」
    log.info("op=cancel_line line_id=%s from=%s to=%s actor=%s", lid, from_state, to_state, who)
    return {"line_id": lid, "state": to_state}


@router.post("/plan-lines/{line_id}/transition")
def move_line(line_id: int, body: dict, who: str = Depends(actor)):
    """哪些迁移合法完全由 003 迁移的白名单（`plan_line_transition`）裁决，
    这里不设阶段 A 专属的关卡——比如「已提交→已确认」这条 forward 边今天就走得通，
    是因为它在白名单里，不是因为这段代码替它开了路。非法就 422 并回显 allowed[]，
    让「不在白名单里」看起来就是「现在不行」，而不是一个默默成功的动作。
    """
    to_state = (body.get("to_state") or "").strip()
    lid, from_state, new_state = _transition(line_id, to_state, (body.get("reason") or "").strip(),
                                             who, f"transition:{line_id}")
    log.info("op=move_line line_id=%s from=%s to=%s actor=%s", lid, from_state, new_state, who)
    return {"line_id": lid, "state": new_state}
