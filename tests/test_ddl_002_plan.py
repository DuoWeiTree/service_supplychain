"""002 的约束挡得住吗。每条都插一行违反的，断言具体是哪个约束名。"""
import datetime as dt

import psycopg2.errors
import pytest

from shared.pg_client import pg_conn

OCT = dt.date(2026, 10, 1)


def mk_plan(cur, owner, months=3, start=OCT, title="10 月计划"):
    cur.execute("INSERT INTO plan (title, period_start, months, owner_actor, created_by)"
                " VALUES (%s, %s, %s, %s, %s) RETURNING plan_id", (title, start, months, owner, owner))
    return cur.fetchone()[0]


def test_months_range_is_1_to_24(wipe, seed):
    """★ S-7：03 §2.1 曾写 1..12，而 M-10 裁定 1..24 —— 统一按 24。"""
    with pg_conn() as c, c.cursor() as cur:
        mk_plan(cur, seed.actor, months=24)
    for bad in (0, 25):
        with pytest.raises(psycopg2.errors.CheckViolation) as ei, pg_conn() as c, c.cursor() as cur:
            mk_plan(cur, seed.actor, months=bad)
        assert ei.value.diag.constraint_name == "plan_months_1_24"


def test_period_start_must_be_first_of_month(wipe, seed):
    with pytest.raises(psycopg2.errors.CheckViolation) as ei, pg_conn() as c, c.cursor() as cur:
        mk_plan(cur, seed.actor, start=dt.date(2026, 10, 15))
    assert ei.value.diag.constraint_name == "plan_period_start_is_month_start"


def test_owner_must_be_a_known_actor(wipe, seed):
    with pytest.raises(psycopg2.errors.ForeignKeyViolation) as ei, pg_conn() as c, c.cursor() as cur:
        mk_plan(cur, "nobody")
    assert ei.value.diag.constraint_name == "plan_owner_fk"


def test_one_active_claim_per_msku_across_plans(wipe, seed):
    """★ 判据③ 的库层那一半：独占由部分唯一索引裁决，不靠先查后写。"""
    with pg_conn() as c, c.cursor() as cur:
        p1, p2 = mk_plan(cur, seed.actor), mk_plan(cur, seed.actor, title="另一张")
        cur.execute("INSERT INTO msku_claim (plan_id, seller_sku, sid, claimed_by)"
                    " VALUES (%s, %s, %s, %s)", (p1, *seed.msku_a, seed.actor))
        with pytest.raises(psycopg2.errors.UniqueViolation) as ei:
            cur.execute("INSERT INTO msku_claim (plan_id, seller_sku, sid, claimed_by)"
                        " VALUES (%s, %s, %s, %s)", (p2, *seed.msku_a, seed.actor))
        assert ei.value.diag.constraint_name == "msku_claim_one_active_idx"


def test_released_claim_frees_the_msku_but_keeps_the_row(wipe, seed):
    with pg_conn() as c, c.cursor() as cur:
        p1, p2 = mk_plan(cur, seed.actor), mk_plan(cur, seed.actor, title="另一张")
        cur.execute("INSERT INTO msku_claim (plan_id, seller_sku, sid, claimed_by)"
                    " VALUES (%s, %s, %s, %s)", (p1, *seed.msku_a, seed.actor))
        cur.execute("UPDATE msku_claim SET released_at = now(), released_by = %s"
                    " WHERE plan_id = %s", (seed.actor, p1))
        cur.execute("INSERT INTO msku_claim (plan_id, seller_sku, sid, claimed_by)"
                    " VALUES (%s, %s, %s, %s)", (p2, *seed.msku_a, seed.actor))
        cur.execute("SELECT count(*) FROM msku_claim")
        assert cur.fetchone()[0] == 2, "★ 释放不删行 —— 留痕没了就查不到谁占过"


def test_ordered_claim_no_longer_blocks(wipe, seed):
    """★ 独占窗口只到「已下单」为止（03:171 的 WHERE NOT plan_ordered）。
    阶段 A 里 plan_ordered 恒 false（S-12），这里直接置位验证索引的另一半。"""
    with pg_conn() as c, c.cursor() as cur:
        p1, p2 = mk_plan(cur, seed.actor), mk_plan(cur, seed.actor, title="另一张")
        cur.execute("INSERT INTO msku_claim (plan_id, seller_sku, sid, claimed_by, plan_ordered)"
                    " VALUES (%s, %s, %s, %s, true)", (p1, *seed.msku_a, seed.actor))
        cur.execute("INSERT INTO msku_claim (plan_id, seller_sku, sid, claimed_by)"
                    " VALUES (%s, %s, %s, %s)", (p2, *seed.msku_a, seed.actor))


def test_demand_cell_requires_a_claim_and_a_bridge_row(wipe, seed):
    """★ 没认领就没格子。"""
    with pg_conn() as c, c.cursor() as cur:
        p = mk_plan(cur, seed.actor)
        with pytest.raises(psycopg2.errors.ForeignKeyViolation) as ei:
            cur.execute("INSERT INTO plan_demand_cell (plan_id, seller_sku, sid, period_start)"
                        " VALUES (%s, %s, %s, %s)", (p, *seed.msku_a, OCT))
        assert ei.value.diag.constraint_name == "plan_demand_cell_claim_fk"


