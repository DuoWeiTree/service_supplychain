"""005 · 五个审计列的外键由库层担保，不靠接口层自觉。

★ 今天没有脏数据，是因为 `api/ui/deps.actor` 在每次写之前校验了 x-actor。
  但那是**一条路径**的自觉：迁移脚本、阶段 B 的 jobs/、任何绕过 api/ 的写入都不经过它。
  「目前还没人写错」和「写不错」长得一模一样 —— 这个文件把两者分开。
"""
import psycopg2.errors
import pytest
from helpers import mint

from shared.pg_client import pg_conn

GHOST = "ghost-actor"

#: (表, 列, 约束名)。★ 五列一条不落地列出来：漏掉一列，那一列会一直看起来「也有外键」。
AUDIT_COLUMNS = [
    ("msku_claim", "claimed_by", "msku_claim_claimed_by_fk"),
    ("msku_claim", "released_by", "msku_claim_released_by_fk"),
    ("plan_demand_cell", "updated_by", "plan_demand_cell_updated_by_fk"),
    ("plan_purchase_cell", "updated_by", "plan_purchase_cell_updated_by_fk"),
    ("plan_line_event", "actor", "plan_line_event_actor_fk"),
]


@pytest.mark.parametrize("table,column,constraint", AUDIT_COLUMNS)
def test_every_audit_column_has_its_foreign_key(wipe, table, column, constraint):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT con.conname, a.attname"
            "  FROM pg_constraint con"
            "  JOIN pg_class t ON t.oid = con.conrelid"
            "  JOIN pg_namespace n ON n.oid = t.relnamespace"
            "  JOIN unnest(con.conkey) k(attnum) ON true"
            "  JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum"
            " WHERE n.nspname = current_schema() AND t.relname = %s"
            "   AND con.contype = 'f' AND con.conname = %s", (table, constraint))
        rows = cur.fetchall()
    assert rows == [(constraint, column)], f"{table}.{column} 的外键不在库里：{rows}"


def test_a_bogus_actor_cannot_claim(wipe, seed):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO plan (title, period_start, months, owner_actor, created_by)"
                    " VALUES ('t', '2026-10-01', 3, %s, %s) RETURNING plan_id",
                    (seed.actor, seed.actor))
        pid = cur.fetchone()[0]
    with pytest.raises(psycopg2.errors.ForeignKeyViolation) as ei, \
            pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO msku_claim (plan_id, seller_sku, sid, claimed_by)"
                    " VALUES (%s, %s, %s, %s)", (pid, *seed.msku_a, GHOST))
    assert ei.value.diag.constraint_name == "msku_claim_claimed_by_fk"


def test_a_bogus_actor_cannot_release(wipe, seed):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO plan (title, period_start, months, owner_actor, created_by)"
                    " VALUES ('t', '2026-10-01', 3, %s, %s) RETURNING plan_id",
                    (seed.actor, seed.actor))
        pid = cur.fetchone()[0]
        cur.execute("INSERT INTO msku_claim (plan_id, seller_sku, sid, claimed_by)"
                    " VALUES (%s, %s, %s, %s)", (pid, *seed.msku_a, seed.actor))
    with pytest.raises(psycopg2.errors.ForeignKeyViolation) as ei, \
            pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE msku_claim SET released_at = now(), released_by = %s"
                    " WHERE plan_id = %s", (GHOST, pid))
    assert ei.value.diag.constraint_name == "msku_claim_released_by_fk"


def test_a_bogus_actor_cannot_touch_either_kind_of_cell(wipe, seed):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO plan (title, period_start, months, owner_actor, created_by)"
                    " VALUES ('t', '2026-10-01', 3, %s, %s) RETURNING plan_id",
                    (seed.actor, seed.actor))
        pid = cur.fetchone()[0]
        cur.execute("INSERT INTO msku_claim (plan_id, seller_sku, sid, claimed_by)"
                    " VALUES (%s, %s, %s, %s)", (pid, *seed.msku_a, seed.actor))
    with pytest.raises(psycopg2.errors.ForeignKeyViolation) as ei, \
            pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO plan_demand_cell (plan_id, seller_sku, sid, period_start,"
                    " updated_by) VALUES (%s, %s, %s, '2026-10-01', %s)",
                    (pid, *seed.msku_a, GHOST))
    assert ei.value.diag.constraint_name == "plan_demand_cell_updated_by_fk"
    with pytest.raises(psycopg2.errors.ForeignKeyViolation) as ei, \
            pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO plan_purchase_cell (plan_id, sku, period_start, updated_by)"
                    " VALUES (%s, %s, '2026-10-01', %s)", (pid, seed.sku_a, GHOST))
    assert ei.value.diag.constraint_name == "plan_purchase_cell_updated_by_fk"


def test_a_bogus_actor_cannot_push_a_state(wipe, seed):
    """★ 事件表只追加（003 的 t_event_append_only）：actor 写错了连改都改不了，
    所以它比其余几列更必须在写入当下就被挡住。"""
    with pg_conn() as c, c.cursor() as cur:
        _, line = mint(cur, seed.actor, seed.sku_a)
    with pytest.raises(psycopg2.errors.ForeignKeyViolation) as ei, \
            pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO plan_line_event (line_id, from_state, to_state, actor, reason)"
                    " VALUES (%s, '已提交', '已撤销', %s, '不做了')", (line, GHOST))
    assert ei.value.diag.constraint_name == "plan_line_event_actor_fk"


def test_a_real_actor_still_gets_through(wipe, seed):
    """★ 新守卫「抓到真问题」与「误伤好东西」同形 —— 在职的人必须照常写得进去。"""
    with pg_conn() as c, c.cursor() as cur:
        _, line = mint(cur, seed.actor, seed.sku_a)
        cur.execute("INSERT INTO plan_line_event (line_id, from_state, to_state, actor, reason)"
                    " VALUES (%s, '已提交', '已撤销', %s, '不做了')", (line, seed.actor))
        cur.execute("SELECT state FROM plan_line WHERE line_id = %s", (line,))
        assert cur.fetchone()[0] == "已撤销"


def test_a_deactivated_actor_is_still_a_valid_reference(wipe, seed):
    """★ 外键管的是「这个人存在过」，不是「他今天还在职」——
    停用的人留下的留痕不许因此失效（在职校验在 api/ui/deps.py，两件事）。"""
    with pg_conn() as c, c.cursor() as cur:
        _, line = mint(cur, seed.actor, seed.sku_a)
        cur.execute("INSERT INTO plan_line_event (line_id, from_state, to_state, actor, reason)"
                    " VALUES (%s, '已提交', '已撤销', %s, '离职前撤的')",
                    (line, seed.actor_inactive))
