"""提交领星海外仓备货单（spec 2026-10-12 §5）。取 → 判 → 调 → 记，出站留痕在这里。

★ 出站约定（CLAUDE.md 出站 #2 #3 #4）：建前查重 · 已拿到单号的跳过（断点续跑）·
  结果未知先再查重、仍不知道就转人工（待核实），**绝不自动重试**。
★ HTTP 不持任何行锁；每次调用前后各一个短事务（同采购单 place 的形状）。
★ 同一张表的提交 / 取消 / 核实用会话级 advisory lock 互斥（sheet_op）：查重与建单之间若有别人插进来，
  就会建出第二张真单（终审 C2 / I3）。
"""
from __future__ import annotations

import contextlib
import datetime as dt
import logging
import re
import time

from erp import gateway
from erp.transport import CallResult, cause_chain
from repo import dispatch as repo
from repo import purchase as po_repo
from rules import fba_plan, ows
from shared.pg_client import business_schema, pg_conn, pg_error_fields, timed
from usecase.draft_po import Conflict
from usecase import submit_fba
from usecase.place_po import call_logged

log = logging.getLogger("scm.usecase")


def _iso(v):
    return None if v is None else v.isoformat()


@contextlib.contextmanager
def sheet_op(sheet_id: int):
    """一张表同一时刻只一个出站操作。拿不到就 409，不排队 —— 人看到「正在进行」比干等清楚。"""
    key = f"{business_schema()}.sheet_op"
    with pg_conn(autocommit=True) as c, c.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(hashtext(%s), %s)", (key, sheet_id))
        if not cur.fetchone()[0]:
            log.warning("op=sheet_op sheet=%s outcome=busy", sheet_id)
            raise Conflict(409, "sheet_busy", "这张排货表正在提交 / 取消 / 核实，等它结束再试", sheet_id=sheet_id)
        try:
            yield
        finally:
            cur.execute("SELECT pg_advisory_unlock(hashtext(%s), %s)", (key, sheet_id))


def _fba_live_fnsku(sheet_id: int) -> dict | None:
    """FBA 表：提交前现查领星 listing 的 fnsku（AR-D1）；不是 FBA 表 → {}。HTTP 不持数据库连接。"""
    with pg_conn() as c, c.cursor() as cur:
        s = repo.read_sheet(cur, sheet_id)
        if s is None or s["route"] != "fba":
            return {}
        keys = sorted({(ln["seller_sku"], ln["sid"]) for c_ in repo.consignments(cur, sheet_id) if c_["channel"] == "fba"
                       for ln in c_["lines"]})
    return submit_fba.live_fnsku(keys) if keys else {}


def _plan(cur, sheet_id: int, fnsku_live: dict | None = None) -> tuple[dict, dict, list[dict]]:
    s = repo.read_sheet(cur, sheet_id)
    if s is None:
        raise Conflict(404, "sheet_not_found", "排货表不存在", sheet_id=sheet_id)
    cons = repo.consignments(cur, sheet_id)
    skus = sorted({ln["sku"] for c in cons for ln in c["lines"]})
    keys = sorted({(ln["seller_sku"], ln["sid"]) for c in cons for ln in c["lines"]})
    all_refs = repo.ext_refs(cur, sheet_id)
    refs = [e for e in all_refs if e["voided_at"] is None]
    ext = {(e["consignment_id"], e["from_wid"]): e["ext_no"] for e in refs}
    if s["route"] == "fba":                                        # ★ C-c1（CC-D1）：领星 FBA 发货计划
        plan = fba_plan.plan_orders(cons, fnsku_live or {}, repo.packing(cur, sheet_id), ext)
        if fnsku_live is None:
            # listing 读不到：不拿镜像冒充（AR-D1），也不把每行都报成「没有 fnsku」—— 只报读不到
            plan["blockers"] = [b for b in plan["blockers"] if b["code"] != "fnsku_missing"]
            plan["blockers"].append({"code": "fnsku_unavailable"})
        lock = {(e["consignment_id"], e["from_wid"]): e for e in refs if e["kind"] == "fba_plan"}
        for o in plan["orders"]:
            ref = lock.get((o["consignment_id"], o["s_wid"]))
            o["locked"] = None if ref is None else bool(ref["locked"])
            o["order_sns"] = None if ref is None else ref["order_sns"]
    else:
        plan = ows.plan_orders(cons, repo.sheet_logistics(cur, sheet_id), repo.product_ids(cur, skus),
                               repo.fnsku_map(cur, keys), ext)
    if s["route"] is None:
        plan["blockers"].append({"code": "legacy_mixed"})          # ★ CA-D13：旧版混合表只能看与取消
    voided = [e["ext_no"] for e in all_refs if e["voided_at"] is not None]
    if voided and s["state"] == "已确认":
        # ★ 作废只来自取消删单：表还没取消 = 取消做了一半。再提交会按同一参考号建出一张没人记的真单（终审 C1）
        plan["blockers"].append({"code": "cancel_incomplete", "ext_nos": voided})
    return s, plan, repo.open_attention(cur, sheet_id)


