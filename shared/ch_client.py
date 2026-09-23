"""ClickHouse 客户端 —— 只读 jxd_raw。

★ 内网强制无代理：HTTP(S)_PROXY 会把 192.168.66.211 劫持掉，表现是 503 而不是
  「连不上」，于是没人会想到去看代理。这条是邻居仓 model_inventory_forecast
  实测出来的，照搬它的 PoolManager 写法。
★ dim/ 不许 import 本模块（tests/test_layering.py:55）—— 客户端由 jobs/ 注入。

★ Task 5 收紧：只读不能只靠文档承诺 —— `clickhouse_connect` 原始客户端上
  `command()` / `insert()` 两个写方法必须**出不了这个模块**。`ch_client()`
  的模块级入口只交出下面这层薄封装，`insert`/`command` 不在它身上。
  ★ 终审 M-2 订正：这**不是**「类型系统就是护栏本身」—— `__slots__` 只挡住了
  `__dict__`，`w._raw.command(...)` 一次属性访问就拿得回原始客户端。真正在
  守的是两条：① 下划线约定（`_raw` 出现在别处 review 看得见）；
  ② `tests/test_layering.py::test_l7_no_writes_to_clickhouse` 按正则扫
  `jobs/**` 与本文件里「写动词 + jxd_raw」的组合。把护栏说得比实际强，
  下一个人就会据此少做一次检查。
"""
from __future__ import annotations

import logging
import socket
import time
from collections.abc import Callable

import clickhouse_connect
from clickhouse_connect.driver import httputil

from shared.config import clickhouse

log = logging.getLogger("scm.ch")

#: 内网直连。5 秒连不上就是网络/主机出问题了，不是慢。
CH_CONNECT_TIMEOUT_S = 5

#: ★ 终审 M-7：`clickhouse_connect` 的 `send_receive_timeout` 默认 **300 秒**
#:   （`driver/httpclient.py`）。不覆盖它，CH 接了 TCP 却不回数据时，启动钩子
#:   里同步跑的那轮刷新最坏会把 lifespan 卡住 300s × 4 张镜像 —— 这段时间
#:   `/health` 同样不可达，与 I-1 是同一个代价、换了一扇门。
#:   一轮真刷新实测 0.7 秒，60 秒已经宽得离谱，还能在一分钟内把问题暴露出来。
CH_SEND_RECEIVE_TIMEOUT_S = 60

Query = Callable[[str], list[tuple]]


class ReadOnlyClient:
    """只暴露 `query()`。★ 不用组合出转发全部属性的 `__getattr__` —— 那样
    `command`/`insert` 一样会经由属性穿透逃出去，收紧就成了摆设。"""

    __slots__ = ("_raw",)

    def __init__(self, raw) -> None:
        self._raw = raw

    def query(self, sql: str):
        return self._raw.query(sql)


def _cause_chain(e: BaseException) -> list[BaseException]:
    """从最外层一路挖到最里层。★ 必须同时看 `__cause__` 与 `__context__` ——
    `raise X from e` 走前者，裸 `raise X`（驱动里更常见）走后者，只看一个就会
    在半路断掉，而 errno 与真正的成因全在最里层。带 `seen` 是因为异常链理论上
    可以成环，转不出去就只剩一个卡死的进程和零条日志。"""
    chain: list[BaseException] = []
    seen: set[int] = set()
    cur: BaseException | None = e
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        chain.append(cur)
        cur = cur.__cause__ or cur.__context__
    return chain


def _classify(exc: BaseException) -> str | None:
    """★ 只按**标准库**异常判，不按 urllib3 的类判。

    2026-09-22 实测（clickhouse-connect 1.5.0 · urllib3 2.8.0），三种真形态的
    cause 链末端都是标准库异常：

        连接被拒 127.0.0.1:9   OperationalError ← MaxRetryError ← NewConnectionError  ← ConnectionRefusedError(111)
        建连超时 10.255.255.1  OperationalError ← MaxRetryError ← ConnectTimeoutError ← TimeoutError
        读超时（接上不回话）    OperationalError ← ReadTimeoutError ← TimeoutError
        域名解析失败            OperationalError ← MaxRetryError ← NameResolutionError ← socket.gaierror(-2)

    ★ 为什么不按 urllib3 的类判：它的继承关系是**反的** ——
      `NameResolutionError ⊂ NewConnectionError ⊂ ConnectTimeoutError`。
      照「ConnectTimeoutError → timeout」写，连接被拒会被报成超时，
      于是有人去调大超时，而那是一行都不会生效的改动。
      顺带：`urllib3` 是 `HTTP_CLIENTS`，`test_l4_l5_outbound_http_only_in_erp`
      禁止 `shared/` import 它 —— 按标准库判连这条规则都不用动。
    """
    if isinstance(exc, socket.gaierror):
        return "dns"
    if isinstance(exc, ConnectionRefusedError):
        return "connect_refused"
    if isinstance(exc, TimeoutError):
        # ★ 建连超时与读超时都落这里：两者的处置相同（调大超时可能有用），
        #   而「是哪一种」由 causes 链里的 ConnectTimeoutError / ReadTimeoutError
        #   如实点名 —— 不在 kind 上多劈一维去背一个没人按它分叉的区别。
        return "timeout"
    if isinstance(exc, OSError) and exc.errno is not None:
        # 无路由 / 网络不可达 / 连接被重置……：连得上与否这一侧的其它形态。
        return "connect_failed"
    return None


def describe_failure(e: BaseException) -> dict:
    """★ 「超时」与「连不上」必须分得开：前者调大超时有用，后者一行都不生效。

    ★ 终审 I-2：旧版按 `isinstance(e, TimeoutError)` 与 `e.errno` 判，而真驱动
      抛的是 `clickhouse_connect...OperationalError` —— 它既不是 `TimeoutError`，
      身上也没有 `errno`（errno 在 cause 链最里层），于是两种真形态的 `kind`
      **都是 `other`**，判据从未生效。守它的那条测试喂的是手搓的内建
      `TimeoutError` 与带 `.errno` 的 `OSError`，这条路径上永远不会出现的两个形状。
    """
    chain = _cause_chain(e)
    kind = next((k for k in (_classify(x) for x in chain) if k), "other")
    code = next((c for c in (getattr(x, "errno", None) for x in chain) if c is not None), None)
    return {"kind": kind, "type": type(e).__name__,
            "causes": " <- ".join(type(x).__name__ for x in chain),
            "code": code, "msg": str(e)}


def ch_client() -> ReadOnlyClient:
    c = clickhouse()
    pm = httputil.get_pool_manager(http_proxy=None, https_proxy=None)   # ★ 无代理直连
    raw = clickhouse_connect.get_client(
        host=c["host"], port=int(c.get("port", 8123)),
        username=c.get("user", "default"), password=c.get("password", ""),
        database=c.get("database", "jxd_raw"), secure=c.get("secure", False),
        connect_timeout=CH_CONNECT_TIMEOUT_S,
        send_receive_timeout=CH_SEND_RECEIVE_TIMEOUT_S, pool_mgr=pm,
    )
    return ReadOnlyClient(raw)


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
