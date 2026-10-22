"""销售计划 Excel 导入的界面壳：解析表单 → usecase.plan_import（裁决都在那边）。"""
from __future__ import annotations

import dataclasses
import datetime as dt
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from api.common.deps import actor, declared, require_fresh_mirrors
from api.common.errors import ApiError
from api.common.source_errors import SOURCE_ERRORS, source_error
from api.common.source_factory import source_dep
from dim.source import Source
from shared.pg_client import timed
from usecase.plan_import import ImportRejected, import_plan, parse_header, plan_periods_ym
from usecase.plan_xlsx import build_template

log = logging.getLogger("scm.api")

router = APIRouter()

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.post("/plans/import", dependencies=[Depends(require_fresh_mirrors)])
def import_plan_ep(request: Request, source: Annotated[Source, Depends(source_dep)],
                   who: str = Depends(actor),
                   title: str = Form(""), period_start: str = Form(""),
                   months: str = Form(""), file: UploadFile = File(...)):
    declared(request)
    data = file.file.read()
    header, errs = parse_header(title, period_start, months, who)
    try:
        with timed("plan_import", actor=who, file_bytes=len(data)):
            if header is None:
                raise ImportRejected(errs)   # 表头不成立，月份列无从比对，文件不判
            out = import_plan(source, header, data, who)
    except ImportRejected as e:
        errs = [dataclasses.asdict(x) for x in e.errors]
        raise ApiError(422, "import_rejected", f"有 {len(errs)} 处问题，计划没建",
                       {"errors": errs}) from None
    except SOURCE_ERRORS as e:
        log.warning("op=plan_import_api actor=%s outcome=%s cause=%s", who, type(e).__name__,
                    getattr(e, "cause", None))
        raise source_error(e) from e
    return JSONResponse(status_code=201, content=out)


@router.get("/plans/import/template")
def template(request: Request, period_start: str = "", months: str = "3"):
    declared(request, "period_start", "months")
    header, errs = parse_header("-", period_start, months, "-")
    if header is None:
        e = errs[0]
        code = "bad_date" if e.code == "bad_date" else "validation_error"
        raise ApiError(400, code, e.message, {"field": e.extra.get("field"), "got": e.extra.get("got")})
    name = f"plan-import-{header.period_start:%Y-%m}-{header.months}m.xlsx"
    return Response(build_template(plan_periods_ym(header.period_start, header.months)),
                    media_type=XLSX,
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
