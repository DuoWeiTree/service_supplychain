"""转人工：看得见 · 看得懂 · 接得住（spec §6.6）。每个处理都落 po_attention 的 谁 · 何时 · 理由。"""
from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal, InvalidOperation

from repo import purchase as repo
from shared.pg_client import pg_conn, timed
from usecase import place_po
from usecase.draft_po import Conflict, load_po

log = logging.getLogger("scm.usecase")

_ATTEMPT_COLS = ("call_id", "started_at", "actor", "endpoint", "mode", "host", "path", "idem_key",
                 "elapsed_ms", "outcome", "http_status", "lx_code", "lx_msg", "cause", "retry_of",
                 "request", "response")


def _known(d: dict) -> str:
    outcome, cause = d.get("create_outcome"), d.get("create_cause")
    if d.get("request_confirmed_sent"):
        return f"请求已发到领星，未收到可确认的结果（{outcome}，{cause}）"
    return f"进程中断或调用异常，请求是否已发出不确定（{cause}）"


def _last_lookup(attempts: list[dict], after_call: int) -> dict | None:
    """★ 只认「按幂等键的窗口查重」（请求带 search_field_time）且晚于本次 create：
    登记已有单的窄查询、更早一轮的查重都不是「这次下单之后查过没有」的证据。"""
    for a in reversed(attempts):
        if a["endpoint"] != "purchase_order_list" or a["call_id"] <= after_call:
            continue
        if not isinstance(a["request"], dict) or "search_field_time" not in a["request"]:
            continue
        resp = a["response"] if isinstance(a["response"], dict) else {}
        total, rows = resp.get("total"), resp.get("data") or []
        truncated = None
        if a["outcome"] == "ok":
            truncated = (isinstance(total, int) and total > len(rows)) or len(rows) >= place_po._LOOKUP_LEN
        req = a["request"] or {}
        return {"call_id": a["call_id"], "at": a["at"], "outcome": a["outcome"], "truncated": truncated,
                "window": {k: req.get(k) for k in ("search_field_time", "start_date", "end_date")}}
    return None


def timeline(po_id: int) -> dict:
    with timed("po_timeline", po_id=po_id), pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT 1 FROM po WHERE po_id=%s", (po_id,))
        if cur.fetchone() is None:
            raise Conflict(404, "po_not_found", "采购单不存在", po_id=po_id)
        cur.execute(f"SELECT {', '.join(_ATTEMPT_COLS)} FROM erp_call WHERE po_id=%s ORDER BY call_id", (po_id,))
        attempts = []
        for row in cur.fetchall():
            a = dict(zip(_ATTEMPT_COLS, row, strict=True))
            a["at"] = a.pop("started_at").isoformat()
            attempts.append(a)
        cur.execute("SELECT from_state, to_state, actor, reason, src, at FROM po_event WHERE po_id=%s ORDER BY seq",
                    (po_id,))
        events = [{"from": a, "to": b, "actor": c_, "reason": d, "src": e, "at": f.isoformat()}
                  for a, b, c_, d, e, f in cur.fetchall()]
        cur.execute("SELECT attention_id, kind, opened_at, resolved_at, resolution, resolved_by, reason, detail"
                    " FROM po_attention WHERE po_id=%s ORDER BY attention_id", (po_id,))
        attention = [{"attention_id": a, "kind": b, "opened_at": c_.isoformat(),
                      "resolved_at": d.isoformat() if d else None, "resolution": e, "resolved_by": f,
                      "reason": g, "detail": h} for a, b, c_, d, e, f, g, h in cur.fetchall()]
        open_nv = next((x for x in attention if x["resolved_at"] is None and x["kind"] == "needs_verify"), None)
        started = None
        if open_nv:
            call = repo.get_call(cur, open_nv["detail"].get("create_call_id") or 0)
            started = call["started_at"] if call else None
    conclusion = None
    if open_nv:
        d = open_nv["detail"]
        window = place_po._lookup_params(
            (started or dt.datetime.now().astimezone()).astimezone().date())
        conclusion = {"kind": open_nv["kind"], "known": _known(d), "unknown": "领星是否已建单",
                      "last_lookup": _last_lookup(attempts, d.get("create_call_id") or 0),
                      "how_to_search": {"custom_order_sn": d.get("custom_order_sn"),
                                        "window": {k: window[k] for k in ("search_field_time", "start_date",
                                                                          "end_date")}}}
    return {"po_id": po_id, "conclusion": conclusion, "attempts": attempts, "events": events,
            "attention": attention}


