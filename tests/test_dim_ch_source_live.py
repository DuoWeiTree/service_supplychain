"""实测：四条镜像取数 SQL + ChSource.as_of() 在真实 ClickHouse 上真的跑得通。

★ 存在的理由（review 09-22 finding 1）：`tests/test_dim_ch_source.py` 的
  `replay()` 完全无视 SQL 文本，只回放固定的假行——`SQL_SELLER` 那条
  `LEFT JOIN` 两侧 `sid` 类型不同（`lingxing_seller_list.sid` 是 String、
  `lingxing_product_listing.sid` 是 Int64，均见 design §7.0），在真实
  ClickHouse 上 100% 报 `NO_COMMON_TYPE`，12 个 fixture 测试没有一个测得出
  来。这里补一层结构性覆盖不到的那部分：真的连一次 CH，跑一次 SQL。

★ 只在 CH 不可达时跳过，且跳过原因必须点名 host/port/cause —— 不许静默跳过
  （同 `tests/test_probe_ch.py` 的规矩，跳过与「没测到」必须长得不一样）。
  这台机器上 CH 是可达的，本文件的测试就应当真的跑，不是长期停在 skip。

★ Fix round 2（团队负责人裁定 2026-09-23，finding 2）：`SQL_FBA_CAPTURE_DAYS`/
  `ChSource.as_of()` 之前没有同类 live 覆盖——`tests/test_dim_ch_source_forecast.py`
  的 7~14 个测试全部用假 `query`，同样完全无视 SQL 文本，跟 `SQL_SELLER` 当年
  那个坑是同一种结构性盲区。下面补上。
"""
from __future__ import annotations

import datetime as dt

import pytest

from dim import ch_source as cs
from dim.registry import by_name
from shared.ch_client import ch_client, ch_query, describe_failure
from shared.config import clickhouse

#: 四张镜像各自的 fetch_* —— ★ 每条都真跑，不是挑着跑一条就收工。
_TARGETS = [
    ("sku_catalog", cs.fetch_sku_catalog),
    ("msku_bridge", cs.fetch_msku_bridge),
    ("warehouse", cs.fetch_warehouse),
    ("seller", cs.fetch_seller),
]


def _skip_if_unreachable():
    """★ 两个 fixture 共用同一套「跳过必须点名」判据，避免各写一份走样。"""
    c = clickhouse()
    where = f"{c['host']}:{c.get('port', 8123)}/{c.get('database', 'jxd_raw')}"
    try:
        return ch_query(ch_client()), where
    except Exception as e:  # noqa: BLE001 - 建连失败的类型不可预知，跳过前先分类记下来
        pytest.skip(f"CH 不可达 target={where} cause={describe_failure(e)} —— "
                     f"跳过而非静默：本条从不因为「没测到」而绿")
        return None, where


@pytest.fixture(scope="module")
def live_query():
    base, _where = _skip_if_unreachable()

    def limited(sql: str, parameters: dict | None = None) -> list[tuple]:
        # ★ 这是冒烟测试，不是覆盖面测试——LIMIT 只为控制读取量，不影响
        #   「SQL 在真实源上能不能跑通」这个判据。50 行是为了在 msku_bridge
        #   实测约 28.6% 的 amzn.gr.* 排除率下，仍大概率留下至少一条有效行，
        #   避免下面的宽度断言因为「恰好全滤空」而变成空转的。
        # ★ `parameters` 照样往下传（终审 I-5）——签名跟不上就会在替身这一层
        #   悄悄把参数吃掉，而那是「证人不在现场」的另一种形状。
        return base(sql.strip() + " LIMIT 50", parameters)

    return limited


@pytest.fixture(scope="module")
def live_query_raw():
    """★ 不追加 LIMIT——`SQL_FBA_CAPTURE_DAYS` 本来就是 GROUP BY 聚合查询，
    结果已经收窄到「候选日个数」这么几行，追加 LIMIT 只是噪音。"""
    base, _where = _skip_if_unreachable()
    return base


@pytest.mark.parametrize("name,fetch", _TARGETS)
def test_sql_runs_on_real_clickhouse_and_row_width_matches_registry(name, fetch, live_query):
    """★ 真跑一次 SQL（不是回放）；跑通之后再验行宽——两件事都要证明。"""
    got = fetch(live_query)
    assert got.rows, (
        f"{name}: 真实 CH 一行有效数据都没取到——LIMIT 太小或这批被 "
        "drop_reasons 全滤空了，下面的宽度断言就是空转的")
    width = len(by_name(name).columns)
    assert all(len(r) == width for r in got.rows), (
        f"{name}: fetch 输出的行宽与登记表 columns（{width}）对不上：{got.rows[:1]}")


