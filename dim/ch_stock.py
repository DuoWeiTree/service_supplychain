"""本地仓可发库存 · 采购行项进度 —— 排货网格按需读取（spec 2026-10-10 §4）。

★ 不做镜像：`refresh_dims` 只 upsert 不删，源里消失的 (sku, wid) 会以旧的可发量留成幽灵库存。
★ 纯层：query / classify_failure 由 api/ 注入（`dim/` 不许 import `shared.ch_client`）。
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from collections.abc import Callable
from typing import NamedTuple

from dim.ch_source import (
    PURCHASE_ITEMS_TABLE,
    ChUnavailable,
    Query,
    UnknownShape,
    _unclassified_failure,
    pick_snapshot_date,
)

__all__ = ["ChStock", "PoItemProgress", "StockSnapshot", "UnknownShape"]

log = logging.getLogger("scm.dim")

STOCK_TABLE = "jxd_raw.lingxing_inventory_warehouse"

#: 每个采集日的整表行数 / 仓数 / 是否已写完。★ 第 3 列给 `pick_snapshot_date` 当覆盖面
#: （它叫 n_sid，这里是 uniq(wid)：本表的整店缺口形状是整仓缺）。
SQL_STOCK_DAYS = f"""
SELECT _captured_date, count() AS rows, uniq(wid) AS uniq_wid,
       now() - INTERVAL {{settle:UInt16}} MINUTE > max(_captured_at) AS settled,
       max(_captured_at) AS captured_at
  FROM {STOCK_TABLE}
 WHERE _captured_date >= today() - {{lookback:UInt16}}
 GROUP BY _captured_date
 ORDER BY _captured_date DESC
"""

#: ★ 只读 `product_valid_num`（`02` §3.1：禁止总量相减）。源键 (wid, sku, seller_id, fnsku)
#: 10-10 实测零重复；fnsku 在桶内求和（CA-D8 只按店分桶）。
#: ★ 非本地仓、未知店的行不在 SQL 里滤 —— 滤掉就数不出丢了多少。
SQL_STOCK = f"""
SELECT wid, sku, seller_id, toInt64(sum(ifNull(product_valid_num, 0))) AS valid
  FROM {STOCK_TABLE}
 WHERE _captured_date = {{as_of:Date}} AND sku IN {{skus:Array(String)}}
 GROUP BY wid, sku, seller_id
"""

#: 同 SQL_STOCK，不按货号过滤（无计划来源区要看全部本地可发，CA-D2）
SQL_STOCK_ALL = f"""
SELECT wid, sku, seller_id, toInt64(sum(ifNull(product_valid_num, 0))) AS valid
  FROM {STOCK_TABLE}
 WHERE _captured_date = {{as_of:Date}} AND ifNull(product_valid_num, 0) > 0
 GROUP BY wid, sku, seller_id
"""

#: ★ 带上那天最后一行的 _captured_at：排货页要拿它和采购页领星读回的时刻比谁新（CA-D12）
SQL_PO_ITEMS_AS_OF = f"""
SELECT _captured_date, max(_captured_at) FROM {PURCHASE_ITEMS_TABLE}
 WHERE _captured_date = (SELECT max(_captured_date) FROM {PURCHASE_ITEMS_TABLE})
 GROUP BY _captured_date
"""

#: ★ `quantity_entry` = 已入库，`quantity_receive` = 待到货（10-10 实测，CA-D9）。
SQL_PO_ITEMS = f"""
SELECT order_sn, sku,
       toInt64(sum(ifNull(quantity_entry, 0)))   AS entered,
       toInt64(sum(ifNull(quantity_receive, 0))) AS pending
  FROM {PURCHASE_ITEMS_TABLE}
 WHERE _captured_date = {{as_of:Date}} AND order_sn IN {{sns:Array(String)}}
   AND ifNull(is_delete, 0) = 0
 GROUP BY order_sn, sku
