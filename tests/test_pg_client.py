"""pg_conn() 对中止事务的收尾行为。

★ Task 4 复核（2026-09-22）实测踩到：块内用 pytest.raises 吞掉一次库层异常后
  正常退出 with pg_conn()，事务此时已 aborted，PG 对它的 COMMIT 会静默折成
  ROLLBACK —— 早前在同一事务里写入的行随之消失，且没有任何异常、任何日志。
  这正是「静默兜底是最坏的一种」要防的那类：调用方以为提交了，其实全丢了。
"""
import psycopg2
import psycopg2.errors
import pytest

from shared.pg_client import pg_conn


def test_pg_conn_raises_if_block_swallows_an_error_and_exits_normally(business_db):
    # ★ 这两层 with 不能按 SIM117 合并：内层的 pytest.raises 必须留在 pg_conn 块
    #   **里面** —— 它就是「块内被吞掉的那次库层异常」本身。合进同一行的话它退化成
    #   一个包住整块的 raises，测的就不再是收口守卫，而是「执行了一句坏 SQL」。
    with pytest.raises(RuntimeError), pg_conn() as c, c.cursor() as cur:  # noqa: SIM117
        with pytest.raises(psycopg2.errors.UndefinedTable):
            cur.execute("SELECT * FROM no_such_table_zzz")


def test_pg_conn_still_commits_normally_when_nothing_is_swallowed(wipe, seed):
    """★ 收口不能矫枉过正 —— 正常提交必须还是正常提交。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO actor (actor_id, name) VALUES ('zed', '泽德')")
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM actor WHERE actor_id = 'zed'")
        assert cur.fetchone()[0] == 1
