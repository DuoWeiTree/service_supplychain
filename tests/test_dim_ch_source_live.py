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
        "SQL 文本或 pick_snapshot_date 的排序/分类可能对不上了")
