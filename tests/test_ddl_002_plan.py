"""002 的约束挡得住吗。每条都插一行违反的，断言具体是哪个约束名。

★ 全部违规行测试都遵循同一个事务结构：违规插入要么整段包在
  `pytest.raises(..., pg_conn() as c, ...)` 里（异常直接穿出 `with pg_conn()`），
  要么先在一个独立、干净提交的块里把前置数据建好，再另起一个块触发违规。
  绝不在同一个 `with pg_conn()` 块内先用嵌套 `pytest.raises` 吞掉一次库层异常、
  再让块正常退出继续做别的事 —— `pg_conn()` 现在会对着一个 aborted 事务的正常退出
  主动报错（见 `shared/pg_client.py` 与 `tests/test_pg_client.py`），这类写法会撞上它，
  而这正是它存在的意义：不允许调用方在悄悄吞掉异常后继续假装事务完好。
"""
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


def test_created_by_must_be_a_known_actor(wipe, seed):
    with pytest.raises(psycopg2.errors.ForeignKeyViolation) as ei, pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO plan (title, period_start, months, owner_actor, created_by)"
                    " VALUES (%s, %s, %s, %s, %s)", ("标题", OCT, 3, seed.actor, "nobody"))
    assert ei.value.diag.constraint_name == "plan_created_by_fk"


def test_plan_pkey_rejects_duplicate_id(wipe, seed):
    with pg_conn() as c, c.cursor() as cur:
        p = mk_plan(cur, seed.actor)
    with pytest.raises(psycopg2.errors.UniqueViolation) as ei, pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO plan (plan_id, title, period_start, months, owner_actor, created_by)"
                    " VALUES (%s, %s, %s, %s, %s, %s)", (p, "撞键", OCT, 3, seed.actor, seed.actor))
    assert ei.value.diag.constraint_name == "plan_pkey"


def test_one_active_claim_per_msku_across_plans(wipe, seed):
    """★ 判据③ 的库层那一半：独占由部分唯一索引裁决，不靠先查后写。"""
    with pg_conn() as c, c.cursor() as cur:
        p1 = mk_plan(cur, seed.actor)
        p2 = mk_plan(cur, seed.actor, title="另一张")
        cur.execute("INSERT INTO msku_claim (plan_id, seller_sku, sid, claimed_by)"
                    " VALUES (%s, %s, %s, %s)", (p1, *seed.msku_a, seed.actor))
    with pytest.raises(psycopg2.errors.UniqueViolation) as ei, pg_conn() as c, c.cursor() as cur:
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


def test_msku_claim_pkey_rejects_duplicate_claim_row(wipe, seed):
    """★ 与部分唯一索引分开验证：释放掉旧行后它不再挡新占用，
    但 (plan_id, seller_sku, sid) 这个主键组合本身还是不能重复插入。"""
    with pg_conn() as c, c.cursor() as cur:
        p = mk_plan(cur, seed.actor)
        cur.execute("INSERT INTO msku_claim (plan_id, seller_sku, sid, claimed_by)"
                    " VALUES (%s, %s, %s, %s)", (p, *seed.msku_a, seed.actor))
        cur.execute("UPDATE msku_claim SET released_at = now(), released_by = %s"
                    " WHERE plan_id = %s", (seed.actor, p))
    with pytest.raises(psycopg2.errors.UniqueViolation) as ei, pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO msku_claim (plan_id, seller_sku, sid, claimed_by)"
                    " VALUES (%s, %s, %s, %s)", (p, *seed.msku_a, seed.actor))
    assert ei.value.diag.constraint_name == "msku_claim_pkey"


def test_demand_cell_requires_a_claim_and_a_bridge_row(wipe, seed):
    """★ 没认领就没格子。"""
    with pg_conn() as c, c.cursor() as cur:
        p = mk_plan(cur, seed.actor)
    with pytest.raises(psycopg2.errors.ForeignKeyViolation) as ei, pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO plan_demand_cell (plan_id, seller_sku, sid, period_start)"
                    " VALUES (%s, %s, %s, %s)", (p, *seed.msku_a, OCT))
    assert ei.value.diag.constraint_name == "plan_demand_cell_claim_fk"


