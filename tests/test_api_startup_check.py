"""启动检查。★ OQ-8 裁定（控制器 09-22）：不拦启动、不拦 /health/readiness——
只记录 + 在需要时触发一次立即刷新；拒绝服务只在业务路由按请求判。"""
import logging

from starlette.testclient import TestClient

import api
from jobs import refresh_dims
from shared import config as config_module


def _cfg(monkeypatch, **kw):
    cfg = dict(config_module.freshness())
    cfg.update(scheduler_enabled=False, **kw)
    monkeypatch.setattr(config_module, "freshness", lambda: cfg)


def test_boot_with_all_mirrors_stale_still_serves_health_and_gates_business_per_request(
        wipe, monkeypatch):
    """★ 判据：/health 200、/v1/readiness 能看到陈旧、/v1/plans 503，
    且启动只触发一次刷新——不是零次（没做该做的事），也不是一次以上（重复刷）。"""
    _cfg(monkeypatch, startup_gate=True)
    calls = []
    monkeypatch.setattr(refresh_dims, "refresh_all",
                        lambda *a, **k: (calls.append((a, k)), [])[1])
    with TestClient(api.create_app()) as c:
        assert c.get("/health").status_code == 200
        r = c.get("/v1/readiness")
        assert r.status_code == 200
        never_refreshed = {m["mirror"] for m in r.json()["mirrors"] if m["refreshed_at"] is None}
        assert {"sku_catalog", "msku_bridge", "seller", "warehouse"} <= never_refreshed
        pr = c.get("/v1/plans")
        assert pr.status_code == 503 and pr.json()["error"] == "mirror_stale"
    assert len(calls) == 1, f"启动应恰好触发一次刷新，实际 {len(calls)} 次：{calls}"
    assert calls[0][0] == ("scheduler",), "启动触发的刷新必须走 trigger='scheduler' 那条口子"


def test_startup_gate_false_never_triggers_a_refresh(wipe, monkeypatch):
    """★ startup_gate 现在的语义是「是否在启动时触发刷新」，不是「是否拒绝启动」。"""
    _cfg(monkeypatch, startup_gate=False)
    calls = []
    monkeypatch.setattr(refresh_dims, "refresh_all",
                        lambda *a, **k: (calls.append((a, k)), [])[1])
    with TestClient(api.create_app()) as c:
        assert c.get("/health").status_code == 200
    assert calls == [], "startup_gate=false 时一次都不许触发"


def test_a_failing_startup_refresh_is_logged_with_cause_and_does_not_stop_the_app(
        wipe, monkeypatch, caplog):
    """★ 静默兜底是最坏的一种：刷新崩了要连原因一起留声，且绝不能让应用起不来。"""
    _cfg(monkeypatch, startup_gate=True)

    def _boom(*a, **k):
        raise RuntimeError("CH 连不上：模拟 UND_ERR_CONNECT_TIMEOUT")

    monkeypatch.setattr(refresh_dims, "refresh_all", _boom)
    with caplog.at_level(logging.WARNING, logger="scm.api"):  # noqa: SIM117
        with TestClient(api.create_app()) as c:
            assert c.get("/health").status_code == 200
    assert "CH 连不上" in caplog.text and "UND_ERR_CONNECT_TIMEOUT" in caplog.text


def test_check_only_looks_at_gate_503_mirrors(wipe, monkeypatch, seed):
    """label_only 的镜像（sku_category）陈旧不触发这条路径，只标注（01:275）。"""
    from dim.registry import gate_503_names
    assert "sku_category" not in gate_503_names()
