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
             " start_date / end_date（★ 必填）/ offset / length / order_sn[] / custom_order_sn[]"
             "（领星文档 Purchase/PurchaseOrderList.md；2026-10-10 实测两个过滤都生效，order_sns 不认）"),
    # ── C-b 海外仓备货单（CB 组；领星文档 Warehouse/*.md）。★ 我们的名字不用领星占用词 inbound ──
    Endpoint("create_ows_order", "POST", "/erp/sc/routing/owms/inbound/createInbound",
             "reversible", "inbound_order_no", "ows_order_nos",
             "Warehouse/CreateInbound.md · ★ 发货前可删（stockOrder/delete），故 reversible；status 必须显式给 40（CB-D2，默认 60 已完成）"),
    Endpoint("ows_order_nos", "POST", "/erp/sc/routing/owms/inbound/listOrderNos", "read", None, None,
             "Warehouse/listOrderNos.md · inbound_order_no[] → overseas_order_no（超时查重）"),
    Endpoint("ows_order_detail", "POST", "/basicOpen/overSeaWarehouse/stockOrder/detail", "read", None, None,
             "Warehouse/OverSeasStockDetail.md · overseas_order_no"),
    Endpoint("ows_in_transit_list", "POST", "/erp/sc/routing/owms/inbound/listInbound", "read", None, None,
             "Warehouse/listInbound.md · status 50 待收货 · page / page_size ≤ 50 · data[].products[] stock_num / receive_num · "
             "estimated_time 预计到货；10-15 实测 8 单 / 7101 件 / 4.6 s（当月在途海外仓部分，FM-D6）"),
    Endpoint("ows_order_delete", "POST", "/basicOpen/overSeaWarehouse/stockOrder/delete", "reversible", None, None,
             "Warehouse/DeleteOverSeaStockOrder.md · overseas_order_nos[]；仅 待审批 / 待配货 / 待发货 / 已驳回"),
    Endpoint("logistics_channel_list", "POST", "/erp/sc/data/local_inventory/channelList", "read", None, None,
             "Logistics/ChannelList.md · offset / length 必填；data[].id = logistics_id（CB-D3）"),
    Endpoint("ows_sku_match", "POST", "/erp/sc/routing/owms/inbound/matchSkuList", "read", None, None,
             "Warehouse/matchSkuList.md · wid（逗号分隔，必填）/ is_matched / offset / length（默认 20，10-13 实测 1000 可用；每次 3.6~4.8 s）→ local_sku ↔"
             " oversea_product_code（三方 sku 编码；★ 有 API 的三方海外仓建备货单必填，10-12 领星拒「第三方sku不能为空」）"),
    # ── C-c1 FBA 发货计划（CC-D1；领星文档 FBA/*.md）。★ 我们的名字不用领星占用词 shipment_plan / allocation ──
    Endpoint("create_fba_plan", "POST", "/erp/sc/routing/storage/shipment/createShipmentPlan",
             "reversible", None, "fba_plan_list",
             "FBA/CreateShipmentPlan.md · ★ 无幂等键（07 §3）→ 参考号写批次 remark、调用前后按 remark 查重；"
             "product_list[sid, msku, fnsku(必填), shipment_plan_quantity, wid, packing_type, quantity_in_case, box_num]"),
    Endpoint("fba_plan_list", "POST", "/erp/sc/data/fba_report/shipmentPlanLists", "read", None, None,
             "FBA/ShipmentPlanLists.md · sids / search_field_time / start_date / end_date / seq / offset / length；"
             "status -5 驳回 · 0 待审核 · 5 待处理 · 10 已处理；lock_status 1 未锁 · 2 已锁 · 3 已使用"),
    Endpoint("fba_plan_update", "POST", "/erp/sc/routing/storage/shipment/updateShipmentPlan", "reversible", None, None,
             "FBA/UpdatePlanLists.md · order_sn 必填 + 要改的字段（数量 / 装箱 / 仓 / remark）；★ 文档无作废 / 删除计划接口（CC-Q2）"),
    Endpoint("fba_plan_lock", "POST", "/basicOpen/openapi/fba/allocateStorage", "reversible", None, None,
             "FBA/FbaPlanStorageAllocate.md · 按计划号批量锁库存"),
    Endpoint("fba_plan_unlock", "POST", "/basicOpen/openapi/fba/releaseStorage", "reversible", None, None,
             "FBA/FbaPlanStorageRelease.md · orderNos[] 按计划号释放库存"),
    Endpoint("amazon_listing", "POST", "/erp/sc/data/mws/listing", "read", None, None,
             "Sale/Listing.md · sid 逗号串（必填）· search_field seller_sku / asin / sku · search_value[] ≤ 10 · exact_search 1；"
             "唯一键 sid + seller_sku；★ 令牌桶 1（整账号共享，串行调）；AR-D1：fnsku 现查这里，不读 CH 镜像"),
    Endpoint("local_inventory_details", "POST", "/erp/sc/routing/data/local_inventory/inventoryDetails", "read", None, None,
             "Warehouse/InventoryDetails.md · wid（逗号分隔）/ sku / offset / length ≤ 800；实时，不等 CH 次日"),
)
ENDPOINTS = {e.name: e for e in _ALL}


def get(name: str) -> Endpoint:
    if name not in ENDPOINTS:
        raise UnregisteredEndpoint(f"未登记的领星端点 {name!r}；已登记：{sorted(ENDPOINTS)}")
    return ENDPOINTS[name]
