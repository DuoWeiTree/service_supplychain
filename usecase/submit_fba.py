"""C-c1 · FBA 排货单 → 领星 FBA 发货计划（spec 2026-10-13-dispatch-submit-fba-plan · CC-D1~D4）。

★ 领星无幂等键：批次 remark = 参考号；建前查重、结果未知再查重、仍不知道 → 待核实，★ 绝不自动重试（出站 #2）。
★ CC-D3 实测：建计划不锁库存 → 建后必锁；锁按 `data.failNum` 判；锁失败的批次排货单不算已投送，再提交只重锁不重建。
★ CC-D4：没有作废接口 → 取消 = 释放库存 + 计划备注「SCM 已取消 请作废」+ 点名人工作废。
★ 调用方（submit_ows 的入口）已持 sheet_op；这里不持任何数据库连接做 HTTP。
"""
from __future__ import annotations

import datetime as dt
import logging
import time

from erp import gateway
from erp.transport import CallResult, cause_chain
from repo import dispatch as repo
from repo import purchase as po_repo
from rules import fba_plan
from shared.pg_client import pg_conn
from usecase.draft_po import Conflict

log = logging.getLogger("scm.usecase")

LIST_PAGE = 200            # shipmentPlanLists 每页条数；设计取值 · 未实测上限
LOOKUP_BACK_DAYS = 2       # 查重时间窗：创建日往前几天；设计取值 · 未实测
CANCEL_REMARK = "SCM 已取消 请作废"


def _call(name: str, params: dict) -> CallResult:
    try:
        _, r = gateway.call(name, params)
    except Exception as e:  # noqa: BLE001 —— 驱动抛了也可能已发出：按结果未知处理
        r = CallResult("unknown", "-", "-", 0, cause=f"driver exception: {cause_chain(e)}")
    return r


def lookup(sid: str, idem: str, since: dt.date | None = None) -> tuple[list[dict], CallResult]:
    """按批次 remark = 参考号查 → [{seq, order_sns, plans}]。读失败 → ([], r)，调用方先看 outcome 再信「没有」。"""
    since = since or dt.date.today() - dt.timedelta(days=LOOKUP_BACK_DAYS)
    hits, off, t0 = [], 0, time.perf_counter()
    while True:
        r = _call("fba_plan_list", {"sids": str(sid), "search_field_time": "gmt_create", "start_date": since.isoformat(),
                                    "end_date": (dt.date.today() + dt.timedelta(days=1)).isoformat(),
                                    "offset": off, "length": LIST_PAGE})
        if r.outcome != "ok":
            break
        groups = (r.body or {}).get("data") or []
        for g in groups:
            if (g.get("remark") or "").strip() == idem:
                plans = g.get("list") or []
                hits.append({"seq": g.get("seq"), "order_sns": [p["order_sn"] for p in plans if p.get("order_sn")],
                             "plans": plans})
        off += len(groups)
        if not groups or off >= int((r.body or {}).get("total") or 0):
            break
    log.info("op=fba_plan_lookup idem=%s sid=%s host=%s path=%s outcome=%s hits=%s cause=%s ms=%d", idem, sid, r.host,
             r.path, r.outcome, [h["seq"] for h in hits], r.cause, (time.perf_counter() - t0) * 1000)
    return hits, r


def read_batch(sid: str, seq: str, since: dt.date) -> tuple[dict | None, CallResult]:
    """回执 / 登记：按批次号读一个批次（没有 → (None, ok)）。"""
    r = _call("fba_plan_list", {"sids": str(sid), "search_field_time": "gmt_create", "start_date": since.isoformat(),
                                "end_date": (dt.date.today() + dt.timedelta(days=1)).isoformat(), "seq": seq,
                                "offset": 0, "length": LIST_PAGE})
    if r.outcome != "ok":
        return None, r
    g = next((x for x in (r.body or {}).get("data") or [] if x.get("seq") == seq), None)
    return g, r