def test_as_of_runs_on_real_clickhouse_and_picks_a_real_candidate_day(live_query_raw):
    """★ 真跑一次 `SQL_FBA_CAPTURE_DAYS` + `ChSource.as_of()` 的纯变换链路
    （不是回放）。不拿本机 wall clock 当判据（Fix round 1 才刚把这条比较从
    Python 侧搬进 CH——这条测试自己也不能开倒车）：直接用同一条 SQL 原文
    再查一次候选日集合，断言 `as_of()` 落在这批真实候选日之内，而不是拿
    `dt.date.today()` 去猜「够不够新」。"""
    rows = live_query_raw(cs.SQL_FBA_CAPTURE_DAYS,
                          {"lookback": cs.LOOKBACK_DAYS, "settle": cs.SETTLE_MINUTES})
    assert rows, (
        f"{cs.FBA_DETAIL_TABLE} 近 {cs.LOOKBACK_DAYS} 天一个采集日都没有——"
        "下面的断言就是空转的")
    candidate_dates = {r[0] for r in rows}

    src = cs.ChSource(live_query_raw, classify_failure=describe_failure)
    got = src.as_of()
    assert isinstance(got, dt.date)
    assert got in candidate_dates, (
        f"as_of()={got} 不在真实候选日 {sorted(candidate_dates)} 里——形态不对，"
        f"SQL 文本或 pick_snapshot_date 的排序/分类可能对不上了")


def test_onhand_sql_runs_on_real_clickhouse_and_row_shape_is_sane(live_query_raw):
    """★ Task 2 review finding 4：`SQL_FBA_ONHAND` 是新 SQL、自带一次 `toString(sid)`
    转型——同 `SQL_SELLER` 当年的坑一个形状（`sid` 两侧类型不同在真实 CH 上直接
    `NO_COMMON_TYPE`，12 个 fixture 回放测试一个都测不出来）。这里真跑一次
    `SQL_FBA_ONHAND`（不是回放），验证行宽与「已知有货的 sid 真的出现在结果
    里」，并断言 `sid=0` 欧洲共享池今天确实非零地被排除——这条断言若变成 0，
    要么共享池真的清空了要么排除逻辑本身坏了，两者都必须显式暴露，不能悄悄
    通过。"""
    src = cs.ChSource(live_query_raw, classify_failure=describe_failure)
    as_of = src.as_of()
    rows = live_query_raw(cs.SQL_FBA_ONHAND, {"as_of": as_of.isoformat()})
    assert rows, (
        f"{cs.FBA_DETAIL_TABLE} 在 as_of={as_of} 真实 CH 上一行在仓数据都没取到——"
        "下面的断言就是空转的")
    assert all(len(r) == 4 for r in rows), (
        f"SQL_FBA_ONHAND 的行宽应为 4（sid, seller_sku, units, raw_rows）："
        f"real row={rows[:1]}")

    by_key, dropped = cs.parse_onhand(rows)
    assert any(sid == "11072" for sid, _seller_sku in by_key), (
        "已知有货的 sid 11072（design §1.3 E-1/E-4 的常驻样本）在真实 CH 上"
        "一个 key 都没出现——形态不对")
    shared = dropped.get("shared_pool_excluded", {})
    assert shared.get("units", 0) > 0 and shared.get("rows", 0) > 0, (
        f"sid=0 欧洲共享池今天理应非零（design E-4：实测 1,114 行 / 14,073 件），"
        f"实际拿到 {shared!r}——排除逻辑或数据源本身可能已经变了")


