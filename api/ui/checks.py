"""G3：v_check_* 汇总。空 = 通过；非空就点名每一行（spec §11.1）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from api.ui.deps import actor_optional, declared
from shared.pg_client import pg_conn, timed

router = APIRouter()
CHECKS = ("po_source", "line_state", "po_unplanned", "po_landed")


@router.get("/checks/purchase")
def purchase_checks(request: Request, who: str | None = Depends(actor_optional)):
    declared(request)
    with timed("checks_purchase", actor=who), pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT check_name, ref FROM v_check_purchase ORDER BY check_name")
        violations = [{"check_name": a, "ref": b} for a, b in cur.fetchall()]
    counts = {k: sum(1 for v in violations if v["check_name"] == k) for k in CHECKS}
    return {"ok": not violations, "counts": counts, "violations": violations}
