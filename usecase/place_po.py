"""⛔ 下单（spec §6.2~§6.4）。

事务①：锁草稿 → 校验 token / 无待核实 → 落 erp_call(in_flight) → COMMIT
HTTP：createPurchaseOrder（★ 不持锁跨 HTTP；in_flight 唯一索引就是锁）
事务②：按结果收尾。结果未知 → 立即按幂等键查重；查不到 → 待人工核实，绝不自动重试。
"""
from __future__ import annotations

import datetime as dt
import logging

from erp import gateway
from erp.endpoints import get as get_endpoint
from erp.transport import CallResult, cause_chain
from repo import purchase as repo
from rules.purchase import create_order_payload, extract_order_sn, find_by_custom_sn
from shared.config import erp as erp_config
from shared.pg_client import pg_conn, timed
from usecase.draft_po import Conflict, load_po
from usecase.line_state import reevaluate

log = logging.getLogger("scm.usecase")


def _lookup_params(day: dt.date) -> dict:
    # ★ 无 custom_order_sn 过滤（erp/endpoints.py 出处）：按建单日前后各一天取，客户端筛
    return {"search_field_time": "create_time", "start_date": (day - dt.timedelta(days=1)).isoformat(),
            "end_date": (day + dt.timedelta(days=1)).isoformat(), "offset": 0, "length": 500}


def _obs_source() -> str:
    return "api" if gateway.mode() == "live" else "sim"


def read_back(cur, po_id: int, order: dict, call_id: int | None) -> None:
    """落地首检的数据源（spec §11 G1）：领星明细进 po_item_obs，行号回填 po_line。"""
    items = order.get("item_list") or []
    n = repo.insert_item_obs(cur, order["order_sn"], items, _obs_source(), call_id)
    m = repo.backfill_item_ids(cur, po_id, items)
    log.info("op=po_read_back po_id=%s order_sn=%s items_obs=%s item_ids_backfilled=%s",
             po_id, order["order_sn"], n, m)


def call_logged(name: str, params: dict, *, po_id: int, actor: str,
                retry_of: int | None) -> tuple[int, CallResult]:
    """读调用：落 erp_call → 调 → 收尾。一个连接一个事务，失败也留痕（Task 12 / 13 复用）。"""
    with pg_conn() as c, c.cursor() as cur:
        ep = get_endpoint(name)
        cid = repo.insert_call(cur, endpoint=name, side_effect=ep.side_effect, mode=gateway.mode(),
                               po_id=po_id, idem_key=None, actor=actor, request=gateway.redact(params),
                               retry_of=retry_of)
    try:
        _, r = gateway.call(name, params)
    except Exception as e:  # noqa: BLE001 —— 同 place()：驱动抛了也要收尾留痕，调用方按 unknown 处理
        log.error("op=erp_read_exception name=%s po_id=%s err_type=%s cause=%s", name, po_id,
                  type(e).__name__, cause_chain(e))
        r = CallResult("unknown", "-", "-", 0, cause=f"driver exception: {cause_chain(e)}")
    with pg_conn() as c, c.cursor() as cur:
        repo.close_call(cur, cid, r)
    return cid, r