def test_onhand_available_runs_on_real_clickhouse_and_matches_a_hand_written_sum(live_query_raw):
    """★ 真值对账（design §7）：`ChSource.onhand_available` 的答案要和一条独立
    手写的 SQL 对上——同一条 SQL 自己对自己不算对账。不硬编码某个具体 msku
    （那会在该 msku 未来清库存/下架后变成一条会莫名其妙红掉的测试），改为从
    真实批量结果里随手取一个非共享池的 key，分别用 `ChSource` 与独立 SQL 各查
    一次，断言两者相等。"""
    src = cs.ChSource(live_query_raw, classify_failure=describe_failure)
    as_of = src.as_of()
    rows = live_query_raw(cs.SQL_FBA_ONHAND, {"as_of": as_of.isoformat()})
    by_key, _dropped = cs.parse_onhand(rows)
    assert by_key, f"{cs.FBA_DETAIL_TABLE} 在 as_of={as_of} 批量结果为空——对账无从做起"
    (sid, seller_sku), want = next(iter(by_key.items()))

    got = src.onhand_available(seller_sku, sid)
    assert got == want, (
        f"ChSource.onhand_available({seller_sku!r}, {sid!r})={got} 与批量结果"
        f"{want} 对不上——两条路径本该读同一批缓存")

    hand = live_query_raw(f"""
        SELECT toInt64(sum(afn_fulfillable_quantity))
          FROM {cs.FBA_DETAIL_TABLE}
         WHERE _captured_date = toDate('{as_of.isoformat()}')
           AND toString(sid) = '{sid}' AND seller_sku = '{seller_sku}'
    """)
    assert hand and hand[0][0] == want, (
        f"独立手写 SQL 对 (sid={sid}, seller_sku={seller_sku}) 算出 {hand}，"
        f"与 onhand_available 的 {want} 对不上——两条路径本该读同一批快照")


#: ---------------------------------------------------------------------------
#: Task 3（purchase_in_transit，design §4.4 / §8 OQ-5）：`SQL_PURCHASE_AS_OF`/
#: `SQL_PURCHASE_IN_TRANSIT` 是新 SQL，且 `WITH o AS (...)` 里的 `argMax(status,
#: _captured_date)` 与主查询的 `INNER JOIN` 同 `SQL_SELLER` 当年的坑一个形状——
#: 结构性问题只有真的连一次 CH 才测得出来。★ Task 3 review 要求：live 覆盖是
#: 必需项，不是可选项（Task 2 曾因为漏了这条被打回）。
#: ---------------------------------------------------------------------------

def test_purchase_as_of_sql_runs_on_real_clickhouse(live_query_raw):
    """★ `SQL_PURCHASE_AS_OF` 真跑一次，拿到一个非空的候选日——采购表是覆盖型、
    没有历史快照（docs/01:94），所以这里只断言「有值」，不断言具体日期。"""
    rows = live_query_raw(cs.SQL_PURCHASE_AS_OF)
    assert rows and rows[0][0] is not None, (
        f"{cs.PURCHASE_ITEMS_TABLE} 真实 CH 上 max(_captured_date) 是空的——"
        "采购表这批可能整个没采到")
    assert isinstance(rows[0][0], dt.date)


def test_purchase_in_transit_sql_runs_on_real_clickhouse_and_row_shape_is_sane(live_query_raw):
    """★ 真跑一次 `SQL_PURCHASE_IN_TRANSIT`（不是回放），验证行宽与「按状态过滤
    确实生效」——E-12 那个坑（`status=9` 已完成单 `quantity_receive` 全为 0）在
    真实数据上仍然存在：手写一条**不带 `o.st = 2`** 的算术版本对比，真实开口量
    必须明显小于裸算术版本，否则说明 `o.st = 2` 这个过滤条件已经在真实 SQL 里
    失效了（这条断言就是「必须按状态过滤」这条判据本身在真实数据上的证据）。
    """
    src = cs.ChSource(live_query_raw, classify_failure=describe_failure)
    purchase_as_of = src.purchase_as_of()
    rows = live_query_raw(cs.SQL_PURCHASE_IN_TRANSIT,
                          {"purchase_as_of": purchase_as_of.isoformat()})
    assert rows, (
        f"{cs.PURCHASE_ITEMS_TABLE} 在 purchase_as_of={purchase_as_of} 真实 CH 上"
        "一行开口在途都没取到——下面的断言就是空转的")
    assert all(len(r) == 4 for r in rows), (
        f"SQL_PURCHASE_IN_TRANSIT 的行宽应为 4（sku, period, units, ref）："
        f"real row={rows[:1]}")
    true_total = sum(int(r[2]) for r in rows)

    naive_sql = f"""
        SELECT toInt64(sum(i.quantity_real - i.quantity_receive)) AS units
          FROM {cs.PURCHASE_ITEMS_TABLE} AS i
         WHERE i._captured_date = toDate('{purchase_as_of.isoformat()}')
           AND ifNull(i.is_delete, 0) = 0
           AND i.quantity_real > i.quantity_receive
    """
    naive_total = live_query_raw(naive_sql)[0][0]
    assert naive_total > true_total, (
        f"裸算术（不按 status 过滤）算出 {naive_total}，应当明显大于按 "
        f"o.st=2 过滤后的真实开口量 {true_total}（E-12：已完成单 "
        f"quantity_receive 全为 0）——如果两者相等，说明 status 过滤在真实 "
        f"SQL 里已经不起作用了")


