"""调度器。★ 关着的调度器要吵一声 —— 不执行的东西不会失败。"""
import logging

from apscheduler.triggers.cron import CronTrigger

from jobs import scheduler as S
from jobs.lock import advisory_lock
from shared import config as config_module
from tests.helpers import make_ch_unreachable


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


def test_bad_refresh_timezone_names_the_config_key(monkeypatch):
    """★ 终审 M-3：`refresh_at` 有 `_hour_minute()` 包一层、消息里带
    `[freshness] refresh_at`；`refresh_timezone` 旁边什么都没有，裸抛
    `ZoneInfoNotFoundError: 'No time zone found with key Asia/Shangahi'`。
    启动即炸是对的（配置类错误往启动钩子放），但拿到这句话的人不知道该去改
    哪个文件的哪一行 —— 两者不对称，而不对称的那一半迟早被当成 bug 去查代码。
    """
    _with_freshness(monkeypatch, refresh_timezone="Asia/Shangahi")
    try:
        S.build_scheduler()
    except ValueError as e:
        assert "refresh_timezone" in str(e), f"没点名配置键：{e}"
        assert "Asia/Shangahi" in str(e), f"没回显写错的值：{e}"
    else:
        raise AssertionError("时区写错了却起得来")


def test_freshness_config_has_every_key_even_when_toml_omits_them(monkeypatch):
    monkeypatch.setattr(config_module, "_CFG", {}, raising=False)
    config_module._CFG = {}
    got = config_module.freshness()
    assert set(got) >= {"max_age_hours", "refresh_at", "scheduler_enabled",
                        "coverage_drop_threshold", "startup_gate", "refresh_timezone"}


def test_tick_returns_cleanly_when_lock_is_busy(wipe, scm_log):
    """★ 锁被占是正常状态（另一轮 CLI/运维触发正在跑），不是任务出错 ——
    `_tick()` 必须就地接住 `RefreshInFlight`，打一条日志、干净返回，
    不许把它扔进 APScheduler 的事件循环让 `_on_error` 当成一次 job 失败。

    ★ 夹具从裸 `caplog` 换成 `scm_log`：同一批里其它日志断言已经因为
    `scm` 的 propagate=False 而只在别的测试先跑过时才绿（见 `1884440`），
    这条长在同一片沙地上。"""
    with scm_log.at_level(logging.WARNING, logger="scm.jobs"), advisory_lock():
        S._tick()   # 不能抛 —— 抛了这条测试本身就会失败
    assert "refresh_in_flight" in scm_log.text


def test_tick_returns_cleanly_when_ch_is_unreachable(wipe, scm_log, monkeypatch):
    """★ 复审残留 ②：`ChUnreachable` 逃出了 `_tick()`，被 APScheduler 的
    `_on_error` 记成泛泛的 `op=scheduled_job outcome=fail` + 一页 traceback ——
    四个入口里唯独夜跑这条没有点名的收场（CLI 退出码 1、运维接口 503、
    直调拿到带 runs 的异常，都收得干干净净）。

    证据行照样写全（那是 I-3 修好的），缺的是**这一轮怎么结束**：
    「CH 连不上」与「_tick 自己有 bug」被吞进同一种 fail 日志，
    与 `RefreshInFlight` 当初要分开的正是同一件事。
    """
    make_ch_unreachable(monkeypatch)
    with scm_log.at_level(logging.WARNING, logger="scm.jobs"):
        S._tick()   # 不能抛
    assert "reason=ch_unreachable" in scm_log.text, f"没点名这一轮为什么结束：{scm_log.text!r}"
    assert "err_type=ChUnreachable" in scm_log.text, f"没带异常类名：{scm_log.text!r}"
    assert "outcome=skipped" not in scm_log.text, (
        "CH 连不上是真失败，不是「跳过」—— 与锁被占必须分得开")