def test_expected_units_null_is_allowed_but_negative_is_not(wipe, seed):
    """★ NULL = 未知（M-8），不是 0；负数才是错。两者必须分得开。"""
    with pg_conn() as c, c.cursor() as cur:
        p = mk_plan(cur, seed.actor)
        cur.execute("INSERT INTO msku_claim (plan_id, seller_sku, sid, claimed_by)"
                    " VALUES (%s, %s, %s, %s)", (p, *seed.msku_a, seed.actor))
        cur.execute("INSERT INTO plan_demand_cell (plan_id, seller_sku, sid, period_start)"
                    " VALUES (%s, %s, %s, %s)", (p, *seed.msku_a, OCT))
        cur.execute("SELECT expected_units, system_units FROM plan_demand_cell")
        assert cur.fetchone() == (None, None)
        with pytest.raises(psycopg2.errors.CheckViolation) as ei:
            cur.execute("UPDATE plan_demand_cell SET expected_units = -1")
        assert ei.value.diag.constraint_name == "plan_demand_cell_expected_nonneg"


def test_one_current_and_one_in_flight_rev_per_plan(wipe, seed):
    """★ S-4：同一计划最多 1 版在流转 —— 由库层裁决，不靠接口先查后写。"""
    with pg_conn() as c, c.cursor() as cur:
        p = mk_plan(cur, seed.actor)
        cur.execute("INSERT INTO plan_rev (plan_id, rev, content_digest, is_current, submitted_by)"
                    " VALUES (%s, 1, 'd1', true, %s)", (p, seed.actor))
        with pytest.raises(psycopg2.errors.UniqueViolation) as ei:
            cur.execute("INSERT INTO plan_rev (plan_id, rev, content_digest, submitted_by)"
                        " VALUES (%s, 2, 'd2', %s)", (p, seed.actor))
        assert ei.value.diag.constraint_name == "plan_rev_one_in_flight_idx"


def test_plan_line_total_units_must_be_positive(wipe, seed):
    """★ 坑①：总量 0 会因为 0 = 0 恒真，一诞生就自动跳到「已确认」。"""
    with pg_conn() as c, c.cursor() as cur:
        p = mk_plan(cur, seed.actor)
        cur.execute("INSERT INTO plan_rev (plan_id, rev, content_digest, submitted_by)"
                    " VALUES (%s, 1, 'd1', %s)", (p, seed.actor))
        with pytest.raises(psycopg2.errors.CheckViolation) as ei:
            cur.execute(
                "INSERT INTO plan_line (plan_id, rev, sku, period_start, total_units,"
                " demand_by_seller, demand_at_submit, state)"
                " VALUES (%s, 1, %s, %s, 0, '{}'::jsonb, 0, '已提交')", (p, seed.sku_a, OCT))
        assert ei.value.diag.constraint_name == "plan_line_total_positive"


def test_plan_line_has_no_seller_id(wipe):
    """★ P2 / S-3：记录是货号级，店铺归属在组单时由采购员决定（po_source_map）。
    列还在的话，组单时会出现两个真相。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT column_name FROM information_schema.columns"
                    " WHERE table_schema = current_schema() AND table_name = 'plan_line'")
        cols = {r[0] for r in cur.fetchall()}
    assert "seller_id" not in cols
    assert {"demand_by_seller", "demand_at_submit"} <= cols, "S-6：冻结的两列必须在"


def test_skip_reason_is_a_closed_set_and_append_only(wipe, seed):
    """★ 三段各起一个 pg_conn：一次 CheckViolation 会让整个事务进入 aborted 状态，
    conftest 的 pg_conn 在 aborted 事务上 commit() 会被 PG 悄悄折成 ROLLBACK ——
    plan/plan_rev 若和那次违规插入同一事务，会连带消失。分段让每段的提交互不牵连。"""
    with pg_conn() as c, c.cursor() as cur:
        p = mk_plan(cur, seed.actor)
        cur.execute("INSERT INTO plan_rev (plan_id, rev, content_digest, submitted_by)"
                    " VALUES (%s, 1, 'd1', %s)", (p, seed.actor))
    with pg_conn() as c, c.cursor() as cur:
        with pytest.raises(psycopg2.errors.CheckViolation) as ei:
            cur.execute("INSERT INTO plan_submit_skip (plan_id, rev, sku, period_start, reason)"
                        " VALUES (%s, 1, %s, %s, 'because')", (p, seed.sku_a, OCT))
        assert ei.value.diag.constraint_name == "plan_submit_skip_reason_known"
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO plan_submit_skip (plan_id, rev, sku, period_start, reason)"
                    " VALUES (%s, 1, %s, %s, 'zero_purchase')", (p, seed.sku_a, OCT))
        with pytest.raises(psycopg2.errors.RaiseException):     # ★ S-17：只追加
            cur.execute("UPDATE plan_submit_skip SET reason = 'no_claimed_msku'")