def test_purchase_in_transit_matches_a_hand_written_sum(live_query_raw):
    """★ 真值对账（同 onhand 那条的道理）：从真实批量结果里随手取一个货号，
    分别用 `ChSource.purchase_in_transit` 与独立手写 SQL 各查一次，断言两者
    的合计相等——不硬编码某个具体货号，避免该货号未来到货/单据关闭后变成一条
    莫名其妙红掉的测试。"""
    src = cs.ChSource(live_query_raw, classify_failure=describe_failure)
    purchase_as_of = src.purchase_as_of()
    rows = live_query_raw(cs.SQL_PURCHASE_IN_TRANSIT,
                          {"purchase_as_of": purchase_as_of.isoformat()})
    by_sku, _dropped = cs.parse_in_transit(rows)
    assert by_sku, f"{cs.PURCHASE_ITEMS_TABLE} 在 purchase_as_of={purchase_as_of} 批量结果为空——对账无从做起"
    sku = next(iter(by_sku))
    want = sum(t.units for t in by_sku[sku])

    got_rows = src.purchase_in_transit(sku)
    got = sum(t.units for t in got_rows)
    assert got == want, (
        f"ChSource.purchase_in_transit({sku!r}) 合计={got} 与批量结果合计"
        f"{want} 对不上——两条路径本该读同一批缓存")

    hand = live_query_raw(f"""
        WITH o AS (
            SELECT order_sn, argMax(status, _captured_date) AS st
              FROM {cs.PURCHASE_ORDER_TABLE}
             GROUP BY order_sn
        )
        SELECT toInt64(sum(i.quantity_real - i.quantity_receive))
          FROM {cs.PURCHASE_ITEMS_TABLE} AS i
         INNER JOIN o ON o.order_sn = i.order_sn
         WHERE i._captured_date = toDate('{purchase_as_of.isoformat()}')
           AND o.st = 2 AND ifNull(i.is_delete, 0) = 0
           AND i.quantity_real > i.quantity_receive
           AND i.sku = '{sku}'
    """)
    assert hand and hand[0][0] == want, (
        f"独立手写 SQL 对 sku={sku} 算出 {hand}，与批量结果合计 {want} 对不上"
        "——两条路径本该读同一批快照")


def test_purchase_as_of_is_within_staleness_budget(live_query_raw):
    """★ 控制器裁定追加（09-23，task-6-brief.md）：Task 3 新增的
    `purchase_staleness_days`（默认 3 天，OQ-5）之前只有构造出来的假快照日在
    单测里验证过判定逻辑（`tests/test_dim_ch_source_forecast.py`
    `test_purchase_staleness_days_is_wired_into_the_check`），没有用真实两张表
    的实际年龄差核实过默认值本身够不够用。这里读 `purchase_as_of()` 与 `as_of()`
    在真实 CH 上的天数差，断言小于 `purchase_staleness_days` 默认值——若这台
    机器上真的超了，测试应该红：红了说明默认值 3 天定小了，或者采购表的采集
    真的断了，两种都不该被静默吞掉（`purchase_as_of()` 内部已经会在超阈值时
    抛 `PurchaseTableStale`，这条测试要的是提前把「差多少天」摆出来，而不是
    等断言炸了才去猜）。"""
    # ★ 不碰私有属性 `_purchase_staleness_days`——直接从构造函数的默认值读，
    #   与 ChSource 未显式传参时实际生效的阈值是同一个数。
    threshold_days = cs.ChSource.__init__.__kwdefaults__["purchase_staleness_days"]
    src = cs.ChSource(live_query_raw, classify_failure=describe_failure)
    as_of = src.as_of()
    purchase_as_of = src.purchase_as_of()  # 若已超阈值，这一步自己就会抛 PurchaseTableStale
    age_days = (as_of - purchase_as_of).days
    assert age_days < threshold_days, (
        f"purchase_as_of={purchase_as_of} 比 as_of={as_of} 晚 {age_days} 天，"
        f"已经达到/超过默认阈值 {threshold_days} 天——"
        "要么默认值 3 天定小了，要么采购表采集真的断了，两种都不该被静默吞掉"
        "（正常情况下 `purchase_as_of()` 会在超阈值时先抛 PurchaseTableStale，"
        "走到这条断言本身就说明还在预算内）")


