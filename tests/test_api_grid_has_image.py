"""`GET /v1/plans/{plan_id}/grid` 的货号行带 `has_image`（sku-image 计划 Task 3）。

三态收敛成一个明确的布尔（不是 null、不是缺字段）：有真图 true；只有占位图
false；镜像里没这个货号也 false。★ 零出站——grid() 只从 `sku_category` 左连，
不许打一次 PM，用「被调用就 fail 测试」的桩钉住（同 `tests/test_api_sku_image.py`
的既有写法）。
"""
from __future__ import annotations

from shared import pm_client
from shared.pg_client import pg_conn
from tests.helpers import H, prepared


def _seed_category(sku: str, *, placeholder: bool) -> None:
    with pg_conn() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO sku_category (sku, category_id, category_path, pm_sku_matched,"
            " match_path, main_image_url, image_is_placeholder, refreshed_at)"
            " VALUES (%s, 'C1', '宠物用品/猫用品', %s, 'direct', 'https://x/real.jpg', %s, now())",
            (sku, sku, placeholder))


def _grid(client, seed, pid):
    r = client.get(f"/v1/plans/{pid}/grid", headers=H(seed.actor))
    assert r.status_code == 200, r.text
    return r.json()


def _has_image_values(g: dict, sku: str) -> set:
    rows = [r for r in g["forecast_sku"] if r["sku"] == sku]
    assert rows, f"{sku} 在 forecast_sku 里一行都没有——夹具没搭对"
    return {r["has_image"] for r in rows}


def test_has_image_true_for_a_real_matched_sku(client, seed):
    _seed_category(seed.sku_a, placeholder=False)
    pid = prepared(client, seed)
    g = _grid(client, seed, pid)
    assert _has_image_values(g, seed.sku_a) == {True}


def test_has_image_false_for_placeholder_only(client, seed):
    _seed_category(seed.sku_a, placeholder=True)
    pid = prepared(client, seed)
    g = _grid(client, seed, pid)
    assert _has_image_values(g, seed.sku_a) == {False}


def test_has_image_false_when_no_sku_category_row(client, seed):
    """★ 镜像里没这个货号——不是 null、不是缺字段，是 false。"""
    pid = prepared(client, seed)
    g = _grid(client, seed, pid)
    rows = [r for r in g["forecast_sku"] if r["sku"] == seed.sku_a]
    assert rows
    for r in rows:
        assert "has_image" in r, "键必须在——不是缺字段"
        assert r["has_image"] is False, f"没有 sku_category 行时必须是 false，不是 {r['has_image']!r}"


def test_grid_produces_zero_pm_outbound(client, seed, monkeypatch):
    def _boom_if_called(*a, **kw):
        raise AssertionError("grid() 打了一次 PM——has_image 必须只从 sku_category 左连零出站")

    monkeypatch.setattr(pm_client, "mint_token", _boom_if_called)
    monkeypatch.setattr(pm_client, "fetch_image_bytes", _boom_if_called)
    monkeypatch.setattr(pm_client, "fetch_products", _boom_if_called)
    monkeypatch.setattr(pm_client, "fetch_categories", _boom_if_called)

    _seed_category(seed.sku_a, placeholder=False)
    pid = prepared(client, seed)
    g = _grid(client, seed, pid)
    assert _has_image_values(g, seed.sku_a) == {True}
