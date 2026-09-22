"""业务库（PG）连接。一次一连，用完即关。"""
from __future__ import annotations

import contextlib
import logging
import re
import time

import psycopg2
import psycopg2.extensions

from shared.config import business_pg

log = logging.getLogger("scm.pg")

#: 内网直连，3 秒连不上就是网络/主机出问题了。
CONNECT_TIMEOUT_S = 3

#: 光秃秃的小写标识符。PG 会折叠大小写，允许大写会让 "Scm" 和 "scm"
#: 看起来是两个 schema 实际是一个。
_SCHEMA_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class BadSchemaName(Exception):
    def __init__(self, schema):
        super().__init__(f"schema 名必须匹配 {_SCHEMA_RE.pattern}，收到 {schema!r}")


def check_schema(schema: str) -> str:
    """PG 没有 {db:Identifier} 这种参数，schema 名只能拼进 SQL 文本 —— 先过白名单。"""
    if not isinstance(schema, str) or not _SCHEMA_RE.match(schema):
        raise BadSchemaName(schema)
    return schema


def business_schema() -> str:
    return check_schema(business_pg().get("schema", "scm"))


def pg_error_fields(e: psycopg2.Error) -> dict:
    """★ 只取 str(e) 等于丢掉全部上下文：唯一冲突与外键冲突的 message 都长得像一句话，
    真凶在 pgcode 与 constraint_name 里。日志与错误码映射都靠它。"""
    d = getattr(e, "diag", None)
    return {
        "pgcode": getattr(e, "pgcode", None),
        "constraint": getattr(d, "constraint_name", None),
        "table": getattr(d, "table_name", None),
        "primary": getattr(d, "message_primary", None),
        "detail": getattr(d, "message_detail", None),
    }


@contextlib.contextmanager
def timed(op: str, **fields):
    """日志三问：打的谁（op + host/db）· 多久（elapsed_ms）· 怎么失败的（pgcode / constraint）。"""
    c = business_pg()
    where = f"{c['host']}:{c.get('port', 5432)}/{c['dbname']}.{business_schema()}"
    t0 = time.perf_counter()
    try:
        yield
    except psycopg2.Error as e:
        log.warning("op=%s target=%s elapsed_ms=%d outcome=pg_error %s %s",
                    op, where, (time.perf_counter() - t0) * 1000, pg_error_fields(e), fields)
        raise
    except BaseException as e:
        log.warning("op=%s target=%s elapsed_ms=%d outcome=%s msg=%s %s",
                    op, where, (time.perf_counter() - t0) * 1000, type(e).__name__, e, fields)
        raise
    else:
        log.info("op=%s target=%s elapsed_ms=%d outcome=ok %s",
                 op, where, (time.perf_counter() - t0) * 1000, fields)


@contextlib.contextmanager
def pg_conn(autocommit: bool = False):
    """一次一连。不做连接池：写入是低频的人工操作，而池化会引入
    「连接跨请求复用时 search_path / 事务状态残留」这类难查的问题。

    ★ 必须设 search_path：迁移里的 plpgsql 函数体用的是不带 schema 前缀的表名，
      同一份 SQL 才能同时跑在 scm 与 scm_test 上。
    """
    c = business_pg()
    schema = business_schema()
    conn = psycopg2.connect(
        host=c["host"], port=int(c.get("port", 5432)),
        user=c["user"], password=c.get("password", ""),
        dbname=c["dbname"], connect_timeout=CONNECT_TIMEOUT_S,
    )
    conn.autocommit = autocommit
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {schema}")
        yield conn
        if not autocommit:
            # ★ 块内可能用 pytest.raises（或任何 try/except）吞掉了一次库层异常再正常退出——
            #   此时事务已 aborted，PG 对它的 COMMIT 会静默折成 ROLLBACK（无异常、无日志），
            #   同一事务里更早的写入会一并消失。2026-09-22 Task 4 复核实测踩到：两行
            #   plan/plan_rev 就这样无声无息地丢了。宁可在这里响亮地炸，也不能替调用方
            #   悄悄兜底——兜底了它就再也不知道自己吞过一次异常。
            if conn.get_transaction_status() == psycopg2.extensions.TRANSACTION_STATUS_INERROR:
                conn.rollback()
                raise RuntimeError(
                    "pg_conn: 事务已中止却走到了 commit —— 块内有异常被吞掉了")
            conn.commit()
    except BaseException:
        if not autocommit:
            conn.rollback()
        raise
    finally:
        conn.close()
