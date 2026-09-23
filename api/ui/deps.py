"""三样每个业务端点都要过的东西：操作人 · 未声明参数 · 镜像新鲜度。"""
from __future__ import annotations

import datetime as dt

from fastapi import Request

from api.ui.errors import ApiError
from shared.config import freshness
from shared.pg_client import pg_conn, timed


def declared(request: Request, *names: str) -> None:
    """★ 未声明查询参数一律 400：拼错的参数被静默忽略时，
    返回的是「全量」而不是报错 —— 而全量看起来完全正常。"""
    extra = sorted(set(request.query_params) - set(names))
    if extra:
        raise ApiError(400, "unknown_query_param", "有未声明的查询参数",
                       {"unknown": extra, "declared": sorted(names)})


def require_fresh_mirrors() -> None:
    """E-4：任一维度镜像陈旧（或为空）→ 拒绝服务。"""
    max_age = dt.timedelta(hours=float(freshness().get("max_age_hours", 24)))
    now = dt.datetime.now(dt.UTC)
    stale = []
    # ★ 这是挂在五个业务路由上的依赖，每个请求都要跑一次 —— 不包 timed()，
    #   PG 慢或连不上时它只会以一句 500 冒出来，打的哪个库、等了多久全都没有。
    with timed("require_fresh_mirrors"), pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT mirror, refreshed_at FROM v_mirror_freshness ORDER BY mirror")
        for mirror, at in cur.fetchall():
            # ★ NULL 是「一行都没有」，不是「刚刷过」
            if at is None or now - at > max_age:
                stale.append({"mirror": mirror,
                              "refreshed_at": at.isoformat() if at else None})
    if stale:
        raise ApiError(503, "mirror_stale", "维度镜像陈旧，拒绝服务",
                       {"stale": stale, "max_age_hours": max_age.total_seconds() / 3600})


def _check_actor(who: str) -> str:
    with timed("check_actor", actor=who), pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT active FROM actor WHERE actor_id = %s", (who,))
        row = cur.fetchone()
    if row is None:
        raise ApiError(400, "unknown_actor", "x-actor 不在 actor 表里", {"actor": who})
    if not row[0]:
        # ★ 与「查无此人」分得开：停用的人会以为自己打错了名字
        raise ApiError(400, "unknown_actor", "该操作人已停用", {"actor": who, "active": False})
    return who


def actor(request: Request) -> str:
    """写请求的操作人（裁定第 6 条）。必须存在且在职。

    ★ 校验的是「这个人存不存在」，不是权限（权限在上层，08 §0）——
      不校验的话，plan.owner_actor 的外键会以 500 的形态在半路炸。
    """
    who = (request.headers.get("x-actor") or "").strip()
    if not who:
        raise ApiError(400, "unknown_actor", "缺少 x-actor 头", {"header": "x-actor"})
    return _check_actor(who)


def actor_optional(request: Request) -> str | None:
    """读请求的操作人：不给可以，★ 给了就必须有效。

    给了个错名字却照常返回全量，比不让读更坏 —— 人会以为自己看的是那个人的视角。
    """
    who = (request.headers.get("x-actor") or "").strip()
    return _check_actor(who) if who else None


def ensure_plan(cur, plan_id: int) -> None:
    """★ 没有这一步，`plan_rev_plan_id_fkey`（未命名，`translate()` 认不出）
    会让「计划不存在」以 500 冒出来，而不是本仓其余端点统一给的 404。"""
    cur.execute("SELECT 1 FROM plan WHERE plan_id = %s", (plan_id,))
    if cur.fetchone() is None:
        raise ApiError(404, "plan_not_found", "计划不存在", {"plan_id": plan_id})


def ensure_writable(cur, plan_id: int) -> None:
    """归档后一律拒写（409 plan_archived）；读不受影响。

    ★ 归档是**一次事务里释放全部占用**。若归档后还能写，紧接着的一次认领就把刚
      释放的 msku 又扣回去 —— 而 `GET /v1/plans` 默认不显示已归档的计划，于是那个
      msku 被一张「看不见的计划」占着，判据③ 正是这面墙。
    ★ 与「计划不存在」分得开：404 是没有这张计划，409 是有、但它已经封存了。
    """
    cur.execute("SELECT archived_at FROM plan WHERE plan_id = %s", (plan_id,))
    row = cur.fetchone()
    if row is None:
        raise ApiError(404, "plan_not_found", "计划不存在", {"plan_id": plan_id})
    if row[0] is not None:
        raise ApiError(409, "plan_archived", "计划已归档，不接受写入",
                       {"plan_id": plan_id, "archived_at": row[0].isoformat()})


def known_states(cur) -> list[str]:
    """★ 状态值的白名单只有一个出处：`plan_line_state_rank`（004）+ 旁路终态
    「已撤销」（004 注释：它刻意不进 rank 表）。

    三个调用者共用这一份：`lines._allowed_next` 校验迁移、`/v1/plan-lines` 与
    `/v1/plans` 校验 `state=` 查询参数、看板派生统计桶 ——
    各自维护一份宇宙必然分叉，而分叉的形态是「同一个状态在两个屏幕上待遇不同」。
    """
    cur.execute("SELECT state FROM plan_line_state_rank ORDER BY rank")
    return [r[0] for r in cur.fetchall()] + ["已撤销"]
