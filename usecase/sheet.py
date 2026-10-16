"""排货表：建 · 网格 · 安排一格 · 装箱 · 预检 · 确认 · 取消（spec 2026-10-10 §6）。取 → 判 → 写，事务在这里。

★ 本地库存读取器由调用方注入（`make_stock(local_wids, known_sids)`）；CH 读失败不拖垮需求侧（网格照给）。
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import logging
import time

from dim.ch_source import ChDataUnusable, ChUnavailable, UnknownShape
from repo import dispatch as repo
from rules import dispatch as rules
from rules import receipt as receipt_rules
from shared.pg_client import pg_conn, pg_error_fields, timed
from usecase import live_read

log = logging.getLogger("scm.usecase")

TERMINAL = ("已撤销", "已完结")      # ★ CA-D10：按量排，只挡终态


class Conflict(Exception):
    def __init__(self, status: int, error: str, hint: str, **fields):
        self.status, self.error, self.hint, self.fields = status, error, hint, fields
        super().__init__(f"{status} {error}: {hint}")


def _iso(v):
    return None if v is None else v.isoformat()


def _stock_error(e: Exception) -> dict:
    if isinstance(e, ChUnavailable):
        return {"target": e.target, "cause": e.cause}
    if isinstance(e, ChDataUnusable):
        return {"target": "jxd_raw.lingxing_inventory_warehouse", "cause": {"kind": "data_unusable"},
                "rejected": e.rejected}
    return {"target": "jxd_raw.lingxing_inventory_warehouse",
            "cause": {"kind": "unknown_shape", "msg": str(e)}}


def _aware(ts):
    """CH 的 DateTime 不带时区，服务端时区实测 = UTC（10-12）；只有日期的退成那天 0 点（算旧）。"""
    if ts is None:
        return None
    if isinstance(ts, dt.datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=dt.UTC)
    return dt.datetime(ts.year, ts.month, ts.day, tzinfo=dt.UTC)


def _overlay_live(cur, snap, skus: list[str] | None) -> tuple[dict | None, dict]:
    """CA-D12：CH 快照作底，某货号有比它新的领星现读 → 整个货号换成现读的桶。
    返回 (buckets, {sku: {"source", "as_of"}})；CH 读不到时只有现读过的货号有桶。"""
    reads = repo.latest_stock_reads(cur, skus)
    ch_at = _aware(snap.captured_at or snap.as_of) if snap is not None else None
    buckets = dict(snap.buckets) if snap is not None else {}
    meta: dict[str, dict] = {}
    for sku, rd in reads.items():
        if ch_at is not None and rd["read_at"] <= ch_at:
            continue
        for k in [k for k in buckets if k[0] == sku]:
            del buckets[k]
        buckets.update(rd["buckets"])
        meta[sku] = {"source": "api", "as_of": rd["read_at"].isoformat()}
    return (buckets if snap is not None or meta else None), meta


def _read_stock(make_stock, local_wids, known_sids, skus):
    """→ (snapshot | None, error | None)。★ 回退必须有声：失败写日志并把原因带回给屏幕。"""
    try:
        return make_stock(local_wids, known_sids).snapshot(skus), None
    except (ChUnavailable, ChDataUnusable, UnknownShape) as e:
        err = _stock_error(e)
        log.warning("op=sheet_stock outcome=unavailable %s", err)
        return None, err


def _local_and_known(cur):
    whs = repo.warehouse_kinds(cur)
    return whs, {w for w, v in whs.items() if v["kind"] == "local"}, repo.sellers(cur)


# ── 建 / 列 / 读 ─────────────────────────────────────────────

ROUTES = ("fba", "oversea")      # ★ CA-D13：平台仓 · 亚马逊 FBA / 海外仓（日后 wfs 另加）


def create(body: dict, actor: str) -> int:
    plan_id = (body or {}).get("plan_id")
    route = (body or {}).get("route")
    if not isinstance(plan_id, int) or isinstance(plan_id, bool):
        raise Conflict(400, "missing_required", "排货表要对应一个销售计划（CA-D11）", missing=["plan_id"])
    if route is None:
        raise Conflict(400, "missing_required", "排货表要选去向：平台仓 · FBA 或 海外仓（CA-D13）", missing=["route"])
    if route not in ROUTES:
        raise Conflict(400, "bad_route", "去向只能是 fba（平台仓 · 亚马逊 FBA）或 oversea（海外仓）", route=route,
                       allowed=list(ROUTES))
    with timed("sheet_create", actor=actor, plan=plan_id, route=route), pg_conn() as c, c.cursor() as cur:
        p = repo.read_plan(cur, plan_id)
        if p is None:
            raise Conflict(404, "plan_not_found", "计划不存在", plan_id=plan_id)
        if p["archived_at"] is not None:
            raise Conflict(409, "plan_archived", "计划已归档", plan_id=plan_id)
        existing = repo.draft_of(cur, plan_id, route)
        if existing is not None:
            # ★ 库层 sheet_one_draft_per_plan_route 兜底并发；这里先查是为了点名那张草稿
            raise Conflict(409, "draft_exists", "这个计划在这个去向已有一张草稿，接着排那张", sheet_id=existing,
                           plan_id=plan_id, route=route)
        sid = repo.insert_sheet(cur, actor, plan_id, route)
    log.info("op=sheet_create sheet=%s plan=%s route=%s actor=%s", sid, plan_id, route, actor)
    return sid


@contextlib.contextmanager
def _part(name: str):
    """spec §5：任一段读不到整体 500 并点名哪一段 —— 不把 chip 退回成「草稿」。"""
    try:
        with timed(f"dispatch_plans.{name}"):
            yield
    except Conflict:
        raise
    except Exception as e:                    # 点名后照样以 500 冒出去（不吞）
        raise Conflict(500, "dispatch_plans_failed", f"排货列表读不到「{name}」这一段", part=name,
                       err_type=type(e).__name__, **pg_error_fields(e)) from e


def dispatch_plans() -> list[dict]:
    t0 = time.perf_counter()
    with timed("dispatch_plans"), pg_conn() as c, c.cursor() as cur:
        with _part("plans"):
            plans = repo.dispatch_plans(cur)
        ids = [s["sheet_id"] for p in plans for s in p["sheets"]]
        with _part("in_flight"):
            in_flight = repo.sheets_in_flight(cur, ids)
        with _part("attention"):
            attention = repo.sheets_open_attention(cur, ids)
        with _part("drift"):
            stages = repo.sheets_ext_stages(cur, ids)
        with _part("submit_progress"):
            progress = repo.sheets_submit_progress(cur, ids)
        with _part("draft_gaps"):
            gaps = repo.sheets_draft_gaps(cur, ids)
        with _part("archived"):
            archived = repo.sheets_archived(cur, ids)
    drift: dict[int, list[str]] = {}
    for sid, ours, lx in stages:
        d = receipt_rules.drift(ours, lx)
        if d:
            drift.setdefault(sid, []).append(d)
    for p in plans:
        p["sheets"] = [_sheet_facts(s, in_flight, attention, drift, progress, gaps, archived) for s in p["sheets"]]
    log.info("op=dispatch_plans plans=%d sheets=%d records=%d in_flight=%d attention=%d drift=%d archived=%d"
             " elapsed_ms=%d", len(plans), len(ids), sum(len(p["records"]) for p in plans), sum(in_flight.values()),
             sum(attention.values()), sum(len(v) for v in drift.values()), sum(archived.values()),
             (time.perf_counter() - t0) * 1000)
    return plans


def _sheet_facts(s: dict, in_flight: dict, attention: dict, drift: dict, progress: dict, gaps: dict,
                 archived: dict) -> dict:
    """查询按 sheet_ids 全量 GROUP BY，不在结果里 = 真没有（不是没算进来）。
    all_archived（DL-D13）：活的排货单（不含已取消 / 已放弃，同 consignments）至少一张，且张张已归档。"""
    sid = s["sheet_id"]
    pg = progress.get(sid, {"orders": 0, "to_submit": 0, "to_lock": 0})
    gp = gaps.get(sid, {"consignments": 0, "packing_missing": 0, "logistics_missing": 0})
    n_arch = archived.get(sid, 0)
    return {**s, "in_flight": in_flight.get(sid, 0), "attention": attention.get(sid, 0), "drift": drift.get(sid, []),
            "to_lock": pg["to_lock"], "to_submit": pg["to_submit"], **gp,
            "all_submitted": pg["orders"] > 0 and pg["to_submit"] == 0 and pg["to_lock"] == 0,
            "archived": n_arch, "all_archived": gp["consignments"] > 0 and n_arch == gp["consignments"]}


def list_sheets(state: str | None) -> list[dict]:
    with timed("sheet_list"), pg_conn() as c, c.cursor() as cur:
        rows = repo.list_sheets(cur, state)
    return [{**r, "created_at": _iso(r["created_at"])} for r in rows]


def _require(cur, sheet_id: int) -> dict:
    s = repo.read_sheet(cur, sheet_id)
    if s is None:
        raise Conflict(404, "sheet_not_found", "排货表不存在", sheet_id=sheet_id)
    return s


def load(sheet_id: int) -> dict:
    with timed("sheet_read", sheet=sheet_id), pg_conn() as c, c.cursor() as cur:
        s = _require(cur, sheet_id)
        cons = repo.consignments(cur, sheet_id)
        pack = repo.packing(cur, sheet_id)
        last = repo.last_gate_run(cur, sheet_id)
        lg = repo.sheet_logistics(cur, sheet_id)
        refs = repo.ext_refs(cur, sheet_id)
        stages = repo.ext_stages(cur, sheet_id)
        events = repo.sheet_events(cur, sheet_id)
        in_flight = repo.sheets_in_flight(cur, [sheet_id]).get(sheet_id, 0)
    fba_lines = {(ln["seller_sku"], ln["sid"], ln["from_wid"]) for c_ in cons if c_["channel"] == "fba"
                 for ln in c_["lines"]}
    return {
        "sheet_id": s["sheet_id"], "state": s["state"], "created_by": s["created_by"],
        "plan_id": s["plan_id"], "plan_title": s["plan_title"], "route": s["route"],
        "created_at": _iso(s["created_at"]), "watermark": s["ledger_now"],
        "in_flight": in_flight,
        "consignments": cons,
        "packing": [{"seller_sku": k[0], "sid": k[1], "from_wid": k[2], **v, "orphan": k not in fba_lines}
                    for k, v in sorted(pack.items())],
        "last_gate_run": None if last is None else {**last, "stock_as_of": _iso(last["stock_as_of"]),
                                                    "at": _iso(last["at"])},
        "events": [{**e, "at": _iso(e["at"])} for e in events],
        "logistics": [{"dest_wid": w, **v} for w, v in sorted(lg.items())],
        "ext_refs": [_with_stage(e, stages.get(e["ext_ref_id"]), cons) for e in refs],
    }


def _with_stage(e: dict, st: dict | None, cons: list[dict]) -> dict:
    """领星单 + 回执读回的领星进度（spec 2026-10-13 §6）；两轴不一致只点名（`04` §4.5）。"""
    ours = next((c_["our_stage"] for c_ in cons if c_["consignment_id"] == e["consignment_id"]), None)
    lx = st["stage"] if st else None
    return {**e, "at": _iso(e["at"]), "voided_at": _iso(e["voided_at"]), "erp_stage": lx,
            "stage_observed_at": _iso(st["observed_at"]) if st else None,
            "drift": None if e["voided_at"] else receipt_rules.drift(ours or "", lx)}


# ── 网格（spec §6：需求行 + 无计划来源区 + 被丢掉的一侧）──────────

def grid(sheet_id: int, make_stock) -> dict:
    t0 = time.perf_counter()
    with timed("sheet_grid_pg", sheet=sheet_id), pg_conn() as c, c.cursor() as cur:
        s = _require(cur, sheet_id)
        whs, local, sellers = _local_and_known(cur)
        lines = repo.dispatchable_lines(cur, sheet_id, s["plan_id"])
        not_dispatchable = repo.count_lines_not_dispatchable(cur, s["plan_id"])
        cells = repo.sheet_cells(cur, sheet_id)
        pos = repo.linked_pos(cur, [ln["line_id"] for ln in lines])
        committed_all = repo.committed(cur)
    stock, stock_err = _read_stock(make_stock, local, set(sellers), None)
    progress, progress_err = {}, None
    sns = {p["order_sn"] for ps in pos.values() for p in ps if p["order_sn"]}
    if stock_err is None and sns:
        try:
            progress = make_stock(local, set(sellers)).po_progress(sns)
        except (ChUnavailable, UnknownShape) as e:
            progress_err = _stock_error(e)
            log.warning("op=sheet_po_progress outcome=unavailable %s", progress_err)
    with pg_conn() as c, c.cursor() as cur:
        live_buckets, live_meta = _overlay_live(cur, stock, None)
        api_progress = repo.latest_item_progress(cur, sorted(sns)) if sns else {}
    progress = _merge_progress(progress, api_progress)
    ch_stock_at = _aware(stock.captured_at or stock.as_of).isoformat() if stock else None

    skus = sorted({ln["sku"] for ln in lines} | {c_["sku"] for c_ in cells}
                  | ({k[0] for k in live_buckets} if live_buckets else set()))
    with pg_conn() as c, c.cursor() as cur:
        mskus = repo.mskus_by_sku_sid(cur, skus)
        names = repo.sku_names(cur, skus)

    excluded = {"lines_not_dispatchable": not_dispatchable, "seller_not_in_mirror": [], "msku_missing": [],
                "stock": stock.dropped if stock else None}
    demand = []
    for ln in lines:
        stores = []
        for sid, info in sorted((ln["demand_by_seller"] or {}).items()):
            if sid not in sellers:
                excluded["seller_not_in_mirror"].append({"line_id": ln["line_id"], "sid": sid})
                continue
            ms = mskus.get((ln["sku"], sid), [])
            if not ms:
                excluded["msku_missing"].append({"line_id": ln["line_id"], "sid": sid})
            info = info or {}
            stores.append({"sid": sid, "seller_name": sellers[sid]["name"], "market": sellers[sid]["market"],
                           "has_fba": sellers[sid]["has_fba"], "expected": info.get("units"),
                           "basis": info.get("basis"), "mskus": ms})
        line_pos = [{**p, **_progress(progress, p["order_sn"], ln["sku"])} for p in pos.get(ln["line_id"], [])]
        placed = [p for p in line_pos if p["order_sn"] and not p["is_void"]]
        # ★ 已入库 / 待到货 是采购单行项的（同货号整单），不摊回记录（C4）；读不到 = None，不是 0
        unknown = any(p["entered"] is None for p in placed)
        demand.append({
            **{k: ln[k] for k in ("line_id", "plan_id", "plan_title", "rev", "sku", "sku_name", "state",
                                  "total", "staged", "dispatched", "pending", "staged_here", "confirmed",
                                  "ordered")},
            "staged_other": ln["staged"] - ln["staged_here"],
            "entered": None if unknown else sum(p["entered"] for p in placed),
            "arriving": None if unknown else sum(p["pending"] for p in placed),
            "period": _iso(ln["period"]),
            "purchase_orders": line_pos,
            "stores": stores,
        })

    stock_rows = []
    unplanned = []
    if live_buckets is not None:
        # ★ I3：按货号建一次索引 —— 逐货号扫全表在生产量级（6,411 个 (sku, sid)）上是秒级
        by_sku: dict[str, list[tuple[str, list]]] = {}
        for (k_sku, sid), ms in sorted(mskus.items()):
            if sid in sellers:
                by_sku.setdefault(k_sku, []).append((sid, ms))
        for sku in sorted({k[0] for k in live_buckets}):
            stores = [{"sid": sid, "seller_name": sellers[sid]["name"], "market": sellers[sid]["market"],
                       "has_fba": sellers[sid]["has_fba"], "mskus": ms} for sid, ms in by_sku.get(sku, [])]
            if stores:
                unplanned.append({"sku": sku, "sku_name": names.get(sku), "stores": stores})
        for (sku, wid), buckets in sorted(live_buckets.items()):
            need = committed_all.get((sku, wid), {})
            sids = {sid for sid, _ in by_sku.get(sku, [])} | set(need)
            src = live_meta.get(sku) or {"source": "ch", "as_of": ch_stock_at}
            stock_rows.append({"sku": sku, "wid": wid, "buckets": buckets, "committed": need, **src,
                               "available": {sid: rules.available_for(sid, need, buckets) for sid in sorted(sids)}})
    log.info("op=sheet_grid sheet=%s demand=%d unplanned_skus=%d stock=%s excluded=%s elapsed_ms=%d",
             sheet_id, len(demand), len(unplanned),
             f"ch={'ok' if stock else 'unavailable'} live_skus={len(live_meta)}",
             {k: (len(v) if isinstance(v, list) else v) for k, v in excluded.items() if k != "stock"},
             (time.perf_counter() - t0) * 1000)
    return {
        "sheet": {"sheet_id": s["sheet_id"], "state": s["state"], "watermark": s["ledger_now"],
                  "plan_id": s["plan_id"], "plan_title": s["plan_title"], "route": s["route"]},
        "stock_as_of": _iso(stock.as_of) if stock else None,
        "stock_rejected": list(stock.rejected) if stock else [],
        "stock_error": stock_err, "po_progress_error": progress_err,
        "warehouses": {"local": [_wh(whs[w]) for w in sorted(local)],
                       "oversea": [_wh(v) for v in whs.values() if v["kind"].startswith("oversea_")]},
        "demand": demand, "unplanned": unplanned, "stock": stock_rows if live_buckets is not None else None,
        "cells": cells, "excluded": excluded,
    }


def _wh(w: dict) -> dict:
    return {"wid": w["wid"], "name": w["name"], "market": w["market"], "kind": w["kind"]}


def _merge_progress(ch: dict, api: dict) -> dict:
    """CA-D12：每个 (单号, 货号) 取「采购页领星读回」与 CH 中较新的一份，并记下来源。"""
    out = {k: {"entered": v.entered, "pending": v.pending, "at": _aware(v.captured), "source": "ch"} for k, v in ch.items()}
    for k, (e, p, at) in api.items():
        cur = out.get(k)
        if cur is None or (cur["at"] is not None and at > cur["at"]):
            out[k] = {"entered": e, "pending": p, "at": at, "source": "api"}
    return out


def _progress(progress: dict, order_sn: str | None, sku: str) -> dict:
    p = progress.get((order_sn, sku)) if order_sn else None
    if p is None:
        return {"entered": None, "pending": None, "captured": None, "source": None}
    return {"entered": p["entered"], "pending": p["pending"], "captured": _iso(p["at"]), "source": p["source"]}


# ── 安排一格（唯一原语，`15` §4）──────────────────────────────

def _int(v, field):
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        raise Conflict(400, "bad_request", f"{field} 必须是整数", field=field) from None


def _normalize_cell(body: dict) -> dict:
    channel = body.get("channel")
    if channel not in ("fba", "oversea"):
        raise Conflict(400, "bad_request", "channel 只能是 fba / oversea", field="channel")
    units = body.get("units")
    units = 0 if units in (None, "") else units
    if not isinstance(units, int) or isinstance(units, bool) or units < 0:
        raise Conflict(400, "bad_units", "件数必须是不小于 0 的整数（0 / 空 = 撤回）", units=body.get("units"))
    cell = {"line_id": _int(body.get("line_id"), "line_id"), "seller_sku": str(body.get("seller_sku") or ""),
            "sid": str(body.get("sid") or ""), "from_wid": _int(body.get("from_wid"), "from_wid"),
            "channel": channel, "dest_wid": _int(body.get("dest_wid"), "dest_wid"),
            "dest_sid": (body.get("dest_sid") or None), "units": units}
    if channel == "fba" and cell["dest_sid"] is None and cell["dest_wid"] is None:
        cell["dest_sid"] = cell["sid"]          # FBA 目的地就是本店（`15` §5.1）
    if not cell["seller_sku"] or not cell["sid"] or cell["from_wid"] is None:
        raise Conflict(400, "missing_required", "msku · 店 · 来源仓必填",
                       missing=[k for k in ("seller_sku", "sid", "from_wid") if not cell[k] and cell[k] != 0])
    return cell


def _check_cell(cur, cell: dict, whs: dict) -> None:
    if whs.get(cell["from_wid"], {}).get("kind") != "local":
        raise Conflict(409, "not_local_warehouse", "来源仓必须是本地仓（CA-Q19）", from_wid=cell["from_wid"])
    if cell["channel"] == "oversea":
        if cell["dest_sid"] is not None or not str(whs.get(cell["dest_wid"], {}).get("kind", "")).startswith("oversea_"):
            raise Conflict(409, "invalid_destination", "海外仓目的地必须是一个海外仓", dest_wid=cell["dest_wid"])
    elif cell["dest_wid"] is not None or cell["dest_sid"] != cell["sid"]:
        raise Conflict(409, "invalid_destination", "FBA 目的地只能是本店", sid=cell["sid"], dest_sid=cell["dest_sid"])
    sku = repo.bridge_sku(cur, cell["seller_sku"], cell["sid"])
    if sku is None:
        raise Conflict(409, "unknown_msku", "msku 不在 msku 镜像里（先刷新镜像）",
                       seller_sku=cell["seller_sku"], sid=cell["sid"])
    cell["sku"] = sku


def _check_line(cur, cell: dict, existing: dict | None, sheet_plan: int | None) -> None:
    if cell["line_id"] is None or not cell["units"]:
        return          # ★ C1：撤回永远放行 —— 记录被撤销 / 退回后，格子必须撤得回来
    pl = repo.lock_plan_line(cur, cell["line_id"])
    if pl is None:
        raise Conflict(404, "line_not_found", "没有这条计划单记录", line_id=cell["line_id"])
    if pl["state"] in TERMINAL:
        raise Conflict(409, "line_not_dispatchable", "记录已撤销 / 已完结，不能再排",
                       line_id=cell["line_id"], state=pl["state"])
    if sheet_plan is not None and pl["plan_id"] != sheet_plan:
        raise Conflict(409, "line_not_in_plan", "这条记录不属于这张排货表的计划（CA-D11）",
                       line_id=cell["line_id"], line_plan=pl["plan_id"], sheet_plan=sheet_plan)
    if pl["sku"] != cell["sku"]:
        raise Conflict(409, "msku_not_this_sku", "msku 对应的货号与记录的货号不同",
                       line_id=cell["line_id"], line_sku=pl["sku"], msku_sku=cell["sku"])
    allowed = sorted(pl["demand_by_seller"] or {})
    if cell["sid"] not in allowed:
        raise Conflict(409, "seller_not_in_line", "店不在这条记录里（只能排给计划里有这个货号的店，CA-D6）",
                       line_id=cell["line_id"], sid=cell["sid"], allowed=allowed)
    if cell["units"]:
        used = repo.line_used_excluding(cur, cell["line_id"], existing and existing["arrangement_id"])
        if used + cell["units"] > pl["total_units"]:
            raise Conflict(409, "over_total", "超过记录总量（含别的排货表已排未发）", line_id=cell["line_id"],
                           total=pl["total_units"], used=used, requested=cell["units"])


def _rederive(cur, sheet_id: int, actor: str) -> dict:
    old = set(repo.consignment_keys(cur, sheet_id))
    cells = repo.sheet_cells(cur, sheet_id)
    derived = rules.derive(cells)
    repo.rewrite_derivation(cur, sheet_id, cells, derived, actor)
    d = rules.diff(old, derived, set(repo.packing(cur, sheet_id)))
    return {k: [list(x) for x in v] for k, v in d.items()}


def put_cell(sheet_id: int, body: dict, actor: str, make_stock) -> dict:
    t0 = time.perf_counter()
    cell = _normalize_cell(body)
    rep = body.get("replaces")
    with timed("sheet_cell", sheet=sheet_id, actor=actor), pg_conn() as c, c.cursor() as cur:
        s = repo.lock_sheet(cur, sheet_id)
        if s is None:
            raise Conflict(404, "sheet_not_found", "排货表不存在", sheet_id=sheet_id)
        if s["state"] != "草稿":
            raise Conflict(409, "sheet_frozen", "排货表已确认 / 已取消，不可改", sheet_id=sheet_id, state=s["state"])
        repo.lock_sheet_lines(cur, sheet_id, cell["line_id"])
        whs, local, sellers = _local_and_known(cur)
        if cell["units"]:
            _check_cell(cur, cell, whs)
        else:
            cell["sku"] = repo.bridge_sku(cur, cell["seller_sku"], cell["sid"])
        before = None
        if rep:
            # ★ 改目的地 / 来源仓 = 同一事务里撤旧格 + 写新格（不给中间态留窗口）
            old = repo.find_cell(cur, sheet_id, cell["line_id"], cell["seller_sku"], cell["sid"],
                                 _int(rep.get("from_wid"), "replaces.from_wid") or cell["from_wid"],
                                 rep.get("channel"), _int(rep.get("dest_wid"), "replaces.dest_wid"),
                                 rep.get("dest_sid") or None)
            if old is not None:
                before = old
                repo.delete_cell(cur, old["arrangement_id"])
        existing = repo.find_cell(cur, sheet_id, cell["line_id"], cell["seller_sku"], cell["sid"],
                                  cell["from_wid"], cell["channel"], cell["dest_wid"], cell["dest_sid"])
        if rep and existing is not None and (before is None or existing["arrangement_id"] != before["arrangement_id"]):
            # ★ I1：挪到一个已有安排的格子不许悄悄覆盖人填的量 —— 让人去改那一格
            raise Conflict(409, "cell_exists", "目标格子已有安排，直接改那一格的件数",
                           existing_units=existing["units"], arrangement_id=existing["arrangement_id"])
        before = before or existing
        _check_line(cur, cell, existing, s["plan_id"])
        if cell["units"]:
            repo.upsert_cell(cur, existing, sheet_id, cell, actor)
        elif existing is not None:
            repo.delete_cell(cur, existing["arrangement_id"])
        d = _rederive(cur, sheet_id, actor)
        committed = repo.committed(cur, [cell["sku"]]) if cell["sku"] else {}
        watermark = repo.read_sheet(cur, sheet_id)["ledger_now"]
    warnings = _over_available(cell, committed, make_stock, local, set(sellers))
    log.info("op=arrange sheet=%s cell=%s before=%s after=%s diff=%s warnings=%s actor=%s ms=%d",
             sheet_id, {k: cell.get(k) for k in ("line_id", "seller_sku", "sid", "from_wid", "channel",
                                                  "dest_wid", "dest_sid")},
             before and before["units"], cell["units"], d, warnings, actor, (time.perf_counter() - t0) * 1000)
    return {"cell": cell, "consignments_diff": {k: d[k] for k in ("created", "removed", "kept")},
            "orphan_packing": d["orphan_packing"], "watermark": watermark, "warnings": warnings}


def _over_available(cell: dict, committed: dict, make_stock, local, known) -> list[dict]:
    """★ CA-Q20 暂行：写时只警告，G3 在确认时挡。读不到库存也要说出来，不是「没超」。"""
    if not cell["units"] or not cell.get("sku"):
        return []
    snap, err = _read_stock(make_stock, local, known, {cell["sku"]})
    if snap is None:
        return [{"code": "stock_unavailable", **err}]
    key = (cell["sku"], cell["from_wid"])
    need = committed.get(key, {})
    avail = rules.available_for(cell["sid"], need, snap.buckets.get(key, {rules.SHARED: 0}))
    if avail < 0:
        return [{"code": "over_available", "sku": cell["sku"], "from_wid": cell["from_wid"],
                 "sid": cell["sid"], "available": avail}]
    return []


# ── 装箱 · 预检 · 确认 · 取消（spec §5 / §6）─────────────────────

def put_packing(sheet_id: int, key: tuple, body: dict, actor: str) -> dict:
    box = {}
    for f in repo.PACKING_COLS:
        v = (body or {}).get(f)
        if v in (None, ""):
            box[f] = None
            continue
        try:
            num = int(v) if f in ("box_count", "units_per_box") else float(v)
        except (TypeError, ValueError):
            raise Conflict(400, "bad_packing", "装箱字段必须是正数", field=f, value=v) from None
        if num <= 0 or (f in ("box_count", "units_per_box") and num != float(v)):
            raise Conflict(400, "bad_packing", "装箱字段必须是正数（箱数 / 每箱件数为整数）", field=f, value=v)
        box[f] = num
    with timed("sheet_packing", sheet=sheet_id, actor=actor), pg_conn() as c, c.cursor() as cur:
        s = repo.lock_sheet(cur, sheet_id)
        if s is None:
            raise Conflict(404, "sheet_not_found", "排货表不存在", sheet_id=sheet_id)
        if s["state"] != "草稿":
            raise Conflict(409, "sheet_frozen", "排货表已确认 / 已取消，不可改", sheet_id=sheet_id, state=s["state"])
        fba = {(ln["seller_sku"], ln["sid"], ln["from_wid"]) for c_ in repo.consignments(cur, sheet_id)
               if c_["channel"] == "fba" for ln in c_["lines"]}
        if key not in fba:
            # ★ `10` §4：海外仓行没有装箱这一栏 —— 不是禁用，是不存在
            raise Conflict(409, "packing_not_applicable", "只有 FBA 排货行填装箱", seller_sku=key[0], sid=key[1],
                           from_wid=key[2])
        repo.upsert_packing(cur, sheet_id, key, box, actor)
    log.info("op=sheet_packing sheet=%s key=%s box=%s actor=%s", sheet_id, key, box, actor)
    return {"seller_sku": key[0], "sid": key[1], "from_wid": key[2], **box}


def _gate_inputs(cur, sheet_id: int, seen: int | None) -> dict:
    cons = [c_ for c_ in repo.consignments(cur, sheet_id) if c_["our_stage"] in ("草稿", "已确认")]
    skus = sorted({ln["sku"] for c_ in cons for ln in c_["lines"]})
    whs = repo.warehouse_kinds(cur)
    sellers = repo.sellers(cur)
    return {"consignments": cons, "packing": repo.packing(cur, sheet_id),
            "committed": repo.committed(cur, skus), "ledger_conflicts": repo.ledger_conflicts(cur, sheet_id, seen),
            "dest_kinds": {w: v["kind"] for w, v in whs.items()},
            "fba_sids": {s for s, v in sellers.items() if v["has_fba"]},
            "logistics": repo.sheet_logistics(cur, sheet_id),
            "_skus": set(skus), "_local": {w for w, v in whs.items() if v["kind"] == "local"},
            "_known": set(sellers)}


def _evaluate(cur, sheet_id: int, seen: int | None, make_stock, route: str | None,
              live_skus: set[str] | None = None) -> tuple[list[dict], object, dict | None]:
    """G3 的库存 = CH 快照 + 比它新的领星现读（CA-D12）。`live_skus`：确认前刚现读过的货号 ——
    它们一定用现读，CH 读不到也不妨碍判 G3。"""
    inp = _gate_inputs(cur, sheet_id, seen)
    snap, err = _read_stock(make_stock, inp["_local"], inp["_known"], inp["_skus"])
    buckets, meta = _overlay_live(cur, snap, sorted(inp["_skus"]))
    if err is not None and live_skus is not None and live_skus >= inp["_skus"]:
        err = None          # ★ 本表货号全部刚现读过：CH 读不到不影响判 G3
    gs = rules.gates(**{k: v for k, v in inp.items() if not k.startswith("_")},
                     stock=None if err is not None else buckets, stock_error=err)
    return rules.gates_for(route, gs), snap, err


def preflight(sheet_id: int, make_stock) -> dict:
    with timed("sheet_preflight", sheet=sheet_id), pg_conn() as c, c.cursor() as cur:
        s = _require(cur, sheet_id)
        gs, snap, _ = _evaluate(cur, sheet_id, s["ledger_now"], make_stock, s["route"])
    return {"sheet_id": sheet_id, "ok": rules.confirmable(gs), "gates": gs,
            "stock_as_of": _iso(snap.as_of) if snap else None}


def confirm(sheet_id: int, body: dict, actor: str, make_stock) -> dict:
    t0 = time.perf_counter()
    seen = (body or {}).get("seen_watermark")
    if not isinstance(seen, int) or isinstance(seen, bool):
        raise Conflict(400, "missing_required", "确认要带上你看到的水位 seen_watermark（防止确认别人刚改过的表）",
                       missing=["seen_watermark"])
    # ★ CA-D12：确认前现读本表货号的领星库存（不持锁做 HTTP）。读不到 → 503，不拿旧数冒充
    with pg_conn() as c, c.cursor() as cur:
        skus = repo.sheet_skus(cur, sheet_id)
    for sku in skus:
        try:
            live_read.read_stock(sku, actor)
        except live_read.LiveReadFailed as e:
            with pg_conn() as c, c.cursor() as cur:
                # ★ 这一次确认尝试照样留痕（15 §2.4）
                if repo.read_sheet(cur, sheet_id) is not None:
                    repo.insert_gate_run(cur, sheet_id, actor, False, seen, None, None, json.dumps(
                        {"stock_unavailable": {"sku": sku, "target": e.target, **e.cause}}, ensure_ascii=False, default=str))
            log.warning("op=sheet_confirm sheet=%s outcome=live_stock_unavailable sku=%s cause=%s actor=%s",
                        sheet_id, sku, e.cause, actor)
            raise Conflict(503, "stock_unavailable", f"确认前现读领星库存失败（{sku}），可发量无法判定，不能确认",
                           target=e.target, cause=e.cause, sku=sku) from None
    with timed("sheet_confirm", sheet=sheet_id, actor=actor), pg_conn() as c, c.cursor() as cur:
        s = repo.lock_sheet(cur, sheet_id)
        if s is None:
            raise Conflict(404, "sheet_not_found", "排货表不存在", sheet_id=sheet_id)
        if s["state"] != "草稿":
            raise Conflict(409, "sheet_frozen", "排货表已确认 / 已取消", sheet_id=sheet_id, state=s["state"])
        violations = repo.sheet_violations(cur, sheet_id)
        if violations:
            # ★ 同样留痕（15 §2.4）：不过的这一次也写 gate_run，随后在事务外报错
            run_id = repo.insert_gate_run(cur, sheet_id, actor, False, seen, None, None,
                                          json.dumps({"violations": violations}, ensure_ascii=False, default=str))
            log.warning("op=sheet_confirm sheet=%s outcome=dispatch_check_failed violations=%s run=%s actor=%s",
                        sheet_id, violations, run_id, actor)
            gs = None
        else:
            gs, snap, err = _evaluate(cur, sheet_id, seen, make_stock, s["route"], live_skus=set(skus))
            ok = err is None and rules.confirmable(gs)
            # ★ 闸结果每次都留痕（15 §2.4），不论过不过 —— 与状态推进同一事务提交
            run_id = repo.insert_gate_run(cur, sheet_id, actor, ok, seen, snap.as_of if snap else None,
                                          json.dumps(list(snap.rejected)) if snap else None,
                                          json.dumps(gs, ensure_ascii=False, default=str))
            if ok:
                repo.sheet_event(cur, sheet_id, "草稿", "已确认", actor, None, f"gate_run:{run_id}")
    if gs is None:
        raise Conflict(409, "dispatch_check_failed", "这张表有核对没过的安排（先撤回或改掉再确认）",
                       violations=violations, gate_run=run_id)
    failed = [g["gate"] for g in gs if g["ok"] is not True]
    log.info("op=sheet_confirm sheet=%s ok=%s not_passed=%s stock_as_of=%s seen=%s run=%s actor=%s ms=%d",
             sheet_id, ok, failed, snap.as_of if snap else None, seen, run_id, actor,
             (time.perf_counter() - t0) * 1000)
    if err is not None:
        raise Conflict(503, "stock_unavailable", "本地库存读不到，可发量无法判定，不能确认", gates=gs, **err)
    if not ok:
        raise Conflict(409, "confirm_blocked", "确认闸没过（全部列出）", gates=gs, gate_run=run_id)
    return {"sheet_id": sheet_id, "state": "已确认", "gates": gs, "gate_run": run_id}


def cancel(sheet_id: int, body: dict, actor: str) -> dict:
    reason = ((body or {}).get("reason") or "").strip()
    if not reason:
        raise Conflict(400, "reason_required", "取消排货表必须写理由", field="reason")
    # ★ CB-D6：领星里建过的备货单先删（不持行锁做 HTTP）；任一删不掉 → 不取消（库层 020 触发器再挡一次）。
    #   ★ 删单到写取消事件全程持 sheet_op：中间插进一次提交就会留下一张没删的真单（终审 I3）
    from usecase import submit_ows
    try:
        with submit_ows.sheet_op(sheet_id):
            out = submit_ows.delete_all(sheet_id, actor, reason)
            deleted, manual_void = out["deleted"], out["manual_void"]
            with timed("sheet_cancel", sheet=sheet_id, actor=actor), pg_conn() as c, c.cursor() as cur:
                s = repo.lock_sheet(cur, sheet_id)
                if s is None:
                    raise Conflict(404, "sheet_not_found", "排货表不存在", sheet_id=sheet_id)
                if s["state"] not in ("草稿", "已确认"):
                    raise Conflict(409, "sheet_frozen", "排货表已取消", sheet_id=sheet_id, state=s["state"])
                repo.sheet_event(cur, sheet_id, s["state"], "已取消", actor, reason, "ui")
    except submit_ows.Conflict as e:
        raise Conflict(e.status, e.error, e.hint, **e.fields) from None
    log.info("op=sheet_cancel sheet=%s from=%s reason=%s deleted_ows=%s manual_void=%s actor=%s", sheet_id, s["state"],
             reason, deleted, manual_void, actor)
    return {"sheet_id": sheet_id, "state": "已取消", "from": s["state"], "deleted_ows": deleted,
            "manual_void": manual_void}


# ── CA-D14 退回修改：已确认 → 草稿，只要这张表从没发到领星 ─────────────────

def reopen(sheet_id: int, body: dict, actor: str) -> dict:
    """冻结点在「发到领星」，不在「确认」：确认只是我们自己的闸，领星不知道。
    判据在库层（022 `sheet_reopen_after_submit`）；与提交 / 取消共用 sheet_op，不会一边退回一边在发。"""
    reason = ((body or {}).get("reason") or "").strip()
    if not reason:
        raise Conflict(400, "reason_required", "退回修改必须写理由", field="reason")
    from usecase import submit_ows
    try:
        with submit_ows.sheet_op(sheet_id):
            with timed("sheet_reopen", sheet=sheet_id, actor=actor), pg_conn() as c, c.cursor() as cur:
                s = repo.lock_sheet(cur, sheet_id)
                if s is None:
                    raise Conflict(404, "sheet_not_found", "排货表不存在", sheet_id=sheet_id)
                if s["state"] != "已确认":
                    raise Conflict(409, "sheet_not_confirmed", "只有已确认的排货表能退回修改", sheet_id=sheet_id,
                                   state=s["state"])
                repo.sheet_event(cur, sheet_id, "已确认", "草稿", actor, reason, "ui")
    except submit_ows.Conflict as e:
        raise Conflict(e.status, e.error, e.hint, **e.fields) from None
    log.info("op=sheet_reopen sheet=%s reason=%s actor=%s", sheet_id, reason, actor)
    return {"sheet_id": sheet_id, "state": "草稿", "from": "已确认"}