def test_demand_cell_bridge_fk_is_checked_even_with_a_claim(wipe, seed):
    """★ claim_fk 与 bridge_fk 是两条独立的外键；msku_claim 本身不检查 msku_bridge，
    所以能造出「已认领但 bridge 里没有这一行」的格子来单独验证 bridge_fk（而不是靠
    claim_fk 顺带盖住它）。"""
    ghost = ("MSKU-GHOST", seed.seller_a)
    with pg_conn() as c, c.cursor() as cur:
        p = mk_plan(cur, seed.actor)
        cur.execute("INSERT INTO msku_claim (plan_id, seller_sku, sid, claimed_by)"
                    " VALUES (%s, %s, %s, %s)", (p, *ghost, seed.actor))
    with pytest.raises(psycopg2.errors.ForeignKeyViolation) as ei, pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO plan_demand_cell (plan_id, seller_sku, sid, period_start)"
                    " VALUES (%s, %s, %s, %s)", (p, *ghost, OCT))
    assert ei.value.diag.constraint_name == "plan_demand_cell_bridge_fk"


def test_plan_demand_cell_pkey_rejects_duplicate(wipe, seed):
    with pg_conn() as c, c.cursor() as cur:
        p = mk_plan(cur, seed.actor)
        cur.execute("INSERT INTO msku_claim (plan_id, seller_sku, sid, claimed_by)"
                    " VALUES (%s, %s, %s, %s)", (p, *seed.msku_a, seed.actor))
        cur.execute("INSERT INTO plan_demand_cell (plan_id, seller_sku, sid, period_start)"
                    " VALUES (%s, %s, %s, %s)", (p, *seed.msku_a, OCT))
    with pytest.raises(psycopg2.errors.UniqueViolation) as ei, pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO plan_demand_cell (plan_id, seller_sku, sid, period_start)"
                    " VALUES (%s, %s, %s, %s)", (p, *seed.msku_a, OCT))
    assert ei.value.diag.constraint_name == "plan_demand_cell_pkey"


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
    with pytest.raises(psycopg2.errors.CheckViolation) as ei, pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE plan_demand_cell SET expected_units = -1 WHERE plan_id = %s", (p,))
    assert ei.value.diag.constraint_name == "plan_demand_cell_expected_nonneg"


def test_demand_cell_system_units_nonneg(wipe, seed):
    with pg_conn() as c, c.cursor() as cur:
        p = mk_plan(cur, seed.actor)
        cur.execute("INSERT INTO msku_claim (plan_id, seller_sku, sid, claimed_by)"
                    " VALUES (%s, %s, %s, %s)", (p, *seed.msku_a, seed.actor))
    with pytest.raises(psycopg2.errors.CheckViolation) as ei, pg_conn() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO plan_demand_cell (plan_id, seller_sku, sid, period_start, system_units)"
            " VALUES (%s, %s, %s, %s, -1)", (p, *seed.msku_a, OCT))
    assert ei.value.diag.constraint_name == "plan_demand_cell_system_nonneg"


def test_extrapolated_flag_requires_a_system_units_value(wipe, seed):
    """★ M-13：外推标记没有配套的值，就是「标记了却没数」—— 正是它要防的反面情形
    （docs/03 §7.1 同名列已有先例 ck_cell_extrapolated，这里照抄到 §2.1）。"""
    with pg_conn() as c, c.cursor() as cur:
        p = mk_plan(cur, seed.actor)
        cur.execute("INSERT INTO msku_claim (plan_id, seller_sku, sid, claimed_by)"
                    " VALUES (%s, %s, %s, %s)", (p, *seed.msku_a, seed.actor))
    with pytest.raises(psycopg2.errors.CheckViolation) as ei, pg_conn() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO plan_demand_cell (plan_id, seller_sku, sid, period_start, system_extrapolated)"
            " VALUES (%s, %s, %s, %s, true)", (p, *seed.msku_a, OCT))
    assert ei.value.diag.constraint_name == "plan_demand_cell_extrapolated_has_value"


def test_purchase_cell_planned_units_nonneg(wipe, seed):
    with pg_conn() as c, c.cursor() as cur:
        p = mk_plan(cur, seed.actor)
    with pytest.raises(psycopg2.errors.CheckViolation) as ei, pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO plan_purchase_cell (plan_id, sku, period_start, planned_units)"
                    " VALUES (%s, %s, %s, -1)", (p, seed.sku_a, OCT))
    assert ei.value.diag.constraint_name == "plan_purchase_cell_nonneg"


