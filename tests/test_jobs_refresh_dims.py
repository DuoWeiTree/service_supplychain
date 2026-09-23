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


def test_bogus_trigger_is_refused_before_any_mirror_runs(wipe):
    """review 09-22 二轮：调用方拼错 trigger 不能长得像"这批镜像刷新失败了"——
    必须在碰任何镜像之前就地爆炸，不排队也不半跑。"""
    with pytest.raises(rd.UnknownTrigger) as exc:
        rd.refresh_all("bogus")
    assert "scheduler" in str(exc.value) and "cli" in str(exc.value) and "api" in str(exc.value), (
        f"错误消息没有点名允许的取值：{exc.value}")
    with pg_conn() as c, c.cursor() as cur:
        assert _rows(cur, "SELECT count(*) FROM dim_refresh_run") == [(0,)], (
            "坏参数被拒之前不该有任何一次刷新尝试留痕")
    with advisory_lock():          # ★ 校验失败不该先抢锁——锁必须还拿得到
        pass


def test_unknown_or_inactive_actor_is_refused_before_any_mirror_runs(seed):
    """同上一条，针对 actor：不存在、或存在但已停用，都必须在跑任何镜像之前
    就地爆炸——`None`（调度触发，没有人）必须放行。"""
    with pytest.raises(rd.UnknownActor, match="no-such-actor"):
        rd.refresh_all("cli", actor="no-such-actor")
    with pytest.raises(rd.UnknownActor, match=seed.actor_inactive):
        rd.refresh_all("cli", actor=seed.actor_inactive)
    with pg_conn() as c, c.cursor() as cur:
        assert _rows(cur, "SELECT count(*) FROM dim_refresh_run") == [(0,)], (
            "坏 actor 被拒之前不该有任何一次刷新尝试留痕")
    # ★ None 必须放行——不能因为"没传 actor"被误判成坏参数
    got = rd.refresh_all("cli", only="sku_catalog", query=R.replay(R.SKU_CATALOG))
    assert [r.ok for r in got] == [True]


def test_provenance_write_failure_is_contained_and_others_still_run(seed, monkeypatch):
    """review 09-22 二轮 finding：`SAVEPOINT mirror_<name>` 修好了 `_upsert()`
    那一步，但收尾的留痕 INSERT 本身还在保护范围之外——同样的失败形状只是
    挪后了一步（哪怕 trigger/actor 已经在 `refresh_all` 里挡过，这条 INSERT
    仍可能撞上别的约束或连接问题）。这里直接 monkeypatch 留痕 INSERT 的 SQL
    文本（引用一个不存在的列），在不绕开入口校验的前提下单独制造"合法参数、
    DB 却拒绝"的场景。"""
    monkeypatch.setattr(
        rd, "_PROVENANCE_SQL",
        "INSERT INTO dim_refresh_run (mirror, trigger, actor, started_at, finished_at,"
        " source_max_captured, rows_in, rows_dropped, drop_reasons, ok, nonexistent_column)"
        " VALUES (%s, %s, %s, %s, now(), %s, %s, %s, %s, %s, %s) RETURNING run_id")

    got = rd.refresh_all("cli", query=_mixed_query_all_good())
    assert len(got) == 4, "四张镜像都要跑到——留痕写不出来不能打断循环"
    assert all(r.ok is False for r in got), "留痕都没写成，这一轮不能算数"
    assert all(r.run_id == rd.NO_PROVENANCE_ROW for r in got)
    assert all("provenance_write_failed" in (r.error or "") for r in got)

    # ★ upsert 本身仍然落地了——provenance 用的是独立于 mirror_<name> 的
    #   SAVEPOINT，不该连累已经成功写完的镜像数据。
    with pg_conn() as c, c.cursor() as cur:
        assert _rows(cur, "SELECT count(*) FROM sku_catalog WHERE sku = 'DCC1800264G1'") == [(1,)]
        assert _rows(cur, "SELECT count(*) FROM dim_refresh_run") == [(0,)], (
            "留痕 INSERT 本身失败——不该有任何一行落进 dim_refresh_run")


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


def _mixed_query_all_good():
    """四张镜像全给合法数据——用于只想验证留痕写入本身（不是 upsert）失败
    时的隔离效果，不希望 upsert 阶段就先出错混淆归因。"""
    from dim import ch_source as cs
    table = {cs.SQL_SKU_CATALOG: R.SKU_CATALOG, cs.SQL_MSKU_BRIDGE: R.MSKU_BRIDGE,
             cs.SQL_WAREHOUSE: R.WAREHOUSE, cs.SQL_SELLER: R.SELLER}
    return lambda sql: list(table[sql])
