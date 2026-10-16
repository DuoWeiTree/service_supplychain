"""ChStock 在真 CH 上跑通（spec 2026-10-10 §4）。★ 回放测不出 SQL 本身能不能跑。"""
import pytest

from dim import ch_source as cs
from dim import ch_stock as st
from shared.ch_client import describe_failure
from tests.test_dim_ch_source_live import live_query_raw  # noqa: F401 —— 共用连不上即 skip 的夹具

pytestmark = pytest.mark.integration


def _local_wids(q) -> set[int]:
    rows = cs.fetch_warehouse(q).rows
    return {r[0] for r in rows if r[2] == "local"}


def test_snapshot_runs_and_shared_bucket_dominates(live_query_raw):  # noqa: F811
    q = live_query_raw
    local = _local_wids(q)
    skus = {r[0] for r in q(f"SELECT DISTINCT sku FROM {st.STOCK_TABLE}"
                            f" WHERE _captured_date = (SELECT max(_captured_date) FROM {st.STOCK_TABLE})"
                            " AND product_valid_num > 0 LIMIT 40")}
    assert skus, "库存表最新一天没有一条可发 > 0 的货号 —— 下面断言会空转"
    snap = st.ChStock(q, local_wids=local, known_sids=set(),
                      classify_failure=describe_failure).snapshot(skus)
    assert snap.buckets, "本地仓一行都没取到"
    assert all(set(b) == {"shared"} for b in snap.buckets.values()), \
        "known_sids 为空时店铺桶应全部计入 unknown_seller"
    assert snap.dropped["not_local_wid"]["rows"] >= 0


def test_po_progress_runs(live_query_raw):  # noqa: F811
    q = live_query_raw
    sns = {r[0] for r in q(f"SELECT DISTINCT order_sn FROM {cs.PURCHASE_ITEMS_TABLE}"
                           f" WHERE _captured_date = (SELECT max(_captured_date) FROM {cs.PURCHASE_ITEMS_TABLE})"
                           " LIMIT 5")}
    got = st.ChStock(q, local_wids=set(), known_sids=set(),
                     classify_failure=describe_failure).po_progress(sns)
    assert got and all(v.entered >= 0 and v.pending >= 0 for v in got.values())
