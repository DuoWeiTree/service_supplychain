"""G1：领星落下的 ≡ 我们发的（spec §11）。读回一次 → 核对 → 不符开待处理。"""
from __future__ import annotations

import datetime as dt
import logging

from repo import purchase as repo
from shared.pg_client import pg_conn, timed
from usecase import place_po
from usecase.draft_po import Conflict

log = logging.getLogger("scm.usecase")

_COLS = ("problem", "po_id", "order_sn", "line_no", "sku", "wid", "ours_units", "lx_units", "ours_price", "lx_price")


def read_back_now(po_id: int, actor: str) -> dict:
    """详情页打开时现查领星（只读）：落观察 + 落地核对，回带最新单头状态。
    ★ 读不到时 lx 仍给库里最近一次观察（带 captured_at），页面写明「读回失败、这是何时的状态」。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT state, order_sn FROM po WHERE po_id=%s", (po_id,))
        row = cur.fetchone()
    if row is None:
        raise Conflict(404, "po_not_found", "采购单不存在", po_id=po_id)
    if row[0] != "已下单" or not row[1]:
        raise Conflict(409, "po_not_placed", "还没下单到领星，没有可读回的单", po_id=po_id, state=row[0])
    res = verify(po_id, actor)
    with pg_conn() as c, c.cursor() as cur:
        res["lx"] = repo.latest_snapshot(cur, row[1])
    log.info("op=po_read_back_now po_id=%s order_sn=%s checked=%s read_outcome=%s call=%s status=%s problems=%d",
             po_id, row[1], res["checked"], res["read_outcome"], res["read_back_call"],
             (res["lx"] or {}).get("status"), len(res["problems"]))
    return res


def verify(po_id: int, actor: str) -> dict:
    """★ 查不了 ≠ 没有问题：读不到时 checked=False，不开待处理，也不拿旧观测冒充「已核对」。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT order_sn, placed_at FROM po WHERE po_id=%s AND state='已下单'", (po_id,))
        row = cur.fetchone()
    if row is None:
        return {"checked": False, "read_outcome": None, "read_cause": "po 不是已下单", "read_back_call": None,
                "problems": []}
    order_sn, placed_at = row
    day = (placed_at or dt.datetime.now().astimezone()).astimezone().date()
    cid, r = place_po.call_logged("purchase_order_list", place_po.by_order_sn_params(order_sn, day, day),
                                  po_id=po_id, actor=actor, retry_of=None)
    rows = [o for o in ((r.body or {}).get("data") or []) if o.get("order_sn") == order_sn] \
        if r.outcome == "ok" else []
    if not rows:
        log.warning("op=po_landed_verify po_id=%s order_sn=%s call=%s read_outcome=%s cause=%s checked=0 found=0",
                    po_id, order_sn, cid, r.outcome, r.cause)
        return {"checked": False, "read_outcome": r.outcome, "read_cause": r.cause or "读回成功但没有这个单号",
                "read_back_call": cid, "problems": []}
    with timed("po_landed_verify", po_id=po_id), pg_conn() as c, c.cursor() as cur:
        place_po.read_back(cur, po_id, rows[0], cid, actor)
        cur.execute(f"SELECT {', '.join(_COLS)} FROM v_check_po_landed WHERE po_id=%s", (po_id,))
        problems = [dict(zip(_COLS, x)) for x in cur.fetchall()]
        if problems:
            repo.open_attention_row(cur, po_id, "landed_mismatch", cid,
                                    {"known": "领星里的采购单明细与本单不一致", "unknown": "是谁、为何改的",
                                     "order_sn": order_sn, "read_back_call": cid, "read_outcome": r.outcome,
                                     "problems": problems})
    if problems:
        log.warning("op=po_landed_mismatch po_id=%s order_sn=%s call=%s problems=%s", po_id, order_sn, cid,
                    problems)
    return {"checked": True, "read_outcome": r.outcome, "read_cause": r.cause, "read_back_call": cid,
            "problems": problems}