def _stock(action: str, ext_ref_id: int, consignment_id: int, idem: str, order_sns: list[str], actor: str) -> dict:
    """锁 / 释放库存（CC-D3）：一条写调用留痕；★ code=0 不代表每张成功，看 failNum。"""
    name = "fba_plan_lock" if action == "lock" else "fba_plan_unlock"
    with pg_conn() as c, c.cursor() as cur:
        call_id = repo.insert_write_call(cur, endpoint=name, side_effect="reversible", consignment_id=consignment_id,
                                         idem_key=None, actor=actor, request={"orderNos": order_sns})
    t0 = time.perf_counter()
    r = _call(name, {"orderNos": order_sns})
    data = (r.body or {}).get("data") or {}
    fail = int(data.get("failNum") or 0) if isinstance(data, dict) else 0
    ok = r.outcome == "ok" and fail == 0
    with pg_conn() as c, c.cursor() as cur:
        po_repo.close_call_if_in_flight(cur, call_id, r)
        if ok:
            repo.insert_fba_stock(cur, ext_ref_id, action, call_id, actor)
    log.log(logging.INFO if ok else logging.WARNING,
            "op=fba_plan_%s idem=%s plans=%s host=%s path=%s outcome=%s fail=%s errors=%s lx=%s %s cause=%s call=%s ms=%d",
            action, idem, order_sns, r.host, r.path, r.outcome, fail, data.get("error") if isinstance(data, dict) else None,
            r.lx_code, r.lx_msg, r.cause, call_id, (time.perf_counter() - t0) * 1000)
    return {"ok": ok, "outcome": r.outcome if r.outcome != "ok" else ("ok" if ok else "partial"), "fail": fail,
            "errors": data.get("error") if isinstance(data, dict) else None, "lx_msg": r.lx_msg, "cause": r.cause,
            "call_id": call_id}


def _adopt(order: dict, hit: dict, call_id: int | None, found_by: str, actor: str) -> int:
    with pg_conn() as c, c.cursor() as cur:
        eid = repo.insert_ext_ref(cur, order["consignment_id"], order["s_wid"], order["idem_key"], hit["seq"], call_id,
                                  found_by, actor, kind="fba_plan")
        repo.insert_fba_plan_order(cur, eid, hit["order_sns"])
    return eid


def _lock_result(order: dict, eid: int, order_sns: list[str], seq: str, base: str, actor: str) -> dict:
    lk = _stock("lock", eid, order["consignment_id"], order["idem_key"], order_sns, actor)
    res = {"key": order["key"], "idem_key": order["idem_key"], "ext_no": seq, "order_sns": order_sns}
    if lk["ok"]:
        return {**res, "outcome": base}
    return {**res, "outcome": "lock_failed", "lx_msg": lk["lx_msg"], "cause": lk["cause"] or f"failNum={lk['fail']}",
            "errors": lk["errors"]}


def relock(order: dict, ref: dict, actor: str) -> dict:
    """建成了但没锁上：只重锁，不重建（CC-D3）。"""
    return _lock_result(order, ref["ext_ref_id"], ref["order_sns"], ref["ext_no"], "locked", actor)


