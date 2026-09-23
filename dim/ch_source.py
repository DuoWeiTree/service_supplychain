"""真 CH 取数。★ 预测取数（`as_of` / `onhand_available` / `purchase_in_transit` /
`monthly_sales_history`）在 2026-09-23 ChSource 设计里全部接了真 CH——四个协议
方法（`dim/source.py`）没有一个还留着占位实现（01 规则五：空实现会让「该做没做」
和「本来就不用做」长得一模一样）。取数口径在
`docs/superpowers/specs/2026-09-23-chsource-design.md` 与 CLAUDE.md「取数的四条铁律」里；
`ChSource` 类的完整定义在本文件末尾「预测取数」段（与下面的镜像刷新取数层是两件事，
见该段落开头的说明）。
"""
from __future__ import annotations

import datetime as dt
import logging
import time

log = logging.getLogger("scm.dim")


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


#: ★ 09-23 裁定（负责人真实数据下撞见的第二个缺陷）：`lingxing_seller_list.country`
#:   给的是中文国名，不是代码 —— 2026-09-23 实测（21 个 sid 全量，按 sid 去重）
#:   共 15 个不同值。而 `migrations/pg/001_foundation.sql:27` 的 `seller.market`
#:   注释写的是 "US / UK / DE …"，阶段 C（排货/FBA 站点）要按代码匹配，中文名
#:   直接落库会让下游连不上。解析只能在一个地方（这里），且认不出的国名必须
#:   硬失败 —— 不许落进 `else ''` 再被下游过滤掉（同 CLAUDE.md「中文仓名解析
#:   集中在一处 + 未匹配必须硬失败」那条铁律，这里落到 market 代码这一处）。
_COUNTRY_TO_MARKET = {
    "美国": "US", "加拿大": "CA", "日本": "JP", "德国": "DE", "英国": "UK",
    "爱尔兰": "IE", "墨西哥": "MX", "西班牙": "ES", "意大利": "IT",
    "瑞典": "SE", "波兰": "PL", "巴西": "BR", "比利时": "BE",
    "荷兰": "NL", "法国": "FR",
}


def _market_code(country: str) -> str:
    """把 `country` 的中文国名译成 001:27 约定的代码。

    ★ 只在这里判空是不对的 —— 空值已经由调用方的 `_coerce_empty` 处理过
    （留痕 `coerced_empty_market`，OQ-5 那条「留空是还没到」的规矩）；这里收到
    的 `""` 直接放行，不当成「认不出的国家」再报一次错。真正非空但不在地图里
    的国名才是「认不出的形态」，必须硬失败并点名，不许悄悄落成 `''`。
    """
    if not country:
        return ""
    code = _COUNTRY_TO_MARKET.get(country)
    if code is None:
        raise UnknownShape(
            f"country={country!r} 认不出是哪个市场代码。"
            "★ 新国家要在 dim/ch_source.py 的 _COUNTRY_TO_MARKET 里显式加，"
            "不许落进 else '' 再被下游过滤掉")
    return code


def fetch_seller(query: Query) -> Fetched:
    """★ OQ-3 裁定（控制器 09-22）：has_fba 不是「无源刻意抛」，是派生列——
    SQL_SELLER 已经 join 出 has_fba_flag，这里只做类型收敛。market/platform
    的取法按同日另一条裁定（探针 §7.0 之后）：market ← country；platform
    该源表没有列，写常量 'amazon'。

    ★ 09-23 追加：country 是中文国名，必须经 `_market_code` 译成代码 ——
    见上面 `_COUNTRY_TO_MARKET` 的注释。"""
    raw = query(SQL_SELLER)
    _nonempty(raw, "lingxing_seller_list")
    rows, reasons = [], {}
    for seller_id, name, market, _cap, has_fba_flag in raw:
        rows.append((seller_id,
                     _coerce_empty(reasons, "name", name),
                     _market_code(_coerce_empty(reasons, "market", market)),
                     bool(has_fba_flag), "amazon"))
    return Fetched(rows, 0, reasons, max(r[3] for r in raw))


# ---------------------------------------------------------------------------
# 预测取数（design 2026-09-23）。★ 与上面的镜像刷新是两件事：镜像是慢变维度，
#   这里是带时点的量 —— 每个数都要能回答「它是关于什么的」。
# ---------------------------------------------------------------------------

from dim import order_store_map  # ★ 只有 monthly_sales_history 需要它
from dim.source import InTransit  # ★ 只有本段的 ChSource 需要它

#: ★ ChUnavailable 的 target 与 SQL 的 FROM 共用同一个字面量——写两遍会有
#:   一天悄悄对不上（改了表名却只改了其中一处）。
FBA_DETAIL_TABLE = "jxd_raw.lingxing_inventory_fba_detail"

#: 候选快照日。★ 只问 fba_detail —— as_of 标注的是**在仓数**，它的时点就是这张表的。
#: ★ Fix round 1（团队负责人裁定）：「已写完」不再拿 Python 端的 wall clock 去比
#:   CH 的 `_captured_at`——两台机器的时钟不是同一个时钟，naive datetime 相减
#:   看起来能跑，实际比的是两个可能不同时区的「本地时间」，静默算错还不报错
#:   （这正是本仓 CLAUDE.md 明令禁止的「静默兜底」）。改成让 CH 用它自己的
#:   `now()` 就地算好 `settled`，随行一起回来，Python 侧只回放这个布尔值。
SQL_FBA_CAPTURE_DAYS = f"""
SELECT _captured_date, count() AS rows, uniq(sid) AS uniq_sid,
       now() - INTERVAL {{settle}} MINUTE > max(_captured_at) AS settled
  FROM {FBA_DETAIL_TABLE}
 WHERE _captured_date >= today() - {{lookback}}
 GROUP BY _captured_date
 ORDER BY _captured_date DESC
"""

