"""状态机四件套。T1/T2/T3/T6/T21 出自 04 §9 的「必须先红一次」清单。

★ 每条违规插入独占一个 `with pg_conn()` 块，`pytest.raises` 包住整个块
  （不嵌套在块内部）：`pg_conn()` 现在会对着一个 aborted 事务的正常退出主动
  报错（见 test_ddl_002_plan.py 顶部说明 / test_pg_client.py），先建好前置数据
  的部分单独起一个干净提交的块。
"""
import psycopg2.errors
import pytest
from helpers import mint

from shared.pg_client import pg_conn

#: ★ T21 的靶子：把 04 §7.1 / 03 §3 的行表**逐字**抄在这里当断言。
#: 没有它，「状态机是数据」只兑现一半 —— 库里是数据，文档里还是另一份手抄副本，
#: 而两份副本一定会分叉。
WHITELIST = [
    ("[*]", "已提交", "forward", False),
    ("已提交", "已确认", "forward", False),
    ("已确认", "已下单", "forward", False),
    ("已下单", "准备排货", "forward", False),
    ("准备排货", "已排货", "forward", False),
    ("已排货", "已完结", "forward", False),
    ("已确认", "已提交", "back", False),
    ("已提交", "已撤销", "cancel", True),
    ("已确认", "已撤销", "cancel", True),
    ("已下单", "已撤销", "cancel", True),
    ("准备排货", "已撤销", "cancel", True),
    ("已排货", "已撤销", "cancel", True),
]


def test_t21_whitelist_matches_the_document_row_by_row(wipe):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT from_state, to_state, kind, needs_reason FROM plan_line_transition"
                    " ORDER BY from_state, to_state")
        got = cur.fetchall()
    assert sorted(got) == sorted(WHITELIST)


def test_only_one_back_edge_exists(wipe):
    """★ 04:800：back 只有一行。多一行就要重新论证「状态只前进」还成不成立。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT from_state, to_state FROM plan_line_transition WHERE kind = 'back'")
        assert cur.fetchall() == [("已确认", "已提交")]


def test_terminal_states_never_appear_as_from_state(wipe):
    """★ 终态不可离开，由外键裁决 —— 表里没有以它们作 from_state 的行。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM plan_line_transition"
                    " WHERE from_state IN ('已完结','已撤销')")
        assert cur.fetchone()[0] == 0


def test_t1_illegal_transition_is_a_foreign_key_violation(wipe, seed):
    with pg_conn() as c, c.cursor() as cur:
        _, line = mint(cur, seed.actor, seed.sku_a)
    with pytest.raises(psycopg2.errors.ForeignKeyViolation) as ei, \
            pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO plan_line_event (line_id, from_state, to_state, actor)"
                    " VALUES (%s, '已提交', '已排货', %s)", (line, seed.actor))
    assert ei.value.diag.constraint_name == "plan_line_event_transition_fk"


def test_t2_terminal_state_cannot_be_left(wipe, seed):
    with pg_conn() as c, c.cursor() as cur:
        _, line = mint(cur, seed.actor, seed.sku_a)
        cur.execute("INSERT INTO plan_line_event (line_id, from_state, to_state, actor, reason)"
                    " VALUES (%s, '已提交', '已撤销', %s, '不做了')", (line, seed.actor))
        cur.execute("SELECT state FROM plan_line WHERE line_id = %s", (line,))
        assert cur.fetchone()[0] == "已撤销"
    with pytest.raises(psycopg2.errors.ForeignKeyViolation), \
            pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO plan_line_event (line_id, from_state, to_state, actor)"
                    " VALUES (%s, '已撤销', '已确认', %s)", (line, seed.actor))


def test_t3_direct_update_of_state_is_refused(wipe, seed):
    with pg_conn() as c, c.cursor() as cur:
        _, line = mint(cur, seed.actor, seed.sku_a)
    with pytest.raises(psycopg2.errors.RaiseException) as ei, \
            pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE plan_line SET state = '已确认' WHERE line_id = %s", (line,))
    assert "只能由事件表推进" in str(ei.value)


def test_t6_total_zero_never_exists_so_it_cannot_auto_advance(wipe, seed):
    """★ 坑①：量为 0 的记录 0 = 0 恒真，一诞生就该是「已确认」——
    所以它根本不许存在（002 的 CHECK），这里从状态机这一侧再确认一次。"""
    with pytest.raises(psycopg2.errors.CheckViolation), \
            pg_conn() as c, c.cursor() as cur:
        mint(cur, seed.actor, seed.sku_a, total=0)


