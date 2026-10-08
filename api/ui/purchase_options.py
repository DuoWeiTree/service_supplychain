"""组单表单的候选项（spec §2 实测：采购员 1 人 · 采购方 2 个 · 供应商 3 家）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from api.ui.deps import actor_optional, declared
from shared.pg_client import pg_conn, timed

router = APIRouter()


@router.get("/purchase-options")
def options(request: Request, who: str | None = Depends(actor_optional)):
    declared(request)
    with timed("purchase_options", actor=who), pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT supplier_id, name FROM supplier ORDER BY supplier_id")
        suppliers = [{"supplier_id": a, "name": b} for a, b in cur.fetchall()]
        cur.execute("SELECT DISTINCT purchaser_id FROM po ORDER BY 1")
        purchasers = [{"purchaser_id": r[0]} for r in cur.fetchall()]
        cur.execute("SELECT DISTINCT opt_uid FROM po ORDER BY 1")
        opt_uids = [{"opt_uid": r[0], "name": None} for r in cur.fetchall()]
    return {"suppliers": suppliers, "purchasers": purchasers, "opt_uids": opt_uids,
            "note": "采购方 / 采购员候选取自本服务已建的单；首单时为空，按领星实测值手填（spec §2）"}
