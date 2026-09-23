"""取数的纯变换。★ 离线可跑 —— dim/ 是纯层，客户端是注入的。"""
import pytest

from dim import ch_source as cs
from dim.registry import by_name
from tests.fixtures import ch_rows as R


def test_sku_catalog_drops_empty_and_counts_what_it_dropped():
    got = cs.fetch_sku_catalog(R.replay(R.SKU_CATALOG))
    assert got.rows == [("DCC1800264G1", "猫爬架")]
    assert got.dropped == 1 and got.drop_reasons == {"empty_sku": 1}
    assert got.source_max_captured == R.D2


def test_sku_catalog_sql_does_not_reference_nonexistent_local_sku_column():
    """★ 探针实测（design §7.0）：lingxing_product_local_products 没有
    local_sku 列（真实列是 sku）—— local_sku 其实在 lingxing_product_listing，
    照抄草案会在真实 CH 上跑不通。"""
    assert "local_sku" not in cs.SQL_SKU_CATALOG.lower()


def test_msku_bridge_keeps_the_same_string_in_two_stores():
    """★ 同一 msku 字符串跨店是不同 listing —— 合并成一行就丢了一个店的库存。"""
    got = cs.fetch_msku_bridge(R.replay(R.MSKU_BRIDGE))
    assert sorted(got.rows) == [("MSKU-A", "11072", "DCC1800264G1"),
                                 ("MSKU-A", "11094", "DCC1800264G1")]
    assert got.drop_reasons == {"unbound_sku": 1, "virtual_promo_group": 1}


def test_msku_bridge_drops_virtual_promo_groups():
    """★ 控制器 09-22 追加裁定：amzn.gr.* 是亚马逊虚拟促销组，不是 listing，
    必须丢弃且计数 —— 静默滤掉就是又一次「丢的一侧没统计」。"""
    got = cs.fetch_msku_bridge(R.replay(R.MSKU_BRIDGE))
    assert not any(r[0].startswith("amzn.gr.") for r in got.rows)
    assert got.drop_reasons["virtual_promo_group"] == 1


def test_msku_bridge_sql_never_argmaxes_sid():
    """★ 实测教训：把 sid 也 argMax 掉，美国两店的 msku 数在 735~1445 之间摆动。"""
    sql = cs.SQL_MSKU_BRIDGE.lower()
    assert "group by seller_sku, sid" in " ".join(sql.split())
    assert "argmax(sid" not in " ".join(sql.split()).replace(" ", "")


def test_warehouse_kind_is_mapped_from_type_and_subtype():
    got = cs.fetch_warehouse(R.replay(R.WAREHOUSE))
    assert [r[2] for r in got.rows] == ["local", "oversea_self", "oversea_3pl"]
    assert [r[3] for r in got.rows] == [None, None, None], "market 无源，留 NULL 不写 ''"
    assert got.drop_reasons.get("market_null") == 3


def test_unknown_warehouse_kind_fails_loudly():
    """★ 认不出的形态硬失败 —— 落进 else '' 再被下游过滤掉，就是静默少一批仓。"""
    with pytest.raises(cs.UnknownShape) as ei:
        cs.fetch_warehouse(R.replay(R.WAREHOUSE_UNKNOWN))
    assert "9" in str(ei.value) and "说不清是什么仓" in str(ei.value)


def test_seller_has_fba_is_derived_from_listing_fulfillment_channel():
    """OQ-3 裁定（控制器 09-22）：has_fba 是派生列 —— 该 sid 名下只要有一条
    listing 的 fulfillment_channel_type = 'FBA' 就算有 FBA，不是猜的默认值。"""
    got = cs.fetch_seller(R.replay(R.SELLER))
    assert {r[0]: r[3] for r in got.rows} == {"11072": True, "11094": False}


def test_seller_market_is_country_and_platform_is_constant_amazon():
    """★ 控制器 09-22 追加裁定（探针 §7.0 实测后）：lingxing_seller_list 没有
    marketplace/platform 列。market 改用 country；platform 该表压根没有 ——
    它本来就是领星「亚马逊店铺列表」，写常量 'amazon' 并注释清楚，不是猜的
    默认值。非 Amazon 平台店归阶段 B。"""
    got = cs.fetch_seller(R.replay(R.SELLER))
    by_id = {r[0]: r for r in got.rows}
    assert by_id["11072"][2] == "US"        # market ← country（中文国名译码）
    assert by_id["11094"][2] == "UK"
    assert by_id["11072"][4] == "amazon"    # platform ← 常量


def test_seller_country_that_is_not_in_the_market_map_fails_loudly():
    """★ 09-23 两个真实缺陷之二：`seller.market` 在 001_foundation.sql:27 的注释
    写的是代码（US / UK / DE …），而 `lingxing_seller_list.country` 给的是中文
    国名。阶段 C（排货/FBA 站点）要按代码匹配，中文名直接落库会让下游连不上。
    解析只能在一个地方（这里），且认不出的国名必须硬失败 —— 不许落进 `else ''`
    再被下游过滤掉（CLAUDE.md「中文仓名可以解析，但只能在一个地方、必须落到
    确定的字段」同一条铁律，这里落到 market 代码）。"""
    with pytest.raises(cs.UnknownShape) as ei:
        cs.fetch_seller(R.replay([("99999", "某新店", "火星", R.D2, 0)]))
    assert "火星" in str(ei.value)


