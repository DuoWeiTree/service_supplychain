"""`GET /v1/skus/{sku}/image` —— 货号主图代理（sku-image 计划 Task 2）。

全部离线：PM 出站一律通过 monkeypatch `shared.pm_client` 的模块属性替身，
不联真网（真打 PM 的冒烟在 `tests/test_pm_live.py`）。
"""
from __future__ import annotations

import urllib.error

from api.ui import images
from shared import pm_client
from shared.pg_client import pg_conn


def _seed_category(sku: str, *, placeholder: bool, url: str | None = "https://x/real.jpg",
                   category_id="C1", category_path="宠物用品/猫用品") -> None:
    with pg_conn() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO sku_category (sku, category_id, category_path, pm_sku_matched,"
            " match_path, main_image_url, image_is_placeholder, refreshed_at)"
            " VALUES (%s, %s, %s, %s, 'direct', %s, %s, now())",
            (sku, category_id, category_path, sku, url, placeholder))


def _boom_if_called(*a, **kw):
    raise AssertionError("PM 出站被调用了——占位/无匹配/无该行三种情形都该在"
                         "查库之后就短路，不许打一次 PM")


# ══════════════════════════════════════════════════════════════════════
#  Step 1：占位图必须 404 且零出站（本任务第一判据）
# ══════════════════════════════════════════════════════════════════════

def test_placeholder_image_returns_404_without_any_outbound_call(client, seed, monkeypatch):
    monkeypatch.setattr(pm_client, "mint_token", _boom_if_called)
    monkeypatch.setattr(pm_client, "fetch_image_bytes", _boom_if_called)
    _seed_category(seed.sku_a, placeholder=True)

    r = client.get(f"/v1/skus/{seed.sku_a}/image")

    assert r.status_code == 404, r.text
    assert r.json()["reason"] == "placeholder_only"


# ══════════════════════════════════════════════════════════════════════
#  Step 3：三种 404 的 reason 两两不同
# ══════════════════════════════════════════════════════════════════════

def test_sku_not_in_catalog_reason(client, seed, monkeypatch):
    monkeypatch.setattr(pm_client, "mint_token", _boom_if_called)
    r = client.get("/v1/skus/NO-SUCH-SKU/image")
    assert r.status_code == 404, r.text
    assert r.json()["reason"] == "sku_not_in_catalog"


def test_no_pm_match_reason(client, seed, monkeypatch):
    """货号在 sku_catalog 里（seed 已插），但 sku_category 里没有它这一行
    ——两段匹配没对上，或对上了但没有可用品类，两者在这一层不再细分。"""
    monkeypatch.setattr(pm_client, "mint_token", _boom_if_called)
    r = client.get(f"/v1/skus/{seed.sku_a}/image")
    assert r.status_code == 404, r.text
    assert r.json()["reason"] == "no_pm_match"


def test_three_reasons_are_pairwise_distinct(client, seed, monkeypatch):
    monkeypatch.setattr(pm_client, "mint_token", _boom_if_called)
    _seed_category(seed.sku_b, placeholder=True)

    reasons = {
        client.get("/v1/skus/NO-SUCH-SKU/image").json()["reason"],
        client.get(f"/v1/skus/{seed.sku_e}/image").json()["reason"],  # 有货号无 sku_category 行
        client.get(f"/v1/skus/{seed.sku_b}/image").json()["reason"],  # 占位
    }
    assert reasons == {"sku_not_in_catalog", "no_pm_match", "placeholder_only"}, reasons


# ══════════════════════════════════════════════════════════════════════
#  Step 5：PM 超时 → 502 带诊断，不是裸 500、不是 200 空图
# ══════════════════════════════════════════════════════════════════════

