"""销售计划 Excel 导入 —— ui 与 pub 两个薄壳调的同一个函数（spec §1 ③，IM-D1 / IM-D3）。

★ 先全查、后一次写：所有错误一次列全；有错就一张计划都不建。写阶段的认领与写格在
  一个事务里，任何一步失败整笔回滚 —— 不留半张计划、也不留空计划。
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from collections import Counter
from dataclasses import dataclass

from rules.plan_import import COLUMNS, RowError, col_letter, parse_rows
from shared.pg_client import pg_conn
from usecase.plan_claim import ClaimedElsewhere, claim_holder, claim_msku
from usecase.plan_xlsx import MAX_BYTES, NotXlsx, read_first_sheet

log = logging.getLogger("scm.usecase")

#: 拒绝时逐条写进日志的上限（spec §4）
LOG_ERRORS_MAX = 20


@dataclass(frozen=True)
class PlanHeader:
    title: str
    period_start: dt.date
    months: int
    owner_actor: str


class ImportRejected(Exception):
    def __init__(self, errors: list[RowError]):
        self.errors = errors
        super().__init__(f"导入被拒：{len(errors)} 处问题")


def _header_errors(h: PlanHeader) -> list[RowError]:
    """沿用 POST /plans 的码（R4）。范围在那边由库层 CHECK 裁决；这里要先于任何写把错列全，
    所以自己判 —— 码与提示与 api/common/errors.py 那两条 CHECK 的映射一致。"""
    errs = []
    if not (h.title or "").strip():
        errs.append(RowError(0, None, "title_required", "标题必填", extra={"field": "title"}))
    if not isinstance(h.months, int) or isinstance(h.months, bool):
        errs.append(RowError(0, None, "validation_error", "月数须为整数",
                             extra={"field": "months", "got": repr(h.months)}))
    elif not 1 <= h.months <= 24:
        errs.append(RowError(0, None, "bad_months", "计划跨月数必须在 1~24 之间",
                             extra={"field": "months", "got": h.months}))
    if h.period_start.day != 1:
        errs.append(RowError(0, None, "bad_period_start", "起始月必须是月初",
                             extra={"field": "period_start", "got": h.period_start.isoformat()}))
    return errs


def _periods(start: dt.date, months: int) -> list[dt.date]:
    return [dt.date(start.year + (start.month - 1 + i) // 12, (start.month - 1 + i) % 12 + 1, 1)
            for i in range(months)]


def _column_letters(header: list[object]) -> dict[str, str]:
    out = {}
    for i, h in enumerate(header):
        name = str(h).strip() if h is not None else None
        if name in COLUMNS and name not in out:
            out[name] = col_letter(i)
    return out


def _validate_against_db(cur, parsed, letters: dict[str, str]
                         ) -> tuple[list[RowError], set[str]]:
    """只读。错误追加不中断。★ 占用这里只是读；裁决仍在认领的 INSERT（唯一索引）。"""
    errs: list[RowError] = []
    pairs = sorted({(d.seller_sku, d.sid) for d in parsed.demand})
    sids = sorted({sid for _, sid in pairs})
    cur.execute("SELECT seller_id, name, market FROM seller WHERE seller_id = ANY(%s)", (sids,))
    sellers = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    cur.execute("SELECT b.seller_sku, b.sid, b.sku FROM msku_bridge b"
                " JOIN unnest(%s::text[], %s::text[]) AS q(seller_sku, sid)"
                "   ON q.seller_sku = b.seller_sku AND q.sid = b.sid",
                ([p[0] for p in pairs], [p[1] for p in pairs]))
    bridge = {(r[0], r[1]): r[2] for r in cur.fetchall()}
    resolved_skus: set[str] = set()
    for d in parsed.demand:
        ctx = {"seller_sku": d.seller_sku, "sid": d.sid, "sku": d.sku}
        seller = sellers.get(d.sid)
        if seller is None:
            # 店都认不出，桥表必然没有这一对；再报 unknown_msku 只是同一个错说两遍
            errs.append(RowError(d.row, letters.get("店铺ID"), "unknown_seller",
                                 f"店铺ID {d.sid} 不存在", **ctx))
            continue
        name, market = seller
        if d.seller_name is not None and d.seller_name != name:
            errs.append(RowError(d.row, letters.get("店铺"), "seller_mismatch",
                                 f"店铺ID {d.sid} 是「{name}」，表里写的是「{d.seller_name}」",
                                 extra={"expected": name}, **ctx))
        if d.market is not None and d.market != market:
            errs.append(RowError(d.row, letters.get("国家"), "market_mismatch",
                                 f"店铺ID {d.sid} 的国家是 {market}，表里写的是 {d.market}",
                                 extra={"expected": market}, **ctx))
        sku = bridge.get((d.seller_sku, d.sid))
        if sku is None:
            errs.append(RowError(d.row, letters.get("msku"), "unknown_msku", "msku 不在桥表里",
                                 **ctx))
            continue
        resolved_skus.add(sku)
        if d.sku is not None and d.sku != sku:
            errs.append(RowError(d.row, letters.get("货号"), "sku_mismatch",
                                 f"这个 msku 的货号是 {sku}，表里写的是 {d.sku}",
                                 extra={"expected": sku}, **ctx))
        holder = claim_holder(cur, d.seller_sku, d.sid)
        if holder is not None:
            errs.append(_claimed_error(d.row, d.seller_sku, d.sid, sku, holder))
    return errs, resolved_skus


def _claimed_error(row: int, seller_sku: str, sid: str, sku: str | None,
                   holder: tuple[int, str, str] | None) -> RowError:
    if holder is None:
        # 回滚后补名那一刻对方又释放了：占用确实发生过，只是说不出是谁
        return RowError(row, None, "claimed_elsewhere", "被另一张计划占用（补查时已释放）",
                        seller_sku=seller_sku, sid=sid, sku=sku,
                        extra={"plan_id": None, "title": None})
    pid, _, title = holder
    return RowError(row, None, "claimed_elsewhere", f"被计划「{title}」（#{pid}）占用",
                    seller_sku=seller_sku, sid=sid, sku=sku,
                    extra={"plan_id": pid, "title": title})


def _reject(errs: list[RowError], ctx: dict) -> ImportRejected:
    by_code = Counter(e.code for e in errs)
    log.warning("op=plan_import actor=%s title=%r rows=%s mskus=%s skus=%s cells=%s file_bytes=%s"
                " parse_ms=%s validate_ms=%s write_ms=%s outcome=rejected errors=%s",
                ctx["actor"], ctx["title"], ctx.get("rows"), ctx.get("mskus"), ctx.get("skus"),
                ctx.get("cells"), ctx["file_bytes"], ctx.get("parse_ms"), ctx.get("validate_ms"),
                ctx.get("write_ms"), ",".join(f"{c}:{n}" for c, n in sorted(by_code.items())))
    for e in errs[:LOG_ERRORS_MAX]:
        log.warning("op=plan_import outcome=rejected row=%s col=%s code=%s seller_sku=%s sid=%s"
                    " sku=%s message=%s extra=%s",
                    e.row, e.col, e.code, e.seller_sku, e.sid, e.sku, e.message, e.extra)
    return ImportRejected(errs)


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


def import_plan(source, header: PlanHeader, data: bytes, who: str) -> dict:
    t_start = time.perf_counter()
    ctx: dict = {"actor": who, "title": header.title, "file_bytes": len(data)}

    errs = _header_errors(header)
    periods_ok = not any(e.code in ("validation_error", "bad_months", "bad_period_start")
                         for e in errs)
    if len(data) > MAX_BYTES:
        errs.append(RowError(0, None, "file_too_large",
                             f"文件超过 {MAX_BYTES // (1024 * 1024)} MB 上限",
                             extra={"bytes": len(data), "max_bytes": MAX_BYTES}))
        raise _reject(errs, ctx)
    if not periods_ok:
        # 月份列对不对要拿计划的月份来比；抬头的月数 / 起始月本身错了，逐格报只会是噪音
        raise _reject(errs, ctx)

    t0 = time.perf_counter()
    try:
        head, rows = read_first_sheet(data)
    except NotXlsx as e:
        log.warning("op=plan_import actor=%s title=%r outcome=not_xlsx file_bytes=%d detail=%s",
                    who, header.title, len(data), e)
        raise _reject(errs + [RowError(0, None, "not_xlsx", "不是有效的 xlsx 文件")], ctx) from None
    periods = _periods(header.period_start, header.months)
    parsed = parse_rows(head, rows, [p.strftime("%Y-%m") for p in periods])
    ctx.update(rows=sum(1 for r in rows if any(v is not None and str(v).strip() for v in r)),
               parse_ms=_ms(t0))

    t0 = time.perf_counter()
    db_errs: list[RowError] = []
    resolved_skus: set[str] = set()
    if parsed.demand:
        with pg_conn() as c, c.cursor() as cur:
            db_errs, resolved_skus = _validate_against_db(cur, parsed, _column_letters(head))
    # rules 只认得表里写了的货号；销量行没写货号时由桥表补齐，补齐后能对上的不算错
    file_errs = [e for e in parsed.errors
                 if not (e.code == "purchase_sku_not_claimed" and e.sku in resolved_skus)]
    ctx["validate_ms"] = _ms(t0)
    errs = errs + file_errs + db_errs
    if errs:
        errs.sort(key=lambda e: (e.row, e.col or ""))
        raise _reject(errs, ctx)

    t0 = time.perf_counter()
    human = 0
    current = None
    try:
        with pg_conn() as c, c.cursor() as cur:
            cur.execute("INSERT INTO plan (title, period_start, months, owner_actor, created_by)"
                        " VALUES (%s, %s, %s, %s, %s) RETURNING plan_id",
                        (header.title.strip(), header.period_start, header.months,
                         header.owner_actor, who))
            plan_id = cur.fetchone()[0]
            skus: set[str] = set()
            for d in parsed.demand:
                current = d
                skus.add(claim_msku(cur, source, plan_id, d.seller_sku, d.sid, who)
                         ["claimed"]["sku"])
            current = None
            for d in parsed.demand:
                for p in periods:
                    v = d.units.get(p.strftime("%Y-%m"))
                    if v is None:
                        continue  # 空 ≠ 0：格子已由认领种出，expected_units 留 NULL
                    cur.execute("UPDATE plan_demand_cell SET expected_units = %s,"
                                " updated_by = %s, updated_at = now()"
                                " WHERE plan_id = %s AND seller_sku = %s AND sid = %s"
                                "   AND period_start = %s", (v, who, plan_id, d.seller_sku,
                                                             d.sid, p))
                    _one(cur, "plan_demand_cell", plan_id, d.seller_sku, d.sid, p)
                    human += 1
            for r in parsed.purchase:
                for p in periods:
                    v = r.units.get(p.strftime("%Y-%m"))
                    if v is None:
                        continue
                    cur.execute("UPDATE plan_purchase_cell SET planned_units = %s,"
                                " updated_by = %s, updated_at = now()"
                                " WHERE plan_id = %s AND sku = %s AND period_start = %s",
                                (v, who, plan_id, r.sku, p))
                    _one(cur, "plan_purchase_cell", plan_id, r.sku, p)
                    human += 1
    except ClaimedElsewhere as e:
        # pg_conn 已回滚：plan 行与此前的认领一起撤掉。补名必须开新连接 ——
        # 回滚把同一事务里的 SET search_path 一起撤了（见 ClaimedElsewhere docstring）
        holder = e.holder
        if holder is None:
            with pg_conn() as c, c.cursor() as cur:
                holder = claim_holder(cur, e.seller_sku, e.sid)
        ctx.update(write_ms=_ms(t0))
        row = current.row if current is not None else 0
        raise _reject([_claimed_error(row, e.seller_sku, e.sid, None, holder)], ctx) from None
    except Exception as e:
        # 取数源异常等原样上抛（api 层翻译）；这里只补一行带阶段与耗时的日志
        log.warning("op=plan_import actor=%s title=%r rows=%s file_bytes=%s write_ms=%d"
                    " outcome=%s at_seller_sku=%s at_sid=%s pgcode=%s detail=%s rolled_back=true",
                    who, header.title, ctx["rows"], len(data), _ms(t0), type(e).__name__,
                    getattr(current, "seller_sku", None), getattr(current, "sid", None),
                    getattr(e, "pgcode", None), e)
        raise

    n = len(periods)
    out = {"plan_id": plan_id, "mskus": len(parsed.demand), "skus": len(skus),
           "demand_cells": len(parsed.demand) * n, "purchase_cells": len(skus) * n,
           "elapsed_ms": _ms(t_start)}
    log.info("op=plan_import actor=%s title=%r plan_id=%s rows=%s mskus=%s skus=%s cells=%s"
             " file_bytes=%s parse_ms=%s validate_ms=%s write_ms=%d elapsed_ms=%d outcome=ok"
             " errors=-", who, header.title, plan_id, ctx["rows"], out["mskus"], out["skus"],
             human, len(data), ctx["parse_ms"], ctx["validate_ms"], _ms(t0), out["elapsed_ms"])
    return out


def _one(cur, table: str, *key) -> None:
    """格子由认领在同一事务里种出；没命中说明认领与写格对不上了 —— 响亮地炸，整笔回滚。"""
    if cur.rowcount != 1:
        raise RuntimeError(f"plan_import: {table} 应命中 1 行，实际 {cur.rowcount} key={key}")
