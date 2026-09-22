"""计划单记录的查询与状态动作。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from api.ui.deps import actor, actor_optional, declared, require_fresh_mirrors
from api.ui.errors import ApiError
from shared.pg_client import pg_conn, timed

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

    where, args = ["true"], []
    if q.get("plan_ids"):
        try:
            ids = [int(x) for x in q["plan_ids"].split(",") if x]
        except ValueError:
            raise ApiError(400, "bad_plan_ids", "plan_ids 必须是逗号分隔的整数",
                           {"got": q["plan_ids"]}) from None
        where.append("l.plan_id = ANY(%s)")
        args.append(ids)
    for col, key in (("l.state", "state"), ("l.sku", "sku")):
        if q.get(key):
            where.append(f"{col} = %s")
            args.append(q[key])
    if q.get("period"):
        where.append("l.period_start = %s")
        args.append(q["period"] + "-01")

    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT l.line_id, l.plan_id, l.rev, l.sku, l.period_start, l.total_units,"
                    " l.demand_at_submit, l.demand_by_seller, l.state"
                    f" FROM plan_line l WHERE {' AND '.join(where)}"
                    " ORDER BY l.plan_id, l.rev, l.sku, l.period_start", args)
        lines = [{"line_id": r[0], "plan_id": r[1], "rev": r[2], "sku": r[3],
                  "period": r[4].strftime("%Y-%m"), "total_units": r[5],
                  "demand_at_submit": r[6], "demand_by_seller": r[7], "state": r[8]}
                 for r in cur.fetchall()]
        cur.execute("SELECT count(*) FROM plan_line")
        total = cur.fetchone()[0]

    groups: dict[str, dict] = {}
    if group_by:
        key = "sku" if group_by == "sku" else "period"
        for row in lines:
            slot = groups.setdefault(row[key], {"lines": 0, "total_units": 0,
                                                "demand_at_submit": 0})
            slot["lines"] += 1
            slot["total_units"] += row["total_units"]
            slot["demand_at_submit"] += row["demand_at_submit"]
    # ★ 过滤必须同时统计被丢掉的那一侧
    return {"lines": lines, "groups": groups,
            "excluded": {"filtered_out": total - len(lines)}}


def _transition(line_id: int, to_state: str, reason: str, who: str, src: str):
    with timed("plan_line_transition", actor=who, line_id=line_id), \
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
    return {"line_id": line_id, "state": to_state}


@router.post("/plan-lines/{line_id}/cancel")
def cancel_line(line_id: int, body: dict, who: str = Depends(actor)):
    reason = (body.get("reason") or "").strip()
    if not reason:
        raise ApiError(400, "reason_required", "撤销必须填理由", {"field": "reason"})
    return _transition(line_id, "已撤销", reason, who, f"cancel_line:{line_id}")


@router.post("/plan-lines/{line_id}/transition")
def move_line(line_id: int, body: dict, who: str = Depends(actor)):
    """★ 阶段 A 里唯一走得通的目标是「已撤销」（C 类，人做的）。

    其余目标态全是 A 类 —— 由承重墙①②的量推出来，属阶段 B/C。
    这个端点**不替它们提前开路**：非法就 422 并回显 allowed[]，
    让「阶段 A 还没有这条路」看起来就是「现在不行」，而不是一个默默成功的动作。
    """
    to_state = (body.get("to_state") or "").strip()
    return _transition(line_id, to_state, (body.get("reason") or "").strip(), who,
                       f"transition:{line_id}")
