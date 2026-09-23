"""真 CH 取数。★ 阶段 A 没有实现 —— 刻意抛，不给空实现。

空实现会让「该做没做」和「本来就不用做」长得一模一样（01 规则五）。
阶段 B 接上时，这三个方法各自的取数口径在 08 §2.3 与 CLAUDE.md「取数的四条铁律」里。
"""
from __future__ import annotations

import datetime as dt

from dim.source import InTransit

_MSG = ("CH 取数属阶段 B：请按 08 §2.3 实现（商品目录 LEFT JOIN 快照、"
        "msku→货号 按 as_of argMax 但 sid 不参与、日报先按 _captured_date 去重并比对覆盖面）")


class ChSource:
    def as_of(self) -> dt.date:
        raise NotImplementedError(_MSG)

    def monthly_sales_history(self, seller_sku: str, sid: str, months: int
                              ) -> list[tuple[dt.date, int]]:
        raise NotImplementedError(_MSG)

    def onhand_available(self, seller_sku: str, sid: str) -> int | None:
        raise NotImplementedError(_MSG)

    def purchase_in_transit(self, sku: str) -> list[InTransit]:
        raise NotImplementedError(_MSG)


# ---------------------------------------------------------------------------
# 镜像刷新（S-33）：四张阶段 A 镜像的取数 SQL + 纯变换。
# ★ 与上面的 ChSource 是两件事 —— ChSource 是 Source 协议给阶段 B 备的占位，
#   这里是 dim_refresh_run 那条刷新管线用的取数层（design §7.3：客户端由
#   jobs/ 注入，这里只装 SQL 文本 + 纯变换 + 丢弃计数）。
# ---------------------------------------------------------------------------

from collections.abc import Callable
from typing import NamedTuple


class Fetched(NamedTuple):
    rows: list[tuple]
    dropped: int
    drop_reasons: dict[str, int]
    source_max_captured: dt.date | None


class UnknownShape(Exception):
    """认不出的形态 / 空结果。★ 硬失败，不许落进 else '' 再被下游过滤掉。"""


Query = Callable[[str], list[tuple]]

# ★ 列名已由 jobs/probe_ch.py 实测（Task 3 Step 7，design §7.0）。未跑探针就
#   照搬草案 = 在猜——草案本身就有两处对不上（下面两条 SQL 的注释里各记一处）。

# ★ 探针实测：lingxing_product_local_products 没有 local_sku 列，真实列是
#   sku（design §7.0）。local_sku 其实在 lingxing_product_listing，草案在
#   这里抄错了源表。
SQL_SKU_CATALOG = """
SELECT sku,
       argMax(product_name, _captured_date) AS name,
       max(_captured_date)                  AS captured
  FROM jxd_raw.lingxing_product_local_products
 GROUP BY sku
"""

#: ★ 这个 30 天窗口与 `SQL_SELLER` 里 join `lingxing_product_listing` 那一侧
#: **必须是同一个数**（终审 M-9）：两处读的是同一张表、回答的是同一件事
#: （这个店还有没有在卖的 listing）。原先 `SQL_SELLER` 对 `l` 一个
#: `_captured_date` 过滤都没有，用的是全部 58 天历史 —— 今天两个窗口算出的
#: `has_fba` 完全一致（21 个 sid 无一差异，2026-09-22 实测），所以当时没有
#: 实际影响；但一个店停掉全部 FBA listing 超过 30 天之后，两张镜像会对同一
#: 件事给出不同答案，而**没有任何地方记着这两个窗口本该一致**。
_LISTING_WINDOW_DAYS = 30

SQL_MSKU_BRIDGE = f"""
SELECT seller_sku,
       toString(sid)                     AS sid,
       argMax(local_sku, _captured_date) AS sku,
       max(_captured_date)               AS captured
  FROM jxd_raw.lingxing_product_listing
 WHERE _captured_date >= today() - {_LISTING_WINDOW_DAYS}
 GROUP BY seller_sku, sid
"""

SQL_WAREHOUSE = """
SELECT wid,
       argMax(name, _captured_date)     AS name,
       argMax(type, _captured_date)     AS type,
       argMax(sub_type, _captured_date) AS sub_type,
       max(_captured_date)              AS captured
  FROM jxd_raw.lingxing_inventory_warehouses
 GROUP BY wid
"""

