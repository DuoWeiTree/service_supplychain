"""刷新作业。★ 掉档拒批、旧镜像不动、留痕写全。"""
import pytest

from jobs import refresh_dims as rd
from jobs.lock import RefreshInFlight, advisory_lock
from shared.pg_client import pg_conn
from tests.fixtures import ch_rows as R


def _rows(cur, sql, *a):
    cur.execute(sql, a)
    return cur.fetchall()


def test_second_holder_is_refused_not_queued(wipe):
    """★ 三个入口同时刷 = 同一张表两个写入者。排队或静默返回成功都不行。"""
    with advisory_lock(), pytest.raises(RefreshInFlight), advisory_lock():
        pass
    with advisory_lock():          # ★ 前一次释放了才拿得到 —— 证明锁真的解开了
        pass


def test_refresh_writes_rows_and_bumps_refreshed_at(seed):
    got = rd.refresh_all("cli", actor=seed.actor, only="sku_catalog",
                         query=R.replay(R.SKU_CATALOG))
    assert [r.ok for r in got] == [True]
    with pg_conn() as c, c.cursor() as cur:
        assert _rows(cur, "SELECT count(*) FROM sku_catalog WHERE sku = 'DCC1800264G1'") == [(1,)]
        run = _rows(cur, "SELECT mirror, trigger, actor, rows_in, rows_dropped,"
                         " drop_reasons, ok, finished_at IS NOT NULL, source_max_captured"
                         " FROM dim_refresh_run")[0]
    assert run[:5] == ("sku_catalog", "cli", seed.actor, 1, 1)
    assert run[5] == {"empty_sku": 1, "no_baseline": 1}      # ★ 首轮无基线要留声
    assert run[6] is True and run[7] is True
    assert run[8] == R.D2


def test_coverage_drop_refuses_the_batch_and_keeps_the_old_mirror(seed):
    big = [(f"SKU{i}", f"名{i}", R.D2) for i in range(100)]
    rd.refresh_all("cli", only="sku_catalog", query=R.replay(big))
    with pg_conn() as c, c.cursor() as cur:
        before = _rows(cur, "SELECT count(*), max(refreshed_at) FROM sku_catalog")[0]

    got = rd.refresh_all("cli", only="sku_catalog", query=R.replay(big[:50]))
    assert [r.ok for r in got] == [False]
    assert "coverage_drop" in (got[0].error or "")
    with pg_conn() as c, c.cursor() as cur:
        assert _rows(cur, "SELECT count(*), max(refreshed_at) FROM sku_catalog")[0] == before, (
            "拒批了却动了镜像 —— 旧数据必须原封不动")
        assert _rows(cur, "SELECT ok, error FROM dim_refresh_run ORDER BY run_id DESC LIMIT 1"
                     )[0][0] is False


def test_msku_bridge_coverage_also_counts_distinct_sid(seed):
    """★ 整店 0 行是采集缺口的形状，总行数看不出来 —— 已踩第六次。"""
    two = R.MSKU_BRIDGE
    one = [r for r in two if r[1] == "11072"] * 2      # 行数不掉，少一个店
    rd.refresh_all("cli", only="msku_bridge", query=R.replay(two))
    got = rd.refresh_all("cli", only="msku_bridge", query=R.replay(one))
    assert [r.ok for r in got] == [False]
    assert "distinct:sid" in (got[0].error or "")


def test_a_failed_mirror_does_not_stop_the_others(seed):
    """`07:486` 的同一条：一批失败不影响其它批。
    ★ OQ-3 裁定后 seller 不再天然失败（has_fba 已可派生），改用「混进一行认不出
    的仓形态」来制造这一条要验证的失败——warehouse 挂，其余三张照常成功。"""
    got = rd.refresh_all("cli", query=_mixed_query())
    names = {r.mirror: r.ok for r in got}
    assert names["warehouse"] is False
    assert names["seller"] is True
    assert names["sku_catalog"] is True
    assert set(names) == {m.name for m in rd.registry.refresh_order()}