#: ---------------------------------------------------------------------------
#: Task 4（monthly_sales_history，design §4.2 / §7 / §8 OQ-1）：`SQL_MONTHLY_SALES`
#: 是新 SQL，带一层子查询 + `GROUP BY amazon_order_id, order_item_id` 去重——
#: 同前几个 Task 的坑一个形状，只有真的连一次 CH 才测得出结构性问题（比如
#: ClickHouse 对嵌套聚合 / `argMax` 组合的实际支持形态）。★ live 覆盖是必需项，
#: 不是可选项（Task 1/2 都因为漏了这条被打回）。
#: ---------------------------------------------------------------------------

#: ★ design §7 冻结的真值对账样本：msku DCC1800264G1Z2B / sid 11072
#: （store=PETSFIT_NORTH_AMERICA, channel=Amazon.com）。2026-09-23 实测两个
#: 已完结月份：2026-06 = 561、2026-07 = 254（去重后）。
FROZEN_MSKU = "DCC1800264G1Z2B"
FROZEN_SID = "11072"
FROZEN_MONTHS = {dt.date(2026, 6, 1): 561, dt.date(2026, 7, 1): 254}


def test_monthly_sales_sql_runs_on_real_clickhouse_and_row_shape_is_sane(live_query_raw):
    """★ 真跑一次 `SQL_MONTHLY_SALES`（不是回放），验证行宽——(month, units)。"""
    from dim import order_store_map as m
    store, channel = m.store_for(FROZEN_SID)
    rows = live_query_raw(cs.SQL_MONTHLY_SALES,
                          {"store": store, "channel": channel, "seller_sku": FROZEN_MSKU,
                           "start": "2026-06-01", "end": "2026-09-01"})
    assert rows, (
        f"{cs.ORDERS_TABLE} 对 (store={store}, channel={channel}, sku={FROZEN_MSKU}) "
        "真实 CH 上一行月销量都没取到——下面的断言就是空转的")
    assert all(len(r) == 2 for r in rows), (
        f"SQL_MONTHLY_SALES 的行宽应为 2（month, units）：real row={rows[:1]}")
    assert all(isinstance(r[0], dt.date) for r in rows)


#: 「去重降幅算显著」的下限。★ 不是冻结的实测值，是「它确实在起作用」的门槛：
#: 实测量级是 12~20%（见 `test_monthly_sales_dedupe_*` 里的表），5% 留足了余量。
_MATERIAL_DEDUPE_PCT = 5.0
#: 「这个月已经老到不会再被重复采集」的月龄。★ 2026-09-23 实测：2026-03~07 五个月
#: 的降幅**全部是 0.00%**，08 是 12.53%、09 是 19.42% —— 重复采集只发生在最近
#: 两个自然月内。4 个月往前是这条边界之外很远的地方。
_AGED_MONTHS_BACK = 4
#: 降幅「已经衰减掉」的上限。
_DECAYED_DEDUPE_PCT = 1.0


def _month_start(d: dt.date, months_back: int) -> dt.date:
    y, m = d.year, d.month - months_back
    while m <= 0:
        y, m = y - 1, m + 12
    return dt.date(y, m, 1)


def _raw_vs_dedup(query, store: str, channel: str,
                  start: dt.date, end: dt.date) -> tuple[int, int, float]:
    """同一个窗口，裸算术 vs 按 order_item 去重。返回 (raw, dedup, 降幅%)。

    ★ 去重那条的判据与 `SQL_MONTHLY_SALES` **逐字相同**，只是去掉了
    `AND sku = '{seller_sku}'` 这一层（那条 SQL 是按单个 msku 设计的，
    这里要的是整个 (store, channel) 的去重效果）。"""
    raw = query(f"""
        SELECT toInt64(sum(quantity))
          FROM {cs.ORDERS_TABLE}
         WHERE store = '{store}' AND sales_channel = '{channel}'
           AND order_status NOT IN ('Cancelled', 'Pending')
           AND toDate(purchase_date) >= toDate('{start.isoformat()}')
           AND toDate(purchase_date) < toDate('{end.isoformat()}')
    """)[0][0]
    dedup = query(f"""
        SELECT toInt64(sum(units)) FROM (
            SELECT if(argMax(order_status, _captured_date) NOT IN ('Cancelled', 'Pending'),
                      argMax(quantity, _captured_date), 0) AS units
              FROM {cs.ORDERS_TABLE}
             WHERE store = '{store}' AND sales_channel = '{channel}'
               AND toDate(purchase_date) >= toDate('{start.isoformat()}')
               AND toDate(purchase_date) < toDate('{end.isoformat()}')
             GROUP BY amazon_order_id, order_item_id
        )
    """)[0][0]
    assert raw, f"窗口 [{start}, {end}) 裸算术为 {raw} —— 断言会是空转的"
    assert dedup is not None, f"窗口 [{start}, {end}) 去重结果为 None"
    return int(raw), int(dedup), (raw - dedup) / raw * 100