def create(sheet_id: int, order: dict, actor: str) -> dict:
    """一个批次：查重 → 建 → 锁。返回这一张的结果；调用方据 outcome 决定继续还是停。"""
    t0 = time.perf_counter()
    sid = order["dest_sid"]
    hits, lr = lookup(sid, order["idem_key"])
    if len(hits) > 1:
        return _needs_verify(order, None, {"hits": [h["seq"] for h in hits], "reason": "multiple_batches_for_reference"},
                             f"领星里有 {len(hits)} 个同参考号的发货计划批次：{'、'.join(h['seq'] for h in hits)}", actor)
    if hits:
        eid = _adopt(order, hits[0], None, "lookup", actor)
        log.info("op=fba_plan_submit sheet=%s idem=%s outcome=adopted seq=%s via=lookup_before", sheet_id,
                 order["idem_key"], hits[0]["seq"])
        return _lock_result(order, eid, hits[0]["order_sns"], hits[0]["seq"], "adopted", actor)
    if lr.outcome != "ok":
        log.warning("op=fba_plan_submit sheet=%s idem=%s outcome=lookup_failed lookup=%s cause=%s", sheet_id,
                    order["idem_key"], lr.outcome, lr.cause)
        return {"key": order["key"], "idem_key": order["idem_key"], "outcome": "lookup_failed",
                "lookup_outcome": lr.outcome, "cause": lr.cause}
    payload = fba_plan.payload(order, sheet_id)
    with pg_conn() as c, c.cursor() as cur:
        call_id = repo.insert_write_call(cur, endpoint="create_fba_plan", side_effect="reversible",
                                         consignment_id=order["consignment_id"], idem_key=order["idem_key"],
                                         actor=actor, request=gateway.redact(payload))
    r = _call("create_fba_plan", payload)
    data = ((r.body or {}).get("data") or {}) if r.outcome == "ok" else {}
    seq, sns = data.get("seq"), [s for s in (data.get("order_sn") or []) if s]
    with pg_conn() as c, c.cursor() as cur:
        po_repo.close_call_if_in_flight(cur, call_id, r)        # ★ 先单独收尾：记账失败也不卡 in_flight
    log.log(logging.INFO if seq else logging.WARNING,
            "op=fba_plan_submit sheet=%s consignment=%s from_wid=%s idem=%s host=%s path=%s outcome=%s seq=%s plans=%s "
            "lx=%s %s cause=%s call=%s ms=%d", sheet_id, order["consignment_id"], order["s_wid"], order["idem_key"],
            r.host, r.path, r.outcome, seq, sns, r.lx_code, r.lx_msg, r.cause, call_id, (time.perf_counter() - t0) * 1000)
    if seq and sns:
        try:
            eid = _adopt(order, {"seq": seq, "order_sns": sns}, call_id, "create", actor)
        except Exception as e:  # noqa: BLE001 —— 领星已建、本地没记上：响亮点名
            log.error("op=fba_plan_submit_unrecorded sheet=%s idem=%s seq=%s plans=%s call=%s cause=%s", sheet_id,
                      order["idem_key"], seq, sns, call_id, cause_chain(e))
            return {"key": order["key"], "idem_key": order["idem_key"], "outcome": "unrecorded", "ext_no": seq,
                    "call_id": call_id, "cause": f"{type(e).__name__}: {e}"}
        return _lock_result(order, eid, sns, seq, "created", actor)
    if r.outcome == "rejected":
        return {"key": order["key"], "idem_key": order["idem_key"], "outcome": "rejected", "lx_code": r.lx_code,
                "lx_msg": r.lx_msg, "call_id": call_id}
    if r.outcome == "not_sent":
        return {"key": order["key"], "idem_key": order["idem_key"], "outcome": "not_sent", "cause": r.cause,
                "call_id": call_id}
    # 结果未知（或成功却拿不到批次号）→ 再查一次；仍不知道 → 待核实，★ 不重试
    hits, lr = lookup(sid, order["idem_key"])
    if len(hits) == 1:
        eid = _adopt(order, hits[0], call_id, "lookup", actor)
        return _lock_result(order, eid, hits[0]["order_sns"], hits[0]["seq"], "adopted", actor)
    return _needs_verify(order, call_id, {"create_outcome": r.outcome, "create_cause": r.cause,
                                          "lookup_outcome": lr.outcome, "hits": [h["seq"] for h in hits],
                                          "payload": gateway.redact(payload)}, r.cause, actor)


def _needs_verify(order: dict, call_id: int | None, detail: dict, cause, actor: str) -> dict:
    with pg_conn() as c, c.cursor() as cur:
        aid = repo.insert_attention(cur, order["consignment_id"], order["s_wid"], order["idem_key"], call_id, detail)
    log.warning("op=fba_plan_submit idem=%s outcome=needs_verify attention=%s detail=%s actor=%s", order["idem_key"], aid,
                detail, actor)
    return {"key": order["key"], "idem_key": order["idem_key"], "outcome": "needs_verify", "attention_id": aid,
            "cause": cause}