def test_event_table_is_append_only(wipe, seed):
    with pg_conn() as c, c.cursor() as cur:
        _, line = mint(cur, seed.actor, seed.sku_a)
    with pytest.raises(psycopg2.errors.RaiseException), \
            pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE plan_line_event SET actor = 'x' WHERE line_id = %s", (line,))
    with pytest.raises(psycopg2.errors.RaiseException), \
            pg_conn() as c, c.cursor() as cur:
        cur.execute("DELETE FROM plan_line_event WHERE line_id = %s", (line,))


def test_line_without_birth_event_is_refused_at_commit(wipe, seed):
    """★ S-2：插了行却不记事件 = 静默绕过审计链。延迟约束在 COMMIT 时抓它 ——
    不是在 INSERT 那一刻：同一事务里 INSERT 之后立刻查得到那一行，证明
    c_line_birth_event 确实是 DEFERRABLE INITIALLY DEFERRED，直到 commit 才拦。

    ★ 断言走 diag.message_primary，不走 str(e)：实测这台实例上 commit 阶段抛出的
      RaiseException，str(e) 把中文解码乱了（诊断字段走 PQresultErrorField，
      不受影响），与迁移本身无关，是 psycopg2 对 commit 期错误的已知解码差异。
    """
    with pytest.raises(psycopg2.errors.RaiseException) as ei, pg_conn() as c, c.cursor() as cur:
        _, line = mint(cur, seed.actor, seed.sku_a, birth=False)
        # ★ 还没 commit：INSERT 本身没被拦，行已经在事务里可见了。
        cur.execute("SELECT count(*) FROM plan_line WHERE line_id = %s", (line,))
        assert cur.fetchone()[0] == 1, "INSERT 那一刻不该被拦——拦截应该在 commit"
    assert "没有铸出事件" in ei.value.diag.message_primary


def test_birth_event_must_agree_with_the_row_it_mints(wipe, seed):
    """行带 '已确认' 插入 + 铸出事件（from_state='[*]'）→ 必须炸：铸出只能落在「已提交」。

    ★ 造数不能走 mint(state="已确认")：那会同时把铸出事件的 to_state 也改成
      「已确认」，而 ('[*]','已确认') 本身就不在白名单里 —— 会先炸成外键违反，
      测不到 sync_plan_line_state() 里那句「铸出事件要求行处于 已提交」。
      这里手工插行（state='已确认'）+ 手工插一条 to_state='已提交' 的铸出事件，
      让外键先过、专门逼到状态不一致这一支。
    ★ 两条 INSERT 必须在同一个事务/同一个 pg_conn 块里：拆成两块的话，第一块
      单独提交时会先撞上 c_line_birth_event（延迟约束）—— 那一刻同一事务里
      还没有任何铸出事件，会被那条约束拦下而不是测到这里要测的分支。
    """
    with pytest.raises(psycopg2.errors.RaiseException) as ei, \
            pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO plan (title, period_start, months, owner_actor, created_by)"
                    " VALUES ('t', '2026-10-01', 3, %s, %s) RETURNING plan_id", (seed.actor, seed.actor))
        plan_id = cur.fetchone()[0]
        cur.execute("INSERT INTO plan_rev (plan_id, rev, content_digest, submitted_by)"
                    " VALUES (%s, 1, 'd', %s)", (plan_id, seed.actor))
        cur.execute(
            "INSERT INTO plan_line (plan_id, rev, sku, period_start, total_units,"
            " demand_by_seller, demand_at_submit, state)"
            " VALUES (%s, 1, %s, '2026-10-01', 100, '{}'::jsonb, 0, '已确认') RETURNING line_id",
            (plan_id, seed.sku_a))
        line_id = cur.fetchone()[0]
        cur.execute("INSERT INTO plan_line_event (line_id, from_state, to_state, actor, src)"
                    " VALUES (%s, '[*]', '已提交', %s, 'test')", (line_id, seed.actor))
    assert "铸出事件要求行处于 已提交" in str(ei.value)


def test_from_state_mismatch_is_refused(wipe, seed):
    """乐观校验：事件声称的 from_state 与行的当前状态不符 → 硬失败。"""
    with pg_conn() as c, c.cursor() as cur:
        _, line = mint(cur, seed.actor, seed.sku_a)
    with pytest.raises(psycopg2.errors.RaiseException) as ei, \
            pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO plan_line_event (line_id, from_state, to_state, actor)"
                    " VALUES (%s, '已确认', '已下单', %s)", (line, seed.actor))
    assert "from_state 不匹配" in str(ei.value)