def test_monthly_sales_dedupe_is_material_on_recent_months_and_decayed_on_aged_ones(
        live_query_raw):
    """★★ 终审 I-3：判据从「两个写死的月份 == 两个写死的百分比」换成**性质**。

    旧判据钉的是 `("2026-08", 12.5)` / `("2026-09", 19.4)`，而降幅**随月龄衰减**
    ——2026-09-23 实测（PETSFIT_NORTH_AMERICA / Amazon.com）：

        2026-03  0.00%   2026-06  0.00%   2026-08  12.53%
        2026-04  0.00%   2026-07  0.00%   2026-09  19.42%
        2026-05  0.00%

    所以 2026-08 会自己往 0 漂，两条断言都会在大约两个月后炸，而失败信息会说
    「去重逻辑坏了」—— 其实只是时间过去了。**一条会腐烂成假红的门禁是一条会
    被人删掉的门禁。**

    换成三条性质，月份全部从 CH 自己的 `today()` 推：
      ① 最近的活跃窗口（上个月月初 → 今天）降幅必须显著 —— 去重真的在起作用；
      ② 4 个月前那个月降幅必须已经衰减掉 —— 于是一个老月份上的 0% 读作「月龄」，
         不读作「逻辑坏了」，这条性质本身就是那条解释的证据；
      ③ 窗口里每个月 `dedup <= raw` —— 去重永远不许把总量变大。
    ★ 活跃窗口刻意跨两个月（当月 + 上个月）：只取当月的话，每月 1 号那天当月
      才刚开张、还没有任何跨采集日的重复，这条会变成一条每月红一次的假红。
    """
    from dim import order_store_map as m
    store, channel = m.store_for(FROZEN_SID)
    # ★ 用 CH 自己的日期，不用本机 wall clock —— 同 `SQL_FBA_CAPTURE_DAYS` 把
    #   「已写完」判定搬进 CH 的那条理由，这条测试自己也不能开倒车。
    today = live_query_raw("SELECT today()")[0][0]
    assert isinstance(today, dt.date)

    # ① 活跃窗口：上个月月初 → 明天（右开区间，把今天整天包进来）
    active_start = _month_start(today, 1)
    raw, dedup, pct = _raw_vs_dedup(live_query_raw, store, channel,
                                    active_start, today + dt.timedelta(days=1))
    assert dedup < raw and pct >= _MATERIAL_DEDUPE_PCT, (
        f"活跃窗口 [{active_start}, {today}] 去重降幅只有 {pct:.2f}%"
        f"（raw={raw} dedup={dedup}），低于 {_MATERIAL_DEDUPE_PCT}% —— "
        "GROUP BY amazon_order_id, order_item_id 这一步可能在真实 SQL 里"
        "已经不起作用了。★ 这是「最近的月份」，重复采集本该在这里最密集"
        "（2026-09-23 实测 12~20%）")

    # ② 4 个月前那个月：降幅必须已经衰减掉
    aged_start = _month_start(today, _AGED_MONTHS_BACK)
    aged_end = _month_start(today, _AGED_MONTHS_BACK - 1)
    _raw_old, _dedup_old, pct_old = _raw_vs_dedup(live_query_raw, store, channel,
                                                  aged_start, aged_end)
    assert pct_old <= _DECAYED_DEDUPE_PCT, (
        f"{aged_start:%Y-%m}（{_AGED_MONTHS_BACK} 个月前）的去重降幅是 "
        f"{pct_old:.2f}%，高于 {_DECAYED_DEDUPE_PCT}% —— 「降幅随月龄衰减」这条"
        "性质不再成立了。它是①那条断言的**解释**：老月份上的 0% 该读作月龄、"
        "不该读作逻辑坏了。性质变了就要重新实测一次衰减边界，"
        "而不是把这个阈值往上抬")

    # ③ 去重永远不许把总量变大 —— 逐月查，一个月都不许漏
    span_start = _month_start(today, 5)
    rows = live_query_raw(f"""
        SELECT toStartOfMonth(toDate(purchase_date)) AS m, toInt64(sum(quantity))
          FROM {cs.ORDERS_TABLE}
         WHERE store = '{store}' AND sales_channel = '{channel}'
           AND order_status NOT IN ('Cancelled', 'Pending')
           AND toDate(purchase_date) >= toDate('{span_start.isoformat()}')
         GROUP BY m ORDER BY m
    """)
    assert len(rows) >= 3, f"近 6 个月只有 {len(rows)} 个月有单 —— 下面就是空转的"
    deduped = dict(live_query_raw(f"""
        SELECT m, toInt64(sum(units)) FROM (
            SELECT toStartOfMonth(toDate(any(purchase_date))) AS m,
                   if(argMax(order_status, _captured_date) NOT IN ('Cancelled', 'Pending'),
                      argMax(quantity, _captured_date), 0) AS units
              FROM {cs.ORDERS_TABLE}
             WHERE store = '{store}' AND sales_channel = '{channel}'
               AND toDate(purchase_date) >= toDate('{span_start.isoformat()}')
             GROUP BY amazon_order_id, order_item_id
        ) GROUP BY m ORDER BY m
    """))
    inflated = [(str(month), int(n), deduped.get(month)) for month, n in rows
                if deduped.get(month) is None or deduped[month] > n]
    assert not inflated, (
        f"这些月份去重之后总量反而变大（或整月消失）：{inflated}"
        "（格式 (月, raw, dedup)）—— 去重只该合并同一 order_item 的多次采集，"
        "把总量做大说明 argMax 的分组键或状态过滤已经变形了")


