"""导入与模板的 HTTP 壳 —— api/ui 与 api/pub 共用这一份（IM-D6：两条路必须同一行为）。

两边路由只差身份依赖；解析、翻译、日志都在这里，不各抄一份（各抄一份已经漂过一次）。
"""
from __future__ import annotations

import dataclasses
import logging

from fastapi.responses import JSONResponse, Response

from api.common.errors import ApiError
from api.common.source_errors import SEED_ERRORS, SOURCE_ERRORS, seed_error
from erp.transport import cause_chain
from shared.pg_client import timed
from usecase.plan_import import ImportRejected, import_plan, parse_header, plan_periods_ym
from usecase.plan_xlsx import build_template

log = logging.getLogger("scm.api")

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def header_400(errs) -> ApiError:
    """模板与预览的表头错：码沿用 POST /plans（R4），范围错归 validation_error；全部错误列在 errors。"""
    code = "bad_date" if any(e.code == "bad_date" for e in errs) else "validation_error"
    return ApiError(400, code, errs[0].message,
                    {"field": errs[0].extra.get("field"), "got": errs[0].extra.get("got"),
                     "errors": [dataclasses.asdict(e) for e in errs]})


def run_import(source, title: str, period_start: str, months: str, who: str, data: bytes,
               op: str) -> JSONResponse:
    header, errs = parse_header(title, period_start, months, who)
    try:
        with timed(op, actor=who, file_bytes=len(data)):
            if header is None:
                raise ImportRejected(errs)   # 表头不成立，月份列无从比对，文件不判
            out = import_plan(source, header, data, who)
    except ImportRejected as e:
        errs = [dataclasses.asdict(x) for x in e.errors]
        raise ApiError(422, "import_rejected", f"有 {len(errs)} 处问题，计划没建",
                       {"errors": errs}) from None
    except SEED_ERRORS + SOURCE_ERRORS as e:
        log.warning("op=%s actor=%s outcome=%s cause=%r", op, who, type(e).__name__,
                    cause_chain(e))
        raise seed_error(e) from e
    return JSONResponse(status_code=201, content=out)


def template_response(period_start: str, months: str) -> Response:
    header, errs = parse_header("-", period_start, months, "-")
    if header is None:
        raise header_400(errs)
    name = f"plan-import-{header.period_start:%Y-%m}-{header.months}m.xlsx"
    return Response(build_template(plan_periods_ym(header.period_start, header.months)),
                    media_type=XLSX,
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
