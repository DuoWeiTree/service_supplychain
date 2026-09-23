"""运维触发：手动重跑镜像刷新（07:482「另留一个仅运维可用的手动触发」）。

★ 本端点**不挂** `require_fresh_mirrors` —— 镜像陈旧时它恰恰是用来修的那个口子，
挂上去就变成「陈旧 → 503 → 无法刷新 → 永远陈旧」。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from api.ui.deps import actor, declared
from api.ui.errors import ApiError
from jobs import refresh_dims
from jobs.lock import RefreshInFlight

router = APIRouter()


class RefreshBody(BaseModel):
    only: str | None = None


@router.post("/jobs/refresh-dims")
def refresh_dims_now(request: Request, body: RefreshBody, who: str = Depends(actor)):
    declared(request)
    try:
        runs = refresh_dims.refresh_all("api", actor=who, only=body.only)
    except RefreshInFlight as e:
        raise ApiError(409, "refresh_in_flight", str(e), {}) from e
    except refresh_dims.ChUnreachable as e:
        # ★ 终审 I-3：镜像陈旧多半**就是因为** CH 连不上，而这时来点这个专门用来
        #   修它的端点，原先拿到的是不符合 S-29/S-30 形状的裸 500。
        # ★ 503 而不是 500：上游依赖不可用与「我们算错了」调用方的处置不同。
        #   只兜这一种异常、不写 `except Exception` 兜底 —— 那会把编程错误也
        #   涂成一次「上游抖动」，正是本仓点名过的静默兜底。
        raise ApiError(503, "refresh_failed",
                       "上游 ClickHouse 连不上，这一轮一张镜像都没刷；"
                       "dim_refresh_run 里每张镜像各留了一行 ok=false",
                       {"target": e.target, "cause": e.cause}) from e
    except KeyError as e:
        raise ApiError(404, "unknown_mirror", "登记表里没有这个镜像",
                       {"only": body.only}) from e
    return {"runs": [r._asdict() for r in runs]}
