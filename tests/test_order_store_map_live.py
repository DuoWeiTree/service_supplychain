"""映射表不许过期。★ 手列的宇宙必然漏第 N+1 种 —— 所以宇宙要从源里推导出来比。

★ 存在的理由：`dim/order_store_map.py` 的 `STORE_SID` 是一张**声明**的表（见该
  文件顶部注释），不是从 CH 解析出来的 —— Task 4 实测就是靠一条类似下面这条的
  查询才发现 design §1.3 E-16 漏抄了 `(A4PET_EUROPE, Amazon.de) → 11095`
  这一行（见该 commit 与 `tests/test_order_store_map.py` 顶部注释）。那次是
  人在 Task 4 实现时手动跑了一次 CH 才查出来的，不是靠一条常跑的门禁——
  这条测试把那次手动排查变成一条会一直盯着的门禁：CH 里任何一个新的
  `(store, sales_channel)` 组合，都必须在 `STORE_SID` 或显式的 `UNRESOLVED`
  豁免名单里有名有姓，不许悄悄消失或悄悄被并入 `EXCLUDED_CHANNELS`。

★ 只在 CH 不可达时跳过，且跳过原因必须点名 host/port/cause —— 跳过与
  「没测到」必须长得不一样（同 `tests/test_dim_ch_source_live.py` 的规矩）。
  这台机器上 CH 是可达的，本条从不因为「没测到」而绿。
"""
from __future__ import annotations

from collections import Counter

import pytest

from dim import ch_source as cs
from dim import order_store_map as m
from shared.ch_client import ch_client, ch_query, describe_failure
from shared.config import clickhouse

WINDOW_DAYS = 90


def _live_query():
    """★ 跳过必须点名 host/port/cause —— 跳过与「没测到」必须长得不一样。"""
    c = clickhouse()
    where = f"{c['host']}:{c.get('port', 8123)}/{c.get('database', 'jxd_raw')}"
    try:
        q = ch_query(ch_client())
        q("SELECT 1")
    except Exception as e:  # noqa: BLE001 - 建连失败的类型不可预知
        pytest.skip(f"CH 不可达 target={where} cause={describe_failure(e)} —— "
                    f"跳过而非静默：本条从不因为「没测到」而绿")
        return None
    return q


def test_every_amazon_pair_in_ch_is_declared():
    c = clickhouse()
    where = f"{c['host']}:{c.get('port', 8123)}/{c.get('database', 'jxd_raw')}"
    try:
        q = ch_query(ch_client())
        q("SELECT 1")
    except Exception as e:  # noqa: BLE001 - 建连失败的类型不可预知
        pytest.skip(f"CH 不可达 target={where} cause={describe_failure(e)} —— "
                    f"跳过而非静默：本条从不因为「没测到」而绿")
        return

    rows = q(f"""SELECT store, sales_channel, count()
      FROM jxd_raw.amazon_sp_api_report_all_orders
      WHERE toDate(purchase_date) >= today() - {WINDOW_DAYS}
      GROUP BY store, sales_channel""")
    assert rows, "近 90 天一单都没有 —— 这条规则是空转的"

    # ★ 未声明 = 既不在 STORE_SID（已映射）、也不在 EXCLUDED_CHANNELS（显式排除
    #   的非 Amazon 渠道）、也不在 UNRESOLVED（OQ-3 已知但未查清的 6 组豁免）。
    unmapped = [(s, ch, n) for s, ch, n in rows
                if not ch.startswith(m.EXCLUDED_CHANNELS)
                and (s, ch) not in m.STORE_SID
                and (s, ch) not in m.UNRESOLVED]
    assert not unmapped, (
        f"这些 (store, sales_channel) 在 CH 里有单，既没有 sid 映射，也不在"
        f"已知豁免名单 UNRESOLVED 里：{unmapped}。"
        "新开的店要补进 dim/order_store_map.py::STORE_SID；"
        "确认无 sid 的要写进 design OQ-3 并加进 UNRESOLVED、带上单数计数。"
        "★ 不许悄悄并进 EXCLUDED_CHANNELS —— 那会让「没开店」和「忘了配」长得一样")

    # ★ 反过来也要看：UNRESOLVED 里豁免的 6 组必须真的还在 CH 里出现——如果某
    #   一组近 90 天已经没有单了，说明豁免可能已经过期（店关了/改名了），不该
    #   继续白名单挂着一条空转的豁免。这里只留痕点名，不做硬失败——豁免过期
    #   不是数据错误，是「该清理」的信号，跟「新组没声明」的危害不对等。
    seen = {(s, ch) for s, ch, _n in rows}
    stale_exemptions = m.UNRESOLVED - seen
    if stale_exemptions:
        print(f"注意：UNRESOLVED 里这些豁免近 {WINDOW_DAYS} 天已经没有单了，"
              f"可能已经过期，考虑清理：{sorted(stale_exemptions)}")