def test_monthly_sales_history_matches_the_frozen_hand_checked_numbers(live_query_raw):
    """★ 真值对账（design §7）：不用 `ChSource` 自己那条 SQL 自己对自己——
    两个已完结月份的数字是设计阶段冻结的（2026-06=561、2026-07=254），这里
    只验证 `ChSource.monthly_sales_history()` 今天在真实 CH 上仍然吐出同样的
    数字。窗口取 4 个月，只断言这两个已知月份，不断言整个窗口的长度/内容——
    `as_of()` 落在哪个月由今天的日期决定，写死整条 window 会在月份翻篇后变成
    一条莫名其妙红掉的测试。"""
    src = cs.ChSource(live_query_raw, classify_failure=describe_failure)
    got = dict(src.monthly_sales_history(FROZEN_MSKU, FROZEN_SID, 4))
    missing = [m.isoformat() for m in FROZEN_MONTHS if m not in got]
    assert not missing, (
        f"冻结月份 {missing} 不在今天的窗口 {sorted(d.isoformat() for d in got)} 里——"
        "如果这是因为窗口已经滑过去了（说明这条测试到了该换一对新冻结月份的时候），"
        "而不是代码本身的问题，去 design §7 和这条测试一起换成新的已完结月份")
    for month, want in FROZEN_MONTHS.items():
        assert got[month] == want, (
            f"{month}: ChSource.monthly_sales_history 给出 {got[month]}，"
            f"与 design §7 冻结的真值 {want} 对不上")

    window = src.history_window(FROZEN_MSKU, FROZEN_SID)
    assert window["store"] == "PETSFIT_NORTH_AMERICA"
    assert window["sales_channel"] == "Amazon.com"
    assert window["lag_note"], "口径标记不许是空字符串——没有标记的数比没有数更坏"


def test_monthly_sales_history_excludes_the_current_month(live_query_raw):
    """★ 当月绝不入选（month_window）——今天真实调用一次，断言窗口最新一个月
    早于本月月初。"""
    src = cs.ChSource(live_query_raw, classify_failure=describe_failure)
    as_of = src.as_of()
    this_month = dt.date(as_of.year, as_of.month, 1)
    got = src.monthly_sales_history(FROZEN_MSKU, FROZEN_SID, 3)
    assert all(m < this_month for m, _ in got), (
        f"窗口里出现了当月或未来月份：{got}（as_of={as_of}）——"
        "当月是半个月，混进去会把销量读成骤降")


