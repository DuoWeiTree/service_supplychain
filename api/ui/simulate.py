"""「模拟外部」（16 §0 ②）：只在 fake + dev 下开放，扮演领星的返回，不是用户的活。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from api.ui.deps import actor, declared
from api.ui.errors import ApiError
from erp.drivers.fake import FAKE, OUTCOMES
from shared import config as config_module
from shared.pg_client import pg_conn
from usecase import landed

router = APIRouter()


def _only_in_fake() -> None:
    e, a = config_module.erp()["mode"], config_module.auth()["mode"]
    if e != "fake" or a != "dev":
        raise ApiError(403, "simulation_disabled", "模拟外部只在 [erp] mode=fake 且 [auth] mode=dev 下可用",
                       {"erp_mode": e, "auth_mode": a})


@router.post("/simulate/erp/next")
def next_outcome(body: dict, request: Request, who: str = Depends(actor)):
    declared(request)
    _only_in_fake()
    if body.get("outcome") not in OUTCOMES:
        raise ApiError(400, "bad_outcome", "未知的模拟结果", {"got": body.get("outcome"), "allowed": list(OUTCOMES)})
    FAKE.enqueue(body["endpoint"], body["outcome"])
    return {"queued": body}


@router.post("/simulate/po/{po_id}/landed")
def change_landed(po_id: int, body: dict, request: Request, who: str = Depends(actor)):
    declared(request)
    _only_in_fake()
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT order_sn FROM po WHERE po_id=%s AND placed_mode='fake'", (po_id,))
        row = cur.fetchone()
    if row is None:
        raise ApiError(404, "po_not_found", "没有这张 fake 已下单的采购单", {"po_id": po_id})
    FAKE.mutate_item(row[0], body["sku"], body.get("quantity_real"), body.get("price"))
    return {"po_id": po_id, "problems": landed.verify(po_id, who)}
