"""端点登记表（spec §6.1）。★ 副作用等级显式登记，不按路径前缀推断：
createPurchaseOrder 在 /erp/ 下却不可逆（07:351 与 08:242 矛盾）。未登记即拒。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Endpoint:
    name: str
    method: str
    path: str
    side_effect: str          # read | reversible | irreversible
    idem_param: str | None
    lookup: str | None
    source: str               # 出处


class UnregisteredEndpoint(Exception):
    pass


_ALL = (
    Endpoint("create_purchase_order", "POST", "/erp/sc/routing/purchase/purchase/createPurchaseOrder",
             "irreversible", "custom_order_sn", "purchase_order_list", "08:242 · 17:48-51"),
    Endpoint("purchase_order_list", "POST", "/erp/sc/routing/data/local_inventory/purchaseOrderList",
             "read", None, None,
             "jxd_data_collector lingxing_node/src/api-client/purchase.ts:31 · 参数 search_field_time /"
             " start_date / end_date / offset / length / order_sns；★ 无 custom_order_sn 过滤，客户端筛"),
)
ENDPOINTS = {e.name: e for e in _ALL}


def get(name: str) -> Endpoint:
    if name not in ENDPOINTS:
        raise UnregisteredEndpoint(f"未登记的领星端点 {name!r}；已登记：{sorted(ENDPOINTS)}")
    return ENDPOINTS[name]