def lookup_and_settle(po_id: int, actor: str, opened_by_call: int | None, src: str) -> dict:
    """按幂等键查领星；找到 → 补记已下单；找不到 → 开（或保持）待人工核实。"""
    po = load_po(po_id)
    day = dt.datetime.now().astimezone().date()  # 领星 create_time 是本地无时区串，按本地日取窗
    cid, r = call_logged("purchase_order_list", _lookup_params(day), po_id=po_id, actor=actor,
                          retry_of=opened_by_call)
    hit = find_by_custom_sn((r.body or {}).get("data") or [], po["custom_order_sn"]) if r.outcome == "ok" else None
    with timed("po_settle", po_id=po_id, actor=actor), pg_conn() as c, c.cursor() as cur:
        locked = repo.lock_po(cur, po_id)
        if locked["state"] == "已下单":
            # 别的路径（启动恢复 / 另一次核对）已经记上了：只收掉待处理，不重复记账
            repo.resolve_attention(cur, po_id, "needs_verify", "rechecked_found", actor, None)
            return {"outcome": "placed", "po_id": po_id, "order_sn": locked["order_sn"],
                    "erp_call_id": cid, "found_by": "already_recorded"}
        if hit:
            repo.mark_placed(cur, po_id, hit["order_sn"], gateway.mode(), actor, src)
            read_back(cur, po_id, hit, cid)
            repo.resolve_attention(cur, po_id, "needs_verify", "rechecked_found", actor, None)
            line_ids = repo.source_line_ids(cur, po_id)
            repo.lock_plan_lines(cur, line_ids)
            moved = reevaluate(cur, line_ids, actor, f"po:{po_id}:{src}")
            log.warning("op=po_found_by_lookup po_id=%s order_sn=%s lookup_call=%s moved=%s",
                        po_id, hit["order_sn"], cid, moved)
            return {"outcome": "placed", "po_id": po_id, "order_sn": hit["order_sn"],
                    "erp_call_id": cid, "found_by": "lookup"}
        detail = {"lookup_call": cid, "lookup_outcome": r.outcome, "searched": _lookup_params(day),
                  "custom_order_sn": po["custom_order_sn"],
                  "known": "请求已发到领星，没有收到明确结果",
                  "unknown": "领星是否已建单（列表可能尚未刷新）"}
        repo.open_attention_row(cur, po_id, "needs_verify", opened_by_call, detail)
    log.warning("op=po_needs_verify po_id=%s custom_order_sn=%s lookup_call=%s lookup_outcome=%s",
                po_id, po["custom_order_sn"], cid, r.outcome)
    return {"outcome": "needs_verify", "po_id": po_id, "erp_call_id": opened_by_call,
            "lookup_call_id": cid, "timeline": f"/v1/purchase-orders/{po_id}/timeline"}


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
    with timed("po_place_begin", po_id=po_id, actor=actor), pg_conn() as c, c.cursor() as cur:
        locked = repo.lock_po(cur, po_id)
        if locked["discarded"]:
            raise Conflict(409, "po_discarded", "这张草稿已作废", po_id=po_id)
        if locked["state"] != "草稿":
            raise Conflict(409, "po_frozen", "采购单已下单", po_id=po_id, order_sn=locked["order_sn"])
        if any(a["kind"] == "needs_verify" for a in repo.open_attention(cur, po_id)):
            raise Conflict(409, "needs_verify", "这张单的下单结果待人工核实，先处理", po_id=po_id,
                           timeline=f"/v1/purchase-orders/{po_id}/timeline")
        if locked["draft_version"] != po["draft_version"]:
            raise Conflict(409, "stale_confirm_token", "采购单内容已变，请刷新后重新确认", po_id=po_id)
        call_id = repo.insert_call(cur, endpoint="create_purchase_order", side_effect="irreversible",
                                   mode=gateway.mode(), po_id=po_id, idem_key=po["custom_order_sn"],
                                   actor=actor, request=gateway.redact(payload),
                                   retry_of=repo.last_create_call(cur, po_id))   # ★ 重下单串成一条链

    # HTTP（不持任何锁）
    try:
        _, r = gateway.call("create_purchase_order", payload)
    except Exception as e:  # noqa: BLE001 —— 驱动不该抛；抛了请求可能已发出，按结果未知走查重，不能留 in_flight
        log.error("op=po_place_driver_exception po_id=%s call=%s err_type=%s cause=%s", po_id, call_id,
                  type(e).__name__, cause_chain(e))
        r = CallResult("unknown", "-", "-", 0, cause=f"driver exception: {cause_chain(e)}")

    # 事务②
    with timed("po_place_settle", po_id=po_id, outcome=r.outcome), pg_conn() as c, c.cursor() as cur:
        repo.close_call(cur, call_id, r)
    if r.outcome == "rejected":
        return {"outcome": "rejected", "po_id": po_id, "lx_code": r.lx_code, "lx_msg": r.lx_msg,
                "erp_call_id": call_id}
    if r.outcome == "not_sent":
        return {"outcome": "not_sent", "po_id": po_id, "cause": r.cause, "erp_call_id": call_id,
                "hint": "请求没有发到领星，可以直接重试"}
    order_sn = extract_order_sn(r.body) if r.outcome == "ok" else None
    if r.outcome == "ok" and order_sn:
        with pg_conn() as c, c.cursor() as cur:
            repo.mark_placed(cur, po_id, order_sn, gateway.mode(), actor, f"place:{call_id}")
            line_ids = repo.source_line_ids(cur, po_id)
            repo.lock_plan_lines(cur, line_ids)
            moved = reevaluate(cur, line_ids, actor, f"po:{po_id}:place")
        log.info("op=po_placed po_id=%s order_sn=%s call=%s moved=%s", po_id, order_sn, call_id, moved)
        _first_check(po_id, order_sn, actor, call_id)
        return {"outcome": "placed", "po_id": po_id, "order_sn": order_sn, "erp_call_id": call_id}
    # 结果未知，或成功却认不出单号 → 查重
    return lookup_and_settle(po_id, actor, call_id, f"lookup_after:{call_id}")


def _first_check(po_id: int, order_sn: str, actor: str, call_id: int) -> None:
    """下单成功后立即读回一次（spec §11 G1 首检）。读不回不影响已下单，但会在 v_check_po_landed 里亮。"""
    cid, r = call_logged("purchase_order_list", {"order_sns": order_sn, "offset": 0, "length": 50},
                          po_id=po_id, actor=actor, retry_of=None)
    rows = [o for o in ((r.body or {}).get("data") or []) if o.get("order_sn") == order_sn]
    if not rows:
        log.warning("op=po_first_check po_id=%s order_sn=%s outcome=%s found=0", po_id, order_sn, r.outcome)
        return
    with pg_conn() as c, c.cursor() as cur:
        read_back(cur, po_id, rows[0], cid)


def recover_stuck(minutes: int | None = None) -> list[dict]:
    """断点续跑（spec §6.3）：in_flight 超过 V5 分钟的不可逆调用 → 按幂等键查重收尾。
    ★ 收尾记在原下单人名下：actor 表里没有「系统」这个人（plan_line_event_actor_fk），也不该编一个。"""
    minutes = minutes if minutes is not None else int(erp_config()["stale_in_flight_minutes"])
    with pg_conn() as c, c.cursor() as cur:
        stuck = repo.stuck_calls(cur, minutes)
    out = []
    for call_id, po_id, actor in stuck:
        with pg_conn() as c, c.cursor() as cur:
            repo.close_call(cur, call_id, CallResult("unknown", "-", "-", 0,
                                                     cause=f"recovered: in_flight > {minutes} min（进程中断）"))
        res = lookup_and_settle(po_id, actor, call_id, f"recover:{call_id}")
        log.warning("op=recover_stuck call=%s po_id=%s result=%s", call_id, po_id, res["outcome"])
        out.append(res)
    return out
