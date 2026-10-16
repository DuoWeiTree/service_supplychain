"""017 · 排货量视图与核对（承重墙② 的「求和函数 + 恒等式」，CLAUDE.md 架构铁律 2）。"""
import pytest

from shared.pg_client import pg_conn
from tests.test_ddl_016_dispatch import OVERSEA, arrange, derive_one, new_sheet, sheet_event, world  # noqa: F401


def checks():
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT check_name FROM v_check_dispatch ORDER BY 1")
        return [r[0] for r in cur.fetchall()]


def qty(line_id):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT total_units, staged_units, dispatched_units, pending_units"
                    " FROM v_plan_line_dispatch_qty WHERE line_id=%s", (line_id,))
        return cur.fetchone()


@pytest.fixture
def placed(world, seed):  # noqa: F811
    with pg_conn() as c, c.cursor() as cur:
        sid = new_sheet(cur, seed.actor)
        a1 = arrange(cur, seed, sid, world, 30)
        derive_one(cur, seed, sid, a1)
        sid2 = new_sheet(cur, seed.actor, route="oversea")      # ★ CA-D13：海外仓格子在海外仓表
        a2 = arrange(cur, seed, sid2, None, 7, channel="oversea", dest_wid=OVERSEA)
        derive_one(cur, seed, sid2, a2)
    return sid, a1, a2, sid2


def test_normal_flow_has_no_violations_and_quantities_add_up(placed, world, seed):  # noqa: F811
    assert checks() == []
    assert qty(world) == (100, 30, 0, 70)


def test_committed_splits_planned_and_unplanned(placed, seed):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT sid, planned_units, unplanned_units, units FROM v_stock_committed"
                    " WHERE sku=%s AND from_wid=%s", (seed.sku_a, seed.wid))
        assert cur.fetchall() == [(seed.seller_a, 30, 7, 37)]


def test_cancel_releases_staged(placed, world, seed):  # noqa: F811
    sid, _, _, sid2 = placed
    with pg_conn() as c, c.cursor() as cur:
        sheet_event(cur, sid, "草稿", "已取消", seed.actor, reason="t")
        sheet_event(cur, sid2, "草稿", "已取消", seed.actor, reason="t")
    assert qty(world) == (100, 0, 0, 100)
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM v_stock_committed")
        assert cur.fetchone()[0] == 0


def test_arrangement_changed_without_rederive_is_named(placed):
    _, a1, _, _ = placed
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE dispatch_arrangement SET units = 31 WHERE arrangement_id=%s", (a1,))
    assert checks() == ["map_ne_arrangement"]


def test_arrangement_without_map_is_named(placed, world, seed):  # noqa: F811
    sid, _, _, _ = placed
    with pg_conn() as c, c.cursor() as cur:
        arrange(cur, seed, sid, world, 1, msku=seed.msku_b)
    assert checks() == ["map_ne_arrangement"]


def test_line_not_equal_to_map_sum_is_named(placed):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE consignment_line SET units = units + 1 WHERE units = 30")
    assert checks() == ["line_ne_sum_map"]


def test_msku_relinked_to_another_sku_is_named(placed, seed):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE msku_bridge SET sku=%s WHERE seller_sku=%s AND sid=%s",
                    (seed.sku_b, *seed.msku_a))
    assert "msku_sku_drift" in checks()
