"""刀0 只读实调（spec §6.7 左列）。零副作用：只调 purchaseOrderList。
用法：.venv/bin/python -m jobs.probe_lingxing [起 YYYY-MM-DD [止 YYYY-MM-DD]]
退出码：0 ok · 1 业务拒绝/其它 · 3 代理连不上 · 4 token 失败 · 2 缺配置
"""
from __future__ import annotations

import datetime as dt
import json
import sys

from erp.drivers.live import LiveDriver
from erp.endpoints import get
from shared.config import erp


def classify(r) -> tuple[str, int]:
    """三类失败互斥、处置相反：代理（白名单 IP）· token（凭证）· 业务（签名/参数）。"""
    if r.outcome == "ok":
        return "ok", 0
    cause = r.cause or ""
    if r.outcome == "not_sent" and cause.startswith("access_token"):
        return "token_failed", 4
    if r.outcome == "not_sent" and ("proxy" in cause.lower() or "tunnel" in cause.lower()):
        return "proxy_unreachable", 3
    if r.outcome == "rejected":
        return "business_rejected", 1
    return f"other:{r.outcome}", 1


def main(argv: list[str]) -> int:
    cfg = erp()
    missing = [k for k in ("app_id", "app_secret", "proxy_host") if not cfg[k]]
    if missing:
        print(f"[erp] 缺 {missing} —— 本探针需要领星凭证与白名单代理", file=sys.stderr)
        return 2
    day = dt.date.fromisoformat(argv[0]) if argv else dt.date.today() - dt.timedelta(days=1)  # noqa: DTZ011 领星 create_time 是本地时间串
    end = dt.date.fromisoformat(argv[1]) if len(argv) > 1 else day
    ep = get("purchase_order_list")
    r = LiveDriver(cfg).call(ep, {"search_field_time": "create_time", "start_date": day.isoformat(),
                                  "end_date": end.isoformat(), "offset": 0, "length": 5})
    body = r.body if isinstance(r.body, dict) else {}
    data = body.get("data") if isinstance(body.get("data"), list) else []
    first = data[0] if data and isinstance(data[0], dict) else {}
    items = first.get("item_list") or []
    item = items[0] if items and isinstance(items[0], dict) else {}
    klass, code = classify(r)
    print(json.dumps({"class": klass, "start": day.isoformat(), "end": end.isoformat(), "outcome": r.outcome, "ms": r.elapsed_ms,
                      "http": r.http_status, "lx_code": r.lx_code, "lx_msg": r.lx_msg, "cause": r.cause,
                      "top_keys": sorted(body.keys()), "n_orders": len(data),
                      "order_keys": sorted(first.keys()), "item_keys": sorted(item.keys()),
                      "total": body.get("total")}, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