#: matchSkuList 每页条数。★ 10-13 实测：领星接受 1000；每次调用 3.6~4.8 s，200 一页要翻 2 页（7.3 s）→ 1000 一页 1 次（4.8 s）
MATCH_PAGE = 1000


def _match_codes(wid: int) -> tuple[str, dict[int, list[str]] | None]:
    """领星 matchSkuList 现读一个仓的已配对三方编码 → ("api", {product_id: [code]}) / ("no_api", None) / ("fail", None)。
    ★ 读调用不落 erp_call（预览可能没有操作人）；日志三问照记。"""
    out: dict[int, list[str]] = {}
    off, t0 = 0, time.perf_counter()
    while True:
        try:
            _, r = gateway.call("ows_sku_match", {"wid": str(wid), "is_matched": 1, "offset": off, "length": MATCH_PAGE})
        except Exception as e:  # noqa: BLE001 —— 驱动抛了 = 读不到，不猜
            r = CallResult("unknown", "-", "-", 0, cause=f"driver exception: {cause_chain(e)}")
        if r.outcome != "ok":
            log.warning("op=ows_sku_match wid=%s host=%s path=%s outcome=%s lx=%s %s cause=%s offset=%s ms=%d", wid, r.host,
                        r.path, r.outcome, r.lx_code, r.lx_msg, r.cause, off, (time.perf_counter() - t0) * 1000)
            return "fail", None
        rows = (r.body or {}).get("data") or []
        for x in rows:
            if x.get("is_matched") and x.get("product_id") and x.get("oversea_product_code"):
                out.setdefault(int(x["product_id"]), []).append(str(x["oversea_product_code"]))
        off += len(rows)
        if not rows or off >= int((r.body or {}).get("total") or 0):
            break
    kind = "api"
    if not out:
        # 一条已配对都没有：分清「这个仓没接 API（不需要编码）」与「接了 API 但都没配」
        _, r = gateway.call("ows_sku_match", {"wid": str(wid), "offset": 0, "length": 1})
        if r.outcome != "ok":
            return "fail", None
        kind = "api" if int((r.body or {}).get("total") or 0) > 0 else "no_api"
    log.info("op=ows_sku_match wid=%s host=%s path=%s outcome=ok kind=%s matched_products=%d ms=%d", wid, r.host, r.path,
             kind, len(out), (time.perf_counter() - t0) * 1000)
    return kind, (out if kind == "api" else None)


def _with_sku_codes(plan: dict, kinds: dict[int, str]) -> dict:
    """三方海外仓（kind = oversea_3pl）的待建单：现查配对、带上三方编码；不持任何连接做 HTTP。"""
    wids = sorted({o["r_wid"] for o in ows.pending(plan) if kinds.get(o["r_wid"]) == "oversea_3pl"})
    codes: dict[int, dict[int, list[str]] | None] = {}
    for w in wids:
        kind, m = _match_codes(w)
        if kind != "no_api":
            codes[w] = m
    ows.apply_sku_codes(plan, codes)
    return plan


def _token(route: str | None, plan: dict) -> str:
    return fba_plan.confirm_token(plan) if route == "fba" else ows.confirm_token(plan)


def _pending(route: str | None, plan: dict) -> list[dict]:
    return fba_plan.pending(plan) if route == "fba" else ows.pending(plan)


def _unlocked(route: str | None, plan: dict) -> list[dict]:
    """FBA：建成了但库存没锁上的批次（CC-D3）—— 再提交只重锁。"""
    return [o for o in plan["orders"] if route == "fba" and o["ext_no"] is not None and o.get("locked") is False]


def preview(sheet_id: int) -> dict:
    live = _fba_live_fnsku(sheet_id)
    with timed("ows_preview", sheet=sheet_id), pg_conn() as c, c.cursor() as cur:
        s, plan, attn = _plan(cur, sheet_id, live)
        kinds = {w: v["kind"] for w, v in repo.warehouse_kinds(cur).items()}
    if s["route"] != "fba":
        plan = _with_sku_codes(plan, kinds)
    for o in plan["orders"]:
        o.pop("product_skus", None)
    return {"sheet_id": sheet_id, "state": s["state"], "route": s["route"], **plan,
            "to_lock": [o["idem_key"] for o in _unlocked(s["route"], plan)],
            "confirm_token": _token(s["route"], plan),
            "attention": [{**a, "opened_at": _iso(a["opened_at"])} for a in attn]}


