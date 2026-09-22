"""测试之间共用的造数。"""
import datetime as dt

OCT = dt.date(2026, 10, 1)


def mint(cur, actor, sku, total=100, state="已提交", birth=True):
    """铸出一条记录：行带 state 插入 + 追加铸出事件（S-2）。

    ★ birth=False 是给「插了行却不记事件」那条测试用的靶子，不是正常路径。
    """
    cur.execute("INSERT INTO plan (title, period_start, months, owner_actor, created_by)"
                " VALUES ('t', %s, 3, %s, %s) RETURNING plan_id", (OCT, actor, actor))
    plan_id = cur.fetchone()[0]
    cur.execute("INSERT INTO plan_rev (plan_id, rev, content_digest, submitted_by)"
                " VALUES (%s, 1, 'd', %s)", (plan_id, actor))
    cur.execute(
        "INSERT INTO plan_line (plan_id, rev, sku, period_start, total_units,"
        " demand_by_seller, demand_at_submit, state)"
        " VALUES (%s, 1, %s, %s, %s, '{}'::jsonb, 0, %s) RETURNING line_id",
        (plan_id, sku, OCT, total, state))
    line_id = cur.fetchone()[0]
    if birth:
        cur.execute("INSERT INTO plan_line_event (line_id, from_state, to_state, actor, src)"
                    " VALUES (%s, '[*]', %s, %s, 'test')", (line_id, state, actor))
    return plan_id, line_id