#: ★ 实测：该表当天 06:34~07:39 起跑、约 60 秒跑完（design §1.3 E-3），而刷新调度
#:   就排在 06:30 —— 半写窗口是真实存在的。30 分钟 = 实测时长的 30 倍。
#: ★ Fix round 2：这两个数是 `ChSource.__init__` 的默认值，真正生效的是
#:   构造参数 `lookback_days`/`settle_minutes`——它们与 `[forecast]` 的
#:   `snapshot_lookback_days`/`snapshot_settle_minutes` 一一对应，装配层
#:   （Task 5）把配置值传进来。以前这俩配置键声明了但没人读，改了配置文件
#:   毫无效果——一个不生效的旋钮比没有旋钮更坏。
SETTLE_MINUTES = 30
LOOKBACK_DAYS = 7


# ---------------------------------------------------------------------------
# monthly_sales_history（design §4.2，controller 裁定 09-23，§8 OQ-1）
# ★ 三个口径决定，每一个都是实测踩出来的：
#   ① 按 (amazon_order_id, order_item_id) 去重，取 argMax(_captured_date) 的那一版。
#      实测 8,980 组出现两次，跨采集日状态会变（Pending → Shipped）。不去重时
#      2026-08 = 13,611 件、去重后 11,906（−12.5%），2026-09 = 11,331 → 9,130（−19.4%）。
#      去重后与独立源 lingxing_multiplatform_sales_stats 对上（2026-06 差 0.06%）。
#      ★ 兄弟仓的 [demand_history] 没有这一步 —— 这是本仓实测追加的。
#   ② 排除 Cancelled / Pending（沿用兄弟仓口径）。实测 Pending 在已完结的月份
#      残留很少（2026-07 为 165/25,772）。
#   ③ 只统计一个 (store, sales_channel) —— sid 由 dim/order_store_map.py 声明，
#      不在 SQL 里解析店名。
# ---------------------------------------------------------------------------

#: ★ ChUnavailable 的 target 与 SQL 的 FROM 共用同一个字面量——写两遍会有
#:   一天悄悄对不上（同 FBA_DETAIL_TABLE / PURCHASE_ITEMS_TABLE 那条理由）。
ORDERS_TABLE = "jxd_raw.amazon_sp_api_report_all_orders"

SQL_MONTHLY_SALES = f"""
SELECT toStartOfMonth(d) AS month, toInt64(sum(units)) AS units
  FROM (
    SELECT toDate(any(purchase_date)) AS d,
           if(argMax(order_status, _captured_date) NOT IN ('Cancelled', 'Pending'),
              argMax(quantity, _captured_date), 0) AS units
      FROM {ORDERS_TABLE}
     WHERE store = '{{store}}' AND sales_channel = '{{channel}}' AND sku = '{{seller_sku}}'
       AND toDate(purchase_date) >= toDate('{{start}}')
       AND toDate(purchase_date) < toDate('{{end}}')
     GROUP BY amazon_order_id, order_item_id
  )
 GROUP BY month
 ORDER BY month
"""


def month_window(as_of: dt.date, months: int) -> list[dt.date]:
    """最近 `months` 个**完整**自然月，升序。★ 当月绝不入选 ——
    实测 2026-09 只有 9,130 件 vs 8 月 11,906 件，混进去会被读成 23% 的下滑。"""
    if not isinstance(months, int) or isinstance(months, bool) or months < 1:
        raise ValueError(f"months 必须是正整数，收到 {months!r}")
    out: list[dt.date] = []
    y, m = as_of.year, as_of.month
    for _ in range(months):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        out.append(dt.date(y, m, 1))
    return sorted(out)


def fill_months(window: list[dt.date],
                hits: dict[dt.date, int]) -> tuple[list[tuple[dt.date, int]], int]:
    """窗口内空月补 0 并计数。★ 一条命中都没有 → 返回 `[]`（协议：不补 0）——
    「新品没数据」与「卖了 0 件」必须分得开（forecast/estimate.py:22）。"""
    if not hits:
        return [], 0
    series = [(m, int(hits.get(m, 0))) for m in window]
    return series, sum(1 for m in window if m not in hits)


