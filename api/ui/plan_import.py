"""销售计划 Excel 导入的界面壳 —— 与 api/pub 同一个 HTTP 壳（api/common/plan_import_http），只差身份依赖。"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile

from api.common.deps import actor, actor_optional, declared, require_fresh_mirrors
from api.common.plan_import_http import run_import, template_response
from api.common.source_factory import source_dep
from dim.source import Source

router = APIRouter()


@router.post("/plans/import", dependencies=[Depends(require_fresh_mirrors)])
def import_plan_ep(request: Request, source: Annotated[Source, Depends(source_dep)],
                   file: Annotated[UploadFile, File()],
                   who: str = Depends(actor),
                   title: str = Form(""), period_start: str = Form(""),
                   months: str = Form("")):
    declared(request)
    return run_import(source, title, period_start, months, who, file.file.read(), "plan_import")


@router.get("/plans/import/template")
def template(request: Request, period_start: str = "", months: str = "3",
             who: str | None = Depends(actor_optional)):
    declared(request, "period_start", "months")
    return template_response(period_start, months)
