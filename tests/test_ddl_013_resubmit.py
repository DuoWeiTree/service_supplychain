"""013：跳过原因拆空与 0 · 记录换版只走 plan_line_carry · 新版开闸 open_rev_if_live。

★ 每条违规独占一个 `with pg_conn()` 块（同 test_ddl_003 顶部说明）。
"""
import psycopg2.errors
import pytest
from helpers import OCT, advance, mint

from shared.pg_client import pg_conn


def _rev2(cur, plan_id, actor, in_flight=False):
    cur.execute("INSERT INTO plan_rev (plan_id, rev, content_digest, submitted_by, in_flight)"
                " VALUES (%s, 2, 'd2', %s, %s)", (plan_id, actor, in_flight))


def _carry(cur, line_id, plan_id, frm, to, actor):
    cur.execute("INSERT INTO plan_line_carry (line_id, plan_id, from_rev, to_rev, actor, src)"
                " VALUES (%s, %s, %s, %s, %s, 'test')", (line_id, plan_id, frm, to, actor))


def test_skip_reason_now_knows_empty_purchase(seed):
    with pg_conn() as c, c.cursor() as cur:
        pid, _ = mint(cur, seed.actor, seed.sku_a)
        for reason in ("empty_purchase", "zero_purchase", "no_claimed_msku"):
            cur.execute("INSERT INTO plan_submit_skip (plan_id, rev, sku, period_start, reason)"
                        " VALUES (%s, 1, %s, %s, %s)", (pid, seed.sku_b, OCT, reason))
    with pytest.raises(psycopg2.errors.CheckViolation) as ei, pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO plan_submit_skip (plan_id, rev, sku, period_start, reason)"
                    " VALUES (%s, 1, %s, %s, 'empty_or_zero')", (pid, seed.sku_b, OCT))
    assert ei.value.diag.constraint_name == "plan_submit_skip_reason_known"


def test_rev_cannot_be_updated_directly(seed):
    with pg_conn() as c, c.cursor() as cur:
        pid, lid = mint(cur, seed.actor, seed.sku_a)
        _rev2(cur, pid, seed.actor)
    with pytest.raises(psycopg2.errors.RaiseException) as ei, pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE plan_line SET rev = 2 WHERE line_id = %s", (lid,))
    assert "plan_line_carry" in str(ei.value)


def test_carry_moves_the_line_and_releases_the_old_rev(seed):
    with pg_conn() as c, c.cursor() as cur:
        pid, lid = mint(cur, seed.actor, seed.sku_a)
        _rev2(cur, pid, seed.actor)
        _carry(cur, lid, pid, 1, 2, seed.actor)
        cur.execute("SELECT rev, state FROM plan_line WHERE line_id = %s", (lid,))
        assert cur.fetchone() == (2, "已提交")
        cur.execute("SELECT rev, in_flight FROM plan_rev WHERE plan_id = %s ORDER BY rev", (pid,))
        assert cur.fetchall() == [(1, False), (2, False)], "旧版让位；新版等 open_rev_if_live 判"
        cur.execute("SELECT open_rev_if_live(%s, 2)", (pid,))
        assert cur.fetchone()[0] is True
        cur.execute("SELECT in_flight FROM plan_rev WHERE plan_id = %s AND rev = 2", (pid,))
        assert cur.fetchone()[0] is True


def test_carry_must_start_from_the_lines_current_rev(seed):
    with pg_conn() as c, c.cursor() as cur:
        pid, lid = mint(cur, seed.actor, seed.sku_a)
        _rev2(cur, pid, seed.actor)
        cur.execute("INSERT INTO plan_rev (plan_id, rev, content_digest, submitted_by, in_flight)"
                    " VALUES (%s, 3, 'd3', %s, false)", (pid, seed.actor))
    with pytest.raises(psycopg2.errors.RaiseException) as ei, pg_conn() as c, c.cursor() as cur:
        _carry(cur, lid, pid, 2, 3, seed.actor)
    assert "换版不匹配" in str(ei.value)


def test_a_terminal_line_cannot_be_carried(seed):
    with pg_conn() as c, c.cursor() as cur:
        pid, lid = mint(cur, seed.actor, seed.sku_a)
        advance(cur, lid, "已撤销", actor=seed.actor)
        _rev2(cur, pid, seed.actor)
    with pytest.raises(psycopg2.errors.RaiseException) as ei, pg_conn() as c, c.cursor() as cur:
        _carry(cur, lid, pid, 1, 2, seed.actor)
    assert "终态" in str(ei.value)


def test_carry_is_append_only(seed):
    with pg_conn() as c, c.cursor() as cur:
        pid, lid = mint(cur, seed.actor, seed.sku_a)
        _rev2(cur, pid, seed.actor)
        _carry(cur, lid, pid, 1, 2, seed.actor)
    with pytest.raises(psycopg2.errors.RaiseException), pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE plan_line_carry SET src = 'x' WHERE line_id = %s", (lid,))
    with pytest.raises(psycopg2.errors.RaiseException), pg_conn() as c, c.cursor() as cur:
        cur.execute("DELETE FROM plan_line_carry WHERE line_id = %s", (lid,))


def test_open_rev_if_live_leaves_an_empty_rev_closed(seed):
    with pg_conn() as c, c.cursor() as cur:
        pid, lid = mint(cur, seed.actor, seed.sku_a)
        advance(cur, lid, "已撤销", actor=seed.actor)
        _rev2(cur, pid, seed.actor)
        cur.execute("SELECT open_rev_if_live(%s, 2)", (pid,))
        assert cur.fetchone()[0] is False


def test_opening_while_the_old_rev_still_holds_the_slot_is_loud(seed):
    """★ 处置漏了一条 → 旧版没让位 → 撞 S-4 索引。要响，不许吞。"""
    with pg_conn() as c, c.cursor() as cur:
        pid, _ = mint(cur, seed.actor, seed.sku_a)
        _rev2(cur, pid, seed.actor)
        cur.execute("INSERT INTO plan_line (plan_id, rev, sku, period_start, total_units,"
                    " demand_by_seller, demand_at_submit, state)"
                    " VALUES (%s, 2, %s, %s, 5, '{}'::jsonb, 0, '已提交') RETURNING line_id",
                    (pid, seed.sku_b, OCT))
        lid2 = cur.fetchone()[0]
        cur.execute("INSERT INTO plan_line_event (line_id, from_state, to_state, actor, src)"
                    " VALUES (%s, '[*]', '已提交', %s, 'test')", (lid2, seed.actor))
    with pytest.raises(psycopg2.errors.UniqueViolation) as ei, pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT open_rev_if_live(%s, 2)", (pid,))
    assert ei.value.diag.constraint_name == "plan_rev_one_in_flight_idx"
