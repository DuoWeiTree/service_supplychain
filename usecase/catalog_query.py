"""映射清单（spec §1 ①）：每行一个 (msku, 店铺)，带当前占用计划。

★ 在 usecase 而不在 api/ui/catalog：api/pub 不许 import api/ui（L10，裁定 R3）。
"""
from __future__ import annotations

#: ★ LEFT JOIN 而不是 inner join：对不上镜像的行要点数（dropped），不能被 join 静默吃掉。
#   眼下 msku_bridge 对 seller / sku_catalog 都有外键，dropped 按构造为 0；外键一旦放宽，这里照样有声。
#   占用口径同 claim_holder（活认领、未下单）。
_ROWS = (
    "SELECT b.sku, s.name, b.seller_sku, b.sid, se.name, se.market, se.platform, se.has_fba,"
    "       cl.plan_id, p.title, s.sku IS NOT NULL, se.seller_id IS NOT NULL"
    "  FROM msku_bridge b"
    "  LEFT JOIN sku_catalog s ON s.sku = b.sku"
    "  LEFT JOIN seller se ON se.seller_id = b.sid"
    "  LEFT JOIN msku_claim cl"
    "    ON cl.seller_sku = b.seller_sku AND cl.sid = b.sid"
    "   AND cl.released_at IS NULL AND NOT cl.plan_ordered"
    "  LEFT JOIN plan p ON p.plan_id = cl.plan_id"
)


def partition(rows) -> tuple[list[dict], int]:
    """查询行 → (items, dropped)。dropped = 货号或店铺不在镜像里的行。"""
    items, dropped = [], 0
    for (sku, sku_name, seller_sku, sid, seller_name, market, platform, has_fba,
         plan_id, title, has_sku, has_seller) in rows:
        if not (has_sku and has_seller):
            dropped += 1
            continue
        items.append({"sku": sku, "sku_name": sku_name, "seller_sku": seller_sku, "sid": sid,
                      "seller_name": seller_name, "market": market, "platform": platform,
                      "has_fba": has_fba,
                      "claimed_by": None if plan_id is None else {"plan_id": plan_id, "title": title}})
    return items, dropped


def mappings(cur, *, sku: str | None = None, seller_sku: str | None = None,
             sid: str | None = None, market: str | None = None) -> tuple[list[dict], int]:
    """条件精确匹配、AND。调用方保证至少给了一个条件（不给条件是 need_query，不是全量）。

    ★ 店铺不在镜像的行没有 market 可比 —— 给了 market 时它照样计进 dropped（宁可多报一行
      对不上，也不让它因为「比不了」而消失）。"""
    where, params = [], []
    for col, v in (("b.sku", sku), ("b.seller_sku", seller_sku), ("b.sid", sid)):
        if v is not None:
            where.append(f"{col} = %s")
            params.append(v)
    if market is not None:
        where.append("(se.seller_id IS NULL OR se.market = %s)")
        params.append(market)
    if not where:
        raise ValueError("mappings() 至少要一个条件 —— 不给条件是 need_query，不是全量")
    cur.execute(_ROWS + " WHERE " + " AND ".join(where) + " ORDER BY b.sku, b.seller_sku, b.sid",
                params)
    return partition(cur.fetchall())
