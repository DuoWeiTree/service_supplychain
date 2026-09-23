"""调度器。★ 关着的调度器要吵一声 —— 不执行的东西不会失败。"""
import logging

from apscheduler.triggers.cron import CronTrigger

from jobs import scheduler as S
from jobs.lock import advisory_lock
from shared import config as config_module


def _with_freshness(monkeypatch, **kw):
    base = {"max_age_hours": 24, "refresh_at": "06:30", "scheduler_enabled": True,
            "coverage_drop_threshold": 0.30, "startup_gate": True,   # OQ-6 裁定 09-22
            "refresh_timezone": "Asia/Shanghai"}
    monkeypatch.setattr(config_module, "_CFG", {"freshness": {**base, **kw}}, raising=False)
    config_module._CFG = {"freshness": {**base, **kw}}


def test_cron_uses_the_configured_time_and_timezone(monkeypatch):
    _with_freshness(monkeypatch, refresh_at="07:15")
    sch = S.build_scheduler()
    job = sch.get_job(S.REFRESH_JOB_ID)
    assert isinstance(job.trigger, CronTrigger)
    assert (str(job.trigger.fields[job.trigger.FIELD_NAMES.index("hour")]) == "7"
            and str(job.trigger.fields[job.trigger.FIELD_NAMES.index("minute")]) == "15")
    assert "Shanghai" in str(job.trigger.timezone)


def test_misfire_and_coalesce_are_set(monkeypatch):
    _with_freshness(monkeypatch)
    job = S.build_scheduler().get_job(S.REFRESH_JOB_ID)
    assert job.coalesce is True and job.misfire_grace_time == 3600


def test_disabled_scheduler_says_so_out_loud(monkeypatch, caplog):
    _with_freshness(monkeypatch, scheduler_enabled=False)
    with caplog.at_level(logging.WARNING, logger="scm.jobs"):
        assert S.build_scheduler() is None
    assert "scheduler_enabled=false" in caplog.text


def test_bad_refresh_at_fails_at_build_not_at_0630(monkeypatch):
    """★ 配置类错误往启动钩子放 —— 等到第二天早上才炸是查不回来的。"""
    _with_freshness(monkeypatch, refresh_at="半夜")
    try:
        S.build_scheduler()
    except ValueError as e:
        assert "refresh_at" in str(e)
    else:
        raise AssertionError("配置写错了却起得来")


def test_freshness_config_has_every_key_even_when_toml_omits_them(monkeypatch):
    monkeypatch.setattr(config_module, "_CFG", {}, raising=False)
    config_module._CFG = {}
    got = config_module.freshness()
    assert set(got) >= {"max_age_hours", "refresh_at", "scheduler_enabled",
                        "coverage_drop_threshold", "startup_gate", "refresh_timezone"}


def test_tick_returns_cleanly_when_lock_is_busy(wipe, caplog):
    """★ 锁被占是正常状态（另一轮 CLI/运维触发正在跑），不是任务出错 ——
    `_tick()` 必须就地接住 `RefreshInFlight`，打一条日志、干净返回，
    不许把它扔进 APScheduler 的事件循环让 `_on_error` 当成一次 job 失败。"""
    with caplog.at_level(logging.WARNING, logger="scm.jobs"), advisory_lock():
        S._tick()   # 不能抛 —— 抛了这条测试本身就会失败
    assert "refresh_in_flight" in caplog.text