def _require_open(cur, po_id: int) -> dict:
    cur.execute("SELECT 1 FROM po WHERE po_id=%s", (po_id,))
    if cur.fetchone() is None:
        raise Conflict(404, "po_not_found", "采购单不存在", po_id=po_id)
    a = repo.open_attention_of(cur, po_id, "needs_verify")
    if a is None:
        raise Conflict(409, "nothing_to_resolve", "这张单没有待处理的项", po_id=po_id, kind="needs_verify")
    return a


def _item_keys(items) -> list:
    """(sku, wid, units, price) 按 (sku, wid) 配对口径（v_check_po_landed 的无 lx_item_id 分支）；
    数量 / 价格不是数 → 原样保留成对不上，不抛。"""
    out = []
    for sku, wid, units, price in items:
        try:
            u = int(units) if str(units).lstrip("-").isdigit() else f"?{units!r}"
        except (TypeError, ValueError):
            u = f"?{units!r}"
        try:
            pr = str(Decimal(str(price)).normalize())
        except (InvalidOperation, TypeError, ValueError):
            pr = f"?{price!r}"
        out.append((str(sku), None if wid is None else str(wid), u, pr))
    return sorted(out, key=repr)


def recheck(po_id: int, actor: str) -> dict:
    with pg_conn() as c, c.cursor() as cur:
        a = _require_open(cur, po_id)
    res = place_po.lookup_and_settle(po_id, actor, a["opened_by_call"], "recheck",
                                      reason="人工重新核对：按幂等键在领星查到")
    log.warning("op=po_recheck po_id=%s actor=%s outcome=%s lookup_outcome=%s truncated=%s", po_id, actor,
                res["outcome"], res.get("lookup_outcome"), res.get("truncated"))
    return res


def register_existing(po_id: int, order_sn: str, actor: str) -> dict:
    """★ 对上才接受：防止填错单号把别人的单认成自己的。"""
    order_sn = (order_sn or "").strip()
    with pg_conn() as c, c.cursor() as cur:
        _require_open(cur, po_id)
    if not order_sn:
        raise Conflict(400, "missing_required", "必须填领星单号", missing=["order_sn"])
    po = load_po(po_id)
    cid, r = place_po.call_logged("purchase_order_list", {"order_sns": order_sn, "offset": 0, "length": 50},
                                   po_id=po_id, actor=actor, retry_of=None)
    rows = [o for o in ((r.body or {}).get("data") or []) if o.get("order_sn") == order_sn]
    if r.outcome != "ok":
        log.error("op=po_register_existing po_id=%s order_sn=%s actor=%s outcome=lookup_failed"
                  " lookup_call=%s lookup_outcome=%s cause=%s", po_id, order_sn, actor, cid, r.outcome, r.cause)
        raise Conflict(502, "lookup_failed", "查询领星本身失败，无法判断有无此单号；稍后重试",
                       order_sn=order_sn, lookup_call=cid, lookup_outcome=r.outcome, cause=r.cause)
    if not rows:
        log.warning("op=po_register_existing po_id=%s order_sn=%s actor=%s outcome=order_not_found"
                    " lookup_call=%s lookup_outcome=%s", po_id, order_sn, actor, cid, r.outcome)
        raise Conflict(409, "order_not_found", "领星里查不到这个单号",
                       order_sn=order_sn, lookup_call=cid)
    o, mismatch = rows[0], {}
    if str(o.get("custom_order_sn")) != po["custom_order_sn"]:
        mismatch["custom_order_sn"] = {"ours": po["custom_order_sn"], "lingxing": o.get("custom_order_sn")}
    if str(o.get("supplier_id")) != str(po["supplier_id"]):
        mismatch["supplier_id"] = {"ours": po["supplier_id"], "lingxing": o.get("supplier_id")}
    if str(o.get("status")) == "-1":
        log.warning("op=po_register_existing po_id=%s order_sn=%s actor=%s outcome=order_voided", po_id,
                    order_sn, actor)
        raise Conflict(409, "order_voided", "这张领星单已作废，不能登记", order_sn=order_sn, lookup_call=cid)
    ours, theirs = _item_keys((ln["sku"], ln["wid"], ln["units"], ln["price"]) for ln in po["lines"]), \
        _item_keys((it.get("sku"), it.get("wid"), it.get("quantity_real"), it.get("price"))
                   for it in o.get("item_list") or [] if str(it.get("is_delete", 0)) in ("0", "False", "false"))
    if ours != theirs:
        mismatch["items"] = {"ours": ours, "lingxing": theirs}
    if mismatch:
        log.warning("op=po_register_existing po_id=%s order_sn=%s actor=%s outcome=order_mismatch fields=%s",
                    po_id, order_sn, actor, sorted(mismatch))
        raise Conflict(409, "order_mismatch", "这张领星单与本采购单对不上，未接受", order_sn=order_sn,
                       mismatch=mismatch, lookup_call=cid)
    with timed("po_register_existing", po_id=po_id, actor=actor), pg_conn() as c, c.cursor() as cur:
        locked = repo.lock_po(cur, po_id)
        if locked["discarded"]:
            raise Conflict(409, "po_discarded", "这张草稿已作废", po_id=po_id)
        if locked["state"] != "草稿":
            raise Conflict(409, "po_frozen", "采购单已下单", po_id=po_id, order_sn=locked["order_sn"])
        # 同一事务：记已下单与收掉待核实要么一起成、要么一起不成
        moved = place_po._settle_placed(cur, po_id, order_sn, actor, f"register_existing:{cid}",
                                         resolve_attention=False)
        n = repo.resolve_attention(cur, po_id, "needs_verify", "found_existing", actor,
                                   f"人工登记领星单 {order_sn}（已核对 custom_order_sn / 供应商 / 明细）")
        if n == 0:
            raise Conflict(409, "nothing_to_resolve", "待处理项刚被别人处理了", po_id=po_id)
    place_po._read_back_safe(po_id, o, cid)
    log.warning("op=po_register_existing po_id=%s order_sn=%s actor=%s outcome=placed moved=%s",
                po_id, order_sn, actor, moved)
    return {"outcome": "placed", "po_id": po_id, "order_sn": order_sn, "found_by": "manual"}