"""

SHARED = "shared"
_SHARED_IDS = frozenset({"0", ""})


class StockSnapshot(NamedTuple):
    as_of: dt.date
    captured_at: dt.datetime | None      # 这天最后一行的 _captured_at（与领星现读比新旧，CA-D12）
    buckets: dict[tuple[str, int], dict[str, int]]   # (sku, wid) -> {"shared": n, sid: n}
    dropped: dict
    rejected: tuple[dict, ...]


class PoItemProgress(NamedTuple):
    entered: int
    pending: int
    captured: dt.datetime | dt.date       # CH 那批的 _captured_at；取不到时退回采集日


def bucketize(rows, local_wids: set[int], known_sids: set[str]) -> tuple[dict, dict]:
    """(wid, sku, seller_id, valid) → 桶（CA-D8）+ 丢弃计数。★ CH 与领星现读共用这一份（CA-D12），两处各写会分叉。
    计数器显式从 0 开始：「查过了、一行都没丢」与「没查」不能长得一样。"""
    buckets: dict[tuple[str, int], dict[str, int]] = {}
    dropped = {"unknown_seller": {"rows": 0, "units": 0}, "not_local_wid": {"rows": 0, "units": 0}}
    for wid, sku, seller_id, valid in rows:
        wid, valid, seller_id = int(wid), int(valid or 0), str(seller_id or "")
        if wid not in local_wids:
            dropped["not_local_wid"]["rows"] += 1
            dropped["not_local_wid"]["units"] += valid
            continue
        if seller_id in _SHARED_IDS:
            key = SHARED
        elif seller_id in known_sids:
            key = seller_id
        else:
            dropped["unknown_seller"]["rows"] += 1
            dropped["unknown_seller"]["units"] += valid
            continue
        b = buckets.setdefault((sku, wid), {SHARED: 0})
        b[key] = b.get(key, 0) + valid
    return buckets, dropped


class ChStock:
    """★ 默认值出处：`min_rows=7927` / `min_wids=57` = spec §4 实测表（09-28~10-10 最小行数 · 仓数恒 57）；
    `drop_threshold` / `lookback_days` / `settle_minutes` 同 `ChSource`（设计取值，沿用）。"""

    def __init__(self, query: Query, *, local_wids: set[int], known_sids: set[str],
                 classify_failure: Callable[[BaseException], dict] = _unclassified_failure,
                 lookback_days: int = 7, settle_minutes: int = 30, drop_threshold: float = 0.30,
                 min_rows: int = 7927, min_wids: int = 57) -> None:
        self._q = query
        self._local = set(local_wids)
        self._known = {str(s) for s in known_sids}
        self._classify = classify_failure
        self._lookback = lookback_days
        self._settle = settle_minutes
        self._threshold = drop_threshold
        self._min_rows = min_rows
        self._min_wids = min_wids

    def _run(self, op: str, target: str, sql: str, params: dict | None = None) -> list[tuple]:
        t0 = time.perf_counter()
        try:
            rows = self._q(sql, params) if params is not None else self._q(sql)
        except Exception as e:
            cause = self._classify(e)
            log.warning("op=%s outcome=fail target=%s elapsed_ms=%d cause=%s",
                        op, target, (time.perf_counter() - t0) * 1000, cause)
            raise ChUnavailable(target=target, cause=cause) from e
        return rows

    def snapshot(self, skus: set[str] | None) -> StockSnapshot:
        """`skus=None` = 全部可发 > 0 的行（无计划来源区）；空集合 = 不查行，只定快照日。"""
        t0 = time.perf_counter()
        day_rows = self._run("ch_local_stock_days", STOCK_TABLE, SQL_STOCK_DAYS,
                             {"lookback": self._lookback, "settle": self._settle})
        if not day_rows:
            raise UnknownShape(
                f"{STOCK_TABLE} 近 {self._lookback} 天一个采集日都没有。"
                "★ 空不是「仓里没货」——把采集缺口读成空会让全部可发量变成 0")
        as_of, rejected = pick_snapshot_date([r[:4] for r in day_rows], self._threshold, self._min_rows,
                                             self._min_wids, lookback_days=self._lookback)
        captured_at = next((r[4] for r in day_rows if r[0] == as_of and len(r) > 4), None)
        for r in rejected:
            log.warning("op=ch_local_stock outcome=rejected %s accepted=%s", r, as_of)
        if skus is None:
            rows = self._run("ch_local_stock", STOCK_TABLE, SQL_STOCK_ALL, {"as_of": as_of.isoformat()})
        elif skus:
            rows = self._run("ch_local_stock", STOCK_TABLE, SQL_STOCK,
                             {"as_of": as_of.isoformat(), "skus": sorted(skus)})
        else:
            rows = []
        buckets, dropped = bucketize(rows, self._local, self._known)
        log.info("op=ch_local_stock outcome=ok target=%s as_of=%s skus=%s rows=%d rejected=%d "
                 "dropped=%s elapsed_ms=%d", STOCK_TABLE, as_of,
                 "all" if skus is None else len(skus), len(rows),
                 len(rejected), dropped, (time.perf_counter() - t0) * 1000)
        return StockSnapshot(as_of, captured_at, buckets, dropped, tuple(rejected))

    def po_progress(self, order_sns: set[str]) -> dict[tuple[str, str], PoItemProgress]:
        if not order_sns:
            return {}
        t0 = time.perf_counter()
        as_of_rows = self._run("ch_po_items_as_of", PURCHASE_ITEMS_TABLE, SQL_PO_ITEMS_AS_OF)
        as_of = as_of_rows[0][0] if as_of_rows else None
        captured = (as_of_rows[0][1] if as_of_rows and len(as_of_rows[0]) > 1 else None) or as_of
        if as_of is None:
            raise UnknownShape(f"{PURCHASE_ITEMS_TABLE} 一个采集日都没有 —— 不是「没有到货」")
        rows = self._run("ch_po_items", PURCHASE_ITEMS_TABLE, SQL_PO_ITEMS,
                         {"as_of": as_of.isoformat(), "sns": sorted(order_sns)})
        out = {(sn, sku): PoItemProgress(int(e), int(p), captured) for sn, sku, e, p in rows}
        log.info("op=ch_po_items outcome=ok target=%s as_of=%s orders=%d rows=%d elapsed_ms=%d",
                 PURCHASE_ITEMS_TABLE, as_of, len(order_sns), len(rows),
                 (time.perf_counter() - t0) * 1000)
        return out
