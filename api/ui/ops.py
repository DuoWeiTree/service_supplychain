"""运维触发：手动重跑镜像刷新（07:482「另留一个仅运维可用的手动触发」）。

★ 本端点**不挂** `require_fresh_mirrors` —— 镜像陈旧时它恰恰是用来修的那个口子，
挂上去就变成「陈旧 → 503 → 无法刷新 → 永远陈旧」。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from api.ui.deps import actor, declared
from api.ui.errors import ApiError
from dim import registry
from jobs import refresh_dims
from jobs.lock import RefreshInFlight

router = APIRouter()


class RefreshBody(BaseModel):
    only: str | None = None


@router.post("/jobs/refresh-dims")
def refresh_dims_now(request: Request, body: RefreshBody, who: str = Depends(actor)):
    declared(request)
    # ★ 复审残留 ③：名字在调用**之前**验。原先靠函数末尾的 `except KeyError`，
    #   于是任何从刷新深处冒上来的 `KeyError`（编程错误、字典拿错键）都被报成
    #   404「登记表里没有这个镜像」，运维会去查一个拼写正确的名字为什么不存在。
    #   这与同函数里拒绝 `except Exception` 兜底是同一条理由，只是换了异常类型。
    # ★ 只把 `registry.by_name` 这一次包起来：验过之后再冒出来的 KeyError
    #   就不再是「名字错了」，必须走真正的错误路径。
    # ⚠ pending 条目（登记了但本阶段没有取数实现）现在会以 500 冒出来，
    #   而不是原先那句措辞错误的 404 —— 它该有自己的错误码（`mirror_pending`
    #   409），新增错误码要连 `docs/08` §3 一起做，已记进 parked-findings。
    if body.only is not None:
        try:
            registry.by_name(body.only)
        except KeyError as e:
            raise ApiError(404, "unknown_mirror", "登记表里没有这个镜像",
                           {"only": body.only}) from e
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
    return {"runs": [r._asdict() for r in runs]}