def test_pm_timeout_returns_502_with_diagnostics(client, seed, monkeypatch):
    _seed_category(seed.sku_a, placeholder=False)
    monkeypatch.setattr(pm_client, "mint_token", lambda: "tok")

    def _timeout(url, token, timeout=15):
        raise urllib.error.URLError(TimeoutError("timed out"))

    monkeypatch.setattr(pm_client, "fetch_image_bytes", _timeout)

    r = client.get(f"/v1/skus/{seed.sku_a}/image")
    assert r.status_code == 502, r.text
    body = r.json()
    assert body["host"] == "x"
    assert body["path"] == "/real.jpg"
    assert "elapsed_ms" in body
    assert "timeout" in str(body["cause"]).lower() or "timeout" in body.get("cause", {}).get("kind", "")


# ══════════════════════════════════════════════════════════════════════
#  Step 7：进程内缓存 600 秒——命中不重复出站；失败不许被缓存
# ══════════════════════════════════════════════════════════════════════

def test_cache_hit_does_not_fetch_twice(client, seed, monkeypatch):
    _seed_category(seed.sku_a, placeholder=False)
    images._IMAGE_CACHE.clear()
    monkeypatch.setattr(pm_client, "mint_token", lambda: "tok")
    calls = []

    def _fetch(url, token, timeout=15):
        calls.append(url)
        return b"\x89PNG...", "image/png"

    monkeypatch.setattr(pm_client, "fetch_image_bytes", _fetch)

    r1 = client.get(f"/v1/skus/{seed.sku_a}/image")
    r2 = client.get(f"/v1/skus/{seed.sku_a}/image")

    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.content == b"\x89PNG..." and r2.content == b"\x89PNG..."
    assert len(calls) == 1, f"缓存命中却又出站了一次：{calls}"


def test_cache_does_not_cache_failures(client, seed, monkeypatch):
    """★ 502 之后再打要真的重试——缓存只能记成功。"""
    _seed_category(seed.sku_a, placeholder=False)
    images._IMAGE_CACHE.clear()
    monkeypatch.setattr(pm_client, "mint_token", lambda: "tok")
    calls = []

    def _flaky(url, token, timeout=15):
        calls.append(url)
        if len(calls) == 1:
            raise urllib.error.URLError(ConnectionRefusedError(111, "refused"))
        return b"\x89PNG...", "image/png"

    monkeypatch.setattr(pm_client, "fetch_image_bytes", _flaky)

    r1 = client.get(f"/v1/skus/{seed.sku_a}/image")
    assert r1.status_code == 502, r1.text
    r2 = client.get(f"/v1/skus/{seed.sku_a}/image")
    assert r2.status_code == 200, r2.text
    assert len(calls) == 2, f"失败之后没有真的重试：{calls}"


# ══════════════════════════════════════════════════════════════════════
#  200：字节 + content-type 原样透传
# ══════════════════════════════════════════════════════════════════════

def test_unknown_query_param_is_400(client, seed, monkeypatch):
    """★ 本仓全部端点的既有规矩（`api/ui/deps.py::declared`）——拼错的参数
    被静默忽略时，返回的是一次「看起来成功」的响应。"""
    monkeypatch.setattr(pm_client, "mint_token", _boom_if_called)
    r = client.get(f"/v1/skus/{seed.sku_a}/image", params={"typo": "1"})
    assert r.status_code == 400, r.text
    assert r.json()["error"] == "unknown_query_param"


def test_success_passes_through_bytes_and_content_type(client, seed, monkeypatch):
    _seed_category(seed.sku_a, placeholder=False, url="https://pm.zestrade.com/i/real.png")
    images._IMAGE_CACHE.clear()
    monkeypatch.setattr(pm_client, "mint_token", lambda: "tok")
    monkeypatch.setattr(pm_client, "fetch_image_bytes",
                        lambda url, token, timeout=15: (b"\x89PNGDATA", "image/png"))

    r = client.get(f"/v1/skus/{seed.sku_a}/image")
    assert r.status_code == 200
    assert r.content == b"\x89PNGDATA"
    assert r.headers["content-type"] == "image/png"