def test_cancel_without_reason_is_refused(wipe, seed):
    with pg_conn() as c, c.cursor() as cur:
        _, line = mint(cur, seed.actor, seed.sku_a)
    with pytest.raises(psycopg2.errors.RaiseException) as ei, \
            pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO plan_line_event (line_id, from_state, to_state, actor)"
                    " VALUES (%s, '已提交', '已撤销', %s)", (line, seed.actor))
    assert "必须写理由" in str(ei.value)


def test_rev_leaves_in_flight_when_every_line_is_terminal(wipe, seed):
    with pg_conn() as c, c.cursor() as cur:
        plan, line = mint(cur, seed.actor, seed.sku_a)
        cur.execute("SELECT in_flight FROM plan_rev WHERE plan_id = %s", (plan,))
        assert cur.fetchone()[0] is True
        cur.execute("INSERT INTO plan_line_event (line_id, from_state, to_state, actor, reason)"
                    " VALUES (%s, '已提交', '已撤销', %s, '不做了')", (line, seed.actor))
        cur.execute("SELECT in_flight FROM plan_rev WHERE plan_id = %s", (plan,))
        assert cur.fetchone()[0] is False, "★ 全部记录进终态了，这一版还占着在流转位"


def test_rev_stays_in_flight_while_one_line_is_not_terminal(wipe, seed):
    """★ in_flight 双向证明的另一半：两行里只要有一行没到终态，版就不能落定。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO plan (title, period_start, months, owner_actor, created_by)"
                    " VALUES ('t', '2026-10-01', 3, %s, %s) RETURNING plan_id", (seed.actor, seed.actor))
        plan_id = cur.fetchone()[0]
        cur.execute("INSERT INTO plan_rev (plan_id, rev, content_digest, submitted_by)"
                    " VALUES (%s, 1, 'd', %s)", (plan_id, seed.actor))
        cur.execute(
            "INSERT INTO plan_line (plan_id, rev, sku, period_start, total_units,"
            " demand_by_seller, demand_at_submit, state)"
            " VALUES (%s, 1, %s, '2026-10-01', 100, '{}'::jsonb, 0, '已提交') RETURNING line_id",
            (plan_id, seed.sku_a))
        line_a = cur.fetchone()[0]
        cur.execute("INSERT INTO plan_line_event (line_id, from_state, to_state, actor, src)"
                    " VALUES (%s, '[*]', '已提交', %s, 'test')", (line_a, seed.actor))
        cur.execute(
            "INSERT INTO plan_line (plan_id, rev, sku, period_start, total_units,"
            " demand_by_seller, demand_at_submit, state)"
            " VALUES (%s, 1, %s, '2026-10-01', 50, '{}'::jsonb, 0, '已提交') RETURNING line_id",
            (plan_id, seed.sku_b))
        line_b = cur.fetchone()[0]
        cur.execute("INSERT INTO plan_line_event (line_id, from_state, to_state, actor, src)"
                    " VALUES (%s, '[*]', '已提交', %s, 'test')", (line_b, seed.actor))
        # 只把 line_a 推进到终态，line_b 留在「已提交」
        cur.execute("INSERT INTO plan_line_event (line_id, from_state, to_state, actor, reason)"
                    " VALUES (%s, '已提交', '已撤销', %s, '不做了')", (line_a, seed.actor))
        cur.execute("SELECT in_flight FROM plan_rev WHERE plan_id = %s", (plan_id,))
        assert cur.fetchone()[0] is True, "★ line_b 还没到终态，这一版不能落定"


def test_happy_path_walks_all_the_way_to_done(wipe, seed):
    """[*] → 已提交 → 已确认 → 已下单，全程只经事件表。"""
    with pg_conn() as c, c.cursor() as cur:
        _, line = mint(cur, seed.actor, seed.sku_a)
        cur.execute("INSERT INTO plan_line_event (line_id, from_state, to_state, actor)"
                    " VALUES (%s, '已提交', '已确认', %s)", (line, seed.actor))
        cur.execute("INSERT INTO plan_line_event (line_id, from_state, to_state, actor)"
                    " VALUES (%s, '已确认', '已下单', %s)", (line, seed.actor))
        cur.execute("SELECT state FROM plan_line WHERE line_id = %s", (line,))
        assert cur.fetchone()[0] == "已下单"
