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

import pytest

from dim import order_store_map as m
from shared.ch_client import ch_client, ch_query, describe_failure
from shared.config import clickhouse

WINDOW_DAYS = 90


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
