"""海外仓在途实查（spec 2026-10-15-forecast-three-metrics §2.2 · FM-D6）。

★ 预测块里唯一走领星实查的一项：CH 的海外仓备货单表不全（08-19 起只 35 张、末次 10-09），负责人 10-15 选
  「领星接口实时查 + 短缓存」。只读 `listInbound` status 50 待收货。
★ 读不到 = 未知（`ok=False`，不当 0）；失败只记 FAIL_CACHE_S —— 领星挂着时不让每个请求各等一次 30 秒超时。
"""
from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from dataclasses import dataclass

from erp import gateway
from erp.transport import CallResult, cause_chain
from forecast.month_in_transit import TransitItem

log = logging.getLogger("scm.usecase")

CACHE_S = 600            # FM-Q4：10 分钟 · 设计取值 · 未实测
FAIL_CACHE_S = 60        # FM-Q5：失败记 60 秒 · 设计取值 · 未实测
PAGE_SIZE = 50           # 领星文档上限
MAX_PAGES = 40           # 2000 单：防领星分页失常时无限翻页（设计取值 · 未实测；10-15 实测 8 单）
IN_TRANSIT = 50          # 待收货（已发未收）


@dataclass(frozen=True)
class OverseaInTransit:
    ok: bool
    by_sku: dict[str, list[TransitItem]] | None
    fetched_at: str | None           # ISO，弹窗写「领星实查于 …」
    reason: str | None = None
    bad_rows: int = 0                # 件数认不出的行（计数，不猜）


_cache: tuple[float, OverseaInTransit] | None = None
_lock = threading.Lock()


def _now() -> float:
    return time.monotonic()


def reset_cache() -> None:
    global _cache
    _cache = None


def _date(v) -> dt.date | None:
    """★ 领星空日期常给 0000-00-00：认不出 = 没有日期，不许抛（终审 I-1）。"""
    try:
        return dt.date.fromisoformat(str(v or "").strip()[:10])
    except ValueError:
        return None


def _int(v) -> int | None:
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return None


def _fetch() -> OverseaInTransit:
    t0 = time.perf_counter()
    orders: list[dict] = []
    r: CallResult | None = None
    for page in range(1, MAX_PAGES + 1):
        try:
            _, r = gateway.call("ows_in_transit_list", {"status": IN_TRANSIT, "page": page, "page_size": PAGE_SIZE,
                                                        "is_delete": 0})
        except Exception as e:  # noqa: BLE001 —— 驱动抛了 = 读不到，不猜
            r = CallResult("unknown", "-", "-", 0, cause=f"driver exception: {cause_chain(e)}")
        if r.outcome != "ok":
            log.warning("op=erp_oversea_in_transit host=%s path=%s page=%d outcome=%s lx=%s %s cause=%s ms=%d",
                        r.host, r.path, page, r.outcome, r.lx_code, r.lx_msg, r.cause,
                        (time.perf_counter() - t0) * 1000)
            return OverseaInTransit(False, None, None, f"{r.outcome}: {r.lx_msg or r.cause or ''}".strip())
        data = (r.body or {}).get("data") or []
        if isinstance(data, dict):
            data = data.get("list") or []
        orders += data
        reported = _int((r.body or {}).get("total"))
        if len(data) < PAGE_SIZE or (reported is not None and len(orders) >= reported):
            break
    if reported is not None and len(orders) < reported:
        # ★ 终审 I-2：少于领星报的总数 —— 不当完整结果（不然屏上是一个笃定的、偏小的数）
        log.warning("op=erp_oversea_in_transit outcome=truncated orders=%d total=%d max_pages=%d",
                    len(orders), reported, MAX_PAGES)
        return OverseaInTransit(False, None, None, f"truncated: 取到 {len(orders)} / {reported} 张")
    by_sku: dict[str, list[TransitItem]] = {}
    bad = 0
    for o in orders:
        for p in o.get("products") or []:
            stock, got = _int(p.get("stock_num")), _int(p.get("receive_num"))
            if stock is None or got is None or not p.get("sku"):
                bad += 1
                continue
            left = stock - got
            if left <= 0:
                continue
            sent = _date(o.get("real_delivery_time") or o.get("send_good_handle_time"))
            by_sku.setdefault(p["sku"], []).append(TransitItem(
                "oversea", str(o.get("overseas_order_no")), left, _date(o.get("estimated_time")),
                {"from_wid": o.get("s_wid"), "to_wid": o.get("r_wid"), "shipped_at": sent.isoformat() if sent else None}))
    for xs in by_sku.values():
        xs.sort(key=lambda x: (x.eta or dt.date.max, x.ref))
    log.log(logging.WARNING if bad else logging.INFO,
            "op=erp_oversea_in_transit host=%s path=%s orders=%d skus=%d units=%d bad_rows=%d outcome=ok ms=%d",
            r.host if r else "-", r.path if r else "-", len(orders), len(by_sku),
            sum(x.units for xs in by_sku.values() for x in xs), bad, (time.perf_counter() - t0) * 1000)
    return OverseaInTransit(True, by_sku, dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), bad_rows=bad)


def in_transit_by_sku() -> OverseaInTransit:
    """成功缓存 10 分钟（FM-Q4），失败记 60 秒（FM-Q5）。★ 同一时刻只有一个请求在查领星，其余等它的结果。"""
    global _cache
    with _lock:
        if _cache is not None and _now() - _cache[0] < (CACHE_S if _cache[1].ok else FAIL_CACHE_S):
            log.info("op=erp_oversea_in_transit cache=hit ok=%s age_s=%d", _cache[1].ok, _now() - _cache[0])
            return _cache[1]
        try:
            got = _fetch()
        except Exception as e:  # noqa: BLE001 —— 解析炸了 = 读不到，不许把整张计划表拖成 500（终审 I-1）
            log.error("op=erp_oversea_in_transit outcome=parse_error cause=%s", cause_chain(e))
            got = OverseaInTransit(False, None, None, f"parse: {type(e).__name__}")
        _cache = (_now(), got)
        return got