def _lookup(order: dict, actor: str) -> tuple[list[str], CallResult]:
    """listOrderNos 按参考号查 → 命中的全部领星单号。读失败 → ([], r)，调用方先看 r.outcome 再信「没有」。
    ★ 命中多张（领星若不拒重复参考号，CB-Q2）由调用方转人工，不挑第一张（终审 I4）。"""
    t0 = time.perf_counter()
    _, r = call_logged("ows_order_nos", {"inbound_order_no": [order["idem_key"]]}, po_id=None, actor=actor,
                       retry_of=None)
    hits = [] if r.outcome != "ok" else [d["overseas_order_no"] for d in ((r.body or {}).get("data") or [])
                                         if d.get("inbound_order_no") == order["idem_key"]]
    log.info("op=ows_lookup idem=%s consignment=%s host=%s path=%s outcome=%s hits=%s cause=%s ms=%d",
             order["idem_key"], order.get("consignment_id"), r.host, r.path, r.outcome, hits, r.cause,
             (time.perf_counter() - t0) * 1000)
    return hits, r


def _many(sheet_id: int, order: dict, hits: list[str], call_id: int | None, actor: str) -> dict:
    with pg_conn() as c, c.cursor() as cur:
        aid = repo.insert_attention(cur, order["consignment_id"], order["s_wid"], order["idem_key"], call_id,
                                    {"hits": hits, "reason": "multiple_orders_for_reference"})
    log.warning("op=ows_submit sheet=%s idem=%s outcome=needs_verify reason=multiple_hits hits=%s attention=%s actor=%s",
                sheet_id, order["idem_key"], hits, aid, actor)
    return {"key": order["key"], "idem_key": order["idem_key"], "outcome": "needs_verify", "attention_id": aid,
            "cause": f"领星里有 {len(hits)} 张同参考号的备货单：{'、'.join(hits)}"}


def _adopt(order: dict, ext_no: str, call_id: int | None, found_by: str, actor: str) -> int:
    with pg_conn() as c, c.cursor() as cur:
        return repo.insert_ext_ref(cur, order["consignment_id"], order["s_wid"], order["idem_key"], ext_no, call_id,
                                   found_by, actor)


