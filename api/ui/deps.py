"""三样每个业务端点都要过的东西：操作人 · 未声明参数 · 镜像新鲜度。"""
from __future__ import annotations

import datetime as dt

from fastapi import Request

from api.ui.errors import ApiError
from shared.config import freshness
from shared.pg_client import pg_conn


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
    with pg_conn() as c, c.cursor() as cur:
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
    with pg_conn() as c, c.cursor() as cur:
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
