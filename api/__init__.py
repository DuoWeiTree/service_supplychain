"""api/ui —— 给本仓前端的 BFF。★ 随界面一起变，不进契约（01 §1.1b）。"""
from __future__ import annotations

import contextlib
import logging

import psycopg2
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from api.ui import system
from api.ui.errors import ApiError, translate
from shared.pg_client import business_schema, pg_conn

log = logging.getLogger("scm.api")


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

    app.include_router(system.health_router)
    app.include_router(system.system_router, prefix="/v1")
    return app