def _next_month(d: dt.date) -> dt.date:
    return dt.date(d.year + d.month // 12, d.month % 12 + 1, 1)


#: ★ 列裁定（design §4.3 / OQ-4 裁定）：`afn_fulfillable_quantity` = 可售在仓。
#:   不用 `available_total`（实测 972/8,080 行更大，含预留与不可售，OQ-4：差额
#:   不解释就不用它，禁止总量相减凑数）；不用 `total`（含 inbound，inbound 在
#:   grid 里是另一列）。
#: ★ 不按 `sku` 关联：该列实测 1,932/8,080（23.9%）为空。
#: ★ `sid` 与 `seller_sku` 都出，且 sid 转 String —— PG 侧 seller_id 是 text（001），
#:   以字符串为准的一侧不做数值解析（同 `SQL_SELLER` 的 `toString(l.sid)` 那条理由）。
#: ★ `sid != 0` 的排除**不写进 SQL**，留在 Python 侧的 `parse_onhand` 做——写进
#:   `WHERE` 会让 CH 直接吞掉这批行，Python 端就再也数不出「排除了多少」，
#:   而 27.6% 的可售被排除这件事必须能被计数（controller 09-23 裁定）。
SQL_FBA_ONHAND = f"""
SELECT toString(sid)                              AS sid,
       seller_sku                                 AS seller_sku,
       toInt64(sum(afn_fulfillable_quantity))     AS units,
       count()                                    AS raw_rows
  FROM {FBA_DETAIL_TABLE}
 WHERE _captured_date = toDate('{{as_of}}')
 GROUP BY sid, seller_sku
"""

#: ★ sid=0 是欧洲共享池（PL+SE 合池），不是任何单店的在仓（design §1.3 E-4：
#:   name='PETSFIT-JXD-UK欧洲仓'，seller_group_name='PETSFIT-JXD-PL,PETSFIT-JXD-SE'，
#:   1,114 行 / 14,073 件，占全部可售 27.6%）。折进任何一个 sid 都是凭空给它
#:   14,073 件。归属未裁定（OQ-2）——阶段 A 只排除 + 计数，不猜分摊规则。
SHARED_POOL_SID = "0"


def parse_onhand(rows: list[tuple]) -> tuple[dict[tuple[str, str], int], dict]:
    """纯变换：CH 行 → `(sid, seller_sku) -> units` + 丢弃计数。

    ★ 两种「重复」都记进同一个 `rows_collapsed`，来源不同：
      1. SQL 自己的 `GROUP BY` 内，单个分组由多条原始行 `sum()` 出来
         （`raw_rows > 1`，CLAUDE.md 铁律 4「库存日报会重复行」）；
      2. Python 侧同一个 `(sid, seller_sku)` 键出现在**两条不同的返回行**里——
         这在正常的 `GROUP BY sid, seller_sku` 输出里不该发生，出现了就是
         SQL 或源表形态变了，拼接也要能被看见，不是覆盖掉旧值完事。
    """
    out: dict[tuple[str, str], int] = {}
    dropped: dict = {}
    for sid, seller_sku, units, raw_rows in rows:
        sid = str(sid)
        units = int(units)
        raw_rows = int(raw_rows)
        if sid == SHARED_POOL_SID:
            bucket = dropped.setdefault("shared_pool_excluded", {"rows": 0, "units": 0})
            bucket["rows"] += raw_rows
            bucket["units"] += units
            continue
        if raw_rows > 1:
            dropped["rows_collapsed"] = dropped.get("rows_collapsed", 0) + raw_rows - 1
        key = (sid, seller_sku)
        if key in out:
            dropped["rows_collapsed"] = dropped.get("rows_collapsed", 0) + 1
        out[key] = out.get(key, 0) + units
    return out, dropped


# ---------------------------------------------------------------------------
# purchase_in_transit（design §4.4，controller 裁定 09-23，OQ-5/OQ-6）
# ★ 采购表是覆盖型、没有历史快照（docs/01-架构设计.md:94「CH 的三种病」②），
#   且实测比 fba_detail 晚一天（2026-09-22 vs 09-23，E-14）。所以它用自己的
#   `max(_captured_date)` 作快照日：强行对齐 fba_detail 的 as_of 会在采购表
#   没采完的那天返回空，而「空」与「真的没有在途」长得一模一样。
# ---------------------------------------------------------------------------

#: ★ ChUnavailable 的 target 与两条 SQL 的 FROM 共用同一个字面量——写两遍会
#:   有一天悄悄对不上（同 FBA_DETAIL_TABLE 那条理由）。
PURCHASE_ITEMS_TABLE = "jxd_raw.lingxing_purchase_order_list_items"
PURCHASE_ORDER_TABLE = "jxd_raw.lingxing_purchase_order_list"

SQL_PURCHASE_AS_OF = f"""
SELECT max(_captured_date) FROM {PURCHASE_ITEMS_TABLE}
"""

#: ★★ 必须按采购单 `status` 过滤，不能只靠 `quantity_real > quantity_receive`。
#:    实测（design §1.3 E-12）：status=9（已完成）的 966 行 / 104,479 件，行项
#:    `quantity_receive` **全为 0** —— 只按算术会把这批已到货的货报成在途，
#:    是真实开口量 28,561 件的 3.7 倍。status=-1（已作废）468 行 / 39,375 件
#:    同样显式排除，不依赖「它恰好净为 0」。
#: ★ `expect_arrive_time` 为 NULL 的行（E-14，1 行 / 50 件）不在 SQL 里过滤掉——
#:   过滤会让它悄悄消失，必须留给 `parse_in_transit` 数出来再丢弃（判据五）。
SQL_PURCHASE_IN_TRANSIT = f"""
WITH o AS (
    SELECT order_sn, argMax(status, _captured_date) AS st
      FROM {PURCHASE_ORDER_TABLE}
     GROUP BY order_sn
)
SELECT i.sku                                                  AS sku,
       if(i.expect_arrive_time IS NULL, NULL,
          formatDateTime(i.expect_arrive_time, '%Y-%m'))      AS period,
       toInt64(sum(i.quantity_real - i.quantity_receive))     AS units,
       i.order_sn                                             AS ref
  FROM {PURCHASE_ITEMS_TABLE} AS i
 INNER JOIN o ON o.order_sn = i.order_sn
 WHERE i._captured_date = toDate('{{purchase_as_of}}')
   AND o.st = 2
   AND ifNull(i.is_delete, 0) = 0
   AND i.quantity_real > i.quantity_receive
 GROUP BY sku, period, ref
 ORDER BY sku, period, ref
"""


#: ★ WARNING 日志里最多展开几个 order_sn——丢弃行一多，日志行本身不能被撑爆。
#:   `stats()` 里的 `missing_eta_refs` 不受这个上限限制，截断只发生在日志文本。
_MAX_REFS_IN_LOG = 20


def _format_refs(refs: list[str]) -> str:
    """★ 点名 order_sn，多了就截断加「+N more」尾巴——同一个理由：日志要能读，
    不是要把全部丢弃行原样倒出来。"""
    shown = refs[:_MAX_REFS_IN_LOG]
    tail = f", +{len(refs) - _MAX_REFS_IN_LOG} more" if len(refs) > _MAX_REFS_IN_LOG else ""
    return f"[{', '.join(shown)}{tail}]"


def parse_in_transit(rows: list[tuple]) -> tuple[dict[str, list[InTransit]], dict]:
    """纯变换：CH 行 → `sku -> [InTransit]` + 丢弃计数。

    ★ `period` 为 NULL 的行（`expect_arrive_time` 缺失，E-14）不许落进
    `else ''` 再被下游过滤掉——丢弃必须点名 **order_sn 与件数**（design §4.4），
    不能只留一个计数：只知道「丢了几行」，下次复现还得手工连一次 CH 才查得出
    是哪张单少了到货日，这正是这条规则要省掉的成本（review fix round 1）。
    """
    out: dict[str, list[InTransit]] = {}
    dropped: dict = {}
    for sku, period, units, ref in rows:
        units = int(units)
        if not period:
            dropped["missing_eta_rows"] = dropped.get("missing_eta_rows", 0) + 1
            dropped["missing_eta_units"] = dropped.get("missing_eta_units", 0) + units
            dropped.setdefault("missing_eta_refs", []).append(ref)
            continue
        out.setdefault(sku, []).append(InTransit(period, units, ref))
    return out, dropped


class PurchaseTableStale(Exception):
    """采购表陈旧阈值被突破（OQ-5）。★ 拿不到新鲜数据时必须让调用方读成「未知」，
    绝不能悄悄当「在途为 0」或返回上一轮缓存的数字——同 ChDataUnusable 一个理由。
    由 Task 5 翻成 503。"""

    def __init__(self, captured: dt.date, age_days: int, threshold_days: int) -> None:
        self.captured, self.age_days, self.threshold_days = captured, age_days, threshold_days
        super().__init__(
            f"purchase_table_stale captured={captured} age_days={age_days} "
            f"threshold_days={threshold_days}")


def is_overdue(period: str, as_of: dt.date) -> bool:
    """纯函数（OQ-6）：`period`（`YYYY-MM`）早于 `as_of` 所在月即逾期。

    ★ 不重新定日期——猜一个新日期就是发明数据。归桶标记只在装配层
    （`api/ui/plans.py`，Task 5）现算，`InTransit` 本身不加字段。
    """
    year_s, month_s = period.split("-")
    year, month = int(year_s), int(month_s)
    return (year, month) < (as_of.year, as_of.month)


class ChUnavailable(Exception):
    """CH 连不上/超时。★ 带上 describe_failure 的分类：「超时」与「连不上」处置相反。"""

    def __init__(self, target: str, cause: dict) -> None:
        self.target, self.cause = target, cause
        super().__init__(f"ch_unavailable target={target} cause={cause}")


def _unclassified_failure(e: BaseException) -> dict:
    """★ `classify_failure` 的默认值——`dim/` 不许 import `shared.ch_client`
    （`tests/test_layering.py::test_pure_layers_cannot_reach_a_connection`：
    哪怕只 `from shared.ch_client import describe_failure`，`full_imports()`
    记的是整条 `node.module`，一样会命中 `NO_CONNECTIONS` 而红），所以这里
    没法直接调用那边按 cause 链分辨「超时」与「连不上」的真分类器。

    与 `query`/`now` 同一个模式：真正的分类逻辑由调用方注入——生产装配层
    （`api/ui/source_factory.py`，Task 5）在 `api/` 层，允许 import
    `shared.ch_client`，把 `describe_failure` 本身（签名恰好就是
    `Callable[[BaseException], dict]`）传进来作为 `classify_failure`。
    这个默认值只是离线可跑的占位，`kind` 恒为 `"unknown"`——不许把它误读成
    「已经分类过、就是分不出类型」，那两者是两回事。"""
    return {"kind": "unknown", "type": type(e).__name__, "msg": str(e)}


class ChDataUnusable(Exception):
    """候选日全被拒。★ 带上每一天的数字 —— 「不可用」不说明是哪一种不可用就没法查。"""

    def __init__(self, rejected: list[dict]) -> None:
        self.rejected = rejected
        super().__init__(f"ch_data_unusable rejected={rejected}")


def _default_now() -> dt.datetime:
    """★ 只用于 `ChSource` 的 TTL 缓存自比（同一个 clock 前后两次读数相减），
    不再用于任何跨机器比较——那条已经随「已写完」判定一起搬进 CH 自己的
    `now()` 了（见 `SQL_FBA_CAPTURE_DAYS`）。自比安全：不管系统时区是什么，
    同一个 `dt.datetime.now()` 前后两次调用的差值就是真实流逝的时间。"""
    return dt.datetime.now()  # noqa: DTZ005


def pick_snapshot_date(rows: list[tuple], threshold: float,
                       min_rows: int, min_distinct_sid: int, *,
                       lookback_days: int = LOOKBACK_DAYS
                       ) -> tuple[dt.date, list[dict]]:
    """从新到旧挑第一个「已采完 + 没掉档」的采集日。返回 (日期, 被拒清单)。

    ★ 被拒清单不是可选的返回值 —— 回退可以，必须有声（CLAUDE.md 判据五）。
    ★ 每行的第 4 项 `settled` 是 CH 自己算好的布尔值（`now() - INTERVAL
      {settle} MINUTE > max(_captured_at)`），本函数不再持有任何时钟。
    ★ 掉档分两种，判据不同、留痕也不同（Fix round 1 团队负责人裁定）：
      · `*_drop_vs_neighbour`：跟**下一个更旧的候选日**比掉了——不是历史
        最大值，只往上爬的阈值会把「一直在掉」读成「一直没掉」。
      · `*_below_floor`：压根没到过 `min_rows`/`min_distinct_sid`（调用方从
        `[forecast]` 配置传入的 E-1 实测下限），哪怕邻居也一样低、比不出
        「掉」——纯相邻比较有个洞：整个候选窗口同步塌陷到同一个低值时，
        相邻两日互相看起来完全没「掉」，这条不随候选集本身浮动的地板兜住它。
      优先报 `drop_vs_neighbour`：能跟邻居比出「掉」，说明是这一天出的事，
      比「压根没到过地板」更具体、更能定位到哪天开始坏的。
    """
    if not rows:
        raise UnknownShape(
            f"{FBA_DETAIL_TABLE} 近 {lookback_days} 天一个采集日都没有。"
            "★ 空不是「今天没货」——把采集缺口读成空会让整张表的在仓变成 0")
    ordered = sorted(rows, key=lambda r: r[0], reverse=True)
    limit = 1.0 - threshold
    rejected: list[dict] = []
    for i, (date, n_rows, n_sid, settled) in enumerate(ordered):
        reason = None
        if not settled:
            reason = "not_settled"
        else:
            nxt = ordered[i + 1] if i + 1 < len(ordered) else None
            if nxt is not None and n_rows < nxt[1] * limit:
                reason = "rows_drop_vs_neighbour"
            elif nxt is not None and n_sid < nxt[2] * limit:
                reason = "sid_drop_vs_neighbour"
            elif n_rows < min_rows * limit:
                reason = "rows_below_floor"
            elif n_sid < min_distinct_sid * limit:
                reason = "sid_below_floor"
        if reason is None:
            return date, rejected
        rejected.append({"date": date.isoformat(), "rows": int(n_rows),
                         "uniq_sid": int(n_sid), "reason": reason})
    raise ChDataUnusable(rejected)


class ChSource:
    """真 CH 取数。★ 客户端由调用方注入 —— dim/ 不许 import shared.ch_client。

    `as_of()`（Task 1）、`onhand_available()`（Task 2）、`purchase_in_transit()`
    （Task 3）、`monthly_sales_history()`（Task 4）均已接真取数——四个方法
    没有一个还留着 `NotImplementedError`。

    ★ `min_rows`/`min_distinct_sid`/`lookback_days`/`settle_minutes` 的默认值
    = `shared.config._FORECAST_DEFAULTS` 里同名键（`min_rows`/`min_distinct_sid`
    E-1 实测：`SQL_FBA_CAPTURE_DAYS` 近 20 个采集日 2026-09-23 测得
    7,934~8,080 行、uniq(sid) 恒为 21；`lookback_days`/`settle_minutes` 就是
    模块常量 `LOOKBACK_DAYS`/`SETTLE_MINUTES`）。与 `drop_threshold` /
    `cache_ttl_s` 同一个理由，不是 import 被禁：这些数字跟着 `[forecast]`
    配置走，`dim/` 层只做纯变换、不读配置文件——真正的配置来源由未来的装配层
    （`api/ui/source_factory.py`，Task 5）读出来再作为构造参数传进来，这里
    只重复一次默认值，保证不传时离线也能用同一批 E-1 数字跑起来。

    ★ `classify_failure` 同理是注入点，不是读配置——它对应
    `shared.ch_client.describe_failure` 的签名（`Callable[[BaseException],
    dict]`），生产装配层会把那个真正实现传进来；默认值 `_unclassified_failure`
    只保证离线也能构造、也能触发 `ChUnavailable`，但 `kind` 恒为
    `"unknown"`（见该函数文档）。
    """

    def __init__(self, query: Query, *, now: Callable[[], dt.datetime] = _default_now,
                 drop_threshold: float = 0.30, cache_ttl_s: int = 300,
                 min_rows: int = 7934, min_distinct_sid: int = 21,
                 lookback_days: int = LOOKBACK_DAYS, settle_minutes: int = SETTLE_MINUTES,
                 purchase_staleness_days: int = 3,
                 classify_failure: Callable[[BaseException], dict] = _unclassified_failure
                 ) -> None:
        self._q = query
        self._now = now
        self._threshold = drop_threshold
        self._ttl = dt.timedelta(seconds=cache_ttl_s)
        self._min_rows = min_rows
        self._min_distinct_sid = min_distinct_sid
        self._lookback_days = lookback_days
        self._settle_minutes = settle_minutes
        #: ★ `[forecast].purchase_staleness_days`（OQ-5）——年龄单位是天，与
        #:   `ChSource.as_of()`（fba_detail 快照日）比较，不是与本机 wall clock 比。
        self._purchase_staleness_days = purchase_staleness_days
        self._classify = classify_failure
        self._as_of: tuple[dt.datetime, dt.date] | None = None
        #: ★ 缓存键是解析出来的快照日，不是 TTL——同一 `_captured_date` 的快照
        #:   不可变（E-1/E-3：一天写一次、60 秒写完），日期一变整份丢弃重建
        #:   （不合并，记忆 `defaults-preserve-staleness`）。
        self._onhand_cache: tuple[dt.date, dict[tuple[str, str], int]] | None = None
        #: `(参照的 as_of, 解析出的采购快照日)`。★ 终审 C-3：原先只存后者、一经
        #:   解析就**永不再查**，于是 OQ-5 的陈旧闸在第一次成功之后再也不会触发——
        #:   实测 `as_of()` 走到 2026-10-03 时 `purchase_as_of()` 仍返回
        #:   2026-09-23（真实年龄 10 天 / 阈值 3 天），一声不响地继续供应一批
        #:   冻住的在途。缓存键必须是**比较的参照物**（`as_of()`）：参照物一变，
        #:   年龄就变了，这批缓存对新的参照物就不再有效（同 `_onhand_cache`
        #:   按快照日作键那条理由，只是它的参照物是自己）。
        self._purchase_as_of: tuple[dt.date, dt.date] | None = None
        self._transit_cache: tuple[dt.date, dict[str, list[InTransit]]] | None = None
        #: ★ 最近一批取数的丢弃计数，供上层写进日志与响应（`source_notes.dropped`）。
        self._stats: dict = {}
        #: `(seller_sku, sid)` → 那一次 `monthly_sales_history()` 的窗口留痕
        #: （口径标记，OQ-1 裁定）——装配层把它原样透传进
        #: `POST /plans/{id}/claims` 响应，不许丢弃或改写。没问过的键返回 `{}`，
        #: 不抛错：那是「还没问过」，不是「问了但没有答案」。
        #: ★ 终审 C-2：原先是**一个**槽位，写完之后由调用方在下一行读回来。
        #:   两个并发调用一交错，后写的那个会把先写的整个盖掉——实测线程 B 问的是
        #:   sid 11077（PETSFIT_EUROPE/Amazon.co.uk），拿回来的窗口却写着
        #:   PETSFIT_NORTH_AMERICA/Amazon.com，连 zero_filled 都是 A 的。
        #:   一个指着错店的标记比没有标记更坏：它是对「这个数是关于什么的」
        #:   给出的一个自信的错答案。所以改成**按它回答的那个键存取** ——
        #:   `history_window()` 必须把自己要问的 (seller_sku, sid) 报上来，
        #:   读到别人的那条路径在构造上就不存在了。
        self._history_windows: dict[tuple[str, str], dict] = {}

    def as_of(self) -> dt.date:
        now = self._now()
        if self._as_of is not None and now - self._as_of[0] < self._ttl:
            return self._as_of[1]
        sql = SQL_FBA_CAPTURE_DAYS.format(lookback=self._lookback_days, settle=self._settle_minutes)
        t0 = time.perf_counter()
        try:
            rows = self._q(sql)
        except Exception as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            cause = self._classify(e)
            # ★ 三问：打的谁（target）· 多久（elapsed_ms）· 怎么 failed 的（cause，
            #   含 kind——「超时」与「连不上」处置相反，不许只留 e 的 message。
            log.warning("op=ch_as_of outcome=fail target=%s elapsed_ms=%d cause=%s",
                       FBA_DETAIL_TABLE, elapsed_ms, cause)
            raise ChUnavailable(target=FBA_DETAIL_TABLE, cause=cause) from e
        elapsed_ms = (time.perf_counter() - t0) * 1000
        date, rejected = pick_snapshot_date(
            rows, self._threshold, self._min_rows, self._min_distinct_sid,
            lookback_days=self._lookback_days)
        for r in rejected:
            log.warning("op=ch_as_of outcome=rejected %s accepted=%s —— "
                        "回退了一天，这条就是它的证据", r, date)
        log.info("op=ch_as_of outcome=ok as_of=%s candidates=%d rejected=%d elapsed_ms=%d",
                 date, len(rows), len(rejected), elapsed_ms)
        self._as_of = (now, date)
        return date

    def monthly_sales_history(self, seller_sku: str, sid: str, months: int
                              ) -> list[tuple[dt.date, int]]:
        """最近 `months` 个完整自然月的实际销量，按月升序（协议：dim/source.py:23）。

        ★ sid → (store, sales_channel) 走 `dim/order_store_map.py` 的声明表，
        不在这里解析店名——认不出的 sid 由 `store_for()` 抛 `UnknownStore`，
        不猜一个店垫上。★ 只统计完整自然月，当月绝不入选（`month_window`）。
        ★ 去重在 `SQL_MONTHLY_SALES` 里做（按 order_item argMax，OQ-1 裁定的
        先决条件），本方法不重复这一步，只做窗口对齐 + 补 0 + 留痕。"""
        store, channel = order_store_map.store_for(str(sid))
        window = month_window(self.as_of(), months)
        start, end = window[0], _next_month(window[-1])
        sql = SQL_MONTHLY_SALES.format(
            store=store, channel=channel, seller_sku=seller_sku,
            start=start.isoformat(), end=end.isoformat())
        t0 = time.perf_counter()
        try:
            rows = self._q(sql)
        except Exception as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            cause = self._classify(e)
            log.warning("op=ch_sales outcome=fail target=%s store=%s channel=%s "
                       "msku=%s elapsed_ms=%d cause=%s",
                       ORDERS_TABLE, store, channel, seller_sku, elapsed_ms, cause)
            raise ChUnavailable(target=ORDERS_TABLE, cause=cause) from e
        elapsed_ms = (time.perf_counter() - t0) * 1000
        hits = {r[0]: int(r[1]) for r in rows}
        window_set = set(window)
        extra = sorted(m.isoformat() for m in hits if m not in window_set)
        if extra:
            raise UnknownShape(
                f"月销量落在窗口之外：{extra}（窗口 {[w.isoformat() for w in window]}）。"
                "静默忽略就是漏了一段历史，或者 month_window/SQL 的边界算错了")
        series, filled = fill_months(window, hits)
        newest_age = (self.as_of() - window[-1]).days
        self._history_windows[(seller_sku, str(sid))] = {
            "store": store, "sales_channel": channel,
            "months": [m.isoformat() for m in window],
            "zero_filled": [m.isoformat() for m in window if m not in hits],
            "newest_month": window[-1].isoformat(),
            "newest_month_age_days": newest_age,
            # ★ L-3：订单是滞后采集的。去重已经消掉「重复采集」那一成因，
            #   残余的「真·晚到订单」本期未测（design §8 OQ-1）—— 所以把口径
            #   说出去，而不是给一个没有标记的数。控制器裁定：这个字符串必须
            #   原样透传进 claims 响应，不许在装配层丢弃或改写。
            "lag_note": "orders_are_lagging_collected; deduped_by_order_item; "
                        "true_late_arrival_share_unmeasured_see_design_oq1",
        }
        log.info("op=ch_sales outcome=ok store=%s channel=%s msku=%s months=%d hits=%d"
                 " zero_filled=%d newest_age_days=%d elapsed_ms=%d",
                 store, channel, seller_sku, months, len(hits), filled, newest_age,
                 elapsed_ms)
        return series

    def history_window(self, seller_sku: str, sid: str) -> dict:
        """**这一个** `(seller_sku, sid)` 的窗口留痕，供装配层写进
        `POST /plans/{id}/claims` 响应（控制器裁定：原样透传，不许丢弃/改写）。
        没问过这个键时返回 `{}`。

        ★ 终审 C-2：参数不是装饰 —— 它是这个方法唯一的正确性依据。不带键的
        「最近一次」在并发下会如实地报出另一个请求的店铺名（见 `__init__` 里
        `_history_windows` 上面那段实测）。"""
        return dict(self._history_windows.get((seller_sku, str(sid)), {}))

    def _onhand(self) -> dict[tuple[str, str], int]:
        """按 `as_of()` 的快照日批量取一次并缓存（design §5：21 次 grid 调用
        → 1 条 SQL）。★ 同 `as_of()` 的模式：查询失败必须分类后抛
        `ChUnavailable`，不许让裸 driver 异常从这里漏出去——`as_of()`
        成功之后、在仓批量查询本身仍可能在两次往返之间掉线。"""
        as_of = self.as_of()
        if self._onhand_cache is not None and self._onhand_cache[0] == as_of:
            return self._onhand_cache[1]
        sql = SQL_FBA_ONHAND.format(as_of=as_of.isoformat())
        t0 = time.perf_counter()
        try:
            rows = self._q(sql)
        except Exception as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            cause = self._classify(e)
            log.warning("op=ch_onhand outcome=fail target=%s as_of=%s elapsed_ms=%d cause=%s",
                       FBA_DETAIL_TABLE, as_of, elapsed_ms, cause)
            raise ChUnavailable(target=FBA_DETAIL_TABLE, cause=cause) from e
        elapsed_ms = (time.perf_counter() - t0) * 1000
        if not rows:
            raise UnknownShape(
                f"{FBA_DETAIL_TABLE} 在 as_of={as_of} 取回 0 行 —— "
                "这一天通过了掉档守卫却是空的，说明守卫和取数读的不是同一批")
        by_key, dropped = parse_onhand(rows)
        log.info("op=ch_onhand outcome=ok as_of=%s keys=%d dropped=%s elapsed_ms=%d",
                 as_of, len(by_key), dropped, elapsed_ms)
        self._onhand_cache = (as_of, by_key)
        self._stats["onhand"] = dropped
        return by_key

    def onhand_available(self, seller_sku: str, sid: str) -> int | None:
        # ★ 批次被接受 ⇒ 缺行就是 0（L-1：FBA 报表不返回库存为 0 的 SKU，
        #   完全断货正是最该被看见的状态）。批次不可用 ⇒ `_onhand()` 已经
        #   抛了（`ChUnavailable`/`ChDataUnusable`/`UnknownShape`），绝不会
        #   走到这里返回 0 冒充未知。`has_fba=false` 的「不适用」不经过本
        #   方法（B-6，调用方已按 PG `seller.has_fba` 闸住）。
        return self._onhand().get((str(sid), seller_sku), 0)

    def stats(self) -> dict:
        """最近一次批量取数的丢弃计数，供上层写进日志与响应（`source_notes.dropped`）。"""
        return dict(self._stats)

    def purchase_as_of(self) -> dt.date:
        """采购表自己的 `max(_captured_date)`（覆盖型表，没有历史快照，E-14
        实测比 fba_detail 晚一天）。★ 陈旧阈值（OQ-5）：与 `as_of()`（fba_detail
        快照日）比较年龄，超过 `purchase_staleness_days` 就 `PurchaseTableStale`，
        不许把陈旧数据读成「在途为 0」或悄悄返回上一轮缓存的数字。

        ★ 终审 C-3：缓存键是**参照的 `as_of()`**，不是「查过一次就完事」。
        参照物一往前走，同一个 captured 的年龄就变了，闸必须重新判一次 ——
        否则采购采集停摆之后，每一张 grid 都在一个悄悄老了好几天的
        `source_notes.purchase_as_of` 下继续发一批冻住的在途，而 OQ-5 点名
        「悄悄给昨天的数」正是它要防的那一种结局。"""
        reference = self.as_of()
        if self._purchase_as_of is not None and self._purchase_as_of[0] == reference:
            return self._purchase_as_of[1]
        t0 = time.perf_counter()
        try:
            rows = self._q(SQL_PURCHASE_AS_OF)
        except Exception as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            cause = self._classify(e)
            log.warning("op=ch_purchase_as_of outcome=fail target=%s elapsed_ms=%d cause=%s",
                       PURCHASE_ITEMS_TABLE, elapsed_ms, cause)
            raise ChUnavailable(target=PURCHASE_ITEMS_TABLE, cause=cause) from e
        elapsed_ms = (time.perf_counter() - t0) * 1000
        if not rows or rows[0][0] is None:
            raise UnknownShape(
                f"{PURCHASE_ITEMS_TABLE} 一个采集日都没有 —— "
                "把采集缺口读成「没有在途」会让计划看起来不缺货")
        captured = rows[0][0]
        # ★ 陈旧比较的参照基准是 fba_detail 的快照日（`as_of()`，上面已取），不是
        #   本机 wall clock——两张表都是「采集副本」，比较它们各自的 CH 时点才有意义。
        age_days = (reference - captured).days
        if age_days > self._purchase_staleness_days:
            log.warning("op=ch_purchase_as_of outcome=stale captured=%s age_days=%d "
                        "threshold_days=%d elapsed_ms=%d",
                        captured, age_days, self._purchase_staleness_days, elapsed_ms)
            raise PurchaseTableStale(captured, age_days, self._purchase_staleness_days)
        log.info("op=ch_purchase_as_of outcome=ok captured=%s age_days=%d reference=%s"
                 " elapsed_ms=%d", captured, age_days, reference, elapsed_ms)
        self._purchase_as_of = (reference, captured)
        return captured

    def _in_transit(self) -> dict[str, list[InTransit]]:
        """按 `purchase_as_of()` 的快照日批量取一次并缓存（同 `_onhand()` 的
        批量道理：grid 逐货号调用，不能每次都打一次 CH）。★ 查询失败必须
        分类后抛 `ChUnavailable`——`purchase_as_of()` 成功之后，批量取数
        本身仍可能在两次往返之间掉线。"""
        purchase_as_of = self.purchase_as_of()
        if self._transit_cache is not None and self._transit_cache[0] == purchase_as_of:
            return self._transit_cache[1]
        sql = SQL_PURCHASE_IN_TRANSIT.format(purchase_as_of=purchase_as_of.isoformat())
        t0 = time.perf_counter()
        try:
            rows = self._q(sql)
        except Exception as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            cause = self._classify(e)
            log.warning("op=ch_in_transit outcome=fail target=%s purchase_as_of=%s "
                       "elapsed_ms=%d cause=%s",
                       PURCHASE_ITEMS_TABLE, purchase_as_of, elapsed_ms, cause)
            raise ChUnavailable(target=PURCHASE_ITEMS_TABLE, cause=cause) from e
        elapsed_ms = (time.perf_counter() - t0) * 1000
        by_sku, dropped = parse_in_transit(rows)
        total = sum(t.units for v in by_sku.values() for t in v)
        # ★ E-13 实测开口在途 28,561 件里 2026-10 只有 345 件、之后为 0 —— 计划
        #   窗口内几乎一件都不落。这是数据的形状，不是「没取到」，总量要打出来。
        log.info("op=ch_in_transit outcome=ok purchase_as_of=%s skus=%d units=%d "
                 "dropped=%s elapsed_ms=%d",
                 purchase_as_of, len(by_sku), total, dropped, elapsed_ms)
        if dropped:
            # ★ design §4.4：丢弃 + WARNING 点名 order_sn 与件数——只留计数
            #   (missing_eta_rows/units) 复现时还得手工连一次 CH 才查得出是
            #   哪张单（review fix round 1）。
            log.warning("op=ch_in_transit outcome=partial missing_eta_rows=%d "
                        "missing_eta_units=%d order_sn=%s —— 这些行没有预计到货日，已丢弃",
                        dropped.get("missing_eta_rows", 0), dropped.get("missing_eta_units", 0),
                        _format_refs(dropped.get("missing_eta_refs", [])))
        self._transit_cache = (purchase_as_of, by_sku)
        self._stats["in_transit"] = dropped
        return by_sku

    def purchase_in_transit(self, sku: str) -> list[InTransit]:
        return list(self._in_transit().get(sku, ()))
