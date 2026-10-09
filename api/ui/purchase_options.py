"""组单表单的候选项：本服务已建的单 + 领星历史采购单（spec §2）。"""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, Request

from api.ui.deps import actor_optional, declared
from api.ui.source_factory import lazy_ch_query
from dim.ch_source import fetch_purchase_actors
from shared.ch_client import describe_failure
from shared.pg_client import pg_conn, timed

router = APIRouter()
log = logging.getLogger("scm.api")

LOCAL, HISTORY = "本服务", "领星历史"


def history_query_dep():
    return lazy_ch_query()


def _history(query) -> tuple[dict, dict]:
    """CH 不可用不拖垮接口：历史只是候选来源之一，缺了表单仍可用本服务的候选。"""
    t0 = time.perf_counter()
    try:
        got = fetch_purchase_actors(query)
    except Exception as e:  # noqa: BLE001 —— 任何取数失败都降级，不 503
        f = describe_failure(e)
        log.warning("op=purchase_options_history target=jxd_raw.lingxing_purchase_order_list "
                    "elapsed_ms=%d outcome=fail kind=%s code=%s causes=%s msg=%.200s",
                    (time.perf_counter() - t0) * 1000, f["kind"], f["code"], f["causes"], f["msg"])
        return ({"available": False, "cause": f"{f['kind']}: {f['type']} ({f['causes']})",
                 "as_of": None, "dropped": 0}, {"purchasers": [], "opt_uids": []})
    log.info("op=purchase_options_history target=jxd_raw.lingxing_purchase_order_list elapsed_ms=%d outcome=ok "
             "purchasers=%d opt_uids=%d dropped=%d drop_reasons=%s",
             (time.perf_counter() - t0) * 1000, len(got.purchasers), len(got.opt_uids),
             got.dropped, got.drop_reasons)
    return ({"available": True, "cause": None,
             "as_of": got.as_of.isoformat() if got.as_of else None,
             "dropped": got.dropped},
            {"purchasers": got.purchasers, "opt_uids": got.opt_uids})


@router.get("/purchase-options")
def options(request: Request, who: str | None = Depends(actor_optional),
            query=Depends(history_query_dep)):  # noqa: B008
    declared(request)
    with timed("purchase_options", actor=who), pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT supplier_id, name FROM supplier ORDER BY supplier_id")
        suppliers = [{"supplier_id": a, "name": b} for a, b in cur.fetchall()]
        cur.execute("SELECT wid, name FROM warehouse WHERE kind = 'local' ORDER BY wid")
        warehouses = [{"wid": a, "name": b} for a, b in cur.fetchall()]
        cur.execute("SELECT DISTINCT purchaser_id FROM po ORDER BY 1")
        local_p = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT DISTINCT opt_uid FROM po ORDER BY 1")
        local_o = [r[0] for r in cur.fetchall()]
    history, h = _history(query)

    purchasers = {p: {"purchaser_id": p, "source": LOCAL, "count": None} for p in local_p}
    for p, n in h["purchasers"]:
        purchasers.setdefault(p, {"purchaser_id": p, "source": HISTORY, "count": n})
        if p in local_p:
            purchasers[p]["count"] = n
    opt_uids = {u: {"opt_uid": u, "name": None, "source": LOCAL, "count": None} for u in local_o}
    for u, name, n in h["opt_uids"]:
        e = opt_uids.setdefault(u, {"opt_uid": u, "name": name, "source": HISTORY, "count": n})
        if u in local_o and e["count"] is None:
            e.update(name=name, count=n)
    return {"suppliers": suppliers, "warehouses": warehouses,
            "purchasers": list(purchasers.values()), "opt_uids": list(opt_uids.values()),
            "history": history}
