import pytest

from erp import endpoints, gateway


def _p():
    return {"custom_order_sn": "SCM-PO-1", "sys_supplier_id": 8085, "purchaser_id": 111, "opt_uid": 1,
            "qc_type": 2, "sys_wid": 1001, "product_list": [{"sku": "S1", "quantity_real": 3, "price": 1.5}]}


def test_unregistered_endpoint_refused():
    with pytest.raises(endpoints.UnregisteredEndpoint):
        gateway.call("cancel_purchase_order", {})


def test_create_is_registered_irreversible_with_lookup():
    ep = endpoints.get("create_purchase_order")
    assert (ep.side_effect, ep.idem_param, ep.lookup) == ("irreversible", "custom_order_sn", "purchase_order_list")


def test_double_unknown_created_is_findable_by_lookup(lingxing):
    lingxing.enqueue("create_purchase_order", "unknown_created")
    _, r = gateway.call("create_purchase_order", _p())
    assert r.outcome == "unknown"
    _, lst = gateway.call("purchase_order_list", {"search_field_time": "create_time", "start_date": "2026-10-01", "end_date": "2026-10-31"})
    assert [o["custom_order_sn"] for o in lst.body["data"]] == ["SCM-PO-1"]


def test_double_unknown_absent_is_not_findable(lingxing):
    lingxing.enqueue("create_purchase_order", "unknown_absent")
    gateway.call("create_purchase_order", _p())
    _, lst = gateway.call("purchase_order_list", {"search_field_time": "create_time", "start_date": "2026-10-01", "end_date": "2026-10-31"})
    assert lst.body["data"] == []


def test_redact_hides_secrets():
    assert gateway.redact({"access_token": "t", "sign": "s", "sku": "S1"}) == \
        {"access_token": "***", "sign": "***", "sku": "S1"}


def test_double_list_honours_scripted_outcomes(lingxing):
    for scripted, want in [("not_sent", "not_sent"), ("rejected", "rejected"),
                           ("unknown_created", "unknown"), ("unknown_absent", "unknown")]:
        lingxing.enqueue("purchase_order_list", scripted)
        _, r = gateway.call("purchase_order_list", {"search_field_time": "create_time", "start_date": "2026-10-01", "end_date": "2026-10-31"})
        assert r.outcome == want, scripted
    _, r = gateway.call("purchase_order_list", {"search_field_time": "create_time", "start_date": "2026-10-01", "end_date": "2026-10-31"})
    assert r.outcome == "ok"


def test_unexpected_driver_exception_is_logged_then_reraised(monkeypatch, caplog):
    class Boom:
        def call(self, ep, params):
            raise RuntimeError("boom")
    monkeypatch.setattr(gateway, "_driver", lambda: Boom())
    with caplog.at_level("WARNING", logger="scm.erp"), pytest.raises(RuntimeError):
        gateway.call("create_purchase_order", _p())
    msg = " ".join(r.getMessage() for r in caplog.records)
    assert "outcome=exception" in msg and "SCM-PO-1" in msg and "RuntimeError" in msg


def test_overseas_stock_order_endpoints_are_registered():
    """C-b（CB 组）：海外仓备货单建 / 查重 / 详情 / 删 + 物流渠道 + 本地库存实时。★ 建单可删（仅发货前）= reversible，
    幂等键 inbound_order_no（领星「客户参考号（唯一单号）」），超时后用 listOrderNos 查重。"""
    c = endpoints.get("create_ows_order")
    assert (c.method, c.path, c.side_effect, c.idem_param, c.lookup) == (
        "POST", "/erp/sc/routing/owms/inbound/createInbound", "reversible", "inbound_order_no", "ows_order_nos")
    assert endpoints.get("ows_order_nos").path == "/erp/sc/routing/owms/inbound/listOrderNos"
    assert endpoints.get("ows_order_detail").path == "/basicOpen/overSeaWarehouse/stockOrder/detail"
    assert endpoints.get("ows_order_delete").side_effect == "reversible"
    assert endpoints.get("logistics_channel_list").path == "/erp/sc/data/local_inventory/channelList"
    assert endpoints.get("local_inventory_details").path == "/erp/sc/routing/data/local_inventory/inventoryDetails"
    assert {endpoints.get(n).side_effect for n in ("ows_order_nos", "ows_order_detail", "logistics_channel_list",
                                                    "local_inventory_details")} == {"read"}
