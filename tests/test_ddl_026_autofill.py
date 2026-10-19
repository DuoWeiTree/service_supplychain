"""026 · 默认分配留痕：只追加 · 一张表一次 · failed 必带原因。"""
import psycopg2
import pytest

from shared.pg_client import pg_conn
from tests.test_ddl_016_dispatch import constraint_of, new_sheet, run, world  # noqa: F401


def sheet(cur, seed, route="fba"):
    return new_sheet(cur, seed.actor, route=route)


def event(cur, sid, actor, outcome="done", error=None):
    cur.execute("INSERT INTO dispatch_autofill (sheet_id, rule, outcome, inputs, error, elapsed_ms, actor)"
                " VALUES (%s, 'r', %s, '{}', %s, 1, %s)", (sid, outcome, error, actor))


def test_once_per_sheet_and_failed_needs_error(world, seed):  # noqa: F811
    with pg_conn() as c, c.cursor() as cur:
        sid = sheet(cur, seed)
        event(cur, sid, seed.actor)
    assert constraint_of(run(lambda cur: event(cur, sid, seed.actor))) == "dispatch_autofill_once_per_sheet"
    with pg_conn() as c, c.cursor() as cur:
        sid2 = sheet(cur, seed, "oversea")  # 同计划同去向只许一张草稿
    assert constraint_of(run(lambda cur: event(cur, sid2, seed.actor, "failed"))) == \
        "dispatch_autofill_failed_has_error"


def test_append_only(world, seed):  # noqa: F811
    with pg_conn() as c, c.cursor() as cur:
        event(cur, sheet(cur, seed), seed.actor)
    for sql in ("UPDATE dispatch_autofill SET outcome='failed', error='{}'", "DELETE FROM dispatch_autofill"):
        with pytest.raises(psycopg2.Error), pg_conn() as c, c.cursor() as cur:
            cur.execute(sql)
