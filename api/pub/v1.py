"""/pub/v1：映射 · 预测预览 · 导入 · 模板 · 硬删除（spec §1 ①②③③'④）。裁决都在 usecase。"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from pydantic import BaseModel, ConfigDict

from api.common.deps import actor_optional, declared, require_ai_actor, require_fresh_mirrors
from api.common.errors import ApiError
from api.common.plan_import_http import header_400, run_import, template_response
from api.common.source_errors import SEED_ERRORS, SOURCE_ERRORS, seed_error
from api.common.source_factory import source_dep
from dim.source import Source
from shared.pg_client import pg_conn, timed
from usecase import catalog_query, plan_delete
from usecase.lead_time import read_lead_time
from usecase.plan_claim import seed_rows
from usecase.plan_import import parse_header, plan_periods_ym
from usecase.plan_projection import project

log = logging.getLogger("scm.api")

router = APIRouter()


#: ★ 设计取值 · 未实测（spec §1 ① 未给数；brief 定 200 / 2000）。超出上限按上限截，truncated 回显。
MAPPINGS_DEFAULT_LIMIT, MAPPINGS_MAX_LIMIT = 200, 2000
_MAPPING_FILTERS = ("sku", "seller_sku", "sid", "market")


# ---- ① ----------------------------------------------------------------------

@router.get("/mappings", dependencies=[Depends(require_fresh_mirrors)])
def mappings(request: Request, who: str | None = Depends(actor_optional)):
    declared(request, *_MAPPING_FILTERS, "limit")
    raw = request.query_params.get("limit", str(MAPPINGS_DEFAULT_LIMIT))
    try:
        limit = int(raw)
    except ValueError:
        raise ApiError(400, "bad_limit", "limit 不是整数", {"got": raw}) from None
    if limit < 1:
        raise ApiError(400, "bad_limit", "limit 必须 ≥ 1", {"got": limit})
    limit = min(limit, MAPPINGS_MAX_LIMIT)
    # ★ 空串当没给：?sku= 不是「货号为空串」这个条件
    filters = {k: v for k in _MAPPING_FILTERS if (v := (request.query_params.get(k) or "").strip())}
    if not filters:
        return {"need_query": True, "matched": 0, "truncated": False, "limit": limit,
                "dropped": 0, "items": []}
    with timed("pub_mappings", actor=who, limit=limit, **filters), pg_conn() as c, c.cursor() as cur:
        items, dropped = catalog_query.mappings(cur, **filters)
    if dropped:
        log.warning("op=pub_mappings dropped=%d filters=%s —— 桥表行的货号或店铺不在镜像里",
                    dropped, filters)
    log.info("op=pub_mappings filters=%s matched=%d dropped=%d limit=%d",
             filters, len(items), dropped, limit)
    return {"need_query": False, "matched": len(items), "truncated": len(items) > limit,
            "limit": limit, "dropped": dropped, "items": items[:limit]}


# ---- ② ----------------------------------------------------------------------

class MskuRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seller_sku: str
    sid: str


class PreviewBody(BaseModel):
    # ★ 冻结契约：拼错的字段（如 msku）静默忽略就会算出一张「什么都没选」的预览
    model_config = ConfigDict(extra="forbid")
    period_start: str
    months: int
    mskus: list[MskuRef] | None = None
    skus: list[str] | None = None


def _resolve(cur, mskus: list[MskuRef], skus: list[str]):
    """→ (targets, unknown_mskus, unknown_skus)。targets = [(seller_sku, sid, sku, platform, has_fba)]，去重、有序。"""
    found: dict[tuple[str, str], tuple] = {}
    if mskus:
        cur.execute("SELECT b.seller_sku, b.sid, b.sku, s.platform, s.has_fba FROM msku_bridge b"
                    " JOIN seller s ON s.seller_id = b.sid"
                    " JOIN unnest(%s::text[], %s::text[]) AS q(seller_sku, sid)"
                    "   ON q.seller_sku = b.seller_sku AND q.sid = b.sid",
                    ([m.seller_sku for m in mskus], [m.sid for m in mskus]))
        found.update({(r[0], r[1]): r for r in cur.fetchall()})
    unknown_mskus = [{"seller_sku": m.seller_sku, "sid": m.sid} for m in mskus
                     if (m.seller_sku, m.sid) not in found]
    expanded_skus: set[str] = set()
    if skus:
        cur.execute("SELECT b.seller_sku, b.sid, b.sku, s.platform, s.has_fba FROM msku_bridge b"
                    " JOIN seller s ON s.seller_id = b.sid WHERE b.sku = ANY(%s)", (list(skus),))
        for r in cur.fetchall():
            found[(r[0], r[1])] = r
            expanded_skus.add(r[2])
    # ★ 货号展开成零个 msku 也要点名 —— 不然它在预览里无声消失
    unknown_skus = [s for s in skus if s not in expanded_skus]
    return [found[k] for k in sorted(found)], unknown_mskus, unknown_skus


@router.post("/forecast/preview", dependencies=[Depends(require_fresh_mirrors)])
def forecast_preview(body: PreviewBody, request: Request,
                     source: Annotated[Source, Depends(source_dep)],
                     who: str | None = Depends(actor_optional)):
    declared(request)
    header, errs = parse_header("-", body.period_start, body.months, "-")
    if header is None:
        raise header_400(errs)
    mskus, skus = body.mskus or [], body.skus or []
    if not mskus and not skus:
        raise ApiError(400, "validation_error", "mskus 与 skus 至少给一个",
                       {"fields": ["mskus", "skus"]})
    periods = [dt.date.fromisoformat(ym + "-01")
               for ym in plan_periods_ym(header.period_start, header.months)]
    fields = {"actor": who, "mskus": len(mskus), "skus": len(skus), "months": header.months,
              "period_start": body.period_start}
    with timed("pub_preview_read", **fields), pg_conn() as c, c.cursor() as cur:
        targets, unknown_mskus, unknown_skus = _resolve(cur, mskus, skus)
        lead = read_lead_time(cur)
    if unknown_mskus or unknown_skus:
        raise ApiError(404, "unknown_msku", "有 msku / 货号不在桥表里，预览没算",
                       {"mskus": unknown_mskus, "skus": unknown_skus})
    # ★ 连接已关：种预测 + 推演要打 CH / 领星几十秒，不许把 PG 事务挂着
    try:
        with timed("pub_preview", targets=len(targets), **fields):
            demand = [row for sk, sid, sku, platform, has_fba in targets
                      for row in seed_rows(source, sk, sid, sku, platform, has_fba, periods)]
            proj = project(source, lead, demand, [], periods, plan_id=None)
    except SEED_ERRORS + SOURCE_ERRORS as e:
        raise seed_error(e) from e
    return {"periods": [p.strftime("%Y-%m") for p in periods], **proj}


# ---- ③ ③' —— 与 api/ui 同一个 HTTP 壳，只差身份依赖 ------------------------------

@router.post("/plans/import", dependencies=[Depends(require_fresh_mirrors)])
def import_plan_ep(request: Request, source: Annotated[Source, Depends(source_dep)],
                   file: Annotated[UploadFile, File()],
                   who: str = Depends(require_ai_actor),
                   title: str = Form(""), period_start: str = Form(""),
                   months: str = Form("")):
    declared(request)
    return run_import(source, title, period_start, months, who, file.file.read(),
                      "pub_plan_import")


@router.get("/plans/import/template")
def template(request: Request, period_start: str = "", months: str = "3",
             who: str | None = Depends(actor_optional)):
    declared(request, "period_start", "months")
    return template_response(period_start, months)


# ---- ④ ----------------------------------------------------------------------

@router.delete("/plans/{plan_id}")
def delete_plan(plan_id: int, request: Request, who: str = Depends(require_ai_actor)):
    declared(request)
    # 成功与每种拒绝的 op=delete_plan 日志在 usecase 里打（库里不留痕，那一行是唯一痕迹）
    try:
        return plan_delete.delete_plan(plan_id, who)
    except plan_delete.PlanNotFound:
        raise ApiError(404, "plan_not_found", "计划不存在", {"plan_id": plan_id}) from None
    except plan_delete.PlanSubmitted as e:
        raise ApiError(409, "plan_submitted", "计划提交过，不能删；只能归档",
                       {"plan_id": plan_id, "revs": e.revs}) from None
    except plan_delete.PlanReferenced as e:
        raise ApiError(409, "plan_referenced", f"计划被 {e.table} 引用，没删",
                       {"plan_id": plan_id, "table": e.table, "constraint": e.constraint}) from None