def test_unknown_mirror_name_is_refused(seed):
    with pytest.raises(KeyError):
        rd.refresh_all("cli", only="no_such_mirror", query=R.replay([]))


def test_a_db_level_failure_inside_upsert_still_writes_provenance_and_does_not_stop_others(seed):
    """review 09-22 finding 1：`_upsert()` 的 SQL 执行阶段才炸的原始 DB 异常
    （这里用主键重复触发——`sku_catalog` 的 `coverage=("rows",)` 没有
    `distinct:` 规则，拦不住"这一批内部有没有重复主键"这种形态）不能连累
    收尾那条留痕 `INSERT`，也不能打断其余镜像的遍历。这与
    `test_a_failed_mirror_does_not_stop_the_others` 的区别是：那条测试的失败
    发生在 `m.fetch()` 内部（Python 级异常，事务还没被碰脏）；这条测试的失败
    发生在 `_upsert()` 内部（DB 级异常，事务会被 PG 标记为 aborted）。"""
    with pg_conn() as c, c.cursor() as cur:
        before = _rows(cur, "SELECT count(*) FROM sku_catalog WHERE sku = 'SKU-DUP'")[0]

    got = rd.refresh_all("cli", query=_mixed_query_with_db_level_failure())
    names = {r.mirror: r.ok for r in got}
    assert names["sku_catalog"] is False, "主键重复的这一批必须失败，不能悄悄落进镜像"
    assert names["seller"] is True
    assert names["warehouse"] is True
    assert names["msku_bridge"] is True
    assert set(names) == {m.name for m in rd.registry.refresh_order()}, (
        "四张镜像都要跑到——sku_catalog 失败不许打断循环，warehouse/msku_bridge"
        "不许连尝试都没被尝试")

    with pg_conn() as c, c.cursor() as cur:
        assert _rows(cur, "SELECT count(*) FROM sku_catalog WHERE sku = 'SKU-DUP'")[0] == before, (
            "拒批了却动了镜像 —— 主键重复的批次不该有任何一行落进 sku_catalog")
        run = _rows(cur, "SELECT ok, error FROM dim_refresh_run"
                         " WHERE mirror = 'sku_catalog' ORDER BY run_id DESC LIMIT 1")[0]
    assert run[0] is False, "DB 级异常也必须留下 ok=false 的一行，不能让整个 run 消失"
    assert "cannot affect row a second time" in (run[1] or ""), (
        f"error 字段没有点名异常成因：{run[1]!r}")


def _mixed_query():
    """按 SQL 里出现的表名派发到对应的假行。"""
    from dim import ch_source as cs
    table = {cs.SQL_SKU_CATALOG: R.SKU_CATALOG, cs.SQL_MSKU_BRIDGE: R.MSKU_BRIDGE,
             cs.SQL_WAREHOUSE: R.WAREHOUSE_UNKNOWN, cs.SQL_SELLER: R.SELLER}
    return lambda sql: list(table[sql])


def _mixed_query_with_db_level_failure():
    """★ 与 `_mixed_query` 的区别：这里让 `sku_catalog` 拿到一批 fetch 层
    filters 拦不住的重复主键行——失败发生在 `_upsert()` 的 `execute_values`
    调用本身（PG 报 `ON CONFLICT DO UPDATE command cannot affect row a
    second time`），而不是 `ch_source.fetch_*` 里的 Python 级 `UnknownShape`。
    """
    from dim import ch_source as cs
    dup_sku_catalog = [("SKU-DUP", "第一份", R.D2), ("SKU-DUP", "第二份(重复主键)", R.D2)]
    table = {cs.SQL_SKU_CATALOG: dup_sku_catalog, cs.SQL_MSKU_BRIDGE: R.MSKU_BRIDGE,
             cs.SQL_WAREHOUSE: R.WAREHOUSE, cs.SQL_SELLER: R.SELLER}
    return lambda sql: list(table[sql])
