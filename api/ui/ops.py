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
    except KeyError as e:
        raise ApiError(404, "unknown_mirror", "登记表里没有这个镜像",
                       {"only": body.only}) from e
    return {"runs": [r._asdict() for r in runs]}
