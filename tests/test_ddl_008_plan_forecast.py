"""008 的结构守卫（spec §6 · 03 §38/§41）。"""
import psycopg2
import pytest

from shared.pg_client import pg_conn


def test_pipeline_segment_is_data_not_code(business_db):
    """03 §38：段做成数据。★ directed 就是 14 §1 那条横线。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT segment, ord, directed FROM pipeline_segment ORDER BY ord")
        assert cur.fetchall() == [("making", 1, False), ("local", 2, False),
                                  ("staged", 3, True), ("shipping", 4, True)]


def test_lead_time_seed_is_the_ruled_default(business_db):
    """spec §4.4 · 03 §41：oversea_sea 生产 30 / 货运 60（负责人 09-24）。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT route, segment, days FROM lead_time"
                    " WHERE valid_from = DATE '2026-01-01' ORDER BY route, segment")
        assert cur.fetchall() == [("air", "shipping", 10), ("domestic", "shipping", 3),
                                  ("oversea_sea", "making", 30), ("oversea_sea", "shipping", 60)]


@pytest.mark.parametrize("days", [-1, 721])
def test_lead_time_days_range_is_enforced(business_db, days):
    with pytest.raises(psycopg2.errors.CheckViolation), pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO lead_time (route, segment, days, valid_from)"
                    " VALUES ('oversea_sea', 'making', %s, DATE '2030-01-01')", (days,))


def test_lead_time_segment_must_exist(business_db):
    with pytest.raises(psycopg2.errors.ForeignKeyViolation), pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO lead_time (route, segment, days, valid_from)"
                    " VALUES ('oversea_sea', 'flying', 3, DATE '2030-01-01')")


def test_system_basis_is_a_nullable_jsonb_column(business_db):
    """spec §3.3：依据随数一起存。★ 可空 —— 不适用 / 还没算过的格子没有依据。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT data_type, is_nullable FROM information_schema.columns"
                    " WHERE table_schema = current_schema() AND table_name = 'plan_demand_cell'"
                    "   AND column_name = 'system_basis'")
        assert cur.fetchone() == ("jsonb", "YES")
