"""009 的结构守卫：`sku_category` 七个业务列都在、`sku` 外键指向 `sku_catalog`、
`image_is_placeholder` 非空有默认、`match_path` 只认 direct/base。

★ 证据表（01 规则 8）：结构一旦能悄悄改，「占位图判定按 basename」这类判据
  就没有地基。先弄红一次再补 009，见计划 Task 1 Step 1/2。
"""
import psycopg2
import pytest

from shared.pg_client import pg_conn


def _seed_sku(sku: str) -> None:
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO sku_catalog (sku, name, refreshed_at) VALUES (%s, %s, now())",
                    (sku, "测试货号"))


def test_seven_columns_present(wipe):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = current_schema() AND table_name = 'sku_category'")
        cols = {r[0] for r in cur.fetchall()}
    expected = {"sku", "category_id", "category_path", "pm_sku_matched",
                "match_path", "main_image_url", "image_is_placeholder", "refreshed_at"}
    assert expected <= cols, f"缺列：{expected - cols}"


def test_sku_fk_points_at_sku_catalog(wipe):
    with pytest.raises(psycopg2.errors.ForeignKeyViolation), \
         pg_conn() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO sku_category (sku, category_id, category_path, pm_sku_matched,"
            " match_path, image_is_placeholder, refreshed_at)"
            " VALUES ('no-such-sku', 'C1', 'root/C1', 'PM-1', 'direct', true, now())")


def test_image_is_placeholder_defaults_to_true(wipe):
    """★ 默认 true：还没判过就当「没有真图」，不许默认当真图（Global Constraints）。"""
    _seed_sku("SKU-009-A")
    with pg_conn() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO sku_category (sku, category_id, category_path, pm_sku_matched,"
            " match_path, refreshed_at) VALUES (%s, 'C1', 'root/C1', 'PM-1', 'direct', now())",
            ("SKU-009-A",))
        cur.execute("SELECT image_is_placeholder FROM sku_category WHERE sku = %s",
                    ("SKU-009-A",))
        assert cur.fetchone() == (True,)


def test_image_is_placeholder_rejects_null(wipe):
    _seed_sku("SKU-009-B")
    with pytest.raises(psycopg2.errors.NotNullViolation), \
         pg_conn() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO sku_category (sku, category_id, category_path, pm_sku_matched,"
            " match_path, image_is_placeholder, refreshed_at)"
            " VALUES (%s, 'C2', 'root/C2', 'PM-2', 'direct', NULL, now())", ("SKU-009-B",))


def test_match_path_rejects_unknown_values(wipe):
    _seed_sku("SKU-009-C")
    with pytest.raises(psycopg2.errors.CheckViolation), \
         pg_conn() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO sku_category (sku, category_id, category_path, pm_sku_matched,"
            " match_path, refreshed_at) VALUES (%s, 'C1', 'root/C1', 'PM-1', 'fuzzy', now())",
            ("SKU-009-C",))


def test_match_path_accepts_direct_and_base(wipe):
    _seed_sku("SKU-009-D")
    _seed_sku("SKU-009-E")
    with pg_conn() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO sku_category (sku, category_id, category_path, pm_sku_matched,"
            " match_path, refreshed_at) VALUES (%s, 'C1', 'root/C1', 'PM-1', 'direct', now())",
            ("SKU-009-D",))
        cur.execute(
            "INSERT INTO sku_category (sku, category_id, category_path, pm_sku_matched,"
            " match_path, refreshed_at) VALUES (%s, 'C1', 'root/C1', 'PM-1', 'base', now())",
            ("SKU-009-E",))


def test_category_refresh_table_exists(wipe):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = current_schema() AND table_name = 'category_refresh'")
        cols = {r[0] for r in cur.fetchall()}
    expected = {"refresh_id", "started_at", "finished_at", "n_sku", "n_changed",
                "n_dropped", "outcome", "note"}
    assert expected <= cols, f"缺列：{expected - cols}"
