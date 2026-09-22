"""探活与就绪。★ /health 不加前缀（08 §0）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from api.ui.deps import actor_optional, declared
from shared.pg_client import business_schema, pg_conn, timed

health_router = APIRouter()
system_router = APIRouter()


@health_router.get("/health")
def health(request: Request):
    # ★ 探活也要拒未声明参数：唯一不拦的那个端点会变成「先试探活能不能带这个参数」
    declared(request)
    return {"status": "ok"}


@system_router.get("/readiness")
def readiness(request: Request, who: str | None = Depends(actor_optional)):
    declared(request)
    with timed("readiness"), pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT mirror, refreshed_at FROM v_mirror_freshness ORDER BY mirror")
        mirrors = [{"mirror": m, "refreshed_at": a.isoformat() if a else None}
                   for m, a in cur.fetchall()]
        cur.execute("SELECT version FROM schema_migration ORDER BY version")
        versions = [r[0] for r in cur.fetchall()]
    return {
        "schema": business_schema(),
        "migrations": versions,
        "mirrors": mirrors,
        # ★ 阶段 A 没有领星网关。写成 "ok" 或干脆不返回，都会让
        #   「该做没做」和「本来就不用做」长得一模一样（01 规则五）。
        "erp": "not_implemented",
    }
