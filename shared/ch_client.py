"""ClickHouse 客户端 —— 只读 jxd_raw。

★ 内网强制无代理：HTTP(S)_PROXY 会把 192.168.66.211 劫持掉，表现是 503 而不是
  「连不上」，于是没人会想到去看代理。这条是邻居仓 model_inventory_forecast
  实测出来的，照搬它的 PoolManager 写法。
★ dim/ 不许 import 本模块（tests/test_layering.py:55）—— 客户端由 jobs/ 注入。
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable

import clickhouse_connect
from clickhouse_connect.driver import httputil

from shared.config import clickhouse

log = logging.getLogger("scm.ch")

#: 内网直连。5 秒连不上就是网络/主机出问题了，不是慢。
CH_CONNECT_TIMEOUT_S = 5

Query = Callable[[str], list[tuple]]


def describe_failure(e: BaseException) -> dict:
    """★ 「超时」与「连不上」必须分得开：前者调大超时有用，后者一行都不生效。"""
    cause = getattr(e, "cause", None) or e.__cause__
    code = getattr(cause, "errno", None) or getattr(e, "errno", None)
    kind = "timeout" if isinstance(e, TimeoutError) else ("connect" if code else "other")
    return {"kind": kind, "type": type(e).__name__, "code": code, "msg": str(e)}


def ch_client():
    c = clickhouse()
    pm = httputil.get_pool_manager(http_proxy=None, https_proxy=None)   # ★ 无代理直连
    return clickhouse_connect.get_client(
        host=c["host"], port=int(c.get("port", 8123)),
        username=c.get("user", "default"), password=c.get("password", ""),
        database=c.get("database", "jxd_raw"), secure=c.get("secure", False),
        connect_timeout=CH_CONNECT_TIMEOUT_S, pool_mgr=pm,
    )


def ch_query(client) -> Query:
    """把查询包上日志三问：打的谁（host/db）· 多久 · 怎么失败的（kind + errno）。"""
    c = clickhouse()
    where = f"{c['host']}:{c.get('port', 8123)}/{c.get('database', 'jxd_raw')}"

    def run(sql: str) -> list[tuple]:
        head = " ".join(sql.split())[:120]
        t0 = time.perf_counter()
        try:
            rows = list(client.query(sql).result_rows)
        except BaseException as e:
            log.warning("op=ch_query target=%s elapsed_ms=%d outcome=fail %s sql=%s",
                        where, (time.perf_counter() - t0) * 1000, describe_failure(e), head)
            raise
        log.info("op=ch_query target=%s elapsed_ms=%d outcome=ok rows=%d sql=%s",
                 where, (time.perf_counter() - t0) * 1000, len(rows), head)
        return rows

    return run
