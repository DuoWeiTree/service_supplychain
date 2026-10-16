"""物流方式（CB-D3）：领星头程物流渠道镜像 + 每张海外仓排货单选一个。

★ 源是领星 API `channelList`（CH 里没有这张表）；只读调用照样逐次落 `erp_call`。
"""
from __future__ import annotations

import datetime as dt
import json
import logging

from repo import dispatch as repo
from shared.pg_client import pg_conn, timed
from usecase.draft_po import Conflict
from usecase.place_po import call_logged

log = logging.getLogger("scm.usecase")

PAGE = 200
STALE = dt.timedelta(hours=24)        # 设计取值 · 未实测：渠道表改得很少，一天刷一次


def refresh(actor: str) -> int:
    rows, offset = [], 0
    while True:
        cid, r = call_logged("logistics_channel_list", {"offset": offset, "length": PAGE}, po_id=None, actor=actor,
                             retry_of=None)
        if r.outcome != "ok":
            log.warning("op=logistics_refresh outcome=fail call=%s outcome=%s http=%s lx=%s %s cause=%s", cid,
                        r.outcome, r.http_status, r.lx_code, r.lx_msg, r.cause)
            raise Conflict(503, "erp_unavailable", "读领星物流渠道失败", outcome=r.outcome, lx_code=r.lx_code,
                           lx_msg=r.lx_msg, cause=r.cause, call_id=cid)
        data = (r.body or {}).get("data") or []
        for d in data:
            rows.append((int(d["id"]), d.get("channel_name") or "", d.get("method_name"),
                         ((d.get("provider") or {}).get("logistics_provider_name")),
                         json.dumps(d.get("receive_country_codes") or []), str(d.get("enabled")) in ("1", "True", "true")))
        total = int((r.body or {}).get("total") or 0)
        offset += PAGE
        if not data or offset >= total:
            break
    with timed("logistics_refresh_write"), pg_conn() as c, c.cursor() as cur:
        n = repo.upsert_channels(cur, rows)
    log.info("op=logistics_refresh outcome=ok channels=%d enabled=%d actor=%s", n, sum(1 for r in rows if r[5]), actor)
    return n


def list_channels(actor: str | None) -> dict:
    with pg_conn() as c, c.cursor() as cur:
        rows, at = repo.channels(cur)
    if at is None or dt.datetime.now(dt.UTC) - at > STALE:
        refresh(actor or "system:auto")
        with pg_conn() as c, c.cursor() as cur:
            rows, at = repo.channels(cur)
    return {"channels": rows, "refreshed_at": at.isoformat() if at else None}


def set_for_sheet(sheet_id: int, dest_wid: int, body: dict, actor: str) -> dict:
    lid = (body or {}).get("logistics_id")
    if not isinstance(lid, int) or isinstance(lid, bool):
        raise Conflict(400, "missing_required", "要选一个物流方式", missing=["logistics_id"])
    with timed("sheet_logistics", sheet=sheet_id, actor=actor), pg_conn() as c, c.cursor() as cur:
        s = repo.lock_sheet(cur, sheet_id)
        if s is None:
            raise Conflict(404, "sheet_not_found", "排货表不存在", sheet_id=sheet_id)
        if s["state"] != "草稿":
            raise Conflict(409, "sheet_frozen", "排货表已确认 / 已取消，不可改", sheet_id=sheet_id, state=s["state"])
        if not any(c_["channel"] == "oversea" and c_["dest_wid"] == dest_wid for c_ in repo.consignments(cur, sheet_id)
                   if c_["our_stage"] == "草稿"):
            raise Conflict(409, "not_an_oversea_destination", "这张表没有发往这个海外仓的排货单", dest_wid=dest_wid)
        ch = repo.channel(cur, lid)
        if ch is None or not ch["enabled"]:
            raise Conflict(409, "logistics_not_available", "物流方式不存在或已停用（可先刷新物流渠道）", logistics_id=lid)
        repo.set_logistics(cur, sheet_id, dest_wid, lid, actor)
    log.info("op=sheet_logistics sheet=%s dest_wid=%s logistics_id=%s actor=%s", sheet_id, dest_wid, lid, actor)
    return {"dest_wid": dest_wid, **ch}
