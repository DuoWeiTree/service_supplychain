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

    def limited(sql: str) -> list[tuple]:
        # ★ 这是冒烟测试，不是覆盖面测试——LIMIT 只为控制读取量，不影响
        #   「SQL 在真实源上能不能跑通」这个判据。50 行是为了在 msku_bridge
        #   实测约 28.6% 的 amzn.gr.* 排除率下，仍大概率留下至少一条有效行，
        #   避免下面的宽度断言因为「恰好全滤空」而变成空转的。
        return base(sql.strip() + " LIMIT 50")

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
    sql = cs.SQL_FBA_CAPTURE_DAYS.format(lookback=cs.LOOKBACK_DAYS, settle=cs.SETTLE_MINUTES)
    rows = live_query_raw(sql)
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
    sql = cs.SQL_FBA_ONHAND.format(as_of=as_of.isoformat())
    rows = live_query_raw(sql)
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
    rows = live_query_raw(cs.SQL_FBA_ONHAND.format(as_of=as_of.isoformat()))
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
    sql = cs.SQL_PURCHASE_IN_TRANSIT.format(purchase_as_of=purchase_as_of.isoformat())
    rows = live_query_raw(sql)
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
    rows = live_query_raw(cs.SQL_PURCHASE_IN_TRANSIT.format(
        purchase_as_of=purchase_as_of.isoformat()))
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
    sql = cs.SQL_MONTHLY_SALES.format(
        store=store, channel=channel, seller_sku=FROZEN_MSKU,
        start="2026-06-01", end="2026-09-01")
    rows = live_query_raw(sql)
    assert rows, (
        f"{cs.ORDERS_TABLE} 对 (store={store}, channel={channel}, sku={FROZEN_MSKU}) "
        "真实 CH 上一行月销量都没取到——下面的断言就是空转的")
    assert all(len(r) == 2 for r in rows), (
        f"SQL_MONTHLY_SALES 的行宽应为 2（month, units）：real row={rows[:1]}")
    assert all(isinstance(r[0], dt.date) for r in rows)


def test_monthly_sales_dedupe_reduces_the_total_by_the_measured_magnitude(live_query_raw):
    """★ OQ-1 裁定的先决条件本身在真实数据上的证据：按 (amazon_order_id,
    order_item_id) 去重必须真的让总量变小，且量级要跟 design §1.3 E-9 冻结的
    12.5%（2026-08）/ 19.4%（2026-09）对得上——不是随便小一点就算数，如果两者
    相等或去重后反而更大，说明去重逻辑已经不起作用了。手写一条不带
    `GROUP BY amazon_order_id, order_item_id` 的裸算术版本做对比，同
    `SQL_PURCHASE_IN_TRANSIT` 那条 status 过滤的 live 对比是同一个手法。"""
    from dim import order_store_map as m
    store, channel = m.store_for(FROZEN_SID)
    for month, start, end, want_pct in (
        ("2026-08", "2026-08-01", "2026-09-01", 12.5),
        ("2026-09", "2026-09-01", "2026-10-01", 19.4),
    ):
        raw = live_query_raw(f"""
            SELECT toInt64(sum(quantity))
              FROM {cs.ORDERS_TABLE}
             WHERE store = '{store}' AND sales_channel = '{channel}'
               AND order_status NOT IN ('Cancelled', 'Pending')
               AND toDate(purchase_date) >= toDate('{start}')
               AND toDate(purchase_date) < toDate('{end}')
        """)[0][0]
        # ★ SQL_MONTHLY_SALES 是按单个 seller_sku 设计的（grid 逐 msku 调用）；
        #   这里要的是整个 (store, channel) 的去重效果，所以另写一条同样的去重
        #   逻辑但不按 sku 过滤——去重/状态过滤的判据与 SQL_MONTHLY_SALES 逐字
        #   相同，只是去掉了 `AND sku = '{{seller_sku}}'` 这一层。
        dedup = live_query_raw(f"""
            SELECT toInt64(sum(units)) FROM (
                SELECT if(argMax(order_status, _captured_date) NOT IN ('Cancelled', 'Pending'),
                          argMax(quantity, _captured_date), 0) AS units
                  FROM {cs.ORDERS_TABLE}
                 WHERE store = '{store}' AND sales_channel = '{channel}'
                   AND toDate(purchase_date) >= toDate('{start}')
                   AND toDate(purchase_date) < toDate('{end}')
                 GROUP BY amazon_order_id, order_item_id
            )
        """)[0][0]
        assert raw is not None and dedup is not None, (
            f"{month}: raw={raw} dedup={dedup}——下面的断言就是空转的")
        assert dedup < raw, (
            f"{month}: 去重后 {dedup} 应当明显小于裸算术 {raw}——如果两者相等，"
            "说明 GROUP BY amazon_order_id, order_item_id 这一步在真实 SQL 里"
            "已经不起作用了")
        pct = (raw - dedup) / raw * 100
        assert abs(pct - want_pct) < 3, (
            f"{month}: 去重降幅 {pct:.1f}% 与 design §1.3 E-9 冻结的 {want_pct}% "
            "差太多——量级本身可能已经变了（不是「小一点就算数」）")


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