def _create(sheet_id: int, order: dict, actor: str) -> dict:
    """一张备货单：查重 → 建 → 收尾。返回这一张的结果；调用方据 outcome 决定继续还是停。"""
    t0 = time.perf_counter()
    hits, lr = _lookup(order, actor)
    if len(hits) > 1:
        return _many(sheet_id, order, hits, None, actor)
    if hits:
        hit = hits[0]
        _adopt(order, hit, None, "lookup", actor)
        log.info("op=ows_submit sheet=%s idem=%s outcome=adopted ows=%s via=lookup_before", sheet_id, order["idem_key"], hit)
        return {"key": order["key"], "idem_key": order["idem_key"], "outcome": "adopted", "ext_no": hit}
    if lr.outcome != "ok":
        # 查重本身读不到：不知道领星里有没有，不能放心去建
        log.warning("op=ows_submit sheet=%s idem=%s outcome=lookup_failed lookup=%s cause=%s", sheet_id,
                    order["idem_key"], lr.outcome, lr.cause)
        return {"key": order["key"], "idem_key": order["idem_key"], "outcome": "lookup_failed",
                "lookup_outcome": lr.outcome, "cause": lr.cause}
    payload = ows.payload(order, sheet_id)
    with pg_conn() as c, c.cursor() as cur:
        call_id = repo.insert_write_call(cur, endpoint="create_ows_order", side_effect="reversible",
                                         consignment_id=order["consignment_id"], idem_key=order["idem_key"],
                                         actor=actor, request=gateway.redact(payload))
    try:
        _, r = gateway.call("create_ows_order", payload)
    except Exception as e:  # noqa: BLE001 —— 驱动抛了也可能已发出：按结果未知走查重
        log.error("op=ows_submit_driver_exception sheet=%s call=%s cause=%s", sheet_id, call_id, cause_chain(e))
        r = CallResult("unknown", "-", "-", 0, cause=f"driver exception: {cause_chain(e)}")
    ext_no = ((r.body or {}).get("data") or {}).get("overseas_order_no") if r.outcome == "ok" else None
    with pg_conn() as c, c.cursor() as cur:
        po_repo.close_call_if_in_flight(cur, call_id, r)        # ★ 先单独收尾：记账失败也不许卡 in_flight（终审 C1）
    if ext_no:
        try:
            with pg_conn() as c, c.cursor() as cur:
                repo.insert_ext_ref(cur, order["consignment_id"], order["s_wid"], order["idem_key"], ext_no, call_id,
                                    "create", actor)
        except Exception as e:  # noqa: BLE001 —— 领星已建、本地没记上：响亮点名单号，别让它变成一张没人管的锁库存单
            pg = pg_error_fields(e) if hasattr(e, "pgcode") else None
            log.error("op=ows_submit_unrecorded sheet=%s consignment=%s idem=%s ows=%s call=%s err_type=%s pg=%s cause=%s",
                      sheet_id, order["consignment_id"], order["idem_key"], ext_no, call_id, type(e).__name__, pg,
                      cause_chain(e))
            return {"key": order["key"], "idem_key": order["idem_key"], "outcome": "unrecorded", "ext_no": ext_no,
                    "call_id": call_id, "cause": f"{type(e).__name__}: {e}"}
    ms = (time.perf_counter() - t0) * 1000
    log.log(logging.INFO if ext_no else logging.WARNING,
            "op=ows_submit sheet=%s consignment=%s from_wid=%s idem=%s host=%s path=%s outcome=%s ows=%s lx=%s %s "
            "cause=%s call=%s ms=%d", sheet_id, order["consignment_id"], order["s_wid"], order["idem_key"], r.host,
            r.path, r.outcome, ext_no, r.lx_code, r.lx_msg, r.cause, call_id, ms)
    if ext_no:
        return {"key": order["key"], "idem_key": order["idem_key"], "outcome": "created", "ext_no": ext_no}
    if r.outcome == "rejected":
        return {"key": order["key"], "idem_key": order["idem_key"], "outcome": "rejected", "lx_code": r.lx_code,
                "lx_msg": r.lx_msg, "call_id": call_id}
    if r.outcome == "not_sent":
        return {"key": order["key"], "idem_key": order["idem_key"], "outcome": "not_sent", "cause": r.cause,
                "call_id": call_id}
    # 结果未知（或成功却拿不到单号）→ 再查一次；仍不知道 → 待核实，★ 不重试
    hits, lr = _lookup(order, actor)
    if len(hits) > 1:
        return _many(sheet_id, order, hits, call_id, actor)
    if hits:
        hit = hits[0]
        _adopt(order, hit, call_id, "lookup", actor)
        log.info("op=ows_submit sheet=%s idem=%s outcome=adopted ows=%s via=lookup_after call=%s", sheet_id,
                 order["idem_key"], hit, call_id)
        return {"key": order["key"], "idem_key": order["idem_key"], "outcome": "adopted", "ext_no": hit}
    with pg_conn() as c, c.cursor() as cur:
        aid = repo.insert_attention(cur, order["consignment_id"], order["s_wid"], order["idem_key"], call_id,
                                    {"create_outcome": r.outcome, "create_cause": r.cause, "lookup_outcome": lr.outcome,
                                     "lookup_cause": lr.cause, "payload": gateway.redact(payload)})
    log.warning("op=ows_submit sheet=%s idem=%s outcome=needs_verify attention=%s create=%s lookup=%s", sheet_id,
                order["idem_key"], aid, r.outcome, lr.outcome)
    return {"key": order["key"], "idem_key": order["idem_key"], "outcome": "needs_verify", "attention_id": aid,
            "cause": r.cause}


def _settle_dispatched(sheet_id: int, actor: str) -> list[int]:
    """每个来源仓都有领星单的排货单：已确认 → 已投送（库层触发器再核一次，020）。"""
    moved = []
    with pg_conn() as c, c.cursor() as cur:
        # ★ FBA 批次必须已锁库存才算（CC-D3；库层 consignment_dispatch_needs_lock 再核）
        live = {(e["consignment_id"], e["from_wid"]) for e in repo.ext_refs(cur, sheet_id)
                if e["voided_at"] is None and (e["kind"] != "fba_plan" or e["locked"])}
        for c_ in repo.consignments(cur, sheet_id):
            if c_["our_stage"] == "已确认" and c_["lines"] and \
                    all((c_["consignment_id"], ln["from_wid"]) in live for ln in c_["lines"]):
                repo.consignment_event(cur, c_["consignment_id"], "已确认", "已投送", actor, f"submit:{sheet_id}")
                moved.append(c_["consignment_id"])
    return moved


def submit(sheet_id: int, body: dict, actor: str) -> dict:
    with sheet_op(sheet_id):
        return _submit(sheet_id, body, actor)


