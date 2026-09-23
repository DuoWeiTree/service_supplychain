"""006 的结构守卫。★ 证据表可改就不再是证据（01 规则 8）。"""
import json

import psycopg2
import pytest

from shared.pg_client import pg_conn


def _insert(cur, **kw):
    kw.setdefault("mirror", "seller")
    kw.setdefault("trigger", "cli")
    cols = ", ".join(kw)
    cur.execute(f"INSERT INTO dim_refresh_run ({cols}) VALUES"
                f" ({', '.join(['%s'] * len(kw))}) RETURNING run_id", tuple(kw.values()))
    return cur.fetchone()[0]


def test_trigger_value_is_whitelisted(wipe):
    with pg_conn() as c, c.cursor() as cur:
        for ok in ("scheduler", "cli", "api"):
            _insert(cur, trigger=ok)
    with pytest.raises(psycopg2.errors.CheckViolation), pg_conn() as c, c.cursor() as cur:
        _insert(cur, trigger="cron")


def test_actor_is_nullable_but_must_exist_when_given(seed):
    with pg_conn() as c, c.cursor() as cur:
        _insert(cur, trigger="scheduler", actor=None)     # ★ 调度没有人，不是「匿名」
        _insert(cur, trigger="api", actor=seed.actor)
    with pytest.raises(psycopg2.errors.ForeignKeyViolation), pg_conn() as c, c.cursor() as cur:
        _insert(cur, trigger="api", actor="nobody")


def test_run_rows_cannot_be_updated_or_deleted(wipe):
    with pg_conn() as c, c.cursor() as cur:
        run_id = _insert(cur, ok=False)
    for sql in ("UPDATE dim_refresh_run SET ok = true WHERE run_id = %s",
                "DELETE FROM dim_refresh_run WHERE run_id = %s"):
        with pytest.raises(psycopg2.errors.RaiseException), pg_conn() as c, c.cursor() as cur:
            cur.execute(sql, (run_id,))


def test_drop_reasons_is_queryable_jsonb(wipe):
    with pg_conn() as c, c.cursor() as cur:
        _insert(cur, rows_in=10, rows_dropped=3,
                drop_reasons=json.dumps({"empty_sku": 3}))
        cur.execute("SELECT drop_reasons->>'empty_sku' FROM dim_refresh_run")
        assert cur.fetchone()[0] == "3"


def test_ok_defaults_to_false(wipe):
    """★ 默认 false：一轮崩在半路、没人写结果，它必须看起来像失败而不是成功。"""
    with pg_conn() as c, c.cursor() as cur:
        run_id = _insert(cur)
        cur.execute("SELECT ok, finished_at FROM dim_refresh_run WHERE run_id = %s", (run_id,))
        assert cur.fetchone() == (False, None)
