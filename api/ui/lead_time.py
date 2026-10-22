"""提前期（spec §4.4 · 03 §41）：用户自己改的全局配置。读侧在 usecase/lead_time.py。

★ 当前生效行 = valid_to IS NULL。改动 = 关旧行 + 插今天的新行（同一天再改就原地改今天那行）——
  「现在的规则」在表里，「当时用的规则」随 grid 的 params 一起出去（03 §7 那句话）。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request

from api.common.deps import actor, actor_optional, declared
from api.common.errors import ApiError
from shared.pg_client import pg_conn, timed
from usecase.lead_time import ROUTE, LeadTime, read_lead_time
from usecase.lead_time import current_rows as _current

log = logging.getLogger("scm.api")
router = APIRouter()

_FIELDS = (("make_days", "making"), ("ship_days", "shipping"))
_MAX_DAYS = 720          # 03 §41 CHECK


def lead_time_json(lt: LeadTime) -> dict:
    return {"route": lt.route, "make_days": lt.make_days, "ship_days": lt.ship_days,
            "anchor_day": lt.anchor_day, "valid_from": lt.valid_from}


@router.get("/lead-time")
def get_lead_time(request: Request, who: str | None = Depends(actor_optional)):
    declared(request)
    with timed("get_lead_time", actor=who), pg_conn() as c, c.cursor() as cur:
        return lead_time_json(read_lead_time(cur))


@router.put("/lead-time")
def put_lead_time(body: dict, request: Request, who: str = Depends(actor)):
    declared(request)
    wanted: dict[str, int] = {}
    for field, seg in _FIELDS:
        if field in body:
            v = body[field]
            if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= _MAX_DAYS:
                raise ApiError(400, "validation_error", f"{field} 必须是 0~{_MAX_DAYS} 的整数天",
                               {"fields": [field], "got": v})
            wanted[seg] = v
    if not wanted:
        raise ApiError(400, "validation_error", "至少给 make_days / ship_days 之一",
                       {"fields": [f for f, _ in _FIELDS]})
    changed: list[dict] = []
    with timed("put_lead_time", actor=who), pg_conn() as c, c.cursor() as cur:
        got = _current(cur, lock=True)
        for seg, days in wanted.items():
            old, vf, same_day = got[seg]
            if old == days:
                continue
            if same_day:
                cur.execute("UPDATE lead_time SET days = %s"
                            " WHERE route = %s AND segment = %s AND valid_from = %s",
                            (days, ROUTE, seg, vf))
            else:
                cur.execute("UPDATE lead_time SET valid_to = CURRENT_DATE"
                            " WHERE route = %s AND segment = %s AND valid_to IS NULL", (ROUTE, seg))
                cur.execute("INSERT INTO lead_time (route, segment, days, valid_from)"
                            " VALUES (%s, %s, %s, CURRENT_DATE)", (ROUTE, seg, days))
            changed.append({"segment": seg, "from": old, "to": days})
        lt = read_lead_time(cur)
    for ch in changed:
        log.info("op=lead_time_update actor=%s route=%s segment=%s from=%d to=%d",
                 who, ROUTE, ch["segment"], ch["from"], ch["to"])
    return {**lead_time_json(lt), "changed": changed}
