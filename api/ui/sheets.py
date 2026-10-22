"""排货表端点（spec 2026-10-10 §6）。★ 这里只做 HTTP ↔ usecase 的翻译。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from api.common.deps import actor, actor_optional, declared, require_fresh_mirrors
from api.common.errors import ApiError
from api.common.source_factory import stock_dep
from usecase import draft_po, live_read, logistics, receipt, sheet, submit_ows

router = APIRouter(dependencies=[Depends(require_fresh_mirrors)])


def _call(fn, *a):
    try:
        return fn(*a)
    except (sheet.Conflict, draft_po.Conflict) as e:
        raise ApiError(e.status, e.error, e.hint, e.fields) from None


@router.post("/sheets", status_code=201)
def create(body: dict, request: Request, who: str = Depends(actor), make_stock=Depends(stock_dep)):
    declared(request)
    return _call(sheet.create, body or {}, who, make_stock)


@router.get("/dispatch/plans")
def dispatch_plans(request: Request, who: str | None = Depends(actor_optional)):
    """CA-D11 入口：未完成的销售计划。"""
    declared(request)
    return {"plans": _call(sheet.dispatch_plans)}


@router.get("/sheets")
def list_(request: Request, who: str | None = Depends(actor_optional)):
    declared(request, "state")
    return {"sheets": _call(sheet.list_sheets, request.query_params.get("state"))}


@router.get("/sheets/{sheet_id}")
def get_(sheet_id: int, request: Request, who: str | None = Depends(actor_optional)):
    declared(request)
    return _call(sheet.load, sheet_id)


@router.get("/sheets/{sheet_id}/grid")
def grid(sheet_id: int, request: Request, who: str | None = Depends(actor_optional),
         make_stock=Depends(stock_dep)):
    declared(request)
    return _call(sheet.grid, sheet_id, make_stock)


@router.put("/sheets/{sheet_id}/cells")
def put_cell(sheet_id: int, body: dict, request: Request, who: str = Depends(actor),
             make_stock=Depends(stock_dep)):
    declared(request)
    return _call(sheet.put_cell, sheet_id, body or {}, who, make_stock)


@router.put("/sheets/{sheet_id}/packing/{seller_sku}/{sid}/{from_wid}")
def put_packing(sheet_id: int, seller_sku: str, sid: str, from_wid: int, body: dict, request: Request,
                who: str = Depends(actor)):
    declared(request)
    return _call(sheet.put_packing, sheet_id, (seller_sku, sid, from_wid), body or {}, who)


@router.get("/sheets/{sheet_id}/preflight")
def preflight(sheet_id: int, request: Request, who: str | None = Depends(actor_optional),
              make_stock=Depends(stock_dep)):
    declared(request)
    return _call(sheet.preflight, sheet_id, make_stock)


@router.post("/sheets/{sheet_id}/confirm")
def confirm(sheet_id: int, body: dict, request: Request, who: str = Depends(actor),
            make_stock=Depends(stock_dep)):
    declared(request)
    return _call(sheet.confirm, sheet_id, body or {}, who, make_stock)


@router.post("/sheets/{sheet_id}/cancel")
def cancel(sheet_id: int, body: dict, request: Request, who: str = Depends(actor)):
    declared(request)
    return _call(sheet.cancel, sheet_id, body or {}, who)


@router.post("/sheets/{sheet_id}/live-read")
def live_read_(sheet_id: int, body: dict, request: Request, who: str = Depends(actor)):
    """CA-D12「现查领星」：这个货号的本地库存 + 关联已下单采购单的读回。"""
    declared(request)
    return _call(live_read.refresh, sheet_id, body or {}, who)


@router.get("/logistics-channels")
def logistics_channels(request: Request, who: str | None = Depends(actor_optional)):
    """CB-D3：领星头程物流渠道（镜像；空或陈旧时现刷）。"""
    declared(request)
    return _call(logistics.list_channels, who)


@router.post("/logistics-channels/refresh")
def logistics_refresh(request: Request, who: str = Depends(actor)):
    declared(request)
    return {"channels": _call(logistics.refresh, who)}


@router.put("/sheets/{sheet_id}/logistics/{dest_wid}")
def put_logistics(sheet_id: int, dest_wid: int, body: dict, request: Request, who: str = Depends(actor)):
    declared(request)
    return _call(logistics.set_for_sheet, sheet_id, dest_wid, body or {}, who)


@router.get("/sheets/{sheet_id}/submission")
def submission(sheet_id: int, request: Request, who: str | None = Depends(actor_optional)):
    """C-b：将建的领星备货单（逐张）· 已建的 · 阻断项 · 待核实 · confirm_token。"""
    declared(request)
    return _call(submit_ows.preview, sheet_id)


@router.post("/sheets/{sheet_id}/submit")
def submit(sheet_id: int, body: dict, request: Request, who: str = Depends(actor)):
    """C-b ⛔ 提交领星：逐张建备货单（status 40），已建的跳过；结果未知转待核实，绝不自动重试。"""
    declared(request)
    return _call(submit_ows.submit, sheet_id, body or {}, who)


@router.post("/sheets/{sheet_id}/ext-refs/verify")
def ext_verify(sheet_id: int, body: dict, request: Request, who: str = Depends(actor)):
    declared(request)
    return _call(submit_ows.verify, sheet_id, body or {}, who)


@router.post("/sheets/{sheet_id}/ext-refs/register")
def ext_register(sheet_id: int, body: dict, request: Request, who: str = Depends(actor)):
    declared(request)
    return _call(submit_ows.register, sheet_id, body or {}, who)


@router.post("/sheets/{sheet_id}/ext-refs/confirm-absent")
def ext_confirm_absent(sheet_id: int, body: dict, request: Request, who: str = Depends(actor)):
    declared(request)
    return _call(submit_ows.confirm_absent, sheet_id, body or {}, who)


@router.post("/sheets/{sheet_id}/reopen")
def reopen_sheet(sheet_id: int, body: dict, request: Request, who: str = Depends(actor)):
    declared(request)
    return _call(sheet.reopen, sheet_id, body or {}, who)


@router.post("/sheets/{sheet_id}/receipt")
def sheet_receipt(sheet_id: int, request: Request, who: str = Depends(actor)):
    """现查领星状态（spec 2026-10-13 §5）：这张表已投送排货单的领星单逐张读回。"""
    declared(request)
    return _call(receipt.run, sheet_id, who)


@router.get("/oversea-sku-match")
def oversea_sku_match(request: Request, who: str | None = Depends(actor_optional)):
    """选目的仓时现查：这个货号在这个第三方海外仓配没配对三方 sku（同提交时的判据，CB-D8）。"""
    declared(request, "wid", "sku")
    wid, sku = request.query_params.get("wid"), (request.query_params.get("sku") or "").strip()
    if not (wid or "").isdigit() or not sku:
        raise ApiError(400, "missing_required", "要带 wid（整数）与 sku", {"missing": [k for k, v in
                                                                         (("wid", wid), ("sku", sku)) if not v]})
    return _call(submit_ows.sku_match_status, int(wid), sku)
