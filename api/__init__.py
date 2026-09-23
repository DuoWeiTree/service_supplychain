"""api/ui —— 给本仓前端的 BFF。★ 随界面一起变，不进契约（01 §1.1b）。"""
from __future__ import annotations

import contextlib
import datetime as dt
import logging

import psycopg2
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.ui import catalog, dashboard, lines, ops, plans, source_factory, submit, system
from api.ui.errors import ApiError, translate
from dim.registry import gate_503_names
from jobs import refresh_dims
from jobs import scheduler as job_scheduler
from jobs.lock import RefreshInFlight
from shared import config as config_module
from shared.logging import setup_logging
from shared.pg_client import business_schema, pg_conn, pg_error_fields, pg_target, timed

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


def _stale_mirrors() -> list[dict]:
    """★ 打全部镜像的新鲜度日志（不管新不新鲜），只把陈旧的 gate_503 镜像挑进返回值。"""
    max_age = dt.timedelta(hours=float(config_module.freshness()["max_age_hours"]))
    now = dt.datetime.now(dt.UTC)
    gated = gate_503_names()
    out = []
    with timed("startup"), pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT mirror, refreshed_at FROM v_mirror_freshness ORDER BY mirror")
        for mirror, at in cur.fetchall():
            log.info("startup mirror=%s refreshed_at=%s", mirror, at)
            # ★ NULL 是「一行都没有」，不是「刚刷过」
            if mirror in gated and (at is None or now - at > max_age):
                out.append({"mirror": mirror,
                            "refreshed_at": at.isoformat() if at else None})
        cur.execute("SELECT count(*) FROM schema_migration")
        log.info("startup schema=%s migrations=%d", business_schema(), cur.fetchone()[0])
    return out


def _startup_check() -> None:
    """★ OQ-8 裁定（控制器 09-22）：不拦启动、不拦 /health · /v1/readiness——
    只记录 + 在需要时触发一次立即刷新。真正的拒绝服务只在业务路由按请求判
    （api/ui/deps.py::require_fresh_mirrors）。

    ★ 全程不许抛出会中断 lifespan 的异常：触发的这次刷新本身失败也只记日志——
    静默兜底是最坏的一种，但「刷新失败」和「应用起不来」是两件不同的事，
    把二者绑在一起就是把一次 CH 抖动变成一次人工到场（设计 §10.1 OQ-8）。

    ★ 终审 I-1：**读新鲜度**这一步也在「全程」里。它原先排在 try 之外，于是
      PG 连不上时异常直接冲出 lifespan，`/health` 与 `/v1/readiness` 一起没了——
      而那正是要用来查「为什么起不来」的两个口子（OQ-8 裁定的原话）。
      读不到就不知道该不该刷，所以这里只记一条点名成因的日志然后照常返回，
      不猜「大概是新鲜的」也不猜「大概该刷一轮」。
    """
    try:
        stale = _stale_mirrors()
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:  # noqa: BLE001 - 见上：这一步失败不许中断 lifespan
        log.warning("startup outcome=fail stage=freshness_read err_type=%s pg=%s err=%s"
                    " —— 读不到新鲜度，本次不触发刷新；/health 与 /v1/readiness 照常可达",
                    type(e).__name__,
                    pg_error_fields(e) if isinstance(e, psycopg2.Error) else {}, e)
        return
    if not stale:
        return
    if not config_module.freshness()["startup_gate"]:
        log.warning("startup stale=%s startup_gate=false —— 只记录，不触发刷新；"
                    "业务端点仍会逐请求 503", [s["mirror"] for s in stale])
        return
    log.warning("startup stale=%s startup_gate=true —— 触发一次立即刷新",
               [s["mirror"] for s in stale])
    try:
        runs = refresh_dims.refresh_all("scheduler")
        log.info("startup refresh runs=%s", [(r.mirror, r.ok) for r in runs])
    except RefreshInFlight as e:
        # ★ 锁被占是正常状态（另一轮 CLI/运维触发正在跑），不是启动刷新失败——
        #   与 jobs/scheduler.py::_tick 对同一个异常的处理保持同一判据：必须
        #   记成「跳过」，不能跟真失败混进同一种「fail」日志；也不能指望
        #   RefreshInFlight 自己的消息文本足够自解释——异常类名显式打出来，
        #   下次换一种措辞的异常也照样分得清。
        log.warning("startup refresh outcome=skipped reason=busy err_type=%s err=%s",
                   type(e).__name__, e)
    except (KeyboardInterrupt, SystemExit):
        # ★ 终审 M-5：`BaseException` 会把 Ctrl-C / SystemExit 也记成「刷新失败」。
        #   「兜住一切」的意图是「漏一种异常类型就是漏一批沉默失败」，
        #   而这两种不属于那一批——它们是「人让它停」，必须原样往外走。
        raise
    except Exception as e:  # noqa: BLE001 - 必须兜住一切，让 lifespan 永不中断；
        # 「刷新失败」和「应用起不来」是两件不同的事（设计 §10.1 OQ-8）。
        # ★ 只取 str(e) 等于丢了异常类名与 psycopg2 的 pgcode/constraint——同一条
        #   纪律 jobs/refresh_dims.py::refresh_one 已经示范过，这里照抄，不能
        #   各写一套：真超时（TimeoutError）与连不上（TypeError/cause 里的
        #   errno）签名互斥，只看裸消息会把两者混成一句看不出所以然的话。
        if isinstance(e, psycopg2.Error):
            log.warning("startup refresh outcome=fail err_type=%s pg=%s",
                        type(e).__name__, pg_error_fields(e))
        else:
            log.warning("startup refresh outcome=fail err_type=%s err=%s",
                        type(e).__name__, e)