def _submit(sheet_id: int, body: dict, actor: str) -> dict:
    t0 = time.perf_counter()
    live = _fba_live_fnsku(sheet_id)
    with pg_conn() as c, c.cursor() as cur:
        s, plan, attn = _plan(cur, sheet_id, live)
        kinds = {w: v["kind"] for w, v in repo.warehouse_kinds(cur).items()}
    if s["state"] != "已确认":
        raise Conflict(409, "sheet_not_confirmed", "只有已确认的排货表能提交领星", sheet_id=sheet_id, state=s["state"])
    if attn:
        raise Conflict(409, "needs_verify", "有备货单提交结果待人工核实，先处理", sheet_id=sheet_id,
                       attention=[a["idem_key"] for a in attn])
    if s["route"] != "fba":
        plan = _with_sku_codes(plan, kinds)    # ★ 与预览同一套：编码变了 token 就对不上
    if (body or {}).get("confirm_token") != _token(s["route"], plan):
        raise Conflict(409, "stale_confirm_token", "待建的备货单内容变了，请刷新后重新确认", sheet_id=sheet_id)
    if plan["blockers"]:
        raise Conflict(409, "submission_blocked", "有阻断项，先处理再提交", sheet_id=sheet_id, blockers=plan["blockers"])
    results = []
    with pg_conn() as c, c.cursor() as cur:
        refs = {e["idem_key"]: e for e in repo.ext_refs(cur, sheet_id) if e["voided_at"] is None}
    for order in _unlocked(s["route"], plan):     # ★ 先补锁上次没锁上的（CC-D3：只重锁不重建）
        res = submit_fba.relock(order, refs[order["idem_key"]], actor)
        results.append(res)
        if res["outcome"] != "locked":
            break
    else:
        for order in _pending(s["route"], plan):
            res = submit_fba.create(sheet_id, order, actor) if s["route"] == "fba" else _create(sheet_id, order, actor)
            results.append(res)
            if res["outcome"] not in ("created", "adopted"):
                break                              # ★ 出错就停：剩下的等人看过再点（断点续跑）
    moved = _settle_dispatched(sheet_id, actor)
    log.info("op=ows_submit_done sheet=%s results=%s dispatched=%s actor=%s ms=%d", sheet_id,
             [(r["idem_key"], r["outcome"]) for r in results], moved, actor, (time.perf_counter() - t0) * 1000)
    return {"sheet_id": sheet_id, "results": results, "dispatched": moved, "submission": preview(sheet_id)}


# ── 待核实三出口（spec §5.3，同采购单）────────────────────────────

def _attention(cur, sheet_id: int, attention_id) -> dict:
    a = next((x for x in repo.open_attention(cur, sheet_id) if x["attention_id"] == attention_id), None)
    if a is None:
        raise Conflict(404, "attention_not_found", "没有这条待核实（或已处理）", attention_id=attention_id)
    return a


def verify(sheet_id: int, body: dict, actor: str) -> dict:
    with sheet_op(sheet_id):
        return _verify(sheet_id, body, actor)


def _is_fba(a: dict) -> bool:
    return a["idem_key"].startswith("SCM-FBA-")


def _fba_ctx(a: dict) -> tuple[str, object]:
    """FBA 待核实：查重要店铺 sid 与起始日（开立日前一天）。"""
    with pg_conn() as c, c.cursor() as cur:
        sid = repo.consignment_dest_sid(cur, a["consignment_id"])
    return sid, a["opened_at"].date() - dt.timedelta(days=1)


def _fba_adopt_and_close(sheet_id: int, a: dict, hit: dict, how: str, actor: str) -> dict:
    with pg_conn() as c, c.cursor() as cur:
        eid = repo.insert_ext_ref(cur, a["consignment_id"], a["from_wid"], a["idem_key"], hit["seq"], None,
                                  "lookup" if how == "recheck" else "register", actor, kind="fba_plan")
        repo.insert_fba_plan_order(cur, eid, hit["order_sns"])
        repo.close_attention(cur, a["attention_id"], how, eid, actor, None)
    log.info("op=fba_attention_close sheet=%s attention=%s how=%s seq=%s plans=%s actor=%s", sheet_id, a["attention_id"],
             how, hit["seq"], hit["order_sns"], actor)
    # ★ 认下的批次还没锁库存：下一次「提交领星」只重锁（CC-D3）
    return {"attention_id": a["attention_id"], "ext_no": hit["seq"], "dispatched": [], "needs_lock": True}


