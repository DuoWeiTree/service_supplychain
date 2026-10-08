"""调度器。★ 关着的调度器要吵一声 —— 不执行的东西不会失败。"""
import logging

from apscheduler.triggers.cron import CronTrigger

from jobs import scheduler as S
from jobs.lock import advisory_lock
from shared import config as config_module
from tests.helpers import make_ch_unreachable


def _with_freshness(monkeypatch, **kw):
    """★ 复核（sku-image 计划 Task 3~4 之间，负责人要求先弄红一次）：这行
    `monkeypatch.setattr` 看起来像 `tests/test_pm_client.py::_point_config_at`
    当初踩过的那颗地雷（改全局配置缓存、测试结束后不还原），但**机制不同，
    不是同一类 bug**——判据不是"有没有裸赋值"，是"有没有先经过
    `monkeypatch.setattr` 给 `_CFG` 建档"：建过档的，`.undo()`（测试结束时
    自动触发）按"setattr 那一刻记下的原值"无条件写回去，不管中途这个属性
    又被谁改成了什么；`_point_config_at` 那颗地雷是**从没建档**（只
    `setenv`，然后裸调 `reset_cache()`），所以没有任何东西负责把它还原。
    两者长得像，判据不一样——`test_freshness_pollution_does_not_survive_
    teardown` 用独立的 `MonkeyPatch()` 实例把这个机制单独重放了一遍，下次
    看见类似写法不必重跑一遍这次调查，直接查这条判据。"""
    base = {"max_age_hours": 24, "refresh_at": "06:30", "scheduler_enabled": True,
            "coverage_drop_threshold": 0.30, "startup_gate": True,   # OQ-6 裁定 09-22
            "refresh_timezone": "Asia/Shanghai"}
    monkeypatch.setattr(config_module, "_CFG", {"freshness": {**base, **kw}}, raising=False)


def test_freshness_pollution_does_not_survive_teardown(business_db):
    """★ 独立复现 `_with_freshness` 的 monkeypatch 撤销机制——不依赖测试跑在
    谁前谁后（那样的证据会被"这次刚好没轮到"蒙混过去）。

    构造的正是负责人要求的那个顺序：① 前一个"测试"（这里用手动的
    `MonkeyPatch()` 代替，效果等价）用 `_with_freshness` 同款写法把 `_CFG`
    换成只有 `[freshness]` 的残缺配置；② 它的 `.undo()`（等价于该测试用例
    结束）；③ 后一个测试读 `business_pg()`——如果地雷是真的，这里应该
    `KeyError: 'business_pg'`。"""
    from _pytest.monkeypatch import MonkeyPatch

    from shared.pg_client import business_schema

    before = business_schema()  # 先确认这台测试库当前是干净、可用的（scm_test）

    mp = MonkeyPatch()
    _with_freshness(mp, refresh_at="07:15")
    assert config_module.freshness()["refresh_at"] == "07:15"  # 污染确实发生了
    mp.undo()  # ★ 等价于用了 _with_freshness 的那条测试跑完、monkeypatch 收尾

    # 后一个"测试"：business_pg() 不许 KeyError，_CFG 必须已经原样恢复
    assert business_schema() == before


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
    monkeypatch.setattr(config_module, "_CFG", {}, raising=False)  # 同 _with_freshness：已建档，安全
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


# ---- 终审修复波 #1：卡住的下单调用不能等下一次重启 ----

def test_recover_stuck_runs_every_v5_minutes(monkeypatch):
    import datetime as dt

    from apscheduler.triggers.interval import IntervalTrigger
    _with_freshness(monkeypatch)
    job = S.build_scheduler().get_job(S.RECOVER_JOB_ID)
    assert isinstance(job.trigger, IntervalTrigger)
    assert job.trigger.interval == dt.timedelta(minutes=config_module.erp()["stale_in_flight_minutes"])
    assert job.coalesce is True and job.max_instances == 1


def test_recover_tick_failure_is_logged_not_raised(monkeypatch, scm_log):
    from usecase import place_po

    def boom(*a, **k):
        raise RuntimeError("pg gone")
    monkeypatch.setattr(place_po, "recover_stuck", boom)
    S._recover_tick()
    assert "op=recover_stuck_job outcome=fail" in scm_log.text and "pg gone" in scm_log.text
