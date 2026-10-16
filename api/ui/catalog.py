"""选货号这一步（UC-O2 / P11）。

★ 真实库成百上千个货号，全列出来让人挑从第一天就是错的 ——
  所以不给条件时**故意不返回**，而且这件事要与「查不到」长得不一样。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request

from api.ui.deps import actor_optional, declared, require_fresh_mirrors
from api.ui.errors import ApiError
from shared.pg_client import pg_conn, timed

log = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_fresh_mirrors)])

#: ★ 设计取值 · 未实测。文档没给过上限；超出就报 truncated 让人细化条件。
DEFAULT_LIMIT, MAX_LIMIT = 200, 500


#: ★ 设计取值 · 未实测。「常用」列表的默认条数（负责人 2026-10-15 定口径，见 00 S-25b）。
RECENT_LIMIT = 50

#: 搜索与「常用」共用一套列：每行一个 msku，带它当前的占用
_ROWS = (
    "SELECT b.sku, s.name, b.seller_sku, b.sid, se.name,"
    "       cl.plan_id, cl.claimed_by, p.title"
    "  FROM msku_bridge b"
    "  JOIN sku_catalog s ON s.sku = b.sku"
    "  JOIN seller se ON se.seller_id = b.sid"
    "  LEFT JOIN msku_claim cl"
    "    ON cl.seller_sku = b.seller_sku AND cl.sid = b.sid"
    "   AND cl.released_at IS NULL AND NOT cl.plan_ordered"
    "  LEFT JOIN plan p ON p.plan_id = cl.plan_id"
)


def _limit(request: Request, default: int) -> int:
    try:
        return min(int(request.query_params.get("limit", default)), MAX_LIMIT)
    except ValueError:
        raise ApiError(400, "bad_limit", "limit 不是整数",
                       {"got": request.query_params.get("limit")}) from None


def _group(rows) -> dict[str, dict]:
    """msku 行 → 按货号分组，顺序沿用 rows 的顺序。"""
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
    return by_sku


@router.get("/catalog/skus")
def catalog_skus(request: Request, who: str | None = Depends(actor_optional)):
    declared(request, "q", "limit")
    q = (request.query_params.get("q") or "").strip()
    limit = _limit(request, DEFAULT_LIMIT)
    if not q:
        # ★ 与「查不到」分得开：need_query=true，前端据此提示输入条件。
        #   「常用」走 /catalog/skus/recent，不借空 q —— 否则「没给条件」又和一张列表长得一样
        return {"need_query": True, "matched": 0, "truncated": False, "limit": limit,
                "items": []}

    like = f"%{q}%"
    with timed("catalog_skus", actor=who, q=q), pg_conn() as c, c.cursor() as cur:
        cur.execute(
            _ROWS +
            " WHERE b.sku ILIKE %s OR s.name ILIKE %s OR b.seller_sku ILIKE %s"
            " ORDER BY b.sku, b.seller_sku, b.sid", (like, like, like))
        rows = cur.fetchall()

    out = list(_group(rows).values())
    return {"need_query": False, "matched": len(out), "truncated": len(out) > limit,
            "limit": limit, "items": out[:limit]}


@router.get("/catalog/skus/recent")
def catalog_skus_recent(request: Request, who: str | None = Depends(actor_optional)):
    """以前计划里用过的货号：任一计划认领过（含已释放），按最近一次认领倒序。

    ★ 按全体计划算、不按操作人：操作人目前是临时测试用户（RBAC 后期接入）。
    """
    declared(request, "limit")
    limit = _limit(request, RECENT_LIMIT)
    with timed("catalog_skus_recent", actor=who, limit=limit), pg_conn() as c, c.cursor() as cur:
        # ★ 对不上镜像的认领记录（msku 已从 msku_bridge 消失）要点数，不能被 join 静默吃掉
        cur.execute(
            "SELECT count(*) FROM msku_claim cl"
            " WHERE NOT EXISTS (SELECT 1 FROM msku_bridge b"
            "                    WHERE b.seller_sku = cl.seller_sku AND b.sid = cl.sid)")
        unmapped = cur.fetchone()[0]
        cur.execute(
            "SELECT b.sku, max(cl.claimed_at) AS last"
            "  FROM msku_claim cl"
            "  JOIN msku_bridge b ON b.seller_sku = cl.seller_sku AND b.sid = cl.sid"
            " GROUP BY b.sku ORDER BY last DESC, b.sku LIMIT %s", (limit + 1,))
        ranked = cur.fetchall()
        truncated = len(ranked) > limit
        ranked = ranked[:limit]
        rows = []
        if ranked:
            cur.execute(_ROWS + " WHERE b.sku = ANY(%s) ORDER BY b.sku, b.seller_sku, b.sid",
                        ([sku for sku, _ in ranked],))
            rows = cur.fetchall()

    grouped = _group(rows)
    items = []
    for sku, last in ranked:
        # ★ 货号不在 sku_catalog / 店铺不在 seller 时 _ROWS 的 inner join 会吃掉它 —— 点名告警
        entry = grouped.get(sku)
        if entry is None:
            log.warning("catalog_skus_recent: 货号 %s 认领过但不在 sku_catalog/seller 镜像里", sku)
            continue
        items.append({**entry, "last_claimed_at": last.isoformat()})
    log.info("catalog_skus_recent skus=%d listed=%d truncated=%s unmapped_claims=%d",
             len(ranked), len(items), truncated, unmapped)
    return {"items": items, "limit": limit, "truncated": truncated,
            "unmapped_claims": unmapped}


@router.get("/sellers")
def sellers(request: Request, who: str | None = Depends(actor_optional)):
    declared(request)
    with timed("sellers", actor=who), pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT seller_id, name, market, has_fba, platform FROM seller"
                    " ORDER BY seller_id")
        return {"sellers": [{"seller_id": r[0], "name": r[1], "market": r[2],
                             "has_fba": r[3], "platform": r[4]} for r in cur.fetchall()]}
