"""进程内定时（负责人裁定：APScheduler，不用外部 cron，不用 pg_cron）。

★ 频率与时刻的理由同 07:481：CH 延迟 ≥ 1 天，跑更密没有新数据，所以每日一次、
  落在采集窗口之后。
"""
from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from apscheduler.events import EVENT_JOB_ERROR
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from jobs.lock import RefreshInFlight
from jobs.refresh_dims import refresh_all
from shared.config import freshness

log = logging.getLogger("scm.jobs")

REFRESH_JOB_ID = "refresh_dims"
#: 睡醒/重启后补跑一次（1 小时内），不补跑一串。
MISFIRE_GRACE_S = 3600

_SCHEDULER: AsyncIOScheduler | None = None


def _hour_minute(raw: str) -> tuple[int, int]:
    """★ 配置类错误往启动钩子放：写错了要现在炸，不是明天 06:30 悄悄不跑。"""
    try:
        h, m = raw.split(":")
        return int(h), int(m)
    except Exception as e:
        raise ValueError(f"[freshness] refresh_at 必须是 \"HH:MM\"，收到 {raw!r}") from e


def _timezone(raw: str) -> ZoneInfo:
    """★ 终审 M-3：裸 `ZoneInfo(...)` 抛的是
    `ZoneInfoNotFoundError: 'No time zone found with key Asia/Shangahi'` ——
    启动即炸是对的，但拿到这句话的人不知道该去改哪个文件的哪一行。
    `refresh_at` 旁边已经有 `_hour_minute()` 点名 `[freshness] refresh_at`，
    两者不对称，而不对称的那一半迟早被当成 bug 去查代码。"""
    try:
        return ZoneInfo(raw)
    except Exception as e:
        raise ValueError(
            f"[freshness] refresh_timezone 必须是一个 IANA 时区名"
            f"（如 \"Asia/Shanghai\"），收到 {raw!r}：{e}") from e


def _on_error(event) -> None:
    # ★ APScheduler 默认把 job 异常吞进它自己的 logger —— 不挂这个监听器，
    #   刷新崩了在 scm.* 的日志里一个字都看不到。这道网只接得住 `_tick()`
    #   自己没兜住的异常（编程错误一类）——`RefreshInFlight` 在 `_tick()`
    #   内部就地吃掉，不会走到这里。
    log.warning("op=scheduled_job job=%s outcome=fail", event.job_id,
                exc_info=event.exception)


def _tick() -> None:
    """job 函数本体：与 CLI / 运维接口共用同一个 `refresh_all`、同一把
    advisory lock（设计 §5「互斥」）。

    ★ 锁被占（另一轮 CLI 或运维触发正在跑）是**正常**状态，不是任务出错——
      `RefreshInFlight` 必须在这里就地接住、记一条日志、干净返回，绝不能
      冒出 `_tick()` 让 APScheduler 把它当成一次 job 失败（那样会跟真正的
      编程错误一起被 `_on_error` 吞进同一种「fail」日志，查的时候分不清
      「锁被占，正常」和「真的炸了」）。
    """
    log.info("op=scheduled_job outcome=start trigger=scheduler")
    try:
        runs = refresh_all("scheduler", actor=None)
    except RefreshInFlight:
        log.warning("op=scheduled_job outcome=skipped reason=refresh_in_flight")
        return
    ok = sum(1 for r in runs if r.ok)
    log.info("op=scheduled_job outcome=finish mirrors=%d ok=%d failed=%d",
             len(runs), ok, len(runs) - ok)


def build_scheduler() -> AsyncIOScheduler | None:
    cfg = freshness()
    if not cfg.get("scheduler_enabled", True):
        log.warning("op=scheduler outcome=disabled scheduler_enabled=false"
                    " —— 镜像不会自动刷新，只能靠 CLI 或 /v1/jobs/refresh-dims")
        return None
    hour, minute = _hour_minute(str(cfg["refresh_at"]))
    tz = _timezone(str(cfg["refresh_timezone"]))
    sch = AsyncIOScheduler(timezone=tz)
    sch.add_job(_tick, id=REFRESH_JOB_ID,
                trigger=CronTrigger(hour=hour, minute=minute, timezone=tz),
                coalesce=True, misfire_grace_time=MISFIRE_GRACE_S,
                max_instances=1, replace_existing=True)
    sch.add_listener(_on_error, EVENT_JOB_ERROR)
    log.info("op=scheduler outcome=built at=%02d:%02d tz=%s", hour, minute, tz)
    return sch


def start() -> None:
    global _SCHEDULER
    _SCHEDULER = build_scheduler()
    if _SCHEDULER is not None:
        _SCHEDULER.start()
        log.info("op=scheduler outcome=started jobs=%d", len(_SCHEDULER.get_jobs()))


def shutdown() -> None:
    global _SCHEDULER
    if _SCHEDULER is not None:
        _SCHEDULER.shutdown(wait=False)
        log.info("op=scheduler outcome=stopped")
        _SCHEDULER = None
