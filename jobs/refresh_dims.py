"""维度镜像刷新。取数在 dim/，落库与留痕在这里（设计 §7.1）。

★ upsert 的 SQL 只能写在本层：test_l7_no_writes_to_clickhouse 按正则扫 dim/ 里的
  `insert into`，一段 upsert 字符串就会让它红。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
import time
from typing import NamedTuple

import psycopg2
from psycopg2.extras import execute_values

from dim import ch_source, registry
from jobs.lock import RefreshInFlight, advisory_lock
from shared.ch_client import ch_client, ch_query, describe_failure
from shared.config import clickhouse, freshness
from shared.pg_client import pg_conn, pg_error_fields, timed

log = logging.getLogger("scm.jobs")

#: 与迁移 006 `dim_refresh_run_trigger_known` 的 `CHECK (trigger IN (...))`
#: 同一张白名单——在 Python 侧复述而不是回查 `information_schema` 现查这条
#: CHECK：约束的取值集合几乎不会变，回查反而多一次可能失败的 I/O，且早不
#: 早于建立数据库连接就该拒绝，见下面 `refresh_all` 的用法。
ALLOWED_TRIGGERS = ("scheduler", "cli", "api")

#: review 09-22 二轮：provenance INSERT 本身在 DB 层失败时没有行可引用——
#: 这条 INSERT 是唯一一次写 `dim_refresh_run` 的机会（brief 的「只追加一条
#: 完整的行」判据），失败了不会有"再补一条"的余地，只能用哨兵值占位。
NO_PROVENANCE_ROW = -1


class CoverageDrop(Exception):
    pass


class UnknownTrigger(ValueError):
    """★ review 09-22 二轮：调用方传错 trigger 必须在碰任何镜像之前就地爆炸，
    不能长得像"这批镜像刷新失败了"——运维会去查错镜像，而真正的 bug
    在调用方那一行传参。"""

    def __init__(self, trigger: str):
        super().__init__(
            f"trigger 必须是 {ALLOWED_TRIGGERS} 之一，收到 {trigger!r}")


class UnknownActor(ValueError):
    """★ 同上一条：`actor` 不存在或已停用，是调用方的错，不是镜像刷新失败。"""

    def __init__(self, actor: str):
        super().__init__(f"actor {actor!r} 不存在或已停用")


class RunRow(NamedTuple):
    mirror: str
    run_id: int
    ok: bool
    rows_in: int
    rows_dropped: int
    error: str | None


class ChUnreachable(Exception):
    """CH 建连失败：这一轮一张镜像都刷不了。

    ★ 终审 I-3：原先建连排在 `advisory_lock()` 与任何 `try` 之外，于是整轮在
    进锁之前就炸掉，`dim_refresh_run` **一行都不留** —— 「昨晚 06:30 连不上
    CH」这件事在证据表里完全不存在，第二天最新一行还是前天那次成功，看起来
    像「昨天根本没排过班」。所以这个异常只在**给每张目标镜像都留过一行
    `ok=false` 之后**才抛，`runs` 带着那几行，让三个入口各自照自己的方式收场。
    """

    def __init__(self, target: str, cause: dict, runs: list[RunRow]):
        self.target, self.cause, self.runs = target, cause, runs
        super().__init__(f"ch_unreachable target={target} cause={cause}")


def _threshold() -> float:
    # ★ 设计取值 · 未实测（OQ-6 裁定 09-22 = 0.30）。兄弟仓实测过库存日报单日掉 40%，
    #   取比它更松的阈值，免得把 30% 内的正常波动也当掉档拒了。
    return float(freshness().get("coverage_drop_threshold", 0.30))


def _baseline(cur, mirror: str) -> tuple[int, dict] | None:
    cur.execute("SELECT rows_in, drop_reasons FROM dim_refresh_run"
                " WHERE mirror = %s AND ok ORDER BY run_id DESC LIMIT 1", (mirror,))
    row = cur.fetchone()
    return (row[0], row[1] or {}) if row else None


def _check_coverage(cur, m: registry.Mirror, fetched: ch_source.Fetched,
                    reasons: dict) -> None:
    """`07:485`：掉档超阈值 → 整批不可用、不写。★ 首轮没基线要留声，不是静默放行。

    ★ 2026-09-22 TDD 红态实测揪出一个坑：草案里首轮在 `base is None` 分支直接
    `return`，从没把这一轮的 `distinct_sid` 写进 `reasons`。于是第二轮找基线时
    `prev_reasons` 里根本没有 `distinct_sid` 可比，`msku_bridge` 少一个店那批
    重复行就会绕过覆盖面校验，直接砸进 `_upsert`，在 `ON CONFLICT DO UPDATE`
    里撞成一条原始的 `InFailedSqlTransaction`——而不是干净的 `CoverageDrop`。
    所以 distinct 计数这一段不能挂在「有没有基线」的分支里，两条路径都必须走到。
    """
    limit = 1.0 - _threshold()
    base = _baseline(cur, m.name)
    if base is None:
        reasons["no_baseline"] = 1
        log.warning("op=coverage mirror=%s outcome=no_baseline rows_in=%d"
                    " —— 首轮无基线，本轮不做掉档比较", m.name, len(fetched.rows))
        prev_reasons: dict = {}
    else:
        prev_rows, prev_reasons = base
        if prev_rows and len(fetched.rows) < prev_rows * limit:
            raise CoverageDrop(f"coverage_drop rows {len(fetched.rows)} < {prev_rows} × {limit:.2f}")
    # ★ 一趟循环做两件事：算出这一轮的 distinct 计数（不管有没有基线都要记，
    #   下一轮要拿它当基线）、再拿它跟上一轮比（没基线就跳过比较，不跳过记录）。
    for rule in m.coverage:
        if not rule.startswith("distinct:"):
            continue
        col = rule.split(":", 1)[1]
        idx = m.columns.index(col)
        now = len({r[idx] for r in fetched.rows})
        reasons[f"distinct_{col}"] = now
        prev = prev_reasons.get(f"distinct_{col}")
        if prev and now < prev * limit:
            raise CoverageDrop(
                f"coverage_drop {rule} {now} < {prev} × {limit:.2f} —— "
                "整店 0 行是采集缺口的形状，总行数看不出来")


def _upsert(cur, m: registry.Mirror, rows: list[tuple], at: dt.datetime) -> None:
    """★ 只 upsert，绝不 DELETE 源里消失的行（设计 §6 判据 6）。"""
    cols = ", ".join([*m.columns, "refreshed_at"])
    keys = ", ".join(m.key_columns)
    sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in [*m.columns, "refreshed_at"]
                     if c not in m.key_columns)
    execute_values(
        cur,
        f"INSERT INTO {m.name} ({cols}) VALUES %s"
        f" ON CONFLICT ({keys}) DO UPDATE SET {sets}",
        [(*r, at) for r in rows])


def _check_actor(actor: str | None) -> None:
    """★ review 09-22 二轮：`actor` 必须存在且 active，一次查询搞定，
    在拿 advisory lock、跑任何镜像之前——`None` 允许（调度触发的那一轮
    背后没有人，006 的 `actor` 列本就可空）。"""
    if actor is None:
        return
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT active FROM actor WHERE actor_id = %s", (actor,))
        row = cur.fetchone()
    if row is None or not row[0]:
        raise UnknownActor(actor)


#: ★ 唯一一条写 `dim_refresh_run` 的 SQL 文本——独立成模块级常量 + 函数，是为了
#: 让 review 09-22 二轮要求的"这条 INSERT 本身在 DB 层失败"测试能直接
#: monkeypatch 这里，而不必绕开 `refresh_all` 已经挡住的 trigger/actor 校验
#: 去伪造一个「合法参数、DB 却拒绝」的场景。
_PROVENANCE_SQL = (
    "INSERT INTO dim_refresh_run (mirror, trigger, actor, started_at, finished_at,"
    " source_max_captured, rows_in, rows_dropped, drop_reasons, ok, error)"
    " VALUES (%s, %s, %s, %s, now(), %s, %s, %s, %s, %s, %s) RETURNING run_id"
)


def _write_provenance(cur, mirror: str, trigger: str, actor: str | None,
                      started_at: dt.datetime, source_max_captured, rows_in: int,
                      rows_dropped: int, reasons: dict, ok: bool,
                      error: str | None) -> int:
    cur.execute(_PROVENANCE_SQL,
                (mirror, trigger, actor, started_at, source_max_captured, rows_in,
                 rows_dropped, json.dumps(reasons, ensure_ascii=False), ok, error))
    return cur.fetchone()[0]


def _record_ch_failure(m: registry.Mirror, trigger: str, actor: str | None,
                       started_at: dt.datetime, error: str) -> RunRow:
    """给一张镜像补一行「这一轮压根没连上 CH」的证据。

    ★ 自己一条短事务：连不上 CH 时四张镜像的失败原因完全相同，但留痕仍要
    一张一行 —— 证据表的粒度是镜像，合并成一行会让「哪几张本该刷」这个问题
    第二天没有答案。留痕本身再失败也不许让这一轮悄悄消失（同 `refresh_one`
    的 provenance 兜底判据）：记一条 error 日志 + 哨兵 run_id。
    """
    try:
        with pg_conn() as conn, conn.cursor() as cur:
            run_id = _write_provenance(cur, m.name, trigger, actor, started_at,
                                       None, 0, 0, {"ch_unreachable": 1}, False, error)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:  # noqa: BLE001 - 见上：留痕写不出来也不能让异常改道
        if isinstance(e, psycopg2.Error):
            log.error("op=refresh_provenance mirror=%s trigger=%s outcome=fail"
                      " reason=ch_unreachable err_type=%s pg=%s",
                      m.name, trigger, type(e).__name__, pg_error_fields(e))
        else:
            log.error("op=refresh_provenance mirror=%s trigger=%s outcome=fail"
                      " reason=ch_unreachable err_type=%s err=%s",
                      m.name, trigger, type(e).__name__, e)
        run_id = NO_PROVENANCE_ROW
    return RunRow(m.name, run_id, False, 0, 0, error)


def _ch_query_or_record(targets: list[registry.Mirror], trigger: str,
                        actor: str | None):
    """建连，失败就先把证据写全再抛 `ChUnreachable`。

    ★ `ch_query()` 只包住**查询**，建连失败原先一条 `scm.*` 日志都没有 ——
    能看到的只有 urllib3 / clickhouse_connect 自己那两条第三方 WARNING，
    三问（打的谁 · 多久 · 怎么失败的）一条都答不上来。
    """
    c = clickhouse()
    target = f"{c['host']}:{c.get('port', 8123)}/{c.get('database', 'jxd_raw')}"
    t0 = time.perf_counter()
    try:
        query = ch_query(ch_client())
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:  # 建连失败的类型由驱动决定，分类交给 describe_failure
        cause = describe_failure(e)
        log.error("op=ch_connect target=%s elapsed_ms=%d outcome=fail %s",
                  target, (time.perf_counter() - t0) * 1000, cause)
        error = f"ch_unreachable target={target} {cause}"[:2000]
        started_at = dt.datetime.now(dt.UTC)
        runs = [_record_ch_failure(m, trigger, actor, started_at, error) for m in targets]
        raise ChUnreachable(target, cause, runs) from e
    log.info("op=ch_connect target=%s elapsed_ms=%d outcome=ok",
             target, (time.perf_counter() - t0) * 1000)
    return query


def refresh_one(cur, m: registry.Mirror, query, trigger: str, actor: str | None) -> RunRow:
    """★ `dim_refresh_run` 只追加（006 的 `BEFORE UPDATE OR DELETE` 触发器挡住了
    `UPDATE`）—— 整轮跑完只在收尾时写**一次完整的行**，不分「先 INSERT 占位、
    再 UPDATE 补结果」两次。`started_at` 在 Python 侧先取好时间戳，跟收尾那次
    INSERT 一起落库，而不是靠数据库的 `DEFAULT now()`（那样会记成收尾时刻）。

    ★ review 09-22 finding 1 修复：`_upsert()` 在 SQL 执行阶段抛出的原始 DB
    异常（唯一键冲突、NOT NULL、CHECK……）会把 PG 事务标记为 aborted——如果
    收尾那条 `INSERT INTO dim_refresh_run` 跟 `_upsert` 共享同一个未清理的
    事务，它自己也会跟着炸成 `InFailedSqlTransaction`，一路冒出到
    `refresh_all`，既丢了本该必写的留痕行，又会打断对其余镜像的遍历
    （`refresh_one` 只被调用到一半，循环压根走不到下一个 `m`）。
    `m.fetch()` / `_check_coverage()` 抛出的 Python 级异常（`UnknownShape`、
    `CoverageDrop`）能被干净地记录，纯粹是因为它们发生在**碰这个 cursor
    之前**，事务还没被弄脏——这是巧合，不是不变式，所以必须用 SAVEPOINT
    把「取数 + 校验 + upsert」这一段单独框起来：出错就 `ROLLBACK TO
    SAVEPOINT`，把连接从 aborted 状态里捞回来，让收尾的 `INSERT` 总能在一个
    干净的事务状态上执行。"""
    savepoint = f"mirror_{m.name}"
    started_at = dt.datetime.now(dt.UTC)
    rows_in = rows_dropped = 0
    reasons: dict = {}
    source_max_captured = None
    ok = False
    error: str | None = None
    cur.execute(f"SAVEPOINT {savepoint}")
    try:
        fetched = m.fetch(query)
        rows_in, rows_dropped = len(fetched.rows), fetched.dropped
        reasons = dict(fetched.drop_reasons)
        _check_coverage(cur, m, fetched, reasons)
        _upsert(cur, m, fetched.rows, dt.datetime.now(dt.UTC))
        source_max_captured = fetched.source_max_captured
        ok = True
    except BaseException as e:  # noqa: BLE001 - 必须兜住一切（CoverageDrop/UnknownShape/
        # pg 错误/……）才能保证收尾那条 INSERT 总会写：漏一种异常类型就是漏一批「今天刷过
        # 但没人知道为什么没成」的沉默失败。
        # ★ 出错的这段可能已经把事务弄脏（_upsert 内的原始 DB 异常）——先回滚到
        #   SAVEPOINT，收尾的 INSERT 才有一个干净的事务状态可用。对 Python 级异常
        #   （CoverageDrop/UnknownShape）这个 ROLLBACK 是空操作，无害。
        cur.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        # ★ 失败也要写完整的一行：只打日志的话，明天没人知道今天刷过、更不知道为什么没成。
        #   日志本身也要带上「打的谁 · 多久 · 怎么失败的」三问的第三问——只取 str(e)
        #   会把 psycopg2.Error 的 pgcode/constraint 全部丢掉，落库的 error 字段不能
        #   替代这一行日志（house 07:485 同一条纪律）。
        error = f"{type(e).__name__}: {e}"[:2000]
        if isinstance(e, psycopg2.Error):
            log.warning("op=refresh mirror=%s trigger=%s outcome=fail err_type=%s pg=%s",
                        m.name, trigger, type(e).__name__, pg_error_fields(e))
        else:
            log.warning("op=refresh mirror=%s trigger=%s outcome=fail err_type=%s err=%s",
                        m.name, trigger, type(e).__name__, e)
    # ★ review 09-22 二轮 finding：上面那个 SAVEPOINT 只框住了「取数 + 校验 +
    #   upsert」，收尾这条 provenance INSERT 本身还在保护范围之外——同样的
    #   失败形状只是挪后了一步：这条 INSERT 若在 DB 层失败（哪怕 trigger/actor
    #   已经在 `refresh_all` 里挡过，仍可能撞上别的约束或连接问题），异常会
    #   直接冒出 `refresh_one`，把 `refresh_all` 的循环也炸断。所以它需要
    #   自己单独一层 SAVEPOINT，出错就地兜住、不重试、不让异常逃出去。
    prov_savepoint = f"provenance_{m.name}"
    cur.execute(f"SAVEPOINT {prov_savepoint}")
    try:
        run_id = _write_provenance(cur, m.name, trigger, actor, started_at,
                                   source_max_captured, rows_in, rows_dropped,
                                   reasons, ok, error)
    except BaseException as e:  # noqa: BLE001 - 这是留痕表**唯一**的写入语句；
        # 它失败时必须兜住一切异常类型，否则就是让"今天刷过但一行证据都没留下"
        # 的批次同时炸断 refresh_all 的循环——两条 binding 判据一次性都破。
        cur.execute(f"ROLLBACK TO SAVEPOINT {prov_savepoint}")
        prov_type = type(e).__name__
        if isinstance(e, psycopg2.Error):
            log.error("op=refresh_provenance mirror=%s trigger=%s outcome=fail"
                      " err_type=%s pg=%s", m.name, trigger, prov_type, pg_error_fields(e))
        else:
            log.error("op=refresh_provenance mirror=%s trigger=%s outcome=fail"
                      " err_type=%s err=%s", m.name, trigger, prov_type, e)
        # ★ 留痕都写不出来，这一轮就不能算数——即使前面 _upsert 本身成功了，
        #   也没有任何证据能证明它发生过（下一轮的 _baseline() 找不到这行）。
        #   run_id 用哨兵值占位：没有行可引用。
        ok = False
        run_id = NO_PROVENANCE_ROW
        error = f"provenance_write_failed {prov_type}: {e}"[:2000]
    if ok:
        log.info("op=refresh mirror=%s trigger=%s outcome=ok rows_in=%d dropped=%d %s",
                 m.name, trigger, rows_in, rows_dropped, reasons)
    return RunRow(m.name, run_id, ok, rows_in, rows_dropped, error)


def refresh_all(trigger: str, actor: str | None = None, only: str | None = None,
                query=None) -> list[RunRow]:
    # ★ review 09-22 二轮：调用方传错参数必须在碰任何镜像之前就地爆炸——不排队、
    #   不半跑、不长得像"这批镜像刷新失败了"。两条校验都排在 `advisory_lock()`
    #   之前：坏参数不该先抢锁再报错，那样会让下一个正常调用平白多等一轮。
    if trigger not in ALLOWED_TRIGGERS:
        raise UnknownTrigger(trigger)
    _check_actor(actor)
    targets = [registry.by_name(only)] if only else registry.refresh_order()
    if only and targets[0].pending:
        raise KeyError(f"{only} 是 pending 条目，本阶段没有取数实现")
    own_query = query is None
    out: list[RunRow] = []
    with advisory_lock(), timed("refresh_dims", trigger=trigger, only=only or "all"):
        # ★ 终审 I-3：建连必须在锁内、在保护区内。放在外面时 CH 连不上就是
        #   「整轮在进锁之前炸掉、证据表一行不留」——而镜像陈旧多半**就是因为**
        #   CH 连不上，那一轮恰恰是最该留痕的一轮。
        if own_query:
            query = _ch_query_or_record(targets, trigger, actor)
        for m in targets:
            # ★ 一个镜像一个短事务：一批失败不影响其它批（07:486）
            with pg_conn() as conn, conn.cursor() as cur:
                out.append(refresh_one(cur, m, query, trigger, actor))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="刷新维度镜像")
    ap.add_argument("--only", help="只刷这一个镜像")
    args = ap.parse_args(argv)
    try:
        rows = refresh_all("cli", only=args.only)
    except RefreshInFlight as e:
        print(e, file=sys.stderr)
        return 3
    except ChUnreachable as e:
        # ★ 终审 I-3：运维为了修一个陈旧镜像来跑这条命令，该拿到的是一个约定的
        #   退出码 + 一条说清成因的话，不是一页 traceback。日志三问那一行已经由
        #   `_ch_query_or_record` 打过，这里只负责收场。
        print(e, file=sys.stderr)
        return 1
    for r in rows:
        print(f"{r.mirror:22s} ok={r.ok} rows_in={r.rows_in} dropped={r.rows_dropped}"
              f"{' ' + r.error if r.error else ''}")
    return 0 if all(r.ok for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
