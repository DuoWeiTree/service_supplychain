from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from api.ui.deps import actor_optional, declared
from usecase import attention as attention_uc

router = APIRouter()


@router.get("/attention")
def queue(request: Request, who: str | None = Depends(actor_optional)):
    """顶栏常驻红条的数据源（spec §6.6 ①）：不可关闭，直到这里为空。"""
    declared(request)
    items = attention_uc.queue()
    return {"count": len(items), "items": items}
