"""⛔ 下单（spec §6.2~§6.4）。

事务①：锁草稿 → 校验 token / 无待核实 → 落 erp_call(in_flight) → COMMIT
HTTP：createPurchaseOrder（★ 不持锁跨 HTTP；in_flight 唯一索引就是锁）
事务②：按结果收尾。结果未知 → 立即按幂等键查重；查不到 → 待人工核实，绝不自动重试。
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import logging
import re

from erp import gateway
from erp.endpoints import get as get_endpoint
from erp.transport import CallResult, cause_chain
from repo import purchase as repo
from rules.purchase import (
    create_order_payload,
    extract_order_sn,
    order_differences,
    orders_by_custom_sn,
)
from shared.config import erp as erp_config
from shared.pg_client import pg_conn, timed
from usecase.draft_po import Conflict, load_po
from usecase.line_state import reevaluate

log = logging.getLogger("scm.usecase")

_LOOKUP_LEN = 500


def _lookup_params(day: dt.date) -> dict:
    # ★ 无 custom_order_sn 过滤（erp/endpoints.py 出处）：按建单日前后各一天取，客户端筛
    return {"search_field_time": "create_time", "start_date": (day - dt.timedelta(days=1)).isoformat(),
            "end_date": (day + dt.timedelta(days=1)).isoformat(), "offset": 0, "length": _LOOKUP_LEN}


def _int_like(v) -> bool:
    return (isinstance(v, int) and not isinstance(v, bool)) or \
        (isinstance(v, str) and re.fullmatch(r"-?\d+", v.strip()) is not None)


def _explicit_rejection(r: CallResult) -> bool:
    """领星明确说了「不」：HTTP 2xx/4xx 且 JSON 里有整数形的非成功 code。"""
    code = (r.body or {}).get("code") if isinstance(r.body, dict) else None
    return (r.http_status is not None and (200 <= r.http_status < 300 or 400 <= r.http_status < 500)
            and _int_like(code) and int(code) not in (0, 200))


def _classify(name: str, r: CallResult) -> CallResult:
    """★ 不可逆端点上 rejected 会放开草稿、允许重下：只有明确的业务拒绝才配这个结论，
    其余（code 为空 / false / 非数字、4xx 无 code …）都可能已经建了单 → unknown，走查重。
    读端点不改：读失败本来就不放行任何东西。"""
    if r.outcome != "rejected" or get_endpoint(name).side_effect != "irreversible" or _explicit_rejection(r):
        return r
    code = (r.body or {}).get("code") if isinstance(r.body, dict) else None
    log.warning("op=erp_outcome_reclassified endpoint=%s from=rejected to=unknown http=%s code=%r cause=%s",
                name, r.http_status, code, r.cause)
    return dataclasses.replace(r, outcome="unknown",
                               cause=f"not an explicit business rejection (http={r.http_status} code={code!r})"
                                     + (f"; {r.cause}" if r.cause else ""))


def read_back(cur, po_id: int, order: dict, call_id: int | None) -> None:
    """落地首检的数据源（spec §11 G1）：领星明细进 po_item_obs，行号回填 po_line。"""
    items = order.get("item_list") or []
    n = repo.insert_item_obs(cur, order["order_sn"], items, "api", call_id)
    m = repo.backfill_item_ids(cur, po_id, items)
    log.info("op=po_read_back po_id=%s order_sn=%s items_obs=%s item_ids_backfilled=%s",
             po_id, order["order_sn"], n, m)


def call_logged(name: str, params: dict, *, po_id: int, actor: str,
                retry_of: int | None) -> tuple[int, CallResult]:
    """读调用：落 erp_call → 调 → 收尾。一个连接一个事务，失败也留痕（Task 12 / 13 复用）。"""
    with pg_conn() as c, c.cursor() as cur:
        ep = get_endpoint(name)
        cid = repo.insert_call(cur, endpoint=name, side_effect=ep.side_effect, po_id=po_id, idem_key=None,
                               actor=actor, request=gateway.redact(params), retry_of=retry_of)
    try:
        _, r = gateway.call(name, params)
    except Exception as e:  # noqa: BLE001 —— 同 place()：驱动抛了也要收尾留痕，调用方按 unknown 处理
        log.error("op=erp_read_exception name=%s po_id=%s err_type=%s cause=%s", name, po_id,
                  type(e).__name__, cause_chain(e))
        r = CallResult("unknown", "-", "-", 0, cause=f"driver exception: {cause_chain(e)}")
    with pg_conn() as c, c.cursor() as cur:
        if not repo.close_call_if_in_flight(cur, cid, r):
            # 恢复已按「卡住」收成 unknown；本次真实结果只在这里留一次痕，调用方照常用它
            log.warning("op=erp_read_closed_by_recovery name=%s po_id=%s call=%s outcome=%s http=%s cause=%s",
                        name, po_id, cid, r.outcome, r.http_status, r.cause)
    return cid, r


def _open_needs_verify(cur, po_id: int, create_call_id: int | None, opened_by: str, sent: bool | None,
                       custom_order_sn: str) -> int | None:
    """★ detail 开立即冻结（011 触发器）：只存开立时的事实，查重结果与说明性文字不进来。"""
    c = repo.get_call(cur, create_call_id) if create_call_id else None
    detail = {"create_call_id": create_call_id, "create_outcome": c["outcome"] if c else None,
              "create_cause": c["cause"] if c else None, "request_confirmed_sent": sent,
              "custom_order_sn": custom_order_sn, "opened_by": opened_by}
    return repo.open_attention_row(cur, po_id, "needs_verify", create_call_id, detail)


def _settle_placed(cur, po_id: int, order_sn: str, actor: str, src: str, resolve_attention: bool,
                   reason: str | None = None) -> list:
    """★ 同一事务：锁单 → 锁行 → 记已下单 → 收待处理 → 重判行状态。先锁单后锁行，与改 / 删草稿同序。"""
    repo.lock_po(cur, po_id)
    line_ids = repo.source_line_ids(cur, po_id)
    repo.lock_plan_lines(cur, line_ids)
    repo.mark_placed(cur, po_id, order_sn, actor, src)
    if resolve_attention:
        repo.resolve_attention(cur, po_id, "needs_verify", "rechecked_found", actor, reason)
    return reevaluate(cur, line_ids, actor, f"po:{po_id}:{src}")


def _read_back_safe(po_id: int, order: dict, call_id: int | None) -> None:
    """★ 已下单之后的读回只是补证据：失败只留痕，绝不让「已下单」变成 500。"""
    try:
        with pg_conn() as c, c.cursor() as cur:
            read_back(cur, po_id, order, call_id)
    except Exception as e:  # noqa: BLE001
        log.warning("op=po_read_back po_id=%s order_sn=%s outcome=fail err_type=%s cause=%s", po_id,
                    order.get("order_sn"), type(e).__name__, cause_chain(e))


def lookup_and_settle(po_id: int, actor: str, opened_by_call: int | None, src: str,
                      reason: str | None = None) -> dict:
    """按幂等键查领星。找到 → 记已下单并收掉待核实；其余（没找到 / 查询本身失败 / 结果被截断）
    一律保持待核实 —— ★ 查不了不等于没有。"""
    po = load_po(po_id)
    with pg_conn() as c, c.cursor() as cur:
        started = repo.get_call(cur, opened_by_call)["started_at"] if opened_by_call else None
    day = (started or dt.datetime.now().astimezone()).astimezone().date()  # 领星 create_time 是本地无时区串
    params = _lookup_params(day)
    cid, r = call_logged("purchase_order_list", params, po_id=po_id, actor=actor, retry_of=opened_by_call)
    rows = (r.body or {}).get("data") or [] if r.outcome == "ok" else []
    total = (r.body or {}).get("total") if r.outcome == "ok" else None
    truncated = r.outcome == "ok" and ((isinstance(total, int) and total > len(rows)) or len(rows) >= _LOOKUP_LEN)
    hits = orders_by_custom_sn(rows, po["custom_order_sn"])
    hit, mismatch = None, None
    if truncated:
        log.warning("op=po_lookup po_id=%s lookup_call=%s truncated=true total=%s fetched=%s", po_id, cid,
                    total, len(rows))
    with timed("po_settle", po_id=po_id, actor=actor), pg_conn() as c, c.cursor() as cur:
        locked = repo.lock_po(cur, po_id)
        if locked["discarded"]:
            log.warning("op=po_lookup_on_discarded po_id=%s lookup_call=%s found=%s -- 不记账，待核实保持",
                        po_id, cid, len(hits))
        elif locked["state"] == "已下单":
            # 同键的多张里只要有一张不是已记账的那张就是异常
            extra = next((o for o in hits if o["order_sn"] != locked["order_sn"]), None)
            if extra:
                hit = extra
                # ★ 另有一张同幂等键的真单：不能当成「已记账」吞掉，也不替人决定收哪张
                log.error("op=po_landed_mismatch po_id=%s recorded=%s also_found=%s lookup_call=%s",
                          po_id, locked["order_sn"], hit["order_sn"], cid)
                repo.open_attention_row(cur, po_id, "landed_mismatch", cid, {
                    "recorded_order_sn": locked["order_sn"], "also_found_order_sn": hit["order_sn"],
                    "custom_order_sn": po["custom_order_sn"], "lookup_call_id": cid})
                return {"outcome": "placed", "po_id": po_id, "order_sn": locked["order_sn"],
                        "erp_call_id": cid, "found_by": "already_recorded",
                        "landed_mismatch": hit["order_sn"]}
            repo.resolve_attention(cur, po_id, "needs_verify", "rechecked_found", actor, reason)
            return {"outcome": "placed", "po_id": po_id, "order_sn": locked["order_sn"],
                    "erp_call_id": cid, "found_by": "already_recorded"}
        else:
            hit, mismatch = _adoptable(po, hits, cid)
            if mismatch:
                _open_mismatch(cur, po_id, cid, mismatch)
        if hit:
            moved = _settle_placed(cur, po_id, hit["order_sn"], actor, src, resolve_attention=True,
                                  reason=reason)
        else:
            oc = repo.get_call(cur, opened_by_call) if opened_by_call else None
            if oc and oc["finished_at"] and repo.confirmed_absent_after(cur, po_id, oc["finished_at"]):
                # 查询期间已有人确认「领星无此单」：不再按「进程中断」语义重开
                log.warning("op=po_lookup_after_confirmed_absent po_id=%s lookup_call=%s -- 不重开待核实",
                            po_id, cid)
            else:
                _open_needs_verify(cur, po_id, opened_by_call, "self_heal", None, po["custom_order_sn"])
    if hit:
        _read_back_safe(po_id, hit, cid)
        log.warning("op=po_found_by_lookup po_id=%s order_sn=%s lookup_call=%s moved=%s",
                    po_id, hit["order_sn"], cid, moved)
        return {"outcome": "placed", "po_id": po_id, "order_sn": hit["order_sn"],
                "erp_call_id": cid, "found_by": "lookup"}
    log.warning("op=po_needs_verify po_id=%s custom_order_sn=%s lookup_call=%s lookup_outcome=%s truncated=%s"
                " hits=%s", po_id, po["custom_order_sn"], cid, r.outcome, truncated, [o["order_sn"] for o in hits])
    out = {"outcome": "needs_verify", "po_id": po_id, "erp_call_id": opened_by_call,
           "lookup_call_id": cid, "lookup_outcome": r.outcome, "truncated": truncated,
           "timeline": f"/v1/purchase-orders/{po_id}/timeline"}
    if mismatch:
        out["landed_mismatch"] = mismatch.get("order_sns") or [mismatch["order_sn"]]
    return out


def _adoptable(po: dict, hits: list[dict], cid: int) -> tuple[dict | None, dict | None]:
    """★ 幂等键对上不等于是这张单：同键多张不替人选；单张也要供应商 / 明细对得上才记账（同登记已有单的尺子）。"""
    sns = sorted({o["order_sn"] for o in hits})
    if len(sns) >= 2:
        return None, {"known": "领星里有多张单用了本单的幂等键", "unknown": "哪一张是本单下出去的",
                      "custom_order_sn": po["custom_order_sn"], "order_sns": sns, "lookup_call_id": cid}
    if not sns:
        return None, None
    diff = order_differences(po, hits[0])
    if diff:
        return None, {"known": "领星里按幂等键查到的单与本单对不上", "unknown": "它是不是本单下出去的",
                      "custom_order_sn": po["custom_order_sn"], "order_sn": sns[0], "differences": diff,
                      "lookup_call_id": cid}
    return hits[0], None


def _open_mismatch(cur, po_id: int, cid: int, detail: dict) -> None:
    aid = repo.open_attention_row(cur, po_id, "landed_mismatch", cid, detail)
    log.error("op=po_lookup_mismatch po_id=%s lookup_call=%s attention=%s detail=%s", po_id, cid,
              aid if aid else "already_open", detail)


def place(po_id: int, token: str, actor: str) -> dict:
    po = load_po(po_id)
    if po.get("discarded_at"):
        raise Conflict(409, "po_discarded", "这张草稿已作废", po_id=po_id)
    if token != po["confirm_token"]:
        raise Conflict(409, "stale_confirm_token", "采购单内容已变，请刷新后重新确认", po_id=po_id)
    failed = [g for g in po["preflight"] if not g["ok"]]
    if failed:
        raise Conflict(409, "preflight_failed", "下单前检查未通过", po_id=po_id, failed=failed)
    payload = create_order_payload(po)

    # 事务①
    blocked = None
    with timed("po_place_begin", po_id=po_id, actor=actor), pg_conn() as c, c.cursor() as cur:
        locked = repo.lock_po(cur, po_id)
        if locked["discarded"]:
            raise Conflict(409, "po_discarded", "这张草稿已作废", po_id=po_id)
        if locked["state"] != "草稿":
            raise Conflict(409, "po_frozen", "采购单已下单", po_id=po_id, order_sn=locked["order_sn"])
        if repo.in_flight_call(cur, po_id):
            raise Conflict(409, "po_placing", "下单请求进行中", po_id=po_id)
        if any(a["kind"] == "needs_verify" for a in repo.open_attention(cur, po_id)):
            blocked = "open"
        else:
            last = repo.latest_create_call(cur, po_id)
            if last and last["outcome"] in ("ok", "unknown") and not repo.confirmed_absent_after(
                    cur, po_id, last["finished_at"]):
                # 发出过、没记上账、也没人确认过「没建」：补开待核实再拒（自愈），绝不放行重下
                _open_needs_verify(cur, po_id, last["call_id"], "self_heal", None, po["custom_order_sn"])
                blocked = "self_heal"
        if blocked is None:
            if locked["draft_version"] != po["draft_version"]:
                raise Conflict(409, "stale_confirm_token", "采购单内容已变，请刷新后重新确认", po_id=po_id)
            call_id = repo.insert_call(cur, endpoint="create_purchase_order", side_effect="irreversible",
                                       po_id=po_id, idem_key=po["custom_order_sn"],
                                       actor=actor, request=gateway.redact(payload),
                                       retry_of=repo.last_create_call(cur, po_id))   # ★ 重下单串成一条链
    if blocked:
        log.warning("op=po_place_refused po_id=%s reason=needs_verify via=%s", po_id, blocked)
        raise Conflict(409, "needs_verify", "这张单的下单结果待人工核实，先处理", po_id=po_id,
                       timeline=f"/v1/purchase-orders/{po_id}/timeline")

    # HTTP（不持任何锁）
    sent: bool | None = True
    try:
        _, r = gateway.call("create_purchase_order", payload)
    except Exception as e:  # noqa: BLE001 —— 驱动不该抛；抛了请求可能已发出，按结果未知走查重，不能留 in_flight
        log.error("op=po_place_driver_exception po_id=%s call=%s err_type=%s cause=%s", po_id, call_id,
                  type(e).__name__, cause_chain(e))
        r = CallResult("unknown", "-", "-", 0, cause=f"driver exception: {cause_chain(e)}")
        sent = None
    r = _classify("create_purchase_order", r)

    # 事务②：★ 「发出过但没记账」这个事实与 close_call 同一事务落盘
    order_sn = extract_order_sn(r.body) if r.outcome == "ok" else None
    with timed("po_place_settle", po_id=po_id, outcome=r.outcome), pg_conn() as c, c.cursor() as cur:
        closed = repo.close_call_if_in_flight(cur, call_id, r)
        if not closed:
            pass    # 恢复已先收尾（unknown + 待核实 + 查重）；这里再记账会与它的结论打架
        elif r.outcome == "ok" and order_sn:
            moved = _settle_placed(cur, po_id, order_sn, actor, f"place:{call_id}", resolve_attention=False)
        elif r.outcome in ("ok", "unknown"):
            _open_needs_verify(cur, po_id, call_id, "place", sent, po["custom_order_sn"])
    if not closed:
        return _lost_race(po_id, call_id, r, order_sn)
    if r.outcome == "rejected":
        return {"outcome": "rejected", "po_id": po_id, "lx_code": r.lx_code, "lx_msg": r.lx_msg,
                "erp_call_id": call_id}
    if r.outcome == "not_sent":
        return {"outcome": "not_sent", "po_id": po_id, "cause": r.cause, "erp_call_id": call_id,
                "hint": "请求没有发到领星，可以直接重试"}
    if r.outcome == "ok" and order_sn:
        log.info("op=po_placed po_id=%s order_sn=%s call=%s moved=%s", po_id, order_sn, call_id, moved)
        _first_check(po_id, order_sn, actor)
        return {"outcome": "placed", "po_id": po_id, "order_sn": order_sn, "erp_call_id": call_id}
    # 结果未知，或成功却认不出单号 → 查重
    return lookup_and_settle(po_id, actor, call_id, f"lookup_after:{call_id}")


def _lost_race(po_id: int, call_id: int, r: CallResult, order_sn: str | None) -> dict:
    """★ 事务② 之前 recover_stuck 已把这条调用收成 unknown：领星给的真实结果只剩这一次机会留痕。"""
    log.warning("op=po_place_settle_lost_race po_id=%s call=%s outcome=%s order_sn=%s http=%s lx_code=%s"
                " body=%s -- 调用已被恢复收尾，以库里状态为准", po_id, call_id, r.outcome, order_sn,
                r.http_status, r.lx_code, r.body)
    po = load_po(po_id)
    placed = po["state"] == "已下单"
    return {"outcome": "placed" if placed else "needs_verify", "po_id": po_id, "order_sn": po["order_sn"],
            "erp_call_id": call_id, "settled_by": "recover", "lingxing_outcome": r.outcome,
            "lingxing_order_sn": order_sn, "timeline": f"/v1/purchase-orders/{po_id}/timeline"}


def _first_check(po_id: int, order_sn: str, actor: str) -> None:
    """下单成功后的落地核对（G1）。★ 绝不抛：此时单已记账，核对炸了不能把已下单变成 500。"""
    from usecase import landed  # 函数内 import：landed 反向依赖本模块
    try:
        landed.verify(po_id, actor)
    except Exception as e:  # noqa: BLE001
        log.warning("op=po_first_check po_id=%s order_sn=%s outcome=fail err_type=%s cause=%s", po_id, order_sn,
                    type(e).__name__, cause_chain(e))


def recover_stuck(minutes: int | None = None) -> list[dict]:
    """断点续跑（spec §6.3）：① in_flight 超过 V5 分钟的不可逆调用；② 最近一次 create 为 ok/unknown
    却没记账也没待核实的草稿。都是：先落待核实，再按幂等键查重。
    ★ 收尾记在原下单人名下：actor 表里没有「系统」这个人（plan_line_event_actor_fk），也不该编一个。"""
    minutes = minutes if minutes is not None else int(erp_config()["stale_in_flight_minutes"])
    with pg_conn() as c, c.cursor() as cur:
        stuck = repo.stuck_calls(cur, minutes)
    out = []
    for call_id, po_id, actor in stuck:
        try:
            with pg_conn() as c, c.cursor() as cur:
                n = repo.close_call_if_in_flight(cur, call_id, CallResult(
                    "unknown", "-", "-", 0, cause=f"recovered: in_flight > {minutes} min（进程中断）"))
                if n == 0:
                    log.info("op=recover_stuck call=%s po_id=%s skipped=already_settled", call_id, po_id)
                    continue
                _open_needs_verify(cur, po_id, call_id, "recover", None, f"SCM-PO-{po_id}")
            res = lookup_and_settle(po_id, actor, call_id, f"recover:{call_id}")
        except Exception as e:  # noqa: BLE001 —— 一条失败不拖累其余
            log.error("op=recover_stuck call=%s po_id=%s outcome=fail err_type=%s cause=%s", call_id, po_id,
                      type(e).__name__, cause_chain(e))
            continue
        log.warning("op=recover_stuck call=%s po_id=%s result=%s", call_id, po_id, res["outcome"])
        out.append(res)
    with pg_conn() as c, c.cursor() as cur:
        unsettled = repo.unsettled_drafts(cur)
    for call_id, po_id, actor in unsettled:
        try:
            with pg_conn() as c, c.cursor() as cur:
                repo.lock_po(cur, po_id)
                _open_needs_verify(cur, po_id, call_id, "recover", None, f"SCM-PO-{po_id}")
            res = lookup_and_settle(po_id, actor, call_id, f"recover:{call_id}")
        except Exception as e:  # noqa: BLE001
            log.error("op=recover_unsettled call=%s po_id=%s outcome=fail err_type=%s cause=%s", call_id, po_id,
                      type(e).__name__, cause_chain(e))
            continue
        log.warning("op=recover_unsettled call=%s po_id=%s result=%s", call_id, po_id, res["outcome"])
        out.append(res)
    _close_stale_reads(minutes)
    return out


def _close_stale_reads(minutes: int) -> None:
    """③ 卡住的读调用：不开待核实（读不建单），只收尾 —— 否则「确认无此单」永远 lookup_in_progress。"""
    with pg_conn() as c, c.cursor() as cur:
        stale = repo.stuck_calls(cur, minutes, "read")
    for call_id, po_id, _ in stale:
        try:
            with pg_conn() as c, c.cursor() as cur:
                n = repo.close_call_if_in_flight(cur, call_id, CallResult(
                    "unknown", "-", "-", 0, cause=f"recovered: in_flight > {minutes} min（进程中断）"))
        except Exception as e:  # noqa: BLE001 —— 一条失败不拖累其余
            log.error("op=recover_stale_read call=%s po_id=%s outcome=fail err_type=%s cause=%s", call_id, po_id,
                      type(e).__name__, cause_chain(e))
            continue
        log.warning("op=recover_stale_read call=%s po_id=%s closed=%s", call_id, po_id, n)