#: ★ 控制器 09-22 裁定（探针 §7.0 实测后，覆盖 design §7.2 草案）：
#:   lingxing_seller_list 没有 marketplace 列也没有 platform 列 ——
#:   真实列是 region / country / marketplace_id。
#:   · market  ← 按 sid 取 argMax(country, _captured_date)。
#:   · platform 该表压根没有列可取 —— 它本来就是领星的「亚马逊店铺列表」，
#:     写成 Python 侧常量 'amazon'（见 fetch_seller），不是猜的默认值；
#:     非 Amazon 平台的店归阶段 B。
#:   has_fba 仍是派生列（OQ-3，控制器 09-22）：该 sid 名下只要有一条 listing
#:   的 fulfillment_channel_type = 'FBA' 就算有 FBA。
#: ★ review 09-22 修复：两表的 sid 类型不同——lingxing_seller_list.sid 是
#:   String，lingxing_product_listing.sid 是 Int64（均见 design §7.0）。
#:   裸 `ON l.sid = s.sid` 在真实 CH 上直接报 NO_COMMON_TYPE，`fetch_seller`
#:   fixture 回放测不出来（replay() 无视 SQL 文本）。转成
#:   `toString(l.sid) = s.sid`，不是反过来 `toInt64OrNull(s.sid)`——
#:   PG 侧 seller_id 是 text（001），保持以字符串为准的一侧不做数值解析，
#:   避免 sid 里出现非数字格式时 toInt64OrNull 悄悄给出 NULL 而漏关联。
#: ★ 终审 M-9：join 侧的 `_captured_date` 窗口与 `SQL_MSKU_BRIDGE` 共用
#: `_LISTING_WINDOW_DAYS` —— 两处读同一张表、回答同一件事，窗口不同就会让
#: 两张镜像对「这个店还有没有在卖的 listing」给出不同答案。
#: 过滤写在 `ON` 里而不是 `WHERE` 里：`WHERE` 会把 LEFT JOIN 退化成 INNER
#: JOIN，30 天内一条 listing 都没有的店会**整个消失**，而那正是最该被看见的
#: 状态（同 CLAUDE.md「全集用商品目录 LEFT JOIN 快照」那条铁律）。
SQL_SELLER = f"""
SELECT toString(s.sid)                          AS seller_id,
       argMax(s.name, s._captured_date)         AS name,
       argMax(s.country, s._captured_date)      AS market,
       max(s._captured_date)                    AS captured,
       maxIf(1, l.fulfillment_channel_type = 'FBA') AS has_fba_flag
  FROM jxd_raw.lingxing_seller_list s
  LEFT JOIN jxd_raw.lingxing_product_listing l
         ON toString(l.sid) = s.sid
        AND l._captured_date >= today() - {_LISTING_WINDOW_DAYS}
 GROUP BY s.sid
"""

#: 001:54 的 CHECK 只认四个值。fba 这一档在这张源表里没有（OQ-4 裁定：阶段 A 不产出，
#:   CHECK 保留该值给阶段 C）。
_WAREHOUSE_KIND = {(1, 0): "local", (1, 1): "local",
                   (3, 1): "oversea_self", (3, 2): "oversea_3pl"}


def _nonempty(rows: list[tuple], table: str) -> None:
    if not rows:
        raise UnknownShape(
            f"{table} 取回 0 行。★ 空不是「刷新成功、只是没数据」—— "
            "把采集缺口读成空会让整张镜像被清成不存在")


def _bump(reasons: dict, key: str) -> None:
    reasons[key] = reasons.get(key, 0) + 1


def _coerce_empty(reasons: dict, column: str, value):
    """NULL → `''` 的那一次强制转换，必须留下能被观测到的痕迹。

    ★ 终审 M-11：`name or ""` / `market or ""` 原先悄悄做掉，而
    `seller.market` 在 PG 是 NOT NULL（`001:29`）—— 源里的 NULL `country`
    就这样变成 `''`，正是 OQ-5 为 `warehouse.market` 明确拒绝的那一种
    （「留空是『还没到』，写成 `''` 就再也分不开」）。实测今天一例都没有，
    所以这是潜在缺口不是现行 bug；缺的是**代码里能观测它的地方**。

    ★ 计数不进 `rows_dropped`：这些行没有被丢，`rows_dropped` 必须仍然等于
    真丢弃之和，否则就是拿另一种口径去污染它（同终审 M-1 那条的方向）。
    """
    if value:
        return value
    _bump(reasons, f"coerced_empty_{column}")
    return ""


