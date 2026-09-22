"""content_digest（S-13）：sha256(规范化 JSON)。

★ 算法一变，历史 rev 的 digest 全部失配、dashboard 会把所有人都列进
  changed_since_submit[] —— 所以这里的规范化规则改动等同于一次迁移。
规则：purchase 行 + demand 行（含 basis），键排序，★ NULL 保留为 null。
"""
from __future__ import annotations

import hashlib
import json

from rules.effective import effective_demand


def content_digest(purchase_cells, demand_cells) -> str:
    payload = {
        "purchase": sorted(
            [{"sku": c.sku, "period": c.period, "planned_units": c.planned_units}
             for c in purchase_cells],
            key=lambda r: (r["sku"], r["period"])),
        "demand": sorted(
            [{"seller_sku": c.seller_sku, "sid": c.sid, "sku": c.sku, "period": c.period,
              "units": effective_demand(c.system_units, c.expected_units).units,
              "basis": effective_demand(c.system_units, c.expected_units).basis}
             for c in demand_cells],
            key=lambda r: (r["sku"], r["period"], r["sid"], r["seller_sku"])),
    }
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