#: ---------------------------------------------------------------------------
#: 终审 I-5：带值的 SQL 一律走驱动的**服务端参数绑定**，不许 `str.format` 拼串。
#: ---------------------------------------------------------------------------

#: 一个带撇号的 msku。★ 不是「构造的极端输入」：`seller_sku` 来自
#: `lingxing_product_listing`，那是卖家自己起的名字，撇号一点都不稀奇。
#:
#: ★★ 实测（2026-09-23，把 `SQL_MONTHLY_SALES` 改回拼串跑这条门禁）：后果比终审
#:   I-5 描述的**更坏**。拼串生成 `... AND sku = 'A' OR 1=1 --'`，`OR 1=1` 让
#:   整个谓词恒真，于是 CH **不报错**，返回的是**整个店的销量**：
#:
#:     2026-06 = 27,031   2026-07 = 25,772   2026-08 = 22,911
#:
#:   而这个 msku 真实销量是 0。也就是说它不是「一条坏 SQL 被误报成 CH 连不上」，
#:   而是**一个安静的错数**：一个 msku 的需求被填成全店的量，响应 200、
#:   `history_window` 照常产出、没有任何一处报警。
QUOTE_MSKU = "A' OR 1=1 --"


def test_a_quote_in_the_msku_is_bound_not_interpolated(live_query_raw):
    """★ 真打一次 CH：带撇号的 msku 必须**正常返回 0 行**。

    0 行是正确答案（没有这个 msku 的单）—— 关键在于它是「查过了、没有」。
    拼串版本在这里返回的是整个店的销量（见 `QUOTE_MSKU` 上面的实测），
    而那是一个 200 响应里的安静错数，没有任何一处报警。
    """
    src = cs.ChSource(live_query_raw, classify_failure=describe_failure)
    got = src.monthly_sales_history(QUOTE_MSKU, FROZEN_SID, 3)
    assert got == [], f"带撇号的 msku 竟然查出了行：{got}"
    window = src.history_window(QUOTE_MSKU, FROZEN_SID)
    assert window["store"] == "PETSFIT_NORTH_AMERICA", (
        "口径标记也要照常产出 —— 它是这次「查过了、没有」的证据")


def test_a_quote_in_the_msku_really_escapes_the_string_literal_when_interpolated():
    """★ 这条是上一条的**靶子说明**：证明 `QUOTE_MSKU` 真的能打坏拼串版本，
    否则上一条就是在一个无害输入上空转（一个不会致命的输入上的绿是假的绿）。

    不改生产代码，只在本地把同一个值按旧办法拼一次，断言拼出来的谓词已经从
    字符串字面量里逃出去、变成了一个恒真条件。
    """
    # ★ 刻意用 `str.format`（不是 f-string）：复现的就是修复前那一行的写法。
    template = "SELECT 1 FROM t WHERE sku = '{seller_sku}'"
    sql = template.format(seller_sku=QUOTE_MSKU)
    assert "sku = 'A' OR 1=1" in sql, sql
    assert sql.count("'") == 3, (
        f"拼串之后撇号个数应当是奇数（3）—— 字符串字面量没闭合上：{sql}")


def test_no_value_operand_is_string_interpolated_in_the_forecast_sql():
    """★ 防回归：四条带值的 SQL 里不许再出现 `= '{...}'` 这种拼串占位符。

    ★ 判据看的是**源码文本**，不是运行时行为：一个人把某一条改回 `.format`
      拼串，运行时照样跑得通（只要没人给它带撇号的输入），而那正是 I-5
      在生产里潜伏了一整轮的原因 —— 这条断言是它唯一会被发现的地方。
    """
    import re
    for name in ("SQL_FBA_CAPTURE_DAYS", "SQL_MONTHLY_SALES", "SQL_FBA_ONHAND",
                 "SQL_PURCHASE_IN_TRANSIT"):
        sql = getattr(cs, name)
        bad = re.findall(r"'\{[^}]*\}'|toDate\('\{[^}]*\}'\)", sql)
        assert not bad, (
            f"{name} 里还有拼串占位符 {bad} —— 带值的操作数一律走 "
            "`{name:Type}` 服务端绑定（终审 I-5）")
        assert re.search(r"\{[A-Za-z_]+:[A-Za-z0-9()]+\}", sql), (
            f"{name} 里一个绑定占位符都没有 —— 这条断言就是空转的")
