"""组单 · 改草稿 · 删草稿（spec §5）。取 → 判 → 写，事务在这里。"""
from __future__ import annotations

import logging
import time
from decimal import Decimal, InvalidOperation

from repo import purchase as repo
from rules.purchase import NoSellerBasis, confirm_token, po_digest, preflight, seller_caps
from shared.config import erp
from shared.pg_client import pg_conn, timed
from usecase.line_state import reevaluate

log = logging.getLogger("scm.usecase")


class Conflict(Exception):
    def __init__(self, status: int, error: str, hint: str, **fields):
        self.status, self.error, self.hint, self.fields = status, error, hint, fields
        super().__init__(f"{status} {error}: {hint}")


def _normalize(body: dict) -> tuple[dict, list[dict]]:
    missing = [k for k in ("supplier_id", "purchaser_id", "opt_uid", "qc_type") if body.get(k) in (None, "")]
    if missing:
        raise Conflict(400, "missing_required", "单头必填项缺失", missing=missing)
    lines, seen = [], set()
    for ln in body.get("lines") or []:
        key = (ln.get("sku"), ln.get("wid"))
        if key in seen:
            raise Conflict(400, "duplicate_line", "同一张单里同货号同仓只能一行", sku=key[0], wid=key[1])
        seen.add(key)
        try:
            price = Decimal(str(ln.get("price")))
        except (InvalidOperation, TypeError):
            raise Conflict(400, "missing_required", "单价必填且为数字", missing=["lines[].price"],
                           sku=key[0]) from None
        src = [{"line_id": int(s["line_id"]), "seller_id": str(s["seller_id"]), "units": int(s["units"])}
               for s in ln.get("sources") or []]
        units = int(ln.get("units") or 0)
        if units > sum(s["units"] for s in src) and not (ln.get("unplanned_reason") or "").strip():
            raise Conflict(400, "unplanned_reason_required", "超出计划来源的数量必须写理由",
                           sku=key[0], unplanned=units - sum(s["units"] for s in src))
        lines.append({**ln, "price": price, "units": units, "sources": src})
    if not lines:
        raise Conflict(400, "missing_required", "至少一行明细", missing=["lines"])
    return body, lines


def _check_quantities(cur, lines: list[dict], exclude_po: int | None) -> list[int]:
    """Python 侧先判、给出带数字的 409；库层约束（012）兜底。"""
    want: dict[tuple[int, str], int] = {}
    for ln in lines:
        for s in ln["sources"]:
            want[(s["line_id"], s["seller_id"])] = want.get((s["line_id"], s["seller_id"]), 0) + s["units"]
    line_ids = sorted({k[0] for k in want})
    plan_lines = repo.lock_plan_lines(cur, line_ids)
    for lid in line_ids:
        pl = plan_lines.get(lid)
        if pl is None:
            raise Conflict(404, "line_not_found", "没有这条计划单记录", line_id=lid)
        if pl["state"] not in ("已提交", "已确认"):
            raise Conflict(409, "line_not_orderable", "只有 已提交/已确认 的记录能组单",
                           line_id=lid, state=pl["state"])
    used = repo.seller_used(cur, line_ids, exclude_po)
    for lid in line_ids:
        pl = plan_lines[lid]
        try:
            caps = seller_caps(pl["total_units"], pl["demand_by_seller"])
        except NoSellerBasis as e:
            raise Conflict(409, "no_seller_basis", "期望销量为 0 或 unknown 的店无法定分店上限",
                           line_id=lid, sellers=e.sellers) from None
        total_used = sum(v for (l2, _), v in used.items() if l2 == lid)
        total_want = sum(v for (l2, _), v in want.items() if l2 == lid)
        if total_used + total_want > pl["total_units"]:
            raise Conflict(409, "over_total", "超过记录总量", line_id=lid, total=pl["total_units"],
                           used=total_used, requested=total_want)
        for (l2, sid), req in want.items():
            if l2 != lid:
                continue
            cap, u = caps.get(sid, 0), used.get((lid, sid), 0)
            if u + req > cap:
                raise Conflict(409, "over_seller_cap", "超过该店上限（按期望销量占比）",
                               line_id=lid, seller_id=sid, cap=cap, used=u, requested=req)
    return line_ids


def _check_supplier(cur, supplier_id) -> None:
    # 不让外键冲突以 500 冒出：供应商镜像里没有 = 调用方填错，不是我们算错
    if not repo.supplier_exists(cur, supplier_id):
        raise Conflict(400, "unknown_supplier", "没有这个供应商（不在供应商镜像里）",
                       supplier_id=supplier_id)


# 不写「重新核对」：只剩不一致（无待核实）时没有这一步（PB-U12），出口按状态在详情页给
MISMATCH_OPEN_HINT = "领星里有与本单同幂等键、对不上的单未处理；只能在领星改对或作废那张单，出口见本单详情（PB-D16）"