def confirm_absent(po_id: int, reason: str, actor: str) -> dict:
    with pg_conn() as c, c.cursor() as cur:
        _require_open(cur, po_id)
    if not (reason or "").strip():
        raise Conflict(400, "reason_required", "确认「领星无此单」必须写理由（怎么核实的）")
    with timed("po_confirm_absent", po_id=po_id, actor=actor), pg_conn() as c, c.cursor() as cur:
        locked = repo.lock_po(cur, po_id)
        if locked["state"] != "草稿":
            raise Conflict(409, "po_frozen", "采购单已下单", po_id=po_id, order_sn=locked["order_sn"])
        if repo.any_in_flight(cur, po_id):
            log.warning("op=po_confirm_absent po_id=%s actor=%s outcome=lookup_in_progress", po_id, actor)
            raise Conflict(409, "lookup_in_progress", "有查询 / 下单调用进行中，等它结束再确认", po_id=po_id)
        n = repo.resolve_attention(cur, po_id, "needs_verify", "confirmed_absent", actor, reason.strip())
    if n == 0:
        raise Conflict(409, "nothing_to_resolve", "待处理项刚被别人处理了", po_id=po_id)
    log.warning("op=po_confirm_absent po_id=%s actor=%s outcome=confirmed_absent reason=%r", po_id, actor, reason)
    return {"po_id": po_id, "unlocked": ["place", "update", "delete"]}


def queue() -> list[dict]:
    with timed("attention_queue"), pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT a.attention_id, a.po_id, a.kind, a.opened_at, p.custom_order_sn, p.supplier_id"
                    " FROM po_attention a JOIN po p USING (po_id) WHERE a.resolved_at IS NULL"
                    " ORDER BY a.opened_at")
        return [{"attention_id": a, "po_id": b, "kind": k, "opened_at": d.isoformat(), "custom_order_sn": e,
                 "supplier_id": f, "timeline": f"/v1/purchase-orders/{b}/timeline"}
                for a, b, k, d, e, f in cur.fetchall()]