def check_cancellable(live: list[dict], sids: dict[int, str]) -> list[dict]:
    """取消前现查：任一计划已处理（人已在领星推进）→ 返回点名清单，调用方整体拒绝（CC-D4）。读失败 → 抛 503。"""
    blocked = []
    for e in live:
        g, r = read_batch(sids[e["consignment_id"]], e["ext_no"], e["at"].date() - dt.timedelta(days=1))
        if r.outcome != "ok":
            raise Conflict(503, "erp_unavailable", "查领星发货计划状态失败，现在不能取消", ext_no=e["ext_no"],
                           outcome=r.outcome, lx_msg=r.lx_msg, cause=r.cause)
        done = [p.get("order_sn") for p in (g or {}).get("list") or [] if str(p.get("status")) == fba_plan.PROCESSED]
        if done:
            blocked.append({"ext_no": e["ext_no"], "status": "已处理", "plans": done})
    return blocked


def cancel_one(e: dict, actor: str, reason: str) -> dict:
    """释放库存（已锁才放）→ 计划备注「SCM 已取消 请作废」→ 作废留痕。释放失败 = 停，点名（库存还锁着）。"""
    sns = e["order_sns"] or []
    if e.get("locked"):
        un = _stock("unlock", e["ext_ref_id"], e["consignment_id"], e["idem_key"], sns, actor)
        if not un["ok"]:
            raise Conflict(502 if un["outcome"] in ("rejected", "partial") else 503, "fba_unlock_failed",
                           "释放领星发货计划的库存失败，排货表不取消", ext_no=e["ext_no"], plans=sns, outcome=un["outcome"],
                           lx_msg=un["lx_msg"], cause=un["cause"], errors=un["errors"])
    marked = []
    for sn in sns:
        r = _call("fba_plan_update", {"order_sn": sn, "remark": CANCEL_REMARK})
        log.log(logging.INFO if r.outcome == "ok" else logging.WARNING,
                "op=fba_plan_mark_cancel plan=%s host=%s path=%s outcome=%s lx=%s %s cause=%s actor=%s", sn, r.host,
                r.path, r.outcome, r.lx_code, r.lx_msg, r.cause, actor)
        if r.outcome == "ok":
            marked.append(sn)
    with pg_conn() as c, c.cursor() as cur:
        repo.void_ext_ref(cur, e["ext_ref_id"], None, actor, f"{reason}（领星发货计划需人工作废）")
    return {"ext_no": e["ext_no"], "plans": sns, "marked": marked, "manual_void": True}


LISTING_BATCH = 10         # 领星 listing 的 search_value 上限（文档 Sale/Listing：≤ 10）


def live_fnsku(keys: list[tuple[str, str]]) -> dict[tuple[str, str], str] | None:
    """AR-D1：fnsku 现查领星 listing（不读 CH 镜像 —— 镜像隔天才刷，人在亚马逊改好了也要等一天）。
    → {(seller_sku, sid): fnsku}（'' = listing 在但没有 FNSKU / 领星里没有这个 msku）；任一批读失败 → None（不猜）。"""
    out: dict[tuple[str, str], str] = {k: "" for k in keys}
    by_sid: dict[str, list[str]] = {}
    for m, sid in keys:
        by_sid.setdefault(str(sid), []).append(m)
    for sid, mskus in sorted(by_sid.items()):
        for i in range(0, len(mskus), LISTING_BATCH):
            batch = mskus[i:i + LISTING_BATCH]
            t0 = time.perf_counter()
            r = _call("amazon_listing", {"sid": sid, "search_field": "seller_sku", "search_value": batch,
                                         "exact_search": 1, "offset": 0, "length": 1000})
            if r.outcome != "ok":
                log.warning("op=fba_listing sid=%s mskus=%s host=%s path=%s outcome=%s lx=%s %s cause=%s ms=%d", sid, batch,
                            r.host, r.path, r.outcome, r.lx_code, r.lx_msg, r.cause, (time.perf_counter() - t0) * 1000)
                return None
            for x in (r.body or {}).get("data") or []:
                k = (x.get("seller_sku"), str(x.get("sid")))
                if k in out and not x.get("is_delete"):
                    out[k] = (x.get("fnsku") or "").strip()
            log.info("op=fba_listing sid=%s mskus=%s host=%s path=%s outcome=ok fnsku=%s ms=%d", sid, batch, r.host, r.path,
                     {m: out.get((m, sid)) for m in batch}, (time.perf_counter() - t0) * 1000)
    return out