def fetch_sku_catalog(query: Query) -> Fetched:
    raw = query(SQL_SKU_CATALOG)
    _nonempty(raw, "lingxing_product_local_products")
    rows, reasons = [], {}
    dropped = 0
    for sku, name, _cap in raw:
        if not (sku or "").strip():
            _bump(reasons, "empty_sku")
            dropped += 1
            continue
        rows.append((sku, _coerce_empty(reasons, "name", name)))
    return Fetched(rows, dropped, reasons, max(r[2] for r in raw))


def fetch_msku_bridge(query: Query) -> Fetched:
    raw = query(SQL_MSKU_BRIDGE)
    _nonempty(raw, "lingxing_product_listing")
    rows, reasons = [], {}
    for seller_sku, sid, sku, _cap in raw:
        # ★ 控制器 09-22 追加裁定：amzn.gr.* 是亚马逊虚拟促销组，不是真 listing——
        #   丢弃必须计数（不许用 SQL WHERE 静默滤掉，那一侧就再也数不出来了）。
        if (seller_sku or "").startswith("amzn.gr."):
            reasons["virtual_promo_group"] = reasons.get("virtual_promo_group", 0) + 1
            continue
        if not (sku or "").strip():
            reasons["unbound_sku"] = reasons.get("unbound_sku", 0) + 1
            continue
        rows.append((seller_sku, str(sid), sku))
    return Fetched(rows, sum(reasons.values()), reasons, max(r[3] for r in raw))


def fetch_warehouse(query: Query) -> Fetched:
    raw = query(SQL_WAREHOUSE)
    _nonempty(raw, "lingxing_inventory_warehouses")
    rows, reasons = [], {}
    for wid, name, typ, sub, _cap in raw:
        # ★ 终审 M-11：`int(typ)` 在 type 为 NULL 时抛的是 `TypeError`，而不是
        #   下面那条写得很好的 `UnknownShape` —— 于是「认不出的仓」与「源里这
        #   一列是空的」在留痕里长成两种东西，而后者本该走同一条硬失败的路。
        kind = None if typ is None else _WAREHOUSE_KIND.get((int(typ), int(sub or 0)))
        if kind is None:
            raise UnknownShape(
                f"仓 {wid}「{name}」的 type/sub_type = {typ}/{sub} 认不出。"
                "★ 仓名改了要的是报错，不是悄悄少一批货")
        # ★ market 对国内仓无源（OQ-5 裁定：阶段 A 一律留 NULL，不在这里解析中文
        #   仓名 —— 解析只能在一个地方，是兄弟仓 gen_locations.py 的活，不是这里）。
        #   留 NULL 而不是 ''：空是「还没到」，'' 会被当成「查过了、就是没有」。
        reasons["market_null"] = reasons.get("market_null", 0) + 1
        # ★ OQ-4 裁定：按 kind 记一条计数日志，方便一眼看出这轮刷了多少哪一档的仓。
        reasons[f"kind_{kind}"] = reasons.get(f"kind_{kind}", 0) + 1
        rows.append((int(wid), name, kind, None))
    return Fetched(rows, 0, reasons, max(r[4] for r in raw))


def fetch_seller(query: Query) -> Fetched:
    """★ OQ-3 裁定（控制器 09-22）：has_fba 不是「无源刻意抛」，是派生列——
    SQL_SELLER 已经 join 出 has_fba_flag，这里只做类型收敛。market/platform
    的取法按同日另一条裁定（探针 §7.0 之后）：market ← country；platform
    该源表没有列，写常量 'amazon'。"""
    raw = query(SQL_SELLER)
    _nonempty(raw, "lingxing_seller_list")
    rows, reasons = [], {}
    for seller_id, name, market, _cap, has_fba_flag in raw:
        rows.append((seller_id,
                     _coerce_empty(reasons, "name", name),
                     _coerce_empty(reasons, "market", market),
                     bool(has_fba_flag), "amazon"))
    return Fetched(rows, 0, reasons, max(r[3] for r in raw))