def test_purchase_cell_pkey_rejects_duplicate(wipe, seed):
    with pg_conn() as c, c.cursor() as cur:
        p = mk_plan(cur, seed.actor)
        cur.execute("INSERT INTO plan_purchase_cell (plan_id, sku, period_start)"
                    " VALUES (%s, %s, %s)", (p, seed.sku_a, OCT))
    with pytest.raises(psycopg2.errors.UniqueViolation) as ei, pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO plan_purchase_cell (plan_id, sku, period_start)"
                    " VALUES (%s, %s, %s)", (p, seed.sku_a, OCT))
    assert ei.value.diag.constraint_name == "plan_purchase_cell_pkey"


def test_plan_rev_pkey_rejects_duplicate_rev(wipe, seed):
    with pg_conn() as c, c.cursor() as cur:
        p = mk_plan(cur, seed.actor)
        cur.execute("INSERT INTO plan_rev (plan_id, rev, content_digest, submitted_by)"
                    " VALUES (%s, 1, 'd1', %s)", (p, seed.actor))
    with pytest.raises(psycopg2.errors.UniqueViolation) as ei, pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO plan_rev (plan_id, rev, content_digest, submitted_by)"
                    " VALUES (%s, 1, 'd2', %s)", (p, seed.actor))
    assert ei.value.diag.constraint_name == "plan_rev_pkey"


def test_one_current_rev_per_plan(wipe, seed):
    """★ 单独验证 is_current 那条部分唯一索引 —— 两行都设 in_flight=false，
    避免和 plan_rev_one_in_flight_idx 混在一起报错。"""
    with pg_conn() as c, c.cursor() as cur:
        p = mk_plan(cur, seed.actor)
        cur.execute(
            "INSERT INTO plan_rev (plan_id, rev, content_digest, is_current, in_flight, submitted_by)"
            " VALUES (%s, 1, 'd1', true, false, %s)", (p, seed.actor))
    with pytest.raises(psycopg2.errors.UniqueViolation) as ei, pg_conn() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO plan_rev (plan_id, rev, content_digest, is_current, in_flight, submitted_by)"
            " VALUES (%s, 2, 'd2', true, false, %s)", (p, seed.actor))
    assert ei.value.diag.constraint_name == "plan_rev_one_current_idx"


def test_one_current_and_one_in_flight_rev_per_plan(wipe, seed):
    """★ S-4：同一计划最多 1 版在流转 —— 由库层裁决，不靠接口先查后写。"""
    with pg_conn() as c, c.cursor() as cur:
        p = mk_plan(cur, seed.actor)
        cur.execute("INSERT INTO plan_rev (plan_id, rev, content_digest, is_current, submitted_by)"
                    " VALUES (%s, 1, 'd1', true, %s)", (p, seed.actor))
    with pytest.raises(psycopg2.errors.UniqueViolation) as ei, pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO plan_rev (plan_id, rev, content_digest, submitted_by)"
                    " VALUES (%s, 2, 'd2', %s)", (p, seed.actor))
    assert ei.value.diag.constraint_name == "plan_rev_one_in_flight_idx"


def test_in_flight_rev_frees_the_slot_once_set_false(wipe, seed):
    """★ 双向证明的另一半，照 msku_claim 的解禁验证方式：
    把在流转的旧版落定（in_flight=false）之后，新版才能提交。"""
    with pg_conn() as c, c.cursor() as cur:
        p = mk_plan(cur, seed.actor)
        cur.execute("INSERT INTO plan_rev (plan_id, rev, content_digest, is_current, submitted_by)"
                    " VALUES (%s, 1, 'd1', true, %s)", (p, seed.actor))
        cur.execute("UPDATE plan_rev SET in_flight = false WHERE plan_id = %s AND rev = 1", (p,))
        cur.execute("INSERT INTO plan_rev (plan_id, rev, content_digest, submitted_by)"
                    " VALUES (%s, 2, 'd2', %s)", (p, seed.actor))
        cur.execute("SELECT count(*) FROM plan_rev WHERE plan_id = %s", (p,))
        assert cur.fetchone()[0] == 2, "★ 旧版落定后不占位了 —— 新版才提交得进去"


