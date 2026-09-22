"""选货号这一步（UC-O2 / P11）。

★ 真实库成百上千个货号，全列出来让人挑从第一天就是错的 ——
  所以不给条件时**故意不返回**，而且这件事要与「查不到」长得不一样。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from api.ui.deps import actor_optional, declared, require_fresh_mirrors
from api.ui.errors import ApiError
from shared.pg_client import pg_conn, timed

router = APIRouter(dependencies=[Depends(require_fresh_mirrors)])

#: ★ 设计取值 · 未实测。文档没给过上限；超出就报 truncated 让人细化条件。
DEFAULT_LIMIT, MAX_LIMIT = 200, 500


@router.get("/catalog/skus")
def catalog_skus(request: Request, who: str | None = Depends(actor_optional)):
    declared(request, "q", "limit")
    q = (request.query_params.get("q") or "").strip()
    try:
        limit = min(int(request.query_params.get("limit", DEFAULT_LIMIT)), MAX_LIMIT)
    except ValueError:
        raise ApiError(400, "bad_limit", "limit 不是整数",
                       {"got": request.query_params.get("limit")}) from None
    if not q:
        # ★ 与「查不到」分得开：need_query=true，前端据此提示输入条件
        return {"need_query": True, "matched": 0, "truncated": False, "limit": limit,
                "items": []}

    like = f"%{q}%"
    with timed("catalog_skus", actor=who, q=q), pg_conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT b.sku, s.name, b.seller_sku, b.sid, se.name,"
            "       cl.plan_id, cl.claimed_by, p.title"
            "  FROM msku_bridge b"
            "  JOIN sku_catalog s ON s.sku = b.sku"
            "  JOIN seller se ON se.seller_id = b.sid"
            "  LEFT JOIN msku_claim cl"
            "    ON cl.seller_sku = b.seller_sku AND cl.sid = b.sid"
            "   AND cl.released_at IS NULL AND NOT cl.plan_ordered"
            "  LEFT JOIN plan p ON p.plan_id = cl.plan_id"
            " WHERE b.sku ILIKE %s OR s.name ILIKE %s OR b.seller_sku ILIKE %s"
            " ORDER BY b.sku, b.seller_sku, b.sid", (like, like, like))
        rows = cur.fetchall()

    by_sku: dict[str, dict] = {}
    holders: dict[str, dict[int, str]] = {}
    for sku, sku_name, seller_sku, sid, seller_name, plan_id, claimed_by, title in rows:
        entry = by_sku.setdefault(sku, {
            "sku": sku, "name": sku_name, "mskus": [],
            # ★ 阶段 A 恒空：「店铺没挂渠道」的渠道码不在 seller 镜像里。
            #   由 test_unbuildable_sellers_is_empty_for_a_stated_reason 盯着列，
            #   渠道码一进表，那条测试转红，逼这里长出真的判据（06 §1.2 点名）。
            "unbuildable_sellers": [],
            "claimed_by": None, "claimed_by_plans": [],
        })
        entry["mskus"].append({
            "seller_sku": seller_sku, "sid": sid, "seller_name": seller_name,
            # ★ 被占用的行留在表里标出来，不过滤（P11）
            "selectable": plan_id is None,
            "claimed_by": None if plan_id is None
            else {"plan_id": plan_id, "title": title, "actor": claimed_by},
        })
        if plan_id is not None:
            holders.setdefault(sku, {})[plan_id] = title

    for sku, entry in by_sku.items():
        held = holders.get(sku, {})
        entry["claimed_by_plans"] = [{"plan_id": pid, "title": t} for pid, t in sorted(held.items())]
        # ★ 货号级只在「占用方唯一」时才给得出一个答案；两张计划各占一部分时
        #   挑一个显示就是编 —— 那时 claimed_by 留 null，名单在 claimed_by_plans 里
        entry["claimed_by"] = entry["claimed_by_plans"][0] if len(held) == 1 else None

    out = list(by_sku.values())
    return {"need_query": False, "matched": len(out), "truncated": len(out) > limit,
            "limit": limit, "items": out[:limit]}


@router.get("/sellers")
def sellers(request: Request, who: str | None = Depends(actor_optional)):
    declared(request)
    with timed("sellers", actor=who), pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT seller_id, name, market, has_fba, platform FROM seller"
                    " ORDER BY seller_id")
        return {"sellers": [{"seller_id": r[0], "name": r[1], "market": r[2],
                             "has_fba": r[3], "platform": r[4]} for r in cur.fetchall()]}
