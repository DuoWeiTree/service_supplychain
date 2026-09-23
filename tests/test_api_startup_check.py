"""启动检查。★ OQ-8 裁定（控制器 09-22）：不拦启动、不拦 /health/readiness——
只记录 + 在需要时触发一次立即刷新；拒绝服务只在业务路由按请求判。"""
import logging

import psycopg2
from starlette.testclient import TestClient

import api
from jobs import refresh_dims
from jobs.lock import RefreshInFlight
from shared import config as config_module
from shared.pg_client import pg_conn


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
    """★ 静默兜底是最坏的一种：刷新崩了要连原因一起留声，且绝不能让应用起不来。

    ★ fix round 2：异常消息本身刻意不含类名（"CH 连不上：模拟
    UND_ERR_CONNECT_TIMEOUT" 里没有 "RuntimeError" 这个词）——这样
    `"RuntimeError" in caplog.text` 只有日志真的打了 `type(e).__name__`
    才会过，不会被"消息文本恰好包含那几个字"这种巧合托住。只断言消息文本
    （旧版测试的写法）证不出"类名被记录"，这正是本轮要补的洞。
    """
    _cfg(monkeypatch, startup_gate=True)

    def _boom(*a, **k):
        raise RuntimeError("CH 连不上：模拟 UND_ERR_CONNECT_TIMEOUT")

    monkeypatch.setattr(refresh_dims, "refresh_all", _boom)
    with caplog.at_level(logging.WARNING, logger="scm.api"):  # noqa: SIM117
        with TestClient(api.create_app()) as c:
            assert c.get("/health").status_code == 200
    assert "CH 连不上" in caplog.text and "UND_ERR_CONNECT_TIMEOUT" in caplog.text
    assert "RuntimeError" in caplog.text, (
        "日志必须带异常类名（err_type=<class>），不能只有 str(e)——"
        f"实际日志：{caplog.text!r}")


def test_a_failing_startup_refresh_that_is_a_pg_error_logs_class_and_pgcode(
        wipe, monkeypatch, caplog):
    """★ psycopg2.Error 的 pgcode/constraint 全在 `.diag` 里，不在 `str(e)` 里——
    真实 PG 拒绝（唯一键冲突、外键冲突……）与真实 CH 连不上必须分得开，不能靠
    "记得看 err_type"。用一次真的坏查询在测试库里现抓一个真 `psycopg2.Error`，
    而不是手搓一个只是同名的假对象——`.diag`/`.pgcode` 是 C 扩展populate 的，
    自己拼一个字段名对不齐的假货会把这条测试变成自证空转。
    """
    _cfg(monkeypatch, startup_gate=True)
    pg_err = None
    try:
        with pg_conn() as c, c.cursor() as cur:
            cur.execute("SELECT * FROM no_such_table_zzz")
    except psycopg2.Error as captured:
        pg_err = captured
    assert pg_err is not None, "这条 SQL 本该失败，没失败就验不了 pgcode"

    def _boom(*a, **k):
        raise pg_err

    monkeypatch.setattr(refresh_dims, "refresh_all", _boom)
    with caplog.at_level(logging.WARNING, logger="scm.api"):  # noqa: SIM117
        with TestClient(api.create_app()) as c:
            assert c.get("/health").status_code == 200
    assert type(pg_err).__name__ in caplog.text, \
        f"缺异常类名：{caplog.text!r}"
    assert pg_err.pgcode in caplog.text, f"缺 pgcode：{caplog.text!r}"


def test_a_busy_lock_at_startup_is_skipped_not_treated_as_a_failure(
        wipe, monkeypatch, caplog):
    """★ advisory lock 被占（另一轮 CLI/运维触发正在跑）是正常状态，不是启动
    刷新失败——与 `jobs/scheduler.py::_tick` 对 `RefreshInFlight` 的处理保持
    同一判据：必须记成"跳过"，不能跟真失败混进同一种 `outcome=fail` 日志，
    否则运维会把正常的锁竞争当成真事故去查。"""
    _cfg(monkeypatch, startup_gate=True)

    def _busy(*a, **k):
        raise RefreshInFlight()

    monkeypatch.setattr(refresh_dims, "refresh_all", _busy)
    with caplog.at_level(logging.WARNING, logger="scm.api"):  # noqa: SIM117
        with TestClient(api.create_app()) as c:
            assert c.get("/health").status_code == 200
    assert "outcome=skipped" in caplog.text and "busy" in caplog.text
    assert "outcome=fail" not in caplog.text, \
        f"抢锁被占不许算成失败：{caplog.text!r}"


def test_boot_with_unreachable_pg_still_serves_health_and_names_the_cause(
        wipe, monkeypatch, caplog):
    """★ OQ-8 的原文是「不拦启动，**也不拦** `/health` / `/v1/readiness`」，
    而实现只把 CH 侧（触发的那次刷新）兜住了：读新鲜度的那一行在 `try` 之外，
    PG 连不上时异常直接冲出 lifespan，应用整个起不来 —— 容器里就是无限崩溃重启，
    而查「为什么起不来」要用的正是那两个口子。

    ★ 不 monkeypatch `_stale_mirrors` 造一个假异常：那验的是「假异常接不接得住」，
      不是「PG 真连不上时应用起不起得来」。把连接配置指到本机一个死端口，
      让 psycopg2 真的去连、真的被拒（loopback，不依赖内网、不依赖超时）。
    """
    _cfg(monkeypatch, startup_gate=True)
    from shared import pg_client
    dead = {**pg_client.business_pg(), "host": "127.0.0.1", "port": 5499}
    monkeypatch.setattr(pg_client, "business_pg", lambda: dead)

    with caplog.at_level(logging.WARNING, logger="scm.api"):  # noqa: SIM117
        with TestClient(api.create_app()) as c:
            assert c.get("/health").status_code == 200, "PG 连不上也不许拖垮 /health"
    assert "stage=freshness_read" in caplog.text, (
        f"没点名是哪一步失败的：{caplog.text!r}")
    assert "OperationalError" in caplog.text, (
        f"日志必须带异常类名，不能只有 str(e)：{caplog.text!r}")
    assert "5499" in caplog.text, (
        f"日志必须点名打的谁（host:port）—— 否则下次得重新复现：{caplog.text!r}")


def test_suite_default_never_triggers_a_refresh_even_with_stale_mirrors(wipe, monkeypatch):
    """★ fix round 1：conftest 把 `[freshness] startup_gate` 全局钉成 false ——
    不这样，任何一个带 `with TestClient(...)` 对着空表/陈旧镜像跑 lifespan 的
    测试，都会在默认值 `startup_gate=true` 下真的去抢 advisory lock、打真 CH。

    ★ 本测试刻意不调用 `_cfg()`、不 monkeypatch `freshness` ——
    验的正是"什么都不做"时的默认保护，而不是某个测试自己记得关。
    """
    calls = []
    monkeypatch.setattr(refresh_dims, "refresh_all",
                        lambda *a, **k: (calls.append((a, k)), [])[1])
    with TestClient(api.create_app()) as c:      # wipe：四张镜像全空，天然陈旧
        assert c.get("/health").status_code == 200
    assert calls == [], f"套件默认值必须保护 CH 不被打——实际触发了 {calls}"