def _verify(sheet_id: int, body: dict, actor: str) -> dict:
    """重新查重：领星里查到了 → 认下来、关掉待核实；查不到 → 仍开着。"""
    with pg_conn() as c, c.cursor() as cur:
        a = _attention(cur, sheet_id, (body or {}).get("attention_id"))
    if _is_fba(a):
        sid, since = _fba_ctx(a)
        hits, r = submit_fba.lookup(sid, a["idem_key"], since)
        if len(hits) > 1:
            raise Conflict(409, "multiple_found", "领星里有多个同参考号的发货计划批次：在领星作废多余的，再登记留下的那个",
                           idem_key=a["idem_key"], hits=[h["seq"] for h in hits])
        if not hits:
            raise Conflict(409, "still_unknown", "领星里还查不到这个发货计划批次：可以登记已有批次号，或确认领星无此批次",
                           idem_key=a["idem_key"], lookup_outcome=r.outcome, cause=r.cause)
        return _fba_adopt_and_close(sheet_id, a, hits[0], "recheck", actor)
    order = {"idem_key": a["idem_key"], "consignment_id": a["consignment_id"], "s_wid": a["from_wid"]}
    hits, r = _lookup(order, actor)
    if len(hits) > 1:
        raise Conflict(409, "multiple_found", "领星里有多张同参考号的备货单：在领星删掉多余的，再登记留下的那张",
                       idem_key=a["idem_key"], hits=hits)
    hit = hits[0] if hits else None
    if not hit:
        raise Conflict(409, "still_unknown", "领星里还查不到这张备货单：可以登记已有单号，或确认领星无此单",
                       idem_key=a["idem_key"], lookup_outcome=r.outcome, cause=r.cause)
    with pg_conn() as c, c.cursor() as cur:
        eid = repo.insert_ext_ref(cur, a["consignment_id"], a["from_wid"], a["idem_key"], hit, None, "lookup", actor)
        repo.close_attention(cur, a["attention_id"], "recheck", eid, actor, None)
    log.info("op=ows_attention_close sheet=%s attention=%s how=recheck ows=%s actor=%s", sheet_id, a["attention_id"], hit, actor)
    return {"attention_id": a["attention_id"], "ext_no": hit, "dispatched": _settle_dispatched(sheet_id, actor)}


def register(sheet_id: int, body: dict, actor: str) -> dict:
    with sheet_op(sheet_id):
        return _register(sheet_id, body, actor)


def _register(sheet_id: int, body: dict, actor: str) -> dict:
    """人在领星里找到了单号：先现查详情，参考号对得上才认（不是我们的单不许挂上来）。"""
    ext_no = str((body or {}).get("ext_no") or "").strip()
    if not ext_no:
        raise Conflict(400, "missing_required", "要登记的领星备货单号", missing=["ext_no"])
    with pg_conn() as c, c.cursor() as cur:
        a = _attention(cur, sheet_id, (body or {}).get("attention_id"))
    if _is_fba(a):
        sid, since = _fba_ctx(a)
        g, r = submit_fba.read_batch(sid, ext_no, since)
        ref = (g or {}).get("remark")
        if (ref or "").strip() != a["idem_key"]:
            raise Conflict(409, "ext_no_mismatch", "这个领星发货计划批次的备注不是这张提交的参考号（或查不到）", ext_no=ext_no,
                           expected=a["idem_key"], found=ref, read_outcome=r.outcome, lx_msg=r.lx_msg)
        sns = [p["order_sn"] for p in (g or {}).get("list") or [] if p.get("order_sn")]
        return _fba_adopt_and_close(sheet_id, a, {"seq": ext_no, "order_sns": sns}, "register", actor)
    _, r = call_logged("ows_order_detail", {"overseas_order_no": ext_no}, po_id=None, actor=actor, retry_of=None)
    ref = ((r.body or {}).get("data") or {}).get("inbound_order_no") if r.outcome == "ok" else None
    if ref != a["idem_key"]:
        raise Conflict(409, "ext_no_mismatch", "这张领星备货单的参考号不是这张提交的（或查不到）", ext_no=ext_no,
                       expected=a["idem_key"], found=ref, read_outcome=r.outcome, lx_msg=r.lx_msg)
    with pg_conn() as c, c.cursor() as cur:
        eid = repo.insert_ext_ref(cur, a["consignment_id"], a["from_wid"], a["idem_key"], ext_no, None, "register", actor)
        repo.close_attention(cur, a["attention_id"], "register", eid, actor, None)
    log.info("op=ows_attention_close sheet=%s attention=%s how=register ows=%s actor=%s", sheet_id, a["attention_id"],
             ext_no, actor)
    return {"attention_id": a["attention_id"], "ext_no": ext_no, "dispatched": _settle_dispatched(sheet_id, actor)}


def confirm_absent(sheet_id: int, body: dict, actor: str) -> dict:
    with sheet_op(sheet_id):
        return _confirm_absent(sheet_id, body, actor)


def _confirm_absent(sheet_id: int, body: dict, actor: str) -> dict:
    """人确认领星里没有：要理由；再查一次，查到了就不许这么说（证据矛盾）。之后允许重建。"""
    reason = str((body or {}).get("reason") or "").strip()
    if not reason:
        raise Conflict(400, "reason_required", "确认领星无此单必须写理由", field="reason")
    with pg_conn() as c, c.cursor() as cur:
        a = _attention(cur, sheet_id, (body or {}).get("attention_id"))
    if _is_fba(a):
        sid, since = _fba_ctx(a)
        fhits, r = submit_fba.lookup(sid, a["idem_key"], since)
        hits = [h["seq"] for h in fhits]
    else:
        hits, r = _lookup({"idem_key": a["idem_key"]}, actor)
    hit = hits or None
    if hit or r.outcome != "ok":
        raise Conflict(409, "contradicts_evidence" if hit else "lookup_failed",
                       "领星里查得到这张单，不能确认为无" if hit else "查重读不到领星，现在没法确认无此单",
                       found=hit, idem_key=a["idem_key"], lookup_outcome=r.outcome)
    with pg_conn() as c, c.cursor() as cur:
        repo.close_attention(cur, a["attention_id"], "confirm_absent", None, actor, reason)
    log.warning("op=ows_attention_close sheet=%s attention=%s how=confirm_absent reason=%s actor=%s", sheet_id,
                a["attention_id"], reason, actor)
    return {"attention_id": a["attention_id"], "closed": "confirm_absent"}


