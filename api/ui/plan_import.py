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
from rules.plan_import import RowError
from shared.pg_client import timed
from usecase.plan_import import ImportRejected, PlanHeader, import_plan
from usecase.plan_xlsx import build_template

log = logging.getLogger("scm.api")

router = APIRouter()

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _month_start(s: str) -> dt.date | None:
    try:
        return dt.datetime.strptime((s or "").strip(), "%Y-%m").date()
    except ValueError:
        return None


@router.post("/plans/import", dependencies=[Depends(require_fresh_mirrors)])
def import_plan_ep(request: Request, source: Annotated[Source, Depends(source_dep)],
                   who: str = Depends(actor),
                   title: str = Form(""), period_start: str = Form(""),
                   months: str = Form(""), file: UploadFile = File(...)):
    declared(request)
    data = file.file.read()
    start = _month_start(period_start)
    # 起始月解析不了也不单独 400：与文件里的错一起回，免得改一处又撞一处
    pre: list[RowError] = []
    if start is None:
        pre.append(RowError(0, None, "bad_date", "起始月不是 YYYY-MM",
                            extra={"field": "period_start", "got": period_start}))
        start = dt.date(1970, 1, 1)
    n = int(months) if months.strip().isdigit() else months  # 非整数原样交给 usecase 报 validation_error
    header = PlanHeader(title=title, period_start=start, months=n, owner_actor=who)
    try:
        with timed("plan_import", actor=who, file_bytes=len(data)):
            if pre:
                # 起始月不可用 → 月份列无从比对，文件不读；表头其余的错仍由 usecase 一并裁决
                try:
                    import_plan(source, header, b"", who)
                except ImportRejected as e:
                    pre += [x for x in e.errors if x.code != "not_xlsx"]
                raise ImportRejected(pre)
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
    start = _month_start(period_start)
    if start is None:
        raise ApiError(400, "bad_date", "period_start 不是 YYYY-MM",
                       {"field": "period_start", "got": period_start})
    if not months.strip().isdigit() or not 1 <= int(months) <= 24:
        raise ApiError(400, "validation_error", "months 须为 1~24 的整数",
                       {"fields": ["months"], "got": months})
    n = int(months)
    periods = [f"{start.year + (start.month - 1 + i) // 12}-{(start.month - 1 + i) % 12 + 1:02d}"
               for i in range(n)]
    body = build_template(periods)
    name = f"plan-import-{start:%Y-%m}-{n}m.xlsx"
    return Response(body, media_type=XLSX,
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
