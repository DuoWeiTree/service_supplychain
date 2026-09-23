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
from typing import NamedTuple

from psycopg2.extras import execute_values

from dim import ch_source, registry
from jobs.lock import RefreshInFlight, advisory_lock
from shared.ch_client import ch_client, ch_query
from shared.config import freshness
from shared.pg_client import pg_conn, timed

log = logging.getLogger("scm.jobs")


class CoverageDrop(Exception):
    pass


class RunRow(NamedTuple):
    mirror: str
    run_id: int
    ok: bool
    rows_in: int
    rows_dropped: int
    error: str | None


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


def refresh_one(cur, m: registry.Mirror, query, trigger: str, actor: str | None) -> RunRow:
    """★ `dim_refresh_run` 只追加（006 的 `BEFORE UPDATE OR DELETE` 触发器挡住了
    `UPDATE`）—— 整轮跑完只在收尾时写**一次完整的行**，不分「先 INSERT 占位、
    再 UPDATE 补结果」两次。`started_at` 在 Python 侧先取好时间戳，跟收尾那次
    INSERT 一起落库，而不是靠数据库的 `DEFAULT now()`（那样会记成收尾时刻）。"""
    started_at = dt.datetime.now(dt.UTC)
    rows_in = rows_dropped = 0
    reasons: dict = {}
    source_max_captured = None
    ok = False
    error: str | None = None
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
        # ★ 失败也要写完整的一行：只打日志的话，明天没人知道今天刷过、更不知道为什么没成
        error = f"{type(e).__name__}: {e}"[:2000]
        log.warning("op=refresh mirror=%s trigger=%s outcome=fail err=%s",
                    m.name, trigger, e)
    cur.execute(
        "INSERT INTO dim_refresh_run (mirror, trigger, actor, started_at, finished_at,"
        " source_max_captured, rows_in, rows_dropped, drop_reasons, ok, error)"
        " VALUES (%s, %s, %s, %s, now(), %s, %s, %s, %s, %s, %s) RETURNING run_id",
        (m.name, trigger, actor, started_at, source_max_captured, rows_in, rows_dropped,
         json.dumps(reasons, ensure_ascii=False), ok, error))
    run_id = cur.fetchone()[0]
    if ok:
        log.info("op=refresh mirror=%s trigger=%s outcome=ok rows_in=%d dropped=%d %s",
                 m.name, trigger, rows_in, rows_dropped, reasons)
    return RunRow(m.name, run_id, ok, rows_in, rows_dropped, error)


def refresh_all(trigger: str, actor: str | None = None, only: str | None = None,
                query=None) -> list[RunRow]:
    targets = [registry.by_name(only)] if only else registry.refresh_order()
    if only and targets[0].pending:
        raise KeyError(f"{only} 是 pending 条目，本阶段没有取数实现")
    own_query = query is None
    if own_query:
        query = ch_query(ch_client())
    out: list[RunRow] = []
    with advisory_lock(), timed("refresh_dims", trigger=trigger, only=only or "all"):
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
    for r in rows:
        print(f"{r.mirror:22s} ok={r.ok} rows_in={r.rows_in} dropped={r.rows_dropped}"
              f"{' ' + r.error if r.error else ''}")
    return 0 if all(r.ok for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
