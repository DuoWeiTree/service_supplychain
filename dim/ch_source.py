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

SQL_MSKU_BRIDGE = """
SELECT seller_sku,
       toString(sid)                     AS sid,
       argMax(local_sku, _captured_date) AS sku,
       max(_captured_date)               AS captured
  FROM jxd_raw.lingxing_product_listing
 WHERE _captured_date >= today() - 30
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
SQL_SELLER = """
SELECT toString(s.sid)                          AS seller_id,
       argMax(s.name, s._captured_date)         AS name,
       argMax(s.country, s._captured_date)      AS market,
       max(s._captured_date)                    AS captured,
       maxIf(1, l.fulfillment_channel_type = 'FBA') AS has_fba_flag
  FROM jxd_raw.lingxing_seller_list s
  LEFT JOIN jxd_raw.lingxing_product_listing l ON toString(l.sid) = s.sid
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


def fetch_sku_catalog(query: Query) -> Fetched:
    raw = query(SQL_SKU_CATALOG)
    _nonempty(raw, "lingxing_product_local_products")
    rows, reasons = [], {}
    for sku, name, _cap in raw:
        if not (sku or "").strip():
            reasons["empty_sku"] = reasons.get("empty_sku", 0) + 1
            continue
        rows.append((sku, name or ""))
    return Fetched(rows, sum(reasons.values()), reasons, max(r[2] for r in raw))


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
        kind = _WAREHOUSE_KIND.get((int(typ), int(sub or 0)))
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
    rows = []
    for seller_id, name, market, _cap, has_fba_flag in raw:
        rows.append((seller_id, name or "", market or "", bool(has_fba_flag), "amazon"))
    return Fetched(rows, 0, {}, max(r[3] for r in raw))
