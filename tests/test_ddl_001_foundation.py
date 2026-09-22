"""001 的约束真的挡得住吗 —— 每条都插一行违反的，断言具体是哪个错。

★ 「约束在库层」这条铁律靠这些测试兑现。只写 DDL 不写这些，
  等于把「我以为它挡得住」当成「它挡得住」。
"""
import psycopg2.errors
import pytest

from shared.pg_client import pg_conn


def _exec(sql, args=()):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute(sql, args)


def test_mirror_freshness_lists_exactly_four_mirrors(wipe):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT mirror FROM v_mirror_freshness ORDER BY mirror")
        assert [r[0] for r in cur.fetchall()] == [
            "msku_bridge", "seller", "sku_catalog", "warehouse"]


def test_empty_mirror_reports_null_not_fresh(wipe):
    """★ 空镜像的 max(refreshed_at) 是 NULL —— 必须能与「刚刷过」分得开。
    把 NULL 当成新鲜，就是拿一张空表当可用数据在服务。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT refreshed_at FROM v_mirror_freshness WHERE mirror = 'seller'")
        assert cur.fetchone()[0] is None


def test_msku_bridge_primary_key_includes_sid(wipe, seed):
    """★ 同一 msku 字符串跨店是不同 listing：只按 seller_sku 建键会撞键并静默覆盖。"""
    _exec("INSERT INTO msku_bridge VALUES (%s, %s, %s, now())",
          (seed.msku_a[0], seed.seller_b, seed.sku_a))   # 同 seller_sku、不同 sid → 必须能插
    with pytest.raises(psycopg2.errors.UniqueViolation):
        _exec("INSERT INTO msku_bridge VALUES (%s, %s, %s, now())",
              (seed.msku_a[0], seed.msku_a[1], seed.sku_a))  # 同 (seller_sku, sid) → 撞主键


def test_msku_bridge_requires_known_seller_and_sku(wipe, seed):
    with pytest.raises(psycopg2.errors.ForeignKeyViolation) as ei:
        _exec("INSERT INTO msku_bridge VALUES ('MSKU-X', '9999999999', %s, now())", (seed.sku_a,))
    assert "seller" in (ei.value.diag.constraint_name or "")
    with pytest.raises(psycopg2.errors.ForeignKeyViolation) as ei:
        _exec("INSERT INTO msku_bridge VALUES ('MSKU-X', %s, 'NO-SUCH-SKU', now())",
              (seed.seller_a,))
    assert "sku" in (ei.value.diag.constraint_name or "")


def test_warehouse_kind_is_a_closed_set(wipe):
    with pytest.raises(psycopg2.errors.CheckViolation) as ei:
        _exec("INSERT INTO warehouse VALUES (1, '某仓', 'fba_like', 'US', now())")
    assert ei.value.diag.constraint_name == "warehouse_kind_known"


def test_forbid_update_delete_names_the_table_and_the_op(wipe, seed):
    """守卫函数本身要能被证伪：挂一张临时的只追加表，改它或删它都必须炸且点名。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("CREATE TEMP TABLE probe_append_only (x int)")
        cur.execute("CREATE TRIGGER t BEFORE UPDATE OR DELETE ON probe_append_only "
                    "FOR EACH ROW EXECUTE FUNCTION forbid_update_delete()")
        cur.execute("INSERT INTO probe_append_only VALUES (1)")
        with pytest.raises(psycopg2.errors.RaiseException) as ei:
            cur.execute("UPDATE probe_append_only SET x = 2")
        assert "probe_append_only" in str(ei.value) and "UPDATE" in str(ei.value)

    with pg_conn() as c, c.cursor() as cur:
        cur.execute("CREATE TEMP TABLE probe_append_only (x int)")
        cur.execute("CREATE TRIGGER t BEFORE UPDATE OR DELETE ON probe_append_only "
                    "FOR EACH ROW EXECUTE FUNCTION forbid_update_delete()")
        cur.execute("INSERT INTO probe_append_only VALUES (1)")
        with pytest.raises(psycopg2.errors.RaiseException) as ei:
            cur.execute("DELETE FROM probe_append_only")
        assert "probe_append_only" in str(ei.value) and "DELETE" in str(ei.value)