def test_plan_line_total_units_must_be_positive(wipe, seed):
    """★ 坑①：总量 0 会因为 0 = 0 恒真，一诞生就自动跳到「已确认」。"""
    with pg_conn() as c, c.cursor() as cur:
        p = mk_plan(cur, seed.actor)
        cur.execute("INSERT INTO plan_rev (plan_id, rev, content_digest, submitted_by)"
                    " VALUES (%s, 1, 'd1', %s)", (p, seed.actor))
    with pytest.raises(psycopg2.errors.CheckViolation) as ei, pg_conn() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO plan_line (plan_id, rev, sku, period_start, total_units,"
            " demand_by_seller, demand_at_submit, state)"
            " VALUES (%s, 1, %s, %s, 0, '{}'::jsonb, 0, '已提交')", (p, seed.sku_a, OCT))
    assert ei.value.diag.constraint_name == "plan_line_total_positive"


def test_plan_line_demand_at_submit_nonneg(wipe, seed):
    with pg_conn() as c, c.cursor() as cur:
        p = mk_plan(cur, seed.actor)
        cur.execute("INSERT INTO plan_rev (plan_id, rev, content_digest, submitted_by)"
                    " VALUES (%s, 1, 'd1', %s)", (p, seed.actor))
    with pytest.raises(psycopg2.errors.CheckViolation) as ei, pg_conn() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO plan_line (plan_id, rev, sku, period_start, total_units,"
            " demand_by_seller, demand_at_submit, state)"
            " VALUES (%s, 1, %s, %s, 5, '{}'::jsonb, -1, '已提交')", (p, seed.sku_a, OCT))
    assert ei.value.diag.constraint_name == "plan_line_demand_nonneg"


def test_plan_line_pkey_rejects_duplicate_id(wipe, seed):
    with pg_conn() as c, c.cursor() as cur:
        p = mk_plan(cur, seed.actor)
        cur.execute("INSERT INTO plan_rev (plan_id, rev, content_digest, submitted_by)"
                    " VALUES (%s, 1, 'd1', %s)", (p, seed.actor))
        cur.execute(
            "INSERT INTO plan_line (plan_id, rev, sku, period_start, total_units,"
            " demand_by_seller, demand_at_submit, state)"
            " VALUES (%s, 1, %s, %s, 5, '{}'::jsonb, 0, '已提交') RETURNING line_id",
            (p, seed.sku_a, OCT))
        line_id = cur.fetchone()[0]
    with pytest.raises(psycopg2.errors.UniqueViolation) as ei, pg_conn() as c, c.cursor() as cur:
        # ★ 换一个 sku 避免同时撞上 plan_line_one_per_cell，纯粹只测 line_id 主键
        cur.execute(
            "INSERT INTO plan_line (line_id, plan_id, rev, sku, period_start, total_units,"
            " demand_by_seller, demand_at_submit, state)"
            " VALUES (%s, %s, 1, %s, %s, 5, '{}'::jsonb, 0, '已提交')",
            (line_id, p, seed.sku_b, OCT))
    assert ei.value.diag.constraint_name == "plan_line_pkey"


def test_plan_line_one_per_cell(wipe, seed):
    with pg_conn() as c, c.cursor() as cur:
        p = mk_plan(cur, seed.actor)
        cur.execute("INSERT INTO plan_rev (plan_id, rev, content_digest, submitted_by)"
                    " VALUES (%s, 1, 'd1', %s)", (p, seed.actor))
        cur.execute(
            "INSERT INTO plan_line (plan_id, rev, sku, period_start, total_units,"
            " demand_by_seller, demand_at_submit, state)"
            " VALUES (%s, 1, %s, %s, 5, '{}'::jsonb, 0, '已提交')", (p, seed.sku_a, OCT))
    with pytest.raises(psycopg2.errors.UniqueViolation) as ei, pg_conn() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO plan_line (plan_id, rev, sku, period_start, total_units,"
            " demand_by_seller, demand_at_submit, state)"
            " VALUES (%s, 1, %s, %s, 9, '{}'::jsonb, 0, '已提交')", (p, seed.sku_a, OCT))
    assert ei.value.diag.constraint_name == "plan_line_one_per_cell"


