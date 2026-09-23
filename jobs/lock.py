"""三个入口共用的互斥锁。

★ 用 PG 的**会话级** advisory lock：它随连接断开自动释放，所以进程被 kill -9
  也不会留一把没人解的锁。代价是这个连接必须在整轮刷新期间一直开着。
"""
from __future__ import annotations

import contextlib
import logging

from shared.pg_client import pg_conn

log = logging.getLogger("scm.jobs")

#: 任取的常量，全仓唯一即可。取设计定稿日，方便在 pg_locks 里认出它是谁。
ADVISORY_LOCK_KEY = 20260922


class RefreshInFlight(Exception):
    def __init__(self):
        super().__init__(
            f"另一轮镜像刷新正在跑（advisory lock {ADVISORY_LOCK_KEY}）。"
            "★ 不排队也不返回成功 —— 排队会让两轮写同一张表，"
            "返回成功会让调用方以为刷过了")


@contextlib.contextmanager
def advisory_lock():
    with pg_conn(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
        if not cur.fetchone()[0]:
            log.warning("op=advisory_lock key=%s outcome=busy", ADVISORY_LOCK_KEY)
            raise RefreshInFlight()
        log.info("op=advisory_lock key=%s outcome=acquired", ADVISORY_LOCK_KEY)
        try:
            yield
        finally:
            cur.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))
            log.info("op=advisory_lock key=%s outcome=released", ADVISORY_LOCK_KEY)