# ── 取消时删除领星备货单（CB-D6，spec §5.4）──────────────────────────

DELETABLE = {"10", "20", "30", "40", "待审核", "已驳回", "待配货", "待发货"}   # 领星文档 DeleteOverSeaStockOrder


def delete_all(sheet_id: int, actor: str, reason: str) -> dict:
    """先逐张现查状态，任一已过待发货 → 整体拒绝（一张都不删）；全可删才逐张删，删一张记一张作废。
    → {deleted: [领星单号], manual_void: [{ext_no, plans, marked}]}（后者 = FBA 计划需在领星手工作废，CC-D4）。"""
    with pg_conn() as c, c.cursor() as cur:
        if repo.open_attention(cur, sheet_id):
            raise Conflict(409, "needs_verify", "有备货单提交结果待人工核实，先处理再取消", sheet_id=sheet_id)
        all_live = [e for e in repo.ext_refs(cur, sheet_id) if e["voided_at"] is None]
        sids = {e["consignment_id"]: repo.consignment_dest_sid(cur, e["consignment_id"]) for e in all_live}
    fba = [e for e in all_live if e["kind"] == "fba_plan"]
    live = [e for e in all_live if e["kind"] != "fba_plan"]
    # ★ CC-D4：FBA 计划没有作废接口 —— 先全部现查，任一已处理整体拒绝；再释放库存 + 备注 + 点名人工作废
    fblocked = submit_fba.check_cancellable(fba, sids)
    if fblocked:
        log.warning("op=fba_plan_cancel sheet=%s outcome=refused blocked=%s actor=%s", sheet_id, fblocked, actor)
        raise Conflict(409, "fba_plan_not_cancellable", "有领星发货计划已「已处理」（已在领星推进），排货表不取消",
                       orders=fblocked)
    fdone = [submit_fba.cancel_one(e, actor, reason) for e in fba]
    if fdone:
        log.warning("op=fba_plan_cancel sheet=%s outcome=released_needs_manual_void plans=%s actor=%s", sheet_id, fdone,
                    actor)
    if not live:
        return {"deleted": [d["ext_no"] for d in fdone], "manual_void": fdone}
    blocked, gone = [], []
    for e in live:
        did, r = call_logged("ows_order_detail", {"overseas_order_no": e["ext_no"]}, po_id=None, actor=actor, retry_of=None)
        if r.outcome == "rejected" and "不存在或已删除" in (r.lx_msg or ""):
            # ★ 上次删除超时其实删掉了（终审 I1）：再按参考号确认一次，真没了才记作废；否则照旧拒绝
            hits, lr = _lookup(e, actor)
            if lr.outcome == "ok" and e["ext_no"] not in hits:
                gone.append((e, did))
                continue
        if r.outcome != "ok":
            raise Conflict(503, "erp_unavailable", "查领星备货单状态失败，现在不能取消", ext_no=e["ext_no"],
                           outcome=r.outcome, lx_msg=r.lx_msg, cause=r.cause)
        st = str(((r.body or {}).get("data") or {}).get("status"))
        if st not in DELETABLE:
            blocked.append({"ext_no": e["ext_no"], "status": st})
    if blocked:
        log.warning("op=ows_cancel sheet=%s outcome=refused blocked=%s actor=%s", sheet_id, blocked, actor)
        raise Conflict(409, "ows_not_cancellable", "有备货单在领星已过「待发货」，删不掉，排货表不取消", orders=blocked)
    done = []
    for e, did in gone:
        with pg_conn() as c, c.cursor() as cur:
            repo.void_ext_ref(cur, e["ext_ref_id"], did, actor, f"{reason}（领星已无此单）")
        log.warning("op=ows_delete sheet=%s ows=%s outcome=already_gone call=%s actor=%s", sheet_id, e["ext_no"], did, actor)
        done.append(e["ext_no"])
    for e in (x for x in live if x not in [g for g, _ in gone]):
        with pg_conn() as c, c.cursor() as cur:
            call_id = repo.insert_write_call(cur, endpoint="ows_order_delete", side_effect="reversible",
                                             consignment_id=e["consignment_id"], idem_key=e["idem_key"], actor=actor,
                                             request={"overseas_order_nos": [e["ext_no"]]})
        try:
            _, r = gateway.call("ows_order_delete", {"overseas_order_nos": [e["ext_no"]]})
        except Exception as ex:  # noqa: BLE001
            r = CallResult("unknown", "-", "-", 0, cause=f"driver exception: {cause_chain(ex)}")
        with pg_conn() as c, c.cursor() as cur:
            po_repo.close_call_if_in_flight(cur, call_id, r)
            if r.outcome == "ok":
                repo.void_ext_ref(cur, e["ext_ref_id"], call_id, actor, reason)
        log.log(logging.INFO if r.outcome == "ok" else logging.WARNING,
                "op=ows_delete sheet=%s ows=%s host=%s path=%s outcome=%s lx=%s %s cause=%s call=%s actor=%s",
                sheet_id, e["ext_no"], r.host, r.path, r.outcome, r.lx_code, r.lx_msg, r.cause, call_id, actor)
        if r.outcome != "ok":
            raise Conflict(502 if r.outcome == "rejected" else 503, "ows_delete_failed",
                           "删除领星备货单失败，已删的照样留痕，排货表不取消", ext_no=e["ext_no"], deleted=done,
                           outcome=r.outcome, lx_msg=r.lx_msg, cause=r.cause)
        done.append(e["ext_no"])
    return {"deleted": done + [d["ext_no"] for d in fdone], "manual_void": fdone}


