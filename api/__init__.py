"""api/ui —— 给本仓前端的 BFF。★ 随界面一起变，不进契约（01 §1.1b）。"""
from __future__ import annotations

import contextlib
import logging

import psycopg2
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.ui import catalog, dashboard, lines, plans, submit, system
from api.ui.errors import ApiError, translate
from shared.pg_client import business_schema, pg_conn

log = logging.getLogger("scm.api")

#: status → (error, hint)。S-30：框架产生的 404/405 也要走 S-29 的形状。
_FRAMEWORK_STATUS_CODES: dict[int, tuple[str, str]] = {
    404: ("not_found", "检查路径是否拼对，或该路由还没实现"),
    405: ("method_not_allowed", "换成这条路径声明的 HTTP 方法"),
}


async def _reshape_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """S-30：框架/路由层自己抛的 HTTPException 也要落成 {error, hint, …} 形状。

    ★ `detail` 已经是 `{"error": …}` 形状时原样放行 —— 这是给显式
    `raise HTTPException(status_code=…, detail={"error": …})` 的口子留的，
    本任务目前没有调用点，但重新套一层形状等于丢掉调用方已经给出的点名字段。
    """
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    error, hint = _FRAMEWORK_STATUS_CODES.get(exc.status_code, ("http_error", "检查请求是否符合接口约定"))
    return JSONResponse(status_code=exc.status_code, content={
        "error": error, "hint": hint, "path": request.url.path, "method": request.method,
    })


async def _reshape_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """S-30：请求体/参数没通过 Pydantic 校验 —— 400 而不是框架默认的 422。

    ★ 本仓 422 专留给 `illegal_transition`（S-18：非法状态迁移，`08:51` 自洽）；
    请求本身格式不对是「你写错了」，按 `08` §0 状态码表走 400，不与状态机语义混用。
    """
    fields = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
    return JSONResponse(status_code=400, content={
        "error": "validation_error", "hint": "按 fields 逐项改", "fields": fields,
    })


def _log_startup() -> None:
    """★ 配置类问题往启动钩子放，别等第一个请求才炸 ——
    在启动日志第一屏可见，胜过淹没在访问日志里的一片 500。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT mirror, refreshed_at FROM v_mirror_freshness ORDER BY mirror")
        for mirror, at in cur.fetchall():
            log.info("startup mirror=%s refreshed_at=%s", mirror, at)
        cur.execute("SELECT count(*) FROM schema_migration")
        log.info("startup schema=%s migrations=%d", business_schema(), cur.fetchone()[0])


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI):
    # ★ FastAPI 0.141 移除了 add_event_handler（曾经只是 deprecated）——
    #   lifespan 是现在唯一的启动钩子入口，行为等价：serve 第一个请求前跑完。
    _log_startup()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="service_supplychain · api/ui", lifespan=_lifespan)

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status, content=exc.body())

    @app.exception_handler(psycopg2.Error)
    async def _pg_error(request: Request, exc: psycopg2.Error):
        translated = translate(exc)
        if translated is None:
            raise exc     # ★ 认不出的约束不许被兜成业务错误，让它以 500 冒出来
        return JSONResponse(status_code=translated.status, content=translated.body())

    app.add_exception_handler(StarletteHTTPException, _reshape_http_error)
    app.add_exception_handler(RequestValidationError, _reshape_validation_error)

    app.include_router(system.health_router)
    app.include_router(system.system_router, prefix="/v1")
    app.include_router(plans.router, prefix="/v1")
    app.include_router(submit.router, prefix="/v1")
    app.include_router(catalog.router, prefix="/v1")
    app.include_router(lines.router, prefix="/v1")
    app.include_router(dashboard.router, prefix="/v1")
    return app