def test_seller_country_map_covers_every_value_seen_live():
    """★ 2026-09-23 实测 `lingxing_seller_list`（21 个 sid 全量，`argMax(country,
    _captured_date)` 按 sid 去重后）的全部分布 —— 15 个不同的中文国名。覆盖不全，
    生产刷新时随时会在没见过的国家上硬失败（这是设计意图，但地图必须先把已知的
    全部收进来，不能让常见国家也炸）。"""
    live_countries = ["美国", "加拿大", "日本", "德国", "英国", "爱尔兰",
                      "墨西哥", "西班牙", "意大利", "瑞典", "波兰", "巴西",
                      "比利时", "荷兰", "法国"]
    rows = [(str(100 + i), f"店{i}", country, R.D2, 0)
           for i, country in enumerate(live_countries)]
    got = cs.fetch_seller(R.replay(rows))
    codes = {r[2] for r in got.rows}
    assert codes == {"US", "CA", "JP", "DE", "UK", "IE", "MX", "ES", "IT",
                     "SE", "PL", "BR", "BE", "NL", "FR"}, codes
    assert len(codes) == len(live_countries), "15 个国家不许被映射成同一个代码"


def test_both_listing_reads_use_the_same_capture_window():
    """★ 终审 M-9：`SQL_SELLER` 的 join 侧原先对 `l` 一个 `_captured_date`
    过滤都没有（全部 58 天历史），而 `SQL_MSKU_BRIDGE` 卡 30 天。两处读的是
    同一张表、回答的是同一件事（这个店还有没有在卖的 listing）。实测今天两个
    窗口的 has_fba 完全一致（21 个 sid 无一差异），所以没有实际影响 ——
    但**没有任何地方记着这两个窗口本该一致**。这条测试就是那个地方。
    """
    window = f"today() - {cs._LISTING_WINDOW_DAYS}"
    assert window in cs.SQL_MSKU_BRIDGE, "msku_bridge 的窗口没走共享常量"
    assert window in cs.SQL_SELLER, "seller 的 join 侧没有同一个窗口"


def test_seller_keeps_the_left_join_when_it_filters_the_listing_side():
    """★ 窗口过滤必须写在 `ON` 里。挪进 `WHERE` 会把 LEFT JOIN 退化成 INNER
    JOIN —— 30 天内一条 listing 都没有的店会**整个消失**，而那正是最该被看见
    的状态（同「全集用商品目录 LEFT JOIN 快照」那条铁律：断货的 SKU 不返回，
    直接查快照会让完全断货的货号从预测里整个消失）。"""
    assert "LEFT JOIN" in cs.SQL_SELLER.upper()
    assert "WHERE" not in cs.SQL_SELLER.upper(), (
        "seller 的窗口过滤跑到 WHERE 去了 —— LEFT JOIN 就退化成 INNER JOIN 了")


def test_seller_sql_does_not_reference_nonexistent_columns():
    """★ 探针实测（design §7.0）：lingxing_seller_list 没有 marketplace/platform
    列 —— 照抄草案会在真实 CH 上跑不通。"""
    sql = cs.SQL_SELLER.lower()
    assert "marketplace" not in sql and ".platform" not in sql


def test_row_width_matches_the_registry():
    for name, fn, rows in [("sku_catalog", cs.fetch_sku_catalog, R.SKU_CATALOG),
                           ("msku_bridge", cs.fetch_msku_bridge, R.MSKU_BRIDGE),
                           ("warehouse", cs.fetch_warehouse, R.WAREHOUSE),
                           ("seller", cs.fetch_seller, R.SELLER)]:      # OQ-3 已裁定，seller 不再抛
        got = fn(R.replay(rows))
        assert all(len(r) == len(by_name(name).columns) for r in got.rows), name


def test_empty_source_is_not_silently_ok():
    """★ 一行都没取到不是「刷新成功、只是没数据」。"""
    with pytest.raises(cs.UnknownShape):
        cs.fetch_sku_catalog(R.replay([]))


def test_a_null_warehouse_type_fails_loudly_like_any_other_unknown_shape():
    """★ 终审 M-11：`int(typ)` 在 `type` 为 NULL 时抛的是 `TypeError`，而不是
    那条写得很好的 `UnknownShape` —— 于是「认不出的仓」与「源里这一列是空的」
    在留痕里长成两种东西，而后者本该走同一条硬失败的路。
    实测今天 118 个仓的 `type` 一个 NULL 都没有，所以这是潜在缺口不是现行 bug；
    但认不出的形态必须默认可见，而不是等它出现那天才发现没人接。"""
    with pytest.raises(cs.UnknownShape) as ei:
        cs.fetch_warehouse(R.replay([(9, "类型为空的仓", None, None, R.D2)]))
    assert "9" in str(ei.value) and "类型为空的仓" in str(ei.value)


def test_coerced_empty_strings_are_counted_not_silent():
    """★ 终审 M-11：`name or ""` / `market or ""` 把 NULL 悄悄变成 `''`，
    而 `fetch_seller` 无条件返回 `dropped=0, drop_reasons={}` —— 代码里
    **没有留下能观测它的地方**。`seller.market` 在 PG 是 NOT NULL（001:29），
    所以源里的 NULL `country` 会变成 `''`，正是 OQ-5 为 `warehouse.market`
    明确拒绝的那一种（「留空是『还没到』，写成 `''` 就再也分不开」）。

    ★ 计数不算 `rows_dropped`：这些行没有被丢，`rows_dropped` 必须仍然等于
      真丢弃之和，否则就是拿另一种口径去污染它。
    """
    got = cs.fetch_seller(R.replay([("11072", None, None, R.D2, 0)]))
    assert got.dropped == 0, "强制转换不是丢弃，不许计进 rows_dropped"
    assert got.drop_reasons.get("coerced_empty_name") == 1, got.drop_reasons
    assert got.drop_reasons.get("coerced_empty_market") == 1, got.drop_reasons

    cat = cs.fetch_sku_catalog(R.replay([("SKU-1", None, R.D2)]))
    assert cat.drop_reasons.get("coerced_empty_name") == 1, cat.drop_reasons
