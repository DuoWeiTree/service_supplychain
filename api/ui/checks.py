"""G3：v_check_* 汇总。空 = 通过；非空就点名每一行（spec §11.1）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from api.common.deps import actor_optional, declared
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


DISPATCH_CHECKS = ("map_ne_arrangement", "line_ne_sum_map", "over_total", "msku_sku_drift", "line_not_dispatchable")


@router.get("/checks/dispatch")
def dispatch_checks(request: Request, who: str | None = Depends(actor_optional)):
    """承重墙②：安排 ≡ 映射 · 排货行 ≡ Σ映射（计划 + 无计划来源）· 不超总量 · msku 未改挂。"""
    declared(request)
    with timed("checks_dispatch", actor=who), pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT check_name, ref FROM v_check_dispatch ORDER BY check_name")
        violations = [{"check_name": a, "ref": b} for a, b in cur.fetchall()]
    counts = {k: sum(1 for v in violations if v["check_name"] == k) for k in DISPATCH_CHECKS}
    return {"ok": not violations, "counts": counts, "violations": violations}
