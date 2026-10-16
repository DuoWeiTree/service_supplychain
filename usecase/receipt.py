"""领星备货单回执（spec 2026-10-13 · CB-D9 · AR-D1）。取 → 判 → 记。

★ 只读领星 API（`ows_order_detail`），不碰 CH（AR-D1：CH 只给预测）。
★ 逐表持 `sheet_op`：与提交 / 取消 / 退回修改互斥；定时轮次里拿不到锁的表跳过（记 skipped，不是失败）。
★ HTTP 不持数据库连接；读完一张表再一个事务写观察 + 归档。
"""
from __future__ import annotations

import logging
import time

from erp import gateway
from erp.transport import CallResult, cause_chain
from repo import dispatch as repo
from rules import fba_plan
from rules import receipt as rules
from shared.pg_client import pg_conn, timed
from usecase.draft_po import Conflict
from usecase.submit_ows import sheet_op

log = logging.getLogger("scm.usecase")

#: 领星备货单详情里原样留进观察的字段（发货 / 到达 / 签收时间、物流单号）—— 领星文档 OverSeasStockDetail
_KEEP = ("real_delivery_time", "estimated_time", "delivery_date", "transportation_start_time",
         "transportation_arrival_time", "logistics_tracking_number", "update_time")


def _read_fba(ext: dict) -> dict:
    """FBA 发货计划批次（C-c1）：按批次号读回各计划状态与锁库存；批次进度 = 最慢的计划（rules.fba_plan.batch_stage）。"""
    import datetime as dt
    from usecase import submit_fba          # 函数内 import：submit_fba → submit_ows 链路较重
    t0 = time.perf_counter()
    g, r = submit_fba.read_batch(ext["dest_sid"], ext["ext_no"], ext["at"].date() - dt.timedelta(days=1))
    out = {"ext_ref_id": ext["ext_ref_id"], "ext_no": ext["ext_no"], "consignment_id": ext["consignment_id"],
           "outcome": r.outcome, "lx_msg": r.lx_msg, "cause": r.cause}
    if r.outcome == "ok" and g is None:
        out.update(status="gone", stage=rules.GONE[0], rank=rules.GONE[1], sub_status=None, detail={})
    elif r.outcome == "ok":
        plans = g.get("list") or []
        st = fba_plan.batch_stage(plans)
        if st is None:
            out.update(outcome="unknown_status", cause=f"领星计划 status={[p.get('status') for p in plans]!r} 认不出")
        else:
            out.update(status=",".join(str(p.get("status")) for p in plans), stage=st[0], rank=st[1], sub_status=None,
                       detail={"plans": [{k: p.get(k) for k in ("order_sn", "status", "lock_status", "shipment_plan_quantity")}
                                         for p in plans],
                               "shipments": sorted({m.get("shipment_mws_sn") for p in plans for m in p.get("mws_relate") or []
                                                    if m.get("shipment_mws_sn")})})
    log.log(logging.INFO if out["outcome"] == "ok" else logging.WARNING,
            "op=fba_plan_receipt seq=%s consignment=%s host=%s path=%s outcome=%s stage=%s lx=%s %s cause=%s ms=%d",
            ext["ext_no"], ext["consignment_id"], r.host, r.path, out["outcome"], out.get("stage"), r.lx_code, r.lx_msg,
            out["cause"], (time.perf_counter() - t0) * 1000)
    return out


def _read(ext: dict) -> dict:
    """读一张领星单 → {outcome, stage, rank, status, sub_status, detail, lx_msg, cause}。"""
    if ext.get("kind") == "fba_plan":
        return _read_fba(ext)
    t0 = time.perf_counter()
    try:
        _, r = gateway.call("ows_order_detail", {"overseas_order_no": ext["ext_no"]})
    except Exception as e:  # noqa: BLE001 —— 驱动抛了 = 这一张没读到，不拖累其余
        r = CallResult("unknown", "-", "-", 0, cause=f"driver exception: {cause_chain(e)}")
    out = {"ext_ref_id": ext["ext_ref_id"], "ext_no": ext["ext_no"], "consignment_id": ext["consignment_id"],
           "outcome": r.outcome, "lx_msg": r.lx_msg, "cause": r.cause}
    if r.outcome == "rejected" and "不存在或已删除" in (r.lx_msg or ""):
        out.update(outcome="ok", status="gone", stage=rules.GONE[0], rank=rules.GONE[1], sub_status=None, detail={})
    elif r.outcome == "ok":
        data = (r.body or {}).get("data") or {}
        st = rules.stage_of(data.get("status"))
        if st is None:
            out.update(outcome="unknown_status", cause=f"领星 status={data.get('status')!r} 认不出")
        else:
            out.update(status=str(data.get("status")), stage=st[0], rank=st[1],
                       sub_status=None if data.get("sub_status") is None else str(data.get("sub_status")),
                       detail={k: data.get(k) for k in _KEEP if data.get(k) not in (None, "")})
    log.log(logging.INFO if out["outcome"] == "ok" else logging.WARNING,
            "op=ows_receipt ows=%s consignment=%s host=%s path=%s outcome=%s stage=%s lx=%s %s cause=%s ms=%d",
            ext["ext_no"], ext["consignment_id"], r.host, r.path, out["outcome"], out.get("stage"), r.lx_code,
            r.lx_msg, out["cause"], (time.perf_counter() - t0) * 1000)
    return out


