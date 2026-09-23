"""迁移执行器自己的守卫。

★ 「已执行的迁移不可改」这条铁律靠 checksum 核对兑现 ——
  没有它，改一行旧迁移在开发机上悄悄生效，而生产上根本不会重跑。
"""
import pytest

from migrations.pg import apply as mig
from shared.pg_client import BadSchemaName, pg_conn


def test_migration_files_carry_no_transaction_control():
    """★ 迁移文件自带 BEGIN/COMMIT，外层 rollback 就兜不住 —— 预演会真落库。"""
    bad = []
    for name, sql in mig.migrations():
        for kw in ("begin;", "commit;", "rollback;"):
            if kw in sql.lower():
                bad.append(f"{name} 含 {kw}")
    assert not bad, f"事务边界只能由 apply() 持有：{bad}"


def test_schema_name_whitelist():
    for bad in ["scm; drop schema public", "SCM", "s" * 64, "", "public.scm"]:
        with pytest.raises(BadSchemaName):
            mig.apply(bad)


def test_applied_migrations_are_recorded_with_checksum(business_db):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT version, checksum FROM schema_migration ORDER BY version")
        rows = cur.fetchall()
    assert [r[0] for r in rows] == [n for n, _ in mig.migrations()]
    assert rows, "一条迁移都没记 —— 下面两条断言就是空转的"
    assert all(len(r[1]) == 64 for r in rows), "checksum 不是 sha256"


def test_rerun_is_a_noop(business_db):
    assert mig.apply(business_db) == [], "已应用的迁移被重复执行了"


def test_changed_migration_is_refused(business_db, monkeypatch):
    """改掉一份已执行迁移的内容 → 必须点名那份文件硬失败。"""
    orig = mig.migrations()
    monkeypatch.setattr(mig, "migrations", lambda: [(orig[0][0], orig[0][1] + "\n-- 偷改一行\n")])
    with pytest.raises(mig.MigrationChanged) as ei:
        mig.apply(business_db)
    assert orig[0][0] in str(ei.value)


def test_pg_conn_sets_search_path(business_db):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SHOW search_path")
        assert cur.fetchone()[0].strip() == business_db


def test_wipe_fails_loudly_once_all_tables_should_exist(business_db, monkeypatch, request):
    """★ 静默兜底是最坏的一种：`wipe` 把 DATA_TABLES 过滤到「已存在」那步，
    本身会哑掉「表名拼错/改名」这类真事故 —— 拼错的表和「还没到它的迁移」长得一模一样。
    Stage A 最后一个迁移（004）落地后 DATA_TABLES 应与 existing 相等，
    这里假装 004 已落地、掺一张假表，验证 `wipe` 会点名它硬失败，而不是悄悄漏掉。"""
    import conftest

    monkeypatch.setattr(conftest, "DATA_TABLES", conftest.DATA_TABLES + ("no_such_table_zzz",))
    monkeypatch.setattr(mig, "applied_versions", lambda schema: ["004_plan_overall_state.sql"])

    with pytest.raises(AssertionError) as ei:
        request.getfixturevalue("wipe")
    assert "no_such_table_zzz" in str(ei.value)
