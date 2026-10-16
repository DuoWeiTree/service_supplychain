"""排货页「现查领星」（CA-D12）：现读一个货号的本地仓库存 + 读回它关联的已下单采购单，落库后网格取较新的。

★ 只读调用，但照样逐次落 `erp_call`（打的谁 · 多久 · 怎么失败的）。
★ 现读失败不退回旧数据冒充新的 —— 调用方拿到 `LiveReadFailed` 自己决定（网格照常、确认 503）。
"""
from __future__ import annotations

import logging
import time

from dim.ch_stock import bucketize
from erp.endpoints import get as get_endpoint
from repo import dispatch as repo
from shared.pg_client import pg_conn, timed
from usecase import landed
from usecase.draft_po import Conflict as PoConflict
from usecase.place_po import call_logged

log = logging.getLogger("scm.usecase")

PAGE = 800          # inventoryDetails length 上限（领星文档 Warehouse/InventoryDetails.md）
MAX_PAGES = 20      # 设计取值 · 未实测：一个货号在全部本地仓的行远少于 800 × 20


class LiveReadFailed(Exception):
    def __init__(self, target: str, cause: dict):
        self.target, self.cause = target, cause
        super().__init__(f"live read failed target={target} cause={cause}")


def read_stock(sku: str, actor: str) -> dict:
    """领星 inventoryDetails 现读一个货号在全部本地仓的可发，按桶落 `stock_read` / `stock_obs`。"""
    t0 = time.perf_counter()
    target = get_endpoint("local_inventory_details").path
    with pg_conn() as c, c.cursor() as cur:
        whs = repo.warehouse_kinds(cur)
        known = set(repo.sellers(cur))
    local = {w for w, v in whs.items() if v["kind"] == "local"}
    rows, last_call = [], None
    for page in range(MAX_PAGES):
        params = {"wid": ",".join(str(w) for w in sorted(local)), "sku": sku, "offset": page * PAGE, "length": PAGE}
        last_call, r = call_logged("local_inventory_details", params, po_id=None, actor=actor, retry_of=None)
        if r.outcome != "ok":
            cause = {"outcome": r.outcome, "http": r.http_status, "lx_code": r.lx_code, "lx_msg": r.lx_msg,
                     "cause": r.cause, "call_id": last_call}
            log.warning("op=live_stock outcome=fail sku=%s target=%s page=%s ms=%d cause=%s", sku, target, page,
                        (time.perf_counter() - t0) * 1000, cause)
            raise LiveReadFailed(target, cause)
        data = (r.body or {}).get("data") or []
        # ★ sku 参数是模糊搜索：只认完全相等的行（别的货号不是这个货号的库存）
        rows += [(d.get("wid"), d.get("sku"), d.get("seller_id"), d.get("product_valid_num"))
                 for d in data if d.get("sku") == sku]
        total = int((r.body or {}).get("total") or 0)
        if len(data) < PAGE or (page + 1) * PAGE >= total:
            break
    else:
        log.warning("op=live_stock outcome=truncated sku=%s pages=%s —— 行数超出设计上限，后面的仓没读到", sku, MAX_PAGES)
    buckets, dropped = bucketize(rows, local, known)
    with timed("live_stock_write", sku=sku), pg_conn() as c, c.cursor() as cur:
        read_id, read_at = repo.insert_stock_read(cur, sku, last_call, buckets, dropped, len(rows), actor)
    log.info("op=live_stock outcome=ok sku=%s target=%s rows=%d warehouses=%d dropped=%s read=%s ms=%d",
             sku, target, len(rows), len(buckets), dropped, read_id, (time.perf_counter() - t0) * 1000)
    return {"read_id": read_id, "read_at": read_at.isoformat(), "dropped": dropped,
            "buckets": [{"wid": wid, "buckets": b} for (_, wid), b in sorted(buckets.items())]}


def refresh(sheet_id: int, body: dict, actor: str) -> dict:
    """「现查领星」：这个货号的本地库存 + 它关联的已下单采购单（采购页同一个读回）。各自成败分开报。"""
    sku = str((body or {}).get("sku") or "").strip()
    if not sku:
        raise PoConflict(400, "missing_required", "要现查哪个货号", missing=["sku"])
    with pg_conn() as c, c.cursor() as cur:
        if repo.read_sheet(cur, sheet_id) is None:
            raise PoConflict(404, "sheet_not_found", "排货表不存在", sheet_id=sheet_id)
        pos = repo.placed_pos_for_sku(cur, sheet_id, sku)
    try:
        stock = read_stock(sku, actor)
    except LiveReadFailed as e:
        stock = {"error": {"target": e.target, **e.cause}}
    results = []
    for po_id, order_sn in pos:
        try:
            res = landed.read_back_now(po_id, actor)
            results.append({"po_id": po_id, "order_sn": order_sn, "outcome": res.get("read_outcome") or "unknown",
                            "cause": res.get("read_cause")})
        except PoConflict as e:
            results.append({"po_id": po_id, "order_sn": order_sn, "outcome": e.error, "cause": e.hint})
    log.info("op=live_refresh sheet=%s sku=%s stock=%s pos=%s actor=%s", sheet_id, sku,
             "error" if "error" in stock else "ok", [(r["order_sn"], r["outcome"]) for r in results], actor)
    return {"sku": sku, "stock": stock, "purchase_orders": results}
