"""真 CH 取数。★ 预测取数（`as_of`）已在 2026-09-23 ChSource 设计里接真 CH——

其余三个方法（`monthly_sales_history` / `onhand_available` / `purchase_in_transit`）
仍刻意抛 `NotImplementedError`，不给空实现：空实现会让「该做没做」和「本来就不用做」
长得一模一样（01 规则五）。取数口径在 `docs/superpowers/specs/2026-09-23-chsource-design.md`
与 CLAUDE.md「取数的四条铁律」里；`ChSource` 类的完整定义在本文件末尾「预测取数」段
（与下面的镜像刷新取数层是两件事，见该段落开头的说明）。
"""
from __future__ import annotations

import datetime as dt
import logging

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

from dim.source import InTransit  # ★ 只有本段的 ChSource 需要它

#: 候选快照日。★ 只问 fba_detail —— as_of 标注的是**在仓数**，它的时点就是这张表的。
SQL_FBA_CAPTURE_DAYS = """
SELECT _captured_date, count() AS rows, uniq(sid) AS uniq_sid, max(_captured_at) AS done_at
  FROM jxd_raw.lingxing_inventory_fba_detail
 WHERE _captured_date >= today() - {lookback}
 GROUP BY _captured_date
 ORDER BY _captured_date DESC
"""

#: ★ 实测：该表当天 06:34~07:39 起跑、约 60 秒跑完（design §1.3 E-3），而刷新调度
#:   就排在 06:30 —— 半写窗口是真实存在的。30 分钟 = 实测时长的 30 倍。
SETTLE_MINUTES = 30
LOOKBACK_DAYS = 7

#: ★ E-1 实测下限（近 20 个采集日 7,934~8,080 行、每日恰好 21 个 sid）。
#:   纯「比对相邻候选日」有个洞：如果整个候选窗口同步塌陷到同一个低值
#:   （例如某次故障连续几天都只写回同样少的行数），相邻两日互相看起来
#:   完全没有「掉」，掉档守卫会对着一堆同样坏的日子视而不见。这条绝对
#:   下限与相邻比较是「或」的关系：任一条不过都算掉档，堵上这个洞。
NOMINAL_ROWS = 7934
NOMINAL_SID = 21

#: ★ 其余三个方法在本任务（Task 1）仍未实现，留给后续任务（Task 2~4）——
#:   与文件顶部旧占位类的道理一样：空实现要能被认出来，不许悄悄返回假数据。
_STUB_MSG = ("CH 取数属后续阶段任务：请按 docs/superpowers/specs/2026-09-23-chsource-design.md"
             " §4 实现（商品目录 LEFT JOIN 快照、msku→货号按 as_of argMax 但 sid 不参与、"
             "日报先按 _captured_date 去重并比对覆盖面）")


class ChUnavailable(Exception):
    """CH 连不上/超时。★ 带上 describe_failure 的分类：「超时」与「连不上」处置相反。"""

    def __init__(self, target: str, cause: dict) -> None:
        self.target, self.cause = target, cause
        super().__init__(f"ch_unavailable target={target} cause={cause}")


class ChDataUnusable(Exception):
    """候选日全被拒。★ 带上每一天的数字 —— 「不可用」不说明是哪一种不可用就没法查。"""

    def __init__(self, rejected: list[dict]) -> None:
        self.rejected = rejected
        super().__init__(f"ch_data_unusable rejected={rejected}")


def _utc_now() -> dt.datetime:
    # ★ 全仓（CH 的 _captured_at、本模块的比较）一律用 naive datetime——
    #   与一个 tz-aware 的 now() 相减会直接抛 TypeError，不是更安全，是更脆。
    return dt.datetime.now()  # noqa: DTZ005


def pick_snapshot_date(rows: list[tuple], now: dt.datetime,
                       threshold: float) -> tuple[dt.date, list[dict]]:
    """从新到旧挑第一个「已采完 + 没掉档」的采集日。返回 (日期, 被拒清单)。

    ★ 被拒清单不是可选的返回值 —— 回退可以，必须有声（CLAUDE.md 判据五）。
    ★ 掉档比的是**下一个更旧的候选日**，不是历史最大值：只往上爬的阈值会把
      「一直在掉」读成「一直没掉」。但只比相邻日会漏掉「整窗口同步塌陷」
      （相邻两天一样低，互相看不出掉档）——所以还要 OR 上 `NOMINAL_ROWS` /
      `NOMINAL_SID`（E-1 实测下限）这条不随候选集本身浮动的绝对底线。
    """
    if not rows:
        raise UnknownShape(
            f"lingxing_inventory_fba_detail 近 {LOOKBACK_DAYS} 天一个采集日都没有。"
            "★ 空不是「今天没货」——把采集缺口读成空会让整张表的在仓变成 0")
    ordered = sorted(rows, key=lambda r: r[0], reverse=True)
    limit = 1.0 - threshold
    rejected: list[dict] = []
    for i, (date, n_rows, n_sid, done_at) in enumerate(ordered):
        reason = None
        if done_at is None or now - done_at < dt.timedelta(minutes=SETTLE_MINUTES):
            reason = "not_settled"
        else:
            nxt = ordered[i + 1] if i + 1 < len(ordered) else None
            rows_drop = n_rows < NOMINAL_ROWS * limit or (
                nxt is not None and n_rows < nxt[1] * limit)
            sid_drop = n_sid < NOMINAL_SID * limit or (
                nxt is not None and n_sid < nxt[2] * limit)
            if rows_drop:
                reason = "coverage_drop_rows"
            elif sid_drop:
                reason = "coverage_drop_uniq_sid"
        if reason is None:
            return date, rejected
        rejected.append({"date": date.isoformat(), "rows": int(n_rows),
                         "uniq_sid": int(n_sid), "reason": reason})
    raise ChDataUnusable(rejected)


class ChSource:
    """真 CH 取数。★ 客户端由调用方注入 —— dim/ 不许 import shared.ch_client。

    只有 `as_of()` 在本任务（Task 1）接了真取数；其余三个方法仍显式
    `NotImplementedError`，留给 Task 2~4。
    """

    def __init__(self, query: Query, *, now: Callable[[], dt.datetime] = _utc_now,
                 drop_threshold: float = 0.30, cache_ttl_s: int = 300) -> None:
        self._q = query
        self._now = now
        self._threshold = drop_threshold
        self._ttl = dt.timedelta(seconds=cache_ttl_s)
        self._as_of: tuple[dt.datetime, dt.date] | None = None

    def as_of(self) -> dt.date:
        now = self._now()
        if self._as_of is not None and now - self._as_of[0] < self._ttl:
            return self._as_of[1]
        rows = self._q(SQL_FBA_CAPTURE_DAYS.format(lookback=LOOKBACK_DAYS))
        date, rejected = pick_snapshot_date(rows, now, self._threshold)
        for r in rejected:
            log.warning("op=ch_as_of outcome=rejected %s accepted=%s —— "
                        "回退了一天，这条就是它的证据", r, date)
        log.info("op=ch_as_of outcome=ok as_of=%s candidates=%d rejected=%d",
                 date, len(rows), len(rejected))
        self._as_of = (now, date)
        return date

    def monthly_sales_history(self, seller_sku: str, sid: str, months: int
                              ) -> list[tuple[dt.date, int]]:
        raise NotImplementedError(_STUB_MSG)

    def onhand_available(self, seller_sku: str, sid: str) -> int | None:
        raise NotImplementedError(_STUB_MSG)

    def purchase_in_transit(self, sku: str) -> list[InTransit]:
        raise NotImplementedError(_STUB_MSG)
