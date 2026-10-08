"""G1：领星落下的 ≡ 我们发的（spec §11）。读回一次 → 核对 → 不符开待处理。"""
from __future__ import annotations

import logging

from repo import purchase as repo
from shared.pg_client import pg_conn, timed
from usecase import place_po

log = logging.getLogger("scm.usecase")

_COLS = ("problem", "po_id", "order_sn", "line_no", "sku", "wid", "ours_units", "lx_units", "ours_price", "lx_price")


def verify(po_id: int, actor: str) -> list[dict]:
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT order_sn FROM po WHERE po_id=%s AND state='已下单'", (po_id,))
        row = cur.fetchone()
    if row is None:
        return []
    order_sn = row[0]
    cid, r = place_po.call_logged("purchase_order_list", {"order_sns": order_sn, "offset": 0, "length": 50},
                                  po_id=po_id, actor=actor, retry_of=None)
    rows = [o for o in ((r.body or {}).get("data") or []) if o.get("order_sn") == order_sn]
    if not rows:
        # 读不到不等于核对通过：留痕，视图仍按已有观测判
        log.warning("op=po_landed_verify po_id=%s order_sn=%s call=%s outcome=%s found=0", po_id, order_sn, cid,
                    r.outcome)
    with timed("po_landed_verify", po_id=po_id), pg_conn() as c, c.cursor() as cur:
        if rows:
            place_po.read_back(cur, po_id, rows[0], cid)
        cur.execute(f"SELECT {', '.join(_COLS)} FROM v_check_po_landed WHERE po_id=%s", (po_id,))
        problems = [dict(zip(_COLS, x)) for x in cur.fetchall()]
        if problems:
            repo.open_attention_row(cur, po_id, "landed_mismatch", cid,
                                    {"known": "领星里的采购单明细与本单不一致", "unknown": "是谁、为何改的",
                                     "order_sn": order_sn, "read_back_call": cid, "problems": problems})
    if problems:
        log.warning("op=po_landed_mismatch po_id=%s order_sn=%s call=%s problems=%s", po_id, order_sn, cid,
                    problems)
    return problems