def _sheet(sheet_id: int, run_id: int, actor: str | None) -> tuple[list[dict], list[int]]:
    """读一张表的全部待读回领星单，一个事务写观察并归档。调用方已持 sheet_op。"""
    with pg_conn() as c, c.cursor() as cur:
        targets = repo.receipt_targets(cur, sheet_id)
    results = [_read(e) for e in targets]
    archived: list[int] = []
    with pg_conn() as c, c.cursor() as cur:
        for x in results:
            if x["outcome"] == "ok":
                repo.insert_stage_obs(cur, x["ext_ref_id"], run_id, x["status"], x["stage"], x["rank"],
                                      x["sub_status"], x["detail"])
        cids = sorted({e["consignment_id"] for e in targets})
        for cid in repo.archivable(cur, cids):
            who = actor or next(e["actor"] for e in targets if e["consignment_id"] == cid)   # 定时：记在提交人名下
            repo.consignment_event(cur, cid, "已投送", "已归档", who, f"receipt:{run_id}")
            archived.append(cid)
    if archived:
        log.info("op=ows_archive sheet=%s consignments=%s run=%s", sheet_id, archived, run_id)
    return results, archived


def _failures(results: list[dict]) -> list[dict]:
    return [{k: x.get(k) for k in ("ext_no", "outcome", "lx_msg", "cause")} for x in results if x["outcome"] != "ok"]


def run(sheet_id: int, actor: str) -> dict:
    """手动「现查领星状态」：这一张表。"""
    t0 = time.perf_counter()
    with sheet_op(sheet_id), timed("ows_receipt_manual", sheet=sheet_id, actor=actor):
        with pg_conn() as c, c.cursor() as cur:
            if repo.read_sheet(cur, sheet_id) is None:
                raise Conflict(404, "sheet_not_found", "排货表不存在", sheet_id=sheet_id)
            run_id = repo.insert_receipt_run(cur, "manual", actor, sheet_id)
        results, archived = _sheet(sheet_id, run_id, actor)
        fails = _failures(results)
        with pg_conn() as c, c.cursor() as cur:
            repo.insert_receipt_result(cur, run_id, len(results), len(results) - len(fails), len(fails), 0, archived, fails)
    log.info("op=ows_receipt_run run=%s trigger=manual sheet=%s refs=%d failed=%d archived=%s actor=%s ms=%d", run_id,
             sheet_id, len(results), len(fails), archived, actor, (time.perf_counter() - t0) * 1000)
    return {"sheet_id": sheet_id, "run_id": run_id, "archived": archived,
            "results": [{k: x.get(k) for k in ("ext_no", "outcome", "stage", "sub_status", "lx_msg", "cause")}
                        for x in results]}


def tick() -> dict:
    """定时轮次：全部已投送排货单的领星单。一张表失败 / 被占用不拖累其余。"""
    t0 = time.perf_counter()
    with pg_conn() as c, c.cursor() as cur:
        sheets = sorted({e["sheet_id"] for e in repo.receipt_targets(cur, None)})
        run_id = repo.insert_receipt_run(cur, "scheduler", None, None)
    results: list[dict] = []
    archived: list[int] = []
    skipped = 0
    for sid in sheets:
        try:
            with sheet_op(sid):
                rs, ar = _sheet(sid, run_id, None)
        except Conflict as e:
            if e.error != "sheet_busy":
                raise
            skipped += 1
            log.info("op=ows_receipt sheet=%s outcome=skipped reason=sheet_busy run=%s", sid, run_id)
            continue
        except Exception as e:  # noqa: BLE001 —— 一张表的库层错误不拖垮整轮，但必须点名
            log.error("op=ows_receipt sheet=%s outcome=fail err_type=%s cause=%s run=%s", sid, type(e).__name__,
                      cause_chain(e), run_id)
            results.append({"ext_no": f"sheet:{sid}", "outcome": "error", "cause": cause_chain(e)})
            continue
        results += rs
        archived += ar
    fails = _failures(results)
    with pg_conn() as c, c.cursor() as cur:
        repo.insert_receipt_result(cur, run_id, len(results), len(results) - len(fails), len(fails), skipped, archived, fails)
    out = {"run_id": run_id, "refs": len(results), "ok": len(results) - len(fails), "failed": len(fails),
           "skipped": skipped, "archived": archived}
    log.log(logging.WARNING if fails else logging.INFO,
            "op=ows_receipt_run run=%s trigger=scheduler sheets=%d refs=%d failed=%d skipped=%d archived=%s ms=%d",
            run_id, len(sheets), out["refs"], out["failed"], skipped, archived, (time.perf_counter() - t0) * 1000)
    return out
