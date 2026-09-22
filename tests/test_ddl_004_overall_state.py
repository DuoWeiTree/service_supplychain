"""整体状态是**派生**的（04 §3），不是字段。两个边界必须显式定义。"""
from helpers import mint

from shared.pg_client import pg_conn


def test_cancelled_is_not_in_the_rank_table(wipe):
    """★ 已撤销是旁路终态，不参与 rank 比较（04:814-819）。
    把它放进 rank 表，木桶会把一张「撤了一条、其余在跑」的计划算成已撤销。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT state FROM plan_line_state_rank ORDER BY rank")
        assert [r[0] for r in cur.fetchall()] == [
            "已提交", "已确认", "已下单", "准备排货", "已排货", "已完结"]


def test_never_submitted_plan_has_no_overall_state(wipe, seed):
    """★ 「从未提交」不是「已撤销」—— 两者在看板上的处置相反。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO plan (title, period_start, months, owner_actor, created_by)"
                    " VALUES ('空的', '2026-10-01', 3, %s, %s) RETURNING plan_id",
                    (seed.actor, seed.actor))
        pid = cur.fetchone()[0]
        cur.execute("SELECT overall FROM v_plan_overall_state WHERE plan_id = %s", (pid,))
        assert cur.fetchone()[0] is None


def test_all_lines_cancelled_makes_the_plan_cancelled(wipe, seed):
    with pg_conn() as c, c.cursor() as cur:
        plan, line = mint(cur, seed.actor, seed.sku_a)
        cur.execute("INSERT INTO plan_line_event (line_id, from_state, to_state, actor, reason)"
                    " VALUES (%s, '已提交', '已撤销', %s, '不做了')", (line, seed.actor))
        cur.execute("SELECT overall FROM v_plan_overall_state WHERE plan_id = %s", (plan,))
        assert cur.fetchone()[0] == "已撤销"