def test_plan_line_rev_must_exist(wipe, seed):
    with pg_conn() as c, c.cursor() as cur:
        p = mk_plan(cur, seed.actor)
    with pytest.raises(psycopg2.errors.ForeignKeyViolation) as ei, pg_conn() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO plan_line (plan_id, rev, sku, period_start, total_units,"
            " demand_by_seller, demand_at_submit, state)"
            " VALUES (%s, 1, %s, %s, 5, '{}'::jsonb, 0, '已提交')", (p, seed.sku_a, OCT))
    assert ei.value.diag.constraint_name == "plan_line_rev_fk"


def test_plan_line_has_no_seller_id(wipe):
    """★ P2 / S-3：记录是货号级，店铺归属在组单时由采购员决定（po_source_map）。
    列还在的话，组单时会出现两个真相。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT column_name FROM information_schema.columns"
                    " WHERE table_schema = current_schema() AND table_name = 'plan_line'")
        cols = {r[0] for r in cur.fetchall()}
    assert "seller_id" not in cols
    assert {"demand_by_seller", "demand_at_submit"} <= cols, "S-6：冻结的两列必须在"


def test_plan_submit_skip_pkey_rejects_duplicate_id(wipe, seed):
    with pg_conn() as c, c.cursor() as cur:
        p = mk_plan(cur, seed.actor)
        cur.execute("INSERT INTO plan_rev (plan_id, rev, content_digest, submitted_by)"
                    " VALUES (%s, 1, 'd1', %s)", (p, seed.actor))
        cur.execute(
            "INSERT INTO plan_submit_skip (plan_id, rev, sku, period_start, reason)"
            " VALUES (%s, 1, %s, %s, 'zero_purchase') RETURNING skip_id",
            (p, seed.sku_a, OCT))
        skip_id = cur.fetchone()[0]
    with pytest.raises(psycopg2.errors.UniqueViolation) as ei, pg_conn() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO plan_submit_skip (skip_id, plan_id, rev, sku, period_start, reason)"
            " VALUES (%s, %s, 1, %s, %s, 'no_claimed_msku')",
            (skip_id, p, seed.sku_b, OCT))
    assert ei.value.diag.constraint_name == "plan_submit_skip_pkey"


def test_plan_submit_skip_rev_must_exist(wipe, seed):
    with pg_conn() as c, c.cursor() as cur:
        p = mk_plan(cur, seed.actor)
    with pytest.raises(psycopg2.errors.ForeignKeyViolation) as ei, pg_conn() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO plan_submit_skip (plan_id, rev, sku, period_start, reason)"
            " VALUES (%s, 1, %s, %s, 'zero_purchase')", (p, seed.sku_a, OCT))
    assert ei.value.diag.constraint_name == "plan_submit_skip_rev_fk"


def test_skip_reason_is_a_closed_set_and_append_only(wipe, seed):
    """★ 每段各起一个 pg_conn：一次库层异常会让整个事务进入 aborted 状态，
    `pg_conn()` 现在会对「块内吞异常后正常退出」主动报错（见 test_pg_client.py），
    所以每次「验证一个会失败的操作」都要让异常整段穿出 `with pg_conn()`，
    不能在块内捕获后继续用同一个游标做别的事。"""
    with pg_conn() as c, c.cursor() as cur:
        p = mk_plan(cur, seed.actor)
        cur.execute("INSERT INTO plan_rev (plan_id, rev, content_digest, submitted_by)"
                    " VALUES (%s, 1, 'd1', %s)", (p, seed.actor))
    with pytest.raises(psycopg2.errors.CheckViolation) as ei, pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO plan_submit_skip (plan_id, rev, sku, period_start, reason)"
                    " VALUES (%s, 1, %s, %s, 'because')", (p, seed.sku_a, OCT))
    assert ei.value.diag.constraint_name == "plan_submit_skip_reason_known"
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO plan_submit_skip (plan_id, rev, sku, period_start, reason)"
                    " VALUES (%s, 1, %s, %s, 'zero_purchase')", (p, seed.sku_a, OCT))
    with pytest.raises(psycopg2.errors.RaiseException), pg_conn() as c, c.cursor() as cur:  # ★ S-17：只追加
        cur.execute("UPDATE plan_submit_skip SET reason = 'no_claimed_msku' WHERE plan_id = %s", (p,))
    with pytest.raises(psycopg2.errors.RaiseException), pg_conn() as c, c.cursor() as cur:
        cur.execute("DELETE FROM plan_submit_skip WHERE plan_id = %s", (p,))