def _assert_editable(cur, po_id: int) -> dict:
    po = repo.lock_po(cur, po_id)
    if po is None:
        raise Conflict(404, "po_not_found", "采购单不存在", po_id=po_id)
    if po["discarded"]:
        raise Conflict(409, "po_discarded", "这张草稿已作废", po_id=po_id)
    if po["state"] != "草稿":
        raise Conflict(409, "po_frozen", "采购单已下单，不可改", po_id=po_id, order_sn=po["order_sn"])
    if repo.in_flight_call(cur, po_id):
        raise Conflict(409, "po_placing", "下单请求进行中", po_id=po_id)
    open_kinds = {a["kind"] for a in repo.open_attention(cur, po_id)}
    if "needs_verify" in open_kinds:
        raise Conflict(409, "needs_verify", "这张单的下单结果待人工核实，先处理", po_id=po_id,
                       timeline=f"/v1/purchase-orders/{po_id}/timeline")
    if "landed_mismatch" in open_kinds:
        # PB-D16：草稿阻塞至领星侧改对（重新核对）或作废回读；改 / 删都会让那张真单失去对照
        raise Conflict(409, "mismatch_open", MISMATCH_OPEN_HINT, po_id=po_id, timeline=f"/v1/purchase-orders/{po_id}/timeline")
    return po


def create_draft(body: dict, actor: str) -> int:
    t0 = time.perf_counter()
    header, lines = _normalize(body)
    with timed("create_po", actor=actor), pg_conn() as c, c.cursor() as cur:
        _check_supplier(cur, header["supplier_id"])
        line_ids = _check_quantities(cur, lines, exclude_po=None)
        po_id = repo.insert_po(cur, header, actor)
        repo.replace_lines(cur, po_id, lines)
        moved = reevaluate(cur, line_ids, actor, f"po:{po_id}:create")
    log.info("op=create_po po_id=%s supplier=%s lines=%d sources=%d lines_moved=%s actor=%s ms=%d",
             po_id, header["supplier_id"], len(lines), sum(len(x["sources"]) for x in lines),
             moved, actor, (time.perf_counter() - t0) * 1000)
    return po_id


def update_draft(po_id: int, body: dict, actor: str) -> int:
    header, lines = _normalize(body)
    with timed("update_po", actor=actor, po_id=po_id), pg_conn() as c, c.cursor() as cur:
        po = _assert_editable(cur, po_id)
        _check_supplier(cur, header["supplier_id"])
        if body.get("draft_version") != po["draft_version"]:
            raise Conflict(409, "stale_draft", "草稿已被别人改过，请刷新后再改", po_id=po_id,
                           current_version=po["draft_version"], got=body.get("draft_version"))
        before = repo.source_line_ids(cur, po_id)
        incoming = {s["line_id"] for ln in lines for s in ln["sources"]}
        # 被换掉的旧来源也要锁：否则并发提交可在 line_qty 与事件插入之间改它的状态
        repo.lock_plan_lines(cur, sorted(set(before) | incoming))
        repo.replace_lines(cur, po_id, [])           # 先清，再按新内容判量（不和自己比）
        line_ids = _check_quantities(cur, lines, exclude_po=po_id)
        version = repo.update_po_header(cur, po_id, header)
        repo.replace_lines(cur, po_id, lines)
        moved = reevaluate(cur, sorted(set(before) | set(line_ids)), actor, f"po:{po_id}:update")
    log.info("op=update_po po_id=%s version=%s lines_moved=%s actor=%s", po_id, version, moved, actor)
    return version


def delete_draft(po_id: int, actor: str) -> None:
    with timed("delete_po", actor=actor, po_id=po_id), pg_conn() as c, c.cursor() as cur:
        _assert_editable(cur, po_id)
        before = repo.source_line_ids(cur, po_id)
        repo.lock_plan_lines(cur, before)
        how = "discard" if repo.has_calls(cur, po_id) else "delete"
        if how == "discard":
            repo.discard_po(cur, po_id, actor)     # 有出站留痕：单头留档，额度释放
        else:
            repo.delete_po(cur, po_id)
        moved = reevaluate(cur, before, actor, f"po:{po_id}:{how}")
    log.info("op=delete_po po_id=%s how=%s lines_moved=%s actor=%s", po_id, how, moved, actor)


def load_po(po_id: int) -> dict:
    with timed("read_po", po_id=po_id), pg_conn() as c, c.cursor() as cur:
        po = repo.read_po(cur, po_id)
        if po is None:
            raise Conflict(404, "po_not_found", "采购单不存在", po_id=po_id)
        po["attention"] = repo.open_attention(cur, po_id)
    content = {k: po[k] for k in ("supplier_id", "purchaser_id", "opt_uid", "qc_type", "wid",
                                  "draft_version")}
    # ★ token 只认单的内容：来源上的计划名 / rev 等是展示用的来源链，改个计划标题不该让确认失效
    content["lines"] = [{**{k: ln[k] for k in ("sku", "wid", "units", "price", "unplanned_reason")},
                         "sources": [{k: s[k] for k in ("line_id", "seller_id", "units")} for s in ln["sources"]]}
                        for ln in po["lines"]]
    po["preflight"] = preflight(po)
    po["confirm_token"] = confirm_token(po_id, po_digest(content), erp()["confirm_salt"])
    return po
