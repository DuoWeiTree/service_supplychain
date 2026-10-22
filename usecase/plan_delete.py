"""硬删除一张从未提交过的计划（IM-D4/D5）。

★ 行删掉之后，`op=delete_plan` 这一行日志是唯一的痕迹 —— 成功与每种拒绝都打，带 msku 清单。
"""
from __future__ import annotations

import logging
import time

from shared.pg_client import pg_conn, pg_error_fields

log = logging.getLogger("scm.usecase")

#: L6：usecase 不 import psycopg2，按 SQLSTATE 认外键冲突
_FOREIGN_KEY_VIOLATION = "23503"


class PlanNotFound(Exception):
    def __init__(self, plan_id: int):
        self.plan_id = plan_id
        super().__init__(f"计划不存在：{plan_id}")


class PlanSubmitted(Exception):
    def __init__(self, plan_id: int, revs: int):
        self.plan_id, self.revs = plan_id, revs
        super().__init__(f"计划已提交过 {revs} 版，不能删：{plan_id}")


class PlanReferenced(Exception):
    def __init__(self, plan_id: int, table: str, constraint: str):
        self.plan_id, self.table, self.constraint = plan_id, table, constraint
        super().__init__(f"计划被 {table} 引用（{constraint}），不能删：{plan_id}")


def _fmt(mskus: list[dict]) -> str:
    return ",".join(f"{m['seller_sku']}/{m['sid']}" for m in mskus)


def delete_plan(plan_id: int, who: str) -> dict:
    t0 = time.perf_counter()
    title: str | None = None
    mskus: list[dict] = []
    demand = purchase = None

    def done(outcome: str, **extra) -> None:
        log.info("op=delete_plan plan_id=%s title=%r actor=%s mskus=%s demand_cells=%s"
                 " purchase_cells=%s elapsed_ms=%d outcome=%s%s",
                 plan_id, title, who, _fmt(mskus), demand, purchase,
                 (time.perf_counter() - t0) * 1000, outcome,
                 "".join(f" {k}={v}" for k, v in extra.items()))

    try:
        with pg_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT title FROM plan WHERE plan_id = %s FOR UPDATE", (plan_id,))
            row = cur.fetchone()
            if row is None:
                raise PlanNotFound(plan_id)
            title = row[0]
            cur.execute("SELECT seller_sku, sid FROM msku_claim WHERE plan_id = %s"
                        " ORDER BY seller_sku, sid", (plan_id,))
            mskus = [{"seller_sku": s, "sid": d} for s, d in cur.fetchall()]
            # ★ 先看 plan_rev 再计数：任何一版（含已取消）都是提交过的证据，一律拒绝
            cur.execute("SELECT count(*) FROM plan_rev WHERE plan_id = %s", (plan_id,))
            revs = cur.fetchone()[0]
            if revs:
                raise PlanSubmitted(plan_id, revs)
            cur.execute("SELECT count(*) FROM plan_demand_cell WHERE plan_id = %s", (plan_id,))
            demand = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM plan_purchase_cell WHERE plan_id = %s", (plan_id,))
            purchase = cur.fetchone()[0]
            # ★ 逐表显式删，不依赖级联：别的表多了引用时要撞外键，而不是被悄悄带走
            for t in ("plan_demand_cell", "plan_purchase_cell", "msku_claim", "plan"):
                cur.execute(f"DELETE FROM {t} WHERE plan_id = %s", (plan_id,))
    except PlanNotFound:
        done("PlanNotFound")
        raise
    except PlanSubmitted as e:
        done("PlanSubmitted", revs=e.revs)
        raise
    except Exception as e:
        if getattr(e, "pgcode", None) != _FOREIGN_KEY_VIOLATION:
            done(type(e).__name__, pgcode=getattr(e, "pgcode", None))
            raise
        pg = pg_error_fields(e)
        done("PlanReferenced", pgcode=pg["pgcode"], table=pg["table"],
             constraint=pg["constraint"])
        raise PlanReferenced(plan_id, pg["table"] or "?", pg["constraint"] or "?") from None
    done("ok")
    return {"plan_id": plan_id, "title": title, "mskus": mskus,
            "demand_cells": demand, "purchase_cells": purchase}