# ── 断点续跑：建 / 删调用中途进程没了（终审 I2）────────────────────────

_IDEM = re.compile(r"^SCM-(?:CN|FBA)-(\d+)-(\d+)$")


def recover_stuck(minutes: int) -> list[dict]:
    """排货单上 in_flight 超时的写调用：收成 unknown；建单的再开待核实（三出口照常可走）。
    删单的只收尾 —— 下次取消会现查状态，已删掉的按「领星已无此单」作废（I1）。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT call_id, endpoint, consignment_id, idem_key FROM erp_call WHERE outcome='in_flight'"
                    " AND consignment_id IS NOT NULL AND started_at < now() - make_interval(mins => %s)"
                    " ORDER BY call_id", (minutes,))
        stuck = cur.fetchall()
    out = []
    for call_id, endpoint, cid, idem in stuck:
        try:
            with pg_conn() as c, c.cursor() as cur:
                n = po_repo.close_call_if_in_flight(cur, call_id, CallResult(
                    "unknown", "-", "-", 0, cause=f"recovered: in_flight > {minutes} min（进程中断）"))
                aid = None
                m = _IDEM.match(idem or "")
                if n and endpoint in ("create_ows_order", "create_fba_plan") and m:
                    aid = repo.insert_attention(cur, cid, int(m.group(2)), idem, call_id,
                                                {"create_outcome": "unknown", "opened_by": "recover"})
        except Exception as e:  # noqa: BLE001 —— 一条失败不拖累其余
            log.error("op=ows_recover_stuck call=%s endpoint=%s consignment=%s outcome=fail err_type=%s cause=%s",
                      call_id, endpoint, cid, type(e).__name__, cause_chain(e))
            continue
        if not n:
            continue
        log.warning("op=ows_recover_stuck call=%s endpoint=%s consignment=%s idem=%s attention=%s", call_id, endpoint,
                    cid, idem, aid)
        out.append({"call_id": call_id, "endpoint": endpoint, "idem_key": idem, "attention_id": aid})
    return out


# ── 选目的仓时现查三方 sku 配对（10-13 负责人：选第三方海外仓时就提示）────────────

def sku_match_status(wid: int, sku: str) -> dict:
    """与提交时同一个判据（_match_codes / apply_sku_codes · CB-D8）：只有第三方海外仓（kind = oversea_3pl）才查。
    → {wid, sku, product_id, third_party, needs_match, matched, codes}；读不到 → 503，不猜。"""
    t0 = time.perf_counter()
    with pg_conn() as c, c.cursor() as cur:
        kind = (repo.warehouse_kinds(cur).get(wid) or {}).get("kind")
        pid = repo.product_ids(cur, [sku]).get(sku)
    out = {"wid": wid, "sku": sku, "product_id": pid, "third_party": kind == "oversea_3pl", "needs_match": False,
           "matched": None, "codes": []}
    if kind != "oversea_3pl":
        return out
    api, codes = _match_codes(wid)
    if api == "fail":
        raise Conflict(503, "erp_unavailable", "领星三方 sku 配对读不到，稍后再试", wid=wid, sku=sku)
    if api == "api":
        got = sorted(set((codes or {}).get(pid) or [])) if pid else []
        out.update(needs_match=True, matched=bool(got), codes=got)
    log.info("op=ows_sku_match_check wid=%s sku=%s product_id=%s api=%s matched=%s codes=%s ms=%d", wid, sku, pid, api,
             out["matched"], out["codes"], (time.perf_counter() - t0) * 1000)
    return out