def _database_unavailable(exc: psycopg2.Error) -> ApiError | None:
    """「压根连不上业务库」翻成 503，其余返回 None 交给原来的那条路。

    ★ 复审残留 ①：`_pg_error` 原先把「认不出的约束」与「压根连不上」当成同
    一种放行，于是 PG 挂掉时 `/v1/readiness` 给的是裸 500（纯文本
    `Internal Server Error`，不是 S-29 形状）—— 而那正是要用来查「为什么起
    不来」的口子（OQ-8 原话）。与 I-3 为运维端点选 503 的是同一条理由：
    上游依赖不可用与「我们算错了」调用方的处置不同，同一把尺子要量到底。

    ★ 判据是 `pgcode is None` 而不是「异常类名叫 OperationalError」：
    服务器**回了话**的 OperationalError（如 57P01 管理员关库、53300 连接数
    满）带 pgcode，那是另一类形态，不在这里猜。pgcode 为空 = 这次根本没走到
    服务器，是客户端侧的建连失败。
    """
    if not isinstance(exc, psycopg2.OperationalError) or exc.pgcode is not None:
        return None
    try:
        target = pg_target()
    except Exception:            # noqa: BLE001 - 连配置都读不出来时不许再炸一次
        target = "<配置读不出来>"
    log.warning("op=request outcome=pg_unavailable target=%s err_type=%s err=%s",
                target, type(exc).__name__, exc)
    return ApiError(503, "database_unavailable",
                    "连不上业务库，本次请求没有读到任何数据；"
                    "/health 不依赖它，仍然可达",
                    {"target": target, "err_type": type(exc).__name__})


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI):
    # ★ FastAPI 0.141 移除了 add_event_handler（曾经只是 deprecated）——
    #   lifespan 是现在唯一的启动钩子入口，行为等价：serve 第一个请求前跑完。
    # ★ 取数源的装配已经改成「每请求一次」（终审 C-1/C-2/C-3），于是
    #   `[forecast] source` 配错不再有一个 import 期的单例替它炸。配置类错误
    #   往启动钩子放，别等第一个请求才炸；生效配置也只在这里打一行，
    #   不是每请求打一行去淹没访问日志。★ 这一支**故意**不兜异常：
    #   配错了就该起不来，而不是每个请求各报一次 500。
    source_factory.log_source_config()
    job_scheduler.start()          # ★ 先起调度器：启动检查可能要立刻触发一轮刷新
    try:
        _startup_check()
        yield
    finally:
        job_scheduler.shutdown()


def create_app() -> FastAPI:
    # ★ 第一件事：没有它，下面每一条 INFO（timed() 的耗时、启动钩子、提交后的结果行）
    #   都会落进 logging.lastResort 被按 WARNING 丢掉 —— 本仓全部可追踪性在部署形态下失效。
    setup_logging()
    app = FastAPI(title="service_supplychain · api/ui", lifespan=_lifespan)

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status, content=exc.body())

    @app.exception_handler(psycopg2.Error)
    async def _pg_error(request: Request, exc: psycopg2.Error):
        unavailable = _database_unavailable(exc)
        if unavailable is not None:
            return JSONResponse(status_code=unavailable.status, content=unavailable.body())
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
    app.include_router(ops.router, prefix="/v1")
    return app