def test_every_claimable_sid_resolves_to_a_store_or_is_a_named_exemption():
    """★★ 终审 I-1：**反方向**的门禁 —— 上面那条查的是「报表里出现过的
    (store, channel) 有没有 sid」，而真正的消费者是 `store_for(sid)`。

    两个宇宙不是同一个：`STORE_SID` 是从订单报表那一侧攒出来的，但能被认领的
    是 `msku_bridge` 里的 sid。2026-09-23 实测，三个真店（11098 / 11099 / 11100）
    从正向门禁底下整个漏了过去，它们名下 **179 个 msku** 会在 `source = "ch"`
    切换当天全部 503 —— 而 `README.md` 承诺的是「换源只改一行」。

    ★ 宇宙从 CH **推导**，不手列：`fetch_msku_bridge()`（能被认领的 sid）
      ∪ `fetch_seller()`（seller 镜像的全集）。手列的宇宙必然漏第 N+1 种 ——
      这条门禁存在的理由就是上一份手列的宇宙漏了三个。
    ★ 认不出的 sid 必须**要么可解析，要么在 `NO_ORDER_REPORT_SID` 里有名有姓
      带实测出处**。不许落进「反正也没人认领」这种沉默。
    """
    q = _live_query()

    bridge = cs.fetch_msku_bridge(q)
    sellers = cs.fetch_seller(q)
    bridge_sids = {str(r[1]) for r in bridge.rows}
    seller_sids = {str(r[0]) for r in sellers.rows}
    universe = bridge_sids | seller_sids
    assert universe, "CH 侧推不出任何 sid —— 这条规则是空转的"

    # ★ 统计被丢掉的那一侧：不说「有几个 msku 会 503」，人只会觉得「个别店不好使」。
    mskus_by_sid = Counter(str(r[1]) for r in bridge.rows)

    unresolvable = []
    for sid in sorted(universe):
        try:
            m.store_for(sid)
        except m.UnknownStore:
            if sid not in m.NO_ORDER_REPORT_SID:
                unresolvable.append((sid, mskus_by_sid.get(sid, 0)))
    assert not unresolvable, (
        f"这些 sid 能被认领（在 msku_bridge / seller 里）却解析不出 (store, channel)，"
        f"也不在 NO_ORDER_REPORT_SID 豁免名单里：{unresolvable}"
        f"（格式 (sid, 该 sid 名下的 msku 数)，合计 "
        f"{sum(n for _s, n in unresolvable)} 个 msku 一认领就 503）。"
        "要么补进 dim/order_store_map.py::STORE_SID，要么写进 NO_ORDER_REPORT_SID "
        "并带上实测出处 —— 不许留着，那会让「换源只改一行」在上线当天变成假话")

    # ★ 豁免名单本身也要对着源复核：豁免一个已经不存在的店 = 一条空转的豁免。
    #   与正向那条对 UNRESOLVED 的处理同一个判据：只留痕点名，不硬失败 ——
    #   「豁免过期」是该清理的信号，跟「新店没声明」的危害不对等。
    gone = sorted(set(m.NO_ORDER_REPORT_SID) - universe)
    if gone:
        print(f"注意：NO_ORDER_REPORT_SID 里这些 sid 已经不在 CH 的 seller/bridge 里了，"
              f"豁免可能已过期，考虑清理：{gone}")


def test_the_order_report_has_no_column_that_could_identify_a_sid():
    """★ `NO_ORDER_REPORT_SID` 三条豁免的**依据本身**要有门禁盯着：豁免成立的
    前提是「这张报表里没有任何一列能定位到店」。哪天采集侧补上了 sid /
    seller_id 这类列，整张声明表（连同三条豁免）就该被推翻，而不是继续挂着。

    ★ 2026-09-23 实测：42 列，没有一列含 sid / seller / shop / account；
      `store` 有史以来只出现过 5 个值，没有一个属于那三家店。
    """
    q = _live_query()
    cols = {r[0] for r in q(f"DESCRIBE TABLE {cs.ORDERS_TABLE}")}
    assert cols, "DESCRIBE 一列都没返回 —— 这条规则是空转的"
    hits = sorted(c for c in cols
                  if any(k in c.lower() for k in ("sid", "seller", "shop", "account")))
    assert not hits, (
        f"{cs.ORDERS_TABLE} 出现了可能定位到店的列：{hits}。"
        "dim/order_store_map.py 整张声明表的前提是「这张表里没有 sid」——"
        "前提没了就该重做映射，而不是让三条 NO_ORDER_REPORT_SID 豁免继续挂着")
