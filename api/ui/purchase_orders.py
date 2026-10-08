"""采购单端点（spec §5）。★ 这里只做 HTTP ↔ usecase 的翻译。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from api.ui.deps import actor, actor_optional, declared
from api.ui.errors import ApiError
from repo import purchase as repo
from shared.pg_client import pg_conn, timed
from usecase import attention as attention_uc
from usecase import draft_po, place_po

router = APIRouter()


def _call(fn, *a):
    try:
        return fn(*a)
    except draft_po.Conflict as e:
        raise ApiError(e.status, e.error, e.hint, e.fields) from None


@router.post("/purchase-orders", status_code=201)
def create(body: dict, request: Request, who: str = Depends(actor)):
    declared(request)
    return {"po_id": _call(draft_po.create_draft, body, who)}


@router.get("/purchase-orders")
def list_(request: Request, who: str | None = Depends(actor_optional)):
    declared(request, "state")
    with timed("list_po", actor=who), pg_conn() as c, c.cursor() as cur:
        return {"purchase_orders": repo.list_pos(cur, request.query_params.get("state"))}


@router.get("/purchase-orders/{po_id}")
def get_(po_id: int, request: Request, who: str | None = Depends(actor_optional)):
    declared(request)
    return _call(draft_po.load_po, po_id)


@router.put("/purchase-orders/{po_id}")
def update(po_id: int, body: dict, request: Request, who: str = Depends(actor)):
    declared(request)
    return {"po_id": po_id, "draft_version": _call(draft_po.update_draft, po_id, body, who)}


@router.delete("/purchase-orders/{po_id}", status_code=204)
def delete(po_id: int, request: Request, who: str = Depends(actor)):
    declared(request)
    _call(draft_po.delete_draft, po_id, who)
    return Response(status_code=204)


@router.post("/purchase-orders/{po_id}/place")
def place(po_id: int, body: dict, request: Request, who: str = Depends(actor)):
    declared(request)
    return _call(place_po.place, po_id, (body or {}).get("confirm_token") or "", who)


@router.get("/purchase-orders/{po_id}/timeline")
def timeline(po_id: int, request: Request, who: str | None = Depends(actor_optional)):
    declared(request)
    return _call(attention_uc.timeline, po_id)


@router.post("/purchase-orders/{po_id}/recheck")
def recheck(po_id: int, request: Request, who: str = Depends(actor)):
    declared(request)
    return _call(attention_uc.recheck, po_id, who)


@router.post("/purchase-orders/{po_id}/register-existing")
def register_existing(po_id: int, body: dict, request: Request, who: str = Depends(actor)):
    declared(request)
    return _call(attention_uc.register_existing, po_id, (body or {}).get("order_sn") or "", who)


@router.post("/purchase-orders/{po_id}/confirm-absent")
def confirm_absent(po_id: int, body: dict, request: Request, who: str = Depends(actor)):
    declared(request)
    return _call(attention_uc.confirm_absent, po_id, (body or {}).get("reason") or "", who)


@router.post("/purchase-orders/{po_id}/acknowledge-mismatch")
def acknowledge_mismatch(po_id: int, body: dict, request: Request, who: str = Depends(actor)):
    declared(request)
    return _call(attention_uc.acknowledge_mismatch, po_id, (body or {}).get("reason") or "", who)


@router.get("/purchase-orders/{po_id}/preflight")
def preflight_(po_id: int, request: Request, who: str | None = Depends(actor_optional)):
    declared(request)
    po = _call(draft_po.load_po, po_id)
    return {"po_id": po_id, "ok": all(g["ok"] for g in po["preflight"]), "gates": po["preflight"]}
